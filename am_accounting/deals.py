"""The deal pipeline: acquisitions being looked at, from a new deal through due diligence to under contract.

Each deal has a stage — **New**, **Due Diligence**, **Under Contract**, then **Closed** or **Dead** — the asking or
agreed price, units, NOI (so the cap rate and price per unit are worked out), who brought it, the key dates (received,
offer, due-diligence deadline, firm / waiver date, closing) and the deposit. A due-diligence checklist (the usual
items to start, your own added) tracks what's done. A closed deal can be added to the portfolio as a holding (its
purchase price as the first valuation).

Kept in the portfolio file, next to the holdings.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        "next_step", "notes", "holding_id", "stage_date"]


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)
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
