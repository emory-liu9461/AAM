"""Exit assumptions and the projected returns (IRR, MOIC) of a company's projections.

* **Exit:** the date (the same planned exit as on the Dashboard), the exit value — the projected NOI / EBITDA at the
  exit (the exit year's, or the year after's) ÷ an exit cap rate or × a multiple, or a value entered — less selling
  costs and the debt repaid at exit (the loans' scheduled balance on that date unless entered). The company is
  wound up with the sale: every other debt account is repaid, receivables are collected and payables paid from the
  proceeds, and all its cash is paid out to the owners (the balances in the books today — each projected year's
  cash flow is assumed paid out as it comes, so the cash on hand today is what's left at the exit).
* **Cash flows to the owners (100% of the company):** the money they put in (the cap table's paid-in amounts on their
  dates; without a cap table, the capital put in on the holding), each projected year's cash to owners up to the exit
  (cash flow after debt service — the projections' net cash flow after financing, with equity contributions and
  distributions counted as the owners' own flows), and the net sale proceeds at the exit.
* **Returns:** IRR (dated flows), MOIC (money back ÷ money in) and profit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from . import portfolio as pfm

SCHEMA = """
CREATE TABLE IF NOT EXISTS exit_assumptions (
    holding_id    INTEGER PRIMARY KEY,
    exit_cap_pct  REAL NOT NULL DEFAULT 5.0,
    exit_value    INTEGER NOT NULL DEFAULT 0,      -- entered instead of the cap rate (0 = use the cap rate)
    selling_pct   REAL NOT NULL DEFAULT 2.0,       -- commissions, legal … as % of the exit value
    debt_at_exit  INTEGER,                         -- NULL = the loans' scheduled balance at the exit date
    method        TEXT NOT NULL DEFAULT 'cap',     -- cap (NOI ÷ cap rate) | multiple (NOI / EBITDA × multiple)
    multiple      REAL NOT NULL DEFAULT 10.0,
    basis         TEXT NOT NULL DEFAULT 'exit_year' -- exit_year | next_year (forward)
);
"""
METHODS = {"cap": "Cap rate (NOI ÷ cap rate)", "multiple": "Multiple (NOI / EBITDA × multiple)"}
BASES = {"exit_year": "the exit year's NOI", "next_year": "the year after the exit (forward NOI)"}


def _ready(pf) -> None:
    pf.conn.executescript(SCHEMA)
    cols = {r[1] for r in pf.conn.execute("PRAGMA table_info(exit_assumptions)")}
    with pf.conn:
        for col, decl in (("method", "TEXT NOT NULL DEFAULT 'cap'"), ("multiple", "REAL NOT NULL DEFAULT 10.0"),
                          ("basis", "TEXT NOT NULL DEFAULT 'exit_year'")):
            if col not in cols:
                pf.conn.execute(f"ALTER TABLE exit_assumptions ADD COLUMN {col} {decl}")
EQUITY_IN = ("equity", "contribution", "capital call")
OWNERS_OUT = ("distribution", "dividend")


@dataclass
class ExitAssumptions:
    exit_date: str | None = None
    exit_cap_pct: float = 5.0
    exit_value: int = 0
    selling_pct: float = 2.0
    debt_at_exit: int | None = None
    method: str = "cap"            # cap | multiple
    multiple: float = 10.0
    basis: str = "exit_year"       # exit_year | next_year


def get(pf, h) -> ExitAssumptions:
    from . import operations as op

    _ready(pf)
    r = pf.conn.execute("SELECT * FROM exit_assumptions WHERE holding_id=?", (h.id,)).fetchone()
    a = ExitAssumptions(op.dash_info(pf, h.id).exit_date)
    if h.type == "Operating company":
        a.method = "multiple"
    if r:
        a.exit_cap_pct, a.exit_value, a.selling_pct, a.debt_at_exit = (r["exit_cap_pct"], r["exit_value"],
                                                                       r["selling_pct"], r["debt_at_exit"])
        a.method, a.multiple, a.basis = r["method"], r["multiple"], r["basis"]
    return a


def save(pf, h, a: ExitAssumptions) -> None:
    """Keep the exit assumptions; the exit date is the Dashboard's planned exit."""
    from . import operations as op

    if a.exit_date:
        date.fromisoformat(a.exit_date)
    if a.method not in METHODS or a.basis not in BASES:
        raise ValueError("Unknown exit valuation method")
    if not a.exit_value and a.method == "cap" and not 0 < a.exit_cap_pct < 50:
        raise ValueError("Enter an exit cap rate between 0% and 50%, or an exit value")
    if not a.exit_value and a.method == "multiple" and not 0 < a.multiple < 100:
        raise ValueError("Enter an exit multiple above 0, or an exit value")
    if a.exit_value < 0 or (a.debt_at_exit or 0) < 0 or not 0 <= a.selling_pct < 50:
        raise ValueError("Amounts can't be negative, and selling costs are under 50%")
    _ready(pf)
    with pf.conn:
        pf.conn.execute("INSERT INTO exit_assumptions(holding_id, exit_cap_pct, exit_value, selling_pct, debt_at_exit, "
                        "method, multiple, basis) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(holding_id) DO UPDATE SET "
                        "exit_cap_pct=excluded.exit_cap_pct, exit_value=excluded.exit_value, "
                        "selling_pct=excluded.selling_pct, debt_at_exit=excluded.debt_at_exit, method=excluded.method, "
                        "multiple=excluded.multiple, basis=excluded.basis",
                        (h.id, a.exit_cap_pct, a.exit_value, a.selling_pct, a.debt_at_exit, a.method, a.multiple,
                         a.basis))
    info = op.dash_info(pf, h.id)
    info.exit_date = a.exit_date
    op.save_dash_info(pf, h.id, info)


