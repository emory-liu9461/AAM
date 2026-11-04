from datetime import date

from am_accounting import reports
from am_accounting.books import ParsedTransaction, merchant_key
from am_accounting.categorizer import Categorizer, auto_categorize
from am_accounting.importers import load_statement


def _import(books, fixtures, name="chase_style.csv", bank="1000"):
    txs, _ = load_statement(fixtures / name)
    return books.import_transactions(books.account_by_code(bank).id, txs, filename=name)


def test_import_dedupes(books, fixtures):
    assert _import(books, fixtures) == (10, 0)
    # Re-import: everything is a duplicate, including the two identical Starbucks rows.
    assert _import(books, fixtures) == (0, 10)
    assert len(books.transactions()) == 10
    assert books.check_balanced()


def test_merchant_key():
    assert merchant_key("POS PURCHASE 03/14 STARBUCKS #1234 SEATTLE WA") == "STARBUCKS SEATTLE WA"
    assert merchant_key("STARBUCKS STORE 01234 SEATTLE WA") == "STARBUCKS STORE SEATTLE"


def test_auto_categorize_keywords(books, fixtures):
    _import(books, fixtures)
    stats = auto_categorize(books)
    assert stats["keyword"] == 9 and stats["unmatched"] == 1
    cats = {t.description: books.account(t.category_id).code if t.category_id else None
            for t in books.transactions()}
    assert cats["GUSTO PAYROLL 8812"] == "6750"
    assert cats["ADOBE *CREATIVE CLD 408-536-6000 CA"] == "6500"
    assert cats["STARBUCKS STORE 01234 SEATTLE WA"] == "6250"
    assert cats["MONTHLY SERVICE FEE"] == "6050"
    assert cats["ONLINE TRANSFER TO SAV ...4421"] == "1090"
    assert cats["ACME LANDLORD LLC RENT JAN"] == "6400"
    assert cats["CLIENT PAYMENT INV 1043"] == "4000"
    assert cats["MYSTERY VENDOR 99"] is None


def test_rules_and_learning(books, fixtures):
    _import(books, fixtures)
    supplies = books.account_by_code("6300").id
    mystery = next(t for t in books.transactions() if t.description.startswith("MYSTERY"))
    # Manual categorization teaches the categorizer about this merchant.
    books.set_category(mystery.id, supplies, source="manual")
    new = ParsedTransaction(date(2026, 2, 3), "MYSTERY VENDOR 12", -1000)
    books.import_transactions(books.account_by_code("1000").id, [new])
    auto_categorize(books)
    t = next(t for t in books.transactions() if t.description == "MYSTERY VENDOR 12")
    assert t.category_id == supplies and t.category_source == "learned"

    # Rules beat keywords; manual categories are never overwritten.
    meals = books.account_by_code("6250").id
    office = books.account_by_code("6300").id
    books.add_rule("starbucks", office, direction="out")
    auto_categorize(books, overwrite_auto=True)
    sb = [t for t in books.transactions() if "STARBUCKS" in t.description]
    assert all(t.category_id == office and t.category_source == "rule" for t in sb)
    assert books.transaction(mystery.id).category_source == "manual"
    assert meals != office


def test_reports_balance(books, fixtures):
    checking = books.account_by_code("1000").id
    savings = books.account_by_code("1010").id
    books.set_opening_balance(checking, "2025-12-31", 500000)
    _import(books, fixtures)
    auto_categorize(books)
    # Savings side of the transfer.
    books.import_transactions(savings, [ParsedTransaction(date(2026, 1, 12), "TRANSFER FROM CHK 1234", 100000)])
    auto_categorize(books)
    assert books.transactions(bank_account_id=savings)[0].category_id == books.account_by_code("1090").id

    assert books.check_balanced()
    assert books.account_balance(checking, as_of="2026-01-31") == 637511
    assert books.account_balance(books.account_by_code("1090").id) == 0

    inc = reports.income_statement(books, "2026-01-01", "2026-01-31")
    assert inc.value("Total Revenue") == 550000
    # 1800 payroll + 54.99 + 12.90 + 15 + 1200 rent
    assert inc.value("Total Operating Expenses") == 180000 + 5499 + 1290 + 1500 + 120000
    assert inc.value("Total Other Expenses") == 4200  # uncategorized mystery vendor
    net = 550000 - (180000 + 5499 + 1290 + 1500 + 120000) - 4200
    assert inc.value("Net Income") == net

    bs = reports.balance_sheet(books, "2026-01-31")
    assert all(bs.checks.values())
    assert bs.value("TOTAL ASSETS") == 637511 + 100000
    assert bs.value("Net Income") == net

    cf = reports.cash_flow(books, "2026-01-01", "2026-01-31")
    assert all(cf.checks.values()), cf.to_csv()
    assert cf.value("Cash at Beginning of Period") == 500000
    assert cf.value("Cash at End of Period") == 737511
    assert cf.value("Net Cash from Operating Activities") == net

    tb = reports.trial_balance(books, "2026-01-31")
    assert all(tb.checks.values())
    assert "<table" in inc.to_html() and "Net Income" in inc.to_csv()


def test_opening_balance_in_period_cash_flow(books):
    checking = books.account_by_code("1000").id
    books.set_opening_balance(checking, "2026-01-01", 100000)
    assert books.opening_balance(checking) == ("2026-01-01", 100000)
    books.set_opening_balance(checking, "2026-01-01", 120000)  # replaces, not adds
    assert books.account_balance(checking) == 120000
    cf = reports.cash_flow(books, "2026-01-01", "2026-01-31")
    assert all(cf.checks.values())
    assert cf.value("Cash at End of Period") == 120000


def test_categorizer_never_uses_own_bank(books):
    checking = books.account_by_code("1000").id
    books.add_rule("transfer", checking)
    books.import_transactions(checking, [ParsedTransaction(date(2026, 1, 1), "transfer to savings", -100)])
    tx = books.transactions()[0]
    s = Categorizer(books).suggest(tx)
    assert s is None or s.account_id != checking
