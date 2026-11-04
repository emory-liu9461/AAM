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


def test_invoice_and_bill_in_chosen_accounts(books):
    related = rx.add_control_account(books, "invoice", "Accounts Receivable – Related Party")
    assert related.parent_id == rx.control_account(books, "invoice").id and related.code == "1201"
    assert related.id in [a.id for a in rx.control_choices(books, "invoice")]
    cc = books.add_account("2150", "Credit Card Payable", "liability", "current")
    assert cc.id in [a.id for a in rx.control_choices(books, "bill")]
    income, expense = books.account_by_code("4100").id, books.account_by_code("6400").id
    d1 = rx.add_document(books, "invoice", "Sister Co", "2026-10-01", "2026-10-31", 50000, income,
                         control_id=related.id)
    d2 = rx.add_document(books, "bill", "Landlord", "2026-10-01", "2026-10-31", 20000, expense, control_id=cc.id)
    assert books.account_balance(related.id) == 50000 and books.account_balance(cc.id) == 20000
    assert books.account_balance(rx.control_account(books, "invoice").id) == 0
    # remembered per customer / vendor
    assert rx.default_control(books, "invoice", "sister co") == related.id
    assert rx.default_control(books, "invoice", "Someone New") == rx.control_account(books, "invoice").id
    # AR/AP summaries include these accounts
    assert rx.aging(books, "invoice").ledger_balance == 50000 and rx.aging(books, "bill").ledger_balance == 20000
    # wrong kinds are refused
    with pytest.raises(ValueError, match="asset"):
        rx.add_document(books, "invoice", "X", "2026-10-01", "2026-10-31", 100, income, control_id=cc.id)
    with pytest.raises(ValueError, match="bank"):
        rx.add_document(books, "bill", "X", "2026-10-01", "2026-10-31", 100, expense,
                        control_id=books.account_by_code("1000").id)
    assert d2


def test_changing_account_moves_payments_too(books):
    income = books.account_by_code("4100").id
    main = rx.control_account(books, "invoice")
    other = rx.add_control_account(books, "invoice", "AR – Shareholder")
    doc = rx.add_document(books, "invoice", "Pat", "2026-10-01", "2026-10-31", 30000, income)
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 5), "DEPOSIT", 10000)])
    tx = books.transactions()[0]
    rx.record_payment(books, doc, tx.id)
    d = rx.get_document(books, doc)
    rx.update_document(books, doc, party=d.party, issue_date=d.issue_date, due_date=d.due_date,
                       amount_cents=d.amount_cents, category_id=d.category_id, control_id=other.id)
    assert books.transaction(tx.id).category_id == other.id
    assert rx.get_document(books, doc).paid_cents == 10000  # payment kept
    assert books.account_balance(other.id) == 20000 and books.account_balance(main.id) == 0
    assert books.check_balanced()


def test_generated_invoice_uses_customers_account(books):
    related = rx.add_control_account(books, "invoice", "AR – Related Party")
    i = inv.new_invoice(books, "Sister Co")
    i.lines = [inv.Line("Management fee", 1, 100000, books.account_by_code("4100").id, 13)]
    i.control_id = related.id
    doc_id = inv.save_invoice(books, i)
    assert rx.get_document(books, doc_id).control_id == related.id
    assert books.account_balance(related.id) == 113000
    j = inv.new_invoice(books, "Sister Co")
    j.lines = [inv.Line("Management fee", 1, 50000, books.account_by_code("4100").id)]
    assert rx.get_document(books, inv.save_invoice(books, j)).control_id == related.id  # follows the customer
    assert inv.load_invoice(books, doc_id).control_id == related.id
