"""Revenue by customer and expenses by vendor.

Every income and expense amount in the books is traced back to who it was with:

* a journal entry's line → the customer / vendor entered on it;
* a bank transaction → its name (set by you, from a matched e-Transfer or cheque, or a name rule);
* an invoice or bill → its customer / vendor.

Names are resolved through the Customers & Vendors list (aliases → the contact's name). Amounts with no name
stay in an "unassigned" line, so the totals always add up to the income statement.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .books import Books

UNASSIGNED = "(not assigned)"
NON_CASH = ("depreciation", "amortization", "amortisation")


@dataclass
class PartyTotal:
    name: str
    amount: int = 0                   # revenue (customers) or expenses (vendors) in the period
    count: int = 0                    # entries making it up
    by_account: dict[str, int] = field(default_factory=dict)
    outstanding: int = 0              # receivable (customers) / payable (vendors) at the end date
    last_date: str = ""
    listed: bool = False              # on the Customers & Vendors list
    share: float = 0.0                # of total revenue / expenses

    @property
    def main_account(self) -> str:
        if not self.by_account:
            return ""
        top = max(self.by_account.items(), key=lambda kv: kv[1])[0]
        return top + (f" +{len(self.by_account) - 1}" if len(self.by_account) > 1 else "")


@dataclass
class Breakdown:
    start: str
    end: str
    customers: list[PartyTotal]       # largest first; the unassigned line (if any) last
    vendors: list[PartyTotal]
    revenue: int = 0
    expenses: int = 0
    company: str = ""
    error: str = ""

    def unassigned(self, side: str) -> int:
        rows = self.customers if side == "customers" else self.vendors
        return sum(p.amount for p in rows if p.name == UNASSIGNED)


def breakdown(books: Books, start: str, end: str) -> Breakdown:
    from . import arap, contacts, parties

    out = Breakdown(start, end, [], [], company=books.company_name)
    accts = {a.id: a for a in books.accounts()}
    revenue_ids = {i for i, a in accts.items() if a.type == "income"}
    expense_ids = {i for i, a in accts.items() if a.type == "expense"
                   and not any(w in a.name.lower() for w in NON_CASH)}
    names = parties.names(books)
    amap = contacts.alias_map(books)
    doc_party: dict[int, str] = {}
    if books._has_table("documents"):
        for entry_id, party in books.conn.execute("SELECT entry_id, party FROM documents WHERE entry_id IS NOT NULL"):
            doc_party[entry_id] = amap.get(party.strip().lower(), party.strip())
    listed = {c.name.lower() for c in contacts.all_contacts(books)}
    sides: dict[str, dict[str, PartyTotal]] = {"customers": {}, "vendors": {}}
    entry_party = books.entry_parties()
    has_party = "party" in {r[1] for r in books.conn.execute("PRAGMA table_info(journal_lines)")}
    rows = books.conn.execute(
        "SELECT e.id, e.date, e.transaction_id, l.account_id, l.debit_cents, l.credit_cents, "
        f"{'l.party' if has_party else chr(39) * 2} FROM journal_lines l "
        "JOIN journal_entries e ON e.id = l.entry_id WHERE e.kind != 'opening' AND e.date >= ? AND e.date <= ?",
        (start, end))
    for entry_id, d, tx_id, acct, debit, credit, line_party in rows:
        if acct in revenue_ids:
            side, amt = "customers", credit - debit
        elif acct in expense_ids:
            side, amt = "vendors", debit - credit
        else:
            continue
        if not amt:
            continue
        line_party = ((line_party or "").strip()                                       # on the line, or its side
                      or entry_party.get(entry_id, ("", ""))[0 if side == "customers" else 1])
        who = amap.get(line_party.lower(), line_party) if line_party else ""
        who = who or doc_party.get(entry_id)
        if not who and tx_id is not None and tx_id in names:
            who = names[tx_id].name
        who = who or UNASSIGNED
        p = sides[side].setdefault(who.lower(), PartyTotal(who, listed=who.lower() in listed))
        p.amount += amt
        p.count += 1
        an = accts[acct].name
        p.by_account[an] = p.by_account.get(an, 0) + amt
        p.last_date = max(p.last_date, d)
    end_d = date.fromisoformat(end)
    for kind, side in (("invoice", "customers"), ("bill", "vendors")):
        try:
            items = arap.outstanding(books, kind, end_d)
        except Exception:  # noqa: BLE001 - outstanding balances are a nicety here
            items = []
        for it in items:
            who = amap.get((it.party or "").lower(), it.party or "") or UNASSIGNED
            p = sides[side].setdefault(who.lower(), PartyTotal(who, listed=who.lower() in listed))
            p.outstanding += it.balance_cents
    out.revenue = sum(p.amount for p in sides["customers"].values())
    out.expenses = sum(p.amount for p in sides["vendors"].values())
    for side, total in (("customers", out.revenue), ("vendors", out.expenses)):
        rows_ = sorted(sides[side].values(), key=lambda p: (p.name == UNASSIGNED, -p.amount, p.name.lower()))
        for p in rows_:
            p.share = p.amount / total if total else 0.0
        setattr(out, side, rows_)
    return out


_CACHE: dict = {}       # (file, its modified time and size, …) → result: reused until the file changes


def _file_key(company_path) -> tuple | None:
    try:
        st = Path(company_path).stat()
        return (str(Path(company_path).resolve()), st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def _cached(key, compute):
    if key is None or key[0] is None:
        return compute()
    if key in _CACHE:
        return _CACHE[key]
    out = compute()
    if not getattr(out, "error", ""):
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = out
        after = _file_key(key[0][0])     # reading can set up tables the first time: keep it under that too
        if after is not None:
            _CACHE[(after, *key[1:])] = out
    return out


def company_breakdown(company_path: str | Path, end: date | None = None, months: int = 12,
                      start: str | None = None) -> Breakdown:
    """The last ``months`` months to ``end`` (or ``start``..``end``) for a company file (opened, read, closed;
    the result is reused until the file changes)."""
    end = end or date.today()
    start = start or (end - timedelta(days=round(months * 365.25 / 12)) + timedelta(days=1)).isoformat()
    return _cached((_file_key(company_path), "breakdown", start, end.isoformat()),
                   lambda: _company_breakdown(company_path, start, end))


def _company_breakdown(company_path, start: str, end: date) -> Breakdown:
    try:
        books = Books.open(company_path, create=False)
    except Exception as e:  # noqa: BLE001
        return Breakdown(start, end.isoformat(), [], [], error=f"Couldn't open the company file: {e}")
    try:
        return breakdown(books, start, end.isoformat())
    except Exception as e:  # noqa: BLE001
        return Breakdown(start, end.isoformat(), [], [], error=str(e))
    finally:
        books.close()


@dataclass
class AssignmentCheck:
    """What isn't tied to a customer or vendor yet."""
    revenue_unassigned: int = 0          # revenue in the period with no customer
    revenue_entries: int = 0
    expenses_unassigned: int = 0         # expenses in the period with no vendor
    expense_entries: int = 0
    ar_unallocated: int = 0              # owed to you, not allocated to a customer on the list
    ar_items: list = field(default_factory=list)
    ap_unallocated: int = 0              # you owe, not allocated to a vendor on the list
    ap_items: list = field(default_factory=list)
    unlisted_customers: list[str] = field(default_factory=list)   # names used but not on Customers & Vendors
    unlisted_vendors: list[str] = field(default_factory=list)
    revenue: int = 0
    expenses: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not (self.revenue_unassigned or self.expenses_unassigned or self.ar_items or self.ap_items
                    or self.unlisted_customers or self.unlisted_vendors)

    def lines(self) -> list[str]:
        from .money import fmt_money

        out = []
        if self.revenue_unassigned:
            out.append(f"{fmt_money(self.revenue_unassigned)} of revenue ({self.revenue_entries} entries) has no "
                       f"customer")
        if self.expenses_unassigned:
            out.append(f"{fmt_money(self.expenses_unassigned)} of expenses ({self.expense_entries} entries) has no "
                       f"vendor")
        if self.ar_items:
            out.append(f"{fmt_money(self.ar_unallocated)} owed to you ({len(self.ar_items)} item(s)) isn't allocated "
                       f"to a customer")
        if self.ap_items:
            out.append(f"{fmt_money(self.ap_unallocated)} you owe ({len(self.ap_items)} item(s)) isn't allocated to a "
                       f"vendor")
        if self.unlisted_customers:
            out.append(f"{len(self.unlisted_customers)} customer name(s) not on the Customers & Vendors list: "
                       + ", ".join(self.unlisted_customers[:5]) + (" …" if len(self.unlisted_customers) > 5 else ""))
        if self.unlisted_vendors:
            out.append(f"{len(self.unlisted_vendors)} vendor name(s) not on the Customers & Vendors list: "
                       + ", ".join(self.unlisted_vendors[:5]) + (" …" if len(self.unlisted_vendors) > 5 else ""))
        return out


