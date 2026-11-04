"""Accounts receivable (invoices to customers) and accounts payable (bills from vendors).

How it fits the books:

* Recording an **invoice** posts  Dr Accounts Receivable / Cr the income account.
* Recording a **bill** posts      Dr the expense (or asset) account / Cr Accounts Payable.
* When the money moves, it shows up as a bank transaction. **Recording a
  payment** links that bank transaction to the invoice or bill and categorizes
  it to Accounts Receivable / Payable, so the deposit clears the receivable
  (Dr Bank / Cr A/R) instead of being counted as income a second time.

A bank transaction can pay several invoices, and an invoice can be paid in
several parts. Balances, overdue amounts and aging are worked out as of any date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import chart_of_accounts as coa
from .books import Books, Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    kind         TEXT NOT NULL CHECK (kind IN ('invoice','bill')),
    party        TEXT NOT NULL,
    number       TEXT NOT NULL DEFAULT '',
    issue_date   TEXT NOT NULL,
    due_date     TEXT NOT NULL,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    category_id  INTEGER NOT NULL REFERENCES accounts(id),
    control_id   INTEGER NOT NULL REFERENCES accounts(id),
    memo         TEXT NOT NULL DEFAULT '',
    entry_id     INTEGER REFERENCES journal_entries(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_doc_kind ON documents(kind, due_date);
CREATE TABLE IF NOT EXISTS document_payments (
    id             INTEGER PRIMARY KEY,
    document_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0)
);
CREATE INDEX IF NOT EXISTS ix_docpay_doc ON document_payments(document_id);
CREATE INDEX IF NOT EXISTS ix_docpay_tx ON document_payments(transaction_id);
CREATE TABLE IF NOT EXISTS document_files (
    id            INTEGER PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    rel_path      TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '',
    generated     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_docfile_doc ON document_files(document_id);
"""

KIND_LABELS = {"invoice": ("Invoice", "Customer", "Accounts Receivable"),
               "bill": ("Bill", "Vendor", "Accounts Payable")}
AGING_BUCKETS = ["Current", "1–30 days", "31–60 days", "61–90 days", "Over 90 days"]


def ensure_schema(books: Books) -> None:
    if books.first_time("schema:rx"):
        books.conn.executescript(SCHEMA)


@dataclass
class Document:
    id: int
    kind: str
    party: str
    number: str
    issue_date: str
    due_date: str
    amount_cents: int
    category_id: int
    control_id: int
    memo: str
    paid_cents: int = 0

    @property
    def balance_cents(self) -> int:
        return self.amount_cents - self.paid_cents

    def days_overdue(self, as_of: date) -> int:
        return (as_of - date.fromisoformat(self.due_date)).days

    def status(self, as_of: date | None = None) -> str:
        as_of = as_of or date.today()
        if self.balance_cents <= 0:
            return "Paid"
        if self.days_overdue(as_of) > 0:
            return f"Overdue {self.days_overdue(as_of)}d"
        return "Partly paid" if self.paid_cents else "Open"


def control_account(books: Books, kind: str):
    return books.role(coa.ACCOUNTS_RECEIVABLE if kind == "invoice" else coa.ACCOUNTS_PAYABLE)


def _post(books: Books, doc_id: int) -> None:
    """(Re)create the journal entry for a document."""
    row = books.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if row["entry_id"]:
        books.conn.execute("DELETE FROM journal_entries WHERE id=?", (row["entry_id"],))
    label = KIND_LABELS[row["kind"]][0]
    memo = f"{label} {row['number']} – {row['party']}".replace("  ", " ")
    amt = row["amount_cents"]
    from .invoicing import posting_lines

    itemized = posting_lines(books, doc_id) if row["kind"] == "invoice" else None
    if itemized:  # invoice from the generator: one credit per income account, plus sales tax
        lines = [(row["control_id"], amt, 0)] + [(a, 0, c) for a, c in itemized]
    elif row["kind"] == "invoice":
        lines = [(row["control_id"], amt, 0), (row["category_id"], 0, amt)]
    else:
        lines = [(row["category_id"], amt, 0), (row["control_id"], 0, amt)]
    cur = books.conn.execute("INSERT INTO journal_entries(date, memo, kind) VALUES (?,?, 'manual')",
                             (row["issue_date"], memo))
    books.conn.executemany("INSERT INTO journal_lines(entry_id, account_id, debit_cents, credit_cents) "
                           "VALUES (?,?,?,?)", [(cur.lastrowid, a, d, c) for a, d, c in lines])
    books.conn.execute("UPDATE documents SET entry_id=? WHERE id=?", (cur.lastrowid, doc_id))


