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
    for key, p in found.items():
        if key not in have:
            save(pf, p)
            n += 1
    return n