def assignment_check(books: Books, start: str, end: str) -> AssignmentCheck:
    """Revenue without a customer, expenses without a vendor, AR / AP not allocated to someone on the
    Customers & Vendors list, and names in use that aren't on the list."""
    from . import arap, contacts

    b = breakdown(books, start, end)
    c = AssignmentCheck(revenue=b.revenue, expenses=b.expenses)
    for p in b.customers:
        if p.name == UNASSIGNED:
            c.revenue_unassigned, c.revenue_entries = p.amount, p.count
        elif not p.listed and p.amount:
            c.unlisted_customers.append(p.name)
    for p in b.vendors:
        if p.name == UNASSIGNED:
            c.expenses_unassigned, c.expense_entries = p.amount, p.count
        elif not p.listed and p.amount:
            c.unlisted_vendors.append(p.name)
    listed = {x.name.lower() for x in contacts.all_contacts(books)}
    end_d = date.fromisoformat(end)
    for kind, attr in (("invoice", "ar"), ("bill", "ap")):
        items = [i for i in arap.outstanding(books, kind, end_d) if i.balance_cents
                 and (i.party or "").lower() not in listed]
        setattr(c, f"{attr}_items", items)
        setattr(c, f"{attr}_unallocated", sum(i.balance_cents for i in items))
    return c


def company_assignment_check(company_path: str | Path, end: date | None = None, start: str | None = None,
                             months: int = 12) -> AssignmentCheck:
    end = end or date.today()
    start = start or (end - timedelta(days=round(months * 365.25 / 12)) + timedelta(days=1)).isoformat()
    return _cached((_file_key(company_path), "assignment", start, end.isoformat()),
                   lambda: _company_assignment_check(company_path, start, end))


def _company_assignment_check(company_path, start: str, end: date) -> AssignmentCheck:
    try:
        books = Books.open(company_path, create=False)
    except Exception as e:  # noqa: BLE001
        return AssignmentCheck(error=f"Couldn't open the company file: {e}")
    try:
        return assignment_check(books, start, end.isoformat())
    except Exception as e:  # noqa: BLE001
        return AssignmentCheck(error=str(e))
    finally:
        books.close()