def _validate(books: Books, kind: str, party: str, issue_date: str, due_date: str, amount_cents: int,
              category_id: int) -> None:
    if kind not in KIND_LABELS:
        raise ValueError(f"Unknown document kind {kind!r}")
    if not party.strip():
        raise ValueError(f"Enter the {KIND_LABELS[kind][1].lower()} name")
    if amount_cents <= 0:
        raise ValueError("The amount must be more than zero")
    if date.fromisoformat(due_date) < date.fromisoformat(issue_date):
        raise ValueError("The due date can't be before the issue date")
    cat = books.account(category_id)
    if cat.is_bank:
        raise ValueError("Choose what it's for (an income, expense or other account), not a bank account")
    if cat.key in (coa.ACCOUNTS_RECEIVABLE, coa.ACCOUNTS_PAYABLE):
        raise ValueError("Choose what it's for, not Accounts Receivable / Payable itself")


def control_choices(books: Books, kind: str) -> list:
    """Accounts an invoice can be owed in (receivables: non-bank assets) or a bill in (payables: liabilities),
    the main Accounts Receivable / Payable first."""
    main = control_account(books, kind)
    want = "asset" if kind == "invoice" else "liability"
    others = [a for a in books.accounts(types=(want,), active_only=True)
              if a.id != main.id and not a.is_bank and a.key not in (coa.UNCATEGORIZED_EXPENSE, coa.UNCATEGORIZED_INCOME)]
    under = [a for a in others if _is_under(books, a, main.id)]
    rest = [a for a in others if a not in under and a.subtype in ("current", "other_current", "")]
    rest += [a for a in others if a not in under and a not in rest]
    return [main] + under + rest


def _is_under(books: Books, acct, ancestor_id: int) -> bool:
    seen = set()
    while acct.parent_id and acct.parent_id not in seen:
        if acct.parent_id == ancestor_id:
            return True
        seen.add(acct.parent_id)
        acct = books.account(acct.parent_id)
    return False


def default_control(books: Books, kind: str, party: str = "") -> int:
    """The customer's / vendor's own account (Customers & Vendors list), else the account last used for them,
    else Accounts Receivable / Payable."""
    ensure_schema(books)
    if party.strip():
        from . import contacts

        c = contacts.find(books, party)
        acct = (c.receivable_id if kind == "invoice" else c.payable_id) if c else None
        if acct:
            try:
                books.account(acct)
                return acct
            except KeyError:
                pass
        row = books.conn.execute("SELECT control_id FROM documents WHERE kind=? AND LOWER(party)=LOWER(?) "
                                 "ORDER BY id DESC LIMIT 1", (kind, party.strip())).fetchone()
        if row:
            try:
                books.account(row[0])
                return row[0]
            except KeyError:
                pass
    return control_account(books, kind).id


def _validate_control(books: Books, kind: str, control_id: int, category_id: int) -> None:
    a = books.account(control_id)
    if a.is_bank:
        raise ValueError("Choose a receivable / payable account, not a bank account")
    if kind == "invoice" and a.type != "asset":
        raise ValueError(f"{a.name} isn't an asset account; an invoice is owed to you in a receivable (asset) account")
    if kind == "bill" and a.type != "liability":
        raise ValueError(f"{a.name} isn't a liability account; a bill is owed in a payable (liability) account")
    if control_id == category_id:
        raise ValueError("The receivable / payable account must differ from the income / expense account")


def add_control_account(books: Books, kind: str, name: str):
    """A new sub-account under Accounts Receivable / Payable, e.g. 'Accounts Receivable – Related Party'."""
    main = control_account(books, kind)
    if not name.strip():
        raise ValueError("Enter a name for the account")
    used = {a.code for a in books.accounts()}
    base = int(main.code) if main.code.isdigit() else (1200 if kind == "invoice" else 2000)
    code = next((str(n) for n in range(base + 1, base + 100) if str(n) not in used), None)
    if code is None:
        raise ValueError("No free account number next to Accounts Receivable / Payable")
    return books.add_account(code, name.strip(), main.type, main.subtype, parent_id=main.id)


