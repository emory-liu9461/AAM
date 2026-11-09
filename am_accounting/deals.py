"""The deal pipeline: acquisitions being looked at, from a new deal through due diligence to under contract.

Each deal has a stage — **New**, **Due Diligence**, **Under Contract**, then **Closed** or **Dead** — the asking or
agreed price, units, NOI (so the cap rate and price per unit are worked out), who brought it, the key dates (received,
offer, due-diligence deadline, firm / waiver date, closing) and the deposit. A due-diligence checklist (the usual
items to start, your own added) tracks what's done. A closed deal can be added to the portfolio as a holding (its
purchase price as the first valuation).

Kept in the portfolio file, next to the holdings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

STAGES = ["New", "Due Diligence", "Under Contract", "Closed", "Dead"]
PIPELINE = STAGES[:3]          # the columns on the pipeline page
ASSET_TYPES = ["Multifamily", "Mixed use", "Retail", "Office", "Industrial", "Land", "Operating business", "Other"]
STATUSES = ["", "In Progress", "Done", "N/A"]   # blank = not started
# The standard deal checklist: (section, group, item) — a blank group puts the item straight under its section.
CHECKLIST: list[tuple[str, str, str]] = [
    *[("Deliverables", "APS", x) for x in ("LOI", "Signed APS")],
    *[("Deliverables", "Rent Roll", x) for x in ("Summary", "Leases", "N1s")],
    *[("Deliverables", "Operating Bills", x) for x in ("Property Tax", "Hydro Bills", "Water Bill", "Gas Bill",
                                                        "Insurance Bill", "Capex Bill")],
    *[("Deliverables", "Legals / Financials", x) for x in ("Corp Info", "Financial Statements")],
    *[("Appraisal", "", x) for x in ("APS", "Operating Budget", "Rent Roll", "Property Taxes", "Site Contact",
                                     "Leases")],
    ("BCA", "", "Site Contact"),
    ("ESA", "", "Site Contact"),
    *[("Financing", "Property Info", x) for x in ("Rent Roll", "Suite Layout and Finishes",
                                                  "Historical Operating Statements",
                                                  "TTM Property Tax, Insurance, Utilities",
                                                  "Stabilized Appraisal Report", "Energy Report",
                                                  "Signed Attestations", "ESA Report")],
    ("Financing", "Sponsor & Property Questions", "Sponsor & Property Questions"),
    *[("Financing", "Legal / Financial", x) for x in ("Org. Chart", "Financial Statements – 3 yr", "PNW Documents",
                                                      "RE Portfolio (CMHC format)", "Credit Reports",
                                                      "NOA, T1, T4", "Guarantors' PNW Documents")],
]
DD_ITEMS = [item for _s, _g, item in CHECKLIST]

SCHEMA = """
CREATE TABLE IF NOT EXISTS deals (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    address       TEXT NOT NULL DEFAULT '',
    asset_type    TEXT NOT NULL DEFAULT 'Multifamily',
    stage         TEXT NOT NULL DEFAULT 'New',
    price_cents   INTEGER NOT NULL DEFAULT 0,    -- asking price (New) / offer / agreed price
    units         INTEGER NOT NULL DEFAULT 0,
    sqft          REAL,
    noi_cents     INTEGER NOT NULL DEFAULT 0,    -- in-place NOI, a year
    equity_cents  INTEGER NOT NULL DEFAULT 0,    -- equity needed
    source        TEXT NOT NULL DEFAULT '',      -- broker / off-market / referral …
    contact       TEXT NOT NULL DEFAULT '',
    lead          TEXT NOT NULL DEFAULT '',      -- who on our side
    received      TEXT,
    offer_date    TEXT,
    dd_deadline   TEXT,
    firm_date     TEXT,                          -- conditions waived / firm
    closing_date  TEXT,
    deposit_cents INTEGER NOT NULL DEFAULT 0,
    next_step     TEXT NOT NULL DEFAULT '',
    notes         TEXT NOT NULL DEFAULT '',
    holding_id    INTEGER,                       -- added to the portfolio as this holding
    stage_date    TEXT,                          -- when it moved to its stage
    created       TEXT NOT NULL DEFAULT (date('now'))
);
CREATE TABLE IF NOT EXISTS deal_dd (
    id       INTEGER PRIMARY KEY,
    deal_id  INTEGER NOT NULL REFERENCES deals(id) ON DELETE CASCADE,
    item     TEXT NOT NULL,
    done     INTEGER NOT NULL DEFAULT 0,
    due      TEXT,
    note     TEXT NOT NULL DEFAULT '',
    sort     INTEGER NOT NULL DEFAULT 0,
    section  TEXT NOT NULL DEFAULT '',
    grp      TEXT NOT NULL DEFAULT '',
    status   TEXT NOT NULL DEFAULT ''          -- '' | In Progress | Done | N/A
);
CREATE INDEX IF NOT EXISTS ix_deal_dd ON deal_dd(deal_id, sort);
"""


@dataclass
class Deal:
    name: str
    address: str = ""
    asset_type: str = "Multifamily"
    stage: str = "New"
    price_cents: int = 0
    units: int = 0
    sqft: float | None = None
    noi_cents: int = 0
    equity_cents: int = 0
    source: str = ""
    contact: str = ""
    lead: str = ""
    received: str | None = None
    offer_date: str | None = None
    dd_deadline: str | None = None
    firm_date: str | None = None
    closing_date: str | None = None
    deposit_cents: int = 0
    next_step: str = ""
    notes: str = ""
    holding_id: int | None = None
    stage_date: str | None = None
    uw: str = ""              # underwriting assumptions (JSON — see Underwriting)
    id: int | None = None
    dd_done: int = 0          # checklist progress (read with the deal)
    dd_total: int = 0
    dd_progress: int = 0      # in progress
    dd_na: int = 0            # not applicable

    @property
    def cap_rate(self) -> float | None:
        return self.noi_cents / self.price_cents if self.price_cents and self.noi_cents else None

    @property
    def per_unit(self) -> int | None:
        return round(self.price_cents / self.units) if self.units and self.price_cents else None

    @property
    def key_date(self) -> tuple[str, str | None]:
        """(what, date) that matters most at its stage."""
        if self.stage == "Due Diligence":
            return "DD deadline", self.dd_deadline
        if self.stage == "Under Contract":
            return ("Closing", self.closing_date) if self.closing_date or not self.firm_date else \
                ("Firm", self.firm_date)
        if self.stage == "Closed":
            return "Closed", self.closing_date
        return "Received", self.received

    def days_to(self, as_of: date | None = None) -> int | None:
        d = self.key_date[1]
        if not d or self.stage in ("New", "Closed", "Dead"):
            return None
        return (date.fromisoformat(d) - (as_of or date.today())).days


COLS = ["name", "address", "asset_type", "stage", "price_cents", "units", "sqft", "noi_cents", "equity_cents", "source",
        "contact", "lead", "received", "offer_date", "dd_deadline", "firm_date", "closing_date", "deposit_cents",
        "next_step", "notes", "holding_id", "stage_date", "uw"]


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)
    if "uw" not in {r[1] for r in pf.conn.execute("PRAGMA table_info(deals)")}:
        with pf.conn:
            pf.conn.execute("ALTER TABLE deals ADD COLUMN uw TEXT NOT NULL DEFAULT ''")
    cols = {r[1] for r in pf.conn.execute("PRAGMA table_info(deal_dd)")}
    with pf.conn:
        for col in ("section", "grp", "status"):
            if col not in cols:
                pf.conn.execute(f"ALTER TABLE deal_dd ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
                if col == "status":
                    pf.conn.execute("UPDATE deal_dd SET status='Done' WHERE done=1")


def _row(r) -> Deal:
    return Deal(**{c: r[c] for c in COLS}, id=r["id"])


def deals(pf, stages: list[str] | None = None) -> list[Deal]:
    """Deals (at these stages), each with its checklist progress; soonest key date first within a stage."""
    ready(pf)
    q = "SELECT * FROM deals"
    args: list = []
    if stages:
        q += f" WHERE stage IN ({','.join('?' * len(stages))})"
        args = list(stages)
    out = [_row(r) for r in pf.conn.execute(q + " ORDER BY id", args)]
    prog = {i: (d, t, p, n) for i, d, t, p, n in pf.conn.execute(
        "SELECT deal_id, SUM(status='Done'), COUNT(*), SUM(status='In Progress'), SUM(status='N/A') FROM deal_dd "
        "GROUP BY deal_id")}
    for d in out:
        d.dd_done, d.dd_total, d.dd_progress, d.dd_na = prog.get(d.id, (0, 0, 0, 0))
    return sorted(out, key=lambda d: (STAGES.index(d.stage) if d.stage in STAGES else 9,
                                      d.key_date[1] or "9999", d.name.lower()))


def deal(pf, deal_id: int) -> Deal:
    ready(pf)
    r = pf.conn.execute("SELECT * FROM deals WHERE id=?", (deal_id,)).fetchone()
    if r is None:
        raise KeyError(deal_id)
    return _row(r)


def save(pf, d: Deal) -> int:
    ready(pf)
    if not d.name.strip():
        raise ValueError("Enter the deal's name")
    if d.stage not in STAGES:
        raise ValueError(f"The stage is one of {', '.join(STAGES)}")
    if min(d.price_cents, d.noi_cents, d.equity_cents, d.deposit_cents, d.units) < 0:
        raise ValueError("Amounts can't be negative")
    for x in (d.received, d.offer_date, d.dd_deadline, d.firm_date, d.closing_date):
        if x:
            date.fromisoformat(x)
    d.name = d.name.strip()
    if d.id is not None:
        old = pf.conn.execute("SELECT stage FROM deals WHERE id=?", (d.id,)).fetchone()
        if old and old[0] != d.stage:
            d.stage_date = date.today().isoformat()
    d.stage_date = d.stage_date or date.today().isoformat()
    vals = [getattr(d, c) for c in COLS]
    with pf.conn:
        if d.id is None:
            d.id = pf.conn.execute(f"INSERT INTO deals({', '.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                                   vals).lastrowid
        else:
            pf.conn.execute(f"UPDATE deals SET {', '.join(c + '=?' for c in COLS)} WHERE id=?", vals + [d.id])
    return d.id


def delete(pf, deal_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM deal_dd WHERE deal_id=?", (deal_id,))
        pf.conn.execute("DELETE FROM deals WHERE id=?", (deal_id,))


def move(pf, deal_id: int, stage: str) -> Deal:
    """Move a deal to a stage. Going into due diligence starts its checklist (the usual items) if it has none."""
    d = deal(pf, deal_id)
    d.stage = stage
    save(pf, d)
    if stage == "Due Diligence" and not checklist(pf, deal_id):
        start_checklist(pf, deal_id)
    return d


def advance(pf, deal_id: int) -> Deal:
    d = deal(pf, deal_id)
    i = STAGES.index(d.stage)
    if i >= STAGES.index("Closed"):
        return d
    return move(pf, deal_id, STAGES[i + 1])


def back(pf, deal_id: int) -> Deal:
    d = deal(pf, deal_id)
    if d.stage == "Dead":
        return move(pf, deal_id, "New")
    i = STAGES.index(d.stage)
    return move(pf, deal_id, STAGES[max(0, i - 1)])


# ---- due-diligence checklist ----

@dataclass
class DDItem:
    item: str
    done: bool = False
    due: str | None = None
    note: str = ""
    id: int | None = None
    sort: int = 0
    section: str = ""
    grp: str = ""
    status: str = ""          # '' (not started) | In Progress | Done | N/A

    def __post_init__(self):
        if self.done and not self.status:
            self.status = "Done"
        self.done = self.status == "Done"


def checklist(pf, deal_id: int) -> list[DDItem]:
    ready(pf)
    return [DDItem(r["item"], False, r["due"], r["note"], r["id"], r["sort"], r["section"], r["grp"],
                   r["status"] or ("Done" if r["done"] else ""))
            for r in pf.conn.execute("SELECT * FROM deal_dd WHERE deal_id=? ORDER BY sort, id", (deal_id,))]


def save_checklist(pf, deal_id: int, items: list[DDItem]) -> None:
    """Replace the deal's checklist with these items (blank ones left out)."""
    ready(pf)
    for it in items:
        if it.due:
            date.fromisoformat(it.due)
        if it.status not in STATUSES:
            raise ValueError(f"The status is one of {', '.join(x for x in STATUSES if x)} (or blank)")
    with pf.conn:
        pf.conn.execute("DELETE FROM deal_dd WHERE deal_id=?", (deal_id,))
        pf.conn.executemany(
            "INSERT INTO deal_dd(deal_id, item, done, due, note, sort, section, grp, status) VALUES (?,?,?,?,?,?,?,?,?)",
            [(deal_id, it.item.strip(), int(it.status == "Done"), it.due or None, it.note.strip(), k,
              it.section.strip(), it.grp.strip(), it.status) for k, it in enumerate(items) if it.item.strip()])


