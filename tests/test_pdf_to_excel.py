from datetime import date, datetime

import pytest

from am_accounting.pdf_to_excel import convert_pdf_to_excel
from am_accounting.watcher import FAILED_DIR, PROCESSED_DIR, PdfFolderWatcher, main

from test_importers import STATEMENT_TEXT

pytest.importorskip("reportlab")


def make_pdf(path, text=STATEMENT_TEXT):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for line in text.splitlines():
        c.drawString(50, y, line)
        y -= 16
    c.save()
    return path


def test_convert_pdf_to_excel(tmp_path):
    from openpyxl import load_workbook

    pdf = make_pdf(tmp_path / "jan.pdf")
    result = convert_pdf_to_excel(pdf)
    assert result.xlsx_path == tmp_path / "jan.xlsx"
    assert result.count == 5
    wb = load_workbook(result.xlsx_path)
    assert wb.sheetnames == ["Transactions", "Summary", "Extracted Text"]
    ws = wb["Transactions"]
    assert [c.value for c in ws[1]] == ["Date", "Description", "Money In", "Money Out", "Amount"]
    first = [c.value for c in ws[2]]
    assert first[0] == datetime(2026, 1, 2) and first[1] == "STRIPE TRANSFER ACME"
    assert first[2] == 2500 and first[3] is None and first[4] == 2500
    assert ws.cell(row=7, column=5).value == "=SUM(E2:E6)"
    summary = {r[0].value: r[1].value for r in wb["Summary"].iter_rows() if r[0].value}
    assert summary["Transactions"] == 5
    assert summary["Money in"] == 5500 and round(summary["Money out"], 2) == 1869.99
    # A second conversion never overwrites the first.
    assert convert_pdf_to_excel(pdf).xlsx_path.name == "jan (2).xlsx"


def test_watcher_converts_once_file_is_stable(tmp_path):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    inbox.mkdir()
    events = []
    w = PdfFolderWatcher(inbox, out, on_event=events.append)
    make_pdf(inbox / "jan.pdf")
    (inbox / "notes.txt").write_text("ignore me")
    (inbox / "broken.pdf").write_bytes(b"%PDF-1.4 not really a pdf")

    assert w.scan_once() == []  # first sighting: wait to make sure the file isn't still being written
    w.scan_once()
    by_name = {e.pdf_name: e for e in events}
    assert by_name["jan.pdf"].ok and by_name["jan.pdf"].count == 5
    assert (out / "jan.xlsx").exists()
    assert (inbox / PROCESSED_DIR / "jan.pdf").exists()
    assert not by_name["broken.pdf"].ok
    assert (inbox / FAILED_DIR / "broken.pdf").exists()
    assert (inbox / FAILED_DIR / "broken.error.txt").exists()
    assert (inbox / "notes.txt").exists()
    assert w.scan_once() == []  # nothing left to do


def test_watcher_background_thread(tmp_path):
    import threading

    inbox, out = tmp_path / "inbox", tmp_path / "out"
    inbox.mkdir()  # don't race the watcher thread creating it
    done = threading.Event()
    w = PdfFolderWatcher(inbox, out, interval=0.05, on_event=lambda e: done.set())
    w.start()
    try:
        make_pdf(tmp_path / "feb.pdf").rename(inbox / "feb.pdf")
        assert done.wait(10)
    finally:
        w.stop()
    assert not w.running
    assert (out / "feb.xlsx").exists()


def test_cli_once(tmp_path, capsys):
    inbox, out = tmp_path / "inbox", tmp_path / "out"
    inbox.mkdir()
    make_pdf(inbox / "mar.pdf")
    assert main(["--inbox", str(inbox), "--out", str(out), "--once"]) == 0
    assert "mar.pdf" in capsys.readouterr().out
    assert (out / "mar.xlsx").exists()