def add_document(books: Books, kind: str, party: str, issue_date: str, due_date: str, amount_cents: int,
                 category_id: int, *, number: str = "", memo: str = "", control_id: int | None = None) -> int:
    ensure_schema(books)
    _validate(books, kind, party, issue_date, due_date, amount_cents, category_id)
    control = control_id or control_account(books, kind).id
    _validate_control(books, kind, control, category_id)
    with books.conn:
        cur = books.conn.execute(
            "INSERT INTO documents(kind, party, number, issue_date, due_date, amount_cents, category_id, control_id, "
            "memo) VALUES (?,?,?,?,?,?,?,?,?)",
            (kind, party.strip(), number.strip(), issue_date, due_date, amount_cents, category_id, control, memo))
        _post(books, cur.lastrowid)
    from . import contacts

    contacts.ensure_contact(books, party, "customer" if kind == "invoice" else "vendor")
    return cur.lastrowid


def update_document(books: Books, doc_id: int, *, party: str, issue_date: str, due_date: str, amount_cents: int,
                    category_id: int, number: str = "", memo: str = "", control_id: int | None = None) -> None:
    doc = get_document(books, doc_id)
    _validate(books, doc.kind, party, issue_date, due_date, amount_cents, category_id)
    control = control_id or doc.control_id
    _validate_control(books, doc.kind, control, category_id)
    if amount_cents < doc.paid_cents:
        raise ValueError(f"{fmt(doc.paid_cents)} has already been paid; the amount can't be less than that")
    with books.conn:
        books.conn.execute("UPDATE documents SET party=?, number=?, issue_date=?, due_date=?, amount_cents=?, "
                           "category_id=?, memo=?, control_id=? WHERE id=?",
                           (party.strip(), number.strip(), issue_date, due_date, amount_cents, category_id, memo,
                            control, doc_id))
        _post(books, doc_id)
    if control != doc.control_id:  # payments already recorded move to the new account too
        for tx, _amt in payments_for(books, doc_id):
            books.set_category(tx.id, control, source="manual", reviewed=True)


def delete_document(books: Books, doc_id: int) -> None:
    """Delete an invoice or bill. Payments linked to it go back to uncategorized."""
    doc = get_document(books, doc_id)
    tx_ids = [r[0] for r in books.conn.execute(
        "SELECT transaction_id FROM document_payments WHERE document_id=?", (doc_id,))]
    entry = books.conn.execute("SELECT entry_id FROM documents WHERE id=?", (doc_id,)).fetchone()[0]
    with books.conn:
        books.conn.execute("DELETE FROM documents WHERE id=?", (doc.id,))
        if entry:
            books.conn.execute("DELETE FROM journal_entries WHERE id=?", (entry,))
    for tx_id in tx_ids:
        _release_transaction(books, tx_id)


def fmt(cents: int) -> str:
    from .money import fmt_money

    return fmt_money(cents)


def _paid_map(books: Books, as_of: str | None = None) -> dict[int, int]:
    sql = ("SELECT p.document_id, SUM(p.amount_cents) FROM document_payments p "
           "JOIN transactions t ON t.id = p.transaction_id")
    args: list = []
    if as_of:
        sql += " WHERE t.date <= ?"
        args.append(as_of)
    sql += " GROUP BY p.document_id"
    return dict(books.conn.execute(sql, args).fetchall())


def _row_to_doc(r, paid: dict[int, int]) -> Document:
    return Document(r["id"], r["kind"], r["party"], r["number"], r["issue_date"], r["due_date"], r["amount_cents"],
                    r["category_id"], r["control_id"], r["memo"], paid.get(r["id"], 0))


def get_document(books: Books, doc_id: int) -> Document:
    ensure_schema(books)
    r = books.conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
    if r is None:
        raise KeyError(doc_id)
    return _row_to_doc(r, _paid_map(books))


