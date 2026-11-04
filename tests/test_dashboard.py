from datetime import date

from am_accounting import reports
from am_accounting.books import ParsedTransaction
from am_accounting.dashboard import build_dashboard
from am_accounting.demo import populate_sample


def test_dashboard_figures(books):
    chk = books.account_by_code("1000").id
    books.set_opening_balance(chk, "2026-01-01", 100000)
    books.import_transactions(chk, [
        ParsedTransaction(date(2026, 1, 10), "OLD SALE", 50000),            # outside 120 days
        ParsedTransaction(date(2026, 3, 1), "SALE", 30000),                  # in 120 only
        ParsedTransaction(date(2026, 4, 1), "SALE", 20000),                  # in 90 and 120
        ParsedTransaction(date(2026, 5, 20), "GUSTO PAYROLL", -10000),       # in all
        ParsedTransaction(date(2026, 6, 1), "CLIENT PAYMENT", 40000),        # in all (last day)
    ])
    d = build_dashboard(books, date(2026, 6, 1))
    p30, p90, p120 = d.income
    assert (p30.days, p30.start) == (30, date(2026, 5, 3))
    assert (p30.revenue, p30.expenses, p30.net) == (40000, 10000, 30000)
    assert (p90.revenue, p90.expenses) == (60000, 10000)
    assert (p120.revenue, p120.expenses) == (90000, 10000)
    assert p30.margin == 0.75
    assert d.cash_total == 100000 + 50000 + 30000 + 20000 - 10000 + 40000
    assert dict(d.cash_accounts)["Business Checking"] == d.cash_total
    assert d.total_assets == d.cash_total and d.total_liabilities == 0
    assert d.balanced
    assert d.data_through == "2026-06-01" and d.last_import
    assert d.uncategorized == 5 and d.unreviewed == 5


def test_dashboard_matches_reports_on_sample(books):
    end = date(2026, 10, 31)
    populate_sample(books, end)
    d = build_dashboard(books, end)
    bs = reports.balance_sheet(books, end.isoformat())
    assert d.balanced and all(bs.checks.values())
    assert d.total_assets == bs.value("TOTAL ASSETS")
    assert d.total_liabilities == bs.value("Total Liabilities")
    assert d.total_equity == bs.value("Total Equity")
    for p in d.income:
        inc = reports.income_statement(books, p.start.isoformat(), end.isoformat())
        assert p.net == inc.value("Net Income")
    assert d.cash_total > 0 and len(d.cash_accounts) == 2


def test_dashboard_empty_books(books):
    d = build_dashboard(books, date(2026, 6, 1))
    assert d.last_import is None and d.data_through is None
    assert all(p.revenue == p.expenses == 0 and p.margin is None for p in d.income)
    assert d.cash_total == 0 and d.balanced


def test_balance_sheet_breakdown(books):
    from am_accounting import receivables as rx

    bank = books.account_by_code("1000").id
    books.set_opening_balance(bank, "2026-01-01", 500000)
    books.set_opening_balance(books.account_by_code("1500").id, "2026-01-01", 200000)        # equipment
    books.set_opening_balance(books.account_by_code("1590").id, "2026-01-01", -20000)        # accumulated depr.
    books.set_opening_balance(books.account_by_code("1300").id, "2026-01-01", 30000)         # prepaid
    books.set_opening_balance(books.account_by_code("2100").id, "2026-01-01", 45000)         # credit card
    books.set_opening_balance(books.account_by_code("2500").id, "2026-01-01", 150000)        # loan
    rx.add_document(books, "invoice", "A", "2026-01-05", "2026-02-04", 70000, books.account_by_code("4100").id)
    rx.add_document(books, "bill", "B", "2026-01-06", "2026-02-05", 15000, books.account_by_code("6300").id)
    d = build_dashboard(books, date(2026, 1, 31))
    assert d.cash_total == 500000
    assert d.short_term_assets == 30000 + 70000
    assert d.long_term_assets == 180000
    assert d.short_term_debt == 45000 + 15000
    assert d.long_term_debt == 150000
    assert d.total_assets == d.cash_total + d.short_term_assets + d.long_term_assets
    assert d.total_liabilities == d.short_term_debt + d.long_term_debt
    assert d.balanced