def scheduled_debt(pf, h, on: date) -> int:
    """What the loans on Financing & Cap Table are scheduled to owe on a date."""
    total = 0
    for ln in pfm.loans(pf, h, with_books=False):
        if ln.start_date and ln.start_date > on.isoformat():
            continue
        if ln.maturity_date and ln.maturity_date < on.isoformat():
            continue            # repaid at maturity (the balloon is in the projections' principal)
        sb = ln.scheduled_balance(on) if ln.start_date else None
        total += sb if sb is not None else ln.balance
    return total


@dataclass
class Returns:
    exit_date: date
    noi_year: int = 0              # the projected year whose NOI / EBITDA values the exit
    exit_noi: int = 0
    exit_value: int = 0
    selling_costs: int = 0
    debt_repaid: int = 0          # the loans on Financing & Cap Table
    other_debt: int = 0           # other debt accounts in the books (not on Financing; owners' loans aside)
    receivables: int = 0          # collected
    payables: int = 0             # paid
    cash: int = 0                 # paid out
    net_proceeds: int = 0
    flows: list[tuple[date, int, str]] = field(default_factory=list)    # to the owners: (date, amount, what)
    irr: float | None = None
    error: str = ""

    @property
    def money_in(self) -> int:
        return -sum(a for _d, a, _w in self.flows if a < 0)

    @property
    def money_back(self) -> int:
        return sum(a for _d, a, _w in self.flows if a > 0)

    @property
    def moic(self) -> float | None:
        return self.money_back / self.money_in if self.money_in else None

    @property
    def profit(self) -> int:
        return self.money_back - self.money_in


def _is(name: str, words) -> bool:
    return any(w in name.lower() for w in words)


