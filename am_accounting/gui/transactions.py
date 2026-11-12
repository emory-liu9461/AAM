from __future__ import annotations

from datetime import date

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget)

from ..books import Books, Transaction
from ..categorizer import Categorizer
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, account_label, confirm, error, fill_account_combo

COLUMNS = ["Date", "Bank Account", "Description", "Customer / Vendor", "Amount", "Category", "Source", "Reviewed"]
C_DATE, C_BANK, C_DESC, C_PARTY, C_AMT, C_CAT, C_SRC, C_REV = range(8)
SOURCE_LABELS = {"manual": "You", "rule": "Rule", "learned": "Learned", "keyword": "Suggested", "loan": "Loan",
                 "journal": "Journal", None: ""}


NO_NAME = "\x00no-name"     # the name filter: transactions with no customer / vendor


class JournalRow:
    """A journal entry (not through a bank account — e.g. a mortgage put on the balance sheet, depreciation, an
    accrual) shown among the transactions; double-click to change it."""

    def __init__(self, books: Books, entry: dict):
        self.entry = entry
        self.id = -entry["id"]                 # (negative: not a bank transaction)
        self.date = entry["date"]
        self.description = entry["memo"] or "Journal entry"
        self.memo = ""
        self.amount_cents = sum(d for _a, d, _c in entry["lines"])
        self.category_id = None
        self.category_source = "journal"
        self.reviewed = True
        self.manual = False
        self.bank_account_id = None
        self.parties = [p for p in (entry.get("parties") or []) if p]

        def name(a):
            try:
                return books.account(a).name
            except KeyError:
                return "?"
        self.accounts = " · ".join(f"{name(a)} {'Dr' if d else 'Cr'} {fmt_money(d or c, parens=False)}"
                                   for a, d, c in entry["lines"])
        self.account_ids = {a for a, _d, _c in entry["lines"]}


def show_unnamed(tab) -> None:
    """Transactions with no customer / vendor (all of them, not just those to review)."""
    i = tab.status_filter.findData("all") if tab.status_filter.findData("all") >= 0 else 0
    tab.status_filter.setCurrentIndex(i)
    tab.refresh()
    tab.name_filter.setCurrentIndex(max(0, tab.name_filter.findData(NO_NAME)))
    tab.refresh()


class TransactionModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        self.rows: list[Transaction] = []
        self.names: dict[int, str] = {}

    def set_books(self, books: Books) -> None:
        self.books = books

    def load(self, rows: list[Transaction], names: dict | None = None) -> None:
        self.beginResetModel()
        self.rows = rows
        self.names = names or {}
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return COLUMNS[section]
        if role == Qt.ItemDataRole.TextAlignmentRole and section == C_AMT:
            return ALIGN_RIGHT
        return None

    def flags(self, index):
        f = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if isinstance(self.rows[index.row()], JournalRow):
            return f                            # changed in its own window (double-click)
        if index.column() == C_CAT:
            f |= Qt.ItemFlag.ItemIsEditable
        if index.column() == C_REV:
            f |= Qt.ItemFlag.ItemIsUserCheckable
        return f

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        tx = self.rows[index.row()]
        c = index.column()
        if isinstance(tx, JournalRow):
            return self._journal_data(tx, c, role)
        if role == Qt.ItemDataRole.DisplayRole:
            if c == C_DATE:
                return tx.date
            if c == C_BANK:
                return self.books.account(tx.bank_account_id).name
            if c == C_DESC:
                return ("✎ " if tx.manual else "") + tx.description + (f"  — {tx.memo}" if tx.memo else "")
            if c == C_PARTY:
                return self.names.get(tx.id, "")
            if c == C_AMT:
                return fmt_money(tx.amount_cents, parens=False)
            if c == C_CAT:
                parts = self.books.splits(tx.id)
                if parts:
                    return " + ".join(f"{self.books.account(a).name} {fmt_money(cents)}" for a, cents, _m, _r in parts)
                return account_label(self.books, tx.category_id) or "Uncategorized"
            if c == C_SRC:
                return SOURCE_LABELS.get(tx.category_source, tx.category_source)
        if role == Qt.ItemDataRole.EditRole and c == C_CAT:
            return tx.category_id
        if role == Qt.ItemDataRole.CheckStateRole and c == C_REV:
            return Qt.CheckState.Checked if tx.reviewed else Qt.CheckState.Unchecked
        if role == Qt.ItemDataRole.TextAlignmentRole and c == C_AMT:
            return ALIGN_RIGHT
        if role == Qt.ItemDataRole.ForegroundRole:
            if c == C_AMT:
                return POSITIVE if tx.amount_cents > 0 else NEGATIVE
            if c == C_CAT and tx.category_id is None:
                return MUTED
            if c == C_SRC:
                return MUTED
        if role == Qt.ItemDataRole.ToolTipRole and c == C_DESC:
            return tx.description + ("\n\nEntered by hand" if tx.manual else "") + \
                "\n\nDouble-click to edit the customer / vendor, category and note"
        if role == Qt.ItemDataRole.ToolTipRole and c == C_PARTY:
            return "Double-click to set the customer / vendor"
        return None

    def _journal_data(self, j: JournalRow, c: int, role):
        if role == Qt.ItemDataRole.DisplayRole:
            return {C_DATE: j.date, C_BANK: "Journal entry", C_DESC: j.description, C_PARTY: ", ".join(j.parties),
                    C_AMT: fmt_money(j.amount_cents, parens=False), C_CAT: j.accounts, C_SRC: "Journal"}.get(c)
        if role == Qt.ItemDataRole.TextAlignmentRole and c == C_AMT:
            return ALIGN_RIGHT
        if role == Qt.ItemDataRole.ForegroundRole and c in (C_BANK, C_SRC):
            return MUTED
        if role == Qt.ItemDataRole.ToolTipRole:
            return (f"{j.description}\n\n" + "\n".join(j.accounts.split(" · "))
                    + "\n\nA journal entry (not through a bank account) — double-click to change its lines")
        return None

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        tx = self.rows[index.row()]
        if isinstance(tx, JournalRow):
            return False
        if index.column() == C_CAT and role == Qt.ItemDataRole.EditRole:
            if value == "__sep__":
                return False
            try:
                self.books.set_category(tx.id, value, source="manual", reviewed=True)
            except ValueError as e:
                error(None, "Can't categorize", str(e))
                return False
            self.rows[index.row()] = self.books.transaction(tx.id)
            self.dataChanged.emit(self.index(index.row(), 0), self.index(index.row(), len(COLUMNS) - 1))
            self.changed.emit()
            return True
        if index.column() == C_REV and role == Qt.ItemDataRole.CheckStateRole:
            checked = Qt.CheckState(value) == Qt.CheckState.Checked
            self.books.set_reviewed([tx.id], checked)
            self.rows[index.row()] = self.books.transaction(tx.id)
            self.dataChanged.emit(index, index)
            self.changed.emit()
            return True
        return False


class CategoryDelegate(QStyledItemDelegate):
    def __init__(self, books_getter, parent=None):
        super().__init__(parent)
        self.books_getter = books_getter

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.setMaxVisibleItems(25)
        tx = index.model().rows[index.row()]
        fill_account_combo(combo, self.books_getter(), exclude={tx.bank_account_id})
        return combo

    def setEditorData(self, editor, index):
        i = editor.findData(index.data(Qt.ItemDataRole.EditRole))
        if i >= 0:
            editor.setCurrentIndex(i)
        editor.showPopup()

    def setModelData(self, editor, model, index):
        model.setData(index, editor.currentData(), Qt.ItemDataRole.EditRole)


