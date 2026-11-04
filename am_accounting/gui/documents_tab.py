"""Invoices (accounts receivable) and bills (accounts payable)."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import doc_folder as df
from .. import receivables as rx
from . import doc_files as files
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, confirm, error, fill_account_combo

TERMS = [("Due on receipt", 0), ("Net 15", 15), ("Net 30", 30), ("Net 45", 45), ("Net 60", 60), ("Net 90", 90)]


def _qd(iso: str | date) -> QDate:
    d = date.fromisoformat(iso) if isinstance(iso, str) else iso
    return QDate(d.year, d.month, d.day)


def _iso(qd: QDate) -> str:
    return qd.toString("yyyy-MM-dd")


def _money_box() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 1e11)
    box.setDecimals(2)
    box.setPrefix("$ ")
    box.setGroupSeparatorShown(True)
    return box


class DocumentDialog(QDialog):
    """Create or edit an invoice or a bill."""

    def __init__(self, books: Books, kind: str, doc: rx.Document | None = None, parent=None):
        super().__init__(parent)
        self.books, self.kind, self.doc = books, kind, doc
        label, party_label, _ = rx.KIND_LABELS[kind]
        self.setWindowTitle(f"Edit {label}" if doc else f"New {label}")
        self.resize(520, 0)
        form = QFormLayout(self)
        self.party = QComboBox()
        self.party.setEditable(True)
        from .. import contacts

        names = sorted({c.name for c in contacts.all_contacts(books, "customer" if kind == "invoice" else "vendor",
                                                              active_only=True)}
                       | {d.party for d in rx.documents(books, kind, status="all", as_of=date(9999, 12, 31))},
                       key=str.lower)
        self.party.addItems(names)
        self.party.setCurrentText(doc.party if doc else "")
        self.party.lineEdit().setPlaceholderText(f"{party_label} name")
        self.number = QLineEdit(doc.number if doc else rx.next_number(books, kind))
        self.number.setPlaceholderText("Invoice number" if kind == "invoice" else "Vendor's bill / reference number")
        self.issue = QDateEdit(_qd(doc.issue_date if doc else date.today()))
        self.issue.setCalendarPopup(True)
        self.terms = QComboBox()
        for name, days in TERMS:
            self.terms.addItem(name, days)
        self.terms.addItem("Custom date", None)
        self.due = QDateEdit(_qd(doc.due_date if doc else date.today() + timedelta(days=30)))
        self.due.setCalendarPopup(True)
        self.amount = _money_box()
        self.amount.setMinimum(0.01)
        self.amount.setValue(doc.amount_cents / 100 if doc else 0)
        self.category = QComboBox()
        self.category.setMaxVisibleItems(25)
        fill_account_combo(self.category, books)
        default = doc.category_id if doc else self._default_category()
        i = self.category.findData(default)
        if i >= 0:
            self.category.setCurrentIndex(i)
        from .control_account import ControlAccountPicker

        self.control = ControlAccountPicker(books, kind, doc.control_id if doc else None)
        if not doc:
            self.party.currentTextChanged.connect(self.control.follow_party)
            self.party.currentTextChanged.connect(self._usual_account)
        self.memo = QLineEdit(doc.memo if doc else "")
        self.memo.setPlaceholderText("Optional description")
        form.addRow(f"{party_label}:", self.party)
        form.addRow("Number:", self.number)
        form.addRow("Date:", self.issue)
        form.addRow("Terms:", self.terms)
        form.addRow("Due date:", self.due)
        form.addRow("Amount:", self.amount)
        form.addRow("Income account:" if kind == "invoice" else "Expense account:", self.category)
        form.addRow("Receivable account:" if kind == "invoice" else "Payable account:", self.control)
        form.addRow("Description:", self.memo)
        if doc and doc.paid_cents:
            form.addRow(QLabel(f"{fmt_money(doc.paid_cents)} has been paid so far."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Save" if doc else f"Create {label}", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        if doc:
            days = (date.fromisoformat(doc.due_date) - date.fromisoformat(doc.issue_date)).days
            j = self.terms.findData(days)
            self.terms.setCurrentIndex(j if j >= 0 else self.terms.count() - 1)
        else:
            self.terms.setCurrentIndex(self.terms.findData(30))
        self.terms.currentIndexChanged.connect(self._apply_terms)
        self.issue.dateChanged.connect(self._apply_terms)
        self.due.dateChanged.connect(self._due_edited)
        self._apply_terms()

    def _usual_account(self, party: str) -> None:
        """A contact's usual income / expense account becomes the default."""
        from .. import contacts

        c = contacts.find(self.books, party)
        acct = (c.income_id if self.kind == "invoice" else c.expense_id) if c else None
        if acct:
            i = self.category.findData(acct)
            if i >= 0:
                self.category.setCurrentIndex(i)

    def _default_category(self) -> int | None:
        for code in (("4100", "4000") if self.kind == "invoice" else ("6000",)):
            try:
                return self.books.account_by_code(code).id
            except KeyError:
                continue
        wanted = "income" if self.kind == "invoice" else "expense"
        return next((a.id for a in self.books.accounts(types=(wanted,), active_only=True)), None)

    def _apply_terms(self) -> None:
        days = self.terms.currentData()
        if days is not None:
            self.due.blockSignals(True)
            self.due.setDate(self.issue.date().addDays(days))
            self.due.blockSignals(False)

    def _due_edited(self) -> None:
        days = self.issue.date().daysTo(self.due.date())
        j = self.terms.findData(days)
        self.terms.blockSignals(True)
        self.terms.setCurrentIndex(j if j >= 0 else self.terms.count() - 1)
        self.terms.blockSignals(False)

    def save(self) -> None:
        cat = self.category.currentData()
        if cat in (None, "__sep__"):
            error(self, "Choose an account", "Choose the account this is for.")
            return
        args = dict(party=self.party.currentText(), issue_date=_iso(self.issue.date()),
                    due_date=_iso(self.due.date()), amount_cents=round(self.amount.value() * 100),
                    category_id=cat, number=self.number.text(), memo=self.memo.text(),
                    control_id=self.control.value())
        try:
            if self.doc:
                rx.update_document(self.books, self.doc.id, **args)
                self.saved_id = self.doc.id
            else:
                self.saved_id = rx.add_document(self.books, self.kind, **args)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save", str(e))
            return
        self.accept()


