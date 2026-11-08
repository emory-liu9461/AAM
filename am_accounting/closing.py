"""Closing a deal: a deal under contract becomes a new operating company with its opening balance sheet.

One step sets up:

* **The company** — a new company file (an operating company, corporation chart of accounts), its legal name and
  fiscal year.
* **The purchase** — a property account ("Property – <name>") with a sub-account for each closing cost: the purchase
  price (or Land and Building when a land share is given), land transfer tax, legal fees, consultant reports, lender
  fees, title insurance, other costs. Acquisition costs are part of the property's cost.
* **The financing** — each loan (mortgage, vendor take-back …) on Financing & Cap Table and as a liability in the
  books; **the equity** — each owner's money as share capital (by class) or as a shareholder loan, and on the cap
  table (paid in on closing).
* **Cash left over** (sources − uses) goes into the bank account as the company's starting cash.

It's all one entry on the closing date: the company's **opening balance sheet** (so it isn't capex or borrowing in
the year's results). The company is added to the portfolio as a holding linked to its books, with the purchase price
as its first valuation and its sources & uses filled in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from . import deals as dl
from . import portfolio as pfm

COST_LINES = ["Purchase price", "Land transfer tax", "Legal fees", "Consultant reports (BCA, ESA, appraisal)",
              "Lender & financing fees", "Title insurance", "Other closing costs"]
EQUITY_FORMS = ["Share capital", "Shareholder loan"]


def ontario_ltt(price_cents: int, *, single_family: bool = False) -> int:
    """Ontario land transfer tax (provincial), an estimate: 0.5% to $55,000, 1.0% to $250,000, 1.5% to $400,000,
    2.0% above — and 2.5% above $2,000,000 for land with one or two single-family homes. (Toronto adds its own
    municipal tax; check the figure with your lawyer.)"""
    bands = [(55_000_00, 0.005), (250_000_00, 0.01), (400_000_00, 0.015)]
    bands += [(2_000_000_00, 0.02), (None, 0.025)] if single_family else [(None, 0.02)]
    tax, low = 0.0, 0
    for top, rate in bands:
        hi = price_cents if top is None else min(price_cents, top)
        if hi > low:
            tax += (hi - low) * rate
        if top is None or price_cents <= top:
            break
        low = top
    return round(tax)


@dataclass
class EquityLine:
    owner: str
    amount_cents: int
    share_class: str = "Class A"
    company: str = ""
    form: str = "Share capital"      # or "Shareholder loan"


@dataclass
class ClosingSetup:
    company_name: str                       # the corporation, e.g. "1001234567 Ontario Inc."
    property_name: str                      # e.g. "45 Elm St"
    closing_date: str
    costs: list[tuple[str, int]]            # [(what, cents)] — the uses
    loans: list[pfm.Loan] = field(default_factory=list)
    equity: list[EquityLine] = field(default_factory=list)
    land_pct: float = 0.0                   # share of the purchase price that is land (not depreciated)
    entity_type: str = "Corporation"
    fiscal_year_start_month: int = 1
    ownership_pct: float = 100.0            # the portfolio's share of the company
    prepaid: list = field(default_factory=list)   # [deal_costs.Prepaid] — paid before closing by other companies

    @property
    def uses(self) -> int:
        return sum(c for _l, c in self.costs)

    @property
    def debt(self) -> int:
        return sum(ln.principal_cents for ln in self.loans)

    @property
    def equity_total(self) -> int:
        return sum(e.amount_cents for e in self.equity)

    @property
    def prepaid_total(self) -> int:
        return sum(p.total for p in self.prepaid)

    @property
    def sources(self) -> int:
        """Debt + equity + what other companies paid before closing (the deposit, pre-closing costs)."""
        return self.debt + self.equity_total + self.prepaid_total

    @property
    def cash_left(self) -> int:
        """Sources − uses: the company's starting cash (negative = not enough money to close)."""
        return self.sources - self.uses

    @property
    def price(self) -> int:
        return next((c for label, c in self.costs if label == "Purchase price"), 0)

    def check(self) -> None:
        if not self.company_name.strip():
            raise ValueError("Enter the company's name")
        if not self.property_name.strip():
            raise ValueError("Enter the property's name")
        date.fromisoformat(self.closing_date)
        if any(c < 0 for _l, c in self.costs) or any(e.amount_cents < 0 for e in self.equity):
            raise ValueError("Amounts can't be negative")
        if not self.price:
            raise ValueError("Enter the purchase price")
        if not 0 <= self.land_pct < 100:
            raise ValueError("The land share is a percentage below 100")
        for ln in self.loans:
            if not ln.lender.strip() or ln.principal_cents <= 0:
                raise ValueError("Each loan needs a lender and an amount")
        for e in self.equity:
            if not (e.owner.strip() or e.company.strip()) or e.amount_cents <= 0:
                raise ValueError("Each owner needs a name and an amount")
            if e.form not in EQUITY_FORMS:
                raise ValueError(f"Equity is one of {', '.join(EQUITY_FORMS)}")
        if self.cash_left < 0:
            raise ValueError(f"The money in ({_m(self.sources)}) is {_m(-self.cash_left)} short of what closing "
                             f"costs ({_m(self.uses)}) — add debt or equity")


