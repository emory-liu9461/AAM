"""Asset management: a holding company's portfolio of operating businesses.

Each **holding** is a business the holding company owns all or part of — a real estate company, an operating
company, a joint venture, a minority investment… — with:

* type, ownership %, date acquired, status (active / exited), sector and location;
* an optional **linked company file** (a company in this app): its books give the holding's financials —
  revenue and net income for the last twelve months, cash, total assets, liabilities, debt, equity,
  receivables and payables — with no re-keying;
* **capital flows**: money the holding company put in (acquisition, contributions, loans in) and took out
  (distributions / dividends, loan repayments, sale proceeds);
* **valuations** of the whole business (100% equity value) over time — appraisal, purchase price, DCF, book
  value… Our share is the value × ownership %. Without a valuation, the linked books' equity is used (book value).

From these: invested capital, distributions, current value of our interest (NAV), gain, multiple on invested
capital (MOIC) and IRR, per holding and for the portfolio, plus allocation by type and a proportionate
consolidation of the linked companies' financials.

The portfolio is stored in its own file next to the company list, so it spans every company.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

# An asset taken on with a loan in one journal entry (a loan matched to Assets Held) is financing, not capital
# spending, and no money was borrowed in cash — leave such entries out of capex and of debt funding.
NOT_FINANCED = ("NOT (e.kind = 'manual' AND EXISTS (SELECT 1 FROM journal_lines l2 JOIN accounts a2 "
                "ON a2.id = l2.account_id WHERE l2.entry_id = e.id AND a2.type = 'liability' "
                "AND a2.subtype = 'long_term' AND l2.credit_cents > 0) AND NOT EXISTS (SELECT 1 FROM journal_lines l3 "
                "JOIN accounts a3 ON a3.id = l3.account_id WHERE l3.entry_id = e.id AND a3.is_bank = 1))")

TYPES = ["Real estate", "Operating company", "Holding company", "Joint venture", "Minority investment",
         "Fund / LP interest", "Loan / note receivable", "Other"]
FLOW_KINDS = {  # kind: (label, sign for the holding company: -1 money out to the holding, +1 money back)
    "acquisition": ("Acquisition / purchase", -1),
    "contribution": ("Capital contribution", -1),
    "loan_in": ("Shareholder loan advanced", -1),
    "distribution": ("Distribution / dividend", 1),
    "loan_repaid": ("Loan repaid to us", 1),
    "proceeds": ("Sale proceeds", 1),
    "return_of_capital": ("Return of capital", 1),
}
METHODS = ["Appraisal", "Purchase price", "Recent transaction", "DCF", "Market multiple", "Book value", "Other"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS holdings (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    type          TEXT NOT NULL DEFAULT 'Operating company',
    ownership_pct REAL NOT NULL DEFAULT 100 CHECK (ownership_pct > 0 AND ownership_pct <= 100),
    acquired      TEXT,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','exited')),
    sector        TEXT NOT NULL DEFAULT '',
    location      TEXT NOT NULL DEFAULT '',
    company_path  TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS valuations (
    id           INTEGER PRIMARY KEY,
    holding_id   INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    value_cents  INTEGER NOT NULL,           -- 100% equity value of the business
    method       TEXT NOT NULL DEFAULT 'Appraisal',
    note         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS flows (
    id           INTEGER PRIMARY KEY,
    holding_id   INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    date         TEXT NOT NULL,
    kind         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    note         TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_val_h ON valuations(holding_id, date);
CREATE INDEX IF NOT EXISTS ix_flow_h ON flows(holding_id, date);
"""


@dataclass
class Holding:
    name: str
    type: str = "Operating company"
    ownership_pct: float = 100.0
    acquired: str | None = None
    status: str = "active"
    sector: str = ""
    location: str = ""
    company_path: str = ""
    notes: str = ""
    id: int | None = None
    invest_accounts: list[int] = field(default_factory=list)   # in the holding company's books: investment,
    income_accounts: list[int] = field(default_factory=list)   # due-from / loan accounts; dividend income accounts
    owner_path: str = ""          # the holding company that holds the shares ('' = the portfolio's default)
    manager_path: str = ""        # the asset manager assigned to manage this company ('' = none)
    fee_pct: float = 0.0          # management fee, % of the company's value per year
    fee_fixed_cents: int = 0      # and / or a fixed fee per month
    capex_req_cents: int = 0      # annual capital expenditure requirement (0 = use the last twelve months' actual)


@dataclass
class Valuation:
    date: str
    value_cents: int                      # equity value (100%) = company value − debt
    method: str = "Appraisal"
    note: str = ""
    id: int | None = None
    company_value_cents: int | None = None   # the whole business / property (enterprise or gross asset value)
    debt_cents: int | None = None            # debt in place at that date


@dataclass
class Flow:
    date: str
    kind: str
    amount_cents: int
    note: str = ""
    id: int | None = None
    from_books: bool = False     # read from the holding company's books (not entered here)

    @property
    def signed(self) -> int:
        """From the holding company's side: money in (+) or out (−)."""
        return FLOW_KINDS[self.kind][1] * self.amount_cents

    @property
    def label(self) -> str:
        return FLOW_KINDS[self.kind][0]


