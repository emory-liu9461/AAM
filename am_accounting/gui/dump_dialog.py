"""Invoice Dump window: what was read from each dropped invoice/receipt and which bank transaction it matches."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import invoice_dump as dump
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, confirm, error, fill_account_combo


def _open(path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def _settings():
    from PySide6.QtCore import QSettings

    return QSettings("AM Accounting", "AM Accounting")


def process_all_bills(books: Books, parent=None, *, record_unpaid: bool | None = None,
                      quiet: bool = False) -> dump.RunSummary:
    """Read everything in the Invoice Dump and record the expenses, with a progress bar and a summary."""
    from PySide6.QtWidgets import QMessageBox, QProgressDialog

    if record_unpaid is None:
        record_unpaid = _settings().value("dump/record_unpaid", True, type=bool)
    total = len(dump.pending_files(books))
    bar = QProgressDialog("Reading bills…", None, 0, max(total, 1), parent)
    bar.setWindowTitle("Process All Bills")
    bar.setMinimumDuration(0)
    bar.setValue(0)

    def progress(n, count, name):
        bar.setMaximum(count)
        bar.setValue(n - 1)
        bar.setLabelText(f"Reading {n} of {count}: {name}")
        QApplication.processEvents()

    QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
    try:
        summary = dump.run_all(books, record_unpaid=record_unpaid, progress=progress)
    except Exception as e:  # noqa: BLE001
        summary = dump.RunSummary(attention=[f"Stopped: {e}"])
    finally:
        QApplication.restoreOverrideCursor()
        bar.close()
    if not quiet:
        lines = [f"<b>{summary.describe()}</b>"]
        if summary.bills:
            lines.append(f"Recorded as future payables (bills in Accounts Payable, files in "
                         f"{dump.DUMP_DIR}/{dump.PAYABLES_DIR}): " + ", ".join(summary.bills))
        if summary.attention:
            lines.append("Needs you (Bookkeeping → Invoice Dump):<br>• " + "<br>• ".join(summary.attention[:12]))
        box = QMessageBox(parent)
        box.setWindowTitle("Process All Bills")
        box.setTextFormat(Qt.TextFormat.RichText)
        box.setText("<br><br>".join(lines))
        box.exec()
    return summary


class MatchByHandDialog(QDialog):
    """Pick the bank transaction an invoice/receipt belongs to."""

    def __init__(self, books: Books, item: dump.DumpItem, parent=None):
        super().__init__(parent)
        self.books, self.item = books, item
        self.setWindowTitle(f"Match {item.original_name}")
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        read = (f"Read from the document: total <b>{fmt_money(item.amount_cents) if item.amount_cents else '—'}</b>"
                f" · date <b>{item.doc_date or '—'}</b> · from <b>{item.party or '—'}</b>"
                f"{' · number <b>' + item.number + '</b>' if item.number else ''}")
        lay.addWidget(QLabel(read))
        row = QHBoxLayout()
        self.search = QLineEdit(placeholderText="Search bank transactions…")
        self.search.textChanged.connect(self.refresh)
        self.show_all = QComboBox()
        self.show_all.addItem("Same amount first", False)
        self.show_all.addItem("All transactions", True)
        self.show_all.currentIndexChanged.connect(self.refresh)
        row.addWidget(self.search, 1)
        row.addWidget(self.show_all)
        lay.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Bank transaction", "Amount", "Why"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.accept_choice)
        lay.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Match", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.accept_choice)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.refresh()

    def refresh(self) -> None:
        info = dump.DocInfo(self.item.amount_cents, [], date.fromisoformat(self.item.doc_date)
                            if self.item.doc_date else None, self.item.number, self.item.party)
        exclude = dump.linked_transaction_ids(self.books)
        cands = dump.candidates(self.books, info, exclude=exclude, any_amount=bool(self.show_all.currentData())
                                or not info.total)
        if not cands and info.total:
            cands = dump.candidates(self.books, info, exclude=exclude, any_amount=True)
        needle = self.search.text().strip().upper()
        if needle:
            cands = [c for c in cands if needle in c.tx.description.upper()]
        self.cands = cands[:300]
        self.table.setRowCount(len(self.cands))
        for r, c in enumerate(self.cands):
            amt = QTableWidgetItem(fmt_money(c.tx.amount_cents, parens=False))
            amt.setTextAlignment(ALIGN_RIGHT)
            amt.setForeground(POSITIVE if c.tx.amount_cents > 0 else NEGATIVE)
            for col, it in enumerate([QTableWidgetItem(c.tx.date), QTableWidgetItem(c.tx.description), amt,
                                      QTableWidgetItem(c.why)]):
                self.table.setItem(r, col, it)
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(2, 110)
        self.table.setColumnWidth(3, 220)
        if self.cands:
            self.table.selectRow(0)

    def accept_choice(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        dump.link(self.books, self.item.id, self.cands[rows[0].row()].tx.id)
        self.accept()


class FuturePayableDialog(QDialog):
    """Not paid yet: enter it as a bill in Accounts Payable and keep the file in Future Payables."""

    def __init__(self, books: Books, item: dump.DumpItem, parent=None):
        from datetime import timedelta

        super().__init__(parent)
        self.books, self.item = books, item
        self.setWindowTitle(f"Future payable – {item.original_name}")
        form = QFormLayout(self)
        intro = QLabel(f"<b>{fmt_money(item.amount_cents or 0)}</b> isn't in a bank statement yet. It will be "
                       f"recorded as a bill in Accounts Payable and the file moved to "
                       f"<b>{dump.DUMP_DIR}/{dump.PAYABLES_DIR}</b>. When the payment shows up in a statement the "
                       f"bill is marked paid and the invoice moves to <b>{dump.SAVED_DIR}</b>.")
        intro.setWordWrap(True)
        form.addRow(intro)
        self.vendor = QLineEdit(item.party or "")
        issued = date.fromisoformat(item.doc_date) if item.doc_date else date.today()
        due = issued + timedelta(days=30)
        self.due = QDateEdit(QDate(due.year, due.month, due.day))
        self.due.setCalendarPopup(True)
        self.account = QComboBox()
        fill_account_combo(self.account, books, include_none="Suggest from past bills")
        form.addRow("Vendor:", self.vendor)
        form.addRow("Due date:", self.due)
        form.addRow("Expense account:", self.account)
        from .control_account import ControlAccountPicker

        self.control = ControlAccountPicker(books, "bill")
        self.control.follow_party(item.party or "")
        self.vendor.textChanged.connect(self.control.follow_party)
        form.addRow("Payable account:", self.control)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Record Future Payable")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        try:
            dump.mark_payable(self.books, self.item.id, category_id=self.account.currentData(),
                              due_date=self.due.date().toString("yyyy-MM-dd"),
                              vendor=self.vendor.text().strip() or None, control_id=self.control.value())
        except (ValueError, KeyError) as e:
            error(self, "Couldn't record the future payable", str(e))
            return
        self.accept()


class FixDetailsDialog(QDialog):
    """Correct a misread total or date, then try matching again."""

    def __init__(self, books: Books, item: dump.DumpItem, parent=None):
        super().__init__(parent)
        self.books, self.item = books, item
        self.setWindowTitle(f"Correct details – {item.original_name}")
        form = QFormLayout(self)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1e10)
        self.amount.setDecimals(2)
        self.amount.setPrefix("$ ")
        self.amount.setGroupSeparatorShown(True)
        self.amount.setValue((item.amount_cents or 0) / 100)
        d = date.fromisoformat(item.doc_date) if item.doc_date else date.today()
        self.date = QDateEdit(QDate(d.year, d.month, d.day))
        self.date.setCalendarPopup(True)
        self.party = QLineEdit(item.party)
        form.addRow("Total:", self.amount)
        form.addRow("Date:", self.date)
        form.addRow("From:", self.party)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        dump.set_details(self.books, self.item.id, amount_cents=round(self.amount.value() * 100) or None,
                         doc_date=self.date.date().toString("yyyy-MM-dd"), party=self.party.text().strip())
        self.accept()


class InvoiceDumpDialog(QDialog):
    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Invoice Dump")
        self.resize(1120, 600)
        lay = QVBoxLayout(self)
        root = dump.dump_root(books)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.intro)
        if root is None:
            self.intro.setText("Set up the company folder first (File → Company Folder & Auto-Save). It includes "
                               "an <b>Invoice Dump</b> folder for invoices, bills and receipts.")
        row = QHBoxLayout()
        self.filter = QComboBox()
        for label, key in [("Everything", None), ("Not yet matched", "unmatched"),
                           ("Recognized (expense not recorded yet)", "recognized"), ("Saved", "saved"),
                           ("Future payables", "payable"), ("Not related", "unrelated")]:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        row.addWidget(QLabel("Show:"))
        row.addWidget(self.filter)
        row.addStretch(1)
        from PySide6.QtWidgets import QCheckBox

        self.record_unpaid = QCheckBox("Record unpaid bills in Accounts Payable")
        self.record_unpaid.setToolTip("Bills with no bank payment yet are recorded as future payables (Accounts "
                                      "Payable, files in Future Payables); they're marked paid when the payment "
                                      "shows up in a statement.")
        self.record_unpaid.setChecked(_settings().value("dump/record_unpaid", True, type=bool))
        self.record_unpaid.toggled.connect(lambda on: _settings().setValue("dump/record_unpaid", on))
        row.addWidget(self.record_unpaid)
        run = QPushButton("Process All Bills")
        run.setObjectName("primary")
        run.setToolTip("Read every file in the Invoice Dump, match it to its bank payment and record the expense")
        run.clicked.connect(self.process_all)
        run.setEnabled(root is not None)
        row.addWidget(run)
        for text, slot in [("Open Folder", self.open_folder)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            b.setEnabled(root is not None)
            row.addWidget(b)
        lay.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Status", "File", "Date", "Total", "From", "Bank transaction / why not",
                                              ""])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_file)
        self.table.setColumnHidden(6, True)
        lay.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        actions.addWidget(self.summary, 1)
        self.buttons = {}
        for text, slot, tip in [
                ("Open File", self.open_file, "Open the selected file"),
                ("Match by Hand…", self.match_by_hand, "Pick the bank transaction it belongs to"),
                ("Future Payable…", self.future_payable,
                 "Not paid yet: record it as a bill in Accounts Payable and move it to Future Payables"),
                ("Not Related", self.not_related,
                 "Wrong invoice / not this company's: move it to Unrelated and leave it out of the books"),
                ("Correct Details…", self.fix_details, "Fix a misread total, date or sender"),
                ("Undo", self.unmatch, "Put it back in Not Yet Matched")]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            actions.addWidget(b)
            self.buttons[text] = b
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        actions.addWidget(done)
        lay.addLayout(actions)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    def refresh(self) -> None:
        root = dump.dump_root(self.books)
        if root is not None:
            self.intro.setText(
                f"Drop invoices, bills and receipts (PDF or photos) into <b>{root}</b>. Each is read and matched to "
                f"its bank transaction. Once its expense is recorded it's renamed with the transaction date and "
                f"invoice number and saved in <b>{dump.saved_root(self.books)}</b>; until then it waits in "
                f"<b>{dump.RECOGNIZED_DIR}</b>. Anything without a match waits in <b>{dump.PENDING_DIR}</b> and is "
                f"checked again after every statement import — or choose <b>Future Payable</b> (not paid yet → "
                f"<b>{dump.PAYABLES_DIR}</b>) or <b>Not Related</b> (wrong invoice → <b>{dump.UNRELATED_DIR}</b>).")
        all_items = dump.items(self.books)
        saved_root = dump.saved_root(self.books)

        def state(i) -> str:
            if i.status != "matched":
                return i.status
            try:
                i.path.relative_to(saved_root)
                return "saved"
            except ValueError:
                return "recognized"

        want = self.filter.currentData()
        shown = [i for i in all_items if want is None or state(i) == want]
        self.items = shown
        self.table.setRowCount(len(shown))
        for r, it in enumerate(shown):
            matched = it.status == "matched"
            tx = it.transaction
            what = (f"{tx.date} · {tx.description} · {fmt_money(tx.amount_cents, parens=False)}"
                    if matched and tx else it.reason)
            label = {"saved": "✓ Saved", "recognized": "Recognized", "unmatched": "Waiting",
                     "payable": "Future Payable", "unrelated": "Not Related"}[state(it)]
            vals = [label, it.path.name, it.doc_date or "",
                    fmt_money(it.amount_cents) if it.amount_cents else "", it.party, what]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if c == 0:
                    cell.setForeground(POSITIVE if state(it) == "saved" else
                                       NEGATIVE if state(it) == "payable" else MUTED)
                    if state(it) == "recognized":
                        cell.setToolTip("Matched to its payment; saved once the payment has an expense category")
                if c == 3:
                    cell.setTextAlignment(ALIGN_RIGHT)
                self.table.setItem(r, c, cell)
        for c, w in enumerate([105, 300, 92, 100, 170]):
            self.table.setColumnWidth(c, w)
        n = {k: sum(1 for i in all_items if state(i) == k)
             for k in ("saved", "recognized", "unmatched", "payable", "unrelated")}
        text = (f"{n['saved']} saved · {n['recognized']} recognized (expense not recorded yet) · "
                f"{n['unmatched']} waiting for a match")
        if n["payable"]:
            text += f" · {n['payable']} future payable"
        if n["unrelated"]:
            text += f" · {n['unrelated']} not related"
        self.summary.setText(text)
        self._update_buttons()

    def _update_buttons(self, *_):
        it = self._selected()
        st = it.status if it else None
        self.buttons["Open File"].setEnabled(it is not None)
        self.buttons["Match by Hand…"].setEnabled(st in ("unmatched", "matched", "payable"))
        self.buttons["Future Payable…"].setEnabled(st in ("unmatched", "unrelated"))
        self.buttons["Not Related"].setEnabled(st in ("unmatched", "payable"))
        self.buttons["Correct Details…"].setEnabled(st in ("unmatched", "payable"))
        self.buttons["Undo"].setEnabled(st in ("matched", "payable", "unrelated"))

    def _selected(self) -> dump.DumpItem | None:
        rows = self.table.selectionModel().selectedRows()
        return self.items[rows[0].row()] if rows else None

    def process_all(self) -> None:
        process_all_bills(self.books, self, record_unpaid=self.record_unpaid.isChecked())
        self.refresh()

    def check_now(self) -> None:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            events = dump.process_dump(self.books, min_age_seconds=0)
        except Exception as e:  # noqa: BLE001
            events = []
            error(self, "Couldn't check the Invoice Dump", str(e))
        finally:
            QApplication.restoreOverrideCursor()
        self.refresh()
        if self.parent() is not None and hasattr(self.parent(), "statusBar"):
            matched = sum(e.matched for e in events)
            self.parent().statusBar().showMessage(
                f"Invoice Dump: {len(events)} item(s) checked, {matched} matched.", 8000)

    def open_folder(self) -> None:
        root = dump.dump_root(self.books)
        if root:
            dump.ensure_folders(root)
            _open(root)

    def open_file(self) -> None:
        it = self._selected()
        if it and it.path.exists():
            _open(it.path)

    def match_by_hand(self) -> None:
        it = self._selected()
        if it is None:
            return
        if it.status == "matched":
            dump.unlink(self.books, it.id)
            it = next(i for i in dump.items(self.books) if i.id == it.id)
        MatchByHandDialog(self.books, it, self).exec()
        self.refresh()

    def fix_details(self) -> None:
        it = self._selected()
        if it is None:
            return
        if FixDetailsDialog(self.books, it, self).exec():
            dump.process_dump(self.books, read_new=False)
            self.refresh()

    def unmatch(self) -> None:
        it = self._selected()
        if it and it.status in ("matched", "payable", "unrelated"):
            dump.unlink(self.books, it.id)
            self.refresh()

    def future_payable(self) -> None:
        it = self._selected()
        if it is None or it.status not in ("unmatched", "unrelated"):
            return
        if not it.amount_cents:
            error(self, "No total", "No total could be read from this invoice. Use Correct Details first.")
            return
        if FuturePayableDialog(self.books, it, self).exec():
            self.refresh()

    def not_related(self) -> None:
        it = self._selected()
        if it is None or it.status not in ("unmatched", "payable"):
            return
        extra = " Its bill will be removed from Accounts Payable." if it.status == "payable" else ""
        if confirm(self, "Not related", f"Move {it.path.name} to {dump.DUMP_DIR}/{dump.UNRELATED_DIR} and leave it "
                   f"out of the books?{extra}"):
            dump.mark_unrelated(self.books, it.id)
            self.refresh()
