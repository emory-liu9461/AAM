"""Headless smoke test: builds every screen and exercises the main flows."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture
def app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_main_window_flows(app, fixtures, tmp_path):
    from am_accounting.gui.import_dialog import ImportDialog
    from am_accounting.gui.main_window import MainWindow
    from am_accounting.gui.reports_tab import write_pdf

    from am_accounting.companies import CompanyRegistry

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(tmp_path / "test.amdb"), registry=reg)
    w.show()
    dlg = ImportDialog(w.books, str(fixtures / "chase_style.csv"), w)
    assert len(dlg.parsed) == 10 and dlg.import_btn.isEnabled()
    dlg.do_import()
    assert dlg.result_counts == (10, 0)
    w.on_accounts_changed()
    assert w.tx_tab.model.rowCount() == 10
    # Re-opening the same file shows everything as already imported.
    assert not ImportDialog(w.books, str(fixtures / "chase_style.csv"), w).import_btn.isEnabled()

    for name in ("Income Statement (P&L)", "Balance Sheet", "Statement of Cash Flows", "Trial Balance"):
        w.reports_tab.kind.setCurrentText(name)
        w.reports_tab.period.setCurrentText("All time")
        w.reports_tab.run()
        assert all(w.reports_tab.current.checks.values()), name
    write_pdf(w.reports_tab.current.to_html(), str(tmp_path / "r.pdf"))
    assert (tmp_path / "r.pdf").stat().st_size > 1000
    w.close()


def test_pdf_watch_service_and_dialog(app, tmp_path):
    from PySide6.QtCore import QSettings

    from am_accounting.gui.pdf_excel import PdfWatchDialog, PdfWatchService

    svc = PdfWatchService()
    svc.settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    dlg = PdfWatchDialog(svc)
    dlg.enabled.setChecked(True)
    dlg.inbox.setText(str(tmp_path / "in"))
    dlg.out.setText(str(tmp_path / "out"))
    dlg.save()
    assert svc.running and "Running" in dlg.status.text()
    dlg.enabled.setChecked(False)
    dlg.save()
    assert not svc.running
    dlg.reject()


def test_multiple_companies(app, tmp_path):
    from am_accounting.books import CompanyProfile, ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.companies import CompaniesDialog, CompanyProfileDialog
    from am_accounting.gui.main_window import SWITCH_MANAGE, SWITCH_NEW, MainWindow
    from datetime import date

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    a = reg.create(CompanyProfile("Alpha Bakery"))
    b = reg.create(CompanyProfile("Beta Consulting", fiscal_year_start_month=7))
    w = MainWindow(str(a), registry=reg)
    w.books.import_transactions(w.books.account_by_code("1000").id,
                                [ParsedTransaction(date(2026, 1, 5), "FLOUR SUPPLIER", -5000)])
    w.on_accounts_changed()
    assert w.tx_tab.model.rowCount() == 1

    names = [w.switcher.itemText(i).strip() for i in range(w.switcher.count())]
    assert "Alpha Bakery" in names and "Beta Consulting" in names and names[0] == "OPERATING COMPANIES"
    assert w.switcher.findData(SWITCH_NEW) > 0 and w.switcher.findData(SWITCH_MANAGE) > 0

    w.switch_books(str(b))
    assert w.books.company_name == "Beta Consulting"
    assert w.windowTitle().startswith("Beta Consulting")
    assert w.tx_tab.model.rowCount() == 0  # books are separate
    assert w.reports_tab.start.date().month() == 7  # fiscal year-to-date starts in July
    assert w.switcher.currentText().strip() == "Beta Consulting"

    # New company dialog copying setup from Beta
    dlg = CompanyProfileDialog(reg, parent=w)
    dlg.name.setText("Gamma LLC")
    dlg.setup.setCurrentIndex(dlg.setup.findData(f"copy:{b}"))
    dlg.save()
    assert dlg.created_path and dlg.created_path.exists()
    w.switch_books(str(dlg.created_path))
    assert w.books.company_name == "Gamma LLC"

    cd = CompaniesDialog(reg, w.books.path, w)
    assert cd.table.rowCount() == 4      # a heading (Operating companies) + the three companies
    cd.reject()
    w.close()


def test_fiscal_period_presets():
    from datetime import date

    from am_accounting.gui.reports_tab import period_presets

    p = period_presets(date(2026, 3, 15), fy_month=7)
    assert p["Year to date"] == (date(2025, 7, 1), date(2026, 3, 15))
    assert p["This quarter"] == (date(2026, 1, 1), date(2026, 3, 15))
    assert p["Last quarter"] == (date(2025, 10, 1), date(2025, 12, 31))
    assert p["Last fiscal year"] == (date(2024, 7, 1), date(2025, 6, 30))
    cal = period_presets(date(2026, 3, 15))
    assert cal["Last year"] == (date(2025, 1, 1), date(2025, 12, 31))


def test_dashboard_tab(app, tmp_path):
    from datetime import date

    from am_accounting.companies import CompanyRegistry
    from am_accounting.demo import SAMPLE_NAME
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(tmp_path / "start.amdb"), registry=reg)
    assert w.tabs.widget(0) is w.dashboard_tab
    w.create_sample_company()
    assert w.books.company_name == SAMPLE_NAME
    dash = w.dashboard_tab
    latest = max(t.date for t in w.books.transactions())          # the sample runs up to today
    assert dash.data.data_through == latest and (date.today() - date.fromisoformat(latest)).days <= 3
    assert [p.days for p in dash.data.income] == [30, 90, 120]
    assert "Last updated" in dash.updated.text()
    w.open_report_from_dashboard("Income Statement (P&L)|30")
    assert w.tabs.currentWidget() is w.reports_tab
    assert w.reports_tab.current.value("Net Income") == dash.data.income[0].net
    w.review_dock.hide()
    w.review_from_dashboard()
    assert w.review_dock.isVisibleTo(w)
    w.close()


def test_inbox_dialog_and_service(app, tmp_path):
    import shutil
    import time
    from pathlib import Path

    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.inbox_dialog import InboxDialog
    from am_accounting.gui.main_window import MainWindow
    from am_accounting.inbox import get_config

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(tmp_path / "Companies" / "A.amdb"), registry=reg)
    w.inbox_service.watcher.interval = 0.05
    dlg = InboxDialog(w.books, w.inbox_service, w)
    inbox = tmp_path / "OneDrive" / "Inbox"
    dlg.enabled.setChecked(True)
    dlg.folder.setText(str(inbox))
    assert "Business Checking" in dlg.layout_hint.text()
    dlg.save()
    assert get_config(w.books).enabled
    shutil.copy(Path(__file__).parent / "fixtures" / "chase_style.csv", inbox / "Business Checking" / "jan.csv")
    for _ in range(100):
        app.processEvents()
        if w.inbox_service.history:
            break
        time.sleep(0.05)
    assert w.inbox_service.history and w.inbox_service.history[0][1].imported == 10
    app.processEvents()
    assert len(w.books.transactions()) == 10
    w.close()


def test_accounts_tab_customization(app, tmp_path):
    from PySide6.QtCore import Qt

    from am_accounting.books import Books
    from am_accounting.gui.accounts import AccountDialog, AccountsTab, DeleteAccountDialog, LabelsDialog

    books = Books.open(":memory:")
    tab = AccountsTab(books)
    assert tab.tree.topLevelItemCount() == 5
    rev = books.account_by_code("4000")
    dlg = AccountDialog(books, parent_account=rev)
    dlg.name.setText("Subscriptions")
    dlg.code.setText("4005")
    dlg.save()
    assert books.account_by_code("4005").parent_id == rev.id
    tab.refresh()
    income = tab.tree.topLevelItem(3)
    names = []
    for i in range(income.childCount()):
        item = income.child(i)
        names.append(item.text(0))
        if item.text(0) == "Sales Revenue":
            assert item.child(0).text(0) == "Subscriptions"
    assert "Sales Revenue" in names

    unc = books.account_by_code("6999")
    edit = AccountDialog(books, unc)
    assert not edit.type.isEnabled() and "app uses" in edit.note.text()

    spare = books.add_account("6123", "Spare", "expense")
    DeleteAccountDialog(books, spare).save()
    assert all(a.code != "6123" for a in books.accounts())

    labels = LabelsDialog(books)
    labels.edits["equity"].setText("Shareholders' Equity")
    labels.save()
    assert books.labels()["equity"] == "Shareholders' Equity"


def test_invoices_and_bills_tab(app, tmp_path):
    from datetime import date

    from am_accounting import receivables as rx
    from am_accounting.books import ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.documents_tab import DocumentDialog, PaymentDialog
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(tmp_path / "Companies" / "A.amdb"), registry=reg)
    dlg = DocumentDialog(w.books, "invoice", parent=w)
    dlg.party.setCurrentText("Acme Co")
    dlg.amount.setValue(1234.56)
    assert dlg.number.text() == "INV-1001"
    dlg.terms.setCurrentIndex(dlg.terms.findData(15))
    assert dlg.issue.date().daysTo(dlg.due.date()) == 15
    dlg.save()
    doc = rx.documents(w.books, "invoice")[0]
    assert doc.amount_cents == 123456

    w.books.import_transactions(w.books.account_by_code("1000").id,
                                [ParsedTransaction(date.today(), "ACME CO ACH", 123456)])
    pay = PaymentDialog(w.books, doc.id, w)
    assert pay.table.rowCount() == 1 and pay.apply_btn.isEnabled()
    pay.apply()
    assert rx.get_document(w.books, doc.id).balance_cents == 0
    pay.accept()

    w.open_documents("bill")
    assert w.tabs.currentWidget() is w.docs_tab and w.docs_tab.kind == "bill"
    w.dashboard_tab.refresh()
    assert "ACCOUNTS RECEIVABLE & PAYABLE" in w.dashboard_tab.arap_card.kicker.text()
    w.close()


def test_review_panel(app, tmp_path):
    from datetime import date

    from am_accounting.books import ParsedTransaction
    from am_accounting.categorizer import auto_categorize
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(tmp_path / "Companies" / "A.amdb"), registry=reg)
    b = w.books
    b.import_transactions(b.account_by_code("1000").id, [
        ParsedTransaction(date(2026, 1, 3), "ZELLE TO J MARTINEZ 4411", -35000),
        ParsedTransaction(date(2026, 1, 10), "ZELLE TO J MARTINEZ 9902", -60000),
        ParsedTransaction(date(2026, 1, 4), "CHECK 1042", -42500),
        ParsedTransaction(date(2026, 1, 5), "ONLINE TRANSFER TO SAV 4421", -100000)])
    auto_categorize(b)
    p = w.review_panel
    p.refresh()
    assert p.list.count() == 4 and p.count.text() == "4"
    # select a Zelle payment and answer with a quick button, applying to the similar one and making a rule
    row = next(i for i, it in enumerate(p.items) if it.tx.description.startswith("ZELLE"))
    p.list.setCurrentRow(row)
    assert p.similar.isVisibleTo(p) and p.similar.isChecked()
    p.memo.setText("Design work")
    p.make_rule.setChecked(True)
    contractor = b.account_by_code("6100").id
    p._save(contractor)
    assert p.list.count() == 2
    assert all(t.category_id == contractor for t in b.transactions() if t.description.startswith("ZELLE"))
    assert any(r.pattern == "zelle to j martinez" for r in b.rules())
    # checks can't make rules
    row = next(i for i, it in enumerate(p.items) if it.kind == "Check")
    p.list.setCurrentRow(row)
    assert not p.make_rule.isEnabled() and p.badge.text() == "CHECK #1042"
    # confirm the transfer guess
    row = next(i for i, it in enumerate(p.items) if it.kind == "Transfer")
    p.list.setCurrentRow(row)
    assert p.confirm_btn.isVisibleTo(p)
    p._confirm()
    assert p.list.count() == 1
    w.close()


def test_match_dialog_and_invoice_folder(app, tmp_path):
    from datetime import date

    from am_accounting import doc_folder as df
    from am_accounting import receivables as rx
    from am_accounting.books import ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui import doc_files as files
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(tmp_path / "Companies" / "A.amdb"), registry=reg)
    b = w.books
    svc = b.account_by_code("4100").id
    inv1 = rx.add_document(b, "invoice", "Lakeside Clinic", "2026-01-06", "2026-02-05", 375000, svc, number="INV-1")
    rx.add_document(b, "invoice", "Summit", "2026-01-08", "2026-02-07", 250000, svc, number="INV-2")
    rx.add_document(b, "invoice", "Riverbend", "2026-01-08", "2026-02-07", 250000, svc, number="INV-3")
    b.import_transactions(b.account_by_code("1000").id, [
        ParsedTransaction(date(2026, 1, 21), "DEPOSIT LAKESIDE CLINIC", 375000),
        ParsedTransaction(date(2026, 1, 23), "MOBILE DEPOSIT", 250000)])

    # folder settings: choose a folder -> PDFs are created for existing invoices
    fs = files.FolderSettingsDialog(b, w)
    fs.folder.setText(str(tmp_path / "OneDrive" / "Invoices & Bills"))
    assert "Invoices/Open/" in fs.example.text()
    fs.save()
    root = tmp_path / "OneDrive" / "Invoices & Bills"
    pdfs = sorted(p.name for p in (root / "Invoices" / "Open").iterdir())
    assert pdfs == ["2026-01-06 INV-1 Lakeside Clinic $3,750.00.pdf", "2026-01-08 INV-2 Summit $2,500.00.pdf",
                    "2026-01-08 INV-3 Riverbend $2,500.00.pdf"]
    assert (root / "Invoices" / "Open" / pdfs[0]).read_bytes().startswith(b"%PDF")

    md = files.MatchDialog(b, w)
    assert md.table.rowCount() == 2
    checked = [md.table.item(r, 0).checkState() for r in range(2)]
    assert sorted(c.value for c in checked) == [0, 2]  # one confident (ticked), one possible (unticked)
    md.apply()
    assert md.matched == 1 and rx.get_document(b, inv1).balance_cents == 0
    assert (root / "Invoices" / "Paid" / "2026" / "2026-01-06 INV-1 Lakeside Clinic $3,750.00.pdf").exists()

    # drop folder -> attach to a bill
    bill = rx.add_document(b, "bill", "Harbor Supply Co", "2026-01-03", "2026-02-02", 212500,
                           b.account_by_code("5000").id, number="HS-1")
    (root / df.DROP_DIR / "scan.pdf").write_bytes(b"%PDF-1.4")
    w.docs_tab.refresh()
    assert w.docs_tab.unfiled_btn.text() == "File 1 New File…"
    ud = files.UnfiledDialog(b, w)
    ud.target.setCurrentIndex(ud.target.findData(bill))
    ud.attach()
    assert (root / "Bills" / "Open" / "2026-01-03 HS-1 Harbor Supply Co $2,125.00.pdf").exists()
    fd = files.FilesDialog(b, bill, w)
    assert fd.list.count() == 1
    w.close()


def test_dark_mode_keeps_everything_legible(app, tmp_path, monkeypatch):
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QPalette

    from am_accounting.gui import common, theme

    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    monkeypatch.setattr(theme, "_settings", lambda: store)
    try:
        theme.set_mode("dark")
        assert theme.is_dark()
        assert app.palette().color(QPalette.ColorRole.Window).lightness() < 80
        assert common.POSITIVE.name() == theme.DARK["green"]  # meaning colours switch with the theme
        from am_accounting.gui.dashboard_tab import css

        assert theme.DARK["attention_bg"] in css()
        from am_accounting.books import Books
        from am_accounting.gui.reports_tab import ReportsTab

        tab = ReportsTab(Books.open(":memory:"))
        assert "background: #ffffff" in tab.view.styleSheet()  # statements on white paper
        theme.set_mode("light")
        assert not theme.is_dark() and common.POSITIVE.name() == theme.LIGHT["green"]
    finally:
        theme.set_mode("light")


def test_company_folder_dialog_and_autosave(app, tmp_path):
    from datetime import date

    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.workspace_dialog import AutoSaveService, WorkspaceDialog

    books = Books.open(tmp_path / "Co.amdb")
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 1, 5), "SALE", 1000)])
    dlg = WorkspaceDialog(books, first_time=True)
    root = tmp_path / "OneDrive" / "Co"
    dlg.root.setText(str(root))
    dlg.freq.setCurrentIndex(dlg.freq.findData("weekly"))
    assert "Week" in dlg.example.text()
    assert dlg.save()
    assert (root / ws.BANK_DIR / "Business Checking").is_dir() and (root / ws.DOCS_DIR).is_dir()
    results = AutoSaveService(lambda: books).check()
    assert len(results) == 1 and results[0].period.end < date.today()
    saved = list((root / ws.REPORTS_DIR).rglob("*.pdf"))
    assert saved and all(p.read_bytes().startswith(b"%PDF") for p in saved)
    assert list((root / ws.BACKUP_DIR).glob("*.amdb"))
    books.close()


def test_invoice_dump_window(app, tmp_path):
    from datetime import date

    from am_accounting import invoice_dump as dump
    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.dump_dialog import InvoiceDumpDialog, MatchByHandDialog
    from test_invoice_dump import HARBOR, invoice_pdf

    books = Books.open(tmp_path / "Co.amdb")
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")))
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date(2026, 10, 25), "HARBOR SUPPLY CO PAYMENT", -212500),
        ParsedTransaction(date(2026, 10, 27), "WIRE OUT 1182", -30000),
        ParsedTransaction(date(2026, 10, 28), "WIRE OUT 1183", -30000)])
    root = dump.dump_root(books)
    invoice_pdf(root / "h.pdf", HARBOR)
    invoice_pdf(root / "c.pdf", ["J Martinez", "Invoice 77", "Date: 2026-10-20", "Total 300.00"])
    dlg = InvoiceDumpDialog(books)
    dlg.check_now()
    assert dlg.table.rowCount() == 2 and "1 recognized (expense not recorded yet) · 1 waiting" in dlg.summary.text()
    waiting = next(i for i in dlg.items if i.status == "unmatched")
    pick = MatchByHandDialog(books, waiting)
    assert pick.table.rowCount() >= 2
    pick.table.selectRow(0)
    pick.accept_choice()
    dlg.refresh()
    assert "2 recognized (expense not recorded yet) · 0 waiting" in dlg.summary.text()
    # an unmatched invoice: Future Payable or Not Related
    from am_accounting import receivables as rx
    from am_accounting.gui.dump_dialog import FuturePayableDialog

    invoice_pdf(root / "k.pdf", ["Keystone Printing", "Invoice No. KP-9", "Date: 2026-10-20", "Amount Due 89.00"])
    invoice_pdf(root / "x.pdf", ["Someone Else Ltd", "Invoice 12", "Date: 2026-10-21", "Total 41.00"])
    dlg.check_now()
    k = next(i for i in dlg.items if i.party == "Keystone Printing")
    dlg.table.selectRow(dlg.items.index(k))
    assert dlg.buttons["Future Payable…"].isEnabled() and not dlg.buttons["Undo"].isEnabled()
    fp = FuturePayableDialog(books, k)
    fp.save()
    assert rx.documents(books, "bill")[0].party == "Keystone Printing"
    x = next(i for i in dump.items(books, "unmatched") if i.party == "Someone Else Ltd")
    dump.mark_unrelated(books, x.id)
    dlg.refresh()
    assert "1 future payable · 1 not related" in dlg.summary.text()
    assert {i.path.parent.name for i in dlg.items if i.status in ("payable", "unrelated")} == \
        {dump.PAYABLES_DIR, dump.UNRELATED_DIR}
    books.close()


def test_process_all_bills_button(app, tmp_path):
    from datetime import date

    from am_accounting import invoice_dump as dump
    from am_accounting import receivables as rx
    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.dashboard_tab import DashboardTab
    from am_accounting.gui.dump_dialog import process_all_bills
    from test_invoice_dump import HARBOR, invoice_pdf

    books = Books.open(tmp_path / "Co.amdb")
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")))
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 25), "HARBOR SUPPLY CO PAYMENT", -212500)])
    root = dump.dump_root(books)
    invoice_pdf(root / "a.pdf", HARBOR)
    invoice_pdf(root / "b.pdf", ["Keystone Printing", "Invoice No. KP-1", "Date: 2026-10-20", "Amount Due 89.00"])
    dash = DashboardTab(books)
    assert dash.bills_btn.text() == "Process All Bills (2 new)"
    s = process_all_bills(books, quiet=True, record_unpaid=True)
    assert (s.read, s.matched, len(s.bills)) == (2, 1, 1)
    assert rx.documents(books, "bill")[0].party == "Keystone Printing"
    dash.refresh()
    assert dash.bills_btn.text() == "Process All Bills"
    books.close()


def test_invoice_generator(app, tmp_path):
    from datetime import date

    from am_accounting import doc_folder as df
    from am_accounting import invoicing as inv
    from am_accounting import receivables as rx
    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.invoice_generator import InvoiceGeneratorDialog
    from am_accounting.gui.review_panel import ReviewPanel

    books = Books.open(tmp_path / "Co.amdb")
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")))
    dlg = InvoiceGeneratorDialog(books)
    assert dlg.number.text() == "INV-1001" and dlg.table.rowCount() == 1
    dlg.customer.setCurrentText("Northwind Labs")
    dlg.email.setText("ap@northwind.test")
    dlg.table.item(0, 0).setText("Consulting")
    dlg.table.cellWidget(0, 1).setValue(10)
    dlg.table.cellWidget(0, 2).setValue(150)
    dlg.table.cellWidget(0, 4).setValue(13)
    dlg._add_line()
    dlg.table.item(1, 0).setText("Travel")
    dlg.table.cellWidget(1, 2).setValue(80)
    assert dlg.table.cellWidget(1, 4).value() == 13  # carries the tax rate down
    dlg.table.cellWidget(1, 4).setValue(0)
    assert "Total $1,775.00" in dlg.totals.text()
    dlg.save_and(None)
    doc = rx.get_document(books, dlg.saved_id)
    assert (doc.party, doc.amount_cents) == ("Northwind Labs", 177500)
    pdfs = [f for f in df.files_for(books, doc.id) if f.generated]
    assert pdfs and pdfs[0].path(df.get_config(books)).exists()
    again = InvoiceGeneratorDialog(books, doc.id)
    assert again.table.rowCount() == 2 and again.email.text() == "ap@northwind.test"
    # a deposit with no invoice behind it → Create Invoice for This Deposit
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 20), "E-TRANSFER FROM ACME", 50000)])
    panel = ReviewPanel(books)
    panel.show()
    assert panel.invoice_btn.isVisible()
    tx = books.transactions()[0]
    dep = InvoiceGeneratorDialog(books, deposit_tx_id=tx.id)
    dep.customer.setCurrentText("Acme")
    dep.table.item(0, 0).setText("Website update")
    dep.save_and(None)
    assert rx.get_document(books, dep.saved_id).balance_cents == 0
    assert inv.load_invoice(books, dep.saved_id).issue_date == "2026-10-20"
    panel.close()
    books.close()


def test_reports_tab_saves_trial_balance(app, tmp_path, monkeypatch):
    from datetime import date

    from PySide6.QtGui import QDesktopServices

    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.reports_tab import ReportsTab

    monkeypatch.setattr(QDesktopServices, "openUrl", lambda *a: True)
    books = Books.open(tmp_path / "Co.amdb")
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 7), "SALE", 25000)])
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")))
    tab = ReportsTab(books)
    tab.show()
    assert not tab.tb_btn.isVisible()
    tab.kind.setCurrentText("Trial Balance")
    assert tab.tb_btn.isVisible()
    path = tab.save_trial_balance()
    assert path.exists() and path.parent.parent.name == "Trial Balance" and path.suffix == ".xlsx"
    tab.close()
    books.close()


def test_import_dialog_with_balance_check_message(app, tmp_path, monkeypatch):
    """Statements read by the column reader carry a 'Checked: …' line; showing it must not crash."""
    from datetime import date

    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui import import_dialog

    txs = [ParsedTransaction(date(2024, 4, 1), "STREETSVILLE", -2000),
           ParsedTransaction(date(2024, 4, 2), "E-TRANSFER ***r2T", 250000)]
    monkeypatch.setattr(import_dialog, "parse_statement_bytes", lambda *a, **k: (
        txs, ["Checked: opening balance 868.92 plus these transactions equals the statement's closing balance "
              "3,348.92."]))
    stmt = tmp_path / "td.pdf"
    stmt.write_bytes(b"%PDF-1.4")
    books = Books.open(tmp_path / "Co.amdb")
    dlg = import_dialog.ImportDialog(books, str(stmt))
    assert "✓ Checked" in dlg.info.text() and dlg.import_btn.isEnabled()
    dlg.do_import()
    assert dlg.result_counts == (2, 0) and len(books.transactions()) == 2
    books.close()


def test_etransfer_window_and_control_account_picker(app, tmp_path):
    from datetime import date

    from am_accounting import etransfer as et
    from am_accounting import receivables as rx
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.control_account import ControlAccountPicker
    from am_accounting.gui.documents_tab import DocumentDialog
    from am_accounting.gui.etransfer_dialog import ETransferDialog, PickTransactionDialog
    from am_accounting.gui.review_panel import ReviewPanel

    books = Books.open(tmp_path / "Co.amdb")
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date(2024, 4, 4), "SEND E-TFR ***k9q", -300000),
        ParsedTransaction(date(2024, 4, 15), "SEND E-TFR ***Baz", -300000)])
    panel = ReviewPanel(books)
    panel.show()
    assert panel.etransfer_btn.isVisible()
    dlg = ETransferDialog(books)
    et.import_text(books, "Sent to Jane Smith\n$3,000.00\nApr 4, 2024\nReference: CA9XyZwk9q\n"
                          "Sent to Mike Chen\n$3,000.00\nApr 16, 2024\n")
    dlg.refresh()
    assert dlg.table.rowCount() == 2 and "2 matched" in dlg.summary.text()
    mike = next(r for r in dlg.items if r.name == "Mike Chen")
    et.unlink(books, mike.id)
    pick = PickTransactionDialog(books, et.records(books, status="unmatched")[0])
    assert pick.table.rowCount() == 1  # within 7 days: the free $3,000 line
    pick.days.setValue(14)                # wider: Jane's line (already matched) shows too
    assert pick.table.rowCount() == 2
    assert pick.lines[0].matched_to == "" and "Jane Smith" in pick.lines[1].matched_to
    pick.table.selectRow(0)
    pick.choose()
    assert all(r.transaction_id for r in et.records(books))
    panel.refresh()
    panel.close()
    # AR / AP account picker
    acct = rx.add_control_account(books, "bill", "AP – Related Party")
    d = DocumentDialog(books, "bill")
    d.party.setCurrentText("Sister Co")
    d.control.set_value(acct.id)
    d.amount.setValue(100)
    d.save()
    assert rx.get_document(books, d.saved_id).control_id == acct.id
    p = ControlAccountPicker(books, "bill")
    p.follow_party("Sister Co")
    assert p.value() == acct.id
    books.close()


def test_arap_tab(app, tmp_path):
    from datetime import date

    from am_accounting import receivables as rx
    from am_accounting import workspace as ws
    from am_accounting.books import Books
    from am_accounting.gui.arap_tab import C_DOC, C_PARTY, ARAPTab

    books = Books.open(tmp_path / "Co.amdb")
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")))
    inc = books.account_by_code("4100").id
    rx.add_document(books, "invoice", "Acme", "2026-09-01", "2026-10-01", 20000, inc, number="INV-1")
    rx.add_document(books, "invoice", "Acme", "2026-10-15", "2026-11-14", 50000, inc, number="INV-2")
    rx.add_document(books, "invoice", "Beta", "2026-10-15", "2026-11-14", 10000, inc, number="INV-3")
    tab = ARAPTab(books)
    tab.as_of.setDate(tab.as_of.date().fromString("2026-11-02", "yyyy-MM-dd"))
    t = tab.ar.table
    # grouped by customer: Acme header, its 2 invoices, Beta header, its invoice, total
    assert [t.item(r, C_PARTY).text() for r in range(t.rowCount())] == ["Acme", "Acme", "Acme", "Beta", "Beta",
                                                                         "Total"]
    assert t.item(1, C_DOC).text() == "✗ Missing"
    assert "3 invoices" in tab.ar.summary.text() and "3 without the invoice on file" in tab.ar.summary.text()
    assert "net" in tab.net.text() and tab.ap.items == []
    tab.group.setCurrentIndex(tab.group.findData(None))
    assert t.rowCount() == 4
    tab.show_which.setCurrentIndex(tab.show_which.findData("overdue"))
    assert [i.number for i in tab.ar.items] == ["INV-1"]
    books.close()
    assert date


def test_cheques_window(app, tmp_path, monkeypatch):
    from datetime import date

    from am_accounting import cheques as cq
    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.cheques_dialog import ChequesDialog, FixDialog, PickLineDialog
    from am_accounting.gui.review_panel import ReviewPanel

    books = Books.open(tmp_path / "Co.amdb")
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")), connect_inbox=False, connect_documents=False)
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date(2024, 4, 15), "CHQ#00126-9000000001", -498250)])
    panel = ReviewPanel(books)
    panel.show()
    assert panel.cheque_btn.isVisible()
    monkeypatch.setattr(cq, "read_file", lambda p: cq.ChequeInfo("cheque", "", None, "Jane Smith", None, ""))
    (cq.folder(books) / "scan.jpg").write_bytes(b"x")
    dlg = ChequesDialog(books)
    dlg.check_now()
    assert dlg.table.rowCount() == 1 and dlg.items[0].transaction_id is None
    fix = FixDialog(books, dlg.items[0])
    fix.number.setText("126")
    fix.amount.setValue(4982.50)
    fix.save()
    dlg.refresh()
    assert dlg.items[0].transaction_id and "1 matched" in dlg.summary.text()
    cq.unlink(books, dlg.items[0].id)
    dlg.refresh()
    pick = PickLineDialog(books, dlg.items[0])
    assert pick.table.rowCount() == 1
    pick.choose()
    assert cq.items(books, "matched")
    panel.refresh()
    panel.close()
    books.close()


def test_to_categorize_shows_matched_etransfer_and_cheque(app, tmp_path, monkeypatch):
    from datetime import date

    from am_accounting import cheques as cq
    from am_accounting import etransfer as et
    from am_accounting import workspace as ws
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.review_panel import ReviewPanel

    books = Books.open(tmp_path / "Co.amdb")
    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")), connect_inbox=False, connect_documents=False)
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date(2024, 4, 4), "SEND E-TFR ***k9q", -300000),
        ParsedTransaction(date(2024, 4, 15), "CHQ#00126-9000000001", -498250)])
    et.import_text(books, "Sent to Jane Smith\n$3,000.00\nApr 4, 2024\nReference: CA9XyZwk9q\nMessage: April rent\n")
    monkeypatch.setattr(cq, "read_file", lambda p: cq.ChequeInfo("cheque", "126", "2024-04-10", "Lakeshore Mgmt",
                                                                  498250, "May rent"))
    (cq.folder(books) / "scan.jpg").write_bytes(b"x")
    cq.process_folder(books, min_age_seconds=0)
    panel = ReviewPanel(books)
    panel.show()
    from am_accounting.gui.review_panel import HTML_ROLE

    texts = [panel.list.item(i).data(HTML_ROLE) or "" for i in range(panel.list.count())]
    assert any("↔ to Jane Smith · “April rent”" in t for t in texts)
    assert any("✎ Cheque #126 to Lakeshore Mgmt · “May rent”" in t for t in texts)
    for i, it in enumerate(panel.items):
        panel.list.setCurrentRow(i)
        assert panel.found_box.isVisible()
        if it.tx.description.startswith("CHQ"):
            assert "Lakeshore Mgmt" in panel.found_label.text() and panel.found_open.isVisible()
            panel._unmatch_found()
            assert cq.items(books, "matched") == []
        else:
            assert "CA9XyZwk9q" in panel.found_label.text() and not panel.found_open.isVisible()
    panel.close()
    books.close()


def test_to_categorize_names(app, tmp_path):
    from datetime import date

    from am_accounting import etransfer as et
    from am_accounting import parties
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.review_panel import ReviewPanel
    from am_accounting.gui.transactions import TransactionsTab

    books = Books.open(tmp_path / "Co.amdb")
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date(2026, 7, 29), "E-TRANSFER ***aa1", 10000),
        ParsedTransaction(date(2026, 7, 31), "E-TRANSFER ***aa2", 35000),
        ParsedTransaction(date(2026, 8, 20), "AB123 TFR-FR 7340218", 400000)])
    et.import_text(books, "Received from Alex Morgan\n$100.00\nJul 29, 2026\nReceived from Alex Morgan\n$350.00\n"
                          "Jul 31, 2026\n")
    panel = ReviewPanel(books)
    panel.show()
    labels = [panel.name_filter.itemText(i) for i in range(panel.name_filter.count())]
    assert labels == ["Everyone (3)", "Alex Morgan (2)", "No name yet (1)"]
    panel.name_filter.setCurrentIndex(1)
    assert [q.name for q in panel.items] == ["Alex Morgan", "Alex Morgan"]
    assert "Also apply to 1 other with Alex Morgan" in panel.similar.text()
    panel.name_filter.setCurrentIndex(0)
    panel.group.setChecked(True)
    assert panel.row_item[0] is None and panel.list.item(0).text().startswith("Alex Morgan")
    tfr = next(i for i, q in enumerate(panel.row_item) if q and q.tx.description.startswith("AB123"))
    panel.list.setCurrentRow(tfr)
    assert panel.name_pattern.text() == "TFR-FR 7340218"
    panel.name_edit.setCurrentText("Alex Morgan")
    panel.name_future.setChecked(True)
    panel._save_name()
    assert parties.rules(books)[0][1:] == ("TFR-FR 7340218", "Alex Morgan")
    assert panel.name_filter.itemText(1) == "Alex Morgan (3)"
    tab = TransactionsTab(books)
    tab.status_filter.setCurrentIndex(0)
    tab.name_filter.setCurrentIndex(tab.name_filter.findData("Alex Morgan"))
    assert tab.model.rowCount() == 3
    panel.close()
    books.close()


def test_customers_vendors_tab(app, tmp_path):
    from am_accounting import contacts as ct
    from am_accounting.books import Books
    from am_accounting.gui.contacts_tab import ContactDialog, ContactsTab

    books = Books.open(tmp_path / "Co.amdb")
    tab = ContactsTab(books)
    dlg = ContactDialog(books, role="customer")
    dlg.name.setText("Sister Co Ltd")
    dlg.type.setCurrentText("Related entity")
    dlg.show()
    assert dlg.due_from.isVisible() and not dlg.due_to.isVisible()
    dlg._make_related("receivable")
    dlg.aliases.setText("PRIYA SHAH; SISTER CO")
    dlg.save()
    c = ct.find(books, "sister co")
    assert c.type == "Related entity" and books.account(c.receivable_id).name == "Due from Sister Co Ltd"
    v = ContactDialog(books, role="vendor")
    v.name.setText("Harbor Supply Co")
    v.save()
    tab.refresh()
    assert tab.table.rowCount() == 1 and tab.table.item(0, 3).text() == "Due from Sister Co Ltd"
    tab.which.button(1).setChecked(True)
    tab.refresh()
    assert tab.table.item(0, 0).text() == "Harbor Supply Co"
    tab.which.button(2).setChecked(True)
    tab.refresh()
    assert tab.table.rowCount() == 2
    books.close()


def test_arap_assign_dialog(app, tmp_path):
    from datetime import date

    from am_accounting import contacts as ct
    from am_accounting import receivables as rx
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.arap_tab import ARAPTab, AssignDialog

    books = Books.open(tmp_path / "Co.amdb")
    c = ct.Contact("Sister Co Ltd", "customer", "Related entity")
    ct.save(books, c)
    ct.related_account(books, c, "receivable")
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 4, 9), "AB123 TFR-FR 7340218", -2500000)])
    books.set_category(books.transactions()[0].id, rx.control_account(books, "invoice").id, source="manual")
    tab = ARAPTab(books)
    items = tab.ar.items
    dlg = AssignDialog(books, "invoice", items)
    assert dlg.contact.count() == 1
    dlg.contact.setCurrentText("Sister Co Ltd")
    assert not dlg.move.isHidden() and dlg.move.isChecked() and "Due from Sister Co Ltd" in dlg.move.text()
    dlg.save()
    tab.refresh()
    assert [(i.party, i.account) for i in tab.ar.items] == [("Sister Co Ltd", "Due from Sister Co Ltd")]
    books.close()


def test_reports_drilldown_and_transaction_search(app, tmp_path):
    from datetime import date

    from PySide6.QtCore import QUrl

    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.reports_tab import ReportsTab
    from am_accounting.gui.transactions import TransactionsTab

    books = Books.open(tmp_path / "Co.amdb")
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date.today(), "MONTHLY PLAN FEE", -500), ParsedTransaction(date.today(), "SUBWAY", -1276)])
    fees = books.account_by_code("6050").id
    fee_tx = next(t for t in books.transactions() if t.description == "MONTHLY PLAN FEE")
    books.set_category(fee_tx.id, fees, source="manual")
    tab = ReportsTab(books)
    tab.kind.setCurrentText("Income Statement (P&L)")
    assert "MONTHLY PLAN FEE" not in tab.view.toPlainText()
    tab._clicked(QUrl(f"acct:{fees}"))
    assert "MONTHLY PLAN FEE" in tab.view.toPlainText()
    tab._clicked(QUrl(f"acct:{fees}"))
    assert "MONTHLY PLAN FEE" not in tab.view.toPlainText()
    tab.expand_all()
    assert "MONTHLY PLAN FEE" in tab.view.toPlainText() and "SUBWAY" in tab.view.toPlainText()
    tx = TransactionsTab(books)
    tx.status_filter.setCurrentIndex(0)
    tx.search.setText("bank and merchant fees")
    assert tx.model.rowCount() == 1
    tx.search.setText("12.76")
    assert tx.model.rowCount() == 1
    books.close()


def test_transaction_dialog_sets_customer_vendor(app, tmp_path):
    from datetime import date

    from PySide6.QtCore import QUrl

    from am_accounting import contacts as ct
    from am_accounting import parties
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.reports_tab import ReportsTab
    from am_accounting.gui.transaction_dialog import TransactionDialog
    from am_accounting.gui.transactions import C_PARTY, TransactionsTab

    books = Books.open(tmp_path / "Co.amdb")
    books.import_transactions(books.account_by_code("1000").id, [
        ParsedTransaction(date.today(), "AB123 TFR-FR 7340218", 400000),
        ParsedTransaction(date.today(), "SEND E-TFR ***k9q", -300000),
        ParsedTransaction(date.today(), "SEND E-TFR ***Baz", -300000)])
    sister = ct.Contact("Sister Co Ltd", "both", "Related entity")
    ct.save(books, sister)
    due = ct.related_account(books, sister, "receivable")
    tfr = next(t for t in books.transactions() if t.description.startswith("AB123"))
    dlg = TransactionDialog(books, tfr.id)
    dlg.who.setCurrentText("Sister Co Ltd")
    assert not dlg.use_acct.isHidden() and "Due from Sister Co Ltd" in dlg.use_acct.text()
    dlg._use_contact_account()
    dlg.future.setChecked(True)
    assert dlg.pattern.text() == "TFR-FR 7340218"
    dlg.save()
    t = books.transaction(tfr.id)
    assert t.category_id == due and parties.name_of(books, t.id).name == "Sister Co Ltd"
    assert parties.rules(books)[0][1:] == ("TFR-FR 7340218", "Sister Co Ltd")
    # a new name is added to the Customers & Vendors list as a vendor (money out)
    etf = next(t for t in books.transactions() if t.description.endswith("k9q"))
    d2 = TransactionDialog(books, etf.id)
    d2.who.setCurrentText("Jane Smith")
    d2.save()
    assert ct.find(books, "Jane Smith").role == "vendor"
    # the Transactions tab shows the column; several rows at once
    tab = TransactionsTab(books)
    tab.status_filter.setCurrentIndex(0)
    names = {tab.model.rows[r].description: tab.model.index(r, C_PARTY).data() for r in range(tab.model.rowCount())}
    assert names["AB123 TFR-FR 7340218"] == "Sister Co Ltd" and names["SEND E-TFR ***k9q"] == "Jane Smith"
    # statements: entries under an expanded line link to this window
    rt = ReportsTab(books)
    rt.kind.setCurrentText("Balance Sheet")
    rt._clicked(QUrl(f"acct:{due}"))
    assert f'href="tx:{tfr.id}"' in rt._html(links=True)
    books.close()


def test_asset_management_mode(app, tmp_path, monkeypatch):
    from datetime import date

    from am_accounting import portfolio as pfm
    from am_accounting.books import Books, CompanyProfile, ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.asset_mgmt import AssetManagementView, FlowDialog, HoldingDialog, ValuationDialog
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op = reg.create(CompanyProfile("Northwind OpCo Inc"))
    b = Books.open(op)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date.today(), "CLIENT", 120000_00)])
    b.set_category(b.transactions()[0].id, b.account_by_code("4100").id, source="manual")
    b.close()
    w = MainWindow(str(op), registry=reg)
    assert w.stack.currentWidget() is w.accounting_page
    w.set_mode("assets")
    v = w.asset_view
    assert w.stack.currentWidget() is v and w.mode_tabs.tabText(w.mode_tabs.currentIndex()) == "Asset Management"
    auto = v.pf.holdings()                    # the operating company was pulled in automatically
    assert [(x.name, x.company_path) for x in auto] == [("Northwind OpCo Inc", str(op))]
    hd = HoldingDialog(v.pf, v.companies_fn())
    hd.name.setText("Northwind OpCo")
    hd.company.setCurrentIndex(hd.company.findData(str(op)))
    import am_accounting.gui.asset_mgmt as am_gui

    errors = []
    monkeypatch.setattr(am_gui, "error", lambda *a: errors.append(a[2]))
    hd.save()                                 # can't link the same books twice
    assert errors and "already linked" in errors[0]
    assert len(v.pf.holdings()) == 1
    hd.reject()
    hd = HoldingDialog(v.pf, v.companies_fn(), auto[0])
    hd.own.setValue(80)
    hd.save()
    h = v.pf.holdings()[0]
    v.refresh()
    v.table.selectRow(0)
    fd = FlowDialog(v.pf, h)
    fd.kind.setCurrentIndex(fd.kind.findData("contribution"))
    fd.amount.setValue(50000)
    fd.save()
    vd = ValuationDialog(v.pf, h)
    vd.equity_only.setChecked(True)
    vd.equity.setValue(200000)
    vd.save()
    v.refresh()
    p = v.summary.performances[0]
    assert p.nav == 160000_00 and p.invested == 50000_00 and p.financials.revenue_ltm == 120000_00
    assert v.grouped.cards["nav"].value.text() == "$160,000.00" and v.cons_table.columnCount() == 3
    assert v.cons_table.item(0, 0).text() == "Revenue" and v.cons_table.item(0, 1).text() == "$120,000.00"
    v.table.selectRow(0)
    v.open_books()  # back to Accounting with the holding's books
    assert w.stack.currentWidget() is w.accounting_page and w.books.company_name == "Northwind OpCo Inc"
    w.toggle_mode()
    assert w.stack.currentWidget() is v
    w.close()


def test_asset_management_reads_holding_company_books(app, tmp_path):
    from datetime import date

    from am_accounting.books import Books, CompanyProfile, ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.asset_mgmt import HoldingDialog
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    hold = reg.create(CompanyProfile("Morgan Holdings Inc", role="holding"))
    w = MainWindow(str(hold), registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    d = HoldingDialog(v.pf, v.companies_fn(), parent=v, create_company=v.create_company)
    d.name.setText("Lakeshore Apartments LP")
    d.type.setCurrentText("Real estate")
    d.owner.setCurrentIndex(d.owner.findData(str(hold)))       # Morgan Holdings holds the shares
    d._create_books()                       # a new company file for the holding
    d._create_investment_account()          # 'Investment in Lakeshore Apartments LP' in the holdco's books
    d.save()
    h = v.pf.holdings()[0]
    assert h.company_path and "Lakeshore" in h.company_path and len(h.invest_accounts) == 1
    assert h.owner_path == str(hold)
    assert any(e.name == "Lakeshore Apartments LP" for e in reg.companies())
    # money sent from the holding company's bank to that account → a contribution in Asset Management
    b = Books.open(hold)
    b.import_transactions(b.account_by_code("1000").id, [ParsedTransaction(date(2026, 2, 1), "WIRE OUT", -75000_00)])
    b.set_category(b.transactions()[0].id, h.invest_accounts[0], source="manual")
    b.close()
    v.refresh()
    p = v.summary.performances[0]
    assert p.invested == 75000_00 and p.flows[0].from_books
    v.table.selectRow(0)
    assert "(books)" in v.flow_table.item(0, 1).text()
    w.close()


def test_new_company_role_choice(app, tmp_path):
    from am_accounting import portfolio as pfm
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.companies import CompanyProfileDialog
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    first = reg.create(CompanyProfile("Northwind OpCo"))
    w = MainWindow(str(first), registry=reg)
    dlg = CompanyProfileDialog(reg, parent=w)
    assert list(dlg.role_buttons) == ["operating", "holding", "asset_manager"]
    assert dlg.role_buttons["operating"].isChecked()
    dlg.name.setText("Morgan Holdings Inc")
    dlg.role_buttons["holding"].setChecked(True)
    dlg.save()
    w.switch_books(str(dlg.created_path))
    w._after_company_created(str(dlg.created_path), dlg.role())
    w._fill_switcher()
    texts = [w.switcher.itemText(i).strip() for i in range(w.switcher.count())]
    assert texts.index("HOLDING COMPANIES") < texts.index("Morgan Holdings Inc")
    pf = pfm.Portfolio(w.portfolio_path())
    assert pf.holdco_path == str(dlg.created_path)
    pf.close()
    assert w.books.profile().role == "holding"
    w.close()



def test_asset_manager_workflow(app, tmp_path):
    from am_accounting import portfolio as pfm
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.asset_mgmt import HoldingDialog
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    hold = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    mgr = str(reg.create(CompanyProfile("Northstar Capital", role="asset_manager")))
    op = str(reg.create(CompanyProfile("Northwind OpCo Inc")))
    w = MainWindow(op, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    # only the operating company was pulled in automatically; edit it to set its owner and manager
    assert [x.name for x in v.pf.holdings()] == ["Northwind OpCo Inc"]
    d = HoldingDialog(v.pf, v.companies_fn(), v.pf.holdings()[0], parent=v)
    # each picker only offers the right kind of company
    assert [d.owner.itemText(i) for i in range(d.owner.count())] == ["(no holding company)", "Morgan Holdings Inc"]
    assert [d.manager.itemText(i) for i in range(d.manager.count())] == ["(no asset manager)", "Northstar Capital"]
    assert [d.company.itemText(i) for i in range(d.company.count())] == ["(not linked)", "Northwind OpCo Inc"]
    d.name.setText("Northwind OpCo")
    d.owner.setCurrentIndex(1)
    d.own.setValue(60)
    d.company.setCurrentIndex(1)
    d.manager.setCurrentIndex(1)
    d.fee_pct.setValue(2)
    d.fee_fixed.setValue(500)
    d.save()
    h = v.pf.holdings()[0]
    assert (h.owner_path, h.manager_path, h.fee_pct, h.fee_fixed_cents) == (hold, mgr, 2.0, 500_00)
    v.pf.add_valuation(h.id, pfm.Valuation("2026-01-01", 1_000_000_00))
    v.refresh()
    labels = [v.viewing.itemText(i).strip() for i in range(v.viewing.count()) if v.viewing.itemText(i)]
    assert labels == ["All operating companies", "Northwind OpCo Inc", "All holding companies", "Morgan Holdings Inc",
                      "All asset managers", "Northstar Capital"]       # grouped like the Company list
    for data, n in ((("company", op), 1), (("owners", ""), 1), (("owner", hold), 1), (("managers", ""), 1)):
        v.viewing.setCurrentIndex(next(i for i in range(v.viewing.count())
                                       if v.viewing.itemData(i) and tuple(v.viewing.itemData(i)) == data))
        v.refresh()
        assert len(v.summary.performances) == n, data
    v.viewing.setCurrentIndex(next(i for i in range(v.viewing.count())
                                   if v.viewing.itemData(i) and tuple(v.viewing.itemData(i)) == ("manager", mgr)))
    v.refresh()
    assert v.mgr.currentData() == mgr and v.managed_table.item(0, 0).text() == "Northwind OpCo Inc"
    assert v.managed_table.item(0, 6).text() == "$26,000.00"   # 2% of $1M + $500 × 12
    w.close()


def test_role_bar_links_to_asset_management(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op = str(reg.create(CompanyProfile("Northwind OpCo Inc")))
    hold = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    mgr = str(reg.create(CompanyProfile("Northstar Capital", role="asset_manager")))
    w = MainWindow(op, registry=reg)
    assert w.role_bar.isHidden()
    w.switch_books(hold)
    assert not w.role_bar.isHidden() and "holding company" in w.role_text.text()
    w._open_role_view()
    assert w.stack.currentWidget() is w.asset_view and tuple(w.asset_view.viewing.currentData()) == ("owner", hold)
    w.set_mode("accounting")
    w.switch_books(mgr)
    assert w.role_btn.text() == "Open Managed Companies"
    w._open_role_view()
    assert tuple(w.asset_view.viewing.currentData()) == ("manager", mgr)
    av = w.asset_view
    assert av.tabs.currentIndex() == 0 and av.portfolio_tabs.currentWidget() is av.managed_page
    w.close()


def test_portfolio_summary_and_company_overview(app, tmp_path, monkeypatch):
    from datetime import date

    from am_accounting import portfolio as pfm
    from am_accounting.books import Books, CompanyProfile, ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.asset_mgmt import SUM_ROWS, ValuationDialog
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op = str(reg.create(CompanyProfile("Lakeshore Apartments LP")))
    b = Books.open(op)
    bank = b.account_by_code("1000").id
    mort = b.add_account("2610", "Mortgage Payable", "liability", "long_term").id
    b.set_opening_balance(mort, "2025-01-01", 1_800_000_00)
    b.import_transactions(bank, [ParsedTransaction(date.today(), "RENTS", 35000_00)])
    b.set_category(b.transactions()[0].id, b.account_by_code("4100").id, source="manual")
    b.close()
    w = MainWindow(op, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    h = v.pf.holdings()[0]                     # pulled in automatically, linked to its books
    assert h.company_path == op
    h.type, h.ownership_pct = "Real estate", 50
    v.pf.save_holding(h)
    vd = ValuationDialog(v.pf, h)
    assert vd.debt.value() == 1_800_000      # prefilled from its books
    vd.company_value.setValue(3_250_000)
    assert "$1,450,000.00" in vd.result.text() and "$725,000.00" in vd.result.text()
    vd.save()
    v.refresh()
    assert v.tabs.tabText(0) == "Portfolio" and v.tabs.tabText(1) == "Dashboard"
    tabs = [v.tabs.tabText(i) for i in range(1, v.tabs.count()) if v.tabs.isTabVisible(i)]
    assert tabs == ["Dashboard", "Rent Roll && Operations", "Cash Flow", "CapEx", "Financing && Cap Table",
                    "Waterfall (LP / GP)", "Projections"]
    subs = [v.ops_page.sub.tabText(i) for i in range(v.ops_page.sub.count())]
    assert subs[3] == "Customers && Vendors" and v.ops_page.sub.widget(3) is v.cv_page    # after Operating Costs
    assert v.cv_pick.isHidden()                         # follows the company picked at the top of the page
    v.show_deals()                                         # Deal Pipeline: a button beside Portfolio Summary
    assert v.tabs.currentWidget() is v.deals_page and v.deals_btn.isChecked() and not v.summary_btn.isChecked()
    assert not v.tabs.isTabVisible(v.tabs.indexOf(v.deals_page))
    v.show_summary()
    assert v.summary_btn.isChecked() and not v.deals_btn.isChecked()
    assert [v.portfolio_tabs.tabText(i) for i in range(v.portfolio_tabs.count())] == [
        "Summary", "Holdings", "Consolidated Financials", "Managed Companies"]   # behind the button
    assert not v.tabs.isTabVisible(0)                     # a button at the top, not a tab
    v.tabs.setCurrentIndex(1)
    assert not v.summary_btn.isChecked()
    v.summary_btn.click()
    assert v.tabs.currentIndex() == 0 and v.summary_btn.isChecked()
    t = v.overview_table
    # vertical: one column per company (+ portfolio), one row per figure
    assert t.horizontalHeaderItem(1).toolTip() == "Lakeshore Apartments LP"      # full name on hover
    assert t.horizontalHeaderItem(2).toolTip() == "Portfolio (our share)"
    assert [t.item(r, 0).text() for r in range(t.rowCount())] == SUM_ROWS
    row = {SUM_ROWS[r]: t.item(r, 1).text() for r in range(len(SUM_ROWS))}
    total = {SUM_ROWS[r]: t.item(r, 2).text() for r in range(len(SUM_ROWS))}
    assert (total["Value"], total["Debt"], total["NAV"]) == ("$1,625,000.00", "$900,000.00", "$725,000.00")
    assert SUM_ROWS[2:] == ["", "Value", "Debt", "NAV", "", "Revenue", "Op. Ex", "EBITDA", "", "Non-Op. Ex", "CapEx",
                            "Net cash flow", "", "MOIC", "IRR", "Cash on cash return", "", "Lender(s)", "Principal",
                            "Balance outstanding", "Interest rate", "Annual interest", "Maturity"]
    assert row["Balance outstanding"] == "$1,800,000.00" and row["Lender(s)"] == "Mortgage Payable"  # from the books
    assert (row["Value"], row["Debt"], row["NAV"]) == ("$3,250,000.00", "$1,800,000.00", "$725,000.00")
    assert row["Revenue"] == "$35,000.00" and row["EBITDA"] == "$35,000.00" and row["Net cash flow"] == "$35,000.00"
    g = v.grouped.cards
    assert g["value"].value.text() == "$1,625,000.00" and g["debt"].value.text() == "$900,000.00"
    assert g["nav"].value.text() == "$725,000.00" and g["revenue"].value.text() == "$17,500.00"  # our 50% share
    # order: value, debt, NAV | revenue, Op. Ex, EBITDA | Non-Op. Ex, CapEx, net cash flow | MOIC, IRR, cash on cash
    assert list(g) == ["value", "debt", "nav", "revenue", "opex", "ebitda", "nonop", "capex", "ncf", "moic", "irr",
                       "coc"]
    v._open_company_overview(t.model().index(3, 1))
    assert v.tabs.currentWidget() is v.dash_page and v.co_grouped.cards["nav"].value.text() == "$725,000.00"
    dd = v.dash_page.data
    assert dd["value"][-1] == ["NAV (50% owned)", "$725,000.00"] and dd["operations"][2] == ["EBITDA", "$35,000.00"]
    assert v.co_grouped.cards["ebitda"].value.text() == "$35,000.00" and len(v.co_chart.data) == 12
    assert v.co_vals.rowCount() == 1
    # existing financing: the mortgage in the books, then its terms entered
    from PySide6.QtCore import QDate

    from am_accounting.gui.asset_mgmt import LoanDialog

    assert v.co_loans.rowCount() == 2 and "add terms" in v.co_loans.item(0, 0).text()   # summary on the overview
    v._go_financing(0)                                   # its own page: Financing & Cap Table
    assert v.tabs.tabText(v.tabs.currentIndex()) == "Financing && Cap Table"
    assert v.fin_pick.currentText() == "Lakeshore Apartments LP" and v._fin_sel == 0
    ld = LoanDialog(v.pf, v._fin_perf().holding, v._selected_loan())
    assert ld.balance.value() == 1_800_000 and not ld.balance.isEnabled()   # balance read from the books
    ld.lender.setText("TD Bank")
    ld.principal.setValue(2_000_000)
    ld.rate.setValue(5.25)
    ld.start.setDate(QDate(2024, 4, 30))
    ld.term_y.setValue(5)
    ld.amort_y.setValue(25)
    ld.freq.setCurrentText("Monthly")
    assert ld.maturity.text() == "2029-04-30" and ld.payment.value() == 11984.95   # worked out
    assert ld.sched.rowCount() == 60 + 2                 # 60 payments, the balloon, the total
    ld.repayment.setCurrentIndex(ld.repayment.findData("Interest only"))
    assert ld.payment.value() == 8750 and not ld.amort_y.isEnabled()
    ld.repayment.setCurrentIndex(ld.repayment.findData("Amortizing"))
    ld.save()
    v.refresh()
    fl = v.fin_loan_cells
    assert fl[0][0] == "TD Bank" and fl[0][2] == "Amortizing"
    assert v.fin_lower.tabText(1) == "Check Against the Balance Sheet" and "DEBT" in v.fin_recon.toPlainText()
    assert v.fin_recon_data.debt_gap == 0 and v.fin_check.text() == "✓ Matches the balance sheet"
    assert fl[0][5] == "5.25% fixed" and fl[0][6] == "5 yr / 25 yr amort."
    assert fl[0][7] == "$11,984.95 monthly" and fl[0][10] == "2029-04-30"
    assert "TD Bank" in v.fin_loans.toPlainText() and 'href="loan:0"' in v.fin_loans.toHtml()   # click to select
    import re as _re

    assert set(_re.findall(r'border="(\d+)"', v.fin_loans.toHtml())) <= {"0"}             # no lines
    from PySide6.QtCore import QUrl

    v._fin_select(None)
    assert v._selected_loan() is None
    v.fin_loans.anchorClicked.emit(QUrl("loan:0"))                # clicking the lender selects the loan
    assert v._selected_loan() is v._fin_rows[0] and v.fin_sched.rowCount() == 62
    assert v.fin_sched.rowCount() == 62 and v.fin_sched.item(61, 0).text() == "Total"
    v.fin_sched_view.setCurrentIndex(1)                  # by year
    assert v.fin_sched.item(0, 0).text() == "2024"
    assert v.co_loans.item(0, 0).text() == "TD Bank" and v.co_loans.item(0, 6).text() == "2029-04-30"
    # capital stack and cap table
    stack = {v.fin_stack.item(r, 0).text(): v.fin_stack.item(r, 1).text() for r in range(v.fin_stack.rowCount())}
    assert stack["TD Bank – Mortgage"] == "$1,800,000.00" and stack["Total capitalization"] == "$3,250,000.00"
    assert v.fin_holders.item(0, 4).text() == "50.00%" and v.fin_holders.item(1, 0).text() == "Other owners"
    from am_accounting.gui.asset_mgmt import ShareholderDialog

    for name, co, amt in (("Alex Morgan", "Morgan Holdings Inc", 600_000), ("Jane Shah", "", 400_000)):
        sd = ShareholderDialog(v.pf, v._fin_perf().holding, None, v)
        sd.name.setText(name)
        sd.company.setText(co)
        sd.amount.setValue(amt)
        sd.save()
    v.refresh()
    fh = v.fin_holders
    assert [fh.item(r, 0).text() for r in range(2)] == ["Alex Morgan", "Jane Shah"]
    assert fh.item(0, 1).text() == "Morgan Holdings Inc" and fh.item(0, 4).text() == "60.00%"
    assert fh.item(2, 3).text() == "$1,000,000.00" and fh.item(0, 2).text() == "Common"
    # equity by share class, imported from a workbook laid out like a Debt-Equity tab
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Debt-Equity"
    ws.append(["Equity Contribution"])
    ws.append(["Class A Shares", None, "Name of Investor", "Name of Corporation", "Equity", "UBO%", "Funded"])
    ws.append([None, None, "Pat Lee", "Lee Holdings Inc.", 600000, 0.48, 600000])
    ws.append([None, None, "Sam Roe", "Sam Roe", 150000, 0.12, 100000])
    ws.append(["Total Class A", None, None, None, 750000])
    ws.append(["Class B Shares"])
    ws.append([None, None, "Name of Investor", "Name of Corporation", "Equity", "UBO%"])
    ws.append([None, None, "Kim Day", "Day Capital Ltd", 500000, 0.4, 520000])
    wbp = tmp_path / "equity.xlsx"
    wb.save(wbp)
    import am_accounting.gui.asset_mgmt as amg

    monkeypatch.setattr(amg, "confirm", lambda *a: True)
    monkeypatch.setattr(amg, "info", lambda *a: None)
    assert v._fin_import_equity(str(wbp)) == 3
    rows = {fh.item(r, 0).text(): r for r in range(fh.rowCount())}
    assert fh.item(rows["Total Class A"], 3).text() == "$750,000.00"
    assert fh.item(rows["Total Class A"], 4).text() == "60.00%" and fh.item(rows["Total Class A"], 5).text() == "$700,000.00"
    assert fh.item(rows["Total Class B"], 6).text() == "over by $20,000.00"
    assert fh.item(rows["Sam Roe"], 1).text() == "—"           # same as the investor: no separate company
    row = {SUM_ROWS[r]: t.item(r, 1).text() for r in range(len(SUM_ROWS))}
    total = {SUM_ROWS[r]: t.item(r, 2).text() for r in range(len(SUM_ROWS))}
    assert (row["Lender(s)"], row["Principal"], row["Interest rate"], row["Maturity"]) == (
        "TD Bank", "$2,000,000.00", "5.25%", "2029-04-30")
    assert total["Balance outstanding"] == "$900,000.00" and total["Annual interest"] == "$47,250.00"
    # sources & uses on the front page
    from am_accounting.gui.asset_mgmt import SU_ROWS, SourcesUsesDialog

    su = v.su_table
    assert su.horizontalHeaderItem(1).toolTip() == "Lakeshore Apartments LP" and su.rowCount() == len(SU_ROWS)
    col = {SU_ROWS[r]: su.item(r, 1).text() for r in range(len(SU_ROWS)) if su.item(r, 1)}
    assert col["Debt financing"] == "$2,000,000.00" and col["Basis"] == "Worked out"
    assert col["Total sources"] == col["Total uses"]
    sd = SourcesUsesDialog(v.pf, v.summary.performances[0], v._as_of_date())
    sd.boxes["Purchase price"].setValue(3_000_000)
    sd.boxes["Equity – us"].setValue(1_250_000)
    sd.balance()
    assert sd.boxes["Cash reserve / other"].value() == 250_000
    sd.save()
    v.refresh()
    col = {SU_ROWS[r]: su.item(r, 1).text() for r in range(len(SU_ROWS)) if su.item(r, 1)}
    assert col["Basis"] == "Entered" and col["Total uses"] == "$3,250,000.00"
    assert su.item(SU_ROWS.index("Total uses"), 2).text() == "$1,625,000.00"     # our 50%
    # customers & vendors page: revenue by customer (the RENTS deposit has no name yet)
    assert v.cv_pick.currentText() == "Lakeshore Apartments LP"
    cv = v.cv_customers
    assert cv.item(0, 0).text() == "(not assigned)" and cv.item(0, 1).text() == "$35,000.00"
    assert cv.item(cv.rowCount() - 1, 1).text() == "$35,000.00" and "0% assigned" in v.cv_info.text()
    w.close()


def test_waterfall_tab(app, tmp_path):
    from am_accounting import portfolio as pfm
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    hold = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    mgr = str(reg.create(CompanyProfile("Northstar Capital", role="asset_manager")))
    w = MainWindow(hold, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    h = pfm.Holding("A Co", owner_path=hold, manager_path=mgr)
    v.pf.save_holding(h)
    v.pf.add_flow(h.id, pfm.Flow("2024-01-01", "acquisition", 1_000_000_00))
    v.pf.add_valuation(h.id, pfm.Valuation("2025-12-31", 2_000_000_00))
    v.refresh()
    assert v.wf_pick.itemText(0).startswith("Overall") and v.wf_pick.findData(hold) == 1
    assert v.wf_pick.currentData() == hold          # the holding company picked at the top
    v.wf_pick.setCurrentIndex(0)
    assert not v.wf_terms_box.isEnabled()          # overall: each holding company keeps its own terms
    v.wf_pick.setCurrentIndex(1)
    assert v.wf_terms_box.isEnabled() and v.wf_lp.value() == 98 and v.wf_scope.count() == 2
    assert v.wf_cards["gp"].sub.text() == "Northstar Capital"
    lp = v.wf_table.item(4, 2).text()
    v.wf_carry.setValue(30)
    v._save_terms()
    assert pfm.get_terms(v.pf, hold).carry_pct == 30 and v.wf_table.item(4, 2).text() != lp
    assert v.wf_table.item(4, 0).text() == "Total" and v.wf_parts.rowCount() == 1
    # a company's own cap table: owners straight from Financing & Cap Table, through the tiers on its equity value
    for name, cls, amt in (("Pat Lee", "Class A", 900_000_00), ("Sponsor Co", "Class B", 100_000_00)):
        pfm.save_shareholder(v.pf, h.id, pfm.Shareholder(name, amount_cents=amt, share_class=cls, funded_cents=amt,
                                                         funded_date="2024-01-01"))
    v.refresh()
    i = v.wf_pick.findData(f"__co__:{h.id}")
    assert i > 0 and "cap table" in v.wf_pick.itemText(i)
    v.wf_pick.setCurrentIndex(i)
    assert v.wf_owners.isVisible() or not v.isVisible()
    assert v.wf_owners.rowCount() == 3 and v.wf_owners.item(0, 0).text() == "Pat Lee"
    assert v.wf_owners.item(0, 3).text() == "LP" and v.wf_owners.item(2, 6).text() == "$2,000,000.00"
    v.wf_gp_class.setCurrentIndex(v.wf_gp_class.findData("Class B"))
    assert v.wf_owners.item(1, 3).text() == "GP" and v.wf_owners.item(1, 9).text() != ""
    v._save_terms()
    assert pfm.get_terms(v.pf, pfm.company_terms_key(h.id)).gp_name == "Class B"
    w.close()


def test_projections_window(app, tmp_path):
    from datetime import date

    from PySide6.QtCore import QDate

    from am_accounting import portfolio as pfm
    from am_accounting.gui.asset_mgmt import AssetManagementView, ProjectionEditor, ProjectionsDialog

    v = AssetManagementView(tmp_path / "pf.ampf", lambda: [])
    h = pfm.Holding("Lakeshore", "Real estate", 50)
    v.pf.save_holding(h)
    pfm.save_loan(v.pf, h.id, pfm.Loan("First National", "Mortgage", 1_000_000_00, rate_pct=5.0,
                                       start_date=f"{date.today().year}-01-01", term_months=60, amort_months=300))
    actual = pfm.Financials("2026-10-31", revenue_ltm=400000_00, opex_ltm=180000_00, nonop_ltm=50000_00,
                            capex_ltm=30000_00)
    d = ProjectionsDialog(v.pf, h, actual)
    e = d.editor
    assert [e.tabs.tabText(i) for i in range(e.tabs.count())] == ["Summary", "Revenue && OpEx", "CapEx",
                                                                  "Financing && Equity", "Exit && Returns"]
    e.add_year()
    year = e.years[0]

    def put(grid, name, amount, col=1):
        t = e.grids[grid].table
        names = [t.item(r, 0).text() for r in range(t.rowCount())]
        t.item(names.index(name), col).setText(amount)

    def get(grid, name, col=1):
        t = e.grids[grid].table
        names = [t.item(r, 0).text() for r in range(t.rowCount())]
        return t.item(names.index(name), col).text()

    put("ops", "Rental income", "400,000")
    put("ops", "Parking", "20000")
    put("ops", "Property tax", "$60,000")
    put("ops", "Insurance", "40,000.00")
    put("ops", "Utilities", "90000")
    put("ops", "Non-Op. Ex (taxes, other)", "10,000")
    assert get("ops", "Total revenue") == "$420,000.00" and get("ops", "NOI / EBITDA") == "$230,000.00"
    assert get("ops", "Cash flow from operations") == "$220,000.00"
    put("capex", "In-suite renovations", "250,000")
    e.grids["capex"].add_item("capex")
    t = e.grids["capex"].table
    t.item([t.item(r, 0).text() for r in range(t.rowCount())].index("New item"), 0).setText("Roof replacement")
    put("capex", "Roof replacement", "80,000")
    assert get("capex", "Total CapEx") == "$330,000.00"
    put("fin", "Equity contributions", "150,000")
    put("fin", "Distributions to owners", "20,000")
    assert get("fin", "Net financing & equity") == "$130,000.00"
    # everything flows into the summary
    sm = e.summary_table
    rows = {sm.item(r, 0).text(): r for r in range(sm.rowCount()) if sm.item(r, 0).text()}
    col = 2                                      # after the last 12 months
    assert sm.item(rows["Cash flow before financing"], col).text() == "($110,000.00)"
    assert sm.item(rows["Net cash flow after financing"], col).text() == "$20,000.00"
    e.fill_financing()                           # the mortgage's interest and principal for the year
    assert get("fin", "Interest – First National").startswith("$") and get("fin", "Interest – First National") != "$0.00"
    assert get("fin", "Principal – First National") != "$0.00"
    e.add_year()   # copies the last year forward
    assert e.years == [year, year + 1] and get("ops", "Total revenue", 2) == "$420,000.00"
    # exit at the end of the first year: IRR and MOIC on the Summary and on Exit & Returns
    e.x_date.setDate(QDate(year, 12, 31))
    e.x_cap.setValue(5.0)
    assert "IRR" in e.returns_line.text() and "MOIC" in e.returns_line.text()
    r = e.returns()
    assert r.exit_noi == 230_000_00 and r.exit_value == 4_600_000_00 and "EXIT" in e.returns_page.toPlainText()
    e.x_method.setCurrentIndex(e.x_method.findData("multiple"))
    e.x_mult.setValue(10)
    assert e.returns().exit_value == 2_300_000_00 and "at 10×" in e.returns_page.toPlainText()
    e.x_method.setCurrentIndex(e.x_method.findData("cap"))
    d.save()
    from am_accounting import exit_returns as er
    assert er.get(v.pf, h).exit_date == f"{year}-12-31"
    p0 = pfm.projections(v.pf, h.id)[0]
    assert (p0.revenue, p0.opex, p0.nonop, p0.capex) == (420_000_00, 190_000_00, 10_000_00, 330_000_00)
    assert p0.fin_in == 150_000_00 and p0.fin_out > 20_000_00
    lines = {ln.item: ln for ln in pfm.projection_lines(v.pf, h.id)}
    assert lines["Roof replacement"].kind == "capex" and lines["Interest – First National"].kind == "fin_out"
    v.refresh()
    v.proj_pick.setCurrentIndex(0)   # portfolio, our 50%
    t = v.proj_table
    assert t.horizontalHeaderItem(1).text() == f"{year} (projected)" and t.item(0, 1).text() == "$210,000.00"
    assert t.item(8, 1).text() == "$75,000.00"           # money in, our half
    v.proj_pick.setCurrentIndex(1)   # the company: edited right on the page
    assert isinstance(v.proj_editor, ProjectionEditor) and v.proj_stack.currentWidget() is v.proj_editor
    assert v.proj_editor.years == [year, year + 1] and not v.proj_editor.dirty
    v.close_portfolio()


def test_company_dropdown_with_asset_and_accounting_tabs(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    hold = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    a = str(reg.create(CompanyProfile("Alpha Ltd")))
    b = str(reg.create(CompanyProfile("Beta Ltd")))
    w = MainWindow(a, registry=reg)
    w.set_mode("accounting")
    tabs = [w.mode_tabs.tabText(i) for i in range(w.mode_tabs.count())]
    assert tabs == ["Asset Management", "Accounting"] and w.mode_tabs.currentIndex() == 1
    assert w.company_role.text() == "Operating company"
    w.mode_tabs.setCurrentIndex(0)                      # Asset Management for Alpha
    v = w.asset_view
    assert w.stack.currentWidget() is v and v.tabs.tabText(v.tabs.currentIndex()) == "Dashboard"
    assert v.co_pick.currentText() == "Alpha Ltd"
    w.switcher.setCurrentIndex(w.switcher.findData(b))  # pick Beta from the dropdown: still Asset Management
    w._on_switcher(w.switcher.currentIndex())
    assert w.books.company_name == "Beta Ltd" and v.co_pick.currentText() == "Beta Ltd"
    assert w.stack.currentWidget() is v
    w.switcher.setCurrentIndex(w.switcher.findData(hold))
    w._on_switcher(w.switcher.currentIndex())
    assert v.viewing.currentData() and v.viewing.currentData()[0] == "owner"   # its shareholdings
    assert v.tabs.currentIndex() == 0 and "Holding" in w.company_role.text()
    w.mode_tabs.setCurrentIndex(1)                      # Accounting for the holding company
    assert w.stack.currentWidget() is w.accounting_page and w.books.company_name == "Morgan Holdings Inc"
    w.close()


def test_manual_transaction_and_journal_entries(app, tmp_path):
    from datetime import date

    from PySide6.QtCore import QDate

    from am_accounting import parties
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.manual_entry import JournalEntriesDialog, JournalEntryDialog, ManualTransactionDialog
    from am_accounting.gui.transactions import TransactionsTab

    b = Books.open(tmp_path / "c.amdb", create=True)
    bank = b.account_by_code("1000").id
    sales = b.account_by_code("4100").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 5, 1), "DEPOSIT", 500_00)])
    tab = TransactionsTab(b)
    d = ManualTransactionDialog(b, bank_id=bank)
    d.date.setDate(QDate(2026, 5, 2))
    d.money_in.setChecked(True)
    d.amount.setValue(500)
    assert "Already in this account" in d.warning.text()       # same amount 1 day apart
    d.description.setText("Cash sale")
    d.category.setCurrentIndex(d.category.findData(sales))
    d.who.setCurrentText("Walk-in Customer")
    d.save()
    tx = b.transaction(d.tx_id)
    assert tx.manual and tx.amount_cents == 500_00 and tx.category_id == sales and tx.reviewed
    assert parties.name_of(b, tx.id).name == "Walk-in Customer"
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("all"))
    tab.refresh()
    assert any(r.manual and tab.model.data(tab.model.index(i, 2)).startswith("✎ ") for i, r in
               enumerate(tab.model.rows))
    e = ManualTransactionDialog(b, tx.id)                       # change the amount later
    assert e.category is None and e.money_in.isChecked()
    e.amount.setValue(450)
    e.save()
    assert b.transaction(tx.id).amount_cents == 450_00
    imported = next(t for t in b.transactions() if not t.manual)
    with pytest.raises(ValueError):
        b.update_manual_transaction(imported.id, bank, "2026-05-01", "x", 1)

    # a journal entry: depreciation
    dep = b.add_account("6995", "Depreciation Expense", "expense", "operating").id
    acc = b.add_account("1795", "Accumulated Depreciation", "asset", "fixed_asset").id
    je = JournalEntryDialog(b)
    je.date.setDate(QDate(2026, 6, 30))
    je.memo.setText("Depreciation H1")
    assert [je.table.cellWidget(r, 0).currentText() for r in range(2)] == ["Debit", "Credit"]
    je.table.cellWidget(0, 1).setCurrentIndex(je.table.cellWidget(0, 1).findData(dep))
    je.table.cellWidget(0, 2).setValue(1200)
    je.table.cellWidget(1, 1).setCurrentIndex(je.table.cellWidget(1, 1).findData(acc))
    je.table.cellWidget(1, 2).setValue(1000)
    assert "out by $200.00" in je.totals.text()
    je.table.setCurrentCell(1, 1)
    je.balance_line()
    assert je.table.cellWidget(1, 2).value() == 1200 and "balanced" in je.totals.text()
    je.save()
    assert b.ledger_totals(end="2026-12-31")[dep] == 1200_00
    lst = JournalEntriesDialog(b)
    assert lst.table.rowCount() == 1 and lst.table.item(0, 1).text() == "Depreciation H1"
    lst.table.selectRow(0)
    ch = JournalEntryDialog(b, lst._current())
    assert ch.table.cellWidget(0, 0).currentText() == "Debit" and ch.table.cellWidget(1, 0).currentText() == "Credit"
    ch.table.cellWidget(0, 2).setValue(1500)
    ch.table.cellWidget(1, 2).setValue(1500)
    ch.save()
    lst.refresh()
    assert lst.table.rowCount() == 1 and b.ledger_totals(end="2026-12-31")[dep] == 1500_00
    # a property bought with a mortgage and the owners' money: one debit, two credits
    prop = b.add_account("1610", "Property", "asset", "fixed_asset").id
    mtg = b.add_account("2710", "Mortgage", "liability", "long_term").id
    eqa = b.add_account("3950", "Partners' Capital", "equity", "").id
    je = JournalEntryDialog(b)
    je.memo.setText("Purchase of 12 Main St")
    je.table.cellWidget(0, 1).setCurrentIndex(je.table.cellWidget(0, 1).findData(prop))
    je.table.cellWidget(0, 2).setValue(1_000_000)
    je.table.cellWidget(1, 1).setCurrentIndex(je.table.cellWidget(1, 1).findData(mtg))
    je.table.cellWidget(1, 2).setValue(750_000)
    je.add_line(eqa, side="credit", fill=True)               # the rest: what balances the entry
    assert je.table.cellWidget(2, 2).value() == 250_000 and "balanced" in je.totals.text()
    je.save()
    t = b.ledger_totals(end="2026-12-31")
    assert t[prop] == 1_000_000_00 and t[mtg] == -750_000_00 and t[eqa] == -250_000_00
    # customers and vendors on journal entry lines: rent for unit 200, a hydro bill from the hydro company
    from am_accounting import contacts, party_totals

    rent = b.add_account("4160", "Rental Income", "income", "operating").id
    hydro = b.add_account("6410", "Hydro", "expense", "operating").id
    ar = next(a.id for a in b.accounts() if a.type == "asset" and "receivable" in a.name.lower())
    ap = next(a.id for a in b.accounts() if a.type == "liability" and "payable" in a.name.lower())
    je = JournalEntryDialog(b)
    je.date.setDate(QDate(2026, 7, 1))
    je.memo.setText("July rent and hydro")
    assert je.table.columnCount() == 4 and je.table.horizontalHeaderItem(3).text() == "Customer / Vendor"
    je.table.cellWidget(0, 1).setCurrentIndex(je.table.cellWidget(0, 1).findData(ar))
    je.table.cellWidget(0, 2).setValue(1_800)
    je.table.cellWidget(1, 1).setCurrentIndex(je.table.cellWidget(1, 1).findData(rent))
    je.table.cellWidget(1, 2).setValue(1_800)
    je.table.cellWidget(1, 3).setCurrentText("Unit 200")
    je.add_line(hydro, 420_00, side="debit", party="Toronto Hydro")
    je.add_line(ap, side="credit", fill=True)
    assert "balanced" in je.totals.text()
    je.save()
    e = next(x for x in b.journal_entries(kind="manual") if x["memo"] == "July rent and hydro")
    assert e["parties"] == ["", "Unit 200", "Toronto Hydro", ""]
    bd = party_totals.breakdown(b, "2026-01-01", "2026-12-31")
    assert any(c.name == "Unit 200" and c.amount == 1_800_00 for c in bd.customers)
    assert any(v.name == "Toronto Hydro" and v.by_account == {"Hydro": 420_00} for v in bd.vendors)
    assert {c.name for c in contacts.all_contacts(b)} >= {"Unit 200", "Toronto Hydro"}   # on the list
    # owing, by customer and vendor: AR from the rent, AP from the hydro bill
    from am_accounting import arap

    ar_items = {i.party: i.balance_cents for i in arap.outstanding(b, "invoice", date(2026, 12, 31))}
    ap_items = {i.party: i.balance_cents for i in arap.outstanding(b, "bill", date(2026, 12, 31))}
    assert ar_items.get("Unit 200") == 1_800_00 and ap_items.get("Toronto Hydro") == 420_00
    assert next(c for c in bd.customers if c.name == "Unit 200").outstanding == 1_800_00
    assert next(v for v in bd.vendors if v.name == "Toronto Hydro").outstanding == 420_00
    sums = {x.contact.name: x for x in contacts.summaries(b)}
    assert sums["Unit 200"].receivable_cents == 1_800_00 and sums["Toronto Hydro"].payable_cents == 420_00
    cash = b.account_by_code("1000").id                               # the tenant pays $1,000 of it
    b.replace_journal_entry(None, "2026-07-05", "Unit 200 rent received",
                            [(cash, 1_000_00, 0, ""), (ar, 0, 1_000_00, "Unit 200")])
    bd2 = party_totals.breakdown(b, "2026-01-01", "2026-12-31")
    assert next(c for c in bd2.customers if c.name == "Unit 200").outstanding == 800_00
    again = JournalEntryDialog(b, e)                                  # opens with the names
    assert again.table.cellWidget(2, 3).currentText() == "Toronto Hydro"
    lst.refresh()
    assert any("(Toronto Hydro)" in (lst.table.item(r, 2).text() if lst.table.item(r, 2) else "")
               for r in range(lst.table.rowCount()))
    b.close()


def test_loans_in_the_accounting(app, tmp_path, monkeypatch):
    from datetime import date

    from am_accounting import loan_link as ll, portfolio as pfm
    from am_accounting.books import CompanyProfile, ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui import asset_mgmt, loans_dialog
    from am_accounting.gui.loans_dialog import LoanPaymentsDialog, LoanSplitDialog
    from am_accounting.gui.main_window import MainWindow

    monkeypatch.setattr(loans_dialog, "info", lambda *a: None)
    monkeypatch.setattr(asset_mgmt, "info", lambda *a: None)
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op = str(reg.create(CompanyProfile("Opco")))
    w = MainWindow(op, registry=reg)
    bank = w.books.account_by_code("1000").id
    w.books.import_transactions(bank, [ParsedTransaction(date(2026, 1, 5), "OPENING DEPOSIT", 10_000_00),
                                       ParsedTransaction(date(2026, 2, 1), "RBC LOAN PMT", -2_000_00),
                                       ParsedTransaction(date(2026, 3, 1), "WIRE OUT", -2_500_00)])
    w.set_mode("assets")
    v = w.asset_view
    h = v.pf.holdings()[0]
    pfm.save_loan(v.pf, h.id, pfm.Loan("RBC", "Term loan", 100_000_00, rate_pct=6, start_date="2025-07-01",
                                       term_months=60))
    v.refresh()
    v._go_financing(0)
    v._fin_link()                                          # Asset Management: create the loan's account
    ln = pfm.loans(v.pf, h, with_books=False)[0]
    assert ln.account_id is not None
    w.set_mode("accounting")
    assert w.books.account(ln.account_id).type == "liability"
    dlg = LoanPaymentsDialog(w.books)
    assert dlg.loans.rowCount() == 1 and "Term loan – RBC" in dlg.loans.item(0, 4).text()
    assert dlg.found.rowCount() == 1 and dlg.found.item(0, 2).text() == "RBC LOAN PMT"   # by name + loan word
    dlg.record()
    t = next(x for x in w.books.transactions() if x.description == "RBC LOAN PMT")
    assert ll.is_loan_tx(w.books, t.id)
    w.tx_tab.status_filter.setCurrentIndex(w.tx_tab.status_filter.findData("all"))
    w.tx_tab.refresh()
    r = next(i for i, x in enumerate(w.tx_tab.model.rows) if x.id == t.id)
    cat = w.tx_tab.model.data(w.tx_tab.model.index(r, 5))
    assert "Term loan – RBC" in cat and "Interest Expense" in cat     # shown split
    parts = w.books.splits(t.id)
    assert parts[0][0] == ln.account_id and parts[0][1] + parts[1][1] == 2_000_00 and parts[1][1] > 0
    wire = next(x for x in w.books.transactions() if x.description == "WIRE OUT")
    sd = LoanSplitDialog(w.books, wire.id)                 # any bank line, by hand
    assert sd.as_payment.isChecked() and sd.principal.value() + sd.interest.value() == 2500
    sd.interest.setValue(400)
    assert sd.principal.value() == 2100
    sd.save()
    assert [(p, i) for _t, p, i in ll.recorded(w.books, ln.id)][-1] == (2100_00, 400_00)
    w.set_mode("assets")
    v.refresh()
    v._go_financing(0)
    assert "2 recorded" in v.fin_sched.item(v.fin_sched.rowCount() - 1, 6).text()
    w.set_mode("accounting")
    w.close()



def test_review_panel_handles_thousands_quickly(app, tmp_path):
    """Thousands of transactions to categorize: the side panel fills fast and categorizing doesn't freeze."""
    import time
    from datetime import date, timedelta

    from am_accounting import parties
    from am_accounting.books import Books, ParsedTransaction
    from am_accounting.gui.review_panel import ReviewPanel

    b = Books.open(tmp_path / "big.amdb", create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2024, 1, 1) + timedelta(days=i % 700), f"SHOP {i % 300}",
                                                   -(100 + i) * 100) for i in range(3000)])
    for i in range(0, 3000, 3):
        parties.set_name(b, [i + 1], f"Shop {i % 300}")
    exp = b.account_by_code("6000").id if any(a.code == "6000" for a in b.accounts()) else \
        next(a.id for a in b.accounts() if a.type == "expense")
    with b.batch():
        for t in b.transactions()[:900]:
            b.set_category(t.id, exp, source="manual", reviewed=True)
    t0 = time.perf_counter()
    panel = ReviewPanel(b)
    panel.show()
    app.processEvents()
    assert panel.list.count() == 2100 and time.perf_counter() - t0 < 8
    t0 = time.perf_counter()
    assert parties.apply_learned(b) > 0                 # learned from the named, categorized ones
    assert time.perf_counter() - t0 < 5
    panel.close()
    b.close()


