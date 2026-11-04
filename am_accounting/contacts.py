"""Customers & vendors list.

Every person or company you deal with — customers, related entities, shareholders, suppliers, employees —
with the accounts their money goes to:

* **Customers** (and related entities you're owed by): a receivable account — Accounts Receivable, or their
  own *Due from Sister Co* — and a usual income account.
* **Vendors** (and related entities you owe): a payable account — Accounts Payable, or *Due to Sister Co*,
  *Credit Card Payable* — and a usual expense account.

**Other names** are how the bank, e-Transfers or cheques show the same contact ("PRIYA SHAH" → *Shah
Holdings*). Names on transactions (see parties.py), invoices, bills and AR / AP lines all resolve to the
contact, so filters and groups use one name. The contact's accounts are offered as one-click choices in the
To Categorize panel and used as defaults for new invoices and bills.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .books import Books

ROLES = {"customer": "Customer", "vendor": "Vendor", "both": "Customer & vendor"}
TYPES = ["Customer", "Related entity", "Shareholder", "Supplier", "Contractor", "Employee", "Lender", "Other"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role            TEXT NOT NULL DEFAULT 'customer' CHECK (role IN ('customer','vendor','both')),
    type            TEXT NOT NULL DEFAULT 'Customer',
    email           TEXT NOT NULL DEFAULT '',
    phone           TEXT NOT NULL DEFAULT '',
    address         TEXT NOT NULL DEFAULT '',
    receivable_id   INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    payable_id      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    income_id       INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    expense_id      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    aliases         TEXT NOT NULL DEFAULT '',
    notes           TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1
);
"""


@dataclass
class Contact:
    name: str
    role: str = "customer"
    type: str = "Customer"
    email: str = ""
    phone: str = ""
    address: str = ""
    receivable_id: int | None = None
    payable_id: int | None = None
    income_id: int | None = None
    expense_id: int | None = None
    aliases: list[str] = field(default_factory=list)
    notes: str = ""
    active: bool = True
    id: int | None = None

    @property
    def is_customer(self) -> bool:
        return self.role in ("customer", "both")

    @property
    def is_vendor(self) -> bool:
        return self.role in ("vendor", "both")


def ensure_schema(books: Books) -> None:
    if not books.first_time("schema:contacts"):
        return
    fresh = not books._has_table("contacts")
    books.conn.executescript(SCHEMA)
    if fresh:
        _seed(books)


def _seed(books: Books) -> None:
    """First use: start the list from customers / vendors already in the books."""
    found: dict[str, Contact] = {}
    if books._has_table("customers"):
        for r in books.conn.execute("SELECT name, email, address FROM customers"):
            found.setdefault(r[0].lower(), Contact(r[0], "customer", "Customer", r[1], "", r[2]))
    if books._has_table("documents"):
        for party, kind in books.conn.execute("SELECT DISTINCT party, kind FROM documents"):
            role = "customer" if kind == "invoice" else "vendor"
            c = found.get(party.lower())
            if c is None:
                found[party.lower()] = Contact(party, role, "Customer" if role == "customer" else "Supplier")
            elif c.role != role:
                c.role = "both"
    with books.conn:
        for c in found.values():
            _insert(books, c)


def _row(r) -> Contact:
    return Contact(r["name"], r["role"], r["type"], r["email"], r["phone"], r["address"], r["receivable_id"],
                   r["payable_id"], r["income_id"], r["expense_id"],
                   [a.strip() for a in r["aliases"].split(";") if a.strip()], r["notes"], bool(r["active"]), r["id"])


def all_contacts(books: Books, role: str | None = None, *, active_only: bool = False) -> list[Contact]:
    """role: 'customer' (incl. both), 'vendor' (incl. both) or None for everyone."""
    ensure_schema(books)
    out = [_row(r) for r in books.conn.execute("SELECT * FROM contacts ORDER BY name COLLATE NOCASE")]
    if role == "customer":
        out = [c for c in out if c.is_customer]
    elif role == "vendor":
        out = [c for c in out if c.is_vendor]
    if active_only:
        out = [c for c in out if c.active]
    return out


def get(books: Books, contact_id: int) -> Contact:
    ensure_schema(books)
    r = books.conn.execute("SELECT * FROM contacts WHERE id=?", (contact_id,)).fetchone()
    if r is None:
        raise KeyError(contact_id)
    return _row(r)


def find(books: Books, name: str) -> Contact | None:
    """The contact with this name or other name (case-insensitive)."""
    key = " ".join(name.split()).lower()
    if not key:
        return None
    for c in all_contacts(books):
        if c.name.lower() == key or key in (a.lower() for a in c.aliases):
            return c
    return None


def alias_map(books: Books) -> dict[str, str]:
    """{lower-case name or other name: contact name} — for resolving names on transactions."""
    out = {}
    for c in all_contacts(books):
        out[c.name.lower()] = c.name
        for a in c.aliases:
            out.setdefault(a.lower(), c.name)
    return out


