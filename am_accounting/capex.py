"""Capital expenditure schedule for one company in the portfolio, linked to its books.

Budget lines are grouped (Hard Costs, Fees, Soft Costs, Closing Costs, Acquisition, Interest — or your own groups).
Each line has a budget and can be linked to an account in the company's books: its **cost to date** and monthly
spending are then what was recorded to that account (debits less credits, opening balances included), plus any
cost entered by hand that isn't in the books. Cost to complete = budget − cost to date.

**Create Account in the Books** adds a fixed-asset account for a line (e.g. "CapEx – Roofing") and links it, so
paying a contractor and categorizing the payment to it updates the capex schedule.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS capex_lines (
    id            INTEGER PRIMARY KEY,
    holding_id    INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    grp           TEXT NOT NULL DEFAULT 'Hard Costs',
    item          TEXT NOT NULL,
    budget_cents  INTEGER NOT NULL DEFAULT 0,
    account_id    INTEGER,                       -- the account in the company's books ('' = not linked)
    manual_cents  INTEGER NOT NULL DEFAULT 0,    -- cost to date not in the books
    notes         TEXT NOT NULL DEFAULT '',
    sort          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_capex_h ON capex_lines(holding_id, sort);
"""
GROUPS = ["Hard Costs", "Fees", "Soft Costs", "Closing Costs", "Acquisition Cost", "Interest Cost", "Other"]
TEMPLATE = {
    "Hard Costs": ["In-suite renovations", "Common area renovations", "Roofing", "Exterior / brick work",
                   "Paving & parking", "Mechanical / HVAC", "Fire safety", "Cash for keys", "Contingency"],
    "Fees": ["GC fees", "HST / GST"],
    "Soft Costs": ["Building condition assessment", "Environmental (ESA)", "Appraisal", "Architect / design",
                   "Permits", "Energy consultant"],
    "Closing Costs": ["Land transfer tax", "Legal", "Acquisition fees", "Lender fees", "Title insurance"],
    "Acquisition Cost": ["Purchase price"],
}


@dataclass
class CapexLine:
    item: str
    grp: str = "Hard Costs"
    budget_cents: int = 0
    account_id: int | None = None
    manual_cents: int = 0
    notes: str = ""
    id: int | None = None
    sort: int = 0
    account_name: str = ""
    book_cents: int = 0          # cost to date from the books
    monthly: dict | None = None  # {'YYYY-MM': cents} from the books

    @property
    def to_date(self) -> int:
        return self.book_cents + self.manual_cents

    @property
    def to_complete(self) -> int:
        return self.budget_cents - self.to_date

    @property
    def pct_complete(self) -> float | None:
        return self.to_date / self.budget_cents if self.budget_cents else None


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


def save_line(pf, holding_id: int, ln: CapexLine) -> int:
    ready(pf)
    if not ln.item.strip():
        raise ValueError("Enter the cost item")
    if ln.budget_cents < 0:
        raise ValueError("The budget can't be negative")
    vals = (holding_id, ln.grp.strip() or "Other", ln.item.strip(), ln.budget_cents, ln.account_id, ln.manual_cents,
            ln.notes.strip(), ln.sort)
    with pf.conn:
        if ln.id is None:
            if not ln.sort:
                ln.sort = (pf.conn.execute("SELECT COALESCE(MAX(sort), 0) FROM capex_lines WHERE holding_id=?",
                                           (holding_id,)).fetchone()[0] or 0) + 1
                vals = vals[:-1] + (ln.sort,)
            ln.id = pf.conn.execute("INSERT INTO capex_lines(holding_id, grp, item, budget_cents, account_id, "
                                    "manual_cents, notes, sort) VALUES (?,?,?,?,?,?,?,?)", vals).lastrowid
        else:
            pf.conn.execute("UPDATE capex_lines SET holding_id=?, grp=?, item=?, budget_cents=?, account_id=?, "
                            "manual_cents=?, notes=?, sort=? WHERE id=?", vals + (ln.id,))
    return ln.id