def test_change_company_type_and_grouped_lists(app, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    import am_accounting.gui.common as common
    from am_accounting import company_roles
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.companies import CompaniesDialog
    from am_accounting.gui.main_window import MainWindow

    monkeypatch.setattr(common, "info", lambda *a: None)
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op = str(reg.create(CompanyProfile("Northwind OpCo")))
    mgr = str(reg.create(CompanyProfile("Northstar Asset Management")))      # created as an operating company
    reg.create(CompanyProfile("Morgan Holdings Inc", role="holding"))
    w = MainWindow(mgr, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    assert "Northstar Asset Management" in [p.holding.name for p in v.summary.performances]
    label = "Asset manager — " + company_roles.ROLES["asset_manager"][1]
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (label, True)))
    w.change_company_type()                                   # the button next to the company type
    assert company_roles.role_of(w.books) == "asset_manager" and w.company_role.text() == "Asset manager"
    assert v.viewing.currentData()[0] == "manager"          # now shows the companies it manages
    v.viewing.setCurrentIndex(0)                             # the whole portfolio
    v.refresh()
    assert [p.holding.name for p in v.summary.performances] == ["Northwind OpCo"]   # out of the portfolio
    assert v.cons_table.horizontalHeaderItem(1).toolTip().startswith("Northwind OpCo")
    texts = [w.switcher.itemText(i).strip() for i in range(w.switcher.count())]
    assert texts.index("OPERATING COMPANIES") < texts.index("Northwind OpCo") < texts.index("HOLDING COMPANIES") \
        < texts.index("Morgan Holdings Inc") < texts.index("ASSET MANAGERS") < texts.index("Northstar Asset Management")
    dlg = CompaniesDialog(reg, mgr, w)
    col0 = [dlg.table.item(r, 0).text().strip() for r in range(dlg.table.rowCount())]
    assert col0 == ["Operating companies (1)", "Northwind OpCo", "Holding companies (1)", "Morgan Holdings Inc",
                    "Asset managers (1)", "Northstar Asset Management  (open)"]
    dlg.table.selectRow(0)
    assert dlg._selected() is None                            # a heading isn't a company
    dlg.table.selectRow(1)
    label = "Holding company — " + company_roles.ROLES["holding"][1]
    monkeypatch.setattr(QInputDialog, "getItem", staticmethod(lambda *a, **k: (label, True)))
    dlg.change_type()                                         # from Manage Companies too
    assert company_roles.read_role(op) == "holding"
    assert dlg.table.item(0, 0).text() == "Holding companies (2)"   # no operating companies left
    w.set_mode("accounting")
    w.close()


