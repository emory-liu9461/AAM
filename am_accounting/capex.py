"""Capital expenditure schedule for one company in the portfolio, linked to its books.

Budget lines are grouped (Hard Costs, Fees, Soft Costs, Closing Costs, Acquisition, Interest — or your own groups).
Each line has a budget and can be linked to an account in the company's books: its **cost to date** and monthly
spending are then what was recorded to that account (debits less credits, opening balances included), plus any
cost entered by hand that isn't in the books. Cost to complete = budget − cost to date.

**In-suite renovations** has a sub-line for each unit: every unit on the rent roll is added automatically (and
units added to the rent roll later), and **Add Unit** adds one that isn't on the rent roll yet (a newly built unit).
Each unit has its own budget and cost to date (by hand, or its own account in the books). The line's own figures are
for in-suite costs not assigned to a unit; its total is its own figures plus its units'. Once the line is linked
to an account in the books, each unit gets a sub-account of it, so the balance sheet shows in-suite renovations with
a line per unit.

**Create Account in the Books** adds a fixed-asset account for a line (e.g. "CapEx – Roofing") and links it, so
paying a contractor and categorizing the payment to it updates the capex schedule.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS capex_lines (
    id            INTEGER PRIMARY KEY,
    holding_id    INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    grp           TEXT NOT NULL DEFAULT 'Hard Costs',
    item          TEXT NOT NULL,
    budget_cents  INTEGER NOT NULL DEFAULT 0,
    account_id    INTEGER,                       -- the account in the company's books ('' = not linked)
    manual_cents  INTEGER NOT NULL DEFAULT 0,    -- cost to date not in the books
    notes         TEXT NOT NULL DEFAULT '',
    sort          INTEGER NOT NULL DEFAULT 0,
    parent_id     INTEGER,                       -- a unit's sub-line: the line it belongs to
    unit          TEXT NOT NULL DEFAULT ''       -- the unit number of a unit's sub-line
);
CREATE INDEX IF NOT EXISTS ix_capex_h ON capex_lines(holding_id, sort);
"""
GROUPS = ["Hard Costs", "Fees", "Soft Costs", "Closing Costs", "Acquisition Cost", "Interest Cost", "Other"]
TEMPLATE = {
    "Hard Costs": ["In-suite renovations", "Common area renovations", "Roofing", "Exterior / brick work",
                   "Paving & parking", "Mechanical / HVAC", "Fire safety", "Cash for keys", "Contingency"],
    "Fees": ["GC fees", "HST / GST"],
    "Soft Costs": ["Building condition assessment", "Environmental (ESA)", "Appraisal", "Architect / design",
                   "Permits", "Energy consultant"],
    "Closing Costs": ["Land transfer tax", "Legal", "Acquisition fees", "Lender fees", "Title insurance"],
    "Acquisition Cost": ["Purchase price"],
}


@dataclass
class CapexLine:
    item: str
    grp: str = "Hard Costs"
    budget_cents: int = 0
    account_id: int | None = None
    manual_cents: int = 0
    notes: str = ""
    id: int | None = None
    sort: int = 0
    parent_id: int | None = None   # a unit's sub-line of this line
    unit: str = ""                 # its unit number
    parent_item: str = ""          # the line it belongs to (e.g. "In-suite renovations")
    account_name: str = ""
    book_cents: int = 0          # cost to date from the books
    monthly: dict | None = None  # {'YYYY-MM': cents} from the books

    @property
    def label(self) -> str:
        return f"{self.parent_item} – {self.item}" if self.parent_item else self.item

    @property
    def to_date(self) -> int:
        return self.book_cents + self.manual_cents

    @property
    def to_complete(self) -> int:
        return self.budget_cents - self.to_date

    @property
    def pct_complete(self) -> float | None:
        return self.to_date / self.budget_cents if self.budget_cents else None


UNIT_WORDS = ("in-suite", "in suite", "insuite", "suite reno", "unit reno")


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)
    cols = {r[1] for r in pf.conn.execute("PRAGMA table_info(capex_lines)")}
    with pf.conn:
        if "parent_id" not in cols:
            pf.conn.execute("ALTER TABLE capex_lines ADD COLUMN parent_id INTEGER")
        if "unit" not in cols:
            pf.conn.execute("ALTER TABLE capex_lines ADD COLUMN unit TEXT NOT NULL DEFAULT ''")


