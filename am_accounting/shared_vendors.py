"""One vendor list for every company: a vendor in any company's Customers & Vendors is in all of them.

Each company keeps its own list (with its own accounts), so sharing means **adding** to each company the vendors it
doesn't have yet — its name, type, email, phone, address, other names and notes, and, when that company has an
expense account of the same name, that account. A vendor a company already has (by name or other name) is left
exactly as it is there. Inactive vendors aren't shared.

It runs when the app opens and when you switch companies, and right after a vendor is added or changed in
Customers & Vendors. A vendor removed with "Remove from All Companies" is taken off every list (removing it from one
company alone would bring it back from the others).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Vendor:
    name: str
    type: str = "Supplier"
    email: str = ""
    phone: str = ""
    address: str = ""
    aliases: list[str] = field(default_factory=list)
    notes: str = ""
    expense_account: str = ""      # by name: matched to an account of that name in each company
    found_in: list[str] = field(default_factory=list)


def _open(path):
    from .books import Books

    return Books.open(path, create=False)


def collect(paths: list[str]) -> dict[str, Vendor]:
    """Every active vendor in these companies, by lower-case name (the first company with it gives its details)."""
    from . import contacts as ct

    out: dict[str, Vendor] = {}
    for p in paths:
        if not p or not Path(p).exists():
            continue
        try:
            b = _open(p)
        except Exception:  # noqa: BLE001 - a locked or broken file: skipped
            continue
        try:
            names = {a.id: a.name for a in b.accounts()}
            for c in ct.all_contacts(b, "vendor", active_only=True):
                v = out.get(c.name.lower())
                if v is None:
                    v = out[c.name.lower()] = Vendor(c.name, c.type, c.email, c.phone, c.address, list(c.aliases),
                                                     c.notes, names.get(c.expense_id, ""))
                v.found_in.append(p)
        finally:
            b.close()
    return out


def sync(paths: list[str]) -> dict[str, int]:
    """Add to each company the shared vendors it doesn't have. Returns {company file: vendors added}."""
    from . import contacts as ct

    vendors = collect(paths)
    added: dict[str, int] = {}
    for p in paths:
        if not p or not Path(p).exists():
            continue
        missing = [v for v in vendors.values() if p not in v.found_in]
        if not missing:
            continue
        try:
            b = _open(p)
        except Exception:  # noqa: BLE001
            continue
        try:
            skip = skipped(b)
            missing = [v for v in missing if v.name.lower() not in skip]
            accts = {a.name.lower(): a.id for a in b.accounts() if a.type == "expense"}
            n = 0
            for v in missing:
                if ct.find(b, v.name) is not None:          # already there (under another name): left as it is
                    continue
                aliases = [a for a in v.aliases if ct.find(b, a) is None]
                c = ct.Contact(v.name, "vendor", v.type or "Supplier", v.email, v.phone, v.address,
                               expense_id=accts.get(v.expense_account.lower()) if v.expense_account else None,
                               aliases=aliases, notes=v.notes)
                try:
                    ct.save(b, c)
                    n += 1
                except ValueError:
                    continue
            if n:
                added[p] = n
        finally:
            b.close()
    return added


def skipped(books) -> set[str]:
    """Vendors removed from this company only (not added back to it from the others)."""
    import json

    raw = books._meta("shared_vendor_skip")
    return set(json.loads(raw)) if raw else set()


def skip_here(books, name: str) -> None:
    import json

    s = skipped(books) | {name.lower()}
    with books.conn:
        books._set_meta("shared_vendor_skip", json.dumps(sorted(s)))


def remove_everywhere(paths: list[str], name: str) -> int:
    """Take a vendor off every company's list (transactions, invoices and bills keep their names). Returns how
    many companies had it."""
    from . import contacts as ct

    n = 0
    for p in paths:
        if not p or not Path(p).exists():
            continue
        try:
            b = _open(p)
        except Exception:  # noqa: BLE001
            continue
        try:
            c = ct.find(b, name)
            if c is not None and c.name.lower() == name.lower():
                ct.delete(b, c.id)
                n += 1
        finally:
            b.close()
    return n
