"""Invoice generator window: build an itemized invoice, preview it, save it as a PDF and send it."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout)

from .. import invoicing as inv
from ..books import Books
from ..companies import safe_filename
from ..money import fmt_money
from .common import error, fill_account_combo
from .theme import PAPER_CSS

TERMS = [("Due on receipt", 0), ("Net 7", 7), ("Net 15", 15), ("Net 30", 30), ("Net 45", 45), ("Net 60", 60),
         ("Net 90", 90)]
COLS = ["Description", "Qty", "Rate", "Income account", "Tax %", "Amount"]


def _qd(iso: str) -> QDate:
    d = date.fromisoformat(iso)
    return QDate(d.year, d.month, d.day)


def _iso(qd: QDate) -> str:
    return qd.toString("yyyy-MM-dd")


def _spin(decimals: int, maximum: float, *, prefix: str = "", suffix: str = "") -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setDecimals(decimals)
    s.setRange(0, maximum)
    s.setPrefix(prefix)
    s.setSuffix(suffix)
    s.setGroupSeparatorShown(True)
    s.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
    s.setFrame(False)
    return s


class CompanyInvoiceSettingsDialog(QDialog):
    """Your details printed on every invoice, plus tax and terms defaults."""

    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Invoice Settings")
        self.resize(520, 0)
        s = inv.get_settings(books)
        form = QFormLayout(self)
        form.addRow(QLabel(f"Printed on invoices under <b>{books.company_name}</b> "
                           f"(name, legal name and tax ID come from File → Company Settings)."))
        self.address = QPlainTextEdit(s.address)
        self.address.setFixedHeight(70)
        self.email = QLineEdit(s.email)
        self.phone = QLineEdit(s.phone)
        self.pay = QPlainTextEdit(s.payment_instructions)
        self.pay.setFixedHeight(70)
        self.pay.setPlaceholderText("e.g. e-Transfer to billing@yourco.ca, or wire to account …")
        self.footer = QLineEdit(s.footer)
        self.tax_label = QLineEdit(s.tax_label)
        self.tax_label.setPlaceholderText("Sales tax, HST, GST, VAT…")
        self.tax_rate = QDoubleSpinBox()
        self.tax_rate.setRange(0, 99.999)
        self.tax_rate.setDecimals(3)
        self.tax_rate.setSuffix(" %")
        self.tax_rate.setValue(s.default_tax_rate)
        self.terms = QSpinBox()
        self.terms.setRange(0, 365)
        self.terms.setSuffix(" days")
        self.terms.setValue(s.default_terms_days)
        for label, w in [("Address:", self.address), ("Email:", self.email), ("Phone:", self.phone),
                         ("How to pay:", self.pay), ("Footer:", self.footer), ("Tax name:", self.tax_label),
                         ("Default tax rate:", self.tax_rate), ("Default terms:", self.terms)]:
            form.addRow(label, w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        s = inv.InvoiceSettings(self.address.toPlainText(), self.email.text(), self.phone.text(),
                                self.pay.toPlainText(), self.footer.text(), self.tax_label.text() or "Sales tax",
                                self.tax_rate.value(), self.terms.value())
        try:
            inv.set_settings(self.books, s)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class InvoiceGeneratorDialog(QDialog):
    """Create or edit an itemized invoice. With ``deposit_tx_id`` the invoice is for money already received."""

    def __init__(self, books: Books, doc_id: int | None = None, *, deposit_tx_id: int | None = None,
                 customer: str = "", parent=None):
        super().__init__(parent)
        self.books = books
        self.deposit_tx_id = deposit_tx_id
        self.saved_id: int | None = None
        if doc_id is not None:
            self.invoice = inv.load_invoice(books, doc_id)
        elif deposit_tx_id is not None:
            self.invoice = inv.deposit_invoice(books, deposit_tx_id)
        else:
            self.invoice = inv.new_invoice(books, customer)
        self.setWindowTitle(f"Invoice {self.invoice.number}" if doc_id else "New Invoice")
        self.resize(980, 720)
        root = QVBoxLayout(self)

        if deposit_tx_id is not None:
            tx = books.transaction(deposit_tx_id)
            banner = QLabel(f"Invoice for money already received: <b>{fmt_money(tx.amount_cents)}</b> on {tx.date} "
                            f"({tx.description}). It will be marked paid by this deposit.")
            banner.setObjectName("hint")
            banner.setWordWrap(True)
            root.addWidget(banner)
        elif self.invoice.paid_cents:
            paid = QLabel(f"{fmt_money(self.invoice.paid_cents)} has been paid on this invoice so far.")
            paid.setObjectName("hint")
            root.addWidget(paid)

        # Bill to | invoice details
        top = QGridLayout()
        top.setHorizontalSpacing(24)
        bill = QFormLayout()
        self.customer = QComboBox()
        self.customer.setEditable(True)
        self.customer.addItems([c.name for c in inv.customers(books)])
        self.customer.setCurrentText(self.invoice.customer)
        self.customer.lineEdit().setPlaceholderText("Customer name")
        self.customer.activated.connect(self._customer_chosen)
        self.email = QLineEdit(self.invoice.customer_email)
        self.email.setPlaceholderText("billing@customer.com")
        self.address = QPlainTextEdit(self.invoice.customer_address)
        self.address.setPlaceholderText("Billing address")
        self.address.setFixedHeight(64)
        bill.addRow("Bill to:", self.customer)
        bill.addRow("Email:", self.email)
        bill.addRow("Address:", self.address)
        details = QFormLayout()
        self.number = QLineEdit(self.invoice.number)
        self.issue = QDateEdit(_qd(self.invoice.issue_date))
        self.issue.setCalendarPopup(True)
        self.issue.setDisplayFormat("MMM d, yyyy")
        self.terms = QComboBox()
        for name, days in TERMS:
            self.terms.addItem(name, days)
        self.terms.addItem("Custom date", None)
        self.due = QDateEdit(_qd(self.invoice.due_date))
        self.due.setCalendarPopup(True)
        self.due.setDisplayFormat("MMM d, yyyy")
        self.reference = QLineEdit(self.invoice.reference)
        self.reference.setPlaceholderText("Customer PO / job (optional)")
        details.addRow("Invoice #:", self.number)
        details.addRow("Date:", self.issue)
        details.addRow("Terms:", self.terms)
        details.addRow("Due:", self.due)
        details.addRow("Reference:", self.reference)
        from .control_account import ControlAccountPicker

        self.control = ControlAccountPicker(books, "invoice", self.invoice.control_id if doc_id else None)
        if not doc_id:
            self.control.follow_party(self.invoice.customer)
            self.customer.currentTextChanged.connect(self.control.follow_party)
        details.addRow("AR account:", self.control)
        top.addLayout(bill, 0, 0)
        top.addLayout(details, 0, 1)
        top.setColumnStretch(0, 3)
        top.setColumnStretch(1, 2)
        root.addLayout(top)

        # Lines
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c, w in [(1, 70), (2, 110), (3, 220), (4, 70), (5, 110)]:
            self.table.setColumnWidth(c, w)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.itemChanged.connect(lambda *_: self._recalc())
        root.addWidget(self.table, 1)
        line_btns = QHBoxLayout()
        add = QPushButton("+ Add Line")
        add.clicked.connect(lambda: self._add_line())
        remove = QPushButton("Remove Line")
        remove.clicked.connect(self._remove_line)
        line_btns.addWidget(add)
        line_btns.addWidget(remove)
        line_btns.addStretch(1)
        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        self.totals.setAlignment(Qt.AlignmentFlag.AlignRight)
        line_btns.addWidget(self.totals)
        root.addLayout(line_btns)

        notes_row = QHBoxLayout()
        self.notes = QPlainTextEdit(self.invoice.notes)
        self.notes.setPlaceholderText("Notes to the customer (optional) — e.g. project details, thank-you")
        self.notes.setFixedHeight(60)
        notes_row.addWidget(self.notes)
        root.addLayout(notes_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(sep)
        bottom = QHBoxLayout()
        settings = QPushButton("Invoice Settings…")
        settings.setToolTip("Your address, email, payment instructions, tax name and defaults")
        settings.clicked.connect(self.edit_settings)
        bottom.addWidget(settings)
        bottom.addStretch(1)
        preview = QPushButton("Preview")
        preview.clicked.connect(self.preview)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Save")
        save.clicked.connect(lambda: self.save_and(None))
        pdf = QPushButton("Save && Open PDF")
        pdf.clicked.connect(lambda: self.save_and("pdf"))
        email = QPushButton("Save && Email…")
        email.setObjectName("primary")
        email.setToolTip("Save, create the PDF and open an email to the customer (attach the PDF that's shown)")
        email.clicked.connect(lambda: self.save_and("email"))
        for b in (preview, cancel, save, pdf, email):
            bottom.addWidget(b)
        root.addLayout(bottom)

        for ln in self.invoice.lines:
            self._add_line(ln)
        days = (date.fromisoformat(self.invoice.due_date) - date.fromisoformat(self.invoice.issue_date)).days
        j = self.terms.findData(days)
        self.terms.setCurrentIndex(j if j >= 0 else self.terms.count() - 1)
        self.terms.currentIndexChanged.connect(self._apply_terms)
        self.issue.dateChanged.connect(self._apply_terms)
        self.due.dateChanged.connect(self._due_edited)
        self._recalc()

    # -- lines ----------------------------------------------------------------

    def _add_line(self, ln: inv.Line | None = None) -> None:
        if ln is None:
            s = inv.get_settings(self.books)
            prev = self._line(self.table.rowCount() - 1) if self.table.rowCount() else None
            ln = inv.Line("", 1, 0, prev.account_id if prev else inv.default_income_account(self.books),
                          prev.tax_rate if prev else s.default_tax_rate)
        r = self.table.rowCount()
        self.table.blockSignals(True)
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(ln.description))
        qty = _spin(2, 1e7)
        qty.setValue(ln.quantity)
        rate = _spin(2, 1e10, prefix="$ ")
        rate.setValue(ln.unit_cents / 100)
        acct = QComboBox()
        acct.setMaxVisibleItems(25)
        fill_account_combo(acct, self.books)
        i = acct.findData(ln.account_id)
        if i >= 0:
            acct.setCurrentIndex(i)
        tax = _spin(2, 99.99, suffix=" %")
        tax.setValue(ln.tax_rate)
        for c, w in [(1, qty), (2, rate), (3, acct), (4, tax)]:
            self.table.setCellWidget(r, c, w)
        for w in (qty, rate, tax):
            w.valueChanged.connect(lambda *_: self._recalc())
        amount = QTableWidgetItem()
        amount.setFlags(amount.flags() & ~Qt.ItemFlag.ItemIsEditable)
        amount.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.table.setItem(r, 5, amount)
        self.table.blockSignals(False)
        self._recalc()
        if not ln.description:
            self.table.setCurrentCell(r, 0)

    def _remove_line(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self._recalc()

    def _line(self, r: int) -> inv.Line:
        w = self.table.cellWidget
        item = self.table.item(r, 0)
        acct = w(r, 3).currentData()
        return inv.Line(item.text() if item else "", w(r, 1).value(), round(w(r, 2).value() * 100),
                        acct if isinstance(acct, int) else 0, w(r, 4).value())

    def lines(self) -> list[inv.Line]:
        return [self._line(r) for r in range(self.table.rowCount())]

    def _recalc(self) -> None:
        lines = self.lines()
        self.table.blockSignals(True)
        for r, ln in enumerate(lines):
            self.table.item(r, 5).setText(fmt_money(ln.amount_cents))
        self.table.blockSignals(False)
        t = inv.totals(lines)
        label = inv.get_settings(self.books).tax_label
        parts = [f"Subtotal {fmt_money(t.subtotal_cents)}"]
        parts += [f"{label} {inv.fmt_rate(r)} {fmt_money(x)}" for r, x in t.taxes]
        self.totals.setText(" · ".join(parts) + f" &nbsp; <b style='font-size:13pt'>Total "
                                                f"{fmt_money(t.total_cents)}</b>")

    # -- header -----------------------------------------------------------------

    def _customer_chosen(self) -> None:
        c = inv.customer(self.books, self.customer.currentText())
        if c:
            if c.email:
                self.email.setText(c.email)
            if c.address:
                self.address.setPlainText(c.address)

    def _apply_terms(self) -> None:
        days = self.terms.currentData()
        if days is not None:
            self.due.blockSignals(True)
            self.due.setDate(self.issue.date().addDays(days))
            self.due.blockSignals(False)

    def _due_edited(self) -> None:
        j = self.terms.findData(self.issue.date().daysTo(self.due.date()))
        self.terms.blockSignals(True)
        self.terms.setCurrentIndex(j if j >= 0 else self.terms.count() - 1)
        self.terms.blockSignals(False)

    def collect(self) -> inv.Invoice:
        i = self.invoice
        i.customer = self.customer.currentText().strip()
        i.customer_email = self.email.text()
        i.customer_address = self.address.toPlainText()
        i.number = self.number.text().strip()
        i.issue_date, i.due_date = _iso(self.issue.date()), _iso(self.due.date())
        i.reference = self.reference.text()
        i.notes = self.notes.toPlainText()
        i.lines = self.lines()
        i.control_id = self.control.value()
        return i

    # -- actions --------------------------------------------------------------

    def edit_settings(self) -> None:
        if CompanyInvoiceSettingsDialog(self.books, self).exec():
            self._recalc()

    def preview(self) -> None:
        """Show the invoice as it will print, without saving it."""
        draft = self.collect()
        if not draft.customer:
            error(self, "Customer", "Enter who the invoice is for.")
            return
        draft.lines = [ln for ln in draft.lines if ln.description.strip() or ln.unit_cents]
        html = inv.render_html(self.books, draft)
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Preview – Invoice {draft.number}")
        dlg.resize(760, 880)
        lay = QVBoxLayout(dlg)
        view = QTextBrowser()
        view.setStyleSheet(PAPER_CSS)
        view.setHtml(html)
        lay.addWidget(view)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(dlg.reject)
        lay.addWidget(close)
        self._preview = dlg
        dlg.exec()

    def save(self) -> int | None:
        i = self.collect()
        if not i.customer:
            error(self, "Customer", "Enter who the invoice is for.")
            return None
        try:
            if self.deposit_tx_id is not None and i.id is None:
                doc_id = inv.invoice_for_deposit(self.books, self.deposit_tx_id, i)
            else:
                doc_id = inv.save_invoice(self.books, i)
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't save the invoice", str(e))
            return None
        self.saved_id = doc_id
        from . import doc_files

        try:
            doc_files.sync(self.books, [doc_id])
        except Exception:  # noqa: BLE001 - filing problems mustn't lose the invoice
            pass
        return doc_id

    def pdf_path(self, doc_id: int, *, ask: bool = True) -> Path | None:
        """The invoice PDF: filed in Invoices & Bills when that folder is set up, otherwise saved where you choose."""
        from .. import doc_folder as df
        from . import doc_files
        from .reports_tab import write_pdf

        cfg = df.get_config(self.books)
        if cfg.enabled:
            return doc_files.generate_invoice_pdf(self.books, doc_id)
        name = safe_filename(f"Invoice {self.invoice.number} {self.invoice.customer}") + ".pdf"
        target = str(Path.home() / name)
        if ask:
            target, _ = QFileDialog.getSaveFileName(self, "Save invoice PDF", target, "PDF (*.pdf)")
            if not target:
                return None
        write_pdf(inv.invoice_html(self.books, doc_id), target)
        return Path(target)

    def save_and(self, then: str | None) -> None:
        doc_id = self.save()
        if doc_id is None:
            return
        if then in ("pdf", "email"):
            try:
                path = self.pdf_path(doc_id)
            except Exception as e:  # noqa: BLE001
                error(self, "Couldn't create the PDF", str(e))
                path = None
            if path and then == "pdf":
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            if path and then == "email":
                self.email_invoice(doc_id, path)
        self.accept()

    def email_invoice(self, doc_id: int, path: Path) -> None:
        """Open a new email to the customer and show the PDF's folder so it can be attached."""
        from urllib.parse import quote

        i = self.invoice
        t = i.totals
        company = self.books.company_name
        subject = f"Invoice {i.number} from {company}"
        body = (f"Hello,\n\nPlease find attached invoice {i.number} for {fmt_money(t.total_cents)}, "
                f"due {i.due_date}.\n\nThank you,\n{company}")
        QDesktopServices.openUrl(QUrl(f"mailto:{quote(i.customer_email)}?subject={quote(subject)}"
                                      f"&body={quote(body)}"))
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))
        inv.mark_sent(self.books, doc_id)