def _m(cents: int) -> str:
    from .money import fmt_money

    return fmt_money(cents)


def default_setup(deal: dl.Deal, pf=None) -> ClosingSetup:
    """A starting point from the deal: its price and closing date, an estimate of Ontario land transfer tax, the
    costs paid before closing (Deposits & Costs, recorded in the paying companies' books), the deal's equity and the
    rest as a mortgage."""
    price = deal.price_cents
    costs = [(label, 0) for label in COST_LINES]
    costs[0] = ("Purchase price", price)
    costs[1] = ("Land transfer tax", ontario_ltt(price) if price else 0)
    s = ClosingSetup("", deal.name, deal.closing_date or date.today().isoformat(), costs)
    if pf is not None and deal.id is not None:
        from . import deal_costs as dc

        s.prepaid = dc.prepaid(pf, deal.id)
        for p in s.prepaid:            # pre-closing costs are part of the property's cost
            s.costs += [(c.item, c.amount_cents) for c in p.lines if c.kind == "cost"]
    if deal.equity_cents and price > deal.equity_cents:
        s.loans = [pfm.Loan("", "Mortgage", price - deal.equity_cents, start_date=s.closing_date, term_months=60,
                            amort_months=300)]
    eq = max(0, s.uses - s.debt - s.prepaid_total)
    if eq:
        s.equity = [EquityLine("", eq)]
    return s


def _account(books, code: str, name: str, type_: str, subtype: str = "", parent_id: int | None = None) -> int:
    used = {a.code for a in books.accounts()}
    while code in used:
        code = str(int(code) + 1)
    base, n = name, 2
    while any(a.name.lower() == name.lower() for a in books.accounts()):
        name, n = f"{base} ({n})", n + 1
    return books.add_account(code, name, type_, subtype, parent_id=parent_id).id


