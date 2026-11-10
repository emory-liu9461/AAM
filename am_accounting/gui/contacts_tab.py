"""Customers & Vendors tab: the list of people and companies, with their accounts and balances."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import contacts as ct
from .. import receivables as rx
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, confirm, error, fill_account_combo


class ContactDialog(QDialog):
    """Add or edit a customer / vendor."""

    def __init__(self, books: Books, contact: ct.Contact | None = None, *, role: str = "customer", parent=None):
        super().__init__(parent)
        self.books = books
        self.contact = contact or ct.Contact("", role, "Customer" if role == "customer" else "Supplier")
        c = self.contact
        self.setWindowTitle(f"Edit {c.name}" if contact else ("New Customer" if role == "customer" else "New Vendor"))
        self.resize(560, 0)
        form = QFormLayout(self)
        self.name = QLineEdit(c.name)
        self.name.setPlaceholderText("e.g. Sister Co Ltd, Alex Morgan, Harbor Supply Co")
        self.role = QComboBox()
        for k, v in ct.ROLES.items():
            self.role.addItem(v, k)
        self.role.setCurrentIndex(self.role.findData(c.role))
        self.type = QComboBox()
        self.type.setEditable(True)
        self.type.addItems(ct.TYPES)
        self.type.setCurrentText(c.type)
        self.email = QLineEdit(c.email)
        self.phone = QLineEdit(c.phone)
        self.address = QPlainTextEdit(c.address)
        self.address.setFixedHeight(56)
        from .control_account import ControlAccountPicker

        self.receivable = ControlAccountPicker(books, "invoice", c.receivable_id)
        self.due_from = QPushButton("Make “Due from …” account")
        self.due_from.setToolTip("Create this contact's own receivable account (a sub-account of Accounts Receivable), "
                                 "e.g. Due from Sister Co — for related entities, shareholders, loans")
        self.due_from.clicked.connect(lambda: self._make_related("receivable"))
        rec_row = QHBoxLayout()
        rec_row.addWidget(self.receivable, 1)
        rec_row.addWidget(self.due_from)
        self.payable = ControlAccountPicker(books, "bill", c.payable_id)
        self.due_to = QPushButton("Make “Due to …” account")
        self.due_to.setToolTip("Create this contact's own payable account (a sub-account of Accounts Payable), "
                               "e.g. Due to Sister Co")
        self.due_to.clicked.connect(lambda: self._make_related("payable"))
        pay_row = QHBoxLayout()
        pay_row.addWidget(self.payable, 1)
        pay_row.addWidget(self.due_to)
        self.income = QComboBox()
        fill_account_combo(self.income, books, include_none="(none)")
        self.income.setCurrentIndex(max(0, self.income.findData(c.income_id)))
        self.expense = QComboBox()
        fill_account_combo(self.expense, books, include_none="(none)")
        self.expense.setCurrentIndex(max(0, self.expense.findData(c.expense_id)))
        self.aliases = QLineEdit("; ".join(c.aliases))
        self.aliases.setPlaceholderText("How the bank / e-Transfers / cheques show them, separated by ;  e.g. "
                                        "PRIYA SHAH; SHAH HOLDINGS INC")
        self.notes = QLineEdit(c.notes)
        self.active = QCheckBox("Active (shown in pick lists)")
        self.active.setChecked(c.active)
        form.addRow("Name:", self.name)
        form.addRow("Customer / vendor:", self.role)
        form.addRow("Type:", self.type)
        self.rec_label = QLabel("Receivable account:")
        form.addRow(self.rec_label, rec_row)
        self.inc_label = QLabel("Usual income account:")
        form.addRow(self.inc_label, self.income)
        self.pay_label = QLabel("Payable account:")
        form.addRow(self.pay_label, pay_row)
        self.exp_label = QLabel("Usual expense account:")
        form.addRow(self.exp_label, self.expense)
        form.addRow("Other names:", self.aliases)
        form.addRow("Email:", self.email)
        form.addRow("Phone:", self.phone)
        form.addRow("Address:", self.address)
        form.addRow("Notes:", self.notes)
        form.addRow("", self.active)
        hint = QLabel("Their accounts are offered as one-click choices in To Categorize and used for new invoices "
                      "and bills. Transactions named with any of their names are grouped under this contact.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.rows = {"customer": [self.rec_label, self.receivable, self.due_from, self.inc_label, self.income],
                     "vendor": [self.pay_label, self.payable, self.due_to, self.exp_label, self.expense]}
        self.role.currentIndexChanged.connect(self._role_changed)
        self.type.currentTextChanged.connect(self._role_changed)
        self._role_changed()

    def _role_changed(self, *_) -> None:
        role = self.role.currentData()
        for side, widgets in self.rows.items():
            show = role == "both" or role == side
            for w in widgets:
                w.setVisible(show)
        related = self.type.currentText() in ("Related entity", "Shareholder", "Lender", "Employee")
        self.due_from.setVisible(related and role in ("customer", "both"))
        self.due_to.setVisible(related and role in ("vendor", "both"))

    def _collect(self) -> ct.Contact:
        c = self.contact
        c.name = self.name.text().strip()
        c.role = self.role.currentData()
        c.type = self.type.currentText().strip() or "Other"
        c.email, c.phone = self.email.text(), self.phone.text()
        c.address, c.notes = self.address.toPlainText(), self.notes.text()
        main_ar, main_ap = rx.control_account(self.books, "invoice").id, rx.control_account(self.books, "bill").id
        rec, pay = self.receivable.value(), self.payable.value()
        c.receivable_id = rec if c.is_customer and rec != main_ar else None
        c.payable_id = pay if c.is_vendor and pay != main_ap else None
        inc, exp = self.income.currentData(), self.expense.currentData()
        c.income_id = inc if c.is_customer and isinstance(inc, int) else None
        c.expense_id = exp if c.is_vendor and isinstance(exp, int) else None
        c.aliases = [a.strip() for a in self.aliases.text().split(";") if a.strip()]
        c.active = self.active.isChecked()
        return c

    def _make_related(self, side: str) -> None:
        c = self._collect()
        if not c.name:
            error(self, "Name first", "Enter the contact's name first.")
            return
        try:
            acct = ct.related_account(self.books, c, side)
        except ValueError as e:
            error(self, "Couldn't create the account", str(e))
            return
        picker = self.receivable if side == "receivable" else self.payable
        picker._fill(acct)
        picker.touched = True

    def save(self) -> None:
        try:
            ct.save(self.books, self._collect())
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


COLS = ["Name", "Type", "Customer / vendor", "Account", "Usual income / expense", "Owed to you", "You owe",
        "Bank transactions", "Last activity", "Other names", "Email / phone"]


class ContactsTab(QWidget):
    changed = Signal()
    show_transactions = Signal(str)   # contact name

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        self.companies = None          # () -> [company file paths]: vendors are shared by all of them
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.which = QButtonGroup(self)
        for i, (text, key) in enumerate([("Customers", "customer"), ("Vendors", "vendor")]):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.setProperty("role_key", key)
            b.clicked.connect(self.refresh)
            self.which.addButton(b, i)
            bar.addWidget(b)
        self.search = QLineEdit(placeholderText="Search name, type, other names…")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(120)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        self.new_btn = QPushButton("New Customer…")     # follows Customers / Vendors
        self.new_btn.setObjectName("primary")
        self.new_btn.clicked.connect(lambda: self.new(self._role()))
        bar.addWidget(self.new_btn)
        lay.addLayout(bar)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.edit)
        lay.addWidget(self.table, 1)
        crow = QHBoxLayout()                   # the check: anything not tied to a customer or vendor
        self.check = QLabel()
        self.check.setTextFormat(Qt.TextFormat.RichText)
        self.check.setWordWrap(True)
        crow.addWidget(self.check, 1)
        unb = QPushButton("Show Unassigned Transactions")
        unb.setToolTip("Transactions with no customer / vendor — select them and Set Customer / Vendor…")
        unb.clicked.connect(self._show_unassigned)
        crow.addWidget(unb)
        lay.addLayout(crow)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        bottom.addWidget(self.summary, 1)
        for text, slot in [("Show Transactions", self.show_tx), ("Edit…", self.edit), ("Delete", self.delete)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        lay.addLayout(bottom)
        self.rows: list[ct.Summary] = []
        self.refresh()

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def _acct(self, acct_id: int | None, fallback: str = "") -> str:
        if not acct_id:
            return fallback
        try:
            return self.books.account(acct_id).name
        except KeyError:
            return fallback

    def _role(self) -> str:
        b = self.which.checkedButton()
        return (b.property("role_key") if b else None) or "customer"

    def refresh(self, *_) -> None:
        role = self._role()
        self.new_btn.setText("New Vendor…" if role == "vendor" else "New Customer…")
        q = self.search.text().strip().lower()
        sums = ct.summaries(self.books)
        rows = [s for s in sums if role is None or (s.contact.is_customer if role == "customer"
                                                     else s.contact.is_vendor)]
        if q:
            rows = [s for s in rows if q in " ".join([s.contact.name, s.contact.type, *s.contact.aliases]).lower()]
        self.rows = rows
        ar_name = rx.control_account(self.books, "invoice").name
        ap_name = rx.control_account(self.books, "bill").name
        self.table.setRowCount(len(rows))
        for r, s in enumerate(rows):
            c = s.contact
            accts = []
            if c.is_customer:
                accts.append(self._acct(c.receivable_id, ar_name))
            if c.is_vendor:
                accts.append(self._acct(c.payable_id, ap_name))
            usual = [self._acct(c.income_id) if c.is_customer else "", self._acct(c.expense_id) if c.is_vendor else ""]
            vals = [c.name, c.type, ct.ROLES[c.role], " / ".join(accts), " / ".join(u for u in usual if u),
                    fmt_money(s.receivable_cents) if s.receivable_cents else "",
                    fmt_money(s.payable_cents) if s.payable_cents else "",
                    str(s.transactions) if s.transactions else "", s.last_date, "; ".join(c.aliases),
                    " · ".join(x for x in (c.email, c.phone) if x)]
            for col, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if col in (5, 6, 7):
                    cell.setTextAlignment(ALIGN_RIGHT)
                if not c.active:
                    cell.setForeground(MUTED)
                self.table.setItem(r, col, cell)
        for col, w in enumerate([190, 110, 120, 190, 170, 105, 105, 125, 95]):
            self.table.setColumnWidth(col, w)
        owed = sum(s.receivable_cents for s in rows)
        owe = sum(s.payable_cents for s in rows)
        vendors = role == "vendor"
        # vendors: what you owe each of them; customers: what each owes you
        self.table.setHorizontalHeaderItem(6, QTableWidgetItem("Owed to them"))
        self.table.setColumnHidden(5, vendors)
        self.table.setColumnHidden(6, not vendors)
        self.summary.setText(f"{len(rows)} vendor(s) · owed to them {fmt_money(owe)}" if vendors else
                             f"{len(rows)} customer(s) · owed to you {fmt_money(owed)}")
        self._fill_check()

    def _fill_check(self) -> None:
        """Revenue / expenses with no customer / vendor, AR / AP not allocated, names not on the list."""
        from datetime import date, timedelta

        from .. import party_totals as pt
        from .theme import c as tc

        end = date.today()
        start = (end - timedelta(days=365) + timedelta(days=1)).isoformat()
        try:
            chk = self.assign_check = pt.assignment_check(self.books, start, end.isoformat())
        except Exception as e:  # noqa: BLE001
            self.check.setText(f"<span style='color:{tc('muted')}'>Couldn't check: {e}</span>")
            return
        if chk.ok:
            self.check.setText(f"<b style='color:{tc('green')}'>✓ Every revenue and expense in the last 12 months has a "
                               f"customer or vendor, and everything owing is allocated.</b>")
        else:
            self.check.setText(f"<b style='color:{tc('red')}'>⚠ Not assigned (last 12 months):</b> "
                               + " · ".join(chk.lines()))

    def _show_unassigned(self) -> None:
        from .transactions import NO_NAME

        self.show_transactions.emit(NO_NAME)

    def _selected(self) -> ct.Contact | None:
        sel = self.table.selectionModel().selectedRows()
        return self.rows[sel[0].row()].contact if sel else None

    def new(self, role: str) -> None:
        if ContactDialog(self.books, role=role, parent=self).exec():
            self.refresh()
            self.changed.emit()

    def edit(self, *_) -> None:
        c = self._selected()
        if c and ContactDialog(self.books, c, parent=self).exec():
            self.refresh()
            self.changed.emit()

    def delete(self, *, where: str | None = None) -> None:
        """A vendor is on every company's list: remove it from all of them, or from this company only."""
        from PySide6.QtWidgets import QMessageBox

        from .. import shared_vendors as sv

        c = self._selected()
        if not c:
            return
        others = [p for p in (self.companies() if self.companies else []) if str(p) != str(self.books.path)]
        if c.is_vendor and others:
            if where is None:
                box = QMessageBox(self)
                box.setWindowTitle("Remove vendor")
                box.setText(f"Remove {c.name}? Vendors are shared by every company.")
                box.setInformativeText("Transactions, invoices and bills keep the name.")
                every = box.addButton("From All Companies", QMessageBox.ButtonRole.AcceptRole)
                here = box.addButton("From This Company Only", QMessageBox.ButtonRole.ActionRole)
                box.addButton(QMessageBox.StandardButton.Cancel)
                box.exec()
                where = "all" if box.clickedButton() is every else "here" if box.clickedButton() is here else ""
            if where == "all":
                sv.remove_everywhere(others, c.name)
                ct.delete(self.books, c.id)
            elif where == "here":
                ct.delete(self.books, c.id)
                sv.skip_here(self.books, c.name)
            else:
                return
        elif not confirm(self, "Remove contact", f"Remove {c.name} from the list? Transactions, invoices and bills "
                                                 f"keep the name."):
            return
        else:
            ct.delete(self.books, c.id)
        self.refresh()
        self.changed.emit()

    def show_tx(self) -> None:
        c = self._selected()
        if c:
            self.show_transactions.emit(c.name)
