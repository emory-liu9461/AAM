"""Entering accounting by hand: a bank transaction, or a journal entry with debit and credit lines."""

from __future__ import annotations

import html

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import contacts, parties
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, confirm, error, fill_account_combo


def _date_edit(iso: str | None = None) -> QDateEdit:
    d = QDateEdit(QDate.fromString(iso, "yyyy-MM-dd") if iso else QDate.currentDate())
    d.setCalendarPopup(True)
    d.setDisplayFormat("yyyy-MM-dd")
    return d


def _money() -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, 1e12)
    s.setDecimals(2)
    s.setPrefix("$ ")
    s.setGroupSeparatorShown(True)
    return s


class ManualTransactionDialog(QDialog):
    """Add (or change) a bank transaction by hand — cash, a cheque not on a statement yet, a missing line…"""

    def __init__(self, books: Books, tx_id: int | None = None, parent=None, bank_id: int | None = None):
        super().__init__(parent)
        self.books, self.tx_id = books, tx_id
        tx = books.transaction(tx_id) if tx_id else None
        self.setWindowTitle("Change Transaction" if tx else "Add Transaction")
        self.resize(560, 0)
        form = QFormLayout(self)
        self.date = _date_edit(tx.date if tx else None)
        form.addRow("Date:", self.date)
        self.bank = QComboBox()
        fill_account_combo(self.bank, books, banks_only=True)
        want = tx.bank_account_id if tx else bank_id
        if want is not None:
            self.bank.setCurrentIndex(max(0, self.bank.findData(want)))
        form.addRow("Bank / cash account:", self.bank)
        dirs = QHBoxLayout()
        self.money_in = QRadioButton("Money in (deposit, receipt)")
        self.money_out = QRadioButton("Money out (payment, withdrawal)")
        (self.money_in if tx and tx.amount_cents > 0 else self.money_out).setChecked(True)
        dirs.addWidget(self.money_in)
        dirs.addWidget(self.money_out)
        dirs.addStretch(1)
        form.addRow("", dirs)
        self.amount = _money()
        if tx:
            self.amount.setValue(abs(tx.amount_cents) / 100)
        form.addRow("Amount:", self.amount)
        self.description = QLineEdit(tx.description if tx else "")
        self.description.setPlaceholderText("e.g. Cash sale, Cheque #1043 to Bay Properties")
        form.addRow("Description:", self.description)
        self.category = None
        self.who = None
        if tx is None:  # a new one: category, who and note here too (an existing one has its own dialog for these)
            self.category = QComboBox()
            self.category.setMaxVisibleItems(25)
            form.addRow("Category:", self.category)
            self.who = QComboBox()
            self.who.setEditable(True)
            self.who.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            self.who.lineEdit().setPlaceholderText("Customer or vendor (optional)")
            names = [""] + [c.name for c in contacts.all_contacts(books, active_only=True)]
            self.who.addItems(names + [n for n in parties.all_names(books) if n not in names])
            form.addRow("Customer / vendor:", self.who)
            self.memo = QLineEdit()
            self.memo.setPlaceholderText("Note (optional)")
            form.addRow("Note:", self.memo)
            self.bank.currentIndexChanged.connect(self._fill_categories)
            self._fill_categories()
        self.warning = QLabel()
        self.warning.setObjectName("hint")
        self.warning.setWordWrap(True)
        self.warning.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(self.warning)
        hint = QLabel("Entered by hand — marked ✎ in the list. If the same transaction later comes in on an imported "
                      "statement, delete one of the two.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setMinimumHeight(hint.fontMetrics().lineSpacing() * 3)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for sig in (self.date.dateChanged, self.amount.valueChanged, self.money_in.toggled,
                    self.bank.currentIndexChanged):
            sig.connect(self._check_duplicates)
        self._check_duplicates()

    def _fill_categories(self, *_) -> None:
        if self.category is not None:
            fill_account_combo(self.category, self.books, include_none="Uncategorized",
                               exclude={self.bank.currentData()})

    def cents(self) -> int:
        c = round(self.amount.value() * 100)
        return c if self.money_in.isChecked() else -c

    def _check_duplicates(self, *_) -> None:
        bank = self.bank.currentData()
        if bank is None or not self.cents():
            self.warning.setText("")
            return
        same = self.books.similar_transactions(bank, self.date.date().toString("yyyy-MM-dd"), self.cents(),
                                               exclude=self.tx_id)
        self.warning.setText("" if not same else
                             "⚠ Already in this account for the same amount: " + "; ".join(
                                 f"{t.date} {html.escape(t.description)}" for t in same[:3]))

    def save(self) -> None:
        bank = self.bank.currentData()
        d = self.date.date().toString("yyyy-MM-dd")
        try:
            if bank is None:
                raise ValueError("Pick a bank account (add one under Chart of Accounts if there's none)")
            if self.tx_id:
                self.books.update_manual_transaction(self.tx_id, bank, d, self.description.text(), self.cents())
            else:
                cat = self.category.currentData()
                cat = cat if isinstance(cat, int) else None
                self.tx_id = self.books.add_manual_transaction(bank, d, self.description.text(), self.cents(),
                                                               category_id=cat, memo=self.memo.text())
                name = " ".join(self.who.currentText().split())
                if name:
                    contacts.ensure_contact(self.books, name, "customer" if self.cents() > 0 else "vendor")
                    parties.set_name(self.books, [self.tx_id], name)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class JournalEntryDialog(QDialog):
    """A journal entry by hand: date, description and two or more debit / credit lines that balance."""

    def __init__(self, books: Books, entry: dict | None = None, parent=None):
        super().__init__(parent)
        self.books, self.entry = books, entry
        self.setWindowTitle("Change Journal Entry" if entry else "New Journal Entry")
        self.resize(820, 460)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.date = _date_edit(entry["date"] if entry else None)
        form.addRow("Date:", self.date)
        self.memo = QLineEdit(entry["memo"] if entry else "")
        self.memo.setPlaceholderText("e.g. Depreciation for 2026, accrued wages, reclassify owner draw")
        form.addRow("Description:", self.memo)
        lay.addLayout(form)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Debit / Credit", "Account", "Amount"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 130)
        self.table.setColumnWidth(2, 170)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = QPushButton("Add Debit")
        add.clicked.connect(lambda: self.add_line(side="debit", fill=True))
        addc = QPushButton("Add Credit")
        addc.clicked.connect(lambda: self.add_line(side="credit", fill=True))
        rem = QPushButton("Remove Line")
        rem.clicked.connect(self.remove_line)
        bal = QPushButton("Balance This Line")
        bal.setToolTip("Set the selected line's amount so debits equal credits")
        bal.clicked.connect(self.balance_line)
        for w in (add, addc, rem, bal):
            row.addWidget(w)
        row.addStretch(1)
        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.totals)
        lay.addLayout(row)
        hint = QLabel("For each line choose Debit or Credit, the account and the amount — e.g. buying a property: "
                      "Debit the property, Credit the mortgage and Credit equity for the owners' money. Debits "
                      "increase assets and expenses; credits increase liabilities, equity and income. Debits must "
                      "equal credits.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        if entry:
            for acct, dr, cr in entry["lines"]:
                self.add_line(acct, dr or cr, side="debit" if dr else "credit")
        else:
            self.add_line(side="debit")
            self.add_line(side="credit")
        self._update()

    def add_line(self, account_id: int | None = None, cents: int = 0, *, side: str = "debit",
                 fill: bool = False) -> None:
        """A line: Debit or Credit, the account, the amount (``fill``: what's needed to balance the entry)."""
        if fill and not cents:
            dr, cr = self.sums()
            cents = max(0, (dr - cr) if side == "credit" else (cr - dr))
        r = self.table.rowCount()
        self.table.insertRow(r)
        sd = QComboBox()
        sd.addItem("Debit", "debit")
        sd.addItem("Credit", "credit")
        sd.setCurrentIndex(0 if side == "debit" else 1)
        sd.currentIndexChanged.connect(self._update)
        self.table.setCellWidget(r, 0, sd)
        combo = QComboBox()
        combo.setMaxVisibleItems(25)
        fill_account_combo(combo, self.books, include_none="— pick an account —")
        combo.setCurrentIndex(max(0, combo.findData(account_id)))
        self.table.setCellWidget(r, 1, combo)
        box = _money()
        box.setValue(cents / 100)
        box.valueChanged.connect(self._update)
        self.table.setCellWidget(r, 2, box)
        self.table.setCurrentCell(r, 1)
        if hasattr(self, "totals"):
            self._update()

    def remove_line(self) -> None:
        r = self.table.currentRow()
        if r >= 0 and self.table.rowCount() > 2:
            self.table.removeRow(r)
            self._update()

    def lines(self) -> list[tuple[int | None, int, int]]:
        """[(account, debit cents, credit cents)]"""
        out = []
        for r in range(self.table.rowCount()):
            acct = self.table.cellWidget(r, 1).currentData()
            cents = round(self.table.cellWidget(r, 2).value() * 100)
            debit = self.table.cellWidget(r, 0).currentData() == "debit"
            out.append((acct if isinstance(acct, int) else None, cents if debit else 0, 0 if debit else cents))
        return out

    def sums(self) -> tuple[int, int]:
        ls = self.lines()
        return sum(x[1] for x in ls), sum(x[2] for x in ls)

    def balance_line(self) -> None:
        r = self.table.currentRow()
        if r < 0:
            return
        box = self.table.cellWidget(r, 2)
        mine = round(box.value() * 100)
        dr, cr = self.sums()
        debit = self.table.cellWidget(r, 0).currentData() == "debit"
        other = (cr if debit else dr)
        same = (dr if debit else cr) - mine
        box.setValue(max(0, other - same) / 100)

    def _update(self, *_) -> None:
        dr, cr = self.sums()
        diff = dr - cr
        self.totals.setText(f"Debits <b>{fmt_money(dr)}</b> · Credits <b>{fmt_money(cr)}</b>"
                            + (" · <span style='color:#1b7f3b'>✓ balanced</span>" if dr and not diff else
                               f" · <span style='color:#b3261e'>out by {fmt_money(abs(diff))}</span>" if diff else ""))

    def save(self) -> None:
        lines = [x for x in self.lines() if x[1] or x[2]]
        if any(a is None for a, _d, _c in lines):
            error(self, "Couldn't save", "Pick an account on every line with an amount")
            return
        try:
            self.books.replace_journal_entry(self.entry["id"] if self.entry else None,
                                             self.date.date().toString("yyyy-MM-dd"), self.memo.text(), lines)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class JournalEntriesDialog(QDialog):
    """The journal entries made by hand: add, change, delete."""

    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.changed = False
        self.setWindowTitle("Journal Entries")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        intro = QLabel("Entries made by hand for things that don't go through a bank account — depreciation, "
                       "accruals, owner contributions in kind, corrections, reclassifications. They show on the "
                       "financial statements like everything else.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        lay.addWidget(intro)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date", "Description", "Accounts", "Amount", ""])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        self.table.setColumnHidden(4, True)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("New Entry…", self.new), ("Change…", self.edit), ("Delete", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if text.startswith("New"):
                b.setObjectName("primary")
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.entries = list(reversed(self.books.manual_entries()))
        self.table.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            accts = ", ".join(f"{self.books.account(a).name} {'Dr' if d else 'Cr'}" for a, d, _c in e["lines"])
            amt = sum(d for _a, d, _c in e["lines"])
            for c, v in enumerate([e["date"], e["memo"], accts, fmt_money(amt)]):
                it = QTableWidgetItem(v)
                if c == 3:
                    it.setTextAlignment(ALIGN_RIGHT)
                self.table.setItem(r, c, it)
        self.table.resizeColumnToContents(0)
        self.table.resizeColumnToContents(1)

    def _current(self) -> dict | None:
        r = self.table.currentRow()
        return self.entries[r] if 0 <= r < len(self.entries) else None

    def new(self) -> None:
        if JournalEntryDialog(self.books, parent=self).exec():
            self.changed = True
            self.refresh()

    def edit(self) -> None:
        e = self._current()
        if e and JournalEntryDialog(self.books, e, self).exec():
            self.changed = True
            self.refresh()

    def delete(self) -> None:
        e = self._current()
        if e and confirm(self, "Delete entry", f"Delete the entry “{e['memo']}” of {e['date']}?"):
            self.books.delete_journal_entry(e["id"])
            self.changed = True
            self.refresh()
