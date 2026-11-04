from datetime import date

import pytest

from am_accounting.importers import load_statement, parse_statement_bytes
from am_accounting.importers.pdf_importer import parse_statement_text


def test_chase_style_csv(fixtures):
    txs, warnings = load_statement(fixtures / "chase_style.csv")
    assert len(txs) == 10
    assert not warnings
    assert txs[0].date == date(2026, 1, 2)
    assert txs[0].amount_cents == 250000
    assert txs[1].amount_cents == -180000
    assert "GUSTO" in txs[1].description


def test_split_debit_credit_columns_with_preamble(fixtures):
    txs, _ = load_statement(fixtures / "split_columns.csv")
    assert [t.amount_cents for t in txs] == [-8500, 125050, -4510]
    assert txs[0].memo == "Phone bill"


def test_headerless_csv():
    data = b"01/05/2026,COFFEE SHOP,-4.50,100.00\n01/06/2026,DEPOSIT,200.00,300.00\n"
    txs, _ = parse_statement_bytes(data, "x.csv")
    assert [t.amount_cents for t in txs] == [-450, 20000]
    assert txs[0].description == "COFFEE SHOP"


def test_semicolon_csv_with_type_column():
    data = "Date;Description;Amount;Type\n2026-03-01;Rent;1200.00;DEBIT\n2026-03-02;Sale;99.00;CREDIT\n".encode()
    txs, _ = parse_statement_bytes(data, "x.csv")
    assert [t.amount_cents for t in txs] == [-120000, 9900]


def test_ofx(fixtures):
    txs, _ = load_statement(fixtures / "sample.ofx")
    assert [t.amount_cents for t in txs] == [-12999, 48000, -30000]
    assert txs[0].date == date(2026, 1, 8)
    assert "COMCAST" in txs[0].description
    assert txs[2].description.startswith("CHECK 1042")


STATEMENT_TEXT = """\
FIRST EXAMPLE BANK
Statement Period: January 1, 2026 through January 31, 2026
Account Summary
Beginning Balance $5,000.00
Deposits and Additions
01/02 STRIPE TRANSFER ACME 2,500.00
01/20 CLIENT PAYMENT INV 1043 3,000.00
Total Deposits and Additions $5,500.00
Electronic Withdrawals
01/03 GUSTO PAYROLL 1,800.00
01/05 ADOBE CREATIVE CLOUD 54.99
Fees
01/10 MONTHLY SERVICE FEE 15.00
Ending Balance $8,630.01
"""


def test_pdf_text_sections():
    txs, warnings = parse_statement_text(STATEMENT_TEXT)
    assert not warnings
    assert [(t.date, t.amount_cents) for t in txs] == [
        (date(2026, 1, 2), 250000), (date(2026, 1, 20), 300000),
        (date(2026, 1, 3), -180000), (date(2026, 1, 5), -5499), (date(2026, 1, 10), -1500),
    ]
    assert txs[0].description == "STRIPE TRANSFER ACME"


RUNNING_BALANCE_TEXT = """\
Statement period 12/01/2025 - 01/15/2026
Date Description Amount Balance
Beginning balance 1,000.00
12/30 CARD PURCHASE AMAZON 25.00 975.00
12/31 MOBILE DEPOSIT 500.00 1,475.00
01/02 ATM WITHDRAWAL (60.00) 1,415.00
01/03 INTEREST PAID 0.12 1,415.12
"""


def test_pdf_text_running_balance_and_year_rollover():
    txs, _ = parse_statement_text(RUNNING_BALANCE_TEXT)
    assert [(t.date, t.amount_cents) for t in txs] == [
        (date(2025, 12, 30), -2500), (date(2025, 12, 31), 50000),
        (date(2026, 1, 2), -6000), (date(2026, 1, 3), 12),
    ]


def test_real_pdf_roundtrip(tmp_path):
    reportlab = pytest.importorskip("reportlab")
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    path = tmp_path / "statement.pdf"
    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for line in STATEMENT_TEXT.splitlines():
        c.drawString(50, y, line)
        y -= 16
    c.save()
    txs, _ = load_statement(path)
    assert sorted(t.amount_cents for t in txs) == sorted([250000, 300000, -180000, -5499, -1500])
