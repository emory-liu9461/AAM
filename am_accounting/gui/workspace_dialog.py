"""Company folder & automatic saving of financial statements."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QSpinBox,
                               QVBoxLayout)

from .. import workspace as ws
from ..books import Books
from ..inbox import detect_cloud_folders
from .common import error


def pdf_writer(html: str, path: str) -> None:
    from .reports_tab import write_pdf

    write_pdf(html, path)


class WorkspaceDialog(QDialog):
    """Pick the company folder; choose what's saved automatically and how often."""

    def __init__(self, books: Books, parent=None, *, first_time: bool = False):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Company Folder & Automatic Saving")
        self.resize(680, 0)
        cfg = ws.get_config(books)
        lay = QVBoxLayout(self)
        intro = QLabel(
            ("<b>Choose a folder for " + books.company_name.rstrip(".") + ".</b> " if first_time else "") +
            "AM Accounting keeps everything for this company in one folder — on this computer or in OneDrive — "
            "and creates these folders inside it:")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        row = QHBoxLayout()
        self.root = QLineEdit(cfg.root)
        self.root.setPlaceholderText("Choose a folder")
        row.addWidget(self.root, 1)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        cloud = QPushButton("Use OneDrive")
        menu = QMenu(self)
        found = detect_cloud_folders()
        if not found:
            menu.addAction("No OneDrive or cloud folder found on this computer").setEnabled(False)
        docs = Path.home() / "Documents"
        for label, base in found + [("This computer (Documents)", docs)]:
            target = ws.suggested_root(base, books.company_name)
            menu.addAction(f"{label} — {target}").triggered.connect(
                lambda _=False, t=target: self.root.setText(str(t)))
        cloud.setMenu(menu)
        row.addWidget(cloud)
        lay.addLayout(row)

        tree = QLabel(
            f"📁 <b>{ws.BANK_DIR}</b> — drop bank statements here (a subfolder per bank account); imported "
            "files are renamed and filed, and PDFs are added to one Excel workbook<br>"
            f"📁 <b>{ws.DOCS_DIR}</b> — invoice and bill files, sorted Open / Paid<br>"
            f"📁 <b>{ws.REPORTS_DIR}</b> — financial statements saved automatically (below)<br>"
            "📁 <b>Trial Balance</b> — a trial balance Excel workbook for each period end (below)<br>"
            f"📁 <b>{ws.BACKUP_DIR}</b> — automatic copies of your books<br>"
            "📁 <b>Cheques &amp; Drafts</b> — drop cheque and bank draft scans or photos; each is matched to its "
            "bank line and renamed with the date, number and payee<br>"
            "📁 <b>E-Transfer History</b> — drop e-Transfer history screenshots, PDFs or CSVs; each transfer is "
            "matched to its bank line and named<br>"
            "📁 <b>Invoice Dump</b> — drop invoices, bills and receipts; each is matched to its bank transaction "
            "(unmatched ones can be marked <i>Future Payable</i> or <i>Not Related</i>)<br>"
            "📁 <b>Saved Invoices</b> — matched and recorded invoices, renamed with the transaction date and "
            "invoice number<br>"
            "Each has an <b>Archive</b> folder for older items (below).")
        tree.setObjectName("hint")
        tree.setWordWrap(True)
        lay.addWidget(tree)
        self.std_tree = tree
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("Folder layout:"))
        self.layout_box = QComboBox()
        for k, v in ws.LAYOUTS.items():
            self.layout_box.addItem(v, k)
        self.layout_box.setCurrentIndex(max(0, self.layout_box.findData(cfg.layout)))
        self.layout_box.setToolTip("Deal folder standard: the folder is a deal's Asset Management → Accounting "
                                   "(set up from the deal's Deal Folder tab)")
        lrow.addWidget(self.layout_box)
        lrow.addStretch(1)
        lay.addLayout(lrow)
        self.deal_tree = QLabel(
            "📁 <b>Banking/Monthly Statements</b> — drop bank statements here (a subfolder per bank account)<br>"
            "📁 <b>Financial Statements</b> — Income Statement, Balance Sheet, Cash Flow and Trial Balance: the "
            "latest outside, older ones in each one's Archive<br>"
            "📁 <b>Invoices/Operating Invoices</b> — bills by vendor (“Enbridge Invoices”): the last 12 months, older "
            "ones Archived, named “## Street Name, Vendor, Month, Invoice”<br>"
            "📁 <b>QuickBooks File</b> — the latest copy of the books; backups in its Archive<br>"
            "📁 <b>Taxes</b> — the year-end package · 📁 <b>Banking</b> — cheques &amp; drafts, e-Transfer history · "
            "📁 <b>Invoices</b> — the invoice dump and saved invoices")
        self.deal_tree.setObjectName("hint")
        self.deal_tree.setWordWrap(True)
        lay.addWidget(self.deal_tree)
        self.layout_box.currentIndexChanged.connect(self._layout_changed)
        self._layout_changed()

        box = QGroupBox("Save financial statements automatically")
        form = QFormLayout(box)
        self.freq = QComboBox()
        for k, v in ws.FREQUENCIES.items():
            self.freq.addItem(v, k)
        self.freq.setCurrentIndex(self.freq.findData(cfg.frequency))
        form.addRow("How often:", self.freq)
        rep_row = QHBoxLayout()
        self.report_boxes = {}
        for k, v in ws.REPORT_CHOICES.items():
            cb = QCheckBox(v)
            cb.setChecked(k in cfg.reports)
            self.report_boxes[k] = cb
            rep_row.addWidget(cb)
        rep_row.addStretch(1)
        form.addRow("Statements:", rep_row)
        fmt_row = QHBoxLayout()
        self.format_boxes = {}
        for k, v in ws.FORMATS.items():
            cb = QCheckBox(v)
            cb.setChecked(k in cfg.formats)
            self.format_boxes[k] = cb
            fmt_row.addWidget(cb)
        fmt_row.addStretch(1)
        form.addRow("Formats:", fmt_row)
        self.ytd = QCheckBox("Also save year-to-date income statement and cash flow")
        self.ytd.setChecked(cfg.year_to_date)
        form.addRow("", self.ytd)
        self.tb = QCheckBox("Save the trial balance as an Excel workbook in the Trial Balance folder")
        self.tb.setToolTip("Trial Balance/<year>/Trial Balance - 31 Oct 2026.xlsx — debits and credits by account, "
                           "a working trial balance with opening, activity and closing, and totals by type")
        self.tb.setChecked(cfg.trial_balance)
        form.addRow("", self.tb)
        backup_row = QHBoxLayout()
        self.backups = QCheckBox("Back up the books each time, keeping the last")
        self.backups.setChecked(cfg.backups)
        self.keep = QSpinBox()
        self.keep.setRange(1, 999)
        self.keep.setValue(cfg.keep_backups)
        backup_row.addWidget(self.backups)
        backup_row.addWidget(self.keep)
        backup_row.addWidget(QLabel("copies"))
        backup_row.addStretch(1)
        form.addRow("", backup_row)
        self.example = QLabel()
        self.example.setObjectName("hint")
        self.example.setWordWrap(True)
        form.addRow(self.example)
        lay.addWidget(box)

        abox = QGroupBox("Archive")
        aform = QFormLayout(abox)
        arow = QHBoxLayout()
        arow.addWidget(QLabel("Move items older than"))
        self.archive_months = QSpinBox()
        self.archive_months.setRange(0, 120)
        self.archive_months.setValue(cfg.archive_months)
        self.archive_months.setSpecialValueText("never")
        arow.addWidget(self.archive_months)
        arow.addWidget(QLabel("months into each folder's Archive"))
        arow.addStretch(1)
        aform.addRow(arow)
        ahint = QLabel("Applies to filed bank statements, paid invoices and bills, saved financial statements and "
                       "backups — keeping their year/month folders. Open invoices and bills are never archived. "
                       "Set to 0 to never archive.")
        ahint.setObjectName("hint")
        ahint.setWordWrap(True)
        aform.addRow(ahint)
        lay.addWidget(abox)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        save = buttons.addButton("Save", QDialogButtonBox.ButtonRole.AcceptRole)
        save.setObjectName("primary")
        now = buttons.addButton("Save Statements Now", QDialogButtonBox.ButtonRole.ActionRole)
        now.clicked.connect(self.save_now)
        opener = buttons.addButton("Open Folder", QDialogButtonBox.ButtonRole.ActionRole)
        opener.clicked.connect(self._open)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.freq.currentIndexChanged.connect(self._example)
        self._example()
        self.adjustSize()

    def _draft(self) -> ws.WorkspaceConfig:
        return ws.WorkspaceConfig(self.root.text().strip(), self.freq.currentData(),
                                  [k for k, cb in self.report_boxes.items() if cb.isChecked()],
                                  [k for k, cb in self.format_boxes.items() if cb.isChecked()],
                                  self.ytd.isChecked(), self.backups.isChecked(), self.keep.value(),
                                  self.archive_months.value(), self.tb.isChecked(),
                                  self.layout_box.currentData() or "standard")

    def _layout_changed(self, *_) -> None:
        deal = self.layout_box.currentData() == "deal"
        self.std_tree.setVisible(not deal)
        self.deal_tree.setVisible(deal)
        if hasattr(self, "example"):
            self._example()

    def _example(self) -> None:
        freq = self.freq.currentData()
        if freq == "off":
            self.example.setText("Statements are only saved when you click “Save Statements Now”.")
            return
        p = ws.completed_periods(freq, date.today())[-1]
        where = (f"Financial Statements/Income Statement/Income Statement - {p.label}.pdf (the one before moves to "
                 f"its Archive)" if getattr(self, "layout_box", None) is not None
                 and self.layout_box.currentData() == "deal" else
                 f"{ws.REPORTS_DIR}/{p.end.year}/{p.folder}/Income Statement - {p.label}.pdf")
        self.example.setText(f"Example: {where} "
                             f"— saved once each period ends; anything missed while the app was closed is saved "
                             f"the next time it's open.")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Company folder", self.root.text() or str(Path.home()))
        if d:
            self.root.setText(d)

    def _open(self) -> None:
        if self.root.text().strip():
            Path(self.root.text()).expanduser().mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.root.text()).expanduser())))

    def save(self) -> bool:
        cfg = self._draft()
        if not cfg.enabled:
            error(self, "Choose a folder", "Choose the company folder first.")
            return False
        if not cfg.reports and not cfg.trial_balance and cfg.frequency != "off":
            error(self, "Choose statements", "Tick at least one statement to save, or choose “Don't save "
                                             "automatically”.")
            return False
        if not cfg.formats:
            error(self, "Choose a format", "Tick PDF, Excel or both.")
            return False
        try:
            ws.set_config(self.books, cfg)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not set up the folder", str(e))
            return False
        self._file_existing_invoices()
        self.accept()
        return True

    def _file_existing_invoices(self) -> None:
        """Create PDFs in Invoices & Bills for invoices that don't have a file yet."""
        from datetime import date as _date

        from .. import doc_folder as df
        from .. import receivables as rx
        from .doc_files import generate_invoice_pdf

        if not df.get_config(self.books).make_pdf:
            return
        for d in rx.documents(self.books, "invoice", status="all", as_of=_date(9999, 12, 31)):
            if not df.files_for(self.books, d.id):
                try:
                    generate_invoice_pdf(self.books, d.id)
                except Exception:  # noqa: BLE001 - a PDF problem mustn't stop the setup
                    pass

    def save_now(self) -> None:
        cfg = self._draft()
        if not cfg.enabled:
            error(self, "Choose a folder", "Choose the company folder first.")
            return
        try:
            ws.set_config(self.books, cfg)
            freq = cfg.frequency if cfg.frequency != "off" else "monthly"
            period = ws.completed_periods(freq, date.today())[-1]
            result = ws.save_period(self.books, period, pdf_writer=pdf_writer, cfg=cfg)
            if cfg.backups:
                result.backup = ws.backup_books(self.books, cfg)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save", str(e))
            return
        msg = f"Saved {len(result.files)} file(s) for {period.label}."
        if result.errors:
            msg += "\n\n" + "\n".join(result.errors)
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.information(self, "Statements saved", msg)


class AutoSaveService(QObject):
    """Checks every half hour (and shortly after start) whether statements are due, and saves them."""

    saved = Signal(object)  # list[SaveResult]

    def __init__(self, get_books, parent=None):
        super().__init__(parent)
        self.get_books = get_books
        self.timer = QTimer(self)
        self.timer.setInterval(30 * 60 * 1000)
        self.timer.timeout.connect(self.check)
        self.timer.start()
        QTimer.singleShot(5000, self.check)

    def check(self) -> list:
        try:
            books = self.get_books()
            results = ws.run_schedule(books, pdf_writer=pdf_writer)
            ws.archive_old(books)  # anything now older than the archive age moves to Archive/
        except Exception:  # noqa: BLE001 - never interrupt the user over a background save
            return []
        if results:
            self.saved.emit(results)
        return results
