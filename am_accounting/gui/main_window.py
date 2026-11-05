from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSettings, QStandardPaths
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QComboBox, QDockWidget, QFileDialog, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QTabWidget, QWidget)

from .. import __version__
from ..books import Books, CompanyProfile
from ..companies import CompanyRegistry
from ..importers import SUPPORTED
from .accounts import AccountsTab
from .common import error
from .dashboard_tab import DashboardTab
from .documents_tab import DocumentsTab
from .review_panel import ReviewPanel
from .companies import FILE_FILTER, CompaniesDialog, CompanyProfileDialog, default_registry
from .import_dialog import ImportDialog
from .inbox_dialog import InboxDialog, InboxService
from .pdf_excel import PdfWatchDialog, PdfWatchService, convert_files_in_background, describe
from .reports_tab import ReportsTab
from .rules import RulesTab
from .transactions import TransactionsTab

def legacy_books_path() -> Path:
    """Where version 0.1 kept its single company file."""
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return Path(docs) / "AM Accounting" / "My Company.amdb"


SWITCH_NEW = "__new__"
SWITCH_MANAGE = "__manage__"


class MainWindow(QMainWindow):
    def __init__(self, books_path: str | None = None, registry: CompanyRegistry | None = None):
        super().__init__()
        self.settings = QSettings("AM Accounting", "AM Accounting")
        self.registry = registry or default_registry()
        path = books_path or self._startup_path()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.books = Books.open(path)
        if path != ":memory:":
            self.registry.touch(path, self.books.company_name)

        self.tabs = QTabWidget()
        self.dashboard_tab = DashboardTab(self.books)
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        from .arap_tab import ARAPTab

        self.arap_tab = ARAPTab(self.books)
        self.tabs.addTab(self.arap_tab, "AR && AP")
        from .contacts_tab import ContactsTab

        self.contacts_tab = ContactsTab(self.books)
        self.tx_tab = TransactionsTab(self.books)
        self.accounts_tab = AccountsTab(self.books)
        self.rules_tab = RulesTab(self.books)
        self.reports_tab = ReportsTab(self.books)
        self.tabs.addTab(self.tx_tab, "Transactions")
        self.docs_tab = DocumentsTab(self.books)
        self.tabs.addTab(self.docs_tab, "Invoices && Bills")
        self.tabs.addTab(self.reports_tab, "Financial Statements")
        self.tabs.addTab(self.contacts_tab, "Customers && Vendors")
        self.tabs.addTab(self.accounts_tab, "Chart of Accounts")
        self.tabs.addTab(self.rules_tab, "Rules")
        # Accounting (the tabs above) and Asset Management (the portfolio), switched with the toggle at the top
        from PySide6.QtWidgets import QStackedWidget

        from PySide6.QtWidgets import QPushButton, QVBoxLayout

        self.stack = QStackedWidget()
        acc_page = QWidget()
        acc_lay = QVBoxLayout(acc_page)
        acc_lay.setContentsMargins(0, 0, 0, 0)
        acc_lay.setSpacing(0)
        self.role_bar = QWidget()
        self.role_bar.setObjectName("rolebar")
        self.role_bar.setStyleSheet("QWidget#rolebar { background: palette(alternate-base); "
                                    "border-bottom: 1px solid palette(midlight); }")
        rb = QHBoxLayout(self.role_bar)
        rb.setContentsMargins(10, 4, 10, 4)
        self.role_text = QLabel()
        self.role_text.setTextFormat(Qt.TextFormat.RichText)
        rb.addWidget(self.role_text, 1)
        self.role_btn = QPushButton()
        self.role_btn.clicked.connect(self._open_role_view)
        rb.addWidget(self.role_btn)
        acc_lay.addWidget(self.role_bar)
        acc_lay.addWidget(self.tabs, 1)
        self.accounting_page = acc_page
        self.stack.addWidget(acc_page)
        self.asset_view = None  # created the first time Asset Management is opened
        # top: pick the company, then its Asset Management or Accounting screen
        from PySide6.QtWidgets import QTabBar

        central = QWidget()
        cl = QVBoxLayout(central)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        head = QWidget()
        head.setObjectName("companybar")
        self.company_bar = head
        head.setStyleSheet("QWidget#companybar { border-bottom: 1px solid palette(mid); }")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(10, 6, 10, 0)
        self.company_label = QLabel("<b>Company:</b>")
        hl.addWidget(self.company_label)
        self.switcher = QComboBox()
        self.switcher.setMinimumWidth(300)
        sf = self.switcher.font()
        sf.setPointSizeF(sf.pointSizeF() * 1.15)
        sf.setBold(True)
        self.switcher.setFont(sf)
        self.switcher.setToolTip("Pick a company; its Asset Management and Accounting screens are the tabs beside")
        self.switcher.activated.connect(self._on_switcher)
        hl.addWidget(self.switcher)
        hl.addSpacing(16)
        self.mode_tabs = QTabBar()
        self.mode_tabs.setDrawBase(False)
        self.mode_tabs.setExpanding(False)
        self.mode_tabs.addTab("Asset Management")
        self.mode_tabs.addTab("Accounting")
        self.mode_tabs.setTabToolTip(0, "This company in the portfolio: overview, customers & vendors, financing, "
                                        "projections… (Ctrl+Shift+M switches)")
        self.mode_tabs.setTabToolTip(1, "This company's bookkeeping (Ctrl+Shift+M switches)")
        self.mode_tabs.setCurrentIndex(1)
        self.mode_tabs.currentChanged.connect(lambda i: self.set_mode(("assets", "accounting")[i]))
        hl.addWidget(self.mode_tabs, 0, Qt.AlignmentFlag.AlignBottom)
        self.company_role = QLabel()
        self.company_role.setObjectName("hint")
        hl.addSpacing(12)
        hl.addWidget(self.company_role)
        type_btn = QPushButton("Change Type…")
        type_btn.setToolTip("Change this company's type: operating company, holding company or asset manager")
        type_btn.clicked.connect(self.change_company_type)
        hl.addWidget(type_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        hl.addStretch(1)
        cl.addWidget(head)
        cl.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        # "To Categorize" side panel, available from every tab
        self.review_panel = ReviewPanel(self.books)
        self.review_dock = QDockWidget("To Categorize", self)
        self.review_dock.setObjectName("review_dock")
        self.review_dock.setWidget(self.review_panel)
        self.review_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable
                                     | QDockWidget.DockWidgetFeature.DockWidgetMovable
                                     | QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.review_dock)
        self.review_panel.changed.connect(self.on_data_changed)
        self.review_dock.setVisible(self.review_panel.total() > 0)
        self.review_dock.visibilityChanged.connect(self._review_visibility)

        self.tx_tab.request_import.connect(self.import_statement)
        self.dashboard_tab.request_import.connect(self.import_statement)
        self.dashboard_tab.request_report.connect(self.open_report_from_dashboard)
        self.dashboard_tab.request_review.connect(self.review_from_dashboard)
        self.dashboard_tab.request_documents.connect(self.open_documents)
        self.docs_tab.changed.connect(self.on_data_changed)
        self.arap_tab.changed.connect(self.on_data_changed)
        self.contacts_tab.changed.connect(self.on_data_changed)
        self.reports_tab.changed.connect(self.on_data_changed)
        self.contacts_tab.show_transactions.connect(self.show_contact_transactions)
        self.arap_tab.request_new.connect(self.arap_new)
        self.arap_tab.request_open.connect(self.arap_open)
        self.tx_tab.changed.connect(self.on_data_changed)
        self.accounts_tab.changed.connect(self.on_accounts_changed)
        self.rules_tab.changed.connect(self.on_data_changed)
        self.tabs.currentChanged.connect(self.on_tab_changed)

        self.pdf_service = PdfWatchService(self)
        self.pdf_service.event.connect(self.on_pdf_converted)
        self.pdf_service.apply()  # starts the background converter if it was enabled last time

        self.inbox_service = InboxService(self.registry, self)
        self.inbox_service.event.connect(self.on_inbox_event)

        from .workspace_dialog import AutoSaveService

        self.autosave = AutoSaveService(lambda: self.books, self)
        self.autosave.saved.connect(self.on_statements_saved)
        self.dashboard_tab.request_workspace.connect(self.workspace_settings)
        self.dashboard_tab.request_process_bills.connect(self.process_all_bills)

        from .. import loan_link

        loan_link.set_portfolio_path(self.portfolio_path())
        self._build_menu()
        self._build_switcher()
        self.mode = "accounting"
        if self.settings.value("mode") == "assets":
            from PySide6.QtCore import QTimer

            QTimer.singleShot(0, lambda: self.set_mode("assets"))
        self.resize(1280, 800)
        self.update_title()
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._maybe_prompt_workspace)

    # -- menus --------------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self._action(file_menu, "&New Company…", self.new_company, QKeySequence.StandardKey.New)
        self._action(file_menu, "&Companies…", self.manage_companies, "Ctrl+Shift+O")
        self.switch_menu = file_menu.addMenu("&Switch Company")
        self.switch_menu.aboutToShow.connect(self._fill_switch_menu)
        self._action(file_menu, "&Open Company File…", self.open_books, QKeySequence.StandardKey.Open)
        file_menu.addSeparator()
        self._action(file_menu, "&Import Bank Statement…", self.import_statement, "Ctrl+I")
        self._action(file_menu, "&Convert PDF Statements to Excel…", self.convert_pdfs, "Ctrl+E")
        file_menu.addSeparator()
        self._action(file_menu, "Company &Settings…", self.company_settings)
        self._action(file_menu, "Company &Folder && Auto-Save…", self.workspace_settings, "Ctrl+Shift+F")
        self._action(file_menu, "Save &Trial Balance to Excel", self.save_trial_balance, "Ctrl+Shift+T")
        file_menu.addSeparator()
        self._action(file_menu, "&Quit", self.close, QKeySequence.StandardKey.Quit)

        books_menu = self.menuBar().addMenu("&Bookkeeping")
        self._action(books_menu, "&Auto-Categorize", self.tx_tab.auto_categorize, "Ctrl+Shift+A")
        toggle = self.review_dock.toggleViewAction()
        toggle.setText("Show “To &Categorize” Panel")
        toggle.setShortcut(QKeySequence("Ctrl+K"))
        books_menu.addAction(toggle)
        self._action(books_menu, "Create &Rule from Selected…", self.rule_from_selection, "Ctrl+R")
        books_menu.addSeparator()
        self._action(books_menu, "Statement &Inbox (Folder / OneDrive)…", self.inbox_settings, "Ctrl+Shift+I")
        self._action(books_menu, "&New Invoice…", lambda: self.open_documents("new:invoice"), "Ctrl+Shift+N")
        self._action(books_menu, "Invoice Se&ttings…", self.invoice_settings)
        books_menu.addSeparator()
        self._action(books_menu, "e-&Transfer History…", self.etransfer_history, "Ctrl+Shift+E")
        self._action(books_menu, "&Cheques && Drafts…", self.cheques_drafts, "Ctrl+Shift+Q")
        self._action(books_menu, "Invoice &Dump…", self.invoice_dump, "Ctrl+Shift+D")
        self._action(books_menu, "Process All &Bills in the Invoice Dump", self.process_all_bills, "Ctrl+B")
        self._action(books_menu, "Automatic &PDF → Excel…", self.pdf_watch_settings)

        view_menu = self.menuBar().addMenu("&View")
        self._action(view_menu, "Switch &Accounting / Asset Management", self.toggle_mode, "Ctrl+Shift+M")
        view_menu.addSeparator()
        appearance = view_menu.addMenu("&Appearance")
        from PySide6.QtGui import QActionGroup

        from . import theme

        group = QActionGroup(self)
        self.appearance_actions = {}
        for key, label in theme.MODES.items():
            a = appearance.addAction(label)
            a.setCheckable(True)
            a.setChecked(theme.mode() == key)
            a.triggered.connect(lambda _=False, k=key: self.set_appearance(k))
            group.addAction(a)
            self.appearance_actions[key] = a
        try:  # follow the operating system when it switches between light and dark
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        except AttributeError:
            pass

        help_menu = self.menuBar().addMenu("&Help")
        self._action(help_menu, "Create &Sample Company", self.create_sample_company)
        self._action(help_menu, "&About", self.about)

    def _action(self, menu, text, slot, shortcut=None) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(slot)
        menu.addAction(a)
        return a

    # -- companies ----------------------------------------------------------

    def _startup_path(self) -> str:
        entry = self.registry.most_recent()
        if entry:
            return entry.path
        for legacy in (self.settings.value("last_books"), str(legacy_books_path())):
            if legacy and Path(legacy).is_file():
                self.registry.add(legacy)
                return legacy
        dlg = CompanyProfileDialog(self.registry, first_run=True)
        if dlg.exec() and dlg.created_path:
            return str(dlg.created_path)
        return str(self.registry.create(CompanyProfile("My Company")))

    def _build_switcher(self) -> None:
        self._fill_switcher()

    def _fill_switcher(self) -> None:
        """Companies grouped by type: operating companies, holding companies, asset managers."""
        from PySide6.QtGui import QStandardItemModel

        from .. import company_roles

        self.switcher.blockSignals(True)
        self.switcher.clear()
        current = 0
        entries = [(e, company_roles.read_role(e.path)) for e in self.registry.companies() if e.exists]
        for key, title in company_roles.GROUPS:
            group = [e for e, r in entries if r == key]
            if not group:
                continue
            if self.switcher.count():
                self.switcher.insertSeparator(self.switcher.count())
            self.switcher.addItem(title.upper(), None)
            head = self.switcher.model().item(self.switcher.count() - 1) \
                if isinstance(self.switcher.model(), QStandardItemModel) else None
            if head is not None:
                head.setEnabled(False)
                f = head.font()
                f.setBold(True)
                head.setFont(f)
            for e in group:
                if self._is_current(e.path):
                    current = self.switcher.count()
                self.switcher.addItem("   " + e.name, e.path)
        self.switcher.insertSeparator(self.switcher.count())
        self.switcher.addItem("New Company…", SWITCH_NEW)
        self.switcher.addItem("Manage Companies…", SWITCH_MANAGE)
        self.switcher.setCurrentIndex(current)
        self.switcher.blockSignals(False)
        role = company_roles.read_role(self.books.path) if self.books.path != ":memory:" else "operating"
        self.company_role.setText(company_roles.label(role))

    def _fill_switch_menu(self) -> None:
        self.switch_menu.clear()
        for e in self.registry.companies():
            if not e.exists:
                continue
            a = self.switch_menu.addAction(e.name)
            a.setCheckable(True)
            a.setChecked(self._is_current(e.path))
            a.triggered.connect(lambda _=False, p=e.path: self.switch_books(p))

    def _on_switcher(self, index: int) -> None:
        data = self.switcher.itemData(index)
        if data == SWITCH_NEW:
            self.new_company()
        elif data == SWITCH_MANAGE:
            self.manage_companies()
        elif data and not self._is_current(data):
            self.switch_books(data)
        self._fill_switcher()  # restore selection if the user cancelled

    def _is_current(self, path: str) -> bool:
        try:
            return Path(path).resolve() == Path(self.books.path).resolve()
        except OSError:
            return False

    def switch_books(self, path: str) -> None:
        if self._is_current(path):
            return
        try:
            new = Books.open(path, create=False)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not open company", str(e))
            return
        self.books.close()
        self.books = new
        self.registry.touch(path, new.company_name)
        self.settings.setValue("last_books", path)
        for tab in (self.review_panel, self.dashboard_tab, self.arap_tab, self.contacts_tab, self.docs_tab, self.tx_tab,
                    self.accounts_tab,
                    self.rules_tab, self.reports_tab):
            tab.set_books(new)
        self.update_title()
        self._fill_switcher()
        if getattr(self, "mode", "accounting") == "assets":
            self._focus_asset_view()
        self.inbox_service.refresh_companies()
        self.statusBar().showMessage(f"Switched to {new.company_name}", 5000)
        self._maybe_prompt_workspace()
        self.autosave.check()

    def new_company(self) -> None:
        dlg = CompanyProfileDialog(self.registry, parent=self)
        if dlg.exec() and dlg.created_path:
            self.switch_books(str(dlg.created_path))
            self._after_company_created(str(dlg.created_path), dlg.role())
        self._fill_switcher()

    def _after_company_created(self, path: str, role: str) -> None:
        """A new holding company becomes the portfolio's holding-company books (if none is set yet)."""
        if role != "holding":
            return
        from .. import portfolio as pfm

        pf = self.asset_view.pf if self.asset_view is not None else pfm.Portfolio(self.portfolio_path())
        try:
            if not pf.holdco_path or not Path(pf.holdco_path).exists():
                pf.holdco_path = path
                self.statusBar().showMessage("This holding company's books are now used by Asset Management for "
                                             "capital flows to and from its holdings.", 10000)
        finally:
            if self.asset_view is None:
                pf.close()

    # -- company folder & automatic saving ---------------------------------

    def _maybe_prompt_workspace(self) -> None:
        """Ask once per company for its folder (non-blocking, so it never gets in the way)."""
        from .. import workspace as ws

        if self.books.path == ":memory:" or ws.get_config(self.books).enabled:
            return
        if self.books._meta("ws_prompted") == "1":
            return
        with self.books.conn:
            self.books._set_meta("ws_prompted", "1")
        self.workspace_settings(first_time=True, blocking=False)

    def workspace_settings(self, first_time: bool = False, blocking: bool = True) -> None:
        from .workspace_dialog import WorkspaceDialog

        dlg = WorkspaceDialog(self.books, self, first_time=bool(first_time))
        dlg.accepted.connect(self._workspace_saved)
        if blocking:
            dlg.exec()
        else:
            dlg.open()

    def _workspace_saved(self) -> None:
        self.inbox_service.refresh_companies()
        self.dashboard_tab.refresh()
        self.docs_tab.refresh()
        results = self.autosave.check()
        if not results:
            self.statusBar().showMessage("Company folder saved.", 8000)

    def on_statements_saved(self, results) -> None:
        files = sum(len(r.files) for r in results)
        labels = ", ".join(r.period.label for r in results)
        errors = [e for r in results for e in r.errors]
        msg = f"Financial statements saved for {labels} ({files} files)."
        if errors:
            msg += " Some files couldn't be saved: " + errors[0]
        self.statusBar().showMessage(msg, 20000)
        self.dashboard_tab.refresh()

    def manage_companies(self) -> None:
        dlg = CompaniesDialog(self.registry, self.books.path, self)
        if dlg.exec() and dlg.chosen_path:
            self.switch_books(dlg.chosen_path)
        self._fill_switcher()
        self.inbox_service.refresh_companies()
        if getattr(dlg, "changed_types", False):
            self._after_type_change()

    def change_company_type(self) -> None:
        """Operating company / holding company / asset manager, for the company that's open."""
        from .companies import change_company_type

        if change_company_type(self, str(self.books.path), self.books):
            self.accounts_tab.refresh()
            self.tx_tab.reload_accounts()
            self._after_type_change()

    def _after_type_change(self) -> None:
        self._fill_switcher()
        self.update_title()
        if self.asset_view is not None:
            self.asset_view.refresh()
            if getattr(self, "mode", "accounting") == "assets":
                self._focus_asset_view()

    def open_books(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open company file", str(Path(self.books.path).parent), FILE_FILTER)
        if path:
            self.switch_books(path)

    def company_settings(self) -> None:
        if CompanyProfileDialog(self.registry, self.books, self).exec():
            self.update_title()
            self._fill_switcher()
            self.reports_tab.refresh_presets()
            self.dashboard_tab.refresh()

    def update_title(self) -> None:
        self._update_role_bar()
        if getattr(self, "mode", "accounting") == "assets":
            self.setWindowTitle(f"{self.books.company_name} · Asset Management — AM Accounting")
            return
        self.setWindowTitle(f"{self.books.company_name} · Accounting — AM Accounting")
        self.statusBar().showMessage(f"Company file: {self.books.path}")

    # -- actions ------------------------------------------------------------

    def import_statement(self) -> None:
        start_dir = self.settings.value("last_import_dir") or str(Path.home())
        paths, _ = QFileDialog.getOpenFileNames(self, "Import bank statements", start_dir, SUPPORTED)
        total_new = total_dup = 0
        workbooks: set[str] = set()
        for path in paths:
            self.settings.setValue("last_import_dir", str(Path(path).parent))
            try:
                dlg = ImportDialog(self.books, path, self)
            except Exception as e:  # noqa: BLE001 - never fail silently: say which file and why
                error(self, "Could not import", f"{Path(path).name} could not be opened for import:\n{e}")
                continue
            if dlg.exec():
                new, dup = dlg.result_counts
                total_new += new
                total_dup += dup
                if getattr(dlg, "excel_path", None):
                    workbooks.add(str(dlg.excel_path))
        if paths:
            from .doc_files import auto_match_and_file

            matched = auto_match_and_file(self.books) if total_new else 0
            if total_new:
                from .. import etransfer

                try:
                    etransfer.match_all(self.books)  # e-Transfer history read earlier waits for its statement
                    from .. import cheques

                    cheques.process_folder(self.books)  # cheques / drafts waiting for this statement
                    from .. import parties

                    parties.apply_learned(self.books)  # names (e.g. "TFR-FR 7340218 → Alex") bring their category
                except Exception:  # noqa: BLE001
                    pass
            self.on_accounts_changed()
            self.docs_tab.refresh()
            extra = f" Matched {matched} payment(s) to invoices/bills." if matched else ""
            if workbooks:
                extra += " PDF statements added to " + ", ".join(Path(w).name for w in sorted(workbooks)) + "."
            self.statusBar().showMessage(f"Imported {total_new} transaction(s); skipped {total_dup} duplicate(s)."
                                         + extra, 10000)
            if total_new and self.review_panel.total():
                self.review_dock.show()

    def convert_pdfs(self) -> None:
        start_dir = self.settings.value("last_import_dir") or str(Path.home())
        pdfs, _ = QFileDialog.getOpenFileNames(self, "Convert PDF statements to Excel", start_dir, "PDF files (*.pdf)")
        if not pdfs:
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Save Excel workbooks to", str(Path(pdfs[0]).parent))
        if not out_dir:
            return
        self.settings.setValue("last_import_dir", str(Path(pdfs[0]).parent))
        self.statusBar().showMessage(f"Converting {len(pdfs)} PDF(s) in the background…", 5000)
        convert_files_in_background(self.pdf_service, pdfs, out_dir)

    def inbox_settings(self) -> None:
        InboxDialog(self.books, self.inbox_service, self).exec()

    def process_all_bills(self) -> None:
        from .dump_dialog import process_all_bills

        process_all_bills(self.books, self)
        self.on_accounts_changed()
        self.docs_tab.refresh()

    def save_trial_balance(self) -> None:
        """Save the trial balance (as of the Financial Statements tab's end date, today by default) to Excel."""
        self.tabs.setCurrentWidget(self.reports_tab)
        self.reports_tab.kind.setCurrentText("Trial Balance")
        path = self.reports_tab.save_trial_balance()
        if path:
            self.statusBar().showMessage(f"Trial balance saved: {path}", 10000)

    def cheques_drafts(self) -> None:
        from .cheques_dialog import ChequesDialog

        ChequesDialog(self.books, self).exec()
        self.on_accounts_changed()

    def etransfer_history(self) -> None:
        from .etransfer_dialog import ETransferDialog

        ETransferDialog(self.books, self).exec()
        self.on_accounts_changed()

    def invoice_settings(self) -> None:
        from .invoice_generator import CompanyInvoiceSettingsDialog

        CompanyInvoiceSettingsDialog(self.books, self).exec()

    def invoice_dump(self) -> None:
        from .dump_dialog import InvoiceDumpDialog

        InvoiceDumpDialog(self.books, self).exec()
        self.tx_tab.refresh()
        self.dashboard_tab.refresh()

    def on_inbox_event(self, event) -> None:
        if event.note:  # Invoice Dump result
            self.statusBar().showMessage(event.note if event.company_path and self._is_current(event.company_path)
                                         else event.describe(), 20000)
            if event.company_path and self._is_current(event.company_path):
                self.tx_tab.refresh()
                self.dashboard_tab.refresh()
            return
        self.statusBar().showMessage(("Inbox: " if event.ok else "Inbox problem — ") + event.describe(), 20000)
        if event.company_path and self._is_current(event.company_path) and event.imported:
            self.on_accounts_changed()
            if self.review_panel.total():
                self.review_dock.show()

    def pdf_watch_settings(self) -> None:
        PdfWatchDialog(self.pdf_service, self).exec()

    def on_pdf_converted(self, event) -> None:
        self.statusBar().showMessage(("PDF → Excel: " if event.ok else "PDF → Excel ") + describe(event), 15000)

    def rule_from_selection(self) -> None:
        ids = self.tx_tab.selected_ids()
        pattern = ""
        if ids:
            tx = self.books.transaction(ids[0])
            pattern = tx.merchant_key.lower() or tx.description
        self.tabs.setCurrentWidget(self.rules_tab)
        self.rules_tab.new_rule(pattern)

    def open_report_from_dashboard(self, spec: str) -> None:
        from datetime import timedelta

        name, _, days = spec.partition("|")
        d = self.dashboard_tab.data
        end = d.as_of
        start = end - timedelta(days=int(days) - 1) if days else end.replace(day=1)
        self.tabs.setCurrentWidget(self.reports_tab)
        self.reports_tab.show_report(name, start, end)

    def open_documents(self, spec: str) -> None:
        new, _, kind = spec.rpartition(":")
        self.tabs.setCurrentWidget(self.docs_tab)
        self.docs_tab.show_kind(kind)
        if new:
            self.docs_tab.new_document(kind)

    def review_from_dashboard(self) -> None:
        if self.review_panel.total():
            self.review_dock.show()
            self.review_dock.raise_()
            return
        d = self.dashboard_tab.data
        status = self.tx_tab.status_filter
        status.setCurrentIndex(status.findData("uncategorized" if d and d.uncategorized else "unreviewed"))
        self.tabs.setCurrentWidget(self.tx_tab)

    def create_sample_company(self) -> None:
        from ..demo import SAMPLE_NAME, create_sample_company

        if self.books.company_name == SAMPLE_NAME:
            error(self, "Sample company is open", "Switch to another company first; the sample is rebuilt fresh.")
            return
        path = create_sample_company(self.registry)
        self.switch_books(str(path))
        self.tabs.setCurrentWidget(self.dashboard_tab)

    def show_contact_transactions(self, name: str) -> None:
        self.tabs.setCurrentWidget(self.tx_tab)
        self.tx_tab.status_filter.setCurrentIndex(self.tx_tab.status_filter.findData("all"))
        self.tx_tab.refresh()
        i = self.tx_tab.name_filter.findData(name)
        if i < 0:
            self.statusBar().showMessage(f"No bank transactions are named {name} yet.", 8000)
        self.tx_tab.name_filter.setCurrentIndex(max(0, i))

    def arap_new(self, kind: str) -> None:
        self.docs_tab.show_kind(kind)
        self.docs_tab.new_document(kind)
        self.arap_tab.refresh()

    def arap_open(self, kind: str, doc_id: int) -> None:
        self.docs_tab.show_kind(kind)
        self.docs_tab.edit_document(doc_id)
        self.arap_tab.refresh()

    def on_data_changed(self) -> None:
        """Something changed: refresh what's on screen now; the other tabs refresh when they're opened (refreshing
        every tab after each change made categorizing slow in big company files)."""
        sender = self.sender()
        if sender is not self.review_panel:
            if self.review_dock.isVisible():
                self.review_panel.refresh()
            else:
                self._review_stale = True
        stale = self.__dict__.setdefault("_stale", set())
        for tab in (self.dashboard_tab, self.arap_tab, self.tx_tab, self.accounts_tab):
            if not (tab is self.arap_tab and sender is self.arap_tab):
                stale.add(tab)
        self._refresh_if_stale(self.tabs.currentWidget())

    def _refresh_if_stale(self, w) -> None:
        stale = self.__dict__.setdefault("_stale", set())
        if w in stale:
            stale.discard(w)
            w.refresh()

    def _review_visibility(self, visible: bool) -> None:
        if visible and getattr(self, "_review_stale", False):
            self._review_stale = False
            self.review_panel.refresh()

    def on_accounts_changed(self) -> None:
        self.review_panel.refresh()
        self.dashboard_tab.refresh()
        self.tx_tab.reload_accounts()
        self.accounts_tab.refresh()
        self.rules_tab.refresh()
        self.reports_tab.refresh_presets()

    def on_tab_changed(self, _index: int) -> None:
        w = self.tabs.currentWidget()
        if w is self.tx_tab:
            self._refresh_if_stale(w)
            return
        self.__dict__.setdefault("_stale", set()).discard(w)  # each tab below refreshes as it's opened
        if w is self.dashboard_tab:
            self.dashboard_tab.refresh()
        elif w is self.docs_tab:
            self.docs_tab.refresh()
        elif w is self.arap_tab:
            self.arap_tab.refresh()
        elif w is self.contacts_tab:
            self.contacts_tab.refresh()
        elif w is self.reports_tab:
            self.reports_tab.run()
        elif w is self.accounts_tab:
            self.accounts_tab.refresh()

    def set_appearance(self, mode: str) -> None:
        from . import theme

        theme.set_mode(mode)
        self.refresh_appearance()

    def _system_scheme_changed(self, *_args) -> None:
        from . import theme

        if theme.mode() == "system":
            theme.apply()
            self.refresh_appearance()

    def refresh_appearance(self) -> None:
        """Repaint everything that uses meaning colours after a light/dark change."""
        from PySide6.QtWidgets import QApplication, QCheckBox, QRadioButton

        bar = getattr(self, "company_bar", None)
        if bar is not None:  # widgets under a style sheet keep the old text colour until re-polished
            bar.setStyleSheet(bar.styleSheet())
        pal = QApplication.palette()
        for kind in (QLabel, QCheckBox, QRadioButton):
            for w in self.findChildren(kind):
                w.setPalette(pal)
        if self.asset_view is not None:
            self.asset_view.refresh()
        for refresh in (self.dashboard_tab.refresh, self.review_panel.refresh, self.tx_tab.refresh,
                        self.docs_tab.refresh, self.accounts_tab.refresh, self.reports_tab.run):
            try:
                refresh()
            except Exception:  # noqa: BLE001 - a repaint problem must never break the app
                pass

    def about(self) -> None:
        QMessageBox.about(self, "About AM Accounting",
                          f"<b>AM Accounting {__version__}</b><p>Offline bookkeeping. Import bank statements "
                          "(CSV, Excel, OFX/QFX, PDF), categorize transactions, and produce an income statement, "
                          "balance sheet and cash-flow statement.</p><p>Your data stays on this computer in a single "
                          f"company file:<br><code>{self.books.path}</code></p>")

    def closeEvent(self, event) -> None:
        self.shutdown_services()
        if self.books.path != ":memory:":
            self.settings.setValue("last_books", self.books.path)
        self.books.close()
        super().closeEvent(event)

    # -- Accounting / Asset Management ---------------------------------------------

    def portfolio_path(self) -> Path:
        return Path(self.registry.registry_file).parent / "portfolio.ampf"

    def set_mode(self, mode: str) -> None:
        if mode == "assets":
            if self.asset_view is None:
                from .asset_mgmt import AssetManagementView

                self.asset_view = AssetManagementView(
                    self.portfolio_path(), self._company_choices,
                    create_company=self._create_company_for_holding)
                self.asset_view.open_company.connect(self.open_company_books)
                self.asset_view.open_loans.connect(self.open_company_loans)
                self.stack.addWidget(self.asset_view)
            self._dock_was_visible = self.review_dock.isVisible()
            self.review_dock.hide()
            self.asset_view.refresh()
            self.stack.setCurrentWidget(self.asset_view)
        else:
            self.stack.setCurrentWidget(self.accounting_page)
            if getattr(self, "_dock_was_visible", False):
                self.review_dock.show()
        self.mode = mode
        self.mode_tabs.blockSignals(True)
        self.mode_tabs.setCurrentIndex(0 if mode == "assets" else 1)
        self.mode_tabs.blockSignals(False)
        self.settings.setValue("mode", mode)
        if mode == "assets":
            self._focus_asset_view()
        self.update_title()

    def _focus_asset_view(self) -> None:
        from .. import company_roles

        if self.asset_view is None or self.books.path == ":memory:":
            return
        note = self.asset_view.focus_company(str(self.books.path), company_roles.role_of(self.books))
        if note:
            self.statusBar().showMessage(note, 8000)

    def _company_choices(self) -> list[tuple[str, str, str]]:
        """(name, path, role) of every company: role = operating | holding | asset_manager."""
        from .. import company_roles

        out = []
        for e in self.registry.companies():
            if not e.exists:
                continue
            out.append((e.name, str(e.path), company_roles.read_role(e.path)))
        return out

    def _create_company_for_holding(self, name: str) -> str:
        """A new company file for a holding (from Asset Management); it appears in the company switcher."""
        path = self.registry.create(CompanyProfile(name, role="operating"))
        self._fill_switcher()
        return str(path)

    def _update_role_bar(self) -> None:
        """Holding companies and asset managers: say so, and link to their work in Asset Management."""
        from .. import company_roles

        if not hasattr(self, "role_bar"):
            return
        role = company_roles.role_of(self.books) if self.books.path != ":memory:" else "operating"
        name = self.books.company_name
        if role == "holding":
            self.role_text.setText(f"<b>{name}</b> is a <b>holding company</b> — it holds shares of operating "
                                   f"companies. Record money it sends to / receives from them here; its shareholdings, "
                                   f"values and returns are in Asset Management.")
            self.role_btn.setText("Open Shareholdings")
        elif role == "asset_manager":
            self.role_text.setText(f"<b>{name}</b> is an <b>asset manager</b> — assigned to manage operating "
                                   f"companies for a fee. Its managed companies, assets under management and fee "
                                   f"billing are in Asset Management.")
            self.role_btn.setText("Open Managed Companies")
        self.role_bar.setVisible(role in ("holding", "asset_manager"))
        self._role = role

    def _open_role_view(self) -> None:
        self.set_mode("assets")

    def toggle_mode(self) -> None:
        self.set_mode("assets" if getattr(self, "mode", "accounting") == "accounting" else "accounting")

    def open_company_books(self, path: str) -> None:
        """From a holding: switch to Accounting with that company's books."""
        if not Path(path).exists():
            error(self, "Company file not found", path)
            return
        self.set_mode("accounting")
        self.switch_books(path)
        self._fill_switcher()

    def open_company_loans(self, path: str) -> None:
        """From Financing & Cap Table: that company's books, Transactions, and its Loans window."""
        self.open_company_books(path)
        if self._is_current(path):
            self.tabs.setCurrentWidget(self.tx_tab)
            self.tx_tab.loan_payments()

    def shutdown_services(self) -> None:
        """Stop background work and signal connections (on close, and before the app quits)."""
        if getattr(self, "_shut_down", False):
            return
        self._shut_down = True
        try:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.styleHints().colorSchemeChanged.disconnect(self._system_scheme_changed)
        except (AttributeError, RuntimeError, TypeError):
            pass
        self.autosave.timer.stop()
        self.pdf_service.stop()
        self.inbox_service.stop()
        if self.asset_view is not None:
            self.asset_view.close_portfolio()