def standard_items(due: str | None = None) -> list[DDItem]:
    return [DDItem(item, due=due, section=sec, grp=grp) for sec, grp, item in CHECKLIST]


def start_checklist(pf, deal_id: int) -> None:
    d = deal(pf, deal_id)
    save_checklist(pf, deal_id, standard_items(d.dd_deadline))


@dataclass
class Progress:
    done: int = 0
    in_progress: int = 0
    na: int = 0
    total: int = 0

    @property
    def needed(self) -> int:
        return self.total - self.na

    @property
    def pct(self) -> float | None:
        """Share of the items that apply that are done."""
        return self.done / self.needed if self.needed else None

    def text(self) -> str:
        if not self.total:
            return "—"
        out = f"{self.done} / {self.needed} done"
        return out + (f" · {self.in_progress} in progress" if self.in_progress else "")


def progress(items: list[DDItem]) -> Progress:
    p = Progress(total=len(items))
    for it in items:
        p.done += it.status == "Done"
        p.in_progress += it.status == "In Progress"
        p.na += it.status == "N/A"
    return p


def export_checklist(path: str, deal_name: str, items: list[DDItem]) -> None:
    """A deal's checklist as an Excel sheet: sections, groups and items, with status (coloured) and notes."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Deal Checklist"
    navy, pale = PatternFill("solid", fgColor="0B1F5C"), PatternFill("solid", fgColor="DDEBF7")
    fills = {"Done": ("C6EFCE", "006100"), "In Progress": ("FFEB9C", "9C5700"), "N/A": ("EDEDED", "595959")}
    ws.append([f"Deal Checklist – {deal_name}"])
    ws.merge_cells("A1:F1")
    ws["A1"].font, ws["A1"].fill = Font(bold=True, size=16, color="FFFFFF"), navy
    ws.append([])
    ws.append(["Section", "Group", "Item", "Status", "Due", "Notes"])
    for c in ws[3]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), navy
    sec = grp = None
    for it in items:
        if it.section != sec:
            sec, grp = it.section, None
            ws.append([sec or "Other"])
            for c in ws[ws.max_row][:6]:
                c.fill = pale
            ws.cell(ws.max_row, 1).font = Font(bold=True, color="0B1F5C")
        if it.grp and it.grp != grp:
            grp = it.grp
            ws.append(["", grp])
            ws.cell(ws.max_row, 2).font = Font(bold=True)
        ws.append(["", "" if it.grp else it.item, it.item if it.grp else "", it.status, it.due or "", it.note])
        if not it.grp:
            ws.cell(ws.max_row, 2).font = Font(bold=True)
        if it.status in fills:
            bg, fg = fills[it.status]
            c = ws.cell(ws.max_row, 4)
            c.fill, c.font, c.alignment = PatternFill("solid", fgColor=bg), Font(color=fg), Alignment(horizontal="center")
    p = progress(items)
    ws.append([])
    ws.append(["Progress", "", p.text()])
    ws.cell(ws.max_row, 1).font = Font(bold=True)
    for col, w in zip("ABCDEF", (16, 28, 40, 14, 12, 40)):
        ws.column_dimensions[col].width = w
    wb.save(path)


# ---- summary ----

@dataclass
class StageTotals:
    count: int = 0
    price: int = 0
    units: int = 0
    equity: int = 0


def totals(rows: list[Deal]) -> dict[str, StageTotals]:
    out = {s: StageTotals() for s in STAGES}
    for d in rows:
        t = out.setdefault(d.stage, StageTotals())
        t.count += 1
        t.price += d.price_cents
        t.units += d.units
        t.equity += d.equity_cents
    return out


def add_to_portfolio(pf, deal_id: int, *, ownership_pct: float = 100.0) -> int:
    """A closed deal becomes a holding: its name, type, address, closing date as acquired, and the price as its
    first valuation (company value, with the equity as equity value when entered). Returns the holding's id."""
    from . import portfolio as pfm

    d = deal(pf, deal_id)
    if d.holding_id is not None and any(h.id == d.holding_id for h in pf.holdings()):
        raise ValueError(f"“{d.name}” is already in the portfolio")
    kind = "Operating company" if d.asset_type == "Operating business" else "Real estate"
    h = pfm.Holding(d.name, kind, ownership_pct, d.closing_date or date.today().isoformat(), sector=d.asset_type,
                    location=d.address, notes=d.notes)
    pf.save_holding(h)
    if d.price_cents:
        eq = d.equity_cents or d.price_cents
        pf.add_valuation(h.id, pfm.Valuation(h.acquired, eq, "Purchase price", "From the deal pipeline",
                                             company_value_cents=d.price_cents,
                                             debt_cents=d.price_cents - eq if d.equity_cents else None))
    d.stage, d.holding_id = "Closed", h.id
    save(pf, d)
    return h.id


