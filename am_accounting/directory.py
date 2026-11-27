"""Contacts (beside Personal Finance): the people you work with across the portfolio — contractors, investors,
lenders, brokers, lawyers, property managers … — with how to reach them and which properties or deals they're on.

Kept in the portfolio file (on this computer). Investors (from the cap tables) and lenders (from the loans) can be
added in one go; someone already in the list (by name) isn't added twice.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

KINDS = ["Contractor", "Investor", "Lender", "Mortgage broker", "Real estate broker", "Lawyer", "Accountant",
         "Property manager", "Insurance", "Appraiser / consultant", "Other"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS directory (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    company   TEXT NOT NULL DEFAULT '',
    kind      TEXT NOT NULL DEFAULT 'Other',
    phone     TEXT NOT NULL DEFAULT '',
    email     TEXT NOT NULL DEFAULT '',
    address   TEXT NOT NULL DEFAULT '',
    website   TEXT NOT NULL DEFAULT '',
    notes     TEXT NOT NULL DEFAULT '',
    works_on  TEXT NOT NULL DEFAULT '[]',   -- the properties / deals they're on (names)
    updated   TEXT NOT NULL DEFAULT ''
);
"""


@dataclass
class Person:
    name: str
    company: str = ""
    kind: str = "Other"
    phone: str = ""
    email: str = ""
    address: str = ""
    website: str = ""
    notes: str = ""
    works_on: list[str] = field(default_factory=list)
    id: int | None = None
    updated: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} ({self.company})" if self.company and self.company != self.name else self.name


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


def _row(r) -> Person:
    return Person(r["name"], r["company"], r["kind"], r["phone"], r["email"], r["address"], r["website"], r["notes"],
                  json.loads(r["works_on"] or "[]"), r["id"], r["updated"])


def people(pf, kind: str | None = None, search: str = "") -> list[Person]:
    """Everyone (or one kind), matching every word of ``search`` in any detail; by name."""
    ready(pf)
    sql, args = "SELECT * FROM directory", []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    out = [_row(r) for r in pf.conn.execute(sql + " ORDER BY name COLLATE NOCASE", args)]
    words = search.lower().split()
    if words:
        def hay(p: Person) -> str:
            return " ".join([p.name, p.company, p.kind, p.phone, p.email, p.address, p.website, p.notes,
                             *p.works_on]).lower()
        out = [p for p in out if all(w in hay(p) for w in words)]
    return out