class Portfolio:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self.conn.executescript(LOAN_SCHEMA)
        self.conn.executescript(SU_SCHEMA)
        self.conn.executescript(CAP_SCHEMA)
        cols = {c[1] for c in self.conn.execute("PRAGMA table_info(holdings)")}
        vcols = {c[1] for c in self.conn.execute("PRAGMA table_info(valuations)")}
        for col in ("company_value_cents", "debt_cents"):
            if col not in vcols:
                self.conn.execute(f"ALTER TABLE valuations ADD COLUMN {col} INTEGER")
        for col, decl in (("invest_accounts", "TEXT NOT NULL DEFAULT ''"), ("income_accounts", "TEXT NOT NULL DEFAULT ''"),
                          ("owner_path", "TEXT NOT NULL DEFAULT ''"), ("manager_path", "TEXT NOT NULL DEFAULT ''"),
                          ("fee_pct", "REAL NOT NULL DEFAULT 0"), ("fee_fixed_cents", "INTEGER NOT NULL DEFAULT 0"),
                          ("capex_req_cents", "INTEGER NOT NULL DEFAULT 0")):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE holdings ADD COLUMN {col} {decl}")
        self.conn.commit()

    # -- settings -------------------------------------------------------------

    @property
    def holdco_path(self) -> str:
        """The holding company's own company file (its books show the money going to / from each holding)."""
        r = self.conn.execute("SELECT value FROM settings WHERE key='holdco_path'").fetchone()
        return r[0] if r else ""

    @holdco_path.setter
    def holdco_path(self, path: str) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO settings(key, value) VALUES ('holdco_path', ?) ON CONFLICT(key) "
                              "DO UPDATE SET value=excluded.value", (str(path or ""),))

    def close(self) -> None:
        self.conn.close()

    # -- holdings -----------------------------------------------------------

    def holdings(self, *, include_exited: bool = True) -> list[Holding]:
        rows = self.conn.execute("SELECT * FROM holdings ORDER BY status, name COLLATE NOCASE").fetchall()
        ids = lambda t: [int(x) for x in (t or "").split(",") if x.strip().isdigit()]  # noqa: E731
        out = [Holding(r["name"], r["type"], r["ownership_pct"], r["acquired"], r["status"], r["sector"],
                       r["location"], r["company_path"], r["notes"], r["id"], ids(r["invest_accounts"]),
                       ids(r["income_accounts"]), r["owner_path"], r["manager_path"], r["fee_pct"],
                       r["fee_fixed_cents"], r["capex_req_cents"]) for r in rows]
        return out if include_exited else [h for h in out if h.status == "active"]

    def owner_of(self, h: Holding) -> str:
        """The holding company file that holds this company's shares."""
        return h.owner_path or self.holdco_path

    def holding(self, holding_id: int) -> Holding:
        h = next((h for h in self.holdings() if h.id == holding_id), None)
        if h is None:
            raise KeyError(holding_id)
        return h

    def save_holding(self, h: Holding) -> int:
        if not h.name.strip():
            raise ValueError("Enter the holding's name")
        if not 0 < h.ownership_pct <= 100:
            raise ValueError("Ownership must be more than 0% and at most 100%")
        clash = self.conn.execute("SELECT id FROM holdings WHERE name=? COLLATE NOCASE AND id != ?",
                                  (h.name.strip(), h.id or 0)).fetchone()
        if clash:
            raise ValueError(f"There's already a holding called {h.name.strip()}")
        if h.company_path:
            twin = next((x for x in self.holdings() if x.id != h.id and _same_file(x.company_path, h.company_path)),
                        None)
            if twin:
                raise ValueError(f"Those books are already linked to the holding {twin.name} — edit that one instead")
        vals = (h.name.strip(), h.type, h.ownership_pct, h.acquired, h.status, h.sector.strip(), h.location.strip(),
                h.company_path, h.notes.strip(), ",".join(map(str, h.invest_accounts)),
                ",".join(map(str, h.income_accounts)), h.owner_path or "", h.manager_path or "", h.fee_pct or 0.0,
                h.fee_fixed_cents or 0, h.capex_req_cents or 0)
        if h.fee_pct < 0 or h.fee_fixed_cents < 0:
            raise ValueError("Fees can't be negative")
        old = self.conn.execute("SELECT company_path FROM holdings WHERE id=?", (h.id,)).fetchone() if h.id else None
        with self.conn:
            if h.id is None:
                h.id = self.conn.execute(
                    "INSERT INTO holdings(name, type, ownership_pct, acquired, status, sector, location, company_path, "
                    "notes, invest_accounts, income_accounts, owner_path, manager_path, fee_pct, fee_fixed_cents, "
                    "capex_req_cents) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
            else:
                self.conn.execute("UPDATE holdings SET name=?, type=?, ownership_pct=?, acquired=?, status=?, "
                                  "sector=?, location=?, company_path=?, notes=?, invest_accounts=?, "
                                  "income_accounts=?, owner_path=?, manager_path=?, fee_pct=?, fee_fixed_cents=?, "
                                  "capex_req_cents=? WHERE id=?", vals + (h.id,))
        if old and old[0] and not _same_file(old[0], h.company_path):
            self._set_skipped(self.skipped_paths() | {old[0]})  # unlinked by hand: don't pull it back in
        if h.company_path and any(_same_file(p, h.company_path) for p in self.skipped_paths()):
            self.unskip(h.company_path)
        return h.id

    def delete_holding(self, holding_id: int) -> None:
        row = self.conn.execute("SELECT company_path FROM holdings WHERE id=?", (holding_id,)).fetchone()
        with self.conn:
            self.conn.execute("DELETE FROM holdings WHERE id=?", (holding_id,))
        if row and row[0]:
            self._set_skipped(self.skipped_paths() | {row[0]})  # removed by hand: don't pull it back in

    # -- operating companies pulled in automatically ------------------------

    def skipped_paths(self) -> set[str]:
        import json

        r = self.conn.execute("SELECT value FROM settings WHERE key='auto_skip'").fetchone()
        try:
            return set(json.loads(r[0])) if r else set()
        except ValueError:
            return set()

    def _set_skipped(self, paths: set[str]) -> None:
        import json

        with self.conn:
            self.conn.execute("INSERT INTO settings(key, value) VALUES ('auto_skip', ?) ON CONFLICT(key) "
                              "DO UPDATE SET value=excluded.value", (json.dumps(sorted(paths)),))

    def unskip(self, path: str) -> None:
        self._set_skipped({p for p in self.skipped_paths() if not _same_file(p, path)})

    @property
    def auto_sync(self) -> bool:
        r = self.conn.execute("SELECT value FROM settings WHERE key='auto_sync'").fetchone()
        return (r[0] if r else "1") == "1"

    @auto_sync.setter
    def auto_sync(self, on: bool) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO settings(key, value) VALUES ('auto_sync', ?) ON CONFLICT(key) "
                              "DO UPDATE SET value=excluded.value", ("1" if on else "0",))

    # -- valuations & flows -------------------------------------------------

    def valuations(self, holding_id: int) -> list[Valuation]:
        return [Valuation(r["date"], r["value_cents"], r["method"], r["note"], r["id"], r["company_value_cents"],
                          r["debt_cents"]) for r in self.conn.execute(
            "SELECT * FROM valuations WHERE holding_id=? ORDER BY date, id", (holding_id,))]

    def add_valuation(self, holding_id: int, v: Valuation) -> int:
        date.fromisoformat(v.date)
        with self.conn:
            return self.conn.execute(
                "INSERT INTO valuations(holding_id, date, value_cents, method, note, company_value_cents, debt_cents) "
                "VALUES (?,?,?,?,?,?,?)", (holding_id, v.date, v.value_cents, v.method, v.note.strip(),
                                           v.company_value_cents, v.debt_cents)).lastrowid

    def delete_valuation(self, valuation_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM valuations WHERE id=?", (valuation_id,))

    def flows(self, holding_id: int | None = None) -> list[Flow]:
        sql, args = "SELECT * FROM flows", ()
        if holding_id is not None:
            sql, args = sql + " WHERE holding_id=?", (holding_id,)
        return [Flow(r["date"], r["kind"], r["amount_cents"], r["note"], r["id"])
                for r in self.conn.execute(sql + " ORDER BY date, id", args)]

    def add_flow(self, holding_id: int, f: Flow) -> int:
        if f.kind not in FLOW_KINDS:
            raise ValueError(f"Unknown kind {f.kind!r}")
        if f.amount_cents <= 0:
            raise ValueError("The amount must be more than zero")
        date.fromisoformat(f.date)
        with self.conn:
            return self.conn.execute("INSERT INTO flows(holding_id, date, kind, amount_cents, note) VALUES (?,?,?,?,?)",
                                     (holding_id, f.date, f.kind, f.amount_cents, f.note.strip())).lastrowid

    def delete_flow(self, flow_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM flows WHERE id=?", (flow_id,))


# ---------------------------------------------------------------------------
# Financials from a linked company file
# ---------------------------------------------------------------------------


@dataclass
class Financials:
    as_of: str
    revenue_ltm: int = 0        # last twelve months
    opex_ltm: int = 0           # cost of sales + operating expenses, excluding depreciation & amortization
    ebitda_ltm: int = 0         # revenue − opex
    cash_flow_ltm: int = 0      # net change in cash over the last twelve months
    nonop_ltm: int = 0          # interest, income taxes, other non-operating expenses (not D&A)
    capex_ltm: int = 0          # capital spending: purchases of fixed assets
    net_income_ltm: int = 0
    cash: int = 0
    total_assets: int = 0
    total_liabilities: int = 0
    debt: int = 0               # loans, mortgages, lines of credit, long-term liabilities
    receivables: int = 0
    payables: int = 0
    equity: int = 0
    company: str = ""
    error: str = ""


def company_financials(company_path: str | Path, as_of: date | None = None) -> Financials:
    """Key figures from a company file in this app (opened read-only, then closed)."""
    from . import receivables as rx
    from .books import Books

    as_of = as_of or date.today()
    out = Financials(as_of.isoformat())
    try:
        books = Books.open(company_path, create=False)
    except Exception as e:  # noqa: BLE001
        out.error = f"Couldn't open the company file: {e}"
        return out
    try:
        out.company = books.company_name
        start = (as_of - timedelta(days=365) + timedelta(days=1)).isoformat()
        period = books.ledger_totals(start=start, end=as_of.isoformat())
        totals = books.ledger_totals(end=as_of.isoformat())
        ar_ids = rx.control_ids(books, "invoice") if books._has_table("documents") else set()
        ap_ids = rx.control_ids(books, "bill") if books._has_table("documents") else set()
        da = ("depreciation", "amortization", "amortisation")
        for a in books.accounts():
            net = totals.get(a.id, 0)
            if a.type == "income":
                out.revenue_ltm += -period.get(a.id, 0) if a.subtype in ("operating", "") else 0
                out.net_income_ltm += -period.get(a.id, 0)
            elif a.type == "expense":
                out.net_income_ltm -= period.get(a.id, 0)
                if any(w in a.name.lower() for w in da):
                    pass  # non-cash: neither operating nor non-operating cash costs
                elif a.subtype in ("operating", "cogs", ""):
                    out.opex_ltm += period.get(a.id, 0)
                else:  # interest, income taxes, other non-operating expenses
                    out.nonop_ltm += period.get(a.id, 0)
            if a.is_bank:
                out.cash_flow_ltm += period.get(a.id, 0)  # net change in cash over the twelve months
            if a.type == "asset":
                out.total_assets += net
                if a.is_bank:
                    out.cash += net
                if a.id in ar_ids:
                    out.receivables += net
            elif a.type == "liability":
                out.total_liabilities += -net
                if a.id in ap_ids:
                    out.payables += -net
                name = a.name.lower()
                if a.subtype == "long_term" or any(w in name for w in ("loan", "mortgage", "debt", "line of credit",
                                                                        "note payable", "credit facility")):
                    out.debt += -net
        out.equity = out.total_assets - out.total_liabilities
        out.ebitda_ltm = out.revenue_ltm - out.opex_ltm
        fixed = [a.id for a in books.accounts() if a.type == "asset" and a.subtype == "fixed_asset"
                 and not any(w in a.name.lower() for w in ("accumulated", "depreciation"))]
        if fixed:  # capital spending: purchases debited to fixed-asset accounts in the twelve months
            q = (f"SELECT COALESCE(SUM(l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e "
                 f"ON e.id = l.entry_id WHERE l.account_id IN ({','.join('?' * len(fixed))}) AND e.kind != 'opening' "
                 f"AND {NOT_FINANCED} AND e.date >= ? AND e.date <= ?")
            out.capex_ltm = books.conn.execute(q, [*fixed, start, as_of.isoformat()]).fetchone()[0]
    except Exception as e:  # noqa: BLE001
        out.error = str(e)
    finally:
        books.close()
    return out


def book_flows(holdco_path: str | Path, h: Holding, as_of: date | None = None) -> list[Flow]:
    """Capital flows read from the holding company's books: every entry in the holding's investment / due-from /
    loan accounts (money out to it = contribution, money back = return of capital; an opening balance =
    acquisition) and its dividend income accounts (= distributions)."""
    from .books import Books

    if not holdco_path or not Path(holdco_path).exists() or not (h.invest_accounts or h.income_accounts):
        return []
    as_of = as_of or date.today()
    books = Books.open(holdco_path, create=False)
    try:
        out: list[Flow] = []
        ids = list(h.invest_accounts) + list(h.income_accounts)
        q = (f"SELECT l.account_id, l.debit_cents - l.credit_cents AS net, e.date, e.kind, e.memo, e.transaction_id "
             f"FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
             f"WHERE l.account_id IN ({','.join('?' * len(ids))}) AND e.date <= ? ORDER BY e.date, e.id")
        for r in books.conn.execute(q, [*ids, as_of.isoformat()]):
            net = r["net"]
            if not net:
                continue
            desc = r["memo"]
            if r["transaction_id"]:
                try:
                    desc = books.transaction(r["transaction_id"]).description
                except KeyError:
                    pass
            try:
                acct = books.account(r["account_id"]).name
            except KeyError:
                acct = "?"
            note = f"From the books: {desc} ({acct})".strip()
            if r["account_id"] in h.income_accounts:
                if net < 0:  # income credited: a distribution / dividend received
                    out.append(Flow(r["date"], "distribution", -net, note, from_books=True))
                continue
            if net > 0:
                kind = "acquisition" if r["kind"] == "opening" else "contribution"
                out.append(Flow(r["date"], kind, net, note, from_books=True))
            else:
                out.append(Flow(r["date"], "return_of_capital", -net, note, from_books=True))
        return out
    finally:
        books.close()


def holdco_accounts(holdco_path: str | Path) -> list[tuple[int, str, str]]:
    """Accounts in the holding company's books a holding can be linked to: (id, label, type) for non-bank assets
    (investments, due-from, loans receivable) and income accounts (dividends, management fees)."""
    from .books import Books

    if not holdco_path or not Path(holdco_path).exists():
        return []
    books = Books.open(holdco_path, create=False)
    try:
        return [(a.id, f"{a.code} · {a.name}", a.type) for a in books.accounts()
                if a.active and ((a.type == "asset" and not a.is_bank) or a.type == "income")]
    finally:
        books.close()


def create_investment_account(holdco_path: str | Path, holding_name: str) -> int:
    """Add 'Investment in <holding>' (a long-term asset) to the holding company's books; returns its id."""
    from .books import Books

    books = Books.open(holdco_path, create=False)
    try:
        label = f"Investment in {holding_name}"
        existing = next((a for a in books.accounts() if a.name.lower() == label.lower()), None)
        if existing:
            return existing.id
        used = {a.code for a in books.accounts()}
        code = next(str(n) for n in range(1700, 1999) if str(n) not in used)
        return books.add_account(code, label, "asset", "other_asset").id
    finally:
        books.close()


def monthly_results(company_path: str | Path, end: date | None = None, months: int = 12
                    ) -> list[tuple[str, int, int]]:
    """[(month label, revenue, net income)] for the last ``months`` months to ``end`` from a company file."""
    return [(m["label"], m["revenue"], m["net_income"]) for m in monthly_figures(company_path, end, months)]


def monthly_figures(company_path: str | Path, end: date | None = None, months: int = 12) -> list[dict]:
    """Month by month (oldest first) from a company file: revenue, opex, ebitda, nonop, capex, net_cash_flow,
    net_income — the same definitions as the last-twelve-months figures."""
    import calendar

    end = end or date.today()
    if not company_path or not Path(company_path).exists():
        return []
    spans = []
    y, m = end.year, end.month
    for _ in range(months):
        spans.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    out = []
    for y, m in reversed(spans):
        first, last = date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
        f = _period_figures(company_path, first, last)
        out.append({"label": first.strftime("%b %y"), "revenue": f.revenue_ltm, "opex": f.opex_ltm,
                    "ebitda": f.ebitda_ltm, "nonop": f.nonop_ltm, "capex": f.capex_ltm,
                    "net_cash_flow": f.ebitda_ltm - f.nonop_ltm - f.capex_ltm, "net_income": f.net_income_ltm})
    return out


def _period_figures(company_path: str | Path, start: date, end: date) -> Financials:
    """Operating figures for any period (the *_ltm fields hold the period's amounts)."""
    days = (end - start).days + 1
    # company_financials looks back 365 days from as_of; compute the period directly instead
    from .books import Books

    f = Financials(end.isoformat())
    books = Books.open(company_path, create=False)
    try:
        t = books.ledger_totals(start=start.isoformat(), end=end.isoformat())
        da = ("depreciation", "amortization", "amortisation")
        for a in books.accounts():
            v = t.get(a.id, 0)
            if a.type == "income":
                if a.subtype in ("operating", ""):
                    f.revenue_ltm += -v
                f.net_income_ltm += -v
            elif a.type == "expense":
                f.net_income_ltm -= v
                if any(w in a.name.lower() for w in da):
                    continue
                if a.subtype in ("operating", "cogs", ""):
                    f.opex_ltm += v
                else:
                    f.nonop_ltm += v
        f.ebitda_ltm = f.revenue_ltm - f.opex_ltm
        fixed = [a.id for a in books.accounts() if a.type == "asset" and a.subtype == "fixed_asset"
                 and not any(w in a.name.lower() for w in ("accumulated", "depreciation"))]
        if fixed:
            q = (f"SELECT COALESCE(SUM(l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
                 f"WHERE l.account_id IN ({','.join('?' * len(fixed))}) AND e.kind != 'opening' AND {NOT_FINANCED} "
                 f"AND e.date >= ? AND e.date <= ?")
            f.capex_ltm = books.conn.execute(q, [*fixed, start.isoformat(), end.isoformat()]).fetchone()[0]
    finally:
        books.close()
    del days
    return f


# ---------------------------------------------------------------------------
# Returns
# ---------------------------------------------------------------------------


def xirr(cashflows: list[tuple[date, int]]) -> float | None:
    """Annualized internal rate of return of dated cash flows (needs money both out and in)."""
    flows = [(d, float(a)) for d, a in cashflows if a]
    if not flows or not any(a < 0 for _, a in flows) or not any(a > 0 for _, a in flows):
        return None
    t0 = min(d for d, _ in flows)

    def npv(rate: float) -> float:
        return sum(a / (1 + rate) ** ((d - t0).days / 365.0) for d, a in flows)

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-6:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


@dataclass
class Performance:
    holding: Holding
    invested: int = 0            # acquisition + contributions + loans advanced
    returned: int = 0            # distributions + loans repaid + sale proceeds
    value_100: int | None = None  # latest 100% equity value
    value_date: str = ""
    value_method: str = ""
    nav: int = 0                 # value of our interest (value × ownership)
    gain: int = 0                # nav + returned − invested
    moic: float | None = None    # (nav + returned) / invested
    irr: float | None = None
    financials: Financials | None = None
    flows: list[Flow] = field(default_factory=list)
    debt: int = 0                 # debt in place (100%): from the valuation, else the company's books
    company_value: int | None = None  # the whole business (100%) = equity value + debt
    capex_req: int = 0            # annual capex requirement (set on the holding, else last twelve months' actual)

    @property
    def has_books(self) -> bool:
        return bool(self.financials and not self.financials.error)

    @property
    def cash_on_cash(self) -> float | None:
        """Our share of the annual net cash flow ÷ the capital we invested."""
        ncf = self.net_cash_flow
        return ncf * self.share / self.invested if ncf is not None and self.invested else None

    @property
    def net_cash_flow(self) -> int | None:
        """EBITDA − non-operating expenses − capex requirement (100%, last twelve months)."""
        if not self.has_books:
            return None
        f = self.financials
        return f.ebitda_ltm - f.nonop_ltm - self.capex_req

    @property
    def ltv(self) -> float | None:
        """Debt ÷ company value."""
        return self.debt / self.company_value if self.company_value else None

    @property
    def share(self) -> float:
        return self.holding.ownership_pct / 100


def performance(pf: Portfolio, h: Holding, as_of: date | None = None, *, with_financials: bool = True
                ) -> Performance:
    as_of = as_of or date.today()
    p = Performance(h)
    p.flows = [f for f in pf.flows(h.id) if f.date <= as_of.isoformat()]
    try:
        p.flows += book_flows(pf.owner_of(h), h, as_of)
    except Exception:  # noqa: BLE001 - a missing / locked holding-company file just means no book flows
        pass
    p.flows.sort(key=lambda f: f.date)
    p.invested = sum(f.amount_cents for f in p.flows if f.signed < 0)
    p.returned = sum(f.amount_cents for f in p.flows if f.signed > 0)
    if with_financials and h.company_path and Path(h.company_path).exists():
        p.financials = company_financials(h.company_path, as_of)
    vals = [v for v in pf.valuations(h.id) if v.date <= as_of.isoformat()]
    books_ok = bool(p.financials and not p.financials.error)
    from_loans = False
    if vals:
        v = vals[-1]
        cv, debt, eq, from_loans = valuation_figures(pf, h, v)
        p.value_100, p.value_date, p.value_method = eq, v.date, v.method
        if debt is None:
            debt = p.financials.debt if books_ok else 0
            cv = cv if v.company_value_cents is not None else eq + debt
        p.debt, p.company_value = debt, cv
    elif books_ok:
        p.value_100, p.value_date, p.value_method = p.financials.equity, p.financials.as_of, "Book value (from books)"
        p.debt = p.financials.debt
        p.company_value = p.value_100 + p.debt
    if h.id and not from_loans and not (vals and vals[-1].debt_cents is not None):
        # loans entered on the Financing table that aren't in the company's books add to its debt
        extra = sum(ln.balance for ln in loans(pf, h, as_of, with_books=False) if ln.account_id is None)
        if extra:
            p.debt += extra
            if vals:
                p.company_value = vals[-1].value_cents + p.debt if vals[-1].company_value_cents is None \
                    else p.company_value
            elif p.value_100 is not None:
                p.company_value = p.value_100 + p.debt
    nxt = next((x for x in projections(pf, h.id) if x.year == as_of.year + 1), None) if h.id else None
    p.capex_req = h.capex_req_cents or (nxt.capex if nxt and nxt.capex else 0) or \
        (p.financials.capex_ltm if books_ok else 0)
    if h.status == "exited":
        p.nav = 0
    elif p.value_100 is not None:
        p.nav = round(p.value_100 * h.ownership_pct / 100)
    p.gain = p.nav + p.returned - p.invested
    if p.invested:
        p.moic = (p.nav + p.returned) / p.invested
    cash = [(date.fromisoformat(f.date), f.signed) for f in p.flows]
    if p.nav:
        cash.append((as_of, p.nav))
    p.irr = xirr(cash)
    return p


@dataclass
class Summary:
    as_of: str
    performances: list[Performance]
    invested: int = 0
    returned: int = 0
    nav: int = 0
    gain: int = 0
    moic: float | None = None
    irr: float | None = None
    company_value: int = 0      # 100% values of the companies (active)
    debt: int = 0
    equity: int = 0
    value_share: int = 0        # at our ownership share
    debt_share: int = 0
    capex_req_share: int = 0    # annual capex requirement at our share
    net_cash_flow_share: int = 0

    @property
    def cash_on_cash(self) -> float | None:
        return self.net_cash_flow_share / self.invested if self.invested and self.consolidated else None
    by_type: dict[str, int] = field(default_factory=dict)          # NAV by type
    consolidated: Financials | None = None                        # linked companies × ownership %


def _first_entry_date(company_path: str) -> str | None:
    try:
        conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
        try:
            r = conn.execute("SELECT MIN(date) FROM journal_entries").fetchone()
        finally:
            conn.close()
        return r[0] if r and r[0] else None
    except sqlite3.Error:
        return None


def sync_operating_companies(pf: Portfolio, companies: list[tuple[str, str, str]]) -> list[str]:
    """Add every operating company in the company list as a holding linked to its books, so its revenue,
    expenses, EBITDA, capex, cash, debt and book value flow into the portfolio with no typing.

    ``companies`` is [(name, path, role)]. Companies already linked to a holding are left as they are, and a
    holding you deleted stays deleted. An unlinked holding with the same name is linked instead of duplicated.
    Returns the names added or linked."""
    if not pf.auto_sync:
        return []
    have = pf.holdings()
    skipped = pf.skipped_paths()
    done = []
    for name, path, role in companies:
        if role != "operating" or not path or not Path(path).exists():
            continue
        if any(_same_file(h.company_path, path) for h in have) or any(_same_file(p, path) for p in skipped):
            continue
        h = next((h for h in have if not h.company_path and h.name.strip().lower() == name.strip().lower()), None)
        if h is None:
            taken = {x.name.lower() for x in have}
            new_name, n = name.strip(), 2
            while new_name.lower() in taken:
                new_name, n = f"{name.strip()} ({n})", n + 1
            h = Holding(new_name, "Operating company", 100.0, _first_entry_date(path),
                        notes="Added automatically from the company list")
            have.append(h)
        h.company_path = path
        pf.save_holding(h)
        done.append(h.name)
    return done


def _same_file(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


_ROLE_CACHE: dict[str, tuple[float, str]] = {}


def linked_role(h: Holding) -> str:
    """The role of the company file a holding is linked to: operating | holding | asset_manager ('' if none)."""
    if not h.company_path:
        return ""
    from . import company_roles

    try:
        mtime = Path(h.company_path).stat().st_mtime
    except OSError:
        return ""
    hit = _ROLE_CACHE.get(h.company_path)
    if hit and hit[0] == mtime:
        return hit[1]
    role = company_roles.read_role(h.company_path)
    _ROLE_CACHE[h.company_path] = (mtime, role)
    return role


def is_portfolio_company(h: Holding) -> bool:
    """Holding companies and asset managers own and run the portfolio; they aren't part of it."""
    return linked_role(h) not in ("holding", "asset_manager")


def select(pf: Portfolio, *, owner: str | None = None, manager: str | None = None,
           include_exited: bool = True, company: str | None = None, any_owner: bool = False,
           any_manager: bool = False) -> list[Holding]:
    """Holdings, optionally only: those held by one holding company (``owner``) or by any (``any_owner``); those
    managed by one asset manager (``manager``) or by any (``any_manager``); or one operating company (``company``,
    its books). Holdings linked to a holding company's or an asset manager's books are left out."""
    out = [h for h in pf.holdings(include_exited=include_exited) if is_portfolio_company(h)]
    if owner:
        out = [h for h in out if _same_file(pf.owner_of(h), owner)]
    if manager:
        out = [h for h in out if _same_file(h.manager_path, manager)]
    if company:
        out = [h for h in out if _same_file(h.company_path, company)]
    if any_owner:
        out = [h for h in out if pf.owner_of(h)]
    if any_manager:
        out = [h for h in out if h.manager_path]
    return out


def summary(pf: Portfolio, as_of: date | None = None, *, include_exited: bool = True, owner: str | None = None,
            manager: str | None = None, **which) -> Summary:
    """``which``: company / any_owner / any_manager, as for ``select``."""
    as_of = as_of or date.today()
    perfs = [performance(pf, h, as_of) for h in select(pf, owner=owner, manager=manager,
                                                       include_exited=include_exited, **which)]
    s = Summary(as_of.isoformat(), perfs)
    s.invested = sum(p.invested for p in perfs)
    s.returned = sum(p.returned for p in perfs)
    s.nav = sum(p.nav for p in perfs)
    s.gain = s.nav + s.returned - s.invested
    s.moic = (s.nav + s.returned) / s.invested if s.invested else None
    cash = [(date.fromisoformat(f.date), f.signed) for p in perfs for f in p.flows]
    if s.nav:
        cash.append((as_of, s.nav))
    s.irr = xirr(cash)
    for p in perfs:
        if p.holding.status != "active" or p.company_value is None:
            continue
        s.company_value += p.company_value
        s.debt += p.debt
        s.equity += p.value_100 or 0
        s.value_share += round(p.company_value * p.share)
        s.debt_share += round(p.debt * p.share)
    for p in perfs:
        if p.holding.status == "active" and p.has_books:
            s.capex_req_share += round(p.capex_req * p.share)
            s.net_cash_flow_share += round(p.net_cash_flow * p.share)
    for p in perfs:
        if p.nav:
            s.by_type[p.holding.type] = s.by_type.get(p.holding.type, 0) + p.nav
    cons = Financials(as_of.isoformat(), company="Portfolio (our share)")
    linked = [p for p in perfs if p.financials and not p.financials.error and p.holding.status == "active"]
    for p in linked:
        share = p.holding.ownership_pct / 100
        for attr in ("revenue_ltm", "opex_ltm", "ebitda_ltm", "cash_flow_ltm", "nonop_ltm", "capex_ltm",
                     "net_income_ltm", "cash",
                     "total_assets", "total_liabilities", "debt", "receivables", "payables", "equity"):
            setattr(cons, attr, getattr(cons, attr) + round(getattr(p.financials, attr) * share))
    s.consolidated = cons if linked else None
    return s


# ---------------------------------------------------------------------------
# Asset manager: managed companies and management fees
# ---------------------------------------------------------------------------


@dataclass
class Managed:
    holding: Holding
    company: str              # the managed company's name (from its books, else the holding's name)
    value: int | None         # 100% value used for the fee (latest valuation, else book equity)
    annual_fee: int           # fee_pct × value + fixed × 12
    billed: int = 0           # management fees invoiced by the manager to this company in the period
    paid: int = 0


def _company_name(h: Holding) -> str:
    if h.company_path and Path(h.company_path).exists():
        from .companies import _read_name

        return _read_name(h.company_path) or h.name
    return h.name


def fee_for(h: Holding, value: int | None, start: date, end: date) -> int:
    """Management fee for a period: the annual % of value pro-rated by days, plus the fixed monthly fee per month."""
    days = (end - start).days + 1
    pct = round((value or 0) * h.fee_pct / 100 * days / 365) if value and h.fee_pct else 0
    months = round(days / 30.4375) or 1
    return pct + h.fee_fixed_cents * months


def managed_companies(pf: Portfolio, manager_path: str, start: date | None = None,
                      end: date | None = None) -> list[Managed]:
    """The companies an asset manager is assigned to, with their fee and what's been billed (from its books)."""
    from .books import Books

    end = end or date.today()
    start = start or date(end.year, 1, 1)
    hs = [h for h in select(pf, manager=manager_path) if h.status == "active"]
    out = []
    for h in hs:
        p = performance(pf, h, end, with_financials=True)
        out.append(Managed(h, _company_name(h), p.value_100, fee_for(h, p.value_100, date(end.year, 1, 1),
                                                                     date(end.year, 12, 31))))
    if manager_path and Path(manager_path).exists() and out:
        books = Books.open(manager_path, create=False)
        try:
            from . import receivables as rx

            docs = rx.documents(books, "invoice", status="all", as_of=date(9999, 12, 31))
            for m in out:
                for d in docs:
                    if d.party.lower() == m.company.lower() and start.isoformat() <= d.issue_date <= end.isoformat() \
                            and "management fee" in (d.memo or "").lower():
                        m.billed += d.amount_cents
                        m.paid += d.paid_cents
        finally:
            books.close()
    return out


def bill_management_fees(pf: Portfolio, manager_path: str, start: date, end: date, *,
                         holding_ids: list[int] | None = None, due_days: int = 30) -> list[tuple[str, int, int]]:
    """Invoice each managed company its management fee for the period, in the asset manager's books (Management
    Fee Income; the company is added as a customer). Skips companies already billed for that period.
    Returns [(company, amount, invoice id)]."""
    from . import company_roles, contacts
    from . import receivables as rx
    from .books import Books

    if not manager_path or not Path(manager_path).exists():
        raise ValueError("Choose the asset manager's company file")
    books = Books.open(manager_path, create=False)
    made = []
    try:
        acct = next((a for a in books.accounts() if a.name.lower() == "management fee income"), None)
        if acct is None:
            company_roles.apply(books, "asset_manager")
            acct = next(a for a in books.accounts() if a.name.lower() == "management fee income")
        memo = f"Management fee {start.isoformat()} to {end.isoformat()}"
        existing = {(d.party.lower(), d.memo) for d in rx.documents(books, "invoice", status="all",
                                                                     as_of=date(9999, 12, 31))}
        for m in managed_companies(pf, manager_path, start, end):
            if holding_ids is not None and m.holding.id not in holding_ids:
                continue
            amount = fee_for(m.holding, m.value, start, end)
            if amount <= 0 or (m.company.lower(), memo) in existing:
                continue
            c = contacts.ensure_contact(books, m.company, "customer")
            doc = rx.add_document(books, "invoice", c.name if c else m.company, end.isoformat(),
                                  (end + timedelta(days=due_days)).isoformat(), amount, acct.id,
                                  number=rx.next_number(books, "invoice"), memo=memo)
            made.append((m.company, amount, doc))
    finally:
        books.close()
    return made


def write_xlsx(pf: Portfolio, path: Path, as_of: date | None = None) -> Path:
    """Portfolio report: holdings with returns, capital flows, and financials from linked books."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    s = summary(pf, as_of)
    wb = Workbook()
    money = '#,##0.00;[Red](#,##0.00);"-"'
    head_fill = PatternFill("solid", fgColor="E8EEF6")

    def header(ws, cols):
        ws.append(cols)
        for c in ws[ws.max_row]:
            c.font = Font(bold=True)
            c.fill = head_fill

    ws = wb.active
    ws.title = "Holdings"
    ws.append(["Portfolio"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"As of {s.as_of}"])
    ws.append([])
    header(ws, ["Holding", "Type", "Ownership %", "Status", "Invested", "Returned", "Value (100%)", "Value date",
                "Method", "Our value (NAV)", "Gain", "MOIC", "IRR", "Revenue LTM", "Net income LTM", "Cash",
                "Debt", "Equity (books)"])
    first = ws.max_row + 1
    for p in s.performances:
        f = p.financials
        ws.append([p.holding.name, p.holding.type, p.holding.ownership_pct / 100, p.holding.status,
                   p.invested / 100, p.returned / 100, (p.value_100 or 0) / 100 if p.value_100 is not None else None,
                   p.value_date, p.value_method, p.nav / 100, p.gain / 100, p.moic, p.irr,
                   *([f.revenue_ltm / 100, f.net_income_ltm / 100, f.cash / 100, f.debt / 100, f.equity / 100]
                     if f and not f.error else [None] * 5)])
    last = ws.max_row
    ws.append(["Total", "", "", "", f"=SUM(E{first}:E{last})", f"=SUM(F{first}:F{last})", "", "", "",
               f"=SUM(J{first}:J{last})", f"=SUM(K{first}:K{last})", s.moic, s.irr])
    for c in ws[ws.max_row]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=first, max_row=ws.max_row):
        for c in row:
            if c.column in (5, 6, 7, 10, 11, 14, 15, 16, 17, 18):
                c.number_format = money
            elif c.column in (3, 13):
                c.number_format = "0.0%"
            elif c.column == 12:
                c.number_format = '0.00"x"'
    for col, w in zip("ABCDEFGHIJKLMNOPQR", (28, 18, 11, 9, 14, 14, 15, 11, 18, 15, 14, 8, 8, 14, 14, 13, 13, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = ws.cell(row=first, column=2)

    fl = wb.create_sheet("Capital flows")
    header(fl, ["Date", "Holding", "Kind", "Amount", "Money in (+) / out (−)", "Note"])
    for p in s.performances:
        for f in p.flows:
            fl.append([f.date, p.holding.name, f.label, f.amount_cents / 100, f.signed / 100,
                       f.note + (" [books]" if f.from_books else "")])
    for row in fl.iter_rows(min_row=2):
        for c in row:
            if c.column in (4, 5):
                c.number_format = money
    for col, w in zip("ABCDEF", (11, 28, 26, 14, 20, 40)):
        fl.column_dimensions[col].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ---------------------------------------------------------------------------
# Distribution waterfall (LP / GP) for a holding company
# ---------------------------------------------------------------------------

WATERFALL_SCHEMA = """
CREATE TABLE IF NOT EXISTS waterfall_terms (
    owner_path  TEXT PRIMARY KEY,          -- the holding company these terms apply to
    lp_pct      REAL NOT NULL DEFAULT 98,  -- share of the capital put in by the LPs (the rest by the GP)
    pref_pct    REAL NOT NULL DEFAULT 8,   -- preferred return per year, compounding annually
    catchup_pct REAL NOT NULL DEFAULT 100, -- share of distributions to the GP in the catch-up tier (0 = no catch-up)
    carry_pct   REAL NOT NULL DEFAULT 20,  -- carried interest
    gp_name     TEXT NOT NULL DEFAULT '',
    lp_name     TEXT NOT NULL DEFAULT 'Limited partners'
);
"""


@dataclass
class Terms:
    lp_pct: float = 98.0
    pref_pct: float = 8.0
    catchup_pct: float = 100.0
    carry_pct: float = 20.0
    gp_name: str = ""
    lp_name: str = "Limited partners"

    @property
    def gp_pct(self) -> float:
        return 100.0 - self.lp_pct


def get_terms(pf: Portfolio, owner_path: str) -> Terms:
    pf.conn.executescript(WATERFALL_SCHEMA)
    r = pf.conn.execute("SELECT * FROM waterfall_terms WHERE owner_path=?", (owner_path or "",)).fetchone()
    if not r:
        return Terms()
    return Terms(r["lp_pct"], r["pref_pct"], r["catchup_pct"], r["carry_pct"], r["gp_name"], r["lp_name"])


def set_terms(pf: Portfolio, owner_path: str, t: Terms) -> None:
    if not 0 <= t.lp_pct <= 100:
        raise ValueError("LP share of capital must be 0–100%")
    if not 0 <= t.carry_pct < 100 or not 0 <= t.pref_pct < 100 or not 0 <= t.catchup_pct <= 100:
        raise ValueError("Percentages must be between 0 and 100")
    if t.catchup_pct and t.catchup_pct <= t.carry_pct:
        raise ValueError("The catch-up % must be more than the carried interest % (or 0 for no catch-up)")
    pf.conn.executescript(WATERFALL_SCHEMA)
    with pf.conn:
        pf.conn.execute(
            "INSERT INTO waterfall_terms(owner_path, lp_pct, pref_pct, catchup_pct, carry_pct, gp_name, lp_name) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(owner_path) DO UPDATE SET lp_pct=excluded.lp_pct, "
            "pref_pct=excluded.pref_pct, catchup_pct=excluded.catchup_pct, carry_pct=excluded.carry_pct, "
            "gp_name=excluded.gp_name, lp_name=excluded.lp_name",
            (owner_path or "", t.lp_pct, t.pref_pct, t.catchup_pct, t.carry_pct, t.gp_name.strip(),
             t.lp_name.strip() or "Limited partners"))


@dataclass
class Tier:
    name: str
    total: int
    lp: int
    gp: int
    note: str = ""


@dataclass
class Waterfall:
    terms: Terms
    capital: int            # capital put in (all partners)
    distributable: int      # returned so far + NAV today (hypothetical liquidation)
    pref: int               # preferred return owed on the capital to the as-of date
    tiers: list[Tier]
    lp_capital: int = 0
    gp_capital: int = 0
    lp_irr: float | None = None
    gp_irr: float | None = None

    @property
    def lp_total(self) -> int:
        return sum(t.lp for t in self.tiers)

    @property
    def gp_total(self) -> int:
        return sum(t.gp for t in self.tiers)

    @property
    def gp_promote(self) -> int:
        """What the GP gets beyond its pro-rata share: catch-up + carried interest."""
        return sum(t.gp for t in self.tiers if t.name in ("GP catch-up", "Carried interest"))

    @property
    def lp_moic(self) -> float | None:
        return self.lp_total / self.lp_capital if self.lp_capital else None

    @property
    def gp_moic(self) -> float | None:
        return self.gp_total / self.gp_capital if self.gp_capital else None

    @property
    def profit(self) -> int:
        return self.distributable - self.capital


def waterfall(flows: list[Flow], nav: int, as_of: date, terms: Terms) -> Waterfall:
    """Split everything distributed plus today's NAV between LPs and the GP in four tiers:

    1. Return of capital — to the partners pro rata (LP / GP capital) until all capital is back.
    2. Preferred return — pro rata, until the capital has earned ``pref_pct`` a year (compounding annually,
       from each contribution's date to the as-of date).
    3. GP catch-up — ``catchup_pct`` of each dollar to the GP (the rest to the partners pro rata) until the GP
       has ``carry_pct`` of the profit paid so far (preferred return + catch-up).
    4. Carried interest — the rest: ``carry_pct`` to the GP, the remainder to the partners pro rata.
    """
    lp_share, gp_share = terms.lp_pct / 100, terms.gp_pct / 100
    carry, cu, r = terms.carry_pct / 100, terms.catchup_pct / 100, terms.pref_pct / 100
    contribs = [(date.fromisoformat(f.date), f.amount_cents) for f in flows if f.signed < 0]
    returned = sum(f.amount_cents for f in flows if f.signed > 0)
    capital = sum(a for _, a in contribs)
    D = returned + max(nav, 0)
    pref = round(sum(a * ((1 + r) ** (max((as_of - d).days, 0) / 365.0) - 1) for d, a in contribs))

    def pro_rata(amount: int) -> tuple[int, int]:
        lp = round(amount * lp_share)
        return lp, amount - lp

    left = D
    tiers = []
    t1 = min(left, capital)
    tiers.append(Tier("Return of capital", t1, *pro_rata(t1), "until all capital is back"))
    left -= t1
    t2 = min(left, pref)
    tiers.append(Tier("Preferred return", t2, *pro_rata(t2), f"{terms.pref_pct:g}% a year on the capital"))
    left -= t2
    if cu > 0 and cu > carry:
        need = round(carry * t2 / (cu - carry)) if t2 >= pref else 0  # only once the pref is fully paid
        t3 = min(left, need)
        gp_cu = round(t3 * cu)
        lp_part, gp_part = pro_rata(t3 - gp_cu)
        tiers.append(Tier("GP catch-up", t3, lp_part, gp_cu + gp_part,
                          f"{terms.catchup_pct:g}% to the GP until it has {terms.carry_pct:g}% of the profit"))
        left -= t3
    else:
        tiers.append(Tier("GP catch-up", 0, 0, 0, "no catch-up"))
    t4 = max(left, 0)
    gp_carry = round(t4 * carry)
    lp_part, gp_part = pro_rata(t4 - gp_carry)
    tiers.append(Tier("Carried interest", t4, lp_part, gp_carry + gp_part,
                      f"{100 - terms.carry_pct:g} / {terms.carry_pct:g} split"))
    w = Waterfall(terms, capital, D, pref, tiers, round(capital * lp_share), capital - round(capital * lp_share))
    # IRRs: each side's share of the contributions, and of every distribution in proportion to its share of the total
    if D:
        lp_frac, gp_frac = w.lp_total / D, w.gp_total / D
        dated = [(date.fromisoformat(f.date), f.signed) for f in flows]
        if nav > 0:
            dated.append((as_of, nav))
        w.lp_irr = xirr([(d, round(a * (lp_share if a < 0 else lp_frac))) for d, a in dated])
        w.gp_irr = xirr([(d, round(a * (gp_share if a < 0 else gp_frac))) for d, a in dated]) if gp_share else None
    return w


def holding_waterfall(pf: Portfolio, owner_path: str, as_of: date | None = None, holding_id: int | None = None
                      ) -> Waterfall:
    """The waterfall for one holding company (all its holdings together), or for one of its holdings."""
    as_of = as_of or date.today()
    hs = select(pf, owner=owner_path)
    if holding_id is not None:
        hs = [h for h in hs if h.id == holding_id]
    flows: list[Flow] = []
    nav = 0
    for h in hs:
        p = performance(pf, h, as_of, with_financials=True)
        flows += p.flows
        nav += p.nav
    flows.sort(key=lambda f: f.date)
    terms = get_terms(pf, owner_path)
    if not terms.gp_name:
        from .companies import _read_name

        mgr = next((h.manager_path for h in hs if h.manager_path), "")
        terms.gp_name = (_read_name(mgr) if mgr and Path(mgr).exists() else "") or "General partner"
    return waterfall(flows, nav, as_of, terms)


@dataclass
class OwnerShare:
    """One cap-table owner's part of a company's waterfall."""
    name: str
    company: str
    share_class: str
    role: str                 # "GP" | "LP"
    paid_in: int
    pct: float                # of the capital paid in
    value: int                # their part of today's equity value through the waterfall
    promote: int = 0          # of which promote (GP owners: catch-up + carried interest)

    @property
    def gain(self) -> int:
        return self.value - self.paid_in

    @property
    def multiple(self) -> float | None:
        return self.value / self.paid_in if self.paid_in else None


def company_terms_key(holding_id: int) -> str:
    return f"holding:{holding_id}"


def cap_table_waterfall(pf: Portfolio, h: Holding, as_of: date | None = None, terms: Terms | None = None
                        ) -> tuple[Waterfall | None, list[OwnerShare], int | None]:
    """A company's equity value today split among the owners on its cap table, through the waterfall tiers.

    Each owner's capital is what they've paid in (from its date); the GP is the share class named in the terms'
    ``gp_name`` (blank: no GP — the value goes pro rata to what each paid in). The LP share of capital comes from
    the cap table. Returns (waterfall, [owner shares], equity value) — (None, [], value) without owners."""
    as_of = as_of or date.today()
    p = performance(pf, h, as_of, with_financials=True)
    value = p.value_100
    holders = [x for x in cap_table(pf, p) if not x.worked_out and x.funded]
    if not holders or value is None:
        return None, [], value
    t = terms or get_terms(pf, company_terms_key(h.id))
    gp_class = t.gp_name.strip()
    is_gp = [bool(gp_class) and (x.share_class or "Common") == gp_class for x in holders]
    total = sum(x.funded for x in holders)
    gp_cap = sum(x.funded for x, g in zip(holders, is_gp) if g)
    lp_cap = total - gp_cap
    t = Terms(lp_cap / total * 100 if total else 100.0, t.pref_pct, t.catchup_pct if gp_class else 0.0,
              t.carry_pct if gp_class else 0.0, gp_class or "No GP", t.lp_name or "Limited partners")
    start = h.acquired or as_of.isoformat()
    flows = sorted((Flow(x.funded_date or start, "contribution", x.funded) for x in holders), key=lambda f: f.date)
    w = waterfall(flows, value, as_of, t)
    out = []
    for x, g in zip(holders, is_gp):
        pool, cap = (w.gp_total, gp_cap) if g else (w.lp_total, lp_cap)
        share = x.funded / cap if cap else 0.0
        out.append(OwnerShare(x.name or x.company, x.company, x.share_class or "Common", "GP" if g else "LP", x.funded,
                              x.funded / total if total else 0.0, round(pool * share),
                              round(w.gp_promote * share) if g else 0))
    return w, out, value


def overall_waterfall(pf: Portfolio, holding_companies: list[str], as_of: date | None = None
                      ) -> tuple[Waterfall, list[tuple[str, Waterfall]]]:
    """The whole project: each holding company's waterfall under its own terms, and their total tier by tier.
    Returns (total, [(holding company path, its waterfall)])."""
    as_of = as_of or date.today()
    parts = [(o, holding_waterfall(pf, o, as_of)) for o in holding_companies if select(pf, owner=o)]
    names = ["Return of capital", "Preferred return", "GP catch-up", "Carried interest"]
    tiers = []
    for n in names:
        ts = [next(t for t in w.tiers if t.name == n) for _o, w in parts]
        tiers.append(Tier(n, sum(t.total for t in ts), sum(t.lp for t in ts), sum(t.gp for t in ts),
                          "all holding companies, each under its own terms"))
    total = Waterfall(Terms(gp_name="General partners"), sum(w.capital for _o, w in parts),
                      sum(w.distributable for _o, w in parts), sum(w.pref for _o, w in parts), tiers,
                      sum(w.lp_capital for _o, w in parts), sum(w.gp_capital for _o, w in parts))
    # IRRs on the combined flows, each part's flows split in its own LP / GP proportions
    lp_cash: list[tuple[date, int]] = []
    gp_cash: list[tuple[date, int]] = []
    for o, w in parts:
        flows: list[Flow] = []
        nav = 0
        for h in select(pf, owner=o):
            p = performance(pf, h, as_of, with_financials=False)
            flows += p.flows
            nav += p.nav
        lpf = w.lp_total / w.distributable if w.distributable else 0
        gpf = w.gp_total / w.distributable if w.distributable else 0
        for f in flows:
            d = date.fromisoformat(f.date)
            lp_cash.append((d, round(f.signed * (w.terms.lp_pct / 100 if f.signed < 0 else lpf))))
            gp_cash.append((d, round(f.signed * (w.terms.gp_pct / 100 if f.signed < 0 else gpf))))
        if nav > 0:
            lp_cash.append((as_of, round(nav * lpf)))
            gp_cash.append((as_of, round(nav * gpf)))
    total.lp_irr, total.gp_irr = xirr(lp_cash), xirr(gp_cash)
    return total, parts


# ---------------------------------------------------------------------------
# Projections: future years of operations and capex for each holding
# ---------------------------------------------------------------------------

PROJ_SCHEMA = """
CREATE TABLE IF NOT EXISTS projections (
    holding_id INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    year       INTEGER NOT NULL,
    revenue    INTEGER NOT NULL DEFAULT 0,
    opex       INTEGER NOT NULL DEFAULT 0,   -- cost of sales + operating expenses (excl. D&A)
    nonop      INTEGER NOT NULL DEFAULT 0,   -- interest, income taxes, other non-operating expenses
    capex      INTEGER NOT NULL DEFAULT 0,   -- capital expenditure spend
    note       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (holding_id, year)
);
"""


@dataclass
class Projection:
    year: int
    revenue: int = 0
    opex: int = 0
    nonop: int = 0
    capex: int = 0
    note: str = ""

    @property
    def ebitda(self) -> int:
        return self.revenue - self.opex

    @property
    def net_cash_flow(self) -> int:
        return self.ebitda - self.nonop - self.capex

    def scaled(self, share: float) -> "Projection":
        return Projection(self.year, round(self.revenue * share), round(self.opex * share), round(self.nonop * share),
                          round(self.capex * share), self.note)


def projections(pf: Portfolio, holding_id: int) -> list[Projection]:
    pf.conn.executescript(PROJ_SCHEMA)
    return [Projection(r["year"], r["revenue"], r["opex"], r["nonop"], r["capex"], r["note"])
            for r in pf.conn.execute("SELECT * FROM projections WHERE holding_id=? ORDER BY year", (holding_id,))]


def save_projections(pf: Portfolio, holding_id: int, rows: list[Projection]) -> None:
    """Replace a holding's projections with these years."""
    years = [p.year for p in rows]
    if len(years) != len(set(years)):
        raise ValueError("Each year can only appear once")
    for p in rows:
        if not 1900 <= p.year <= 2200:
            raise ValueError(f"{p.year} isn't a valid year")
        if min(p.revenue, p.opex, p.nonop, p.capex) < 0:
            raise ValueError(f"{p.year}: amounts can't be negative")
    pf.conn.executescript(PROJ_SCHEMA)
    with pf.conn:
        pf.conn.execute("DELETE FROM projections WHERE holding_id=?", (holding_id,))
        pf.conn.executemany(
            "INSERT INTO projections(holding_id, year, revenue, opex, nonop, capex, note) VALUES (?,?,?,?,?,?,?)",
            [(holding_id, p.year, p.revenue, p.opex, p.nonop, p.capex, p.note.strip()) for p in rows])


def project_from_actuals(f: Financials, first_year: int, years: int, *, revenue_growth: float = 0.0,
                         opex_growth: float = 0.0, nonop_growth: float = 0.0, capex: int | None = None
                         ) -> list[Projection]:
    """Start projections from the last twelve months, growing each line by a % a year."""
    out = []
    for i in range(years):
        g = lambda base, pct: round(base * (1 + pct / 100) ** (i + 1))  # noqa: E731
        out.append(Projection(first_year + i, g(f.revenue_ltm, revenue_growth), g(f.opex_ltm, opex_growth),
                              g(f.nonop_ltm, nonop_growth), f.capex_ltm if capex is None else capex))
    return out


def portfolio_projections(pf: Portfolio, *, owner: str | None = None, manager: str | None = None, **which
                          ) -> dict[int, Projection]:
    """Every active holding's projections at our ownership share, added up by year."""
    pf.conn.executescript(PROJ_SCHEMA)
    total: dict[int, Projection] = {}
    for h in select(pf, owner=owner, manager=manager, **which):
        if h.status != "active":
            continue
        for p in projections(pf, h.id):
            s = p.scaled(h.ownership_pct / 100)
            t = total.setdefault(p.year, Projection(p.year))
            t.revenue += s.revenue
            t.opex += s.opex
            t.nonop += s.nonop
            t.capex += s.capex
    return dict(sorted(total.items()))


# ---------------------------------------------------------------------------
# Financing: each company's loans, mortgages and credit lines
# ---------------------------------------------------------------------------

LOAN_SCHEMA = """
CREATE TABLE IF NOT EXISTS loans (
    id              INTEGER PRIMARY KEY,
    holding_id      INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    lender          TEXT NOT NULL DEFAULT '',
    kind            TEXT NOT NULL DEFAULT 'Term loan',
    account_id      INTEGER,                  -- the liability account in the company's books ('' = not in books)
    principal_cents INTEGER NOT NULL DEFAULT 0,  -- original amount borrowed / facility limit
    balance_cents   INTEGER,                  -- outstanding, when not read from the books
    rate_pct        REAL,
    rate_type       TEXT NOT NULL DEFAULT 'Fixed',
    start_date      TEXT,
    maturity        TEXT,
    payment_cents   INTEGER NOT NULL DEFAULT 0,  -- regular payment (0 = work it out)
    payment_freq    TEXT NOT NULL DEFAULT 'Monthly',
    term_months     INTEGER NOT NULL DEFAULT 0,
    repayment       TEXT NOT NULL DEFAULT 'Amortizing',
    amort_months    INTEGER NOT NULL DEFAULT 0,  -- amortization period (0 = same as the term)
    note            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_loans_h ON loans(holding_id);
"""
LOAN_KINDS = ["Mortgage", "Term loan", "Line of credit", "Construction loan", "Shareholder loan", "Vendor take-back",
              "Bond / note", "Other"]
RATE_TYPES = ["Fixed", "Variable"]
PAY_FREQS = ["Monthly", "Bi-weekly", "Quarterly", "Semi-annual", "Annual", "At maturity"]
PER_YEAR = {"Monthly": 12, "Bi-weekly": 26, "Weekly": 52, "Quarterly": 4, "Semi-annual": 2, "Annual": 1,
            "At maturity": 0}
REPAYMENTS = ["Amortizing", "Interest only"]


def scheduled_payment(principal: int, rate_pct: float | None, amort_months: int, freq: str, repayment: str) -> int:
    """The regular payment in cents: interest only = principal × rate ÷ payments a year; amortizing = the level
    payment that pays the principal off over the amortization period (blended principal + interest)."""
    n_year = PER_YEAR.get(freq, 12)
    if not principal or not n_year:
        return 0
    r = (rate_pct or 0) / 100 / n_year
    if repayment == "Interest only":
        return round(principal * r)
    n = round((amort_months or 0) / 12 * n_year)
    if n <= 0:
        return 0
    if r == 0:
        return round(principal / n)
    return round(principal * r / (1 - (1 + r) ** -n))


def scheduled_balance(principal: int, rate_pct: float | None, amort_months: int, freq: str, repayment: str,
                      payments_made: int) -> int:
    """What's left to pay after ``payments_made`` scheduled payments."""
    n_year = PER_YEAR.get(freq, 12)
    if repayment == "Interest only" or not n_year or payments_made <= 0:
        return principal
    r = (rate_pct or 0) / 100 / n_year
    pay = scheduled_payment(principal, rate_pct, amort_months, freq, repayment)
    k = payments_made
    if r == 0:
        return max(0, principal - pay * k)
    return max(0, round(principal * (1 + r) ** k - pay * ((1 + r) ** k - 1) / r))


def _add_months(d: date, months: int) -> date:
    import calendar

    y, m = divmod(d.month - 1 + months, 12)
    y += d.year
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))
DEBT_WORDS = ("loan", "mortgage", "debt", "line of credit", "note payable", "credit facility")


@dataclass
class Loan:
    lender: str
    kind: str = "Term loan"
    principal_cents: int = 0
    balance_cents: int | None = None
    rate_pct: float | None = None
    rate_type: str = "Fixed"
    start_date: str | None = None
    maturity: str | None = None
    payment_cents: int = 0        # 0 = worked out from principal, rate, amortization and frequency
    payment_freq: str = "Monthly"
    note: str = ""
    account_id: int | None = None
    id: int | None = None
    holding_id: int | None = None
    account_name: str = ""        # filled in when read with the company's books
    book_balance: int | None = None
    terms_missing: bool = False   # a debt account in the books with no terms entered yet
    term_months: int = 0
    repayment: str = "Amortizing"  # or "Interest only"
    amort_months: int = 0         # amortizing over (0 = the term)
    asset_account_id: int | None = None   # the asset the loan is matched to on the balance sheet (None = Assets Held)
    book_entry_id: int | None = None      # the journal entry putting it on the balance sheet (-1 = removed in the books)

    @property
    def amortization(self) -> int:
        return self.amort_months or self.term_months

    @property
    def payment(self) -> int:
        """The regular payment: as entered, else worked out."""
        if self.payment_cents:
            return self.payment_cents
        return scheduled_payment(self.principal_cents, self.rate_pct, self.amortization, self.payment_freq,
                                 self.repayment)

    @property
    def annual_debt_service(self) -> int:
        n = PER_YEAR.get(self.payment_freq, 12)
        if not n:
            return self.annual_interest or 0
        return self.payment * n

    @property
    def maturity_date(self) -> str | None:
        """As entered, else the start date + the term."""
        if self.maturity:
            return self.maturity
        if self.start_date and self.term_months:
            return _add_months(date.fromisoformat(self.start_date), self.term_months).isoformat()
        return None

    def scheduled_balance(self, as_of: date) -> int | None:
        """The balance the schedule says is left at ``as_of`` (None without a start date or principal)."""
        if not self.start_date or not self.principal_cents:
            return None
        n = PER_YEAR.get(self.payment_freq, 12)
        start = date.fromisoformat(self.start_date)
        if not n or as_of <= start:
            return self.principal_cents
        made = int((as_of - start).days / 365.25 * n)
        if self.term_months:
            made = min(made, round(self.term_months / 12 * n))
        return scheduled_balance(self.principal_cents, self.rate_pct, self.amortization, self.payment_freq,
                                 self.repayment, made)

    def balloon(self) -> int | None:
        """What's still owed at the end of the term (amortizing over longer than the term, or interest only)."""
        if not self.principal_cents or not self.term_months:
            return None
        n = PER_YEAR.get(self.payment_freq, 12)
        if not n:
            return self.principal_cents
        return scheduled_balance(self.principal_cents, self.rate_pct, self.amortization, self.payment_freq,
                                 self.repayment, round(self.term_months / 12 * n))

    @property
    def balance(self) -> int:
        """Outstanding now: from the books when linked to an account, else as entered, else the principal."""
        if self.book_balance is not None:
            return self.book_balance
        return self.balance_cents if self.balance_cents is not None else self.principal_cents

    @property
    def annual_interest(self) -> int | None:
        return round(self.balance * self.rate_pct / 100) if self.rate_pct is not None else None

    def years_left(self, as_of: date) -> float | None:
        m = self.maturity_date
        if not m:
            return None
        return (date.fromisoformat(m) - as_of).days / 365.25


def _loans_ready(pf: Portfolio) -> None:
    pf.conn.executescript(LOAN_SCHEMA)
    cols = {c[1] for c in pf.conn.execute("PRAGMA table_info(loans)")}
    for col, decl in (("term_months", "INTEGER NOT NULL DEFAULT 0"), ("repayment", "TEXT NOT NULL DEFAULT 'Amortizing'"),
                      ("amort_months", "INTEGER NOT NULL DEFAULT 0"), ("asset_account_id", "INTEGER"),
                      ("book_entry_id", "INTEGER")):
        if col not in cols:
            pf.conn.execute(f"ALTER TABLE loans ADD COLUMN {col} {decl}")
            if col == "repayment":  # 'Interest only' used to be a payment frequency
                pf.conn.execute("UPDATE loans SET repayment='Interest only', payment_freq='Monthly' "
                                "WHERE payment_freq='Interest only'")
    pf.conn.commit()


def debt_accounts(company_path: str | Path, as_of: date | None = None) -> list[tuple[int, str, int]]:
    """[(account id, name, balance owed)] of the loans, mortgages and long-term liabilities in a company's books."""
    from .books import Books

    as_of = as_of or date.today()
    try:
        books = Books.open(company_path, create=False)
    except Exception:  # noqa: BLE001
        return []
    try:
        totals = books.ledger_totals(end=as_of.isoformat())
        return [(a.id, a.name, -totals.get(a.id, 0)) for a in books.accounts()
                if a.type == "liability" and (a.subtype == "long_term" or any(w in a.name.lower() for w in DEBT_WORDS))]
    finally:
        books.close()


def save_loan(pf: Portfolio, holding_id: int, ln: Loan) -> int:
    _loans_ready(pf)
    if not ln.lender.strip():
        raise ValueError("Enter the lender")
    if ln.principal_cents < 0 or (ln.balance_cents or 0) < 0 or ln.payment_cents < 0:
        raise ValueError("Amounts can't be negative")
    if ln.term_months < 0 or ln.amort_months < 0:
        raise ValueError("The term and amortization can't be negative")
    if ln.repayment not in REPAYMENTS:
        raise ValueError(f"Repayment is one of {', '.join(REPAYMENTS)}")
    if ln.payment_freq not in PER_YEAR:
        raise ValueError(f"Unknown payment frequency {ln.payment_freq!r}")
    if ln.rate_pct is not None and not 0 <= ln.rate_pct <= 100:
        raise ValueError("The interest rate must be between 0% and 100%")
    for d in (ln.start_date, ln.maturity):
        if d:
            date.fromisoformat(d)
    vals = (holding_id, ln.lender.strip(), ln.kind, ln.account_id, ln.principal_cents, ln.balance_cents, ln.rate_pct,
            ln.rate_type, ln.start_date, ln.maturity, ln.payment_cents, ln.payment_freq, ln.note.strip(),
            ln.term_months, ln.repayment, ln.amort_months, ln.asset_account_id, ln.book_entry_id)
    with pf.conn:
        if ln.id is None:
            ln.id = pf.conn.execute(
                "INSERT INTO loans(holding_id, lender, kind, account_id, principal_cents, balance_cents, rate_pct, "
                "rate_type, start_date, maturity, payment_cents, payment_freq, note, term_months, repayment, "
                "amort_months, asset_account_id, book_entry_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vals).lastrowid
        else:
            pf.conn.execute("UPDATE loans SET holding_id=?, lender=?, kind=?, account_id=?, principal_cents=?, "
                            "balance_cents=?, rate_pct=?, rate_type=?, start_date=?, maturity=?, payment_cents=?, "
                            "payment_freq=?, note=?, term_months=?, repayment=?, amort_months=?, asset_account_id=?, "
                            "book_entry_id=? WHERE id=?",
                            vals + (ln.id,))
    ln.holding_id = holding_id
    return ln.id


def delete_loan(pf: Portfolio, loan_id: int) -> None:
    _loans_ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM loans WHERE id=?", (loan_id,))


def loans(pf: Portfolio, h: Holding, as_of: date | None = None, *, with_books: bool = True) -> list[Loan]:
    """A company's financing: the loans entered here, with balances read from its books where linked to an
    account, plus any debt account in its books that has no terms entered yet (so nothing is missed)."""
    _loans_ready(pf)
    out = [Loan(r["lender"], r["kind"], r["principal_cents"], r["balance_cents"], r["rate_pct"], r["rate_type"],
                r["start_date"], r["maturity"], r["payment_cents"], r["payment_freq"], r["note"], r["account_id"],
                r["id"], r["holding_id"], term_months=r["term_months"], repayment=r["repayment"],
                amort_months=r["amort_months"], asset_account_id=r["asset_account_id"],
                book_entry_id=r["book_entry_id"])
           for r in pf.conn.execute("SELECT * FROM loans WHERE holding_id=? ORDER BY maturity IS NULL, maturity, id",
                                    (h.id,))]
    if with_books and h.company_path and Path(h.company_path).exists():
        accts = debt_accounts(h.company_path, as_of)
        names = {i: (n, bal) for i, n, bal in accts}
        for ln in out:
            if ln.account_id in names:
                ln.account_name, ln.book_balance = names[ln.account_id]
        linked = {ln.account_id for ln in out}
        for i, n, bal in accts:
            if i not in linked and bal:
                out.append(Loan(n, "Mortgage" if "mortgage" in n.lower() else
                                "Line of credit" if "line of credit" in n.lower() else "Term loan",
                                account_id=i, holding_id=h.id, account_name=n, book_balance=bal, terms_missing=True))
    return out


def loan_debt(pf: Portfolio, h: Holding, on: date | None = None) -> int | None:
    """What's owed on the loans on the Financing table on a day (None when no loans are entered)."""
    if not h.id:
        return None
    on = on or date.today()
    ls = [ln for ln in loans(pf, h, on) if not (ln.start_date and ln.start_date > on.isoformat())]
    return sum(ln.balance or 0 for ln in ls) if ls else None


def valuation_figures(pf: Portfolio, h: Holding, v: Valuation) -> tuple[int | None, int | None, int, bool]:
    """(company value, debt, equity, debt taken from the loans) of a valuation. A valuation saved with no debt
    while the Financing table has loans on its date takes the debt from the loans (the debt box wasn't filled in),
    and its equity is the company value less that debt."""
    debt, from_loans = v.debt_cents, False
    if not debt:
        lb = loan_debt(pf, h, date.fromisoformat(v.date))
        if lb:
            debt, from_loans = lb, True
    if v.company_value_cents is not None:
        return v.company_value_cents, debt, (v.company_value_cents - (debt or 0)) if from_loans else v.value_cents, \
            from_loans
    return (v.value_cents + debt if debt is not None else None), debt, v.value_cents, from_loans


@dataclass
class Financing:
    loans: list[Loan]
    principal: int = 0
    balance: int = 0
    annual_interest: int = 0
    rate: float | None = None          # weighted by balance, of the loans with a rate
    debt_service: int = 0              # payments a year (principal + interest)
    next_maturity: str | None = None
    lenders: str = ""


def financing_totals(rows: list[Loan], share: float = 1.0) -> Financing:
    f = Financing(rows)
    f.principal = round(sum(ln.principal_cents for ln in rows) * share)
    f.balance = round(sum(ln.balance for ln in rows) * share)
    rated = [ln for ln in rows if ln.rate_pct is not None and ln.balance]
    f.annual_interest = round(sum(ln.annual_interest or 0 for ln in rated) * share)
    f.debt_service = round(sum(ln.annual_debt_service for ln in rows) * share)
    weight = sum(ln.balance for ln in rated)
    f.rate = sum(ln.rate_pct * ln.balance for ln in rated) / weight if weight else None
    mats = sorted(ln.maturity_date for ln in rows if ln.maturity_date)
    f.next_maturity = mats[0] if mats else None
    names = list(dict.fromkeys(ln.lender for ln in rows))
    f.lenders = names[0] + (f" +{len(names) - 1}" if len(names) > 1 else "") if names else ""
    return f


# ---------------------------------------------------------------------------
# Sources & uses of funds, per company and for the portfolio
# ---------------------------------------------------------------------------

SU_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources_uses (
    holding_id   INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    item         TEXT NOT NULL,
    amount_cents INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (holding_id, item)
);
"""
SOURCES = ["Debt financing", "Equity – us", "Equity – other owners", "Other sources"]
USES = ["Purchase price", "CapEx / improvements", "Closing & financing costs", "Cash reserve / other"]
PLUG = "Cash reserve / other"


@dataclass
class SourcesUses:
    sources: dict[str, int]
    uses: dict[str, int]
    entered: bool = False      # typed in (else worked out from loans, capital flows, valuations and the books)

    @property
    def total_sources(self) -> int:
        return sum(self.sources.values())

    @property
    def total_uses(self) -> int:
        return sum(self.uses.values())

    def scaled(self, share: float) -> "SourcesUses":
        return SourcesUses({k: round(v * share) for k, v in self.sources.items()},
                           {k: round(v * share) for k, v in self.uses.items()}, self.entered)


def _su_ready(pf: Portfolio) -> None:
    pf.conn.executescript(SU_SCHEMA)


def capex_since(company_path: str | Path, start: str | None, end: date) -> int:
    """Capital spending (debits to fixed-asset accounts, not opening balances) from start to end."""
    try:
        conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    except sqlite3.Error:
        return 0
    try:
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM accounts WHERE type='asset' AND subtype='fixed_asset' AND lower(name) NOT LIKE "
            "'%accumulated%' AND lower(name) NOT LIKE '%depreciation%'")]
        if not ids:
            return 0
        q = (f"SELECT COALESCE(SUM(l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
             f"WHERE l.account_id IN ({','.join('?' * len(ids))}) AND e.kind != 'opening' AND {NOT_FINANCED} "
             f"AND e.date <= ?"
             + (" AND e.date >= ?" if start else ""))
        return conn.execute(q, [*ids, end.isoformat(), *([start] if start else [])]).fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def entered_sources_uses(pf: Portfolio, holding_id: int) -> dict[str, int]:
    _su_ready(pf)
    return {r[0]: r[1] for r in pf.conn.execute("SELECT item, amount_cents FROM sources_uses WHERE holding_id=?",
                                                (holding_id,))}


def save_sources_uses(pf: Portfolio, holding_id: int, amounts: dict[str, int] | None) -> None:
    """Store a company's sources & uses (None or {} = go back to working them out)."""
    _su_ready(pf)
    for k, v in (amounts or {}).items():
        if k not in SOURCES + USES:
            raise ValueError(f"Unknown line {k!r}")
        if v < 0:
            raise ValueError("Amounts can't be negative")
    with pf.conn:
        pf.conn.execute("DELETE FROM sources_uses WHERE holding_id=?", (holding_id,))
        pf.conn.executemany("INSERT INTO sources_uses(holding_id, item, amount_cents) VALUES (?,?,?)",
                            [(holding_id, k, v) for k, v in (amounts or {}).items()])


def sources_uses(pf: Portfolio, p: "Performance", as_of: date | None = None) -> SourcesUses:
    """100% of the company. As entered, or worked out:
    sources = the loans' principal + the equity we put in (capital flows) + the other owners' equity pro rata;
    uses = the purchase price (a 'Purchase price' valuation), capex since acquisition (from the books), and the
    rest as cash reserve / other so the two sides balance."""
    as_of = as_of or date.today()
    h = p.holding
    typed = entered_sources_uses(pf, h.id) if h.id else {}
    if typed:
        return SourcesUses({k: typed.get(k, 0) for k in SOURCES}, {k: typed.get(k, 0) for k in USES}, True)
    src = dict.fromkeys(SOURCES, 0)
    use = dict.fromkeys(USES, 0)
    src["Debt financing"] = sum(ln.principal_cents or ln.balance for ln in loans(pf, h, as_of))
    src["Equity – us"] = p.invested
    if 0 < h.ownership_pct < 100:
        src["Equity – other owners"] = round(p.invested * (100 - h.ownership_pct) / h.ownership_pct)
    buy = next((v for v in pf.valuations(h.id) if v.method == "Purchase price"), None)
    if buy:
        use["Purchase price"] = buy.company_value_cents if buy.company_value_cents is not None else \
            buy.value_cents + (buy.debt_cents or 0)
    if h.company_path and Path(h.company_path).exists():
        use["CapEx / improvements"] = capex_since(h.company_path, h.acquired, as_of)
    gap = sum(src.values()) - sum(use.values())
    if gap >= 0:
        use[PLUG] = gap
    else:
        src["Other sources"] = -gap
    return SourcesUses(src, use, False)


@dataclass
class ScheduleRow:
    number: int                  # payment number (0 = the balloon at maturity)
    date: str
    payment: int
    interest: int
    principal: int
    balance: int                 # left after this payment


def amortization_schedule(ln: Loan, max_rows: int = 2000) -> list[ScheduleRow]:
    """Every scheduled payment over the term (or the amortization when no term is set), with the interest and
    principal in each and the balance left; a last 'balloon' row when something is still owed at maturity."""
    n_year = PER_YEAR.get(ln.payment_freq, 12)
    principal = ln.principal_cents
    if not principal or not ln.start_date:
        return []
    start = date.fromisoformat(ln.start_date)
    months = ln.term_months or ln.amortization
    if not n_year:  # everything at maturity
        if not months:
            return []
        interest = round(principal * (ln.rate_pct or 0) / 100 * months / 12)
        return [ScheduleRow(1, _add_months(start, months).isoformat(), principal + interest, interest, principal, 0)]
    count = round(months / 12 * n_year) if months else 0
    if count <= 0:
        return []
    r = (ln.rate_pct or 0) / 100 / n_year
    pay = ln.payment or 0
    rows: list[ScheduleRow] = []
    bal = principal
    step = {12: 1, 4: 3, 2: 6, 1: 12}.get(n_year)
    for k in range(1, min(count, max_rows) + 1):
        d = _add_months(start, step * k) if step else start + timedelta(days=round(365.25 / n_year * k))
        interest = round(bal * r)
        if ln.repayment == "Interest only":
            prin = 0
        else:
            prin = min(bal, max(0, pay - interest))
            if k == count and ln.amortization <= (ln.term_months or ln.amortization):
                prin = bal  # last payment clears any rounding left over
        bal -= prin
        rows.append(ScheduleRow(k, d.isoformat(), interest + prin, interest, prin, bal))
        if bal <= 0:
            break
    if bal > 0 and rows:
        rows.append(ScheduleRow(0, rows[-1].date, bal, 0, bal, 0))
    return rows


def schedule_by_year(rows: list[ScheduleRow]) -> list[ScheduleRow]:
    """The schedule added up per calendar year (number = the year)."""
    out: dict[int, ScheduleRow] = {}
    for r in rows:
        y = int(r.date[:4])
        t = out.setdefault(y, ScheduleRow(y, f"{y}", 0, 0, 0, 0))
        t.payment += r.payment
        t.interest += r.interest
        t.principal += r.principal
        t.balance = r.balance
    return list(out.values())


# ---------------------------------------------------------------------------
# Cap table: who owns the company, and the capital stack (debt then equity)
# ---------------------------------------------------------------------------

CAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS shareholders (
    id             INTEGER PRIMARY KEY,
    holding_id     INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    share_class    TEXT NOT NULL DEFAULT 'Common',
    shares         REAL NOT NULL DEFAULT 0,
    invested_cents INTEGER NOT NULL DEFAULT 0,
    note           TEXT NOT NULL DEFAULT '',
    company        TEXT NOT NULL DEFAULT ''
);
"""
SHARE_CLASSES = ["Common", "Class A", "Class B", "Preferred", "LP units", "GP units", "Options / warrants", "Other"]
SENIORITY = {"Mortgage": 0, "Construction loan": 0, "Term loan": 1, "Line of credit": 1, "Bond / note": 2,
             "Vendor take-back": 3, "Other": 3, "Shareholder loan": 4}


@dataclass
class Shareholder:
    name: str                    # the owner (a person, or who to contact at the company)
    company: str = ""            # the owner's company, if it holds through one
    amount_cents: int = 0        # what they put in — each owner's % is their amount ÷ the total
    note: str = ""
    id: int | None = None
    share_class: str = "Common"
    pct: float = 0.0             # of the company
    value: int | None = None     # their share of the equity value
    worked_out: bool = False     # not entered: from the holding's ownership %
    funded_cents: int | None = None  # paid in so far (None = the whole amount)
    funded_date: str | None = None   # when it was paid in (for the cash flow)

    @property
    def funded(self) -> int:
        return self.amount_cents if self.funded_cents is None else self.funded_cents

    @property
    def label(self) -> str:
        return f"{self.name} ({self.company})" if self.company and self.name else (self.name or self.company)


@dataclass
class StackRow:
    name: str
    kind: str
    amount: int
    pct: float = 0.0             # of total capitalization
    cumulative: float = 0.0      # loan-to-value through this tranche
    rate: float | None = None
    maturity: str | None = None


def _cap_ready(pf: Portfolio) -> None:
    pf.conn.executescript(CAP_SCHEMA)
    cols = {c[1] for c in pf.conn.execute("PRAGMA table_info(shareholders)")}
    for col, decl in (("company", "TEXT NOT NULL DEFAULT ''"), ("funded_cents", "INTEGER"), ("funded_date", "TEXT")):
        if col not in cols:
            pf.conn.execute(f"ALTER TABLE shareholders ADD COLUMN {col} {decl}")
    pf.conn.commit()


def save_shareholder(pf: Portfolio, holding_id: int, sh: Shareholder) -> int:
    _cap_ready(pf)
    if not sh.name.strip() and not sh.company.strip():
        raise ValueError("Enter the owner's name (or their company's)")
    if sh.amount_cents <= 0:
        raise ValueError("Enter the amount")
    if sh.funded_cents is not None and sh.funded_cents < 0:
        raise ValueError("The amount funded can't be negative")
    if sh.funded_date:
        date.fromisoformat(sh.funded_date)
    vals = (holding_id, sh.name.strip(), sh.company.strip(), sh.share_class.strip() or "Common",
            sh.amount_cents / 100, sh.amount_cents, sh.note.strip(), sh.funded_cents, sh.funded_date)
    with pf.conn:
        if sh.id is None:
            sh.id = pf.conn.execute("INSERT INTO shareholders(holding_id, name, company, share_class, shares, "
                                    "invested_cents, note, funded_cents, funded_date) VALUES (?,?,?,?,?,?,?,?,?)",
                                    vals).lastrowid
        else:
            pf.conn.execute("UPDATE shareholders SET holding_id=?, name=?, company=?, share_class=?, shares=?, "
                            "invested_cents=?, note=?, funded_cents=?, funded_date=? WHERE id=?", vals + (sh.id,))
    return sh.id


def delete_shareholder(pf: Portfolio, shareholder_id: int) -> None:
    _cap_ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM shareholders WHERE id=?", (shareholder_id,))


def cap_table(pf: Portfolio, p: "Performance", owner_name: str = "") -> list[Shareholder]:
    """The owners as entered (each one's % = their amount ÷ the total); without any, worked out from the holding:
    us at the ownership % (with the capital we put in) and 'Other owners' for the rest."""
    _cap_ready(pf)
    h = p.holding
    rows = [Shareholder(r["name"], r["company"], r["invested_cents"], r["note"], r["id"], r["share_class"],
                        funded_cents=r["funded_cents"], funded_date=r["funded_date"])
            for r in pf.conn.execute("SELECT * FROM shareholders WHERE holding_id=? ORDER BY share_class, id",
                                     (h.id,))]
    if rows:
        total = sum(x.amount_cents for x in rows)
        for x in rows:
            x.pct = x.amount_cents / total if total else 0.0
    else:
        us = Shareholder("Us", owner_name, p.invested, worked_out=True, pct=h.ownership_pct / 100)
        rows = [us]
        if h.ownership_pct < 100:
            other = round(p.invested * (100 - h.ownership_pct) / h.ownership_pct) if p.invested else 0
            rows.append(Shareholder("Other owners", "", other, worked_out=True, pct=1 - h.ownership_pct / 100))
    for x in rows:
        x.value = round(p.value_100 * x.pct) if p.value_100 is not None else None
    return rows


def class_totals(rows: list[Shareholder]) -> dict[str, tuple[int, int, float]]:
    """{share class: (equity, funded, % of all equity)} in the order the classes first appear."""
    total = sum(x.amount_cents for x in rows)
    out: dict[str, list] = {}
    for x in rows:
        t = out.setdefault(x.share_class or "Common", [0, 0])
        t[0] += x.amount_cents
        t[1] += x.funded
    return {k: (a, f, a / total if total else 0.0) for k, (a, f) in out.items()}


def read_equity_table(rows: list[list]) -> list[Shareholder]:
    """Equity contributions from a sheet laid out like: a heading such as "Class A Shares", a row with
    "Name of Investor / Name of Corporation / Equity (/ Funded)", then one row per contribution; repeated for each
    class. Totals and blank rows are skipped."""
    import re as _re

    def money(v) -> int | None:
        if isinstance(v, (int, float)):
            return round(float(v) * 100)
        s_ = _re.sub(r"[^0-9.\-]", "", str(v or ""))
        try:
            return round(float(s_) * 100) if s_ not in ("", "-", ".") else None
        except ValueError:
            return None

    out: list[Shareholder] = []
    cls, cols = "Common", None
    for row in rows:
        cells = [str(c).strip() if c is not None else "" for c in row]
        low = [c.lower() for c in cells]
        m = next((_re.search(r"(class\s+\w+|preferred|common|lp units|gp units)", c, _re.I) for c in cells
                  if _re.search(r"^(class\s+\w+|preferred|common|lp units|gp units)(\s+shares|\s+units)?$", c, _re.I)),
                 None)
        if m:  # a class heading (its row may also hold the column headings)
            cls = m.group(1).title().replace("Lp ", "LP ").replace("Gp ", "GP ")
        if any("investor" in c for c in low) and any("equity" in c or "amount" in c for c in low):
            funded_col = next((i for i, c in enumerate(low) if "funded" in c), None)
            cols = {"name": next(i for i, c in enumerate(low) if "investor" in c),
                    "company": next((i for i, c in enumerate(low) if "corporation" in c or "company" in c), None),
                    "amount": next(i for i, c in enumerate(low) if "equity" in c or "amount" in c),
                    # a later class's headings may leave out "Funded": keep the column found before
                    "funded": funded_col if funded_col is not None else (cols or {}).get("funded")}
            continue
        if m:
            continue
        if cols is None or not any(cells):
            continue
        name = cells[cols["name"]] if cols["name"] < len(cells) else ""
        if not name or name.lower().startswith("total"):
            continue
        amt = money(row[cols["amount"]]) if cols["amount"] < len(row) else None
        if not amt or amt <= 0:
            continue
        company = cells[cols["company"]] if cols["company"] is not None and cols["company"] < len(cells) else ""
        funded = money(row[cols["funded"]]) if cols["funded"] is not None and cols["funded"] < len(row) else None
        out.append(Shareholder(name, company if company != name else "", amt, share_class=cls,
                               funded_cents=funded if funded is not None else 0))
    if not out:
        raise ValueError("Couldn't find an equity table (a heading row with Name of Investor and Equity)")
    return out


def read_equity_file(path) -> tuple[list[Shareholder], str]:
    """Equity contributions from an Excel workbook (a sheet named like 'Equity' first) or a CSV."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        import csv

        with open(path, newline="", encoding="utf-8-sig") as f:
            return read_equity_table(list(csv.reader(f))), path.name
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheets = sorted(wb.worksheets, key=lambda ws: (0 if "equity" in ws.title.lower() else 1))
        problem = None
        for ws in sheets:
            try:
                return read_equity_table([list(r) for r in ws.iter_rows(max_row=300, max_col=12,
                                                                         values_only=True)]), ws.title
            except ValueError as e:
                problem = e
        raise problem or ValueError("No sheets")
    finally:
        wb.close()


def capital_stack(pf: Portfolio, p: "Performance", as_of: date | None = None) -> list[StackRow]:
    """Debt from most senior down, then equity, as shares of the company's total value."""
    loans_ = sorted((ln for ln in loans(pf, p.holding, as_of) if ln.balance),
                    key=lambda ln: (SENIORITY.get(ln.kind, 3), ln.maturity_date or "9999"))
    rows = [StackRow(f"{ln.lender} – {ln.kind}" if not ln.terms_missing else ln.lender, ln.kind, ln.balance,
                     rate=ln.rate_pct, maturity=ln.maturity_date) for ln in loans_]
    debt = sum(r.amount for r in rows)
    equity = (p.company_value - debt) if p.company_value is not None else (p.value_100 or 0)
    rows.append(StackRow("Equity", "Equity", max(0, equity)))
    total = sum(r.amount for r in rows)
    run = 0
    for r in rows:
        run += r.amount
        r.pct = r.amount / total if total else 0.0
        r.cumulative = run / total if total else 0.0
    return rows