# ---- underwriting: the one-page summary ----

@dataclass
class Underwriting:
    """The assumptions behind a deal's one-page summary (all amounts in cents)."""
    rent_inplace: int = 0          # average rent per unit a month, today
    rent_market: int = 0           # … at market (the potential)
    other_income: int = 0          # parking, laundry … a month, all units
    vacancy_pct: float = 3.0
    opex_annual: int = 0           # operating expenses a year (0 = opex_pct of revenue)
    opex_pct: float = 40.0
    reserve_per_unit: int = 300_00  # capital reserve a unit a year
    closing_pct: float = 3.0       # closing costs as % of the price (land transfer tax, legal, reports, fees)
    ltv_pct: float = 65.0
    rate_pct: float = 5.0
    amort_years: float = 25.0
    interest_only: bool = False
    term_years: float = 5.0
    # the seller's rent roll, unit by unit: {"unit", "type", "tenant", "status", "rent", "market"} (a month, cents);
    # when entered, it replaces the average rents above and sets the number of units
    rent_roll: list = field(default_factory=list)
    # operating expenses one by one: {"item", "amount"} (a year, cents); when entered, their total is the opex
    expenses: list = field(default_factory=list)

    @property
    def roll_units(self) -> int:
        return len(self.rent_roll)

    @property
    def roll_inplace(self) -> int:
        """Rent a month from the rent roll: each unit's rent, a vacant unit at its market rent."""
        return sum(_unit_rent(x) for x in self.rent_roll)

    @property
    def roll_occupied_rent(self) -> int:
        return sum(int(x.get("rent") or 0) for x in self.rent_roll if _occupied(x))

    @property
    def roll_market(self) -> int:
        return sum(int(x.get("market") or 0) or int(x.get("rent") or 0) for x in self.rent_roll)

    @property
    def roll_vacant(self) -> int:
        return sum(1 for x in self.rent_roll if not _occupied(x))

    @property
    def expenses_total(self) -> int:
        return sum(int(x.get("amount") or 0) for x in self.expenses)

    def to_json(self) -> str:
        import json
        from dataclasses import asdict

        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str, deal: "Deal | None" = None) -> "Underwriting":
        import json

        try:
            data = json.loads(text) if text else {}
        except ValueError:
            data = {}
        u = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        if not data and deal is not None and deal.price_cents and deal.equity_cents:   # from the deal's equity
            u.ltv_pct = round(max(0.0, (deal.price_cents - deal.equity_cents) / deal.price_cents * 100), 1)
        return u


