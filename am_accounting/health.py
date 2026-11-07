"""What needs attention for one company in Asset Management — the rent roll, expenses and capex — shown at the top
of its pages, each with where to fix it.

* **Rent roll:** none entered; occupied units with no rent; vacant units still showing rent; units with no market rent;
  the same unit twice; leases that have ended (not month-to-month); the rent roll far from the rent in the books.
* **Expenses:** bank transactions not categorized or not reviewed in the books; operating expenses with no vendor;
  revenue with no customer.
* **CapEx:** spending on fixed assets the schedule doesn't track; lines over budget; the schedule not reconciling to
  the balance sheet.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .money import fmt_money

AREAS = ("Rent roll", "Expenses", "CapEx")


@dataclass
class Issue:
    area: str          # Rent roll | Expenses | CapEx
    text: str
    where: str         # rent_roll | operating_costs | customers_vendors | transactions | capex


def _rent_roll(pf, h, as_of: date) -> list[Issue]:
    from . import operations as op

    out = []
    units = op.units(pf, h.id) if h.id else []
    if not units:
        if h.type == "Real estate":
            out.append(Issue("Rent roll", "no rent roll yet — Import Rent Roll…", "rent_roll"))
        return out
    no_rent = [u.unit for u in units if u.occupied and not u.rent_cents]
    if no_rent:
        out.append(Issue("Rent roll", f"{len(no_rent)} occupied unit(s) with no rent ({', '.join(no_rent[:4])}"
                                      f"{' …' if len(no_rent) > 4 else ''})", "rent_roll"))
    vacant_rent = [u.unit for u in units if not u.occupied and u.rent_cents]
    if vacant_rent:
        out.append(Issue("Rent roll", f"{len(vacant_rent)} vacant unit(s) still showing rent "
                                      f"({', '.join(vacant_rent[:4])})", "rent_roll"))
    no_market = sum(1 for u in units if not u.market_cents)
    if no_market:
        out.append(Issue("Rent roll", f"{no_market} unit(s) with no market rent", "rent_roll"))
    seen, dup = set(), []
    for u in units:
        k = u.unit.strip().lower()
        if k in seen:
            dup.append(u.unit)
        seen.add(k)
    if dup:
        out.append(Issue("Rent roll", f"unit(s) listed twice: {', '.join(dup[:4])}", "rent_roll"))
    ended = []
    for u in units:
        try:
            if u.occupied and u.lease_end and date.fromisoformat(u.lease_end[:10]) < as_of:
                ended.append(u.unit)
        except ValueError:
            pass
    if ended:
        out.append(Issue("Rent roll", f"{len(ended)} lease(s) ended — renew or mark month-to-month "
                                      f"({', '.join(ended[:4])})", "rent_roll"))
    # the rent roll against the books' rent
    if h.company_path and Path(h.company_path).exists():
        roll = op.summary(units, as_of).in_place * 12
        books = _book_rent(h.company_path, as_of)
        if roll and books and abs(books - roll) / roll > 0.15:
            out.append(Issue("Rent roll", f"rent roll {fmt_money(roll)} a year vs {fmt_money(books)} of rent in the "
                                          f"books (last 12 months) — check vacancies, rent changes or deposits not "
                                          f"categorized", "rent_roll"))
    return out


def _book_rent(path: str, as_of: date) -> int:
    from datetime import timedelta

    start = (as_of - timedelta(days=365) + timedelta(days=1)).isoformat()
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        r = conn.execute(
            "SELECT COALESCE(SUM(l.credit_cents - l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e "
            "ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id WHERE a.type = 'income' AND "
            "(lower(a.name) LIKE '%rent%' OR lower(a.name) LIKE '%lease%') AND lower(a.name) NOT LIKE '%parking%' "
            "AND e.kind != 'opening' AND e.date >= ? AND e.date <= ?", (start, as_of.isoformat())).fetchone()
        return r[0] or 0
    finally:
        conn.close()


def _expenses(pf, h, as_of: date) -> list[Issue]:
    from . import op_costs as oc
    from . import party_totals as pt

    out = []
    path = h.company_path
    if not path or not Path(path).exists():
        return out
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        unc, unrev = conn.execute("SELECT SUM(category_id IS NULL), SUM(reviewed = 0) FROM transactions").fetchone()
    finally:
        conn.close()
    if unc:
        out.append(Issue("Expenses", f"{unc} uncategorized transaction(s) in the books — figures may change once "
                                     f"they're categorized", "transactions"))
    elif unrev:
        out.append(Issue("Expenses", f"{unrev} transaction(s) to review in the books", "transactions"))
    chk = oc.vendor_check(pf, h, as_of)
    if not chk.error and chk.unassigned:
        out.append(Issue("Expenses", f"{fmt_money(chk.unassigned)} of operating expenses with no vendor",
                         "operating_costs"))
    if not chk.error and chk.other_accounts:
        out.append(Issue("Expenses", f"{len(chk.other_accounts)} expense account(s) not on the operating cost list",
                         "operating_costs"))
    a = pt.company_assignment_check(path, as_of)
    if not a.error and a.revenue_unassigned:
        out.append(Issue("Expenses", f"{fmt_money(a.revenue_unassigned)} of revenue with no customer",
                         "customers_vendors"))
    return out


def _capex(pf, h, as_of: date) -> list[Issue]:
    from . import capex as cx

    out = []
    rows = cx.lines(pf, h, as_of) if h.id else []
    over = [ln for ln in rows if ln.budget_cents and ln.to_complete < 0]
    if over:
        out.append(Issue("CapEx", f"{len(over)} line(s) over budget ({', '.join(ln.label for ln in over[:3])}"
                                  f"{' …' if len(over) > 3 else ''})", "capex"))
    if h.company_path and Path(h.company_path).exists():
        r = cx.reconcile(pf, h, as_of)
        if not r.error:
            if r.unscheduled_since:
                out.append(Issue("CapEx", f"{fmt_money(r.unscheduled_since)} spent on fixed assets the CapEx schedule "
                                          f"doesn't track", "capex"))
            if r.difference:
                out.append(Issue("CapEx", f"the schedule is {fmt_money(abs(r.difference))} off the balance sheet",
                                 "capex"))
    return out


def company_issues(pf, h, as_of: date | None = None) -> list[Issue]:
    """Everything that needs attention for one company (see the module notes)."""
    as_of = as_of or date.today()
    out: list[Issue] = []
    for check in (_rent_roll, _expenses, _capex):
        try:
            out += check(pf, h, as_of)
        except Exception:  # noqa: BLE001 — a check that can't run never stops the page
            pass
    return out