def delete_line(pf, line_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM capex_lines WHERE id=?", (line_id,))


def add_template(pf, holding_id: int) -> int:
    """Start a schedule with the usual cost items (budgets at zero)."""
    n = 0
    for grp, items in TEMPLATE.items():
        for item in items:
            save_line(pf, holding_id, CapexLine(item, grp))
            n += 1
    return n


def _book_activity(company_path: str, account_ids: list[int], as_of: date) -> tuple[dict, dict, dict]:
    """({account: cost to date}, {account: {'YYYY-MM': cents}}, {account: name}) from the books."""
    if not account_ids or not company_path or not Path(company_path).exists():
        return {}, {}, {}
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(account_ids))
        names = dict(conn.execute(f"SELECT id, name FROM accounts WHERE id IN ({q})", account_ids).fetchall())
        totals: dict = {}
        months: dict = {}
        for acct, ym, net in conn.execute(
                f"SELECT l.account_id, substr(e.date, 1, 7), SUM(l.debit_cents - l.credit_cents) FROM journal_lines l "
                f"JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id IN ({q}) AND e.date <= ? "
                f"GROUP BY l.account_id, substr(e.date, 1, 7)", [*account_ids, as_of.isoformat()]):
            totals[acct] = totals.get(acct, 0) + net
            months.setdefault(acct, {})[ym] = net
        return totals, months, names
    finally:
        conn.close()


def lines(pf, holding, as_of: date | None = None) -> list[CapexLine]:
    """The schedule with cost to date and monthly spending read from the company's books."""
    ready(pf)
    as_of = as_of or date.today()
    out = [CapexLine(r["item"], r["grp"], r["budget_cents"], r["account_id"], r["manual_cents"], r["notes"], r["id"],
                     r["sort"])
           for r in pf.conn.execute("SELECT * FROM capex_lines WHERE holding_id=? ORDER BY sort, id", (holding.id,))]
    ids = sorted({ln.account_id for ln in out if ln.account_id is not None})
    totals, months, names = _book_activity(holding.company_path, ids, as_of)
    # an account shared by several lines counts once, on the first of them
    seen: set[int] = set()
    for ln in out:
        if ln.account_id is None:
            continue
        ln.account_name = names.get(ln.account_id, "(account not found)")
        if ln.account_id not in seen:
            ln.book_cents = totals.get(ln.account_id, 0)
            ln.monthly = months.get(ln.account_id, {})
            seen.add(ln.account_id)
    return out


@dataclass
class Totals:
    budget: int = 0
    to_date: int = 0
    to_complete: int = 0
    by_group: dict | None = None


def totals(rows: list[CapexLine]) -> Totals:
    t = Totals(by_group={})
    for ln in rows:
        t.budget += ln.budget_cents
        t.to_date += ln.to_date
        t.to_complete += ln.to_complete
        g = t.by_group.setdefault(ln.grp, [0, 0, 0])
        g[0] += ln.budget_cents
        g[1] += ln.to_date
        g[2] += ln.to_complete
    return t


def monthly_outflows(rows: list[CapexLine], months: list[str]) -> dict[str, int]:
    """Spending recorded in the books per month ('YYYY-MM'), all lines."""
    out = dict.fromkeys(months, 0)
    for ln in rows:
        for ym, cents in (ln.monthly or {}).items():
            if ym in out:
                out[ym] += cents
    return out


def create_account(pf, holding, ln: CapexLine) -> int:
    """A fixed-asset account in the company's books for this line ("CapEx – <item>"), linked to it."""
    from .books import Books

    if not holding.company_path or not Path(holding.company_path).exists():
        raise ValueError("Link this company to its books first")
    books = Books.open(holding.company_path, create=False)
    try:
        used = {a.code for a in books.accounts()}
        code = "1650"
        while code in used:
            code = str(int(code) + 1)
        name = f"CapEx – {ln.item}"
        base, n = name, 2
        while any(a.name.lower() == name.lower() for a in books.accounts()):
            name, n = f"{base} ({n})", n + 1
        acct = books.add_account(code, name, "asset", "fixed_asset").id
    finally:
        books.close()
    ln.account_id = acct
    save_line(pf, holding.id, ln)
    return acct


def book_accounts(company_path: str) -> list[tuple[int, str]]:
    """Accounts a capex line can be linked to: fixed assets first, then other assets and expenses."""
    if not company_path or not Path(company_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, code, name, type, subtype FROM accounts WHERE active=1 AND type IN "
                            "('asset','expense') AND is_bank=0 ORDER BY (subtype!='fixed_asset'), type, code"
                            ).fetchall()
    finally:
        conn.close()
    return [(r[0], f"{r[1]} · {r[2]}") for r in rows]