def test_company_columns_fit_numbers_and_long_names_wrap(app):
    from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

    from am_accounting.gui.asset_mgmt import fit_name_columns

    t = QTableWidget(2, 3)
    t.setHorizontalHeaderLabels(["", "1200-1210 Lakeview Investment Limited", "Portfolio (our share)"])
    for c, v in ((1, "$5,600,000.00"), (2, "$800,000.00")):
        t.setItem(0, c, QTableWidgetItem(v))
    t.setItem(0, 0, QTableWidgetItem("Value"))
    fit_name_columns(t)
    h = t.horizontalHeaderItem(1)
    from PySide6.QtGui import QFontMetrics

    name_w = QFontMetrics(t.horizontalHeader().font()).horizontalAdvance("1200-1210 Lakeview Investment Limited")
    num_w = QFontMetrics(t.item(0, 1).font()).horizontalAdvance("$5,600,000.00")
    assert num_w <= t.columnWidth(1) < name_w                 # fits the number, narrower than the whole name
    assert "\n" in h.text() and h.toolTip() == "1200-1210 Lakeview Investment Limited"
    fit_name_columns(t)                       # again (each refresh): still the full name underneath
    assert t.horizontalHeaderItem(1).toolTip() == "1200-1210 Lakeview Investment Limited"