UNIT_STATUSES = ["Occupied", "Vacant", "Notice given", "Renovation"]
EXPENSE_STARTERS = ["Property tax", "Insurance", "Hydro", "Gas", "Water", "Repairs & maintenance",
                    "Property management", "Superintendent / wages", "Snow removal & landscaping", "Waste removal",
                    "Elevator", "Pest control", "Legal & accounting", "Advertising", "Other"]


def _occupied(x: dict) -> bool:
    return (x.get("status") or "Occupied") not in ("Vacant", "Renovation")


def _unit_rent(x: dict) -> int:
    rent = int(x.get("rent") or 0)
    return rent if _occupied(x) and rent else int(x.get("market") or 0) or rent


def roll_from_units(units) -> list[dict]:
    """A rent roll read from a spreadsheet (operations.Unit) as the deal keeps it."""
    out = []
    for u in units:
        vacant = not u.occupied
        out.append({"unit": u.unit, "type": u.unit_type, "tenant": "" if vacant else u.tenant,
                    "status": "Vacant" if vacant else "Occupied",
                    "rent": u.rent_cents + u.parking_cents + u.locker_cents, "market": u.market_cents})
    return out


def underwriting(d: Deal) -> Underwriting:
    return Underwriting.from_json(d.uw, d)


@dataclass
class Analysis:
    gpr: int = 0                   # gross potential rent, a year
    vacancy: int = 0
    other: int = 0
    egi: int = 0                   # effective gross income
    opex: int = 0
    noi: int = 0
    reserve: int = 0
    price: int = 0
    closing: int = 0
    all_in: int = 0
    loan: int = 0
    debt_service: int = 0
    cash_flow: int = 0             # NOI − reserve − debt service
    equity: int = 0                # all-in cost − loan
    from_deal_noi: bool = False    # NOI as entered on the deal (no rents entered)

    @property
    def cap_rate(self) -> float | None:
        return self.noi / self.price if self.price else None

    @property
    def dscr(self) -> float | None:
        return self.noi / self.debt_service if self.debt_service else None

    @property
    def coc(self) -> float | None:
        return self.cash_flow / self.equity if self.equity > 0 else None

    @property
    def debt_yield(self) -> float | None:
        return self.noi / self.loan if self.loan else None

    @property
    def expense_ratio(self) -> float | None:
        return self.opex / self.egi if self.egi else None

    @property
    def breakeven(self) -> float | None:
        """Occupancy that just covers expenses, reserve and debt service."""
        gross = self.gpr + self.other
        return (self.opex + self.reserve + self.debt_service) / gross if gross else None