def _validate(books: Books, c: Contact) -> None:
    if not c.name.strip():
        raise ValueError("Enter a name")
    if c.role not in ROLES:
        raise ValueError("Choose customer, vendor or both")
    clash = books.conn.execute("SELECT id FROM contacts WHERE name=? COLLATE NOCASE AND id != ?",
                               (c.name.strip(), c.id or 0)).fetchone()
    if clash:
        raise ValueError(f"There's already a contact called {c.name.strip()}")
    for a in c.aliases:
        other = find(books, a)
        if other and other.id != c.id:
            raise ValueError(f"“{a}” is already a name of {other.name}")
    for attr, want in (("receivable_id", "asset"), ("payable_id", "liability")):
        acct_id = getattr(c, attr)
        if acct_id:
            a = books.account(acct_id)
            if a.type != want or a.is_bank:
                raise ValueError(f"{a.name} can't be a {'receivable' if want == 'asset' else 'payable'} account")


def _insert(books: Books, c: Contact) -> int:
    cur = books.conn.execute(
        "INSERT INTO contacts(name, role, type, email, phone, address, receivable_id, payable_id, income_id, "
        "expense_id, aliases, notes, active) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (c.name.strip(), c.role, c.type, c.email.strip(), c.phone.strip(), c.address.strip(), c.receivable_id,
         c.payable_id, c.income_id, c.expense_id, "; ".join(a.strip() for a in c.aliases if a.strip()),
         c.notes.strip(), int(c.active)))
    return cur.lastrowid


def save(books: Books, c: Contact) -> int:
    """Add (c.id None) or update a contact. Renaming updates names you set on transactions and AR / AP lines,
    and the customer / vendor on its invoices and bills."""
    ensure_schema(books)
    _validate(books, c)
    with books.conn:
        if c.id is None:
            c.id = _insert(books, c)
        else:
            old = get(books, c.id)
            books.conn.execute(
                "UPDATE contacts SET name=?, role=?, type=?, email=?, phone=?, address=?, receivable_id=?, "
                "payable_id=?, income_id=?, expense_id=?, aliases=?, notes=?, active=? WHERE id=?",
                (c.name.strip(), c.role, c.type, c.email.strip(), c.phone.strip(), c.address.strip(),
                 c.receivable_id, c.payable_id, c.income_id, c.expense_id,
                 "; ".join(a.strip() for a in c.aliases if a.strip()), c.notes.strip(), int(c.active), c.id))
            if old.name != c.name.strip():
                _rename(books, old.name, c.name.strip())
    return c.id


def _rename(books: Books, old: str, new: str) -> None:
    for table, col in (("tx_names", "name"), ("arap_labels", "party"), ("documents", "party"),
                       ("name_rules", "name"), ("customers", "name")):
        if books._has_table(table):
            try:
                books.conn.execute(f"UPDATE {table} SET {col}=? WHERE {col}=? COLLATE NOCASE", (new, old))
            except Exception:  # noqa: BLE001 - e.g. a unique name already taken in an old table
                pass


def delete(books: Books, contact_id: int) -> None:
    """Remove from the list (transactions, invoices and bills keep their names)."""
    ensure_schema(books)
    with books.conn:
        books.conn.execute("DELETE FROM contacts WHERE id=?", (contact_id,))


def ensure_contact(books: Books, name: str, role: str) -> Contact | None:
    """The contact for this name, added to the list if new (used when invoices / bills are saved)."""
    name = " ".join(name.split())
    if not name:
        return None
    c = find(books, name)
    if c is None:
        c = Contact(name, role, "Customer" if role == "customer" else "Supplier")
        with books.conn:
            c.id = _insert(books, c)
    elif role not in (c.role, "both") and c.role != "both":
        c.role = "both"
        save(books, c)
    return c


def related_account(books: Books, c: Contact, side: str) -> int:
    """Create the contact's own 'Due from <name>' (side='receivable') or 'Due to <name>' ('payable') account
    under Accounts Receivable / Payable, set it on the contact and return it."""
    from . import receivables as rx

    kind = "invoice" if side == "receivable" else "bill"
    label = f"Due from {c.name}" if side == "receivable" else f"Due to {c.name}"
    existing = next((a for a in books.accounts() if a.name.lower() == label.lower()), None)
    acct = existing or rx.add_control_account(books, kind, label)
    setattr(c, "receivable_id" if side == "receivable" else "payable_id", acct.id)
    save(books, c)
    return acct.id


@dataclass
class Summary:
    contact: Contact
    receivable_cents: int = 0     # owed to you (open invoices + AR lines with this name)
    payable_cents: int = 0        # you owe
    transactions: int = 0         # bank transactions with this name
    last_date: str = ""


def summaries(books: Books, as_of: date | None = None) -> list[Summary]:
    """Each contact with what's outstanding and how many bank transactions carry its name."""
    from . import arap, parties

    as_of = as_of or date.today()
    people = all_contacts(books)
    by_name = {c.name.lower(): Summary(c) for c in people}
    amap = alias_map(books)
    for kind, attr in (("invoice", "receivable_cents"), ("bill", "payable_cents")):
        for it in arap.outstanding(books, kind, as_of):
            key = amap.get(it.party.lower(), it.party).lower()
            if key in by_name:
                setattr(by_name[key], attr, getattr(by_name[key], attr) + it.balance_cents)
    for tx_id, n in parties.names(books).items():
        s = by_name.get(n.name.lower())
        if s:
            s.transactions += 1
            d = books.transaction(tx_id).date
            s.last_date = max(s.last_date, d)
    return [by_name[c.name.lower()] for c in people]
