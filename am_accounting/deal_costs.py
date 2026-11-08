"""Deposits and costs paid on a deal before it closes, and which company pays them.

A deal has no company of its own until it closes, so its deposit and its pre-closing costs (appraisal, building
condition assessment, environmental, legal …) are paid by one of the existing companies — usually a holding company.

* **Recording** a line puts it in the paying company's books, in an asset account for the deal ("Deal Deposits &
  Costs – <deal>"), paid one of three ways: a **new bank payment** (a hand-entered bank transaction), **owing**
  (a journal entry to Accounts Payable, with the vendor), or by **categorizing a payment already in its bank feed** —
  so nothing is counted twice.
* **On closing** (Close Deal), the recorded deposit counts toward the purchase price and the recorded costs become part
  of the property's cost in the new company, which owes the payer for them ("Due to <payer>"); the payer's deal
  account is cleared to "Due from <new company>".
* **A dead deal**'s recorded costs (not a refundable deposit) are written off in each payer's books
  ("Abandoned Deal Costs").
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS deal_costs (
    id            INTEGER PRIMARY KEY,
    deal_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'cost',      -- deposit | cost
    item          TEXT NOT NULL,
    vendor        TEXT NOT NULL DEFAULT '',
    amount_cents  INTEGER NOT NULL DEFAULT 0,
    date          TEXT,
    payer_path    TEXT NOT NULL DEFAULT '',          -- the company paying (its company file)
    payer_name    TEXT NOT NULL DEFAULT '',
    how           TEXT NOT NULL DEFAULT '',          -- bank | owing | matched ('' = not in the books yet)
    tx_id         INTEGER,                           -- the bank transaction in the payer's books
    entry_id      INTEGER,                           -- or the journal entry
    settled       TEXT NOT NULL DEFAULT '',          -- closed (carried to the new company) | written_off
    notes         TEXT NOT NULL DEFAULT '',
    sort          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_deal_costs ON deal_costs(deal_id, sort);
"""
KINDS = {"deposit": "Deposit", "cost": "Cost"}
STARTERS = [("deposit", "Deposit"), ("cost", "Appraisal"), ("cost", "Building condition assessment"),
            ("cost", "Phase I environmental (ESA)"), ("cost", "Legal"), ("cost", "Lender application fee")]


@dataclass
class DealCost:
    item: str
    kind: str = "cost"
    amount_cents: int = 0
    date: str | None = None
    payer_path: str = ""
    payer_name: str = ""
    vendor: str = ""
    how: str = ""
    tx_id: int | None = None
    entry_id: int | None = None
    settled: str = ""
    notes: str = ""
    id: int | None = None
    sort: int = 0

    @property
    def recorded(self) -> bool:
        return bool(self.how)

    @property
    def status(self) -> str:
        if self.settled == "closed":
            return "Carried to the new company"
        if self.settled == "written_off":
            return "Written off"
        return {"bank": "Paid from the bank", "owing": "Owing (AP)", "matched": "Matched to a bank payment"}.get(
            self.how, "Not in the books yet")


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


def costs(pf, deal_id: int) -> list[DealCost]:
    ready(pf)
    return [DealCost(r["item"], r["kind"], r["amount_cents"], r["date"], r["payer_path"], r["payer_name"],
                     r["vendor"], r["how"], r["tx_id"], r["entry_id"], r["settled"], r["notes"], r["id"], r["sort"])
            for r in pf.conn.execute("SELECT * FROM deal_costs WHERE deal_id=? ORDER BY sort, id", (deal_id,))]


