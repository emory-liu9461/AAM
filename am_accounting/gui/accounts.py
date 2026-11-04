"""Chart of accounts: fully customizable assets, liabilities, equity, income and expenses."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
                               QWidget)

from .. import chart_of_accounts as coa
from ..books import Account, Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, confirm, error

SECTION_TITLES = {"asset": "Assets", "liability": "Liabilities", "equity": "Equity", "income": "Income",
                  "expense": "Expenses"}


class AccountDialog(QDialog):
    """Create or edit any account: number, name, type, group, parent (sub-account) and status."""

    def __init__(self, books: Books, account: Account | None = None, parent=None, *, type_: str | None = None,
                 parent_account: Account | None = None):
        super().__init__(parent)
        self.books = books
        self.account = account
        self.setWindowTitle("Edit Account" if account else "New Account")
        self.resize(480, 0)
        form = QFormLayout(self)
        self.is_bank = QCheckBox("This is a bank, cash or credit card account (statements are imported into it)")
        self.type = QComboBox()
        for t in coa.ACCOUNT_TYPES:
            self.type.addItem(SECTION_TITLES[t], t)
        self.subtype = QComboBox()
        self.parent_acct = QComboBox()
        self.code = QLineEdit()
        self.name = QLineEdit()
        self.active = QCheckBox("Active (inactive accounts are hidden from category lists)")
        self.active.setChecked(True)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        form.addRow(self.is_bank)
        form.addRow("Type:", self.type)
        form.addRow("Group:", self.subtype)
        form.addRow("Sub-account of:", self.parent_acct)
        form.addRow("Number:", self.code)
        form.addRow("Name:", self.name)
        form.addRow(self.active)
        form.addRow(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self.type.currentIndexChanged.connect(self._type_changed)
        self.parent_acct.currentIndexChanged.connect(self._parent_changed)
        self.is_bank.toggled.connect(self._bank_toggled)
        if account:
            self.is_bank.setChecked(account.is_bank)
            self.is_bank.setEnabled(False)
            self.type.setCurrentIndex(self.type.findData(account.type))
            self._type_changed()
            self.subtype.setCurrentIndex(max(0, self.subtype.findData(account.subtype)))
            self.parent_acct.setCurrentIndex(max(0, self.parent_acct.findData(account.parent_id)))
            self.code.setText(account.code)
            self.name.setText(account.name)
            self.active.setChecked(account.active)
            if account.is_system:
                self.type.setEnabled(False)
                self.active.setEnabled(False)
                self.note.setText(f"The app uses this account for: {coa.ROLE_KEYS[account.key].lower()}. "
                                  "You can rename and renumber it, and change its group.")
        else:
            self.type.setCurrentIndex(self.type.findData(parent_account.type if parent_account else
                                                         (type_ or "expense")))
            self._type_changed()
            if parent_account:
                self.parent_acct.setCurrentIndex(max(0, self.parent_acct.findData(parent_account.id)))

    def _type_changed(self) -> None:
        t = self.type.currentData()
        bank = self.is_bank.isChecked()
        self.subtype.clear()
        for key, label in coa.SUBTYPES[t].items():
            if (key == "bank") == bank or (t != "asset" and not bank):
                self.subtype.addItem(label, key)
        self.parent_acct.blockSignals(True)
        self.parent_acct.clear()
        self.parent_acct.addItem("— None (top-level account) —", None)
        if not bank:
            own = self.account.id if self.account else None
            for a in self.books.accounts(types=(t,)):
                if a.id != own and not a.is_bank:
                    self.parent_acct.addItem(a.label, a.id)
        self.parent_acct.blockSignals(False)
        self.parent_acct.setEnabled(not bank)
        if not self.account:
            self.code.setText(self.books.next_account_code(t, is_bank=bank))
        self._parent_changed()

    def _parent_changed(self) -> None:
        pid = self.parent_acct.currentData()
        if pid is not None:
            parent = self.books.account(pid)
            self.subtype.setCurrentIndex(max(0, self.subtype.findData(parent.subtype)))
        self.subtype.setEnabled(pid is None and not self.is_bank.isChecked())

    def _bank_toggled(self, on: bool) -> None:
        if on:
            self.type.setCurrentIndex(self.type.findData("asset"))
        self.type.setEnabled(not on)
        self._type_changed()

    def save(self) -> None:
        try:
            kwargs = dict(type_=self.type.currentData(), subtype=self.subtype.currentData() or "")
            if self.account:
                self.books.update_account(self.account.id, code=self.code.text(), name=self.name.text(),
                                          active=self.active.isChecked(), parent_id=self.parent_acct.currentData(),
                                          **kwargs)
            else:
                self.books.add_account(self.code.text(), self.name.text(), kwargs["type_"], kwargs["subtype"],
                                       is_bank=self.is_bank.isChecked(), parent_id=self.parent_acct.currentData(),
                                       active=self.active.isChecked())
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save account", str(e))
            return
        self.accept()


class DeleteAccountDialog(QDialog):
    """Delete an unused account, or merge one with history into another account."""

    def __init__(self, books: Books, account: Account, parent=None):
        super().__init__(parent)
        self.books = books
        self.account = account
        self.setWindowTitle(f"Delete – {account.name}")
        use = books.account_usage(account.id)
        self.has_history = bool(use["transactions"] or use["journal_lines"])
        form = QFormLayout(self)
        if self.has_history:
            msg = (f"“{account.name}” has {use['journal_lines']:,} ledger entries. To remove it, merge it into "
                   "another account: everything recorded in it moves there, then it is deleted.")
        else:
            msg = f"“{account.name}” has never been used and can be deleted."
            if use["rules"]:
                msg += f" Its {use['rules']} rule(s) will be deleted too."
        lbl = QLabel(msg)
        lbl.setWordWrap(True)
        form.addRow(lbl)
        self.target = QComboBox()
        if self.has_history:
            for a in books.accounts(active_only=True):
                if a.id != account.id and a.is_bank == account.is_bank:
                    self.target.addItem(a.label, a.id)
            same = [i for i in range(self.target.count())
                    if books.account(self.target.itemData(i)).type == account.type]
            if same:
                self.target.setCurrentIndex(same[0])
            form.addRow("Merge into:", self.target)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Merge and Delete" if self.has_history else "Delete",
                               QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        try:
            if self.has_history:
                self.books.merge_account(self.account.id, self.target.currentData())
            else:
                self.books.delete_account(self.account.id)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not delete", str(e))
            return
        self.accept()


class LabelsDialog(QDialog):
    """Rename the headings on the financial statements (e.g. Equity → Shareholders' Equity)."""

    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Financial Statement Headings")
        self.resize(560, 640)
        outer = QVBoxLayout(self)
        intro = QLabel("Rename any heading used on your financial statements. Leave a box empty to use the "
                       f"default. Defaults follow the business type (“{books.profile().entity_type or 'not set'}”) "
                       "in Company Settings.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        outer.addWidget(intro)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        defaults = coa.default_labels(books.profile().entity_type)
        current = books.labels()
        self.edits: dict[str, QLineEdit] = {}
        for title, keys in coa.LABEL_GROUPS:
            box = QGroupBox(title)
            f = QFormLayout(box)
            for k in keys:
                e = QLineEdit(current[k] if current[k] != defaults[k] else "")
                e.setPlaceholderText(defaults[k])
                self.edits[k] = e
                f.addRow(f"{coa.LABELS[k]}:", e)
            lay.addWidget(box)
        outer.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
                                   | QDialogButtonBox.StandardButton.RestoreDefaults)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            lambda: [e.clear() for e in self.edits.values()])
        outer.addWidget(buttons)

    def save(self) -> None:
        self.books.set_labels({k: e.text() for k, e in self.edits.items()})
        self.accept()


