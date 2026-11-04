"""Invoice generator: itemized invoices for revenue going out to customers.

An invoice made here has line items (description, quantity, rate, income
account, tax rate), customer details, notes and the company's own details.
It is still an ordinary Accounts Receivable invoice (see receivables.py), so it
shows in AR aging, is matched to the customer's payment when the deposit is
imported, and is filed in Invoices & Bills as a PDF.

Posting (done by ``receivables._post``, which reads the lines from here)::

    Dr Accounts Receivable          total
        Cr each income account          its line amounts
        Cr Sales Tax Payable            tax

An invoice can also be made for money that has *already* come in — a deposit
in a bank statement with no invoice behind it. It is created and immediately
marked paid by that deposit, so the income isn't counted twice.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import chart_of_accounts as coa
from . import receivables as rx
from .books import Books
from .money import fmt_money

SALES_TAX_KEY = "2200"  # Sales Tax Payable (permanent key)

SCHEMA = """
CREATE TABLE IF NOT EXISTS invoice_lines (
    id           INTEGER PRIMARY KEY,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    description  TEXT NOT NULL,
    quantity     REAL NOT NULL,
    unit_cents   INTEGER NOT NULL,
    account_id   INTEGER NOT NULL REFERENCES accounts(id),
    tax_rate     REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_invline_doc ON invoice_lines(document_id, position);
CREATE TABLE IF NOT EXISTS invoice_details (
    document_id      INTEGER PRIMARY KEY REFERENCES documents(id) ON DELETE CASCADE,
    customer_email   TEXT NOT NULL DEFAULT '',
    customer_address TEXT NOT NULL DEFAULT '',
    reference        TEXT NOT NULL DEFAULT '',
    notes            TEXT NOT NULL DEFAULT '',
    sent_at          TEXT
);
CREATE TABLE IF NOT EXISTS customers (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email    TEXT NOT NULL DEFAULT '',
    address  TEXT NOT NULL DEFAULT ''
);
"""


def ensure_schema(books: Books) -> None:
    rx.ensure_schema(books)
    if books.first_time("schema:invoicing"):
        books.conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Company details printed on invoices
# ---------------------------------------------------------------------------


@dataclass
class InvoiceSettings:
    address: str = ""
    email: str = ""
    phone: str = ""
    payment_instructions: str = ""
    footer: str = "Thank you for your business."
    tax_label: str = "Sales tax"
    default_tax_rate: float = 0.0
    default_terms_days: int = 30


_SETTING_KEYS = {"address": str, "email": str, "phone": str, "payment_instructions": str, "footer": str,
                 "tax_label": str, "default_tax_rate": float, "default_terms_days": int}


def get_settings(books: Books) -> InvoiceSettings:
    s = InvoiceSettings()
    for k, typ in _SETTING_KEYS.items():
        v = books._meta(f"inv_{k}")
        if v is not None:
            try:
                setattr(s, k, typ(v))
            except ValueError:
                pass
    return s


def set_settings(books: Books, s: InvoiceSettings) -> None:
    if not 0 <= s.default_tax_rate < 100:
        raise ValueError("The tax rate must be between 0 and 100%")
    with books.conn:
        for k in _SETTING_KEYS:
            books._set_meta(f"inv_{k}", str(getattr(s, k)).strip())


# ---------------------------------------------------------------------------
# Lines and totals
# ---------------------------------------------------------------------------


@dataclass
class Line:
    description: str
    quantity: float
    unit_cents: int
    account_id: int
    tax_rate: float = 0.0   # percent, e.g. 13 for 13%

    @property
    def amount_cents(self) -> int:
        return round(self.quantity * self.unit_cents)


@dataclass
class Totals:
    subtotal_cents: int
    taxes: list[tuple[float, int]]  # (rate %, tax) for each rate used
    tax_cents: int
    total_cents: int


def totals(lines: list[Line]) -> Totals:
    subtotal = sum(ln.amount_cents for ln in lines)
    by_rate: dict[float, int] = {}
    for ln in lines:
        if ln.tax_rate:
            by_rate[ln.tax_rate] = by_rate.get(ln.tax_rate, 0) + ln.amount_cents
    taxes = [(r, round(base * r / 100)) for r, base in sorted(by_rate.items())]
    tax = sum(t for _r, t in taxes)
    return Totals(subtotal, taxes, tax, subtotal + tax)


def fmt_rate(rate: float) -> str:
    return f"{rate:g}%"


def posting_lines(books: Books, doc_id: int) -> list[tuple[int, int]] | None:
    """(account, credit) pairs for an itemized invoice, or None for a plain one. Used by receivables._post."""
    if not books._has_table("invoice_lines"):
        return None
    lines = get_lines(books, doc_id)
    if not lines:
        return None
    credits: dict[int, int] = {}
    for ln in lines:
        credits[ln.account_id] = credits.get(ln.account_id, 0) + ln.amount_cents
    tax = totals(lines).tax_cents
    if tax:
        acct = books.role(SALES_TAX_KEY).id
        credits[acct] = credits.get(acct, 0) + tax
    return [(a, c) for a, c in credits.items() if c]


def get_lines(books: Books, doc_id: int) -> list[Line]:
    if not books._has_table("invoice_lines"):
        return []
    return [Line(r["description"], r["quantity"], r["unit_cents"], r["account_id"], r["tax_rate"])
            for r in books.conn.execute("SELECT * FROM invoice_lines WHERE document_id=? ORDER BY position",
                                        (doc_id,))]


# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------


@dataclass
class Customer:
    name: str
    email: str = ""
    address: str = ""


def customers(books: Books) -> list[Customer]:
    ensure_schema(books)
    from . import contacts

    out = {c.name.lower(): Customer(c.name, c.email, c.address) for c in contacts.all_contacts(books, "customer")}
    for r in books.conn.execute("SELECT * FROM customers"):
        old = out.setdefault(r["name"].lower(), Customer(r["name"], r["email"], r["address"]))
        old.email, old.address = old.email or r["email"], old.address or r["address"]
    for (name,) in books.conn.execute("SELECT DISTINCT party FROM documents WHERE kind='invoice'"):
        out.setdefault(name.lower(), Customer(name))
    return sorted(out.values(), key=lambda c: c.name.lower())


def customer(books: Books, name: str) -> Customer | None:
    from . import contacts

    c = contacts.find(books, name)
    key = (c.name if c else name).strip().lower()
    return next((x for x in customers(books) if x.name.lower() == key), None)


def save_customer(books: Books, c: Customer) -> None:
    ensure_schema(books)
    books.conn.execute("INSERT INTO customers(name, email, address) VALUES (?,?,?) ON CONFLICT(name) DO UPDATE "
                       "SET email=excluded.email, address=excluded.address",
                       (c.name.strip(), c.email.strip(), c.address.strip()))
    from . import contacts

    k = contacts.ensure_contact(books, c.name, "customer")
    if k and ((c.email and not k.email) or (c.address and not k.address)):
        k.email, k.address = k.email or c.email.strip(), k.address or c.address.strip()
        contacts.save(books, k)


# ---------------------------------------------------------------------------
# Creating / editing invoices
# ---------------------------------------------------------------------------


@dataclass
class Invoice:
    customer: str
    issue_date: str
    due_date: str
    lines: list[Line]
    number: str = ""
    customer_email: str = ""
    customer_address: str = ""
    reference: str = ""        # customer's PO / job reference
    notes: str = ""
    id: int | None = None
    sent_at: str | None = None
    paid_cents: int = 0
    payments: list = field(default_factory=list)
    control_id: int | None = None  # receivable account it's owed in (None: the customer's usual / A/R)

    @property
    def totals(self) -> Totals:
        return totals(self.lines)


def _validate(books: Books, inv: Invoice) -> None:
    lines = [ln for ln in inv.lines if ln.description.strip() or ln.unit_cents]
    if not lines:
        raise ValueError("Add at least one line")
    for i, ln in enumerate(lines, start=1):
        if not ln.description.strip():
            raise ValueError(f"Line {i}: enter a description")
        if ln.quantity <= 0:
            raise ValueError(f"Line {i}: the quantity must be more than zero")
        if ln.unit_cents < 0:
            raise ValueError(f"Line {i}: the rate can't be negative (add a separate discount line instead)")
        acct = books.account(ln.account_id)
        if acct.is_bank or acct.key in (coa.ACCOUNTS_RECEIVABLE, coa.ACCOUNTS_PAYABLE):
            raise ValueError(f"Line {i}: choose an income account, not {acct.name}")
        if not 0 <= ln.tax_rate < 100:
            raise ValueError(f"Line {i}: the tax rate must be between 0 and 100%")
    inv.lines = lines
    if totals(lines).total_cents <= 0:
        raise ValueError("The invoice total must be more than zero")
    if inv.number.strip():
        dup = books.conn.execute("SELECT id FROM documents WHERE kind='invoice' AND number=? AND id != ?",
                                 (inv.number.strip(), inv.id or 0)).fetchone()
        if dup:
            raise ValueError(f"Invoice number {inv.number.strip()} is already used")


def _summary_memo(lines: list[Line]) -> str:
    first = lines[0].description.strip()
    return first if len(lines) == 1 else f"{first} (+{len(lines) - 1} more)"


def save_invoice(books: Books, inv: Invoice) -> int:
    """Create (inv.id None) or update an itemized invoice. Returns its id."""
    ensure_schema(books)
    _validate(books, inv)
    t = totals(inv.lines)
    memo = _summary_memo(inv.lines)
    args = dict(party=inv.customer, issue_date=inv.issue_date, due_date=inv.due_date, amount_cents=t.total_cents,
                category_id=inv.lines[0].account_id, number=inv.number, memo=memo, control_id=inv.control_id)
    # Lines first, so the journal entry is posted from them
    if inv.id is None:
        rx._validate(books, "invoice", **{k: v for k, v in args.items() if k not in ("number", "memo", "control_id")})
        control = inv.control_id or rx.default_control(books, "invoice", inv.customer)
        rx._validate_control(books, "invoice", control, inv.lines[0].account_id)
        inv.control_id = control
        with books.conn:
            cur = books.conn.execute(
                "INSERT INTO documents(kind, party, number, issue_date, due_date, amount_cents, category_id, "
                "control_id, memo) VALUES ('invoice',?,?,?,?,?,?,?,?)",
                (inv.customer.strip(), inv.number.strip(), inv.issue_date, inv.due_date, t.total_cents,
                 inv.lines[0].account_id, control, memo))
            doc_id = cur.lastrowid
            _write_lines(books, doc_id, inv)
            rx._post(books, doc_id)
    else:
        doc_id = inv.id
        paid = rx.get_document(books, doc_id).paid_cents
        if t.total_cents < paid:
            raise ValueError(f"{fmt_money(paid)} has already been paid; the total can't be less than that")
        with books.conn:
            _write_lines(books, doc_id, inv)
        rx.update_document(books, doc_id, **args)
    with books.conn:
        save_customer(books, Customer(inv.customer, inv.customer_email, inv.customer_address))
    inv.id = doc_id
    return doc_id


def _write_lines(books: Books, doc_id: int, inv: Invoice) -> None:
    books.conn.execute("DELETE FROM invoice_lines WHERE document_id=?", (doc_id,))
    books.conn.executemany(
        "INSERT INTO invoice_lines(document_id, position, description, quantity, unit_cents, account_id, tax_rate) "
        "VALUES (?,?,?,?,?,?,?)",
        [(doc_id, i, ln.description.strip(), ln.quantity, ln.unit_cents, ln.account_id, ln.tax_rate)
         for i, ln in enumerate(inv.lines)])
    books.conn.execute(
        "INSERT INTO invoice_details(document_id, customer_email, customer_address, reference, notes) "
        "VALUES (?,?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET customer_email=excluded.customer_email, "
        "customer_address=excluded.customer_address, reference=excluded.reference, notes=excluded.notes",
        (doc_id, inv.customer_email.strip(), inv.customer_address.strip(), inv.reference.strip(),
         inv.notes.strip()))


def load_invoice(books: Books, doc_id: int) -> Invoice:
    """An invoice as the generator edits it. Invoices made before the generator get one line."""
    ensure_schema(books)
    doc = rx.get_document(books, doc_id)
    if doc.kind != "invoice":
        raise ValueError("Not an invoice")
    lines = get_lines(books, doc_id) or [
        Line(doc.memo or books.account(doc.category_id).name, 1, doc.amount_cents, doc.category_id, 0)]
    d = books.conn.execute("SELECT * FROM invoice_details WHERE document_id=?", (doc_id,)).fetchone()
    cust = customer(books, doc.party)
    return Invoice(doc.party, doc.issue_date, doc.due_date, lines, doc.number,
                   d["customer_email"] if d else (cust.email if cust else ""),
                   d["customer_address"] if d else (cust.address if cust else ""),
                   d["reference"] if d else "", d["notes"] if d else "", doc.id,
                   d["sent_at"] if d else None, doc.paid_cents, rx.payments_for(books, doc_id), doc.control_id)


def new_invoice(books: Books, customer_name: str = "", issue: date | None = None) -> Invoice:
    """A blank invoice with the next number, default terms and one empty line."""
    s = get_settings(books)
    issue = issue or date.today()
    cust = customer(books, customer_name) if customer_name else None
    return Invoice(customer_name, issue.isoformat(), (issue + timedelta(days=s.default_terms_days)).isoformat(),
                   [Line("", 1, 0, default_income_account(books), s.default_tax_rate)],
                   rx.next_number(books, "invoice"), cust.email if cust else "", cust.address if cust else "")


def default_income_account(books: Books) -> int | None:
    for code in ("4100", "4000"):
        try:
            a = books.account_by_code(code)
            if a.type == "income":
                return a.id
        except KeyError:
            continue
    return next((a.id for a in books.accounts(types=("income",), active_only=True)), None)


def mark_sent(books: Books, doc_id: int, when: str | None = None) -> None:
    ensure_schema(books)
    when = when or date.today().isoformat()
    with books.conn:
        books.conn.execute("INSERT INTO invoice_details(document_id, sent_at) VALUES (?,?) "
                           "ON CONFLICT(document_id) DO UPDATE SET sent_at=excluded.sent_at", (doc_id, when))


def invoice_for_deposit(books: Books, tx_id: int, inv: Invoice) -> int:
    """Invoice for money already received: create it and mark it paid by that deposit."""
    tx = books.transaction(tx_id)
    if tx.amount_cents <= 0:
        raise ValueError("Only a deposit (money in) can pay an invoice")
    total = totals(inv.lines).total_cents
    if total > tx.amount_cents:
        raise ValueError(f"The invoice total {fmt_money(total)} is more than the deposit "
                         f"{fmt_money(tx.amount_cents)}")
    doc_id = save_invoice(books, inv)
    try:
        rx.record_payment(books, doc_id, tx_id, total)
    except ValueError:
        rx.delete_document(books, doc_id)
        raise
    return doc_id


def deposit_invoice(books: Books, tx_id: int) -> Invoice:
    """Pre-filled invoice for a deposit: dated that day, due on receipt, one line for the amount."""
    tx = books.transaction(tx_id)
    inv = new_invoice(books, issue=date.fromisoformat(tx.date))
    inv.due_date = tx.date
    acct = tx.category_id if tx.category_id and books.account(tx.category_id).type == "income" else None
    inv.lines = [Line("", 1, tx.amount_cents, acct or default_income_account(books), 0)]
    return inv


# ---------------------------------------------------------------------------
# The printed invoice
# ---------------------------------------------------------------------------


def _br(text: str) -> str:
    return "<br>".join(_html.escape(x) for x in text.strip().splitlines())


def invoice_html(books: Books, doc_id: int) -> str:
    return render_html(books, load_invoice(books, doc_id))


def render_html(books: Books, inv: Invoice) -> str:
    """The printed invoice (also used to preview an unsaved draft)."""
    s = get_settings(books)
    p = books.profile()
    e = _html.escape
    t = inv.totals
    balance = t.total_cents - inv.paid_cents
    show_tax_col = any(ln.tax_rate for ln in inv.lines)
    contact = " · ".join(x for x in (s.phone, s.email) if x)
    rows = []
    for ln in inv.lines:
        qty = f"{ln.quantity:g}"
        tax = f"<td class='r'>{fmt_rate(ln.tax_rate) if ln.tax_rate else '—'}</td>" if show_tax_col else ""
        rows.append(f"<tr><td>{e(ln.description)}</td><td class='r'>{qty}</td>"
                    f"<td class='r'>{e(fmt_money(ln.unit_cents))}</td>{tax}"
                    f"<td class='r'>{e(fmt_money(ln.amount_cents))}</td></tr>")
    span = 4 if show_tax_col else 3
    sums = [f"<tr><td colspan='{span}' class='r'>Subtotal</td><td class='r'>{e(fmt_money(t.subtotal_cents))}</td></tr>"]
    for rate, tax in t.taxes:
        sums.append(f"<tr><td colspan='{span}' class='r'>{e(s.tax_label)} {fmt_rate(rate)}</td>"
                    f"<td class='r'>{e(fmt_money(tax))}</td></tr>")
    sums.append(f"<tr class='total'><td colspan='{span}' class='r'>Total</td>"
                f"<td class='r'>{e(fmt_money(t.total_cents))}</td></tr>")
    if inv.paid_cents:
        sums.append(f"<tr><td colspan='{span}' class='r'>Paid</td>"
                    f"<td class='r'>({e(fmt_money(inv.paid_cents))})</td></tr>")
        sums.append(f"<tr class='total'><td colspan='{span}' class='r'>Balance due</td>"
                    f"<td class='r'>{e(fmt_money(max(balance, 0)))}</td></tr>")
    stamp = "PAID" if balance <= 0 else ""
    tax_head = "<th class='r'>Tax</th>" if show_tax_col else ""
    ref = f"<b>Reference</b> {e(inv.reference)}<br>" if inv.reference else ""
    bill_to = "<br>".join(x for x in (f"<b>{e(inv.customer)}</b>", _br(inv.customer_address), e(inv.customer_email))
                          if x)
    extra = []
    if inv.notes:
        extra.append(f"<p>{_br(inv.notes)}</p>")
    if s.payment_instructions and balance > 0:
        extra.append(f"<p><b>How to pay</b><br>{_br(s.payment_instructions)}</p>")
    if s.footer:
        extra.append(f"<p class='muted'>{_br(s.footer)}</p>")
    return f"""<html><head><style>
body {{ font-family: Helvetica, Arial, sans-serif; color: #1c2430; font-size: 10.5pt; }}
h1 {{ font-size: 24pt; margin: 0; color: #1f3a5f; }}
.company {{ font-size: 15pt; font-weight: bold; color: #1f3a5f; }}
.muted {{ color: #5b6573; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ padding: 6px 8px; vertical-align: top; }}
th {{ text-align: left; border-bottom: 1.5px solid #1c2430; background: #eef2f7; }}
.r {{ text-align: right; }}
.total td {{ font-weight: bold; border-top: 1.5px solid #1c2430; font-size: 12pt; }}
.stamp {{ color: #1b7f3b; font-size: 20pt; font-weight: bold; }}
</style></head><body>
<table width="100%"><tr>
<td><div class="company">{e(p.name)}</div>
<div class="muted">{e(p.legal_name) + '<br>' if p.legal_name and p.legal_name != p.name else ''}{_br(s.address)}
{('<br>' + e(contact)) if contact else ''}{('<br>Tax ID ' + e(p.tax_id)) if p.tax_id else ''}</div></td>
<td class="r"><h1>INVOICE</h1><div class="stamp">{stamp}</div></td></tr></table>
<br>
<table width="100%"><tr>
<td><span class="muted">BILL TO</span><br>{bill_to}</td>
<td class="r"><b>Invoice #</b> {e(inv.number or '—')}<br><b>Date</b> {e(inv.issue_date)}<br>
<b>Due</b> {e(inv.due_date)}<br>{ref}<b>Amount due</b> {e(fmt_money(max(balance, 0)))}</td></tr></table>
<br>
<table width="100%"><tr><th>Description</th><th class="r">Qty</th><th class="r">Rate</th>{tax_head}<th class="r">Amount</th></tr>
{''.join(rows)}
{''.join(sums)}
</table>
<br>{''.join(extra)}
</body></html>"""
