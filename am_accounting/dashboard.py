"""Figures for the dashboard: income over recent periods, balance sheet summary, cash."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .books import Books
from .receivables import AgingSummary, aging

PERIODS = (30, 90, 120)


@dataclass
class PeriodIncome:
    days: int
    start: date
    end: date
    revenue: int
    expenses: int

    @property
    def net(self) -> int:
        return self.revenue - self.expenses

    @property
    def margin(self) -> float | None:
        return self.net / self.revenue if self.revenue else None


@dataclass
class DashboardData:
    company: str
    as_of: date
    last_import: str | None          # when statements were last imported (ISO datetime)
    data_through: str | None         # latest transaction date in the books
    income: list[PeriodIncome] = field(default_factory=list)
    total_assets: int = 0
    total_liabilities: int = 0
    total_equity: int = 0
    cash_total: int = 0
    cash_accounts: list[tuple[str, int]] = field(default_factory=list)
    uncategorized: int = 0
    unreviewed: int = 0
    # Balance sheet breakdown
    short_term_assets: int = 0   # current assets other than cash (receivables, prepaid, inventory…)
    long_term_assets: int = 0    # fixed and other long-term assets
    short_term_debt: int = 0     # current liabilities (payables, credit cards, taxes due…)
    long_term_debt: int = 0      # long-term liabilities (loans)
    receivables: "AgingSummary | None" = None
    payables: "AgingSummary | None" = None

    @property
    def balanced(self) -> bool:
        return self.total_assets == self.total_liabilities + self.total_equity


def _income(books: Books, start: date, end: date) -> tuple[int, int]:
    totals = books.ledger_totals(start=start.isoformat(), end=end.isoformat())
    revenue = expenses = 0
    for a in books.accounts(types=("income", "expense")):
        net = totals.get(a.id, 0)
        if a.type == "income":
            revenue += -net
        else:
            expenses += net
    return revenue, expenses


def build_dashboard(books: Books, as_of: date | None = None, periods: tuple[int, ...] = PERIODS) -> DashboardData:
    as_of = as_of or date.today()
    end = as_of.isoformat()
    last_import = books.conn.execute("SELECT MAX(imported_at) FROM imports WHERE row_count > 0").fetchone()[0]
    stats = books.stats()
    d = DashboardData(books.company_name, as_of, last_import, stats["last"],
                      uncategorized=stats["uncategorized"],
                      unreviewed=books.conn.execute("SELECT COUNT(*) FROM transactions WHERE reviewed=0").fetchone()[0])

    for n in periods:
        start = as_of - timedelta(days=n - 1)
        revenue, expenses = _income(books, start, as_of)
        d.income.append(PeriodIncome(n, start, as_of, revenue, expenses))

    totals = books.ledger_totals(end=end)
    for a in books.accounts():
        net = totals.get(a.id, 0)
        if a.type == "asset":
            d.total_assets += net
            if a.subtype in ("fixed_asset", "other_asset"):
                d.long_term_assets += net
            elif not a.is_bank:
                d.short_term_assets += net
        elif a.type == "liability":
            d.total_liabilities += -net
            if a.subtype == "long_term":
                d.long_term_debt += -net
            else:
                d.short_term_debt += -net
        elif a.type == "equity":
            d.total_equity += -net
        elif a.type == "income":
            d.total_equity += -net   # cumulative earnings belong to equity
        elif a.type == "expense":
            d.total_equity -= net
        if a.is_bank and (net or a.active):
            d.cash_accounts.append((a.name, net))
            d.cash_total += net
    d.receivables = aging(books, "invoice", as_of)
    d.payables = aging(books, "bill", as_of)
    return d