def projected_returns(pf, h, years: list[int], lines: list[pfm.ProjectionLine], nonop: dict[int, int],
                      a: ExitAssumptions, as_of: date | None = None) -> Returns:
    """The owners' cash flows to the exit and the IRR / MOIC (see the module notes)."""
    as_of = as_of or date.today()
    if not a.exit_date:
        last = max(years) if years else as_of.year + 5
        a.exit_date = f"{last}-12-31"
    ex = date.fromisoformat(a.exit_date)
    r = Returns(ex)

    def amt(kind: str, y: int, pick=None) -> int:
        return sum(ln.amounts.get(y, 0) for ln in lines if ln.kind == kind and (pick is None or pick(ln.item)))

    # money the owners put in so far
    holders = [x for x in pfm.cap_table(pf, SimpleNamespace(holding=h, invested=0, value_100=None))
               if not x.worked_out and x.funded] if h.id else []
    if holders:
        for x in holders:
            d = date.fromisoformat(x.funded_date or h.acquired or as_of.isoformat())
            r.flows.append((d, -x.funded, f"{x.name or x.company} paid in"))
    else:
        p = pfm.performance(pf, h, as_of, with_financials=False)
        share = (h.ownership_pct or 100) / 100
        for f in p.flows:
            if f.signed < 0:
                r.flows.append((date.fromisoformat(f.date), round(-f.amount_cents / share), f.label))
            elif f.signed > 0:
                r.flows.append((date.fromisoformat(f.date), round(f.amount_cents / share), f.label))
    if not r.flows:
        r.error = "No money put in yet — add the owners to the cap table (Financing & Cap Table)"
    # each projected year up to the exit: cash to owners
    for y in sorted(years):
        if y > ex.year:
            break
        part = 1.0 if y < ex.year else (ex - date(y, 1, 1)).days / ((date(y, 12, 31) - date(y, 1, 1)).days or 1)
        ops = amt("revenue", y) - amt("opex", y) - nonop.get(y, 0) - amt("capex", y)
        # in the exit year, principal isn't paid separately: the debt repaid from the sale covers what's owed then
        keep_out = (lambda n: not _is(n, OWNERS_OUT) and not n.lower().startswith("principal")) if y == ex.year \
            else (lambda n: not _is(n, OWNERS_OUT))
        debt = amt("fin_in", y, lambda n: not _is(n, EQUITY_IN)) - amt("fin_out", y, keep_out)
        equity_in = amt("fin_in", y, lambda n: _is(n, EQUITY_IN))
        cash = round((ops + debt) * part)
        when = min(date(y, 12, 31), ex)
        if equity_in:
            r.flows.append((date(y, 6, 30) if date(y, 6, 30) <= ex else ex, -round(equity_in * part),
                            f"{y} equity contributions"))
        if cash:
            r.flows.append((when, cash, f"{y} cash to owners" + (" (to the exit)" if part < 1 else "")))
    # the sale: the projected NOI / EBITDA at the exit, at the cap rate or multiple
    exit_year = ex.year
    if a.basis == "next_year":
        exit_year = next((y for y in sorted(years) if y > ex.year), ex.year)
    elif exit_year not in years and years:       # past the projections: the last projected year
        exit_year = max(y for y in years if y <= ex.year) if any(y <= ex.year for y in years) else min(years)
    r.noi_year = exit_year
    r.exit_noi = amt("revenue", exit_year) - amt("opex", exit_year)
    if a.exit_value:
        r.exit_value = a.exit_value
    elif r.exit_noi > 0 and a.method == "multiple" and a.multiple:
        r.exit_value = round(r.exit_noi * a.multiple)
    elif r.exit_noi > 0 and a.exit_cap_pct:
        r.exit_value = round(r.exit_noi / (a.exit_cap_pct / 100))
    r.selling_costs = round(r.exit_value * a.selling_pct / 100)
    r.debt_repaid = a.debt_at_exit if a.debt_at_exit is not None else scheduled_debt(pf, h, ex)
    _wind_up(pf, h, r, as_of)
    r.net_proceeds = (r.exit_value - r.selling_costs - r.debt_repaid - r.other_debt + r.receivables - r.payables
                      + r.cash)
    if r.exit_value:
        r.flows.append((ex, r.net_proceeds, "sale, debts and payables paid, cash paid out"))
    elif not r.error:
        r.error = "No exit value — enter projections (the NOI after the exit) or an exit value"
    r.flows.sort(key=lambda f: f[0])
    r.irr = pfm.xirr([(d, v) for d, v, _w in r.flows])
    return r


def _wind_up(pf, h, r: Returns, as_of: date) -> None:
    """The balance sheet settled at the exit: cash paid out, receivables collected, payables and every other debt
    account paid (balances in the books today)."""
    from pathlib import Path

    from . import fin_recon

    path = h.company_path
    if not path or not Path(path).exists():
        return
    f = pfm.company_financials(path, as_of)
    if f.error:
        return
    r.cash, r.receivables, r.payables = max(f.cash, 0), f.receivables, f.payables
    rec = fin_recon.reconcile(pf, h, as_of)
    r.other_debt = sum(x.books or 0 for x in rec.debt if x.financing is None)
