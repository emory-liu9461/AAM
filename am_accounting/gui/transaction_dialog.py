"""One bank transaction: who it was with (customer / vendor), category and note."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton)

from .. import contacts, parties
from ..books import Books
from ..money import fmt_money
from .common import error, fill_account_combo


class TransactionDialog(QDialog):
    """Opened by double-clicking a transaction (Transactions tab) or an entry under an expanded statement line."""

    def __init__(self, books: Books, tx_id: int, parent=None):
        super().__init__(parent)
        self.books = books
        self.tx = tx = books.transaction(tx_id)
        self.setWindowTitle("Transaction")
        self.resize(600, 0)
        form = QFormLayout(self)
        money_in = tx.amount_cents > 0
        colour = "#1b7f3b" if money_in else "#b3261e"
        head = QLabel(f"<b style='font-size:12pt'>{html.escape(tx.description)}</b><br>"
                      f"{tx.date} · {html.escape(books.account(tx.bank_account_id).name)} · "
                      f"<b style='color:{colour}'>{fmt_money(tx.amount_cents, parens=False)}</b> "
                      f"({'money in' if money_in else 'money out'})")
        head.setTextFormat(Qt.TextFormat.RichText)
        head.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(head)
        from .. import match_details

        found = match_details.for_transaction(books, tx.id)
        if found:
            facts = " · ".join(f"{k} <b>{html.escape(v)}</b>" for k, v in found.facts)
            m = QLabel(f"✓ {html.escape(found.headline)} — {facts}")
            m.setTextFormat(Qt.TextFormat.RichText)
            m.setWordWrap(True)
            m.setObjectName("hint")
            form.addRow(m)

        self.current = parties.name_of(books, tx.id)
        who_row = QHBoxLayout()
        self.who = QComboBox()
        self.who.setEditable(True)
        self.who.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.who.lineEdit().setPlaceholderText("Customer or vendor — pick from the list or type a new name")
        role = "customer" if money_in else "vendor"
        listed = [c.name for c in contacts.all_contacts(books, role, active_only=True)]
        others = [n for n in parties.all_names(books) if n not in listed]
        self.who.addItems([""] + listed + others)
        self.who.setCurrentText(self.current.name if self.current else "")
        who_row.addWidget(self.who, 1)
        new = QPushButton("New Customer…" if money_in else "New Vendor…")
        new.clicked.connect(self.new_contact)
        who_row.addWidget(new)
        form.addRow("Customer / vendor:", who_row)
        if self.current:
            src = {"etransfer": "from the matched e-Transfer", "cheque": "from the matched cheque / draft",
                   "rule": "from a name rule", "manual": "set by you"}.get(self.current.source, "")
            note = QLabel(f"Currently <b>{html.escape(self.current.name)}</b> ({src}).")
            note.setTextFormat(Qt.TextFormat.RichText)
            note.setObjectName("hint")
            form.addRow("", note)
        self.add_contact = QCheckBox("Add to the Customers && Vendors list if new")
        self.add_contact.setChecked(True)
        form.addRow("", self.add_contact)
        fut = QHBoxLayout()
        self.future = QCheckBox("Also for future transactions containing:")
        self.pattern = QLineEdit(parties.suggested_pattern(tx.description))
        self.pattern.setEnabled(False)
        self.future.toggled.connect(self.pattern.setEnabled)
        self.future.setEnabled(bool(self.pattern.text()))
        fut.addWidget(self.future)
        fut.addWidget(self.pattern, 1)
        form.addRow("", fut)

        cat_row = QHBoxLayout()
        self.category = QComboBox()
        self.category.setMaxVisibleItems(25)
        fill_account_combo(self.category, books, include_none="Uncategorized", exclude={tx.bank_account_id})
        self.category.setCurrentIndex(max(0, self.category.findData(tx.category_id)))
        cat_row.addWidget(self.category, 1)
        self.use_acct = QPushButton()
        self.use_acct.clicked.connect(self._use_contact_account)
        cat_row.addWidget(self.use_acct)
        form.addRow("Category:", cat_row)
        self.memo = QLineEdit(tx.memo)
        self.memo.setPlaceholderText("Note (optional)")
        form.addRow("Note:", self.memo)
        from .. import loan_link

        if loan_link.portfolio_path():
            lp = QPushButton("Loan Payment…")
            lp.setToolTip("Record this as a loan payment (principal + interest) or a loan advance")
            lp.clicked.connect(self._loan_payment)
            form.addRow("", lp)
            if loan_link.is_loan_tx(books, tx.id):
                parts = " + ".join(f"{books.account(a).name} {fmt_money(c)}" for a, c, _m, _r in books.splits(tx.id))
                lbl = QLabel(f"Recorded against a loan: {html.escape(parts)}. Changing the category below replaces "
                             f"the split.")
                lbl.setObjectName("hint")
                lbl.setWordWrap(True)
                form.addRow("", lbl)
        if tx.manual:
            fix = QPushButton("Change Date / Amount…")
            fix.setToolTip("Entered by hand: change its date, account, description or amount")
            fix.clicked.connect(self._change_details)
            form.addRow("", fix)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.who.currentTextChanged.connect(self._who_changed)
        self._who_changed()

    def _contact_account(self) -> int | None:
        c = contacts.find(self.books, self.who.currentText())
        if not c:
            return None
        money_in = self.tx.amount_cents > 0
        for acct in (c.receivable_id, c.payable_id, c.income_id if money_in else c.expense_id):
            if acct and acct != self.tx.bank_account_id:
                return acct
        return None

    def _who_changed(self, *_) -> None:
        acct = self._contact_account()
        if acct:
            self.use_acct.setText(f"Use {self.books.account(acct).name}")
            self.use_acct.show()
        else:
            self.use_acct.hide()

    def _use_contact_account(self) -> None:
        acct = self._contact_account()
        if acct:
            self.category.setCurrentIndex(max(0, self.category.findData(acct)))

    def _loan_payment(self) -> None:
        from .loans_dialog import LoanSplitDialog

        if LoanSplitDialog(self.books, self.tx.id, self).exec():
            self.done(1)  # recorded; the category here no longer applies

    def _change_details(self) -> None:
        from .manual_entry import ManualTransactionDialog

        if ManualTransactionDialog(self.books, self.tx.id, self).exec():
            self.save()  # keep what was chosen here too, and close

    def new_contact(self) -> None:
        from .contacts_tab import ContactDialog

        dlg = ContactDialog(self.books, role="customer" if self.tx.amount_cents > 0 else "vendor", parent=self)
        dlg.name.setText(self.who.currentText())
        if dlg.exec():
            self.who.addItem(dlg.contact.name)
            self.who.setCurrentText(dlg.contact.name)

    def save(self) -> None:
        name = " ".join(self.who.currentText().split())
        tx = self.tx
        try:
            if name and self.add_contact.isChecked():
                contacts.ensure_contact(self.books, name, "customer" if tx.amount_cents > 0 else "vendor")
            if self.future.isChecked() and self.pattern.text().strip() and name:
                parties.add_rule(self.books, self.pattern.text(), name)
            old = self.current.name if self.current else ""
            if name != old or (self.current and self.current.source == "manual"):
                parties.set_name(self.books, [tx.id], name)  # blank clears your name
            cat = self.category.currentData()
            cat = cat if isinstance(cat, int) else None
            if cat != tx.category_id:
                self.books.set_category(tx.id, cat, source="manual", reviewed=cat is not None)
            if self.memo.text().strip() != tx.memo:
                self.books.set_memo(tx.id, self.memo.text().strip())
            if name and cat is None:
                parties.apply_learned(self.books, [self.books.transaction(tx.id)])
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()
