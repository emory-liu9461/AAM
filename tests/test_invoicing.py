from datetime import date

import pytest

from am_accounting import invoicing as inv
from am_accounting import receivables as rx
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture
def books(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    yield b
    b.close()


def _acct(books, code):
    return books.account_by_code(code).id


def test_totals_by_tax_rate():
    lines = [inv.Line("Design", 10, 12500, 1, 13), inv.Line("Hosting", 3, 2000, 1, 13),
             inv.Line("Exempt item", 1, 5000, 1, 0), inv.Line("Half", 0.5, 333, 1, 5)]
    t = inv.totals(lines)
    assert lines[3].amount_cents == 166  # 0.5 × $3.33, rounded
    assert t.subtotal_cents == 125000 + 6000 + 5000 + 166
    assert t.taxes == [(5, 8), (13, 17030)]
    assert t.total_cents == t.subtotal_cents + 17038


def test_itemized_invoice_posts_income_by_account_and_sales_tax(books):
    services, sales = _acct(books, "4100"), _acct(books, "4000")
    invoice = inv.new_invoice(books, "Northwind Labs")
    assert invoice.number == "INV-1001" and invoice.due_date
    invoice.issue_date, invoice.due_date = "2026-10-01", "2026-10-31"
    invoice.customer_email, invoice.customer_address = "ap@northwind.test", "1 Main St\nHalifax NS"
    invoice.lines = [inv.Line("Consulting — October", 12, 15000, services, 13),
                     inv.Line("Printed manuals", 4, 2500, sales, 13)]
    doc_id = inv.save_invoice(books, invoice)
    doc = rx.get_document(books, doc_id)
    assert (doc.party, doc.amount_cents, doc.number) == ("Northwind Labs", 214700, "INV-1001")
    assert doc.memo == "Consulting — October (+1 more)"
    assert books.account_balance(services) == 180000 and books.account_balance(sales) == 10000
    assert books.account_balance(books.role(inv.SALES_TAX_KEY).id) == 24700
    assert books.account_balance(books.role(rx.coa.ACCOUNTS_RECEIVABLE).id) == 214700
    assert books.check_balanced()
    # customer remembered for next time
    assert inv.customer(books, "northwind labs").address == "1 Main St\nHalifax NS"
    assert inv.new_invoice(books, "Northwind Labs").customer_email == "ap@northwind.test"
    assert inv.new_invoice(books).number == "INV-1002"
    # edit: drop a line → books follow
    again = inv.load_invoice(books, doc_id)
    assert len(again.lines) == 2 and again.customer_address.startswith("1 Main")
    again.lines = again.lines[:1]
    inv.save_invoice(books, again)
    assert rx.get_document(books, doc_id).amount_cents == 203400
    assert books.account_balance(sales) == 0 and books.check_balanced()
    html = inv.invoice_html(books, doc_id)
    assert "Consulting — October" in html and "Sales tax 13%" in html and "$2,034.00" in html


def test_validation(books):
    i = inv.new_invoice(books, "A")
    with pytest.raises(ValueError, match="at least one line"):
        inv.save_invoice(books, i)
    i.lines = [inv.Line("x", 0, 100, _acct(books, "4000"))]
    with pytest.raises(ValueError, match="quantity"):
        inv.save_invoice(books, i)
    i.lines = [inv.Line("x", 1, 100, books.account_by_code("1000").id)]
    with pytest.raises(ValueError, match="income account"):
        inv.save_invoice(books, i)
    i.lines = [inv.Line("x", 1, 100, _acct(books, "4000"))]
    inv.save_invoice(books, i)
    j = inv.new_invoice(books, "B")
    j.number = i.number
    j.lines = [inv.Line("y", 1, 100, _acct(books, "4000"))]
    with pytest.raises(ValueError, match="already used"):
        inv.save_invoice(books, j)


def test_payment_and_invoice_for_existing_deposit(books):
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [ParsedTransaction(date(2026, 10, 20), "E-TRANSFER FROM NORTHWIND", 113000)])
    tx = books.transactions()[0]
    draft = inv.deposit_invoice(books, tx.id)
    assert draft.issue_date == draft.due_date == "2026-10-20" and draft.lines[0].unit_cents == 113000
    draft.customer = "Northwind Labs"
    draft.lines = [inv.Line("Workshop", 1, 100000, _acct(books, "4100"), 13)]
    doc_id = inv.invoice_for_deposit(books, tx.id, draft)
    doc = rx.get_document(books, doc_id)
    assert doc.amount_cents == 113000 and doc.balance_cents == 0
    assert books.transaction(tx.id).category_id == books.role(rx.coa.ACCOUNTS_RECEIVABLE).id
    assert "PAID" in inv.invoice_html(books, doc_id)
    # an invoice larger than the deposit is refused, and nothing is left behind
    books.import_transactions(bank, [ParsedTransaction(date(2026, 10, 21), "DEPOSIT", 5000)])
    small = next(t for t in books.transactions() if t.amount_cents == 5000)
    d2 = inv.deposit_invoice(books, small.id)
    d2.customer = "X"
    d2.lines[0].unit_cents = 9000
    with pytest.raises(ValueError, match="more than the deposit"):
        inv.invoice_for_deposit(books, small.id, d2)
    assert len(rx.documents(books, "invoice", status="all", as_of=date(2030, 1, 1))) == 1


def test_plain_invoice_loads_as_one_line_and_delete_cleans_up(books):
    doc_id = rx.add_document(books, "invoice", "Old Client", "2026-09-01", "2026-10-01", 50000,
                             _acct(books, "4100"), number="INV-1001", memo="Retainer")
    old = inv.load_invoice(books, doc_id)
    assert [(ln.description, ln.unit_cents) for ln in old.lines] == [("Retainer", 50000)]
    inv.save_invoice(books, old)
    rx.delete_document(books, doc_id)
    assert books.conn.execute("SELECT COUNT(*) FROM invoice_lines").fetchone()[0] == 0


def test_settings_roundtrip(books):
    s = inv.get_settings(books)
    s.address, s.tax_label, s.default_tax_rate, s.default_terms_days = "9 Dock Rd", "HST", 13, 15
    inv.set_settings(books, s)
    s2 = inv.get_settings(books)
    assert (s2.address, s2.tax_label, s2.default_tax_rate, s2.default_terms_days) == ("9 Dock Rd", "HST", 13.0, 15)
    n = inv.new_invoice(books, issue=date(2026, 10, 1))
    assert n.due_date == "2026-10-16" and n.lines[0].tax_rate == 13