def documents(books: Books, kind: str, *, status: str = "open", as_of: date | None = None,
              search: str = "") -> list[Document]:
    """status: open | overdue | paid | all. Balances reflect payments dated on or before ``as_of``."""
    ensure_schema(books)
    as_of = as_of or date.today()
    paid = _paid_map(books, as_of.isoformat())
    sql = "SELECT * FROM documents WHERE kind=? AND issue_date <= ?"
    args: list = [kind, as_of.isoformat()]
    if search:
        sql += " AND (party LIKE ? OR number LIKE ? OR memo LIKE ?)"
        args += [f"%{search}%"] * 3
    sql += " ORDER BY due_date, id"
    out = [_row_to_doc(r, paid) for r in books.conn.execute(sql, args)]
    if status == "open":
        out = [d for d in out if d.balance_cents > 0]
    elif status == "overdue":
        out = [d for d in out if d.balance_cents > 0 and d.days_overdue(as_of) > 0]
    elif status == "paid":
        out = [d for d in out if d.balance_cents <= 0]
    return out


def next_number(books: Books, kind: str) -> str:
    ensure_schema(books)
    prefix = "INV-" if kind == "invoice" else ""
    if not prefix:
        return ""
    nums = [r[0] for r in books.conn.execute("SELECT number FROM documents WHERE kind='invoice'")]
    best = 1000
    for n in nums:
        digits = "".join(ch for ch in n if ch.isdigit())
        if digits:
            best = max(best, int(digits))
    return f"{prefix}{best + 1}"


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


def applied_to_transaction(books: Books, tx_id: int) -> int:
    ensure_schema(books)
    return books.conn.execute("SELECT COALESCE(SUM(amount_cents),0) FROM document_payments WHERE transaction_id=?",
                              (tx_id,)).fetchone()[0]


def payments_for(books: Books, doc_id: int) -> list[tuple[Transaction, int]]:
    rows = books.conn.execute("SELECT transaction_id, amount_cents FROM document_payments WHERE document_id=? "
                              "ORDER BY id", (doc_id,)).fetchall()
    return [(books.transaction(t), a) for t, a in rows]


def record_payment(books: Books, doc_id: int, tx_id: int, amount_cents: int | None = None) -> int:
    """Link a bank transaction to an invoice (a deposit) or bill (a payment). Returns the amount applied."""
    doc = get_document(books, doc_id)
    tx = books.transaction(tx_id)
    if doc.kind == "invoice" and tx.amount_cents <= 0:
        raise ValueError("A customer payment must be a deposit (money in)")
    if doc.kind == "bill" and tx.amount_cents >= 0:
        raise ValueError("A bill payment must be money out of a bank account")
    available = abs(tx.amount_cents) - applied_to_transaction(books, tx_id)
    amount = amount_cents if amount_cents is not None else min(available, doc.balance_cents)
    if amount <= 0:
        raise ValueError("Nothing left to apply: the transaction or the balance is already fully used")
    if amount > available:
        raise ValueError(f"Only {fmt(available)} of this transaction is still unapplied")
    if amount > doc.balance_cents:
        raise ValueError(f"The {KIND_LABELS[doc.kind][0].lower()} only has {fmt(doc.balance_cents)} left to pay")
    with books.conn:
        books.conn.execute("INSERT INTO document_payments(document_id, transaction_id, amount_cents) VALUES (?,?,?)",
                           (doc_id, tx_id, amount))
    books.set_category(tx_id, doc.control_id, source="manual", reviewed=True)
    return amount


def remove_payment(books: Books, doc_id: int, tx_id: int) -> None:
    with books.conn:
        books.conn.execute("DELETE FROM document_payments WHERE document_id=? AND transaction_id=?", (doc_id, tx_id))
    _release_transaction(books, tx_id)


def _release_transaction(books: Books, tx_id: int) -> None:
    """A bank transaction no longer paying anything goes back to uncategorized for review."""
    try:
        books.transaction(tx_id)
    except KeyError:
        return
    if applied_to_transaction(books, tx_id) == 0:
        books.set_category(tx_id, None, source="manual", reviewed=False)


