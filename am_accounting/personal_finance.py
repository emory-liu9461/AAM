"""Personal finances: a monthly budget, and the income you take from your businesses (kept in the portfolio file).

**Income from your businesses** for a year:

* from the portfolio — each holding's distributions / dividends (and, apart, capital returned: return of capital,
  loans repaid, sale proceeds) as recorded on the holding (by hand or from the holding company's books), at **your
  share**: the share set on the Net Worth page (the holding's ownership to start with) ÷ the holding's ownership;
* anything else you take from them, entered by hand: salary, management fees, bonuses, consulting …

**The budget** is a monthly amount per category (income and expenses), against what actually came in and went out
each month — from your statements (see personal_statements.py) plus what you type, except *Business income*, which is
the income from your businesses above (budgeted too, if you like).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from . import personal as pers

INCOME, EXPENSE = "Income", "Expense"
BUSINESS = "Business income"
INCOME_CATS = ["Salary / employment", BUSINESS, "Rental income (personal)", "Investment income", "Other income"]
EXPENSE_CATS = ["Housing (mortgage / rent)", "Property tax", "Utilities", "Home insurance", "Groceries", "Dining out",
                "Transportation", "Car insurance", "Phone and internet", "Health and fitness", "Childcare and education",
                "Clothing", "Entertainment", "Travel", "Gifts and donations", "Debt payments", "Income tax",
                "Savings and investments", "Other"]
INCOME_KINDS = ["Salary", "Management fee", "Dividend", "Bonus", "Consulting", "Other"]
CAPITAL_KINDS = {"return_of_capital", "loan_repaid", "proceeds"}


def _get(pf, key: str, default):
    raw = pers._setting(pf, key)
    return json.loads(raw) if raw else default


def _put(pf, key: str, value) -> None:
    pers._set(pf, key, json.dumps(value))


# ---- income from your businesses ----

@dataclass
class Received:
    """Money you took from a business, entered by hand."""
    date: str
    company: str
    kind: str = "Salary"
    amount: int = 0
    note: str = ""


def received(pf) -> list[Received]:
    return sorted((Received(**d) for d in _get(pf, "business_income", [])), key=lambda r: r.date)


def save_received(pf, rows: list[Received]) -> None:
    _put(pf, "business_income", [r.__dict__ for r in rows if r.amount or r.company])


@dataclass
class FromHolding:
    holding_id: int
    name: str
    share_pct: float              # your share of the property
    factor: float                 # your share ÷ the holding's ownership: what of the holding's flows is yours
    distributions: int = 0        # yours, in the year
    capital: int = 0              # capital returned to you (not income)
    by_month: dict[int, int] = field(default_factory=dict)   # distributions by month


def from_portfolio(pf, year: int) -> list[FromHolding]:
    """Each holding's distributions and capital returned in the year, at your share."""
    from . import portfolio as pfm

    saved = pers.load(pf)
    end = min(date(year, 12, 31), date.today()) if year >= date.today().year else date(year, 12, 31)
    out = []
    for h in pf.holdings():
        share = saved.shares.get(h.id) or pers.my_ownership(pf, h, saved).pct     # from its cap table
        factor = share / h.ownership_pct if h.ownership_pct else 0
        row = FromHolding(h.id, h.name, share, factor)
        try:
            flows = pfm.performance(pf, h, end, with_financials=False).flows
        except Exception:  # noqa: BLE001 — a holding company's books that can't be read: nothing from them
            flows = []
        for f in flows:
            if not f.date.startswith(str(year)):
                continue
            amt = round(f.amount_cents * factor)
            if f.kind == "distribution":
                row.distributions += amt
                m = int(f.date[5:7])
                row.by_month[m] = row.by_month.get(m, 0) + amt
            elif f.kind in CAPITAL_KINDS:
                row.capital += amt
        if row.distributions or row.capital or h.status == "active":
            out.append(row)
    return out


