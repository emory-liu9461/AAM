"""Master cash flow (Portfolio → Master Cash Flow): the next twelve months of everything together — each business's
cash flow at our share, your personal income and spending, the two added up, and cash at the end of each month.

* **A business**, each month: its projections for that year ÷ 12 (revenue − Op. Ex − Non-Op. Ex − CapEx + financing
  in − financing out), else the last twelve months (EBITDA − Non-Op. Ex − the capex requirement) ÷ 12; at our share.
* **Personal**, each month: your budget for that month's year — income (without Business income) and spending.
* **Distributions to you** move cash from the businesses to you: shown as a memo line, not counted twice.
* **Cash** starts from the businesses' cash (from their books, at our share) and your bank accounts.

Worked out here from the portfolio and your budget; nothing is sent anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import personal_finance as pfi
from . import portfolio as pfm

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


@dataclass
class Business:
    name: str
    share: float                          # our ownership (0–1)
    by_month: list[int] = field(default_factory=list)   # at our share
    basis: str = ""                       # "projections" / "last 12 months" / "no books"

    @property
    def total(self) -> int:
        return sum(self.by_month)


@dataclass
class MasterCashFlow:
    months: list[tuple[int, int]]          # (year, month), the next twelve
    businesses: list[Business]
    personal_income: list[int]
    personal_spend: list[int]
    distributions: list[int]               # memo: business income you budgeted (moves cash, not new money)
    cash_start_business: int = 0
    cash_start_personal: int = 0

    @property
    def labels(self) -> list[str]:
        return [f"{MONTH_ABBR[m - 1]} {y}" for y, m in self.months]

    @property
    def business_total(self) -> list[int]:
        return [sum(b.by_month[i] for b in self.businesses) for i in range(len(self.months))]

    @property
    def personal_net(self) -> list[int]:
        return [a - b for a, b in zip(self.personal_income, self.personal_spend)]

    @property
    def net(self) -> list[int]:
        return [a + b for a, b in zip(self.business_total, self.personal_net)]

    @property
    def cash_start(self) -> int:
        return self.cash_start_business + self.cash_start_personal

    @property
    def cash_end(self) -> list[int]:
        out, c = [], self.cash_start
        for n in self.net:
            c += n
            out.append(c)
        return out


def next_months(today: date, n: int = 12) -> list[tuple[int, int]]:
    """The months from next month on."""
    out = []
    for i in range(1, n + 1):
        m = today.month - 1 + i
        out.append((today.year + m // 12, m % 12 + 1))
    return out


def _business(pf, p: pfm.Performance, months: list[tuple[int, int]]) -> Business:
    share = p.holding.ownership_pct / 100
    b = Business(p.holding.name, share)
    proj = {x.year: x for x in pfm.projections(pf, p.holding.id)}
    ltm = p.net_cash_flow
    bases = set()
    for y, _m in months:
        x = proj.get(y)
        if x is not None:
            yearly = x.revenue - x.opex - x.nonop - x.capex + x.fin_in - x.fin_out
            bases.add("projections")
        elif ltm is not None:
            yearly = ltm
            bases.add("last 12 months")
        else:
            yearly = 0
        b.by_month.append(round(yearly * share / 12))
    b.basis = " + ".join(sorted(bases)) or "no books"
    return b


def build(pf, performances: list[pfm.Performance] | None = None, today: date | None = None) -> MasterCashFlow:
    from . import personal_commentary as pc

    today = today or date.today()
    months = next_months(today)
    if performances is None:
        performances = [pfm.performance(pf, h, today) for h in pf.holdings()]
    active = [p for p in performances if p.holding.status == "active"]
    businesses = [_business(pf, p, months) for p in active]
    cash_biz = sum(round(p.financials.cash * p.holding.ownership_pct / 100) for p in active if p.has_books)
    budgets: dict[int, list[pfi.Line]] = {}
    inc, spend, dist = [], [], []
    for y, _m in months:
        lines = budgets.setdefault(y, pfi.budget_lines(pf, y))
        inc.append(sum(ln.budget for ln in lines if ln.group == pfi.INCOME and ln.category != pfi.BUSINESS))
        spend.append(sum(ln.budget for ln in lines if ln.group == pfi.EXPENSE))
        dist.append(sum(ln.budget for ln in lines if ln.category == pfi.BUSINESS))
    return MasterCashFlow(months, businesses, inc, spend, dist, cash_biz, pc.cash_now(pf))