def analyze(d: Deal, u: Underwriting, *, market: bool = False, rent_change: float = 0.0, rate: float | None = None,
            price: int | None = None, ltv: float | None = None) -> Analysis:
    """A year of the deal on these assumptions: in place (or at market rents), with optional changes for the
    sensitivity tables (rent change as a fraction, a different rate, price or LTV)."""
    from . import portfolio as pfm

    a = Analysis()
    a.price = d.price_cents if price is None else price
    units = u.roll_units or d.units
    if u.rent_roll:                          # unit by unit
        monthly = u.roll_market if market else u.roll_inplace
    else:
        rent = (u.rent_market or u.rent_inplace) if market else u.rent_inplace
        monthly = rent * d.units if rent and d.units else 0
    if monthly:
        a.gpr = round(monthly * 12 * (1 + rent_change))
        a.vacancy = round(a.gpr * u.vacancy_pct / 100)
        a.other = round(u.other_income * 12 * (1 + rent_change))
        a.egi = a.gpr - a.vacancy + a.other
        a.opex = u.expenses_total or u.opex_annual or round(a.egi * u.opex_pct / 100)
        a.noi = a.egi - a.opex
    elif d.noi_cents:                       # only the NOI is known
        a.from_deal_noi = True
        a.noi = round(d.noi_cents * (1 + rent_change))
    a.reserve = u.reserve_per_unit * units
    a.closing = round(a.price * u.closing_pct / 100)
    a.all_in = a.price + a.closing
    a.loan = round(a.price * (u.ltv_pct if ltv is None else ltv) / 100)
    if a.loan:
        ln = pfm.Loan("", "Mortgage", a.loan, rate_pct=u.rate_pct if rate is None else rate,
                      term_months=round(u.term_years * 12), amort_months=round(u.amort_years * 12),
                      repayment="Interest only" if u.interest_only else "Amortizing")
        a.debt_service = ln.annual_debt_service
    a.cash_flow = a.noi - a.reserve - a.debt_service
    a.equity = a.all_in - a.loan
    return a