@dataclass
class BusinessIncome:
    year: int
    holdings: list[FromHolding]
    entered: list[Received]

    @property
    def distributions(self) -> int:
        return sum(h.distributions for h in self.holdings)

    @property
    def capital(self) -> int:
        return sum(h.capital for h in self.holdings)

    @property
    def other(self) -> int:
        return sum(r.amount for r in self.entered)

    @property
    def total(self) -> int:
        """Income: distributions + what you entered (capital returned isn't income)."""
        return self.distributions + self.other

    def by_month(self) -> dict[int, int]:
        out = dict.fromkeys(range(1, 13), 0)
        for h in self.holdings:
            for m, v in h.by_month.items():
                out[m] += v
        for r in self.entered:
            out[int(r.date[5:7])] += r.amount
        return out

    def by_company(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for h in self.holdings:
            if h.distributions:
                out[h.name] = out.get(h.name, 0) + h.distributions
        for r in self.entered:
            out[r.company or "Other"] = out.get(r.company or "Other", 0) + r.amount
        return out


def business_income(pf, year: int) -> BusinessIncome:
    return BusinessIncome(year, from_portfolio(pf, year), [r for r in received(pf) if r.date.startswith(str(year))])


# ---- the budget ----

@dataclass
class Line:
    group: str                    # Income / Expense
    category: str
    budget: int = 0               # a month


def budget_lines(pf) -> list[Line]:
    rows = _get(pf, "personal_budget", None)
    if rows is None:
        return [Line(INCOME, c) for c in INCOME_CATS] + [Line(EXPENSE, c) for c in EXPENSE_CATS]
    return [Line(**r) for r in rows]


def save_budget_lines(pf, lines: list[Line]) -> None:
    _put(pf, "personal_budget", [ln.__dict__ for ln in lines])


def actuals(pf, year: int) -> dict[str, dict[int, int]]:
    """{category: {month: cents}} entered for the year (Business income isn't here: it comes from the businesses)."""
    raw = _get(pf, "personal_actuals", {}).get(str(year), {})
    return {c: {int(m): v for m, v in ms.items()} for c, ms in raw.items()}


def set_actual(pf, year: int, category: str, month: int, cents: int | None) -> None:
    allv = _get(pf, "personal_actuals", {})
    yr = allv.setdefault(str(year), {})
    cat = yr.setdefault(category, {})
    if cents:
        cat[str(month)] = cents
    else:
        cat.pop(str(month), None)
    _put(pf, "personal_actuals", allv)


@dataclass
class BudgetRow:
    line: Line
    months: dict[int, int]        # actual by month (statements + typed)
    auto: bool = False            # Business income: from the businesses
    statements: dict[int, int] = field(default_factory=dict)   # the part from your statements

    @property
    def actual(self) -> int:
        return sum(self.months.values())

    def budget_to(self, month: int) -> int:
        return self.line.budget * month


@dataclass
class Budget:
    year: int
    rows: list[BudgetRow]
    through: int                  # months of the year so far (for year-to-date)

    def total(self, group: str, month: int | None = None) -> int:
        rs = [r for r in self.rows if r.line.group == group]
        return sum(r.months.get(month, 0) for r in rs) if month else sum(r.actual for r in rs)

    def budget_total(self, group: str) -> int:
        return sum(r.line.budget for r in self.rows if r.line.group == group)

    def net(self, month: int | None = None) -> int:
        return self.total(INCOME, month) - self.total(EXPENSE, month)


def budget(pf, year: int) -> Budget:
    from . import personal_statements as pst

    lines = budget_lines(pf)
    acts = actuals(pf, year)
    stm = pst.monthly(pf, year)
    biz = business_income(pf, year).by_month()
    rows = []
    for ln in lines:
        if ln.category == BUSINESS:
            rows.append(BudgetRow(ln, {m: v for m, v in biz.items() if v}, auto=True))
        else:
            typed, st = acts.get(ln.category, {}), stm.get(ln.category, {})
            both = {m: typed.get(m, 0) + st.get(m, 0) for m in set(typed) | set(st)}
            rows.append(BudgetRow(ln, {m: v for m, v in both.items() if v}, statements=st))
    today = date.today()
    through = 12 if year < today.year else (today.month if year == today.year else 0)
    return Budget(year, rows, through)


# ---- projected (the budget) against actual, for this week, month and year ----

@dataclass
class Period:
    name: str                     # This week / This month / This year
    start: date
    end: date
    income_proj: int = 0
    income: int = 0
    spend_proj: int = 0
    spend: int = 0
    cash_start: int | None = None  # cash at the start (the snapshot at or before it)
    cash_now: int = 0

    @property
    def saved_proj(self) -> int:
        return self.income_proj - self.spend_proj

    @property
    def saved(self) -> int:
        return self.income - self.spend

    @property
    def cash_proj(self) -> int | None:
        """Where cash should end up: the start, plus what the budget says you'd save."""
        return None if self.cash_start is None else self.cash_start + self.saved_proj


def periods(pf, today: date | None = None) -> list[Period]:
    """This week (Monday on), this month and this year: the budget (a week is 12/52 of a month; the year is the
    whole year's budget) against what's come in and gone out so far, and your cash."""
    from datetime import timedelta

    from . import personal as pers
    from . import personal_accounts as pacc
    from . import personal_statements as pst

    today = today or date.today()
    b = budget(pf, today.year)
    inc_m, exp_m = b.budget_total(INCOME), b.budget_total(EXPENSE)
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)
    out = [Period("This week", week_start, today, round(inc_m * 12 / 52), 0, round(exp_m * 12 / 52)),
           Period("This month", month_start, today, inc_m, b.total(INCOME, today.month), exp_m,
                  b.total(EXPENSE, today.month)),
           Period("This year", year_start, today, inc_m * 12, b.total(INCOME), exp_m * 12, b.total(EXPENSE))]
    # the week: from your statements (typed actuals are by month) and what you entered from your businesses
    income_cats = set(INCOME_CATS)
    for t in pst.transactions(pf, today.year):
        if week_start.isoformat() <= t.date <= today.isoformat() and t.category not in ("", pst.TRANSFER):
            if t.category in income_cats:
                out[0].income += t.amount
            else:
                out[0].spend += -t.amount
    out[0].income += sum(r.amount for r in received(pf) if week_start.isoformat() <= r.date <= today.isoformat())
    cash_now = sum(e.amount for e in pacc.entries(pf)[0] if e.kind in pers.CASH_KINDS)
    hist = pers.history(pf)
    for p in out:
        p.cash_now = cash_now
        before = [h for h in hist if h.date <= p.start.isoformat()]
        p.cash_start = before[-1].cash if before else None
    return out
