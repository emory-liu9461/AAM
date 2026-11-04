from datetime import date

import pytest

from am_accounting import receivables as rx
from am_accounting import reports
from am_accounting.books import Books, ParsedTransaction
from am_accounting.dashboard import build_dashboard


def _setup(books):
    bank = books.account_by_code("1000").id
    service = books.account_by_code("4100").id
    supplies = books.account_by_code("6300").id
    return bank, service, supplies


def test_invoice_posts_to_receivables_and_revenue(books):
    bank, service, _ = _setup(books)
    inv = rx.add_document(books, "invoice", "Acme Co", "2026-01-05", "2026-02-04", 100000, service,
                          number="INV-1001")
    ar = books.account_by_code("1200").id
    assert books.account_balance(ar) == 100000
    assert reports.income_statement(books, "2026-01-01", "2026-01-31").value("Total Revenue") == 100000
    assert books.check_balanced()
    d = rx.get_document(books, inv)
    assert d.balance_cents == 100000 and d.status(date(2026, 1, 10)) == "Open"
    assert d.status(date(2026, 2, 14)) == "Overdue 10d"
    assert rx.next_number(books, "invoice") == "INV-1002"


def test_payment_clears_receivable_without_double_counting_income(books):
    bank, service, _ = _setup(books)
    inv = rx.add_document(books, "invoice", "Acme Co", "2026-01-05", "2026-02-04", 100000, service,
                          number="INV-1001")
    books.import_transactions(bank, [ParsedTransaction(date(2026, 1, 20), "ACME CO PAYMENT INV-1001", 60000),
                                     ParsedTransaction(date(2026, 1, 28), "MOBILE DEPOSIT", 40000),
                                     ParsedTransaction(date(2026, 1, 29), "UNRELATED", 12345)])
    sugg = rx.suggest_payments(books, inv)
    assert sugg[0][0].description == "ACME CO PAYMENT INV-1001" and "number" in sugg[0][2]
    tx1 = sugg[0][0]
    assert rx.record_payment(books, inv, tx1.id) == 60000
    assert rx.get_document(books, inv).status(date(2026, 1, 21)) == "Partly paid"
    tx2 = next(t for t in books.transactions() if t.description == "MOBILE DEPOSIT")
    rx.record_payment(books, inv, tx2.id)
    d = rx.get_document(books, inv)
    assert d.balance_cents == 0 and d.status() == "Paid"
    ar = books.account_by_code("1200").id
    assert books.account_balance(ar) == 0
    inc = reports.income_statement(books, "2026-01-01", "2026-01-31")
    assert inc.value("Total Revenue") == 100000          # not 200,000
    assert books.transaction(tx1.id).category_id == ar and books.transaction(tx1.id).reviewed
    # balances as of a date only count payments made by then
    assert rx.documents(books, "invoice", status="open", as_of=date(2026, 1, 25))[0].balance_cents == 40000
    with pytest.raises(ValueError):
        rx.record_payment(books, inv, tx1.id)  # nothing left
    with pytest.raises(ValueError):
        rx.record_payment(books, inv, next(t.id for t in books.transactions() if t.description == "UNRELATED"))


def test_bill_and_payment(books):
    bank, _, supplies = _setup(books)
    bill = rx.add_document(books, "bill", "Paper Co", "2026-01-02", "2026-01-17", 25000, supplies)
    ap = books.account_by_code("2000").id
    assert books.account_balance(ap) == 25000
    books.import_transactions(bank, [ParsedTransaction(date(2026, 1, 15), "PAPER CO", -25000),
                                     ParsedTransaction(date(2026, 1, 16), "REFUND", 25000)])
    deposit = next(t for t in books.transactions() if t.description == "REFUND")
    with pytest.raises(ValueError):
        rx.record_payment(books, bill, deposit.id)  # money in can't pay a bill
    pay = next(t for t in books.transactions() if t.description == "PAPER CO")
    rx.record_payment(books, bill, pay.id)
    assert books.account_balance(ap) == 0
    assert reports.income_statement(books, "2026-01-01", "2026-01-31").value("Total Operating Expenses") == 25000
    assert all(reports.balance_sheet(books, "2026-01-31").checks.values())