RENT_STEPS = [-0.10, -0.05, 0.0, 0.05, 0.10]
RATE_STEPS = [-1.0, -0.5, 0.0, 0.5, 1.0]
PRICE_STEPS = [-0.10, -0.05, 0.0, 0.05, 0.10]
LTV_STEPS = [-10.0, -5.0, 0.0, 5.0, 10.0]


def sensitivity_rate_rent(d: Deal, u: Underwriting) -> tuple[list[float], list[float], list[list[int]]]:
    """Cash flow after debt service: interest rate (rows) × rent change (columns)."""
    rates = [max(0.0, u.rate_pct + x) for x in RATE_STEPS]
    return rates, RENT_STEPS, [[analyze(d, u, rate=r, rent_change=c).cash_flow for c in RENT_STEPS] for r in rates]


def sensitivity_price_ltv(d: Deal, u: Underwriting) -> tuple[list[int], list[float], list[list[float | None]]]:
    """Cash-on-cash return: purchase price (rows) × loan-to-value (columns)."""
    prices = [round(d.price_cents * (1 + x)) for x in PRICE_STEPS]
    ltvs = [min(95.0, max(0.0, u.ltv_pct + x)) for x in LTV_STEPS]
    return prices, ltvs, [[analyze(d, u, price=p, ltv=v).coc for v in ltvs] for p in prices]
