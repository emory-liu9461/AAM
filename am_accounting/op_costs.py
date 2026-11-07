"""Operating costs of a property: the major cost categories and who provides each (power, water, gas, insurance,
snow removal …), with the account number, contact, contract end and budget — next to what the books show.

Each category is matched to an expense account in the company's books (by name, or the one you pick), so the last
twelve months' spending shows beside it, and the vendor paid the most for it can fill in the provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS op_costs (
    id            INTEGER PRIMARY KEY,
    holding_id    INTEGER NOT NULL,
    category      TEXT NOT NULL,
    provider      TEXT NOT NULL DEFAULT '',
    account_no    TEXT NOT NULL DEFAULT '',     -- the account number with the provider
    contact       TEXT NOT NULL DEFAULT '',     -- name, phone, email
    contract_end  TEXT,
    budget_cents  INTEGER NOT NULL DEFAULT 0,   -- a year
    book_account  TEXT NOT NULL DEFAULT '',     -- the expense account in the books ('' = matched by name)
    notes         TEXT NOT NULL DEFAULT '',
    sort          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_op_costs ON op_costs(holding_id, sort);
"""

# (category, words that find its account in the books, an example provider)
CATEGORIES: list[tuple[str, tuple[str, ...], str]] = [
    ("Property tax", ("property tax", "realty tax", "municipal tax"), "City of Toronto"),
    ("Electricity (hydro)", ("hydro", "electric", "power"), "Toronto Hydro"),
    ("Water & sewer", ("water", "sewer"), "City of Toronto"),
    ("Natural gas", ("gas", "heating fuel"), "Enbridge Gas"),
    ("Insurance", ("insurance",), "Intact Insurance"),
    ("Property management", ("property management", "management fee"), "your property manager"),
    ("Superintendent / wages", ("superintendent", "wage", "salar", "payroll"), "on-site superintendent"),
    ("Repairs & maintenance", ("repair", "maintenance"), "general contractor"),
    ("Snow removal", ("snow",), "snow removal company"),
    ("Landscaping", ("landscap", "lawn", "grounds"), "landscaping company"),
    ("Waste removal", ("waste", "garbage", "disposal", "recycl"), "waste hauler"),
    ("Elevator", ("elevator",), "elevator service company"),
    ("Fire & life safety", ("fire", "life safety", "sprinkler"), "fire inspection company"),
    ("HVAC & boiler service", ("hvac", "boiler", "heating"), "HVAC contractor"),
    ("Pest control", ("pest",), "pest control company"),
    ("Cleaning / janitorial", ("clean", "janitor"), "cleaning company"),
    ("Laundry", ("laundry",), "laundry route operator"),
    ("Internet & telecom", ("internet", "telecom", "telephone", "cable"), "internet provider"),
    ("Security & alarm", ("security", "alarm", "monitoring"), "alarm monitoring company"),
    ("Legal & accounting", ("legal", "accounting", "professional fee"), "lawyer / accountant"),
]
EXAMPLES = {c: ex for c, _w, ex in CATEGORIES}


@dataclass
class CostLine:
    category: str
    provider: str = ""
    account_no: str = ""
    contact: str = ""
    contract_end: str | None = None
    budget_cents: int = 0
    book_account: str = ""
    notes: str = ""
    id: int | None = None
    sort: int = 0
    # from the books (filled by with_books)
    matched_account: str = ""
    actual_cents: int | None = None        # last twelve months
    vendors: list[tuple[str, int]] = field(default_factory=list)   # [(vendor, cents)] paid for it, largest first

    @property
    def top_vendor(self) -> str:
        from .party_totals import UNASSIGNED

        return next((v for v, _c in self.vendors if v != UNASSIGNED), "")


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)
    pf.conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")


def lines(pf, holding_id: int) -> list[CostLine]:
    ready(pf)
    return [CostLine(r["category"], r["provider"], r["account_no"], r["contact"], r["contract_end"], r["budget_cents"],
                     r["book_account"], r["notes"], r["id"], r["sort"])
            for r in pf.conn.execute("SELECT * FROM op_costs WHERE holding_id=? ORDER BY sort, id", (holding_id,))]