def save(pf, p: Person) -> int:
    ready(pf)
    if not p.name.strip():
        raise ValueError("Enter the name")
    p.updated = date.today().isoformat()
    vals = (p.name.strip(), p.company.strip(), p.kind or "Other", p.phone.strip(), p.email.strip(), p.address.strip(),
            p.website.strip(), p.notes.strip(), json.dumps(p.works_on), p.updated)
    with pf.conn:
        if p.id is None:
            p.id = pf.conn.execute("INSERT INTO directory(name, company, kind, phone, email, address, website, notes, "
                                   "works_on, updated) VALUES (?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
        else:
            pf.conn.execute("UPDATE directory SET name=?, company=?, kind=?, phone=?, email=?, address=?, website=?, "
                            "notes=?, works_on=?, updated=? WHERE id=?", vals + (p.id,))
    return p.id


def delete(pf, person_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM directory WHERE id=?", (person_id,))


def counts(pf) -> dict[str, int]:
    ready(pf)
    return dict(pf.conn.execute("SELECT kind, COUNT(*) FROM directory GROUP BY kind").fetchall())


def places(pf) -> list[str]:
    """What someone can be on: the portfolio's properties (short names) and the deals in the pipeline."""
    from . import personal as pers

    out = []
    for h in pf.holdings():
        try:
            out.append(pers.short_name(pf, h))
        except Exception:  # noqa: BLE001
            out.append(h.name)
    try:
        from . import deals as dl

        out += [d.name for d in dl.deals(pf) if d.holding_id is None]
    except Exception:  # noqa: BLE001
        pass
    return list(dict.fromkeys(x for x in out if x))


def from_portfolio(pf) -> int:
    """Add the investors on the cap tables and the lenders on the loans (with the properties they're on) who
    aren't in the list yet. Returns how many were added."""
    from . import personal as pers
    from . import portfolio as pfm

    have = {p.name.strip().lower() for p in people(pf)}
    found: dict[str, Person] = {}
    for h in pf.holdings():
        try:
            where = pers.short_name(pf, h)
        except Exception:  # noqa: BLE001
            where = h.name
        for x in pfm.entered_shareholders(pf, h.id):
            name = (x.name or x.company).strip()
            if not name or x.link in ("me", "holdco"):
                continue
            p = found.setdefault(name.lower(), Person(name, x.company if x.company != name else "", "Investor"))
            if where not in p.works_on:
                p.works_on.append(where)
        try:
            loans = pfm.loans(pf, h, with_books=False)
        except Exception:  # noqa: BLE001
            loans = []
        for ln in loans:
            name = (ln.lender or "").strip()
            if not name:
                continue
            p = found.setdefault(name.lower(), Person(name, name, "Lender"))
            if where not in p.works_on:
                p.works_on.append(where)
    n = 0
    existing = {p.name.strip().lower(): p for p in people(pf)}
    for key, p in found.items():
        if key not in have:
            save(pf, p)
            n += 1
        else:                               # already a contact: put them on the properties they're on
            q = existing[key]
            more = [w for w in p.works_on if w not in q.works_on]
            if more:
                q.works_on += more
                save(pf, q)
    return n


# ---- projects (a deal and the property it became are one project) ----

def _short(pf, h) -> str:
    from . import personal as pers

    try:
        return pers.short_name(pf, h)
    except Exception:  # noqa: BLE001
        return h.name


def project_names(pf, *, holding=None, deal=None) -> tuple[str, list[str]]:
    """(the name people are linked under, every name the project goes by) for a property or a deal."""
    from . import deals as dl

    names: list[str] = []
    try:
        all_deals = dl.deals(pf)
    except Exception:  # noqa: BLE001
        all_deals = []
    if deal is not None and deal.holding_id is not None:
        holding = next((h for h in pf.holdings() if h.id == deal.holding_id), holding)
    if holding is not None:
        primary = _short(pf, holding)
        names += [primary, holding.name]
        names += [d.name for d in all_deals if d.holding_id == holding.id]
    else:
        primary = deal.name if deal is not None else ""
    if deal is not None:
        names.append(deal.name)
    names = list(dict.fromkeys(n for n in names if n))
    return primary or (names[0] if names else ""), names


def on_project(pf, names: list[str], kind: str | None = None) -> list[Person]:
    """The contacts on a project (any of its names), by kind then name."""
    want = {n.strip().lower() for n in names if n.strip()}
    out = [p for p in people(pf, kind) if want & {w.strip().lower() for w in p.works_on}]
    order = {k: i for i, k in enumerate(KINDS)}
    return sorted(out, key=lambda p: (order.get(p.kind, len(KINDS)), p.name.lower()))


def link(pf, p: Person, name: str) -> None:
    if name and name not in p.works_on:
        p.works_on.append(name)
        save(pf, p)


def unlink(pf, p: Person, names: list[str]) -> None:
    drop = {n.strip().lower() for n in names}
    keep = [w for w in p.works_on if w.strip().lower() not in drop]
    if keep != p.works_on:
        p.works_on = keep
        save(pf, p)


def find(pf, name: str) -> Person | None:
    """The contact with this name (or company), if there is one."""
    key = (name or "").strip().lower()
    if not key:
        return None
    ps = people(pf)
    return (next((p for p in ps if p.name.strip().lower() == key), None)
            or next((p for p in ps if p.company.strip().lower() == key), None))


def details(p: Person) -> str:
    """One line of how to reach someone."""
    return " · ".join(x for x in (p.company if p.company != p.name else "", p.phone, p.email, p.address) if x)


# ---- into a company's books: someone from Contacts as a vendor (or customer) of that company ----

BOOKS_TYPE = {"Contractor": "Contractor", "Lender": "Lender", "Mortgage broker": "Supplier", "Investor": "Shareholder",
              "Other": "Other"}


def book_name(p: Person) -> str:
    """The name they go by in the books: their company (how bills and the bank show them), else their own name."""
    return (p.company or p.name).strip()


def assign(books, p: Person, role: str = "vendor"):
    """Make ``p`` a vendor (or customer) in ``books``: added to its Customers & Vendors with their phone, email and
    address, or, if they're there already, made that role too. Returns the books' contact."""
    from . import contacts as ct

    name = book_name(p)
    c = ct.find(books, name) or (ct.find(books, p.name) if p.company else None)
    if c is None:
        c = ct.Contact(name, role, BOOKS_TYPE.get(p.kind, "Supplier" if role == "vendor" else "Customer"),
                       email=p.email, phone=p.phone, address=p.address,
                       aliases=[p.name] if p.company and p.company.strip().lower() != p.name.strip().lower() else [],
                       notes=f"{p.kind} — from Contacts")
    else:
        if c.role != role and c.role != "both":
            c.role = "both"
        c.email, c.phone, c.address = c.email or p.email, c.phone or p.phone, c.address or p.address
    ct.save(books, c)
    return c
