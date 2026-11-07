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
DD_ITEMS = ["Financials / T12 review", "Rent roll & lease audit", "Site visit & unit inspections",
            "Building condition assessment", "Phase I environmental (ESA)", "Appraisal", "Title search",
            "Zoning & permits", "Insurance quote", "Property tax review", "Service contracts review",
            "Lender commitment", "Legal review / purchase agreement"]

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
    sort     INTEGER NOT NULL DEFAULT 0
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
    prog = {i: (d, t) for i, d, t in pf.conn.execute(
        "SELECT deal_id, SUM(done), COUNT(*) FROM deal_dd GROUP BY deal_id")}
    for d in out:
        d.dd_done, d.dd_total = prog.get(d.id, (0, 0))
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


def checklist(pf, deal_id: int) -> list[DDItem]:
    ready(pf)
    return [DDItem(r["item"], bool(r["done"]), r["due"], r["note"], r["id"], r["sort"])
            for r in pf.conn.execute("SELECT * FROM deal_dd WHERE deal_id=? ORDER BY sort, id", (deal_id,))]


def save_checklist(pf, deal_id: int, items: list[DDItem]) -> None:
    """Replace the deal's checklist with these items (blank ones left out)."""
    ready(pf)
    for it in items:
        if it.due:
            date.fromisoformat(it.due)
    with pf.conn:
        pf.conn.execute("DELETE FROM deal_dd WHERE deal_id=?", (deal_id,))
        pf.conn.executemany("INSERT INTO deal_dd(deal_id, item, done, due, note, sort) VALUES (?,?,?,?,?,?)",
                            [(deal_id, it.item.strip(), int(it.done), it.due or None, it.note.strip(), k)
                             for k, it in enumerate(items) if it.item.strip()])


def start_checklist(pf, deal_id: int) -> None:
    d = deal(pf, deal_id)
    save_checklist(pf, deal_id, [DDItem(x, due=d.dd_deadline) for x in DD_ITEMS])


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