class OpeningBalanceDialog(QDialog):
    def __init__(self, books: Books, account: Account, parent=None):
        super().__init__(parent)
        self.books = books
        self.account = account
        self.setWindowTitle(f"Opening Balance – {account.name}")
        form = QFormLayout(self)
        obe = books.role(coa.OPENING_BALANCE_EQUITY).name
        form.addRow(QLabel("Enter the balance on the day before your first imported statement.\n"
                           f"The offset goes to {obe}."))
        current_date, current = books.opening_balance(account.id)
        first, _ = books.date_range()
        default = date.fromisoformat(current_date) if current_date else (
            date.fromisoformat(first) if first else date.today().replace(month=1, day=1))
        self.date = QDateEdit(QDate(default.year, default.month, default.day))
        self.date.setCalendarPopup(True)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(-1e11, 1e11)
        self.amount.setDecimals(2)
        self.amount.setPrefix("$ ")
        self.amount.setGroupSeparatorShown(True)
        self.amount.setValue(current / 100)
        form.addRow("As of:", self.date)
        form.addRow("Balance:", self.amount)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        cents = round(self.amount.value() * 100)
        self.books.set_opening_balance(self.account.id, self.date.date().toString("yyyy-MM-dd"), cents)
        self.accept()


class AccountsTab(QWidget):
    changed = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        layout = QVBoxLayout(self)

        bar = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Find account…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.show_inactive = QCheckBox("Show inactive")
        self.show_inactive.toggled.connect(self.refresh)
        bar.addWidget(self.search, 1)
        bar.addWidget(self.show_inactive)
        new_btn = QPushButton("New Account")
        new_menu = QMenu(self)
        for t in coa.ACCOUNT_TYPES:
            a = new_menu.addAction(f"New {coa.TYPE_LABELS[t].lower()} account…")
            a.triggered.connect(lambda _=False, t=t: self.new_account(t))
        new_menu.addSeparator()
        new_menu.addAction("New bank / credit card account…").triggered.connect(lambda: self.new_account("bank"))
        new_menu.addAction("New sub-account of selected…").triggered.connect(self.new_sub_account)
        new_btn.setMenu(new_menu)
        new_btn.setObjectName("primary")
        bar.addWidget(new_btn)
        for label, slot in [("Edit…", self.edit_account), ("Delete / Merge…", self.delete_account),
                            ("Opening Balance…", self.opening_balance)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        more = QPushButton("Customize")
        more_menu = QMenu(self)
        more_menu.addAction("Financial statement headings…").triggered.connect(self.edit_labels)
        more_menu.addSeparator()
        more_menu.addAction("Export chart of accounts (CSV)…").triggered.connect(self.export_csv)
        more_menu.addAction("Import chart of accounts (CSV)…").triggered.connect(self.import_csv)
        more_menu.addSeparator()
        self.builtin_action = more_menu.addAction("Use built-in category suggestions")
        self.builtin_action.setCheckable(True)
        self.builtin_action.toggled.connect(self._toggle_builtin)
        more.setMenu(more_menu)
        bar.addWidget(more)
        layout.addLayout(bar)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Account", "Number", "Group", "Balance"])
        h = self.tree.header()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 230)
        self.tree.setColumnWidth(3, 140)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemDoubleClicked.connect(lambda item, _c: self.edit_account())
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.tree, 1)
        hint = QLabel("Every account is customizable: add, rename, renumber, regroup, nest as sub-accounts, "
                      "merge or delete. Sub-accounts roll up into their parent on the financial statements.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.refresh()

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def refresh(self) -> None:
        self.builtin_action.blockSignals(True)
        self.builtin_action.setChecked(self.books.builtin_suggestions)
        self.builtin_action.blockSignals(False)
        selected = self._selected()
        self.tree.clear()
        show_inactive = self.show_inactive.isChecked()
        needle = self.search.text().strip().lower()
        accts = [a for a in self.books.accounts() if show_inactive or a.active]
        ids = {a.id for a in accts}
        kids: dict[int | None, list[Account]] = {}
        for a in accts:
            kids.setdefault(a.parent_id if a.parent_id in ids else None, []).append(a)

        def matches(a: Account) -> bool:
            return (not needle or needle in a.name.lower() or needle in a.code.lower()
                    or any(matches(c) for c in kids.get(a.id, [])))

        own = {a.id: self.books.account_balance(a.id) for a in accts}

        def rolled(a: Account) -> int:
            return own[a.id] + sum(rolled(c) for c in kids.get(a.id, []))

        def add(parent_item, a: Account) -> None:
            if not matches(a):
                return
            bal = rolled(a)
            group = coa.subtype_label(a.type, a.subtype) + ("  · used by the app" if a.is_system else "")
            item = QTreeWidgetItem([a.name, a.code, group, fmt_money(bal) if bal else ""])
            item.setData(0, Qt.ItemDataRole.UserRole, a.id)
            item.setTextAlignment(3, ALIGN_RIGHT)
            if kids.get(a.id):
                f = item.font(0)
                f.setBold(True)
                item.setFont(0, f)
                item.setFont(3, f)
                item.setToolTip(3, f"Includes sub-accounts. {a.name} itself: {fmt_money(own[a.id])}")
            if not a.active:
                for c in range(4):
                    item.setForeground(c, MUTED)
                item.setText(0, f"{a.name} (inactive)")
            parent_item.addChild(item)
            for c in kids.get(a.id, []):
                add(item, c)
            if selected and selected.id == a.id:
                self.tree.setCurrentItem(item)

        for t in coa.ACCOUNT_TYPES:
            top = QTreeWidgetItem([SECTION_TITLES[t].upper()])
            top.setData(0, Qt.ItemDataRole.UserRole, None)
            top.setData(0, Qt.ItemDataRole.UserRole + 1, t)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            top.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(top)
            order = list(coa.SUBTYPES[t])
            roots = sorted([a for a in kids.get(None, []) if a.type == t],
                           key=lambda a: (order.index(a.subtype) if a.subtype in order else 99, a.code))
            for a in roots:
                add(top, a)
        self.tree.expandAll()

    def _selected(self) -> Account | None:
        item = self.tree.currentItem() if hasattr(self, "tree") else None
        if item is None:
            return None
        aid = item.data(0, Qt.ItemDataRole.UserRole)
        try:
            return self.books.account(aid) if aid is not None else None
        except KeyError:
            return None

    def _selected_section(self) -> str | None:
        item = self.tree.currentItem()
        while item is not None and item.parent() is not None:
            item = item.parent()
        return item.data(0, Qt.ItemDataRole.UserRole + 1) if item else None

    def _context_menu(self, pos) -> None:
        a = self._selected()
        menu = QMenu(self)
        if a:
            menu.addAction("Edit…").triggered.connect(self.edit_account)
            if not a.is_bank:
                menu.addAction(f"New sub-account of “{a.name}”…").triggered.connect(self.new_sub_account)
            menu.addAction("Delete / Merge…").triggered.connect(self.delete_account)
            if a.type not in ("income", "expense"):
                menu.addAction("Opening balance…").triggered.connect(self.opening_balance)
        else:
            t = self._selected_section() or "expense"
            menu.addAction(f"New {coa.TYPE_LABELS[t].lower()} account…").triggered.connect(
                lambda: self.new_account(t))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _done(self) -> None:
        self.refresh()
        self.changed.emit()

    def new_account(self, type_: str | None = None) -> None:
        dlg = AccountDialog(self.books, parent=self, type_=None if type_ == "bank" else type_)
        if type_ == "bank":
            dlg.is_bank.setChecked(True)
        if dlg.exec():
            self._done()

    def new_sub_account(self) -> None:
        a = self._selected()
        if a is None or a.is_bank:
            error(self, "Select an account", "Select the (non-bank) account to add a sub-account under.")
            return
        if AccountDialog(self.books, parent=self, parent_account=a).exec():
            self._done()

    def edit_account(self) -> None:
        a = self._selected()
        if a and AccountDialog(self.books, a, parent=self).exec():
            self._done()

    def delete_account(self) -> None:
        a = self._selected()
        if a is None:
            return
        if a.is_system:
            error(self, "Can't delete", f"“{a.name}” is used by the app ({coa.ROLE_KEYS[a.key].lower()}). "
                                        "You can rename or renumber it instead.")
            return
        if self.books.account_usage(a.id)["sub_accounts"]:
            error(self, "Has sub-accounts", f"Move or delete the sub-accounts of “{a.name}” first.")
            return
        if DeleteAccountDialog(self.books, a, self).exec():
            self._done()

    def opening_balance(self) -> None:
        a = self._selected()
        if a is None:
            error(self, "Select an account", "Select the account to set an opening balance for.")
            return
        if a.type in ("income", "expense"):
            error(self, "Not allowed", "Opening balances apply to asset, liability and equity accounts only.")
            return
        if OpeningBalanceDialog(self.books, a, self).exec():
            self._done()

    def edit_labels(self) -> None:
        if LabelsDialog(self.books, self).exec():
            self.changed.emit()

    def _toggle_builtin(self, on: bool) -> None:
        self.books.builtin_suggestions = on

    def export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export chart of accounts",
                                              f"{self.books.company_name} chart of accounts.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerows(self.books.export_accounts_rows())

    def import_csv(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import chart of accounts", str(Path.home()),
                                              "CSV (*.csv);;All files (*)")
        if not path:
            return
        if not confirm(self, "Import chart of accounts",
                       "Accounts in the file are added, or updated when the number already exists. "
                       "Nothing is deleted. Continue?"):
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:
                added, updated = self.books.import_accounts_rows(list(csv.reader(f)))
        except Exception as e:  # noqa: BLE001
            error(self, "Import failed — nothing was changed", str(e))
            return
        self._done()
        self.window().statusBar().showMessage(f"Chart of accounts: {added} added, {updated} updated.", 10000)
