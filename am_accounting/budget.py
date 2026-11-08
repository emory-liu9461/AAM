"""Budget vs actual for one property: an annual operating budget, month by month, against the books.

* **The budget** is made from the company's projections for the year (each revenue and operating-expense line, split
  evenly over the twelve months) and can then be changed month by month. It's a copy: changing the projections later
  doesn't change a budget already made.
* **Each budget line is matched to accounts in the books** — the accounts picked for it, or (when none are picked)
  the income / expense accounts with the same name or one containing the other. Operating accounts with activity that
  no line takes are shown as "Not in the budget".
* **Variance**: actual − budget, month and year to date. For revenue more is good; for expenses less is. A line is
  flagged when it's off by at least 10% *and* $500 year to date (both can be changed).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS budget_lines (
    id          INTEGER PRIMARY KEY,
    holding_id  INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    kind        TEXT NOT NULL,               -- revenue | opex
    item        TEXT NOT NULL,
    accounts    TEXT NOT NULL DEFAULT '[]',  -- names of the accounts in the books it's compared with
    months      TEXT NOT NULL DEFAULT '[]',  -- 12 amounts in cents
    sort        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_budget ON budget_lines(holding_id, year, sort);
"""
KINDS = {"revenue": "Revenue", "opex": "Operating expenses"}
FLAG_PCT, FLAG_MIN = 10.0, 500_00
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class BudgetLine:
    kind: str
    item: str
    months: list[int] = field(default_factory=lambda: [0] * 12)
    accounts: list[str] = field(default_factory=list)
    id: int | None = None

    @property
    def total(self) -> int:
        return sum(self.months)


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


def lines(pf, holding_id: int, year: int) -> list[BudgetLine]:
    ready(pf)
    return [BudgetLine(r["kind"], r["item"], (json.loads(r["months"]) + [0] * 12)[:12], json.loads(r["accounts"]),
                       r["id"])
            for r in pf.conn.execute("SELECT * FROM budget_lines WHERE holding_id=? AND year=? ORDER BY sort, id",
                                     (holding_id, year))]


def years(pf, holding_id: int) -> list[int]:
    ready(pf)
    return [r[0] for r in pf.conn.execute("SELECT DISTINCT year FROM budget_lines WHERE holding_id=? ORDER BY year",
                                          (holding_id,))]


def save(pf, holding_id: int, year: int, rows: list[BudgetLine]) -> None:
    """Replace the year's budget with these lines."""
    ready(pf)
    for ln in rows:
        if ln.kind not in KINDS:
            raise ValueError(f"Unknown section {ln.kind!r}")
        if not ln.item.strip():
            raise ValueError("Each budget line needs a name")
        if len(ln.months) != 12:
            raise ValueError("A budget line has twelve months")
        if any(v < 0 for v in ln.months):
            raise ValueError(f"{ln.item}: amounts can't be negative")
    with pf.conn:
        pf.conn.execute("DELETE FROM budget_lines WHERE holding_id=? AND year=?", (holding_id, year))
        for k, ln in enumerate(rows):
            ln.id = pf.conn.execute(
                "INSERT INTO budget_lines(holding_id, year, kind, item, accounts, months, sort) VALUES (?,?,?,?,?,?,?)",
                (holding_id, year, ln.kind, ln.item.strip(), json.dumps(ln.accounts), json.dumps(ln.months),
                 k)).lastrowid


def split(amount: int) -> list[int]:
    """A year's amount over twelve months (the cents left over in December)."""
    m = amount // 12
    return [m] * 11 + [amount - m * 11]


def from_projections(pf, holding_id: int, year: int) -> list[BudgetLine]:
    """The year's revenue and operating-expense projection lines, each split evenly over the months (or the year's
    totals when the projections aren't item by item)."""
    from . import portfolio as pfm

    out = [BudgetLine(ln.kind, ln.item, split(ln.amounts.get(year, 0)))
           for ln in pfm.projection_lines(pf, holding_id) if ln.kind in KINDS and ln.amounts.get(year, 0)]
    if not out:
        p = next((x for x in pfm.projections(pf, holding_id) if x.year == year), None)
        if p is not None:
            out = [BudgetLine("revenue", "Revenue", split(p.revenue)), BudgetLine("opex", "Operating expenses",
                                                                                  split(p.opex))]
    return [b for b in out if b.total]


def from_last_year(company_path: str | Path, year: int, growth_pct: float = 0.0) -> list[BudgetLine]:
    """Each operating income / expense account's months last year, grown by a %."""
    act, types = actuals(company_path, year - 1)
    g = 1 + growth_pct / 100
    return [BudgetLine("revenue" if types[name] == "income" else "opex", name,
                       [max(0, round(v * g)) for v in months], [name])
            for name, months in act.items() if sum(months) > 0]


