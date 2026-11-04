"""Scanned (image-only) PDF statements are read with OCR."""

from datetime import date

import pytest

from am_accounting.importers import parse_statement_bytes
from am_accounting.importers.pdf_importer import parse_statement_text
from am_accounting.importers.respace import respace

ROWS = [("01/02", "STRIPE TRANSFER ACME", "2,500.00", "7,500.00"),
        ("01/03", "GUSTO PAYROLL", "1,800.00", "5,700.00"),
        ("01/05", "ADOBE CREATIVE CLOUD", "54.99", "5,645.01"),
        ("01/10", "MONTHLY SERVICE FEE", "15.00", "5,630.01"),
        ("01/20", "CLIENT PAYMENT INV 1043", "3,000.00", "8,630.01")]


def scanned_statement(tmp_path):
    """A statement printed to PDF, rendered to an image, slightly blurred and tilted, saved image-only."""
    pytest.importorskip("reportlab")
    pdfium = pytest.importorskip("pypdfium2")
    from PIL import ImageFilter
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    src = tmp_path / "text.pdf"
    c = canvas.Canvas(str(src), pagesize=letter)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 740, "FIRST EXAMPLE BANK")
    c.setFont("Helvetica", 10)
    c.drawString(50, 720, "Statement Period: January 1, 2026 through January 31, 2026")
    c.drawString(50, 700, "Beginning Balance")
    c.drawRightString(560, 700, "$5,000.00")
    y = 650
    for d, desc, amt, bal in ROWS:
        c.drawString(50, y, d)
        c.drawString(110, y, desc)
        c.drawRightString(460, y, amt)
        c.drawRightString(560, y, bal)
        y -= 18
    c.save()
    img = pdfium.PdfDocument(str(src))[0].render(scale=200 / 72).to_pil().convert("L")
    img = img.rotate(0.5, fillcolor=255).filter(ImageFilter.GaussianBlur(0.5))
    out = tmp_path / "scanned.pdf"
    img.save(out, "PDF", resolution=200)
    return out


def test_scanned_pdf_is_read_with_ocr(tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    pdf = scanned_statement(tmp_path)
    from am_accounting.importers.pdf_importer import extract_text, text_is_usable

    assert not text_is_usable(extract_text(pdf.read_bytes()))  # no text layer: needs OCR
    txs, warnings = parse_statement_bytes(pdf.read_bytes(), pdf.name)
    assert any("OCR" in w for w in warnings)
    assert [(t.date, t.amount_cents) for t in txs] == [
        (date(2026, 1, 2), 250000), (date(2026, 1, 3), -180000), (date(2026, 1, 5), -5499),
        (date(2026, 1, 10), -1500), (date(2026, 1, 20), 300000)]
    assert [t.description for t in txs] == [r[1] for r in ROWS]  # spaces restored


def test_respace():
    assert respace("01/02 STRIPETRANSFERACME 2,500.00") == "01/02 STRIPE TRANSFER ACME 2,500.00"
    assert respace("CLIENTPAYMENTINV1043") == "CLIENT PAYMENT INV 1043"
    assert respace("ONLINETRANSFERTOSAV4421") == "ONLINE TRANSFER TO SAV 4421"
    for unchanged in ("MISSISSIPPI", "NORTHSTARFABRICATION", "MAINTENANCE", "Beginning Balance"):
        assert respace(unchanged) == unchanged


def test_missing_year_no_longer_drops_transactions():
    text = "Deposits\n03/14 MOBILE DEPOSIT 120.00\nWithdrawals\n03/15 RENT 50.00\n"
    txs, warnings = parse_statement_text(text, fallback_year_end=date(2026, 6, 30))
    assert [(t.date, t.amount_cents) for t in txs] == [(date(2026, 3, 14), 12000), (date(2026, 3, 15), -5000)]
    assert "Could not find the statement year" in warnings[0]
    # months after the fallback end belong to the previous year
    txs, _ = parse_statement_text("Deposits\n11/02 DEPOSIT 5.00\n", fallback_year_end=date(2026, 6, 30))
    assert txs[0].date == date(2025, 11, 2)


def test_period_with_year_only_at_end():
    text = "Statement period January 1 - January 31, 2026\nDeposits\n01/05 SALE 10.00\n"
    txs, warnings = parse_statement_text(text)
    assert txs[0].date == date(2026, 1, 5) and not warnings


def test_bad_and_protected_pdfs_explain_themselves():
    from am_accounting.importers.pdf_importer import PdfReadError

    with pytest.raises(PdfReadError):
        parse_statement_bytes(b"%PDF-1.4 not really a pdf", "x.pdf")