def save(pf, holding_id: int, ln: CostLine) -> int:
    ready(pf)
    if not ln.category.strip():
        raise ValueError("Enter the cost category")
    if ln.budget_cents < 0:
        raise ValueError("The budget can't be negative")
    if ln.contract_end:
        date.fromisoformat(ln.contract_end)
    with pf.conn:
        if ln.id is None and not ln.sort:
            ln.sort = (pf.conn.execute("SELECT COALESCE(MAX(sort), 0) FROM op_costs WHERE holding_id=?",
                                       (holding_id,)).fetchone()[0] or 0) + 1
        vals = (holding_id, ln.category.strip(), ln.provider.strip(), ln.account_no.strip(), ln.contact.strip(),
                ln.contract_end or None, ln.budget_cents, ln.book_account.strip(), ln.notes.strip(), ln.sort)
        if ln.id is None:
            ln.id = pf.conn.execute("INSERT INTO op_costs(holding_id, category, provider, account_no, contact, "
                                    "contract_end, budget_cents, book_account, notes, sort) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                    vals).lastrowid
        else:
            pf.conn.execute("UPDATE op_costs SET holding_id=?, category=?, provider=?, account_no=?, contact=?, "
                            "contract_end=?, budget_cents=?, book_account=?, notes=?, sort=? WHERE id=?",
                            vals + (ln.id,))
    return ln.id


def delete(pf, line_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM op_costs WHERE id=?", (line_id,))


def add_standard(pf, holding_id: int) -> int:
    """Add the usual categories that aren't on the list yet. Returns how many were added."""
    have = {ln.category.lower() for ln in lines(pf, holding_id)}
    n = 0
    for cat, _w, _ex in CATEGORIES:
        if cat.lower() not in have:
            save(pf, holding_id, CostLine(cat))
            n += 1
    return n


def start_once(pf, holding_id: int) -> bool:
    """The first time a property's operating costs are shown: the usual categories (later deletions are kept)."""
    ready(pf)
    key = f"op_costs_started:{holding_id}"
    if pf.conn.execute("SELECT 1 FROM settings WHERE key=?", (key,)).fetchone():
        return False
    with pf.conn:
        pf.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, '1')", (key,))
    if not lines(pf, holding_id):
        add_standard(pf, holding_id)
        return True
    return False


def _match(category: str, accounts: list[str], taken: set[str]) -> str:
    words = next((w for c, w, _e in CATEGORIES if c.lower() == category.lower()), (category.lower(),))
    for name in accounts:
        if name not in taken and any(w in name.lower() for w in words):
            return name
    return ""


def with_books(rows: list[CostLine], company_path: str, as_of: date | None = None) -> None:
    """Fill in each line's expense account, its last twelve months and who was paid for it (from the books)."""
    from . import party_totals

    if not company_path or not Path(company_path).exists():
        return
    bd = party_totals.company_breakdown(company_path, as_of)
    if bd.error:
        return
    per_account: dict[str, list[tuple[str, int]]] = {}
    for v in bd.vendors:
        for acct, cents in v.by_account.items():
            per_account.setdefault(acct, []).append((v.name, cents))
    accounts = _expense_accounts(company_path)
    taken = {ln.book_account for ln in rows if ln.book_account}
    general = {"repairs & maintenance", "property management", "legal & accounting"}   # matched last
    for ln in sorted(rows, key=lambda x: x.category.lower() in general):
        acct = ln.book_account or _match(ln.category, accounts, taken)
        if not ln.book_account and acct:
            taken.add(acct)
        ln.matched_account = acct
        if acct:
            paid = sorted(per_account.get(acct, []), key=lambda x: -x[1])
            ln.vendors = paid
            ln.actual_cents = sum(c for _v, c in paid)


def _expense_accounts(company_path: str) -> list[str]:
    import sqlite3

    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT name FROM accounts WHERE type='expense' AND active=1 ORDER BY code")]
    finally:
        conn.close()


def expense_accounts(company_path: str) -> list[str]:
    if not company_path or not Path(company_path).exists():
        return []
    return _expense_accounts(company_path)


def fill_providers(pf, holding_id: int, rows: list[CostLine]) -> int:
    """Blank providers from the vendor paid the most for the category in the books. Returns how many were filled."""
    n = 0
    for ln in rows:
        if not ln.provider and ln.top_vendor:
            ln.provider = ln.top_vendor
            save(pf, holding_id, ln)
            n += 1
    return n