class TransactionsTab(QWidget):
    changed = Signal()
    request_import = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        layout = QVBoxLayout(self)

        # Filters
        bar = QHBoxLayout()
        self.bank_filter = QComboBox()
        self.status_filter = QComboBox()
        for label, key in [("All transactions", "all"), ("Uncategorized", "uncategorized"),
                           ("Needs review", "unreviewed"), ("Reviewed", "reviewed"),
                           ("Journal entries (non-cash)", "journal")]:
            self.status_filter.addItem(label, key)
        self.status_filter.setCurrentIndex(0)    # all of them, journal entries (non-cash) included
        self.search = QLineEdit(placeholderText="Search description, category, name, amount…")
        self.search.setClearButtonEnabled(True)
        self.name_filter = QComboBox()
        self.name_filter.setToolTip("Only transactions with / from this person or company")
        self.name_filter.setMinimumWidth(170)
        bar.addWidget(QLabel("Account:"))
        bar.addWidget(self.bank_filter)
        bar.addWidget(QLabel("Show:"))
        bar.addWidget(self.status_filter)
        bar.addWidget(QLabel("Name:"))
        bar.addWidget(self.name_filter)
        bar.addWidget(self.search, 1)
        add_btn = QPushButton("Add Transaction…")
        add_btn.setToolTip("Enter a bank or cash transaction by hand (not from a statement)")
        add_btn.clicked.connect(self.add_transaction)
        bar.addWidget(add_btn)
        loans_btn = QPushButton("Loans…")
        loans_btn.setToolTip("Find and record loan payments (principal + interest) and advances for the loans in "
                             "Asset Management")
        loans_btn.clicked.connect(self.loan_payments)
        bar.addWidget(loans_btn)
        je_btn = QPushButton("Journal Entries…")
        je_btn.setToolTip("Entries by hand with debit and credit lines: depreciation, accruals, corrections…")
        je_btn.clicked.connect(self.journal_entries)
        bar.addWidget(je_btn)
        import_btn = QPushButton("Import Statement…")
        import_btn.setObjectName("primary")
        import_btn.clicked.connect(self.request_import)
        bar.addWidget(import_btn)
        layout.addLayout(bar)

        # Table
        self.model = TransactionModel(books)
        self.model.changed.connect(self._on_model_changed)
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setItemDelegateForColumn(C_CAT, CategoryDelegate(lambda: self.books, self.table))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked
                                   | QAbstractItemView.EditTrigger.SelectedClicked
                                   | QAbstractItemView.EditTrigger.EditKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(C_DESC, QHeaderView.ResizeMode.Stretch)
        for c, w in [(C_DATE, 95), (C_BANK, 140), (C_PARTY, 170), (C_AMT, 110), (C_CAT, 240), (C_SRC, 80),
                     (C_REV, 75)]:
            self.table.setColumnWidth(c, w)
        self.table.doubleClicked.connect(self._double_clicked)
        layout.addWidget(self.table, 1)

        # Bulk actions
        actions = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        actions.addWidget(self.summary, 1)
        self.bulk_category = QComboBox()
        self.bulk_category.setMinimumWidth(240)
        actions.addWidget(QLabel("Set category for selected:"))
        actions.addWidget(self.bulk_category)
        apply_btn = QPushButton("Apply")
        apply_btn.clicked.connect(self.apply_bulk_category)
        actions.addWidget(apply_btn)
        who_btn = QPushButton("Set Customer / Vendor…")
        who_btn.setToolTip("Set who the selected transactions were with (one or many)")
        who_btn.clicked.connect(self.set_party_selected)
        actions.addWidget(who_btn)
        split_btn = QPushButton("Split…")
        split_btn.setToolTip("Spread the selected transaction over several categories")
        split_btn.clicked.connect(self.split_selected)
        actions.addWidget(split_btn)
        attach_btn = QPushButton("Open 📎")
        attach_btn.setToolTip("Open the invoice or receipt matched to the selected transaction (from the Invoice Dump)")
        attach_btn.clicked.connect(self.open_attachment)
        actions.addWidget(attach_btn)
        review_btn = QPushButton("Mark Reviewed")
        review_btn.clicked.connect(self.mark_reviewed)
        actions.addWidget(review_btn)
        auto_btn = QPushButton("Auto-Categorize")
        auto_btn.setToolTip("Apply your rules, what was learned from your past choices, and built-in suggestions")
        auto_btn.clicked.connect(self.auto_categorize)
        actions.addWidget(auto_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self.delete_selected)
        actions.addWidget(del_btn)
        layout.addLayout(actions)

        hint = QLabel("Double-click a transaction to edit its customer / vendor, category and note (or double-click "
                      "the category to change just that). Your choices are remembered for future transactions.")
        hint.setObjectName("hint")
        layout.addWidget(hint)

        self.bank_filter.currentIndexChanged.connect(self.refresh)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.name_filter.currentIndexChanged.connect(self.refresh)
        self.set_books(books)

    def set_books(self, books: Books) -> None:
        self.books = books
        self.model.set_books(books)
        self.reload_accounts()

    def reload_accounts(self) -> None:
        fill_account_combo(self.bank_filter, self.books, include_none="All bank accounts", banks_only=True)
        fill_account_combo(self.bulk_category, self.books, include_none="Choose category…")
        self.refresh()

    def open_attachment(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from .. import cheques
        from ..invoice_dump import files_for_transaction

        for tx_id in self.selected_ids()[:1]:
            files = [p for p in files_for_transaction(self.books, tx_id) + cheques.files_for_transaction(self.books, tx_id)
                     if p.exists()]
            if not files:
                self.window().statusBar().showMessage(
                    "No invoice, receipt or cheque is matched to this transaction yet (drop it in the Invoice Dump "
                    "or Cheques & Drafts folder).", 8000)
            for p in files:
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def refresh(self) -> None:
        from .. import parties

        status = self.status_filter.currentData()
        bank = self.bank_filter.currentData()
        rows = [] if status == "journal" else self.books.transactions(bank_account_id=bank, status=status)
        if status in ("all", "journal"):        # journal entries too: everything on the books, not only the bank's
            rows = rows + [j for j in (JournalRow(self.books, e) for e in self.books.manual_entries())
                           if bank is None or bank in j.account_ids]
            rows.sort(key=lambda t: t.date, reverse=True)
        names = {i: n.name for i, n in parties.names(self.books).items()}
        names.update({t.id: ", ".join(t.parties) for t in rows if isinstance(t, JournalRow) and t.parties})
        q = self.search.text().strip().lower()
        if q:  # every word must appear in the description, memo, category, name, bank account or amount
            acct_names = {a.id: f"{a.code} {a.name} {a.label}".lower() for a in self.books.accounts()}

            def haystack(t) -> str:
                if isinstance(t, JournalRow):
                    return " ".join([t.description, t.accounts, names.get(t.id, ""), "journal entry",
                                     fmt_money(t.amount_cents, parens=False), f"{t.amount_cents / 100:.2f}",
                                     t.date]).lower().replace("&", " and ")
                cat = acct_names.get(t.category_id, "uncategorized") if t.category_id else "uncategorized"
                return " ".join([t.description, t.memo, cat, names.get(t.id, ""), acct_names.get(t.bank_account_id, ""),
                                 fmt_money(t.amount_cents, parens=False), f"{abs(t.amount_cents) / 100:.2f}",
                                 t.date]).lower().replace("&", " and ")
            words = q.replace("&", " and ").split()
            rows = [t for t in rows if all(w in haystack(t) for w in words)]
        want = self.name_filter.currentData()
        self.name_filter.blockSignals(True)
        self.name_filter.clear()
        self.name_filter.addItem("Everyone", None)
        self.name_filter.addItem("(no customer / vendor)", NO_NAME)
        for n in sorted({v for v in names.values()}, key=str.lower):
            self.name_filter.addItem(n, n)
        i = self.name_filter.findData(want)
        self.name_filter.setCurrentIndex(i if i >= 0 else 0)
        self.name_filter.blockSignals(False)
        want = self.name_filter.currentData()
        if want == NO_NAME:
            rows = [t for t in rows if not names.get(t.id)]
        elif want:
            rows = [t for t in rows if names.get(t.id, "").lower() == want.lower()]
        self.model.load(rows, names)
        all_tx = self.books.transactions()
        unc = sum(1 for t in all_tx if t.category_id is None)
        unrev = sum(1 for t in all_tx if not t.reviewed)
        self.summary.setText(f"Showing {len(rows)} · {len(all_tx)} total · {unc} uncategorized · {unrev} to review")

    def _double_clicked(self, index) -> None:
        row = self.model.rows[index.row()]
        if isinstance(row, JournalRow):
            self.edit_journal_entry(row.entry)
            return
        if index.column() in (C_CAT, C_REV):
            return  # the category has its own drop-down; Reviewed is a tick box
        self.edit_transaction(self.model.rows[index.row()].id)

    def edit_journal_entry(self, entry: dict, *, run=None) -> bool:
        """Change a journal entry's date, description and lines (``run`` for tests takes the dialog)."""
        from .manual_entry import JournalEntryDialog

        dlg = JournalEntryDialog(self.books, entry, self)
        if run(dlg) if run is not None else dlg.exec():
            self.refresh()
            self.changed.emit()
            return True
        return False

    def edit_transaction(self, tx_id: int) -> bool:
        from .transaction_dialog import TransactionDialog

        if TransactionDialog(self.books, tx_id, self).exec():
            self.refresh()
            self.changed.emit()
            return True
        return False

    def split_selected(self) -> None:
        from .common import info
        from .transaction_dialog import SplitDialog

        ids = self.selected_ids()
        if len(ids) != 1:
            info(self, "Split", "Select one transaction to split over several categories.")
            return
        if SplitDialog(self.books, ids[0], self).exec():
            self.refresh()
            self.changed.emit()

    def add_transaction(self) -> None:
        from .manual_entry import ManualTransactionDialog

        bank = self.bank_filter.currentData()
        if ManualTransactionDialog(self.books, parent=self, bank_id=bank if isinstance(bank, int) else None).exec():
            self.refresh()
            self.changed.emit()

    def loan_payments(self) -> None:
        from .loans_dialog import LoanPaymentsDialog

        dlg = LoanPaymentsDialog(self.books, self)
        dlg.exec()
        if dlg.changed:
            self.reload_accounts()
            self.refresh()
            self.changed.emit()

    def journal_entries(self) -> None:
        from .manual_entry import JournalEntriesDialog

        dlg = JournalEntriesDialog(self.books, self)
        dlg.exec()
        if dlg.changed:
            self.refresh()
            self.changed.emit()

    def set_party_selected(self) -> None:
        ids = self.selected_ids()
        if not ids:
            error(self, "Select transactions", "Select the transaction(s) to set the customer / vendor for.")
            return
        if len(ids) == 1:
            self.edit_transaction(ids[0])
            return
        from PySide6.QtWidgets import QInputDialog

        from .. import contacts, parties

        names = [c.name for c in contacts.all_contacts(self.books, active_only=True)]
        names += [n for n in parties.all_names(self.books) if n not in names]
        name, ok = QInputDialog.getItem(self, "Set customer / vendor",
                                        f"Customer / vendor for the {len(ids)} selected transactions "
                                        f"(type a new name, or blank to clear):", [""] + names, 0, True)
        if not ok:
            return
        name = " ".join(name.split())
        if name:
            money_in = self.books.transaction(ids[0]).amount_cents > 0
            contacts.ensure_contact(self.books, name, "customer" if money_in else "vendor")
        parties.set_name(self.books, ids, name)
        parties.apply_learned(self.books)
        self.refresh()
        self.changed.emit()

    def _on_model_changed(self) -> None:
        self.changed.emit()

    def selected_ids(self) -> list[int]:
        """The bank transactions selected (journal entries left out: they're changed in their own window)."""
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.rows[r].id for r in rows if not isinstance(self.model.rows[r], JournalRow)]

    def selected_entries(self) -> list[dict]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        return [self.model.rows[r].entry for r in rows if isinstance(self.model.rows[r], JournalRow)]

    def apply_bulk_category(self) -> None:
        cat = self.bulk_category.currentData()
        ids = self.selected_ids()
        if not ids or cat in (None, "__sep__"):
            return
        skipped = 0
        with self.books.batch():
            for i in ids:
                try:
                    self.books.set_category(i, cat, source="manual", reviewed=True)
                except ValueError:
                    skipped += 1
        if skipped:
            error(self, "Some transactions skipped",
                  f"{skipped} transaction(s) belong to that bank account and can't be categorized to it.")
        self.refresh()
        self.changed.emit()

    def mark_reviewed(self) -> None:
        ids = self.selected_ids()
        if ids:
            self.books.set_reviewed(ids, True)
            self.refresh()
            self.changed.emit()

    def auto_categorize(self) -> None:
        stats = Categorizer(self.books).run(overwrite_auto=True)
        self.refresh()
        self.changed.emit()
        done = sum(v for k, v in stats.items() if k != "unmatched")
        self.window().statusBar().showMessage(
            f"Auto-categorized {done} transaction(s): {stats.get('rule', 0)} by rule, "
            f"{stats.get('learned', 0)} learned, {stats.get('keyword', 0)} suggested. "
            f"{stats.get('unmatched', 0)} need your attention.", 10000)

    def delete_selected(self) -> None:
        ids, entries = self.selected_ids(), self.selected_entries()
        n = len(ids) + len(entries)
        if n and confirm(self, "Delete transactions", f"Delete {n} transaction(s)"
                                                      + (f" ({len(entries)} journal entr{'y' if len(entries) == 1 else 'ies'})"
                                                         if entries else "") + "? This can't be undone."):
            if ids:
                self.books.delete_transactions(ids)
            for e in entries:
                self.books.delete_journal_entry(e["id"])
            self.refresh()
            self.changed.emit()