def test_operations_and_capex_tabs(app, tmp_path, monkeypatch):
    import csv
    import re
    from datetime import date, timedelta

    from am_accounting.books import CompanyProfile, ParsedTransaction
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui import operations_tab as ot
    from am_accounting.gui.main_window import MainWindow

    monkeypatch.setattr(ot, "info", lambda *a: None)
    monkeypatch.setattr(ot, "confirm", lambda *a: True)
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op_path = str(reg.create(CompanyProfile("Maple Court Ltd")))
    w = MainWindow(op_path, registry=reg)
    bank = w.books.account_by_code("1000").id
    w.books.import_transactions(bank, [ParsedTransaction(date.today().replace(day=1), "RENTS", 3_000_00)])
    w.set_mode("assets")
    v = w.asset_view
    names = [v.tabs.tabText(i) for i in range(v.tabs.count())]
    assert {"Dashboard", "Rent Roll && Operations", "Cash Flow", "CapEx", "Financing && Cap Table"} <= set(names)
    ops = v.ops_page
    assert ops.pick.currentText() == "Maple Court Ltd"
    roll = tmp_path / "roll.csv"
    with open(roll, "w", newline="") as f:
        csv.writer(f).writerows([["Unit", "Tenant", "Unit Type", "Status", "Rent", "Market Rent", "Move In",
                                  "Lease End"],
                                 ["1", "A. Tenant", "1 Bedroom", "Old Lease", "1,200.00", "1,800", "2019-05-01", "MTM"],
                                 ["2", "", "1 Bedroom", "Vacant", "0", "1,800", "", ""]])
    v.tabs.setCurrentWidget(ops)
    assert ops.import_from(str(roll)) == 2
    assert ops.roll.rowCount() == 3 and ops.roll.item(2, 5).text() == "$1,200.00"
    assert ops.cards["units"].value.text() == "2" and ops.cards["occupancy"].value.text() == "50.0%"
    assert ops.cards["ltl"].value.text() == "$600.00"
    text = ops.stmt.toPlainText()                    # one clean page: months across, Pro-Forma / Actual
    assert date.today().strftime("%b %y") in text and "Pro-Forma" in text and "Actual" in text
    for label in ("Rental income", "Total revenue", "Net operating income", "Debt service", "DSCR",
                  "Scheduled rent (rent roll)"):
        assert label in text
    assert set(re.findall(r'border="(\d+)"', ops.stmt.toHtml())) <= {"0"}
    assert ops.mix.rowCount() == 2
    # operating costs: beside the rent roll, the usual categories to start
    from am_accounting import op_costs as oc

    assert [ops.sub.tabText(i) for i in range(ops.sub.count())] == [
        "Monthly Operating Statement", "Rent Roll", "Operating Costs", "Customers && Vendors", "Unit Mix"]
    assert ops.costs.rowCount() == len(oc.CATEGORIES) + 1 and ops.costs.item(0, 0).text() == "Property tax"
    ops.costs.selectRow(1)
    cd = ot.CostDialog(v.pf, ops.perf().holding, ops._cost(), ops)
    assert cd.category.currentText() == "Electricity (hydro)" and "e.g." in cd.provider.lineEdit().placeholderText()
    cd.provider.setCurrentText("Bright Power")
    cd.account_no.setText("ACCT-77")
    cd.budget.setValue(30_000)
    cd.save()
    ops._fill_costs()
    assert ops.costs.item(1, 1).text() == "Bright Power" and ops.costs.item(1, 2).text() == "ACCT-77"
    assert ops.costs.item(ops.costs.rowCount() - 1, 7).text() == "$30,000.00"
    assert ops.vendor_check.error == "" and ops.vendor_status.text()        # the vendor check
    dash = v.dash_page
    v.tabs.setCurrentWidget(dash)                # opening the tab shows the latest figures
    assert dash.title.text() == "Maple Court Ltd Dashboard"
    assert [r[0] for r in dash.data["rental"]] == ["EGI", "Op. Ex", "NOI"]
    assert dash.data["types"][0][:2] == ["1 Bedroom", "1"]                        # one old-lease unit
    assert dash.data["leasing"][0][1] == "1"                                      # one available
    page = dash.page.toPlainText()
    assert "RENTAL STATUS" in page and "LEASING UPDATE" in page and "Maple Court Ltd" in page
    import re as _re

    assert set(_re.findall(r'border="(\d+)"', dash.page.toHtml())) <= {"0"}      # no lines anywhere
    m = ot.MilestoneDialog(v.pf, dash.perf().holding, parent=dash)
    m.name.setText("Turnover milestone – 5 units")
    m.save()
    dash.save_notes("- Rent collected\n- Show unit painted")
    assert any(r[0] == "Turnover milestone – 5 units" for r in dash.data["timeline"])
    assert "Show unit painted" in dash.page.toPlainText()
    cash = v.cash_page
    assert cash.pick.currentText() == "Maple Court Ltd" and cash.cards["cash"].value.text() == "$3,000.00"
    page = cash.page.toPlainText()
    assert "Pro-Forma" in page and "Actual" in page and "Net property cash flow" in page
    assert date.today().strftime("%b %y") in page                                 # months as "Mmm yy"
    m = date.today().replace(day=1)
    for _ in range(3):
        m = (m - timedelta(days=1)).replace(day=1)
    assert cash.page._start == m.strftime("%b %y") == ops.stmt._start            # opens 3 months back
    cash.show_cols.setCurrentIndex(cash.show_cols.findData("actual"))
    assert cash.page.toPlainText().count("Pro-Forma") == 1                         # only in the note
    cap = v.capex_page
    d = ot.CapexLineDialog(v.pf, cap.perf().holding, parent=cap)
    d.item.setText("Roofing")
    d.budget.setValue(50_000)
    d.save()
    cap.fill()
    cap.table.selectRow(1)                       # row 0 is the "Hard Costs" heading
    cap.create_account()
    assert any(a.name == "CapEx – Roofing" for a in w.books.accounts())   # in the open books too
    assert cap.cards["budget"].value.text() == "$50,000.00" and cap.table.item(1, 7).text() == "CapEx – Roofing"
    bank_id = w.books.account_by_code("1000").id
    w.books.import_transactions(bank_id, [ParsedTransaction(date.today().replace(day=1), "ROOFER", -12_500_00)])
    roofer = next(t for t in w.books.transactions() if t.description == "ROOFER")
    w.books.set_category(roofer.id, next(a.id for a in w.books.accounts() if a.name == "CapEx – Roofing"))
    cap.fill()
    assert cap.month_rows[0][0] == "Roofing" and cap.month_rows[0][-1] == 12_500_00
    spend = cap.months.toPlainText()
    assert "HARD COSTS" in spend and "12,500" in spend and date.today().strftime("%b %y") in spend
    assert set(re.findall(r'border="(\d+)"', cap.months.toHtml())) <= {"0"}            # no lines
    # reconciled to the balance sheet: the roofing on the schedule, so no gap from it
    assert cap.lower.tabText(1) == "Reconcile to Balance Sheet" and cap.recon_data.difference == 0
    assert "CAPEX SCHEDULE → BALANCE SHEET" in cap.recon_page.toPlainText()
    assert any(cap.recon.item(r, 0).text() == "CapEx – Roofing" and cap.recon.item(r, 4).text() == "Roofing"
               for r in range(cap.recon.rowCount()))
    w.set_mode("accounting")
    w.close()