def suggest_payments(books: Books, doc_id: int, limit: int = 20) -> list[tuple[Transaction, int, str]]:
    """Bank transactions that could pay this document: [(transaction, unapplied cents, why)], best first."""
    doc = get_document(books, doc_id)
    sign = 1 if doc.kind == "invoice" else -1
    since = (date.fromisoformat(doc.issue_date) - timedelta(days=14)).isoformat()
    words = [w for w in doc.party.upper().replace(",", " ").split() if len(w) > 2 and w not in {"INC", "LLC", "THE"}]
    try:
        transfers = books.account_by_key(coa.TRANSFER_CLEARING).id
    except KeyError:
        transfers = None
    out = []
    for tx in books.transactions(start=since):
        if tx.amount_cents * sign <= 0 or (transfers is not None and tx.category_id == transfers):
            continue  # wrong direction, or money moving between your own accounts
        free = abs(tx.amount_cents) - applied_to_transaction(books, tx.id)
        if free <= 0:
            continue
        score, why = 0, []
        if free == doc.balance_cents:
            score += 3
            why.append("exact amount")
        elif abs(tx.amount_cents) == doc.amount_cents:
            score += 2
            why.append("same as invoice total" if doc.kind == "invoice" else "same as bill total")
        if doc.number and doc.number.upper() in tx.description.upper():
            score += 3
            why.append("number in description")
        if any(w in tx.description.upper() for w in words):
            score += 2
            why.append("name in description")
        if tx.category_id is None or tx.category_source != "manual":
            score += 1
        out.append((score, tx.date, tx, free, ", ".join(why)))
    out.sort(key=lambda x: (-x[0], x[1]))
    return [(tx, free, why) for _s, _d, tx, free, why in out[:limit]]


# ---------------------------------------------------------------------------
# Matching bank transactions to invoices and bills
# ---------------------------------------------------------------------------


@dataclass
class MatchProposal:
    tx: Transaction
    doc: Document
    amount_cents: int
    confidence: str   # "high" (applied automatically) or "possible" (needs a yes from you)
    why: str


def _party_words(party: str) -> list[str]:
    stop = {"INC", "LLC", "LTD", "CO", "CORP", "THE", "AND", "GROUP", "COMPANY", "SERVICES", "OF"}
    return [w for w in re.sub(r"[^A-Z0-9 ]", " ", party.upper()).split() if len(w) > 2 and w not in stop]


def find_matches(books: Books) -> list[MatchProposal]:
    """Propose which bank transactions pay which open invoices and bills.

    A proposal is **high** confidence when the amount matches exactly and either
    the invoice/bill number or the customer/vendor name appears in the bank
    description, or the amount is unique on both sides within the payment window.
    Each transaction and document is used at most once; best matches win.
    """
    ensure_schema(books)
    far = date(9999, 12, 31)
    docs = documents(books, "invoice", status="open", as_of=far) + documents(books, "bill", status="open", as_of=far)
    try:
        transfers = books.account_by_key(coa.TRANSFER_CLEARING).id
    except KeyError:
        transfers = None
    control_ids = {d.control_id for d in docs}
    txs = []
    for t in books.transactions():
        if t.category_id == transfers and transfers is not None:
            continue
        free = abs(t.amount_cents) - applied_to_transaction(books, t.id)
        if free <= 0:
            continue
        txs.append((t, free))

    candidates = []
    for d in docs:
        sign = 1 if d.kind == "invoice" else -1
        issued = date.fromisoformat(d.issue_date)
        latest = date.fromisoformat(d.due_date) + timedelta(days=120)
        words = _party_words(d.party)
        for t, free in txs:
            if t.amount_cents * sign <= 0 or free != d.balance_cents:
                continue
            td = date.fromisoformat(t.date)
            if not (issued - timedelta(days=3) <= td <= latest):
                continue
            desc = t.description.upper()
            why, score = ["exact amount"], 1
            if d.number and re.sub(r"\W", "", d.number.upper()) in re.sub(r"\W", "", desc):
                score += 4
                why.append(f"{d.number} in description")
            hits = [w for w in words if w in desc]
            if hits:
                score += 3
                why.append("name in description")
            decided = t.category_source == "manual" and t.category_id not in (None, *control_ids)
            if decided:
                score -= 2
                why.append(f"you categorized it as {books.account(t.category_id).name}")
            candidates.append([score, t, d, why, decided])

    # Unique on both sides: only one open document with that amount and one transaction that fits it.
    by_doc: dict[int, int] = {}
    by_tx: dict[int, int] = {}
    for _s, t, d, _w, _dec in candidates:
        by_doc[d.id] = by_doc.get(d.id, 0) + 1
        by_tx[t.id] = by_tx.get(t.id, 0) + 1
    for c in candidates:
        score, t, d, why, decided = c
        if by_doc[d.id] == 1 and by_tx[t.id] == 1 and score == 1:
            c[0] = 2
            why.append("only payment of that amount")

    candidates.sort(key=lambda c: (-c[0], c[1].date))
    used_tx: set[int] = set()
    used_doc: set[int] = set()
    out = []
    for score, t, d, why, decided in candidates:
        if t.id in used_tx or d.id in used_doc:
            continue
        used_tx.add(t.id)
        used_doc.add(d.id)
        confident = not decided and (score >= 4 or (score == 2 and "only payment of that amount" in why))
        out.append(MatchProposal(t, d, d.balance_cents, "high" if confident else "possible", ", ".join(why)))
    return out


