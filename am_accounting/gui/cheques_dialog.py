"""Cheques & Drafts window: scans / photos of cheques and bank drafts, matched to statement lines."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import cheques as cq
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, POSITIVE, error

FILE_FILTER = "Cheques and drafts (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.webp)"


class PickLineDialog(QDialog):
    def __init__(self, books: Books, item: cq.Item, parent=None):
        super().__init__(parent)
        self.books, self.item = books, item
        self.setWindowTitle(f"Match {item.info.label}")
        self.resize(760, 380)
        lay = QVBoxLayout(self)
        used = cq.linked_ids(books) - ({item.transaction_id} if item.transaction_id else set())
        info = item.info
        self.cands = cq.candidates(books, info, used, max_days=365)
        if not self.cands and info.amount_cents:  # any cheque / draft line, nearest amount first
            rows = books.conn.execute("SELECT id FROM transactions").fetchall()
            txs = [books.transaction(r[0]) for r in rows]
            txs = [t for t in txs if cq.line_kind(t.description) and t.id not in used]
            txs.sort(key=lambda t: abs(abs(t.amount_cents) - info.amount_cents))
            self.cands = [cq.Candidate(t, 0, "") for t in txs[:30]]
        lay.addWidget(QLabel("Cheque and draft lines in your statements:" if self.cands else
                             "No cheque or draft lines found — import the statement first."))
        self.table = QTableWidget(len(self.cands), 4)
        self.table.setHorizontalHeaderLabels(["Date", "Description", "Amount", "Why"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        for r, c in enumerate(self.cands):
            for col, v in enumerate([c.tx.date, c.tx.description, fmt_money(c.tx.amount_cents, parens=False), c.why]):
                self.table.setItem(r, col, QTableWidgetItem(v))
        if self.cands:
            self.table.selectRow(0)
        lay.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Match")
        buttons.accepted.connect(self.choose)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def choose(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        cq.link(self.books, self.item.id, self.cands[rows[0].row()].tx.id)
        self.accept()


class FixDialog(QDialog):
    """Correct what was read (number, payee, amount, date, memo)."""

    def __init__(self, books: Books, item: cq.Item, parent=None):
        super().__init__(parent)
        self.books, self.item = books, item
        info = item.info
        self.setWindowTitle(f"Correct details – {item.original_name}")
        form = QFormLayout(self)
        self.kind = QComboBox()
        self.kind.addItem("Cheque", "cheque")
        self.kind.addItem("Bank draft / money order", "draft")
        self.kind.setCurrentIndex(self.kind.findData(info.kind))
        self.number = QLineEdit(info.number)
        self.payee = QLineEdit(info.payee)
        self.amount = QDoubleSpinBox()
        self.amount.setRange(0, 1e10)
        self.amount.setDecimals(2)
        self.amount.setPrefix("$ ")
        self.amount.setGroupSeparatorShown(True)
        self.amount.setValue((info.amount_cents or 0) / 100)
        d = date.fromisoformat(info.date) if info.date and not info.date.startswith("?") else date.today()
        self.date = QDateEdit(QDate(d.year, d.month, d.day))
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("MMM d, yyyy")
        self.date_known = bool(info.date and not info.date.startswith("?"))
        self.date.dateChanged.connect(lambda *_: setattr(self, "date_known", True))
        self.memo = QLineEdit(info.memo)
        for label, w in [("Kind:", self.kind), ("Number:", self.number), ("Pay to:", self.payee),
                         ("Amount:", self.amount), ("Date:", self.date), ("Memo:", self.memo)]:
            form.addRow(label, w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        cq.set_details(self.books, self.item.id, kind=self.kind.currentData(), number=self.number.text(),
                       date_=self.date.date().toString("yyyy-MM-dd") if self.date_known else self.item.info.date,
                       payee=self.payee.text(),
                       amount_cents=round(self.amount.value() * 100) or None, memo=self.memo.text())
        self.accept()


class ChequesDialog(QDialog):
    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Cheques & Drafts")
        self.resize(1120, 600)
        lay = QVBoxLayout(self)
        root = cq.folder(books)
        intro = QLabel(
            "Statements show cheques and bank drafts as <b>CHQ#00126</b> or <b>CAD DRAFT 05551234</b>, without "
            "who they were for. Save a scan or photo of each cheque, the bank's cheque image, or the draft "
            "receipt" + (f" in <b>{root}</b>" if root else "") + " (or use <b>Add Files…</b>). The number, payee, "
            "amount, date and memo are read, and each is matched to its statement line by number and amount, "
            "then renamed and filed in <b>Matched</b>. Ones not in a statement yet wait in <b>Not Yet Matched</b> "
            "and are checked after every import.")
        if root is None:
            intro.setText("Set up the company folder first (File → Company Folder & Auto-Save). It includes a "
                          "<b>Cheques &amp; Drafts</b> folder for cheque and bank draft scans.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        bar = QHBoxLayout()
        for text, slot, primary in [("Add Files…", self.add_files, True), ("Check Folder Now", self.check_now, False),
                                    ("Open Folder", self.open_folder, False)]:
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.setEnabled(root is not None)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        for label, key in [("Everything", None), ("Not matched yet", "unmatched"), ("Matched", "matched")]:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.filter)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["Status", "Kind", "Number", "Date", "Pay to", "Amount", "Memo", "File",
                                              "Statement line / why not"])
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.open_file)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        bottom.addWidget(self.summary, 1)
        for text, slot in [("Open File", self.open_file), ("Match by Hand…", self.match_by_hand),
                           ("Correct Details…", self.fix), ("Unmatch", self.unmatch)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        bottom.addWidget(done)
        lay.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        everything = cq.items(self.books)
        want = self.filter.currentData()
        self.items = [i for i in everything if want is None or (want == "matched") == bool(i.transaction_id)]
        self.table.setRowCount(len(self.items))
        for r, it in enumerate(self.items):
            tx = None
            if it.transaction_id:
                try:
                    tx = self.books.transaction(it.transaction_id)
                except KeyError:
                    tx = None
            info = it.info
            vals = ["✓ Matched" if tx else "Waiting", "Cheque" if info.kind == "cheque" else "Draft", info.number,
                    info.date or "", info.payee, fmt_money(info.amount_cents) if info.amount_cents else "",
                    info.memo, it.path.name,
                    f"{tx.date} · {tx.description} · {fmt_money(tx.amount_cents, parens=False)}" if tx else it.reason]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if c == 0:
                    cell.setForeground(POSITIVE if tx else MUTED)
                if c == 5:
                    cell.setTextAlignment(ALIGN_RIGHT)
                self.table.setItem(r, c, cell)
        for c, w in enumerate([95, 60, 85, 92, 190, 100, 120, 230]):
            self.table.setColumnWidth(c, w)
        matched = sum(1 for i in everything if i.transaction_id)
        self.summary.setText(f"{matched} matched · {len(everything) - matched} waiting for their statement line")

    def _selected(self) -> cq.Item | None:
        rows = self.table.selectionModel().selectedRows()
        return self.items[rows[0].row()] if rows else None

    def _run(self, fn) -> list:
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't read the cheques", str(e))
            return []
        finally:
            QApplication.restoreOverrideCursor()
            self.refresh()

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add cheques and drafts", str(Path.home()), FILE_FILTER)
        if not paths:
            return
        root = cq.folder(self.books)
        from ..pdf_to_excel import unique_path
        import shutil

        cq.ensure_folders(root)
        for p in paths:
            shutil.copy2(p, unique_path(root / Path(p).name))
        events = self._run(lambda: cq.process_folder(self.books, min_age_seconds=0))
        self.summary.setText(f"{len(paths)} file(s) read · {sum(e.matched for e in events)} matched")

    def check_now(self) -> None:
        events = self._run(lambda: cq.process_folder(self.books, min_age_seconds=0))
        self.summary.setText(f"{len(events)} checked · {sum(e.matched for e in events)} matched")

    def open_folder(self) -> None:
        root = cq.folder(self.books)
        if root:
            cq.ensure_folders(root)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def open_file(self, *_) -> None:
        it = self._selected()
        if it and it.path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(it.path)))

    def match_by_hand(self) -> None:
        it = self._selected()
        if it and PickLineDialog(self.books, it, self).exec():
            self.refresh()

    def fix(self) -> None:
        it = self._selected()
        if it and FixDialog(self.books, it, self).exec():
            self.refresh()

    def unmatch(self) -> None:
        it = self._selected()
        if it and it.transaction_id:
            cq.unlink(self.books, it.id)
            self.refresh()
