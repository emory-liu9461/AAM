from datetime import date

import pytest

from am_accounting import reports
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture
def books(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [
        ParsedTransaction(date(2026, 4, 30), "MONTHLY PLAN FEE", -500),
        ParsedTransaction(date(2026, 4, 30), "SERVICE CHARGE", -4375),
        ParsedTransaction(date(2026, 4, 12), "PAYPROP AP", 514559),
        ParsedTransaction(date(2026, 4, 2), "SUBWAY", -1276)])
    fees = b.account_by_code("6050").id
    for t in b.transactions():
        if t.description in ("MONTHLY PLAN FEE", "SERVICE CHARGE"):
            b.set_category(t.id, fees, source="manual")
        if t.description == "PAYPROP AP":
            b.set_category(t.id, b.account_by_code("4100").id, source="manual")
    yield b
    b.close()


def test_search_finds_categories(books):
    found = {t.description for t in books.transactions(search="Bank & Merchant")}
    assert found == {"MONTHLY PLAN FEE", "SERVICE CHARGE"}
    assert {t.description for t in books.transactions(search="bank and merchant fees")} == found
    assert {t.description for t in books.transactions(search="6050")} == found
    assert {t.description for t in books.transactions(search="uncat")} == {"SUBWAY"}


def test_statement_lines_expand_into_entries(books):
    r = reports.income_statement(books, "2026-04-01", "2026-04-30")
    fees_line = next(l for l in r.lines if l.label == "Bank & Merchant Fees")
    assert fees_line.account_id == books.account_by_code("6050").id and fees_line.values == [4875]
    entries = reports.account_entries(books, fees_line.account_id, r.start, r.end)
    assert sorted((e.description, e.amount_cents) for e in entries) == [("MONTHLY PLAN FEE", 500),
                                                                         ("SERVICE CHARGE", 4375)]
    assert sum(e.amount_cents for e in entries) == fees_line.values[0]
    closed = r.to_html(links=True)
    assert f'href="acct:{fees_line.account_id}"' in closed and "MONTHLY PLAN FEE" not in closed
    opened = r.to_html(links=True, expanded={fees_line.account_id},
                       details=lambda a: reports.account_entries(books, a, r.start, r.end))
    assert "MONTHLY PLAN FEE" in opened and "SERVICE CHARGE" in opened and "▾" in opened
    assert "href=" not in r.to_html()  # exports have no links
    # balance sheet: the bank line expands into every transaction up to the date, in the bank's direction
    bs = reports.balance_sheet(books, "2026-04-30")
    bank_line = next(l for l in bs.lines if l.label == "Business Checking")
    es = reports.account_entries(books, bank_line.account_id, bs.start, bs.end)
    assert sum(e.amount_cents for e in es) == bank_line.values[0] and len(es) == 4
    tb = reports.trial_balance(books, "2026-04-30")
    assert any(l.account_id == bank_line.account_id for l in tb.lines)


def test_statements_by_period(books, tmp_path):
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date(2026, 5, 3), "PAYPROP AP", 300000), ParsedTransaction(date(2026, 5, 31), "SERVICE CHARGE", -2000)])
    t = next(x for x in books.transactions() if x.date == "2026-05-03")
    books.set_category(t.id, books.account_by_code("4100").id, source="manual")
    t2 = next(x for x in books.transactions() if x.date == "2026-05-31")
    books.set_category(t2.id, books.account_by_code("6050").id, source="manual")
    r = reports.build_by_period(books, "Income Statement (P&L)", "2026-04-01", "2026-05-31", "month")
    assert r.columns == ["Apr 2026", "May 2026", "Total"]
    fees = next(l for l in r.lines if l.label == "Bank & Merchant Fees")
    assert fees.values == [4875, 2000, 6875]
    rev = next(l for l in r.lines if l.label == "Service Revenue")
    assert rev.values == [514559, 300000, 814559]
    html = r.to_html(links=True, expanded={fees.account_id},
                     details=lambda a: reports.account_entries(books, a, r.start, r.end))
    assert "Apr 2026" in html and "SERVICE CHARGE" in html
    bs = reports.build_by_period(books, "Balance Sheet", "2026-04-01", "2026-05-31", "month")
    assert bs.columns == ["Apr 2026", "May 2026"]
    bank = next(l for l in bs.lines if l.label == "Business Checking")
    assert bank.values[1] - bank.values[0] == 300000 - 2000
    assert all(bs.checks.values())
    q = reports.build_by_period(books, "Statement of Cash Flows", "2026-01-01", "2026-12-31", "quarter")
    assert q.columns == ["Q1 2026", "Q2 2026", "Q3 2026", "Q4 2026", "Total"]
    from am_accounting.workspace import report_to_xlsx
    wk = reports.build_by_period(books, "Income Statement (P&L)", "2026-01-01", "2026-12-31", "week")
    report_to_xlsx(wk, tmp_path / "weeks.xlsx")   # more than 26 columns
    assert len(wk.columns) == 54