def close_deal(pf, deal_id: int, s: ClosingSetup, create_company: Callable[..., str]) -> int:
    """Close the deal (see the module notes). ``create_company(name, entity_type=, fiscal_year_start_month=)``
    makes the company file and returns its path. Returns the new holding's id."""
    from .books import Books, CompanyProfile
    from . import loan_link as ll

    s.check()
    d = dl.deal(pf, deal_id)
    if d.holding_id is not None and any(h.id == d.holding_id for h in pf.holdings()):
        raise ValueError(f"“{d.name}” is already in the portfolio")
    path = create_company(s.company_name.strip(), entity_type=s.entity_type,
                          fiscal_year_start_month=s.fiscal_year_start_month)
    # the holding, linked to the new books (so pulling in operating companies doesn't add it twice)
    h = pfm.Holding(s.property_name.strip(), "Real estate", s.ownership_pct, s.closing_date,
                    sector=d.asset_type, location=d.address, company_path=str(path), notes=d.notes)
    pf.save_holding(h)
    books = Books.open(path, create=False)
    try:
        prof = books.profile() if hasattr(books, "profile") else None
        if prof is not None:
            books.set_profile(CompanyProfile(s.company_name.strip(), s.company_name.strip(), s.entity_type,
                                             prof.tax_id, s.fiscal_year_start_month, prof.role or "operating"))
        lines: list[tuple[int, int, int]] = []
        # the property and what it cost
        prop = _account(books, "1600", f"Property – {s.property_name.strip()}", "asset", "fixed_asset")
        for label, cents in s.costs:
            if not cents:
                continue
            if label == "Purchase price" and s.land_pct:
                land = round(cents * s.land_pct / 100)
                for part, amt in (("Land", land), ("Building", cents - land)):
                    if amt:
                        lines.append((_account(books, "1601", f"Property – {s.property_name.strip()} – {part}",
                                               "asset", "fixed_asset", prop), amt, 0))
                continue
            lines.append((_account(books, "1601", f"Property – {s.property_name.strip()} – {label}", "asset",
                                   "fixed_asset", prop), cents, 0))
        # starting cash
        if s.cash_left:
            bank = next((a.id for a in books.accounts() if a.is_bank), None)
            if bank is None:
                bank = books.add_account("1000", "Operating Account", "asset", is_bank=True).id
            lines.append((bank, s.cash_left, 0))
        # loans: on Financing & Cap Table and in the books
        for ln in s.loans:
            ln.start_date = ln.start_date or s.closing_date
            ln.balance_cents = None
            ln.book_entry_id = -1          # put in the books by this closing entry, not matched to Assets Held
            pfm.save_loan(pf, h.id, ln)
            acct, _ = ll.ensure_account(pf, books, ll.CompanyLoan(h, ln))
            ln.book_entry_id = -1
            pfm.save_loan(pf, h.id, ln)
            lines.append((acct, 0, ln.principal_cents))
        # equity: share capital by class, shareholder loans by owner; and the cap table
        classes: dict[str, int] = {}
        for e in s.equity:
            who = e.owner.strip() or e.company.strip()
            if e.form == "Shareholder loan":
                acct = _account(books, "2800", f"Shareholder Loan – {who}", "liability", "long_term")
                lines.append((acct, 0, e.amount_cents))
            else:
                classes[e.share_class or "Common"] = classes.get(e.share_class or "Common", 0) + e.amount_cents
            pfm.save_shareholder(pf, h.id, pfm.Shareholder(e.owner.strip(), e.company.strip(), e.amount_cents,
                                                           "Shareholder loan" if e.form == "Shareholder loan" else "",
                                                           share_class=e.share_class or "Common",
                                                           funded_cents=e.amount_cents,
                                                           funded_date=s.closing_date))
        for cls, cents in classes.items():
            acct = _account(books, "3010", f"Share Capital – {cls}", "equity", "contribution")
            lines.append((acct, 0, cents))
        for p in s.prepaid:              # paid before closing by another company: owed back to it
            if p.total:
                acct = _account(books, "2400", f"Due to {p.payer_name}"[:80], "liability", "current")
                lines.append((acct, 0, p.total))
        books.add_journal_entry(s.closing_date, f"Closing – purchase of {s.property_name.strip()}", lines,
                                kind="opening")
    finally:
        books.close()
    # the first valuation and sources & uses
    pf.add_valuation(h.id, pfm.Valuation(s.closing_date, s.price - s.debt, "Purchase price", "On closing",
                                         company_value_cents=s.price, debt_cents=s.debt))
    ours = round(s.equity_total * s.ownership_pct / 100)
    pfm.save_sources_uses(pf, h.id, {
        "Debt financing": s.debt, "Equity – us": ours, "Equity – other owners": s.equity_total - ours,
        "Other sources": s.prepaid_total, "Purchase price": s.price, "CapEx / improvements": 0,
        "Closing & financing costs": s.uses - s.price, "Cash reserve / other": s.cash_left})
    if s.prepaid:                        # the payers' books: the deal account → due from the new company
        from . import deal_costs as dc

        dc.settle_on_closing(pf, d.id, d.name, s.company_name.strip(), s.closing_date)
    d.stage, d.holding_id, d.closing_date = "Closed", h.id, s.closing_date
    dl.save(pf, d)
    return h.id