def actuals(company_path: str | Path, year: int) -> tuple[dict[str, list[int]], dict[str, str]]:
    """{account name: 12 months} of operating income (credits +) and expenses (debits +) in the books, and each
    account's type. Opening balances and non-operating accounts (interest, depreciation, income taxes) left out."""
    from .operations import NON_OPERATING

    out: dict[str, list[int]] = {}
    types: dict[str, str] = {}
    if not company_path or not Path(company_path).exists():
        return out, types
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT a.name, a.type, CAST(substr(e.date, 6, 2) AS INTEGER), SUM(l.debit_cents - l.credit_cents) "
            "FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id "
            "WHERE a.type IN ('income', 'expense') AND e.kind != 'opening' AND e.date >= ? AND e.date <= ? "
            "GROUP BY a.id, 3 ORDER BY a.code", (f"{year}-01-01", f"{year}-12-31")).fetchall()
    finally:
        conn.close()
    for name, typ, month, net in rows:
        if any(w in name.lower() for w in NON_OPERATING):
            continue
        types[name] = typ
        out.setdefault(name, [0] * 12)[month - 1] += -net if typ == "income" else net
    return out, types


def _auto(line: BudgetLine, name: str, typ: str) -> bool:
    if (typ == "income") != (line.kind == "revenue"):
        return False
    a, b = name.strip().lower(), line.item.strip().lower()
    return a == b or (len(b) >= 4 and b in a) or (len(a) >= 4 and a in b)


@dataclass
class Row:
    kind: str                    # revenue | opex | total
    item: str
    budget_month: int = 0
    actual_month: int = 0
    budget_ytd: int = 0
    actual_ytd: int = 0
    budget_year: int = 0
    accounts: list[str] = field(default_factory=list)
    unbudgeted: bool = False
    bold: bool = False

    @property
    def var_month(self) -> int:
        return self.actual_month - self.budget_month

    @property
    def var_ytd(self) -> int:
        return self.actual_ytd - self.budget_ytd

    @property
    def var_pct(self) -> float | None:
        return self.var_ytd / self.budget_ytd * 100 if self.budget_ytd else None

    @property
    def favourable(self) -> bool:
        return self.var_ytd >= 0 if self.kind in ("revenue", "noi") else self.var_ytd <= 0

    def flagged(self, pct: float = FLAG_PCT, minimum: int = FLAG_MIN) -> bool:
        if self.favourable or abs(self.var_ytd) < minimum:
            return False
        return self.var_pct is None or abs(self.var_pct) >= pct


@dataclass
class Comparison:
    year: int
    month: int                   # through this month (1–12)
    rows: list[Row]

    def flagged(self, pct: float = FLAG_PCT, minimum: int = FLAG_MIN) -> list[Row]:
        return [r for r in self.rows if not r.bold and r.flagged(pct, minimum)]


def compare(pf, holding, year: int, month: int) -> Comparison:
    """Budget vs the books for ``year`` through ``month``: a row per budget line, one per operating account no line
    takes, and totals (revenue, operating expenses, NOI)."""
    budget = lines(pf, holding.id, year)
    act, types = actuals(holding.company_path or "", year)
    taken: dict[str, int] = {}
    for i, ln in enumerate(budget):          # accounts picked for a line first, then the automatic matches
        for name in ln.accounts:
            if name in act and name not in taken:
                taken[name] = i
    for i, ln in enumerate(budget):
        if ln.accounts:
            continue
        for name in act:
            if name not in taken and _auto(ln, name, types[name]):
                taken[name] = i
    rows: list[Row] = []

    def row(kind, item, bud: list[int], actual: list[int], accounts, unbudgeted=False) -> Row:
        return Row(kind, item, bud[month - 1], actual[month - 1], sum(bud[:month]), sum(actual[:month]), sum(bud),
                   accounts, unbudgeted)

    for kind in KINDS:
        for i, ln in enumerate(budget):
            if ln.kind != kind:
                continue
            names = [n for n, j in taken.items() if j == i]
            actual = [sum(act[n][m] for n in names) for m in range(12)]
            rows.append(row(kind, ln.item, ln.months, actual, names))
        for name, months in act.items():
            if name not in taken and ("revenue" if types[name] == "income" else "opex") == kind and any(months):
                rows.append(row(kind, name, [0] * 12, months, [name], True))
    out: list[Row] = []
    totals = {}
    for kind, label in (("revenue", "Total revenue"), ("opex", "Total operating expenses")):
        part = [r for r in rows if r.kind == kind]
        t = Row(kind, label, *(sum(getattr(r, f) for r in part) for f in
                               ("budget_month", "actual_month", "budget_ytd", "actual_ytd", "budget_year")), bold=True)
        totals[kind] = t
        out += part + [t]
    rv, ox = totals["revenue"], totals["opex"]
    out.append(Row("noi", "Net operating income", rv.budget_month - ox.budget_month, rv.actual_month - ox.actual_month,
                   rv.budget_ytd - ox.budget_ytd, rv.actual_ytd - ox.actual_ytd, rv.budget_year - ox.budget_year,
                   bold=True))
    return Comparison(year, month, out)


def account_names(company_path: str | Path) -> list[tuple[str, str]]:
    """[(name, type)] of the operating income and expense accounts in the books."""
    from .operations import NON_OPERATING

    if not company_path or not Path(company_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT name, type FROM accounts WHERE type IN ('income', 'expense') AND active=1 "
                            "ORDER BY type DESC, code").fetchall()
    finally:
        conn.close()
    return [(n, t) for n, t in rows if not any(w in n.lower() for w in NON_OPERATING)]


def default_month(year: int, today: date | None = None) -> int:
    today = today or date.today()
    return 12 if year < today.year else (today.month if year == today.year else 1)