def apply_matches(books: Books, proposals: list[MatchProposal]) -> int:
    done = 0
    for p in proposals:
        try:
            record_payment(books, p.doc.id, p.tx.id, p.amount_cents)
            done += 1
        except ValueError:
            continue  # state changed since the proposal was made
    return done


def auto_match(books: Books) -> list[MatchProposal]:
    """Apply only the high-confidence matches. Returns what was applied."""
    applied = [p for p in find_matches(books) if p.confidence == "high"]
    apply_matches(books, applied)
    return applied


# ---------------------------------------------------------------------------
# Summary for the dashboard
# ---------------------------------------------------------------------------


@dataclass
class AgingSummary:
    kind: str
    ledger_balance: int                    # balance of the A/R or A/P account(s) in the books
    open_total: int                        # sum of open invoice/bill balances
    open_count: int
    overdue_total: int
    overdue_count: int
    buckets: list[tuple[str, int]] = field(default_factory=list)
    top_parties: list[tuple[str, int]] = field(default_factory=list)
    due_next_7_days: int = 0

    @property
    def not_itemized(self) -> int:
        """Part of the balance not backed by invoices/bills (e.g. an opening balance)."""
        return self.ledger_balance - self.open_total


def control_ids(books: Books, kind: str) -> set[int]:
    """Accounts that make up AR (kind='invoice') or AP ('bill'): the main account, its sub-accounts, and every
    account invoices / bills have been assigned to (and their sub-accounts)."""
    ensure_schema(books)
    try:  # don't create the account just to show a zero
        control = books.account_by_key(coa.ACCOUNTS_RECEIVABLE if kind == "invoice" else coa.ACCOUNTS_PAYABLE)
        ids = {control.id}
    except KeyError:
        ids = set()
    ids |= {r[0] for r in books.conn.execute("SELECT DISTINCT control_id FROM documents WHERE kind=?", (kind,))}
    # accounts you created yourself that are clearly receivables / trade payables
    pattern = re.compile(r"receivable|\bA/R\b" if kind == "invoice" else r"accounts payable|trade payable|\bA/P\b",
                         re.I)
    want = "asset" if kind == "invoice" else "liability"
    ids |= {a.id for a in books.accounts() if a.type == want and not a.is_bank and pattern.search(a.name)}
    changed = True
    while changed:  # include sub-accounts
        new = {a.id for a in books.accounts() if a.parent_id in ids} - ids
        changed = bool(new)
        ids |= new
    return ids


def aging(books: Books, kind: str, as_of: date | None = None) -> AgingSummary:
    as_of = as_of or date.today()
    docs = documents(books, kind, status="open", as_of=as_of)
    ids = control_ids(books, kind)
    ledger = sum(books.account_balance(i, as_of=as_of.isoformat()) for i in ids)
    buckets = dict.fromkeys(AGING_BUCKETS, 0)
    by_party: dict[str, int] = {}
    overdue_total = overdue_count = due_soon = 0
    for d in docs:
        late = d.days_overdue(as_of)
        if late <= 0:
            b = "Current"
            if late > -8:
                due_soon += d.balance_cents
        elif late <= 30:
            b = "1–30 days"
        elif late <= 60:
            b = "31–60 days"
        elif late <= 90:
            b = "61–90 days"
        else:
            b = "Over 90 days"
        buckets[b] += d.balance_cents
        if late > 0:
            overdue_total += d.balance_cents
            overdue_count += 1
        by_party[d.party] = by_party.get(d.party, 0) + d.balance_cents
    top = sorted(by_party.items(), key=lambda kv: -kv[1])[:3]
    return AgingSummary(kind, ledger, sum(d.balance_cents for d in docs), len(docs), overdue_total, overdue_count,
                        list(buckets.items()), top, due_soon)