def save(pf, deal_id: int, c: DealCost) -> int:
    ready(pf)
    if not c.item.strip():
        raise ValueError("Enter what the deposit or cost is for")
    if c.kind not in KINDS:
        raise ValueError("A line is a deposit or a cost")
    if c.amount_cents < 0:
        raise ValueError("Amounts can't be negative")
    if c.date:
        date.fromisoformat(c.date)
    vals = (deal_id, c.kind, c.item.strip(), c.vendor.strip(), c.amount_cents, c.date, c.payer_path, c.payer_name,
            c.how, c.tx_id, c.entry_id, c.settled, c.notes.strip(), c.sort)
    with pf.conn:
        if c.id is None:
            c.id = pf.conn.execute("INSERT INTO deal_costs(deal_id, kind, item, vendor, amount_cents, date, payer_path, "
                                   "payer_name, how, tx_id, entry_id, settled, notes, sort) "
                                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", vals).lastrowid
        else:
            pf.conn.execute("UPDATE deal_costs SET deal_id=?, kind=?, item=?, vendor=?, amount_cents=?, date=?, "
                            "payer_path=?, payer_name=?, how=?, tx_id=?, entry_id=?, settled=?, notes=?, sort=? "
                            "WHERE id=?", vals + (c.id,))
    return c.id


def delete(pf, cost_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM deal_costs WHERE id=?", (cost_id,))


# ---- the paying company's books ----

def _deal_account(books, deal_name: str) -> int:
    """The payer's asset account for this deal's deposit and costs (created if needed)."""
    name = f"Deal Deposits & Costs – {deal_name}"[:80]
    for a in books.accounts():
        if a.name.lower() == name.lower():
            return a.id
    used = {a.code for a in books.accounts()}
    code = "1450"
    while code in used:
        code = str(int(code) + 1)
    return books.add_account(code, name, "asset", "current").id


def _account(books, code: str, name: str, type_: str, subtype: str) -> int:
    for a in books.accounts():
        if a.name.lower() == name.lower() and a.type == type_:
            return a.id
    used = {a.code for a in books.accounts()}
    while code in used:
        code = str(int(code) + 1)
    return books.add_account(code, name, type_, subtype).id


def candidates(books, c: DealCost) -> list:
    """Bank payments in the payer's books that could be this line: money out of about the same amount, within 60
    days, not already given a category by hand."""
    out = []
    for t in books.transactions():
        if t.amount_cents >= 0 or (t.category_source == "manual" and t.category_id is not None):
            continue
        if c.amount_cents and abs(-t.amount_cents - c.amount_cents) > max(100, c.amount_cents // 100):
            continue
        if c.date and abs((date.fromisoformat(t.date) - date.fromisoformat(c.date)).days) > 60:
            continue
        out.append(t)
    return sorted(out, key=lambda t: abs(-t.amount_cents - c.amount_cents))


def record(pf, deal_name: str, c: DealCost, how: str, *, bank_id: int | None = None, tx_id: int | None = None,
           deal_id: int | None = None) -> None:
    """Put a line in the paying company's books (see the module notes); ``how`` is bank | owing | matched."""
    from . import contacts, parties
    from .books import Books

    if c.recorded:
        raise ValueError(f"“{c.item}” is already in {c.payer_name}'s books")
    if not c.payer_path or not Path(c.payer_path).exists():
        raise ValueError("Pick the company that pays it")
    if not c.amount_cents:
        raise ValueError("Enter the amount")
    day = c.date or date.today().isoformat()
    memo = f"{deal_name} – {c.item}"
    books = Books.open(c.payer_path, create=False)
    try:
        acct = _deal_account(books, deal_name)
        if how == "bank":
            bank = bank_id or next((a.id for a in books.bank_accounts()), None)
            if bank is None:
                raise ValueError(f"{c.payer_name} has no bank account in its books")
            c.tx_id = books.add_manual_transaction(bank, day, memo, -c.amount_cents, category_id=acct)
            if c.vendor:
                parties.set_name(books, [c.tx_id], c.vendor)
        elif how == "owing":
            from . import receivables as rx

            ap = rx.control_account(books, "bill").id
            c.entry_id = books.add_journal_entry(day, memo, [(acct, c.amount_cents, 0, c.vendor),
                                                             (ap, 0, c.amount_cents, c.vendor)])
        elif how == "matched":
            if tx_id is None:
                raise ValueError("Pick the bank payment")
            books.set_category(tx_id, acct, source="manual")
            c.tx_id = tx_id
            if c.vendor:
                parties.set_name(books, [tx_id], c.vendor)
        else:
            raise ValueError("Unknown way to record it")
        if c.vendor:
            contacts.ensure_contact(books, c.vendor, "vendor")
    finally:
        books.close()
    c.how = how
    if deal_id is not None:
        save(pf, deal_id, c)


def unrecord(pf, deal_id: int, c: DealCost) -> None:
    """Take a line back out of the payer's books (the hand-entered payment or entry removed; a matched bank payment
    goes back to uncategorized)."""
    from .books import Books

    if not c.recorded or c.settled:
        raise ValueError("Only a line in the books and not yet closed or written off can be taken out")
    if c.payer_path and Path(c.payer_path).exists():
        books = Books.open(c.payer_path, create=False)
        try:
            if c.how == "bank" and c.tx_id:
                books.delete_transactions([c.tx_id])
            elif c.how == "matched" and c.tx_id:
                books.set_category(c.tx_id, None)
            elif c.how == "owing" and c.entry_id:
                books.delete_journal_entry(c.entry_id)
        finally:
            books.close()
    c.how, c.tx_id, c.entry_id = "", None, None
    save(pf, deal_id, c)


@dataclass
class Prepaid:
    """What one company paid on the deal before closing (recorded in its books)."""
    payer_name: str
    payer_path: str
    deposit: int = 0
    costs: int = 0
    lines: list | None = None

    @property
    def total(self) -> int:
        return self.deposit + self.costs


def prepaid(pf, deal_id: int) -> list[Prepaid]:
    """Recorded, not yet settled lines by paying company."""
    out: dict[str, Prepaid] = {}
    for c in costs(pf, deal_id):
        if not c.recorded or c.settled:
            continue
        p = out.setdefault(c.payer_path, Prepaid(c.payer_name, c.payer_path, lines=[]))
        if c.kind == "deposit":
            p.deposit += c.amount_cents
        else:
            p.costs += c.amount_cents
        p.lines.append(c)
    return list(out.values())


def settle_on_closing(pf, deal_id: int, deal_name: str, company_name: str, on: str) -> None:
    """In each payer's books: the deal account cleared to "Due from <new company>"; the lines marked closed."""
    from .books import Books

    for p in prepaid(pf, deal_id):
        if not Path(p.payer_path).exists():
            continue
        books = Books.open(p.payer_path, create=False)
        try:
            acct = _deal_account(books, deal_name)
            due = _account(books, "1460", f"Due from {company_name}"[:80], "asset", "current")
            books.add_journal_entry(on, f"{deal_name} closed – deposit and costs owed by {company_name}",
                                    [(due, p.total, 0, company_name), (acct, 0, p.total, "")])
        finally:
            books.close()
        for c in p.lines:
            c.settled = "closed"
            save(pf, deal_id, c)


def write_off(pf, deal_id: int, deal_name: str, on: str | None = None, *, include_deposits: bool = False) -> int:
    """A dead deal: its recorded costs (and deposits, when lost too) expensed in each payer's books as Abandoned Deal
    Costs. Returns the amount written off."""
    from .books import Books

    on = on or date.today().isoformat()
    total = 0
    for p in prepaid(pf, deal_id):
        lines = [c for c in p.lines if c.kind == "cost" or include_deposits]
        amount = sum(c.amount_cents for c in lines)
        if not amount or not Path(p.payer_path).exists():
            continue
        books = Books.open(p.payer_path, create=False)
        try:
            acct = _deal_account(books, deal_name)
            exp = _account(books, "6980", "Abandoned Deal Costs", "expense", "other")
            books.add_journal_entry(on, f"{deal_name} – deal didn't go ahead, costs written off",
                                    [(exp, amount, 0, ""), (acct, 0, amount, "")])
        finally:
            books.close()
        for c in lines:
            c.settled = "written_off"
            save(pf, deal_id, c)
        total += amount
    return total
