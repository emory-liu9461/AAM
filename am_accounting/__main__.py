"""Launch the Aedalus Nexus desktop app: ``python -m am_accounting [company-file.amdb]``."""

from __future__ import annotations

import sys


def selftest(out_path: str) -> int:
    """Check a packaged build can read statements and write workbooks (used by the installer build)."""
    import io
    import tempfile
    from pathlib import Path

    lines = []
    try:
        from .books import Books
        from .importers import parse_statement_bytes
        from .reports import income_statement

        csv_data = b"Date,Description,Amount\n01/02/2026,STRIPE TRANSFER,100.00\n01/03/2026,ADOBE,-20.00\n"
        txs, _ = parse_statement_bytes(csv_data, "x.csv")
        lines.append(f"csv {len(txs)}")
        tmp = Path(tempfile.mkdtemp())
        from .pdf_to_excel import write_workbook

        write_workbook(txs, tmp / "t.xlsx", source_name="x.csv")
        txs2, _ = parse_statement_bytes((tmp / "t.xlsx").read_bytes(), "t.xlsx")
        lines.append(f"xlsx {len(txs2)}")
        import pdfplumber  # noqa: F401  - the PDF reader and its dependencies load

        from PySide6.QtGui import QPdfWriter, QTextDocument
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        writer = QPdfWriter(str(tmp / "r.pdf"))
        doc = QTextDocument()
        b = Books.open(":memory:")
        b.import_transactions(b.account_by_code("1000").id, txs)
        doc.setHtml(income_statement(b, "2026-01-01", "2026-01-31").to_html())
        doc.print_(writer)
        del writer
        pdf_bytes = (tmp / "r.pdf").read_bytes()
        txs3, _ = parse_statement_bytes(pdf_bytes, "r.pdf")  # PDF text extraction round trip
        lines.append(f"pdf {len(pdf_bytes) > 1000}")
        from .importers.pdf_importer import extract_text

        lines.append(f"qt pdf text {'Revenue' in extract_text(pdf_bytes)}")
        # OCR: draw a statement page as an image (with Pillow's built-in font, so it doesn't depend on
        # system fonts), save it as an image-only "scanned" PDF and read it back.
        from PIL import Image, ImageDraw, ImageFont

        from .importers import ocr as ocr_mod
        from .importers.ocr import ocr_available, ocr_pdf_text

        page = Image.new("RGB", (1700, 700), "white")
        draw = ImageDraw.Draw(page)
        font = ImageFont.load_default(size=40)
        for i, line in enumerate(["FIRST EXAMPLE BANK  Statement 2026", "01/05  MOBILE DEPOSIT   1,234.56",
                                  "01/09  OFFICE SUPPLIES   87.10"]):
            draw.text((80, 80 + i * 120), line, fill="black", font=font)
        page.save(tmp / "scan.pdf", "PDF", resolution=200)
        if not ocr_available():
            raise RuntimeError("OCR engine unavailable:\n" + ocr_mod.unavailable_reason)
        text = ocr_pdf_text((tmp / "scan.pdf").read_bytes())
        if "1,234.56" not in text.replace(" ", ""):
            raise RuntimeError(f"OCR self-test failed: {text[:300]!r}")
        lines.append("ocr ok")
        _ = (app, io, txs3)
        lines.append("OK")
        code = 0
    except Exception as e:  # noqa: BLE001
        import traceback

        lines.append("FAILED: " + "".join(traceback.format_exception(e)))
        code = 1
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--selftest":
        return selftest(sys.argv[2])
    from PySide6.QtWidgets import QApplication

    from .gui.common import STYLE
    from .gui.main_window import MainWindow

    if sys.platform == "win32":  # show our icon (not Python's) in the Windows taskbar
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("AMAccounting.Desktop")
        except Exception:  # noqa: BLE001
            pass
    from pathlib import Path

    from PySide6.QtGui import QIcon

    app = QApplication(sys.argv)
    icon = Path(__file__).parent / "assets" / "app.png"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    app.setApplicationName("AM Accounting")      # (kept: where its settings and AppData live)
    app.setOrganizationName("AM Accounting")
    from . import APP_NAME

    app.setApplicationDisplayName(APP_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    from .gui import theme

    theme.apply()
    path = sys.argv[1] if len(sys.argv) > 1 else None
    window = MainWindow(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