def test_unlink_on_recategorize_and_delete(books):
    bank, service, _ = _setup(books)
    inv = rx.add_document(books, "invoice", "Acme", "2026-01-05", "2026-02-04", 5000, service)
    books.import_transactions(bank, [ParsedTransaction(date(2026, 1, 20), "ACME", 5000)])
    tx = books.transactions()[0]
    rx.record_payment(books, inv, tx.id)
    books.set_category(tx.id, service)  # user recategorizes the deposit as plain income
    assert rx.get_document(books, inv).paid_cents == 0
    rx.record_payment(books, inv, tx.id)
    rx.delete_document(books, inv)
    assert books.transaction(tx.id).category_id is None
    assert books.account_balance(books.account_by_code("1200").id) == 0
    assert books.check_balanced()


def test_update_and_validation(books):
    _, service, _ = _setup(books)
    with pytest.raises(ValueError):
        rx.add_document(books, "invoice", "", "2026-01-05", "2026-02-04", 5000, service)
    with pytest.raises(ValueError):
        rx.add_document(books, "invoice", "A", "2026-01-05", "2026-01-01", 5000, service)
    with pytest.raises(ValueError):
        rx.add_document(books, "invoice", "A", "2026-01-05", "2026-02-04", 0, service)
    with pytest.raises(ValueError):
        rx.add_document(books, "invoice", "A", "2026-01-05", "2026-02-04", 10, books.account_by_code("1000").id)
    inv = rx.add_document(books, "invoice", "A", "2026-01-05", "2026-02-04", 5000, service)
    rx.update_document(books, inv, party="A2", issue_date="2026-01-06", due_date="2026-01-20", amount_cents=7000,
                       category_id=books.account_by_code("4000").id)
    assert books.account_balance(books.account_by_code("1200").id) == 7000
    assert books.account_balance(books.account_by_code("4100").id) == 0
    assert books.account_balance(books.account_by_code("4000").id) == 7000


def test_aging_and_dashboard(books):
    _, service, supplies = _setup(books)
    for due, amt in [("2026-03-10", 100), ("2026-02-20", 200), ("2026-01-20", 300), ("2025-12-20", 400),
                     ("2025-10-01", 500)]:
        rx.add_document(books, "invoice", f"C{amt}", "2025-09-01", due, amt, service)
    books.set_opening_balance(books.account_by_code("1200").id, "2025-08-31", 1000)
    s = rx.aging(books, "invoice", date(2026, 3, 1))
    assert dict(s.buckets) == {"Current": 100, "1–30 days": 200, "31–60 days": 300, "61–90 days": 400,
                               "Over 90 days": 500}
    assert (s.open_total, s.overdue_total, s.overdue_count) == (1500, 1400, 4)
    assert s.ledger_balance == 2500 and s.not_itemized == 1000
    assert s.top_parties[0] == ("C500", 500)
    d = build_dashboard(books, date(2026, 3, 1))
    assert d.receivables.ledger_balance == 2500 and d.payables.ledger_balance == 0


def test_merge_and_delete_account_with_documents(books):
    _, service, _ = _setup(books)
    rx.add_document(books, "invoice", "A", "2026-01-05", "2026-02-04", 5000, service)
    with pytest.raises(ValueError):
        books.delete_account(service)
    books.merge_account(service, books.account_by_code("4000").id)
    assert rx.documents(books, "invoice", status="all")[0].category_id == books.account_by_code("4000").id


def test_minimal_company_gets_ar_when_needed():
    b = Books.open(":memory:", template="minimal")
    assert rx.aging(b, "invoice").ledger_balance == 0
    assert all(a.code != "1200" for a in b.accounts())  # dashboard doesn't create it
    rx.add_document(b, "invoice", "A", "2026-01-05", "2026-02-04", 5000, b.account_by_code("4999").id)
    assert b.account_by_code("1200").name == "Accounts Receivable"
