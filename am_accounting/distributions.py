"""Distributions (a company's Distributions tab): how much cash a company has, what to keep back as a reserve, what's
available to pay out, the distributions planned (and the cash left after each), and what's been paid.

* **Cash** comes from the company's books.
* **Reserve**: a set amount, or a number of months of its expenses (Op. Ex + Non-Op. Ex, last twelve months).
* **Available to distribute** = cash − reserve.
* **Net cash flow a month**: its projections for this year ÷ 12, else the last twelve months' net cash flow ÷ 12.
* **Planned distributions** (100%, the whole company): the cash after each one = cash now + the months of cash flow
  until then − the distributions planned up to it; below the reserve is flagged.
* **Marking one paid** records it on the holding (at our share), so it's in the portfolio's returns and your personal
  income (Income from Businesses).

Kept in the portfolio file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import portfolio as pfm

SCHEMA = """
CREATE TABLE IF NOT EXISTS distribution_plan (
    id          INTEGER PRIMARY KEY,
    holding_id  INTEGER NOT NULL,
    date        TEXT NOT NULL,
    amount      INTEGER NOT NULL,            -- cents, the whole company (100%)
    note        TEXT NOT NULL DEFAULT '',
    paid        INTEGER NOT NULL DEFAULT 0,
    flow_id     INTEGER                      -- the distribution recorded on the holding when paid
);
CREATE TABLE IF NOT EXISTS distribution_reserve (
    holding_id  INTEGER PRIMARY KEY,
    amount      INTEGER NOT NULL DEFAULT 0,  -- cents
    months      REAL NOT NULL DEFAULT 0      -- or this many months of expenses
);
"""


def _ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


@dataclass
class Planned:
    date: str
    amount: int                    # 100%
    note: str = ""
    paid: bool = False
    id: int | None = None
    flow_id: int | None = None


@dataclass
class Reserve:
    amount: int = 0
    months: float = 0.0


def plans(pf, holding_id: int) -> list[Planned]:
    _ready(pf)
    return [Planned(r["date"], r["amount"], r["note"], bool(r["paid"]), r["id"], r["flow_id"])
            for r in pf.conn.execute("SELECT * FROM distribution_plan WHERE holding_id=? ORDER BY date, id",
                                     (holding_id,))]


def save_plan(pf, holding_id: int, p: Planned) -> int:
    _ready(pf)
    date.fromisoformat(p.date)
    if p.amount <= 0:
        raise ValueError("The amount must be more than zero")
    with pf.conn:
        if p.id is None:
            p.id = pf.conn.execute("INSERT INTO distribution_plan(holding_id, date, amount, note, paid, flow_id) "
                                   "VALUES (?,?,?,?,?,?)", (holding_id, p.date, p.amount, p.note.strip(), int(p.paid),
                                                             p.flow_id)).lastrowid
        else:
            pf.conn.execute("UPDATE distribution_plan SET date=?, amount=?, note=?, paid=?, flow_id=? WHERE id=?",
                            (p.date, p.amount, p.note.strip(), int(p.paid), p.flow_id, p.id))
    return p.id


def delete_plan(pf, plan_id: int) -> None:
    _ready(pf)
    row = pf.conn.execute("SELECT flow_id FROM distribution_plan WHERE id=?", (plan_id,)).fetchone()
    if row and row["flow_id"]:
        pf.delete_flow(row["flow_id"])
    with pf.conn:
        pf.conn.execute("DELETE FROM distribution_plan WHERE id=?", (plan_id,))


def mark_paid(pf, h: pfm.Holding, p: Planned, paid: bool = True) -> None:
    """Paid: recorded on the holding as a distribution (at our share) — or, unmarked, taken off again."""
    if paid and not p.flow_id:
        ours = round(p.amount * h.ownership_pct / 100)
        if ours > 0:
            p.flow_id = pf.add_flow(h.id, pfm.Flow(p.date, "distribution", ours,
                                                   f"Distribution{': ' + p.note if p.note else ''} (planned)"))
    elif not paid and p.flow_id:
        pf.delete_flow(p.flow_id)
        p.flow_id = None
    p.paid = paid
    save_plan(pf, h.id, p)


def reserve(pf, holding_id: int) -> Reserve:
    _ready(pf)
    r = pf.conn.execute("SELECT amount, months FROM distribution_reserve WHERE holding_id=?", (holding_id,)).fetchone()
    return Reserve(r["amount"], r["months"]) if r else Reserve()


def set_reserve(pf, holding_id: int, r: Reserve) -> None:
    _ready(pf)
    with pf.conn:
        pf.conn.execute("INSERT INTO distribution_reserve(holding_id, amount, months) VALUES (?,?,?) "
                        "ON CONFLICT(holding_id) DO UPDATE SET amount=excluded.amount, months=excluded.months",
                        (holding_id, max(r.amount, 0), max(r.months, 0.0)))


@dataclass
class PlanRow:
    plan: Planned
    cash_before: int
    cash_after: int
    ok: bool                       # cash after stays at or above the reserve
    ours: int                      # our share of it


@dataclass
class Position:
    has_books: bool
    cash: int = 0
    reserve: int = 0
    reserve_basis: str = ""
    monthly_ncf: int = 0
    ncf_basis: str = ""
    rows: list[PlanRow] = field(default_factory=list)
    paid_by_month: dict[str, int] = field(default_factory=dict)   # "YYYY-MM": paid (100%), last 12 months

    @property
    def available(self) -> int:
        return max(self.cash - self.reserve, 0)

    @property
    def planned_ahead(self) -> int:
        return sum(r.plan.amount for r in self.rows if not r.plan.paid)


def _months_between(a: date, b: date) -> int:
    return max((b.year - a.year) * 12 + b.month - a.month, 0)


def position(pf, p: pfm.Performance, today: date | None = None) -> Position:
    today = today or date.today()
    h = p.holding
    f = p.financials if p.has_books else None
    pos = Position(f is not None)
    pos.cash = f.cash if f else 0
    proj = {x.year: x for x in pfm.projections(pf, h.id)}.get(today.year)
    if proj is not None:
        pos.monthly_ncf = round((proj.revenue - proj.opex - proj.nonop - proj.capex) / 12)
        pos.ncf_basis = f"{today.year} projections"
    elif p.net_cash_flow is not None:
        pos.monthly_ncf = round(p.net_cash_flow / 12)
        pos.ncf_basis = "last 12 months"
    r = reserve(pf, h.id)
    if r.months and f:
        pos.reserve = round((f.opex_ltm + f.nonop_ltm) / 12 * r.months)
        pos.reserve_basis = f"{r.months:g} months of expenses"
    else:
        pos.reserve = r.amount
        pos.reserve_basis = "set amount" if r.amount else "none set"
    cash, out = pos.cash, 0
    for pl in plans(pf, h.id):
        if pl.paid:
            continue
        when = date.fromisoformat(pl.date)
        before = cash + pos.monthly_ncf * _months_between(today, when) - out
        out += pl.amount
        after = before - pl.amount
        pos.rows.append(PlanRow(pl, before, after, after >= pos.reserve, round(pl.amount * h.ownership_pct / 100)))
    for pl in plans(pf, h.id):                       # paid ones listed too, after the planned
        if pl.paid:
            pos.rows.append(PlanRow(pl, 0, 0, True, round(pl.amount * h.ownership_pct / 100)))
    if h.company_path:
        from . import personal_finance as pfi

        for y in (today.year - 1, today.year):
            for m, v in pfi.paid_from_books(h.company_path, y, today).items():
                key = f"{y}-{m:02d}"
                if _months_between(date(y, m, 1), today) < 12:
                    pos.paid_by_month[key] = pos.paid_by_month.get(key, 0) + v
    return pos


def owners_split(pf, h: pfm.Holding, amount: int) -> list[tuple[str, int]]:
    """Each owner's part of a distribution of ``amount`` (100%), from the cap table."""
    out = []
    for x in pfm.cap_table(pf, pfm.performance(pf, h, with_financials=False)):
        out.append((x.name or x.company, round(amount * x.pct)))
    return out
