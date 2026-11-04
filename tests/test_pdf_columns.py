"""Column-laid-out PDF statements (e.g. TD Canada Trust business chequing)."""

from datetime import date

import pytest

from am_accounting.importers import parse_statement_bytes

pytest.importorskip("reportlab")


def _canvas(path):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    return canvas.Canvas(str(path), pagesize=letter)


def td_style_statement(path):
    """Invented data in TD's layout: DESCRIPTION | CHEQUE/DEBIT | DEPOSIT/CREDIT | DATE | BALANCE."""
    c = _canvas(path)

    def margin_code(text):  # sideways mail-sorting code in the left margin
        c.saveState()
        c.translate(26, 300)
        c.rotate(90)
        c.drawString(0, 0, text)
        c.restoreState()

    def header(page):
        c.setFont("Helvetica", 8)
        c.drawString(65, 760, "Statement of Account   Statement From - To")
        c.drawString(330, 750, "APR 30/24 - MAY 31/24")
        c.drawString(65, 740, f"BUSINESS CHEQUING   Page {page} of 3")
        for x, label in [(104, "DESCRIPTION"), (212, "CHEQUE/DEBIT"), (309, "DEPOSIT/CREDIT"), (403, "DATE"),
                         (468, "BALANCE")]:
            c.drawString(x, 700, label)

    def row(y, desc, debit="", credit="", day="", bal=""):
        c.drawString(65, y, desc)
        if debit:
            c.drawRightString(298, y, debit)
        if credit:
            c.drawRightString(400, y, credit)  # touches the date column: extracted as "1,410.50MAY06"
        if day:
            c.drawString(401, y, day)
        if bal:
            c.drawRightString(541, y, bal)

    header(1)
    margin_code("123400")
    y = 680
    for r in [("BALANCE FORWARD", "", "", "APR30", "41.20"),
              ("COFFEE HOUSE", "6.35", "", "MAY02", "34.85"),
              ("CLIENT AP", "", "1,410.50", "MAY06", "1,445.35"),
              ("SEND E-TFR ***m4D", "520.00", "", "MAY07", ""),
              ("SEND E-TFR FEE", "1.50", "", "MAY07", "923.85"),
              ("CHQ#00141-9000000002", "2,200.00", "", "MAY10", "1,276.15OD"),
              ("TFR-FR 7340218", "", "3,500.00", "MAY14", "2,223.85")]:
        row(y, *r)
        y -= 9
    c.drawString(65, y - 20, "No. Amount   2 CHQS ENCLOSED NEXT STATEMENT DATE IS JUN 28/24")
    c.drawString(65, y - 30, "Credits 2 4,910.50   MONTHLY MIN. BAL. $1,276.15OD")
    c.showPage()
    header(2)
    y = 680
    for r in [("BALANCE FORWARD", "", "", "MAY14", "2,223.85"),
              ("MONTHLY PLAN FEE", "5.00", "", "MAY31", ""),
              ("OVERDRAFT INTEREST", "9.10", "", "MAY31", "2,209.75")]:
        row(y, *r)
        y -= 9
    c.showPage()
    # page 3: cheque images, no table header — must not be read as transactions
    c.drawString(65, 700, "CHEQUE#00141 $2,200.00 CHEQUE#00141")
    c.save()
    return path


def test_td_style_columns(tmp_path):
    pdf = td_style_statement(tmp_path / "td.pdf")
    txs, warnings = parse_statement_bytes(pdf.read_bytes(), pdf.name)
    assert [(t.date, t.amount_cents, t.description) for t in txs] == [
        (date(2024, 5, 2), -635, "COFFEE HOUSE"),
        (date(2024, 5, 6), 141050, "CLIENT AP"),
        (date(2024, 5, 7), -52000, "SEND E-TFR ***m4D"),
        (date(2024, 5, 7), -150, "SEND E-TFR FEE"),
        (date(2024, 5, 10), -220000, "CHQ#00141-9000000002"),
        (date(2024, 5, 14), 350000, "TFR-FR 7340218"),
        (date(2024, 5, 31), -500, "MONTHLY PLAN FEE"),
        (date(2024, 5, 31), -910, "OVERDRAFT INTEREST"),
    ]
    assert any(w.startswith("Checked:") for w in warnings)  # balances reconcile


def test_date_first_with_date_only_on_first_line_of_day(tmp_path):
    c = _canvas(tmp_path / "bank.pdf")
    c.setFont("Helvetica", 9)
    c.drawString(50, 760, "Statement period: March 1, 2026 to March 31, 2026")
    for x, label in [(50, "Date"), (110, "Description"), (330, "Withdrawals"), (420, "Deposits"), (500, "Balance")]:
        c.drawString(x, 720, label)
    y = 700
    for d, desc, w, dep, bal in [("", "Opening Balance", "", "", "1,000.00"),
                                 ("Mar 3", "Payroll Deposit", "", "2,000.00", ""),
                                 ("", "Hydro One", "120.45", "", "2,879.55"),
                                 ("Mar 9", "Rent", "1,500.00", "", "1,379.55")]:
        if d:
            c.drawString(50, y, d)
        c.drawString(110, y, desc)
        if w:
            c.drawRightString(390, y, w)
        if dep:
            c.drawRightString(470, y, dep)
        if bal:
            c.drawRightString(560, y, bal)
        y -= 14
    c.save()
    data = (tmp_path / "bank.pdf").read_bytes()
    txs, warnings = parse_statement_bytes(data, "bank.pdf")
    assert [(t.date, t.amount_cents, t.description) for t in txs] == [
        (date(2026, 3, 3), 200000, "Payroll Deposit"),
        (date(2026, 3, 3), -12045, "Hydro One"),
        (date(2026, 3, 9), -150000, "Rent")]
    assert any(w.startswith("Checked:") for w in warnings)