def unit_label(unit: str) -> str:
    u = unit.strip()
    return u if u.lower().startswith(("unit", "suite", "apt")) else f"Unit {u}"


def save_line(pf, holding_id: int, ln: CapexLine) -> int:
    ready(pf)
    if not ln.item.strip():
        raise ValueError("Enter the cost item")
    if ln.budget_cents < 0:
        raise ValueError("The budget can't be negative")
    if ln.parent_id is not None:   # a unit's sub-line is in its line's group
        row = pf.conn.execute("SELECT grp FROM capex_lines WHERE id=?", (ln.parent_id,)).fetchone()
        if row:
            ln.grp = row[0]
    with pf.conn:
        if ln.id is None and not ln.sort:
            ln.sort = (pf.conn.execute("SELECT COALESCE(MAX(sort), 0) FROM capex_lines WHERE holding_id=? AND "
                                       "parent_id IS ?", (holding_id, ln.parent_id)).fetchone()[0] or 0) + 1
        vals = (holding_id, ln.grp.strip() or "Other", ln.item.strip(), ln.budget_cents, ln.account_id,
                ln.manual_cents, ln.notes.strip(), ln.sort, ln.parent_id, ln.unit.strip())
        if ln.id is None:
            ln.id = pf.conn.execute("INSERT INTO capex_lines(holding_id, grp, item, budget_cents, account_id, "
                                    "manual_cents, notes, sort, parent_id, unit) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    vals).lastrowid
        else:
            pf.conn.execute("UPDATE capex_lines SET holding_id=?, grp=?, item=?, budget_cents=?, account_id=?, "
                            "manual_cents=?, notes=?, sort=?, parent_id=?, unit=? WHERE id=?", vals + (ln.id,))
            if ln.parent_id is None:
                pf.conn.execute("UPDATE capex_lines SET grp=? WHERE parent_id=?", (vals[1], ln.id))
    return ln.id


def delete_line(pf, line_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM capex_lines WHERE id=? OR parent_id=?", (line_id, line_id))


def add_template(pf, holding_id: int) -> int:
    """Start a schedule with the usual cost items (budgets at zero)."""
    n = 0
    for grp, items in TEMPLATE.items():
        for item in items:
            save_line(pf, holding_id, CapexLine(item, grp))
            n += 1
    sync_units(pf, holding_id)
    return n


def unit_line(pf, holding_id: int) -> CapexLine | None:
    """The in-suite renovations line (the one with a sub-line per unit)."""
    ready(pf)
    for r in pf.conn.execute("SELECT * FROM capex_lines WHERE holding_id=? AND parent_id IS NULL ORDER BY sort, id",
                             (holding_id,)):
        if any(w in r["item"].lower() for w in UNIT_WORDS):
            return CapexLine(r["item"], r["grp"], r["budget_cents"], r["account_id"], r["manual_cents"],
                             r["notes"], r["id"], r["sort"])
    return None


def _unit_keys(pf, parent_id: int) -> set[str]:
    return {(r[0] or r[1]).strip().lower() for r in
            pf.conn.execute("SELECT unit, item FROM capex_lines WHERE parent_id=?", (parent_id,))}


def sync_units(pf, holding_id: int) -> int:
    """Give the in-suite renovations line a sub-line for every unit on the rent roll that doesn't have one yet
    (sub-lines of units no longer on the rent roll are kept — they may have costs). Returns how many were added."""
    from .operations import units

    parent = unit_line(pf, holding_id)
    if parent is None:
        return 0
    have = _unit_keys(pf, parent.id)
    n = 0
    for i, u in enumerate(units(pf, holding_id), 1):
        key = u.unit.strip().lower()
        if key and key not in have and unit_label(u.unit).lower() not in have:
            save_line(pf, holding_id, CapexLine(unit_label(u.unit), parent.grp, parent_id=parent.id, unit=u.unit,
                                                sort=i))
            have.add(key)
            n += 1
    return n


def add_unit(pf, holding_id: int, unit: str, budget_cents: int = 0, *, to_rent_roll: bool = False) -> CapexLine:
    """A unit that isn't on the rent roll yet (e.g. newly built) under in-suite renovations (created if there isn't
    one); optionally added to the rent roll too, as under construction."""
    from . import operations as op

    unit = unit.strip()
    if not unit:
        raise ValueError("Enter the unit number")
    parent = unit_line(pf, holding_id)
    if parent is None:
        parent = CapexLine("In-suite renovations", "Hard Costs")
        save_line(pf, holding_id, parent)
    if unit.lower() in _unit_keys(pf, parent.id) or unit_label(unit).lower() in _unit_keys(pf, parent.id):
        raise ValueError(f"{unit_label(unit)} is already on the capex schedule")
    if to_rent_roll and not any(u.unit.strip().lower() == unit.lower() for u in op.units(pf, holding_id)):
        op.save_unit(pf, holding_id, op.Unit(unit, status="Under construction", notes="New unit"))
    ln = CapexLine(unit_label(unit), parent.grp, budget_cents, parent_id=parent.id, unit=unit)
    save_line(pf, holding_id, ln)
    return ln


def _book_activity(company_path: str, account_ids: list[int], as_of: date) -> tuple[dict, dict, dict]:
    """({account: cost to date}, {account: {'YYYY-MM': cents}}, {account: name}) from the books."""
    if not account_ids or not company_path or not Path(company_path).exists():
        return {}, {}, {}
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(account_ids))
        names = dict(conn.execute(f"SELECT id, name FROM accounts WHERE id IN ({q})", account_ids).fetchall())
        totals: dict = {}
        months: dict = {}
        for acct, ym, net in conn.execute(
                f"SELECT l.account_id, substr(e.date, 1, 7), SUM(l.debit_cents - l.credit_cents) FROM journal_lines l "
                f"JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id IN ({q}) AND e.date <= ? "
                f"GROUP BY l.account_id, substr(e.date, 1, 7)", [*account_ids, as_of.isoformat()]):
            totals[acct] = totals.get(acct, 0) + net
            months.setdefault(acct, {})[ym] = net
        return totals, months, names
    finally:
        conn.close()


def lines(pf, holding, as_of: date | None = None) -> list[CapexLine]:
    """The schedule with cost to date and monthly spending read from the company's books."""
    ready(pf)
    as_of = as_of or date.today()
    flat = [CapexLine(r["item"], r["grp"], r["budget_cents"], r["account_id"], r["manual_cents"], r["notes"],
                      r["id"], r["sort"], r["parent_id"], r["unit"])
            for r in pf.conn.execute("SELECT * FROM capex_lines WHERE holding_id=? ORDER BY sort, id", (holding.id,))]
    tops = [ln for ln in flat if ln.parent_id is None]
    names = {ln.id: ln.item for ln in tops}
    out = []   # each line followed by its units
    for top in tops:
        out.append(top)
        for ln in flat:
            if ln.parent_id == top.id:
                ln.parent_item = names[top.id]
                out.append(ln)
    ids = sorted({ln.account_id for ln in out if ln.account_id is not None})
    totals, months, names = _book_activity(holding.company_path, ids, as_of)
    # an account shared by several lines counts once, on the first of them
    seen: set[int] = set()
    for ln in out:
        if ln.account_id is None:
            continue
        ln.account_name = names.get(ln.account_id, "(account not found)")
        if ln.account_id not in seen:
            ln.book_cents = totals.get(ln.account_id, 0)
            ln.monthly = months.get(ln.account_id, {})
            seen.add(ln.account_id)
    return out


@dataclass
class Totals:
    budget: int = 0
    to_date: int = 0
    to_complete: int = 0
    by_group: dict | None = None


def totals(rows: list[CapexLine]) -> Totals:
    t = Totals(by_group={})
    for ln in rows:
        t.budget += ln.budget_cents
        t.to_date += ln.to_date
        t.to_complete += ln.to_complete
        g = t.by_group.setdefault(ln.grp, [0, 0, 0])
        g[0] += ln.budget_cents
        g[1] += ln.to_date
        g[2] += ln.to_complete
    return t


def monthly_outflows(rows: list[CapexLine], months: list[str]) -> dict[str, int]:
    """Spending recorded in the books per month ('YYYY-MM'), all lines."""
    out = dict.fromkeys(months, 0)
    for ln in rows:
        for ym, cents in (ln.monthly or {}).items():
            if ym in out:
                out[ym] += cents
    return out


def _new_account(books, name: str, parent_id: int | None = None) -> int:
    """A fixed-asset account (a sub-account of ``parent_id`` when given) with a free code and a unique name."""
    used = {a.code for a in books.accounts()}
    code = "1650"
    while code in used:
        code = str(int(code) + 1)
    base, n = name, 2
    while any(a.name.lower() == name.lower() for a in books.accounts()):
        name, n = f"{base} ({n})", n + 1
    if parent_id is not None:
        parent = books.account(parent_id)
        return books.add_account(code, name, parent.type, parent.subtype, parent_id=parent_id).id
    return books.add_account(code, name, "asset", "fixed_asset").id


def _line(pf, line_id: int) -> CapexLine | None:
    r = pf.conn.execute("SELECT * FROM capex_lines WHERE id=?", (line_id,)).fetchone()
    return CapexLine(r["item"], r["grp"], r["budget_cents"], r["account_id"], r["manual_cents"], r["notes"], r["id"],
                     r["sort"], r["parent_id"], r["unit"]) if r else None


def _account_ok(books, account_id: int | None) -> bool:
    if account_id is None:
        return False
    try:
        books.account(account_id)
        return True
    except KeyError:
        return False


def create_account(pf, holding, ln: CapexLine) -> int:
    """A fixed-asset account in the company's books for this line ("CapEx – <item>"), linked to it. A unit's
    account is a sub-account of its line's account (created too if needed), so the balance sheet shows the line
    with a sub-line per unit; creating the line's account gives each of its units a sub-account."""
    from .books import Books

    if not holding.company_path or not Path(holding.company_path).exists():
        raise ValueError("Link this company to its books first")
    books = Books.open(holding.company_path, create=False)
    try:
        if ln.parent_id is not None:
            top = _line(pf, ln.parent_id)
            if top is not None and not _account_ok(books, top.account_id):
                top.account_id = _new_account(books, f"CapEx – {top.item}")
                save_line(pf, holding.id, top)
            ln.parent_item = ln.parent_item or (top.item if top else "")
            ln.account_id = _new_account(books, f"CapEx – {ln.label}", top.account_id if top else None)
        else:
            ln.account_id = _new_account(books, f"CapEx – {ln.label}")
        save_line(pf, holding.id, ln)
    finally:
        books.close()
    link_unit_accounts(pf, holding)
    return ln.account_id


def link_unit_accounts(pf, holding) -> int:
    """When the in-suite renovations line is linked to an account in the books, give each of its units without an
    account a sub-account of it ("CapEx – In-suite renovations – Unit 101"). Returns how many were created."""
    from .books import Books

    top = unit_line(pf, holding.id)
    if top is None or top.account_id is None or not holding.company_path or not Path(holding.company_path).exists():
        return 0
    todo = [ln for ln in lines_raw(pf, holding.id) if ln.parent_id == top.id and ln.account_id is None]
    if not todo:
        return 0
    books = Books.open(holding.company_path, create=False)
    try:
        if not _account_ok(books, top.account_id):
            return 0
        for ln in todo:
            ln.parent_item = top.item
            ln.account_id = _new_account(books, f"CapEx – {ln.label}", top.account_id)
            save_line(pf, holding.id, ln)
    finally:
        books.close()
    return len(todo)


def lines_raw(pf, holding_id: int) -> list[CapexLine]:
    ready(pf)
    return [CapexLine(r["item"], r["grp"], r["budget_cents"], r["account_id"], r["manual_cents"], r["notes"], r["id"],
                      r["sort"], r["parent_id"], r["unit"])
            for r in pf.conn.execute("SELECT * FROM capex_lines WHERE holding_id=? ORDER BY sort, id", (holding_id,))]


def book_accounts(company_path: str) -> list[tuple[int, str]]:
    """Accounts a capex line can be linked to: fixed assets first, then other assets and expenses."""
    if not company_path or not Path(company_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, code, name, type, subtype FROM accounts WHERE active=1 AND type IN "
                            "('asset','expense') AND is_bank=0 ORDER BY (subtype!='fixed_asset'), type, code"
                            ).fetchall()
    finally:
        conn.close()
    return [(r[0], f"{r[1]} · {r[2]}") for r in rows]
