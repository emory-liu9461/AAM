"""A few plain sentences about your money, shown beside the Net Worth statement: how this month is going against
the budget (three notes) and what's coming (cash over the next three months, a large expense that came last year at
this time, whether income covers spending).

Worked out here, from your budget, the actuals and your bank accounts; nothing is sent anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import personal_finance as pfi
from .money import fmt_money

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]


@dataclass
class Commentary:
    now: list[str] = field(default_factory=list)       # this month
    ahead: list[str] = field(default_factory=list)     # the next few months


def _add_months(d: date, n: int) -> tuple[int, int]:
    m = d.month - 1 + n
    return d.year + m // 12, m % 12 + 1


def cash_now(pf) -> int:
    """Cash in your chequing, savings and cash accounts."""
    from . import personal_accounts as pacc

    try:
        return sum(b.balance for b in pacc.load(pf).banks if not b.owed)
    except Exception:  # noqa: BLE001
        return 0


def commentary(pf, today: date | None = None) -> Commentary:
    today = today or date.today()
    out = Commentary()
    m = today.month
    b = pfi.budget(pf, today.year)
    lines = [r for r in b.rows if r.line.budget]
    month = MONTHS[m - 1]

    # ---- this month ----
    spend = [(r.months.get(m, 0) - r.line.budget, r) for r in lines if r.line.group == pfi.EXPENSE]
    over = max(spend, key=lambda x: x[0], default=None)
    if not lines:
        out.now.append("Set a monthly budget on the Budget tab to see how this month is going against it.")
    elif over and over[0] > 0:
        r = over[1]
        out.now.append(f"{r.line.category} is high this month: {fmt_money(r.months.get(m, 0))} against a "
                       f"{fmt_money(r.line.budget)} budget ({fmt_money(over[0])} over).")
    else:
        out.now.append(f"Spending is within budget in every category so far in {month}.")
    inc, binc = b.total(pfi.INCOME, m), b.budget_total(pfi.INCOME)
    if binc:
        if inc < binc:
            out.now.append(f"Income hasn't reached budget this month: {fmt_money(inc)} of {fmt_money(binc)} "
                           f"({fmt_money(binc - inc)} short).")
        else:
            out.now.append(f"Income is on budget this month: {fmt_money(inc)} against {fmt_money(binc)}"
                           + (f" ({fmt_money(inc - binc)} ahead)." if inc > binc else "."))
    elif inc:
        out.now.append(f"Income this month: {fmt_money(inc)} (no income budget set).")
    under = min(spend, key=lambda x: x[0], default=None)
    if under and under[0] < 0:
        r = under[1]
        out.now.append(f"{r.line.category} is low this month: {fmt_money(r.months.get(m, 0))} of a "
                       f"{fmt_money(r.line.budget)} budget ({fmt_money(-under[0])} left).")
    elif lines:
        exp = b.total(pfi.EXPENSE, m)
        out.now.append(f"Spent {fmt_money(exp)} so far this month against a {fmt_money(b.budget_total(pfi.EXPENSE))} "
                       "budget.")

    # ---- ahead ----
    cash = cash_now(pf)
    lows, ends = [], []
    for i in range(1, 4):
        y, mm = _add_months(today, i)
        nb = b if y == today.year else pfi.budget(pf, y)
        net = nb.budget_total(pfi.INCOME) - nb.budget_total(pfi.EXPENSE)
        cash += net
        ends.append((y, mm, cash))
        if net < 0:
            lows.append((y, mm, -net))
    y3, m3, c3 = ends[-1]
    short = next(((y, mm, c) for y, mm, c in ends if c < 0), None)
    if short:
        out.ahead.append(f"Cash could run short by {MONTHS[short[1] - 1]} {short[0]}: about {fmt_money(-short[2])} "
                         "below zero on the budget — plan for it now.")
    else:
        out.ahead.append(f"Cash looks fine for the next three months: about {fmt_money(c3)} by the end of "
                         f"{MONTHS[m3 - 1]} on the budget.")
    big = None                         # last year at this time: a category well above its usual
    last = pfi.budget(pf, today.year - 1)
    for i in range(1, 4):
        y, mm = _add_months(today, i)
        for r in last.rows:
            if r.line.group != pfi.EXPENSE:
                continue
            v = r.months.get(mm, 0)
            usual = r.line.budget or (r.actual / 12 if r.actual else 0)
            if v >= 500_00 and v > 1.5 * usual and (big is None or v > big[2]):
                big = (mm, r.line.category, v)
    if big:
        out.ahead.append(f"A large expense may be coming: last {MONTHS[big[0] - 1]}, {big[1]} was {fmt_money(big[2])}.")
    else:
        out.ahead.append("No large one-off expenses expected — nothing stood out at this time last year.")
    if lows:
        y, mm, gap = lows[0]
        out.ahead.append(f"Income doesn't cover expenses in {MONTHS[mm - 1]}: about {fmt_money(gap)} short on the "
                         "budget.")
    elif lines:
        out.ahead.append("Budgeted income covers expenses in each of the next three months.")
    return out


def html(c: Commentary) -> str:
    def items(xs):
        return "".join(f"<li style='margin-bottom:4px'>{x}</li>" for x in xs)

    return (f"<b>This month</b><ul style='margin-top:2px'>{items(c.now)}</ul>"
            f"<b>Looking ahead</b><ul style='margin-top:2px'>{items(c.ahead)}</ul>")