class PaymentDialog(QDialog):
    """Match bank transactions to an invoice or bill."""

    def __init__(self, books: Books, doc_id: int, parent=None):
        super().__init__(parent)
        self.books, self.doc_id = books, doc_id
        self.resize(860, 520)
        layout = QVBoxLayout(self)
        self.header = QLabel()
        self.header.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.header)
        self.existing_label = QLabel()
        layout.addWidget(self.existing_label)
        self.existing = QTableWidget(0, 4)
        self.existing.setHorizontalHeaderLabels(["Date", "Bank transaction", "Applied", ""])
        self.existing.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.existing.verticalHeader().setVisible(False)
        self.existing.setMaximumHeight(110)
        self.existing.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.existing)

        self.pick_label = QLabel()
        layout.addWidget(self.pick_label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Bank account", "Description", "Amount", "Unapplied", "Why"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._selected_changed)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addWidget(QLabel("Amount to apply:"))
        self.amount = _money_box()
        row.addWidget(self.amount)
        row.addStretch(1)
        self.apply_btn = QPushButton("Record Payment")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        close = QPushButton("Done")
        close.clicked.connect(self.accept)
        row.addWidget(self.apply_btn)
        row.addWidget(close)
        layout.addLayout(row)
        hint = QLabel("The bank transaction is categorized to Accounts Receivable/Payable, so the money isn't "
                      "counted as income or expense a second time.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.refresh()

    def refresh(self) -> None:
        d = rx.get_document(self.books, self.doc_id)
        label, party_label, _ = rx.KIND_LABELS[d.kind]
        self.setWindowTitle(f"Record Payment – {label} {d.number}".strip())
        self.header.setText(f"<b>{label} {d.number}</b> · {d.party} · total {fmt_money(d.amount_cents)} · "
                            f"due {d.due_date} · <b>balance {fmt_money(d.balance_cents)}</b>")
        pays = rx.payments_for(self.books, self.doc_id)
        self.existing_label.setText("Payments recorded:" if pays else "No payments recorded yet.")
        self.existing.setVisible(bool(pays))
        self.existing.setRowCount(len(pays))
        for r, (tx, amt) in enumerate(pays):
            self.existing.setItem(r, 0, QTableWidgetItem(tx.date))
            self.existing.setItem(r, 1, QTableWidgetItem(f"{tx.description} ({fmt_money(tx.amount_cents)})"))
            it = QTableWidgetItem(fmt_money(amt))
            it.setTextAlignment(ALIGN_RIGHT)
            self.existing.setItem(r, 2, it)
            rm = QPushButton("Remove")
            rm.clicked.connect(lambda _=False, t=tx.id: self.remove(t))
            self.existing.setCellWidget(r, 3, rm)

        self.suggestions = rx.suggest_payments(self.books, self.doc_id) if d.balance_cents > 0 else []
        money = "deposits" if d.kind == "invoice" else "payments out"
        self.pick_label.setText(f"Choose the bank transaction that paid it (recent {money}, best matches first):"
                                if d.balance_cents > 0 else "Fully paid.")
        self.table.setRowCount(len(self.suggestions))
        for r, (tx, free, why) in enumerate(self.suggestions):
            vals = [tx.date, self.books.account(tx.bank_account_id).name, tx.description,
                    fmt_money(tx.amount_cents, parens=False), fmt_money(free), why]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (3, 4):
                    it.setTextAlignment(ALIGN_RIGHT)
                if c == 5:
                    it.setForeground(POSITIVE if why else MUTED)
                it.setToolTip(v)
                self.table.setItem(r, c, it)
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(1, 140)
        self.table.setColumnWidth(3, 100)
        self.table.setColumnWidth(4, 100)
        self.table.setColumnWidth(5, 190)
        if self.suggestions:
            self.table.selectRow(0)
        self._selected_changed()

    def _selected_changed(self) -> None:
        d = rx.get_document(self.books, self.doc_id)
        rows = self.table.selectionModel().selectedRows()
        if rows and d.balance_cents > 0:
            _tx, free, _why = self.suggestions[rows[0].row()]
            self.amount.setMaximum(min(free, d.balance_cents) / 100)
            self.amount.setValue(min(free, d.balance_cents) / 100)
        self.apply_btn.setEnabled(bool(rows) and d.balance_cents > 0)

    def apply(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        tx, _free, _why = self.suggestions[rows[0].row()]
        try:
            rx.record_payment(self.books, self.doc_id, tx.id, round(self.amount.value() * 100))
        except Exception as e:  # noqa: BLE001
            error(self, "Could not record payment", str(e))
            return
        self.refresh()

    def remove(self, tx_id: int) -> None:
        rx.remove_payment(self.books, self.doc_id, tx_id)
        self.refresh()


class DocumentsTab(QWidget):
    changed = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        self.kind = "invoice"
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.kind_buttons = QButtonGroup(self)
        for i, (kind, text) in enumerate([("invoice", "Invoices (Receivable)"), ("bill", "Bills (Payable)")]):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _=False, k=kind: self.show_kind(k))
            self.kind_buttons.addButton(b, i)
            bar.addWidget(b)
        bar.addSpacing(12)
        bar.addWidget(QLabel("Show:"))
        self.status = QComboBox()
        for label, key in [("Open", "open"), ("Overdue", "overdue"), ("Paid", "paid"), ("All", "all")]:
            self.status.addItem(label, key)
        self.status.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.status)
        self.search = QLineEdit(placeholderText="Search name, number or description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        self.match_btn = QPushButton("Match Payments…")
        self.match_btn.setToolTip("Match bank deposits and payments to open invoices and bills")
        self.match_btn.clicked.connect(self.match_payments)
        bar.addWidget(self.match_btn)
        self.new_btn = QPushButton()
        self.new_btn.setObjectName("primary")
        self.new_btn.clicked.connect(lambda: self.new_document())
        bar.addWidget(self.new_btn)
        layout.addLayout(bar)

        # Folder strip: where files live, and files waiting to be filed
        folder_bar = QHBoxLayout()
        self.folder_label = QLabel()
        self.folder_label.setObjectName("hint")
        folder_bar.addWidget(self.folder_label, 1)
        self.unfiled_btn = QPushButton()
        self.unfiled_btn.clicked.connect(self.file_unfiled)
        folder_bar.addWidget(self.unfiled_btn)
        open_folder = QPushButton("Open Folder")
        open_folder.clicked.connect(self.open_folder)
        folder_bar.addWidget(open_folder)
        self.open_folder_btn = open_folder
        settings = QPushButton("Folder Settings…")
        settings.clicked.connect(self.folder_settings)
        folder_bar.addWidget(settings)
        layout.addLayout(folder_bar)

        self.table = QTableWidget(0, 11)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.edit_document)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        actions.addWidget(self.summary, 1)
        for label, slot in [("Record Payment…", self.record_payment), ("Files…", self.show_files),
                            ("Edit…", self.edit_document), ("Delete", self.delete_document)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            actions.addWidget(b)
        layout.addLayout(actions)
        self.show_kind("invoice")

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def show_kind(self, kind: str) -> None:
        self.kind = kind
        self.kind_buttons.button(0 if kind == "invoice" else 1).setChecked(True)
        label, party, _ = rx.KIND_LABELS[kind]
        self.new_btn.setText(f"New {label}…")
        self.table.setHorizontalHeaderLabels(["Number", party, "Date", "Due", "Amount", "Paid", "Balance", "Status",
                                              "Account", "AR account" if kind == "invoice" else "AP account",
                                              "Files"])
        self.refresh()

    def refresh(self) -> None:
        today = date.today()
        files_by_doc: dict[int, int] = {}
        for f in df.files_for(self.books):
            files_by_doc[f.document_id] = files_by_doc.get(f.document_id, 0) + 1

        def files_text(doc_id: int) -> str:
            n = files_by_doc.get(doc_id, 0)
            return f"📎 {n}" if n else ""

        cfg = df.get_config(self.books)
        if cfg.enabled:
            parts = cfg.root.parts
            short = ("…/" if len(parts) > 3 else "") + "/".join(parts[-3:])
            self.folder_label.setText(f"📁 Files are kept in {short} — sorted {df.LAYOUTS[cfg.layout].lower()}")
            self.folder_label.setToolTip(str(cfg.root))
        else:
            self.folder_label.setText("📁 Choose a folder (on this computer or OneDrive) to keep invoice and bill "
                                      "files organized →")
        n_unfiled = len(df.unfiled(self.books))
        self.unfiled_btn.setText(f"File {n_unfiled} New File{'s' * (n_unfiled != 1)}…")
        self.unfiled_btn.setVisible(n_unfiled > 0)
        self.open_folder_btn.setEnabled(cfg.enabled)
        n_match = len(rx.find_matches(self.books))
        self.match_btn.setText(f"Match Payments ({n_match})…" if n_match else "Match Payments…")
        docs = rx.documents(self.books, self.kind, status=self.status.currentData(), as_of=date(9999, 12, 31),
                            search=self.search.text().strip())
        self.table.setRowCount(len(docs))
        for r, d in enumerate(docs):
            status = d.status(today)
            vals = [d.number, d.party, d.issue_date, d.due_date, fmt_money(d.amount_cents),
                    fmt_money(d.paid_cents) if d.paid_cents else "", fmt_money(d.balance_cents), status,
                    self.books.account(d.category_id).name, self.books.account(d.control_id).name,
                    files_text(d.id)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, d.id)
                if 4 <= c <= 6:
                    it.setTextAlignment(ALIGN_RIGHT)
                if c == 7:
                    it.setForeground(NEGATIVE if status.startswith("Overdue") else
                                     POSITIVE if status == "Paid" else MUTED)
                self.table.setItem(r, c, it)
        for c, w in enumerate([95, 0, 95, 95, 110, 110, 110, 115, 160, 170, 60]):
            if w:
                self.table.setColumnWidth(c, w)
        s = rx.aging(self.books, self.kind, today)
        noun = "invoices" if self.kind == "invoice" else "bills"
        self.summary.setText(f"{s.open_count} open {noun} · {fmt_money(s.open_total)} outstanding · "
                             f"{fmt_money(s.overdue_total)} overdue · "
                             f"{rx.KIND_LABELS[self.kind][2]} balance {fmt_money(s.ledger_balance)}")

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole) if row >= 0 else None

    def _done(self) -> None:
        self.refresh()
        self.changed.emit()

    def new_document(self, kind: str | None = None) -> int | None:
        if kind and kind != self.kind:
            self.show_kind(kind)
        if self.kind == "invoice":
            from .invoice_generator import InvoiceGeneratorDialog

            dlg = InvoiceGeneratorDialog(self.books, parent=self)
        else:
            dlg = DocumentDialog(self.books, self.kind, parent=self)
        if dlg.exec():
            files.sync(self.books, [dlg.saved_id])
            self._done()
            return dlg.saved_id
        return None

    def edit_document(self, doc_id: int | None = None) -> None:
        doc_id = doc_id if isinstance(doc_id, int) else self._selected_id()
        if doc_id and self.kind == "invoice":
            from .invoice_generator import InvoiceGeneratorDialog

            if InvoiceGeneratorDialog(self.books, doc_id, parent=self).exec():
                self._done()
            return
        if doc_id and DocumentDialog(self.books, self.kind, rx.get_document(self.books, doc_id), self).exec():
            files.sync(self.books, [doc_id])
            self._done()

    def record_payment(self) -> None:
        doc_id = self._selected_id()
        if doc_id is None:
            error(self, "Select one", f"Select the {rx.KIND_LABELS[self.kind][0].lower()} that was paid.")
            return
        PaymentDialog(self.books, doc_id, self).exec()
        files.sync(self.books, [doc_id])
        self._done()

    def delete_document(self) -> None:
        doc_id = self._selected_id()
        if doc_id is None:
            return
        d = rx.get_document(self.books, doc_id)
        label = rx.KIND_LABELS[d.kind][0].lower()
        extra = " Its recorded payments will go back to uncategorized." if d.paid_cents else ""
        if confirm(self, f"Delete {label}", f"Delete {label} {d.number} for {d.party} "
                                            f"({fmt_money(d.amount_cents)})?{extra}"):
            df.release_files(self.books, doc_id)
            rx.delete_document(self.books, doc_id)
            self._done()

    # -- matching & folder --------------------------------------------------

    def match_payments(self) -> None:
        dlg = files.MatchDialog(self.books, self)
        dlg.exec()
        if dlg.matched:
            self.window().statusBar().showMessage(f"Matched {dlg.matched} payment(s) to invoices/bills.", 8000)
        self._done()

    def show_files(self) -> None:
        doc_id = self._selected_id()
        if doc_id is None:
            return
        if not df.get_config(self.books).enabled:
            self.folder_settings()
            if not df.get_config(self.books).enabled:
                return
        files.FilesDialog(self.books, doc_id, self).exec()
        self._done()

    def folder_settings(self) -> None:
        dlg = files.FolderSettingsDialog(self.books, self)
        if dlg.exec():
            moved = len(getattr(dlg, "moves", []))
            self.window().statusBar().showMessage(f"Invoice & bill folder saved; {moved} file(s) re-sorted.", 8000)
            self._done()

    def open_folder(self) -> None:
        cfg = df.get_config(self.books)
        if cfg.enabled:
            df.ensure_folders(cfg)
            files.open_path(cfg.root)

    def file_unfiled(self) -> None:
        files.UnfiledDialog(self.books, self, new_document_cb=self.new_document).exec()
        self._done()
