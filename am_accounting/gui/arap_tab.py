"""AR & AP tab: everything outstanding in receivables and payables, line by line — invoices and bills, and
amounts posted straight to the AR / AP accounts (bank entries, opening balances, journal entries)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView,
                               QLabel, QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import arap
from .. import receivables as rx
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, error

COLS = ["", "Account", "Number", "", "Date", "Due", "Overdue", "Aging", "Amount", "Paid", "Balance", "Description"]
C_PARTY, C_ACCT, C_NUM, C_DOC, C_DATE, C_DUE, C_LATE, C_AGING, C_AMT, C_PAID, C_BAL, C_DESC = range(12)


class Section(QWidget):
    """One list (receivables or payables) with its totals and actions."""

    def __init__(self, tab: "ARAPTab", kind: str):
        super().__init__()
        self.tab, self.kind = tab, kind
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        head = QHBoxLayout()
        self.title = QLabel()
        f = self.title.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.title.setFont(f)
        head.addWidget(self.title)
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        head.addWidget(self.summary, 1)
        new = QPushButton("New Invoice…" if kind == "invoice" else "New Bill…")
        new.clicked.connect(lambda: self.tab.request_new.emit(kind))
        head.addWidget(new)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(COLS))
        labels = list(COLS)
        labels[C_PARTY] = "Customer" if kind == "invoice" else "Vendor"
        labels[C_DOC] = "Invoice on file" if kind == "invoice" else "Bill on file"
        self.table.setHorizontalHeaderLabels(labels)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(C_DESC, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(self.open_item)
        lay.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.aging = QLabel()
        self.aging.setObjectName("hint")
        actions.addWidget(self.aging, 1)
        noun = "Invoice" if kind == "invoice" else "Bill"
        for text, slot, tip in [
                (f"Attach {noun}…", self.attach, f"Attach the {noun.lower()} (PDF, scan or photo); it's copied into "
                                                 f"Invoices & Bills, renamed and sorted"),
                (f"View {noun}", self.view_file, f"Open the {noun.lower()} file"),
                ("Record Payment…", self.record_payment, "Match the bank payment"),
                ("Assign to Customer…" if kind == "invoice" else "Assign to Vendor…", self.name_party,
                 "Assign the selected lines (invoices, bills, bank entries, opening balances) to a customer / "
                 "vendor from the Customers & Vendors list"),
                ("Edit…", self.open_item, "")]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            actions.addWidget(b)
        lay.addLayout(actions)
        self.items: list[arap.Item] = []
        self.row_items: list[arap.Item | None] = []

    def fill(self, as_of: date, search: str, overdue_only: bool, group: str, missing_only: bool = False) -> int:
        books = self.tab.books
        self.items = arap.outstanding(books, self.kind, as_of, search, overdue_only)
        if missing_only:
            self.items = [i for i in self.items if not i.has_document]
        t = arap.totals(books, self.kind, self.items, as_of)
        label = "Accounts Receivable — owed to you" if self.kind == "invoice" else "Accounts Payable — you owe"
        self.title.setText(label)
        missing = sum(1 for i in self.items if i.is_document and not i.has_document)
        noun = "invoice" if self.kind == "invoice" else "bill"
        docs = sum(1 for i in self.items if i.is_document)
        other = len(self.items) - docs
        self.summary.setText(f"  {docs} {noun}{'s' * (docs != 1)}" + (f" + {other} other entr{'ies' if other != 1 else 'y'}"
                                                                       if other else "")
                             + f" · {fmt_money(t.balance_cents)} outstanding · {fmt_money(t.overdue_cents)} overdue · "
                             + (f"{missing} without the {noun} on file" if missing else f"all {noun}s on file"))
        aging = " · ".join(f"{b} {fmt_money(v)}" for b, v in t.buckets.items())
        extra = ""
        if t.not_itemized and not (search or overdue_only or missing_only):
            extra = f"   |   ⚠ {fmt_money(t.not_itemized)} of the {fmt_money(t.ledger_cents)} in the books isn't listed"
        self.aging.setText(f"Aging: {aging}{extra}")

        rows: list[tuple[list[str], int | None, str]] = []  # (values, doc_id, style)
        key = {"party": lambda i: i.party.lower(), "account": lambda i: i.account}.get(group)
        if key:
            groups: dict[str, list[arap.Item]] = {}
            for i in sorted(self.items, key=key):  # stable: keeps customer, account, due-date order
                groups.setdefault(i.party if group == "party" else i.account, []).append(i)
            for name, its in groups.items():
                g = [""] * len(COLS)
                g[C_PARTY if group == "party" else C_ACCT] = name
                g[C_BAL] = fmt_money(sum(i.balance_cents for i in its))
                g[C_DESC] = f"{len(its)} open"
                rows.append((g, None, "group"))
                rows += [(self._vals(i), i, self._style(i)) for i in its]
        else:
            rows = [(self._vals(i), i, self._style(i)) for i in self.items]
        if self.items:
            tot = [""] * len(COLS)
            tot[C_PARTY] = "Total"
            tot[C_AMT] = fmt_money(sum(i.amount_cents for i in self.items))
            tot[C_PAID] = fmt_money(sum(i.paid_cents for i in self.items))
            tot[C_BAL] = fmt_money(t.balance_cents)
            rows.append((tot, None, "total"))
        self.table.setRowCount(len(rows))
        self.row_items = [it for _v, it, _s in rows]
        bold = QFont()
        bold.setBold(True)
        for r, (vals, _doc_id, style) in enumerate(rows):
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                if C_AMT <= c <= C_BAL or c == C_LATE:
                    cell.setTextAlignment(ALIGN_RIGHT)
                if style in ("group", "total"):
                    cell.setFont(bold)
                if c == C_AGING and style not in ("group", "total"):
                    cell.setForeground(NEGATIVE if style == "late" else POSITIVE if v == "Current" else MUTED)
                if c == C_LATE and style == "late":
                    cell.setForeground(NEGATIVE)
                if c == C_DOC and v:
                    cell.setForeground(NEGATIVE if v.startswith("✗") else MUTED if v.startswith("–") else POSITIVE)
                if style == "ledger" and c == C_DATE:
                    cell.setToolTip("Posted straight to the account (not from an invoice / bill); aging counts "
                                    "days since this date")
                self.table.setItem(r, c, cell)
        for c, w in enumerate([190, 170, 95, 130, 92, 92, 70, 70, 105, 95, 110]):
            self.table.setColumnWidth(c, w)
        if not self.items:
            self.table.setRowCount(1)
            empty = QTableWidgetItem("Nothing outstanding" if not (search or overdue_only) else "No matches")
            empty.setForeground(MUTED)
            self.table.setItem(0, C_PARTY, empty)
            self.row_items = [None]
        return t.balance_cents

    @staticmethod
    def _style(i: arap.Item) -> str:
        if not i.is_document:
            return "ledger"
        return "late" if i.days_overdue > 0 else ""

    @staticmethod
    def _vals(i: arap.Item) -> list[str]:
        if i.is_document:
            doc = ("✓ " if i.has_document else "✗ ") + i.document_status
            late = f"{i.days_overdue}d" if i.days_overdue > 0 else ""
        else:
            doc = ("✓ Attached" if i.has_document else "– " + i.document_status)
            late = ""
        return [i.party, i.account, i.number or "—", doc, i.issue_date, i.due_date or "—", late, i.aging,
                fmt_money(i.amount_cents), fmt_money(i.paid_cents) if i.paid_cents else "",
                fmt_money(i.balance_cents), i.description]

    def _item(self) -> arap.Item | None:
        rows = self.table.selectionModel().selectedRows()
        return self.row_items[rows[0].row()] if rows and rows[0].row() < len(self.row_items) else None

    def selected_id(self) -> int | None:
        it = self._item()
        return it.doc_id if it is not None and it.is_document else None

    def open_item(self, *_) -> None:
        it = self._item()
        if it is None:
            return
        if it.is_document:
            self.tab.request_open.emit(self.kind, it.doc_id)
        else:
            self.name_party()

    def _selected_items(self) -> list[arap.Item]:
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        out = []
        for r in rows:
            if r < len(self.row_items) and self.row_items[r] is not None:
                out.append(self.row_items[r])
            elif r < len(self.row_items):  # a group heading: everything under it
                for it in self.row_items[r + 1:]:
                    if it is None:
                        break
                    out.append(it)
        seen, uniq = set(), []
        for it in out:
            k = (it.doc_id, it.key)
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        return uniq

    def name_party(self) -> None:
        items = self._selected_items()
        who = "customer" if self.kind == "invoice" else "vendor"
        if not items:
            error(self, "Select lines", f"Select the line(s) to assign to a {who} (Ctrl / Shift to select several).")
            return
        if AssignDialog(self.tab.books, self.kind, items, self).exec():
            self.tab.refresh()
            self.tab.changed.emit()

    def attach(self) -> None:
        it = self._item()
        noun = "invoice" if self.kind == "invoice" else "bill"
        if it is None:
            error(self, "Select one", "Select the line to attach a file to.")
            return
        from .. import doc_folder as df

        if not df.get_config(self.tab.books).enabled:
            error(self, "No folder yet", "Set up the company folder first (File → Company Folder & Auto-Save) — "
                                         "attached files are kept in its Invoices & Bills folder.")
            return
        paths, _ = QFileDialog.getOpenFileNames(self, f"Attach {noun} for {it.party} {it.number}".strip(),
                                                str(Path.home()),
                                                "Invoices (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.heic *.webp);;"
                                                "All files (*)")
        if not paths:
            return
        try:
            for p in paths:
                arap.attach_to_item(self.tab.books, it, Path(p))
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't attach", str(e))
            return
        self.tab.refresh()
        self.tab.changed.emit()

    def view_file(self) -> None:
        it = self._item()
        if it is None:
            return
        if not it.files:
            error(self, "Not on file", f"There's no file for {it.party} {it.number} yet — use Attach.")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        attached = [p for p, how in it.files if how != "created"] or [p for p, _h in it.files]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(attached[0])))

    def record_payment(self) -> None:
        doc_id = self.selected_id()
        if doc_id is None:
            noun = "invoice" if self.kind == "invoice" else "bill"
            error(self, "Select one", f"Select the {noun} that was paid. (Entries posted straight to the account "
                                      f"are settled by categorizing the payment to the same account.)")
            return
        from . import doc_files
        from .documents_tab import PaymentDialog

        PaymentDialog(self.tab.books, doc_id, self).exec()
        doc_files.sync(self.tab.books, [doc_id])
        self.tab.refresh()
        self.tab.changed.emit()


class AssignDialog(QDialog):
    """Assign AR / AP lines to a customer / vendor from the Customers & Vendors list."""

    def __init__(self, books: Books, kind: str, items: list, parent=None):
        from PySide6.QtWidgets import QCheckBox, QDialogButtonBox, QFormLayout

        super().__init__(parent)
        self.books, self.kind, self.items = books, kind, items
        who = "Customer" if kind == "invoice" else "Vendor"
        self.setWindowTitle(f"Assign to {who}")
        self.resize(520, 0)
        form = QFormLayout(self)
        total = sum(i.balance_cents for i in items)
        first = items[0]
        what = (f"{first.issue_date} · {first.description[:60]} · {fmt_money(first.amount_cents)}" if len(items) == 1
                else f"{len(items)} lines · {fmt_money(total)}")
        form.addRow(QLabel(what))
        row = QHBoxLayout()
        self.contact = QComboBox()
        self.contact.setEditable(True)
        self.contact.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.contact.lineEdit().setPlaceholderText(f"{who} name — pick from the list or type a new one")
        self._fill()
        current = {i.party for i in items}
        if len(current) == 1:
            self.contact.setCurrentText(current.pop())
        row.addWidget(self.contact, 1)
        new = QPushButton(f"New {who}…")
        new.clicked.connect(self.new_contact)
        row.addWidget(new)
        form.addRow(f"{who}:", row)
        self.move = QCheckBox()
        form.addRow("", self.move)
        self.contact.currentTextChanged.connect(self._update_move)
        self._update_move()
        hint = QLabel("Invoices and bills get this as their customer / vendor; bank lines and other entries are "
                      "named after it everywhere (To Categorize, Transactions).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Assign")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.setMinimumHeight(self.sizeHint().height() + 30)

    def _fill(self) -> None:
        from .. import contacts

        role = "customer" if self.kind == "invoice" else "vendor"
        text = self.contact.currentText()
        self.contact.clear()
        self.contact.addItems([c.name for c in contacts.all_contacts(self.books, role, active_only=True)])
        self.contact.setCurrentText(text)

    def _update_move(self, *_) -> None:
        from .. import contacts

        c = contacts.find(self.books, self.contact.currentText())
        acct = (c.receivable_id if self.kind == "invoice" else c.payable_id) if c else None
        movable = [i for i in self.items if i.is_document or i.tx_id is not None]
        if acct and movable:
            name = self.books.account(acct).name
            self.move.setText(f"Also move them to {c.name}'s account: {name}")
            self.move.setChecked(any(i.account != name for i in movable))
            self.move.setVisible(True)
        else:
            self.move.setChecked(False)
            self.move.setVisible(False)

    def new_contact(self) -> None:
        from .contacts_tab import ContactDialog

        role = "customer" if self.kind == "invoice" else "vendor"
        dlg = ContactDialog(self.books, role=role, parent=self)
        dlg.name.setText(self.contact.currentText())
        if dlg.exec():
            self._fill()
            self.contact.setCurrentText(dlg.contact.name)
            self._update_move()

    def save(self) -> None:
        name = self.contact.currentText().strip()
        if not name:
            error(self, "Choose one", "Choose or type the customer / vendor.")
            return
        try:
            arap.assign(self.books, self.items, name, move_account=not self.move.isHidden() and self.move.isChecked())
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't assign", str(e))
            return
        self.accept()


class ARAPTab(QWidget):
    changed = Signal()
    request_new = Signal(str)         # "invoice" | "bill"
    request_open = Signal(str, int)   # kind, document id

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("As of"))
        self.as_of = QDateEdit(calendarPopup=True)
        self.as_of.setDisplayFormat("MMM d, yyyy")
        self.as_of.setDate(QDate.currentDate())
        self.as_of.dateChanged.connect(self.refresh)
        bar.addWidget(self.as_of)
        bar.addWidget(QLabel("Show:"))
        self.show_which = QComboBox()
        for label, key in [("All outstanding", "all"), ("Overdue only", "overdue"),
                           ("Missing the invoice / bill", "missing")]:
            self.show_which.addItem(label, key)
        self.show_which.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.show_which)
        bar.addWidget(QLabel("Group:"))
        self.group = QComboBox()
        for label, key in [("By customer / vendor", "party"), ("By AR / AP account", "account"),
                           ("Line by line (customer A–Z)", None)]:
            self.group.addItem(label, key)
        self.group.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.group)
        self.search = QLineEdit(placeholderText="Search name, number or description…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        bar.addWidget(self.search, 1)
        export = QPushButton("Export Excel…")
        export.clicked.connect(self.export)
        bar.addWidget(export)
        lay.addLayout(bar)
        self.net = QLabel()
        self.net.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.net)
        split = QSplitter(Qt.Orientation.Vertical)
        self.ar = Section(self, "invoice")
        self.ap = Section(self, "bill")
        split.addWidget(self.ar)
        split.addWidget(self.ap)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        lay.addWidget(split, 1)
        self.refresh()

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def _as_of(self) -> date:
        return date.fromisoformat(self.as_of.date().toString("yyyy-MM-dd"))

    def refresh(self) -> None:
        rx.ensure_schema(self.books)
        args = (self._as_of(), self.search.text().strip(), self.show_which.currentData() == "overdue",
                self.group.currentData(), self.show_which.currentData() == "missing")
        ar = self.ar.fill(*args)
        ap = self.ap.fill(*args)
        net = ar - ap
        from .theme import c as tc

        colour = tc("green") if net >= 0 else tc("red")
        self.net.setText(f"Owed to you <b>{fmt_money(ar)}</b> &nbsp;−&nbsp; you owe <b>{fmt_money(ap)}</b> "
                         f"&nbsp;=&nbsp; net <b style='color:{colour}'>{fmt_money(net)}</b>")

    def export(self) -> None:
        as_of = self._as_of()
        name = f"AR and AP outstanding {as_of.isoformat()}.xlsx"
        start = Path.home() / name
        from .. import workspace as ws

        cfg = ws.get_config(self.books)
        if cfg.enabled:
            start = cfg.dir("reports") / name
        path, _ = QFileDialog.getSaveFileName(self, "Export AR & AP", str(start), "Excel (*.xlsx)")
        if not path:
            return
        try:
            arap.write_xlsx(self.books, Path(path), as_of)
        except PermissionError:
            error(self, "Couldn't save", "The file is open in Excel. Close it and try again.")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