def test_combined_workbook(tmp_path):
    from openpyxl import load_workbook

    from am_accounting.books import ParsedTransaction as P
    from am_accounting.pdf_to_excel import COMBINED_NAME, add_to_combined_workbook

    wb_path = tmp_path / COMBINED_NAME
    add_to_combined_workbook(wb_path, "Chase Jan 2026.pdf", [P(date(2026, 1, 3), "A", 1000),
                                                             P(date(2026, 1, 5), "B", -250)], account="Checking")
    add_to_combined_workbook(wb_path, "Savings Jan 2026.pdf", [P(date(2026, 1, 4), "INTEREST", 12)],
                             account="Savings")
    add_to_combined_workbook(wb_path, "Chase Feb 2026.pdf", [P(date(2026, 2, 3), "C", 500)], account="Checking")
    # re-import January checking with a corrected file: its sheet is replaced, not duplicated
    add_to_combined_workbook(wb_path, "Chase Jan 2026.pdf", [P(date(2026, 1, 3), "A", 1000)], account="Checking")
    wb = load_workbook(wb_path)
    assert wb.sheetnames == ["All Transactions", "Summary", "Chase Jan 2026", "Chase Feb 2026", "Savings Jan 2026"]
    rows = [r for r in wb["All Transactions"].iter_rows(min_row=2, values_only=True)]
    assert [(r[0].date(), r[1], r[4], r[5]) for r in rows[:-1]] == [
        (date(2026, 1, 3), "A", 10, "Checking"), (date(2026, 1, 4), "INTEREST", 0.12, "Savings"),
        (date(2026, 2, 3), "C", 5, "Checking")]
    assert rows[-1][1] == "Total" and rows[-1][4] == "=SUM(E2:E4)"
    summary = [r for r in wb["Summary"].iter_rows(min_row=2, values_only=True)]
    assert [(r[1], r[2], r[5]) for r in summary] == [("Chase Jan 2026.pdf", "Checking", 1),
                                                     ("Chase Feb 2026.pdf", "Checking", 1),
                                                     ("Savings Jan 2026.pdf", "Savings", 1)]


def test_import_dialog_adds_pdf_to_workbook(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from am_accounting.books import Books
    from am_accounting.gui.import_dialog import ImportDialog
    from am_accounting.pdf_to_excel import COMBINED_NAME

    app = QApplication.instance() or QApplication([])
    src = tmp_path / "Downloads"
    src.mkdir()
    pdf = make_pdf(src / "January.pdf")
    books = Books.open(":memory:")
    dlg = ImportDialog(books, str(pdf))
    assert dlg.to_excel.isVisibleTo(dlg) and dlg.to_excel.isChecked()
    dlg.do_import()
    assert dlg.excel_path == src / COMBINED_NAME and (src / COMBINED_NAME).exists()
    _ = app


def test_keeps_adding_and_merges_copies_made_while_open_in_excel(tmp_path, monkeypatch):
    from openpyxl import load_workbook
    from openpyxl.workbook.workbook import Workbook

    from am_accounting.books import ParsedTransaction as P
    from am_accounting.pdf_to_excel import COMBINED_NAME, add_to_combined_workbook

    main = tmp_path / COMBINED_NAME
    for m in (1, 2):  # January and February
        add_to_combined_workbook(main, f"2026-0{m}.pdf", [P(date(2026, m, 5), f"M{m}", 100 * m)], account="Chk")

    # March is imported while the workbook is open in Excel (locked): a copy is written instead
    real_save = Workbook.save

    def locked(self, filename):
        if str(filename) == str(main):
            raise PermissionError("locked")
        return real_save(self, filename)

    monkeypatch.setattr(Workbook, "save", locked)
    copy = add_to_combined_workbook(main, "2026-03.pdf", [P(date(2026, 3, 5), "M3", 300)], account="Chk")
    assert copy.name == "Statements (converted from PDF) (2).xlsx"
    monkeypatch.setattr(Workbook, "save", real_save)

    # April, with Excel closed: March is merged back and the copy removed
    assert add_to_combined_workbook(main, "2026-04.pdf", [P(date(2026, 4, 5), "M4", 400)], account="Chk") == main
    assert not copy.exists()
    wb = load_workbook(main)
    assert wb.sheetnames == ["All Transactions", "Summary", "2026-01", "2026-02", "2026-03", "2026-04"]
    descs = [r[1] for r in wb["All Transactions"].iter_rows(min_row=2, values_only=True)]
    assert descs == ["M1", "M2", "M3", "M4", "Total"]


def test_already_imported_pdf_can_still_be_added(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from am_accounting.books import Books
    from am_accounting.gui.import_dialog import ImportDialog
    from am_accounting.pdf_to_excel import COMBINED_NAME

    app = QApplication.instance() or QApplication([])
    pdf = make_pdf(tmp_path / "January.pdf")
    books = Books.open(":memory:")
    first = ImportDialog(books, str(pdf))
    first.to_excel.setChecked(False)
    first.do_import()
    assert not (tmp_path / COMBINED_NAME).exists()
    again = ImportDialog(books, str(pdf))
    again.to_excel.setChecked(True)
    assert again.import_btn.isEnabled() and again.import_btn.text() == "Add to Excel Workbook"
    again.do_import()
    assert again.result_counts == (0, 5) and (tmp_path / COMBINED_NAME).exists()
    again.settings.setValue("import/pdf_to_excel", True)
    _ = app
