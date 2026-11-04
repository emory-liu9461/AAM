"""Revenue by customer and expenses by vendor.

Every income and expense amount in the books is traced back to who it was with:

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
    rows = books.conn.execute(
        "SELECT e.id, e.date, e.transaction_id, l.account_id, l.debit_cents, l.credit_cents FROM journal_lines l "
        "JOIN journal_entries e ON e.id = l.entry_id WHERE e.kind != 'opening' AND e.date >= ? AND e.date <= ?",
        (start, end))
    for entry_id, d, tx_id, acct, debit, credit in rows:
        if acct in revenue_ids:
            side, amt = "customers", credit - debit
        elif acct in expense_ids:
            side, amt = "vendors", debit - credit
        else:
            continue
        if not amt:
            continue
        who = doc_party.get(entry_id)
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


def company_breakdown(company_path: str | Path, end: date | None = None, months: int = 12,
                      start: str | None = None) -> Breakdown:
    """The last ``months`` months to ``end`` (or ``start``..``end``) for a company file (opened, read, closed)."""
    end = end or date.today()
    start = start or (end - timedelta(days=round(months * 365.25 / 12)) + timedelta(days=1)).isoformat()
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
