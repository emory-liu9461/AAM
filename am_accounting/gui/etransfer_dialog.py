"""e-Transfer History window: read history (picture, PDF, CSV, pasted text) and match it to bank e-Transfers."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import etransfer as et
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, POSITIVE, error

FILE_FILTER = "e-Transfer history (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff *.pdf *.csv *.xlsx *.txt)"


class PasteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Paste e-Transfer history")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        hint = QLabel("In online banking, open your Interac e-Transfer history (sent and received), select the list, "
                      "copy it and paste it here.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Apr 4, 2024   Jane Smith   $3,000.00   Deposited   CA9XyZwk9q\n…")
        lay.addWidget(self.text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Read")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class PickTransactionDialog(QDialog):
    """Choose the bank line an e-Transfer belongs to: every line of that amount around its date (also ones
    already matched to another e-Transfer), with the range and 'any amount' adjustable."""

    def __init__(self, books: Books, rec: et.Record, parent=None):
        from PySide6.QtWidgets import QCheckBox, QSpinBox

        super().__init__(parent)
        self.books, self.rec = books, rec
        self.setWindowTitle(f"Match by hand – {rec.label} {fmt_money(rec.amount_cents)}")
        self.resize(940, 460)
        lay = QVBoxLayout(self)
        when = (rec.date or "date unknown").replace("????-", "")
        facts = [f"<b>{rec.label}</b>", f"<b>{fmt_money(rec.amount_cents)}</b>", when]
        if rec.reference:
            facts.append(f"ref {rec.reference}")
        if rec.message:
            facts.append(f"“{rec.message}”")
        head = QLabel(" · ".join(facts))
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)
        opts = QHBoxLayout()
        opts.addWidget(QLabel("Bank lines within"))
        self.days = QSpinBox()
        self.days.setRange(0, 120)
        self.days.setValue(7)
        self.days.setSuffix(" days")
        self.days.valueChanged.connect(self.fill)
        opts.addWidget(self.days)
        opts.addWidget(QLabel(f"of {when}"))
        self.any_amount = QCheckBox("Show e-Transfers of any amount")
        self.any_amount.toggled.connect(self.fill)
        opts.addWidget(self.any_amount)
        opts.addStretch(1)
        lay.addLayout(opts)
        self.note = QLabel()
        self.note.setObjectName("hint")
        lay.addWidget(self.note)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Days apart", "Description", "Amount", "Already matched to",
                                              "Notes"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self.choose)
        lay.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Match")
        buttons.accepted.connect(self.choose)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.fill()
        if not self.lines and not self.any_amount.isChecked():
            self.days.setValue(45)  # nothing close: look wider

    def fill(self, *_) -> None:
        self.lines = et.nearby_lines(self.books, self.rec, self.days.value(), self.any_amount.isChecked())
        self.table.setRowCount(len(self.lines))
        for r, ln in enumerate(self.lines):
            t = ln.tx
            gap = "" if ln.gap is None else ("same day" if ln.gap == 0 else f"{ln.gap}")
            vals = [t.date, gap, t.description, fmt_money(t.amount_cents, parens=False),
                    ("✓ " + ln.matched_to) if ln.matched_to else "", ln.why]
            for c, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if c == 3:
                    cell.setTextAlignment(ALIGN_RIGHT)
                if ln.matched_to or not (ln.same_amount and ln.right_direction):
                    cell.setForeground(MUTED)
                self.table.setItem(r, c, cell)
        for c, w in enumerate([95, 80, 0, 110, 210]):
            if w:
                self.table.setColumnWidth(c, w)
        if self.rec.transaction_id:
            mine = next((i for i, ln in enumerate(self.lines) if ln.tx.id == self.rec.transaction_id), -1)
        else:
            mine = next((i for i, ln in enumerate(self.lines) if not ln.matched_to), 0 if self.lines else -1)
        if mine >= 0:
            self.table.selectRow(mine)
        what = "e-Transfers" if self.any_amount.isChecked() else f"lines of {fmt_money(self.rec.amount_cents)}"
        free = sum(1 for ln in self.lines if not ln.matched_to)
        self.note.setText(f"{len(self.lines)} bank {what} within {self.days.value()} days · {free} not matched yet"
                          if self.lines else f"No bank {what} within {self.days.value()} days — widen the range, "
                                             f"tick 'any amount', or import that statement first.")

    def choose(self, *_) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        ln = self.lines[rows[0].row()]
        if ln.matched_to and ln.tx.id != self.rec.transaction_id:
            from PySide6.QtWidgets import QMessageBox

            if QMessageBox.question(self, "Already matched",
                                    f"{ln.tx.date} {ln.tx.description} is matched to {ln.matched_to}.\n\n"
                                    f"Match it to {self.rec.label} instead? ({ln.matched_to} goes back to "
                                    f"waiting.)") != QMessageBox.StandardButton.Yes:
                return
        if not ln.same_amount:
            from PySide6.QtWidgets import QMessageBox

            if QMessageBox.question(self, "Different amount",
                                    f"This line is {fmt_money(abs(ln.tx.amount_cents))}, the e-Transfer is "
                                    f"{fmt_money(self.rec.amount_cents)}. Match anyway?") \
                    != QMessageBox.StandardButton.Yes:
                return
        et.link(self.books, self.rec.id, ln.tx.id)
        self.accept()


class ETransferDialog(QDialog):
    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("e-Transfer History")
        self.resize(1080, 620)
        lay = QVBoxLayout(self)
        drop = et.folder(books)
        intro = QLabel(
            "Bank statements show e-Transfers as <b>SEND E-TFR ***k9q</b> or <b>E-TRANSFER ***r2T</b>, without the "
            "name. Add your Interac e-Transfer history — a <b>screenshot or photo</b>, a <b>PDF</b>, a <b>CSV/Excel "
            "export</b>, or <b>pasted text</b> — and each transfer is matched to its bank line by the reference "
            "number ending, amount and date. The memo then shows who it was to or from, and the category you "
            "used for that person before is applied."
            + (f"<br>You can also drop history files into <b>{drop}</b>." if drop else ""))
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        bar = QHBoxLayout()
        for text, slot, primary in [("Add Picture / PDF / CSV…", self.add_files, True),
                                    ("Paste Text…", self.paste, False), ("Match Now", self.match_now, False)]:
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(slot)
            bar.addWidget(b)
        if drop:
            b = QPushButton("Open Folder")
            b.clicked.connect(self.open_folder)
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(QLabel("Show:"))
        self.filter = QComboBox()
        for label, key in [("Everything", None), ("Not matched yet", "unmatched"), ("Matched", "matched"),
                           ("Bank e-Transfers with no history yet", "bank")]:
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        bar.addWidget(self.filter)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Status", "Date", "", "Name", "Amount", "Reference", "Message",
                                              "Bank transaction"])
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self.match_by_hand)
        lay.addWidget(self.table, 1)
        bottom = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName("hint")
        bottom.addWidget(self.summary, 1)
        for text, slot in [("Match by Hand…", self.match_by_hand), ("Unmatch", self.unmatch),
                           ("Delete", self.delete)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            bottom.addWidget(b)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        bottom.addWidget(done)
        lay.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        everything = et.records(self.books)
        want = self.filter.currentData()
        lines = et.bank_lines(self.books)
        loose = et.bank_lines_without_history(self.books)
        bank_note = (f"   |   Statements: {len(lines)} e-Transfer line(s) (SEND E-TFR / E-TFR / E-TRANSFER), "
                     f"{len(lines) - len(loose)} matched, {len(loose)} with no history yet")
        if want == "bank":
            self._fill_bank(loose)
            matched = sum(1 for r in everything if r.transaction_id)
            self.summary.setText(f"{len(everything)} e-Transfer(s) in history · {matched} matched" + bank_note)
            return
        self.items = [r for r in everything if want is None or (want == "matched") == bool(r.transaction_id)]
        self.table.setRowCount(len(self.items))
        used = et.linked_ids(self.books)
        for row, r in enumerate(self.items):
            tx = None
            if r.transaction_id:
                try:
                    tx = self.books.transaction(r.transaction_id)
                except KeyError:
                    tx = None
            arrow = {"sent": "Sent to", "received": "From"}.get(r.direction, "")
            vals = ["✓ Matched" if tx else "Waiting", (r.date or "").replace("????-", ""), arrow, r.name,
                    fmt_money(r.amount_cents), r.reference, r.message,
                    f"{tx.date} · {tx.description} · {fmt_money(tx.amount_cents, parens=False)}" if tx else
                    et.why_waiting(self.books, r, used)]
            for col, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if col == 0:
                    cell.setForeground(POSITIVE if tx else MUTED)
                if col == 4:
                    cell.setTextAlignment(ALIGN_RIGHT)
                self.table.setItem(row, col, cell)
        for col, w in enumerate([95, 92, 62, 190, 100, 120, 160]):
            self.table.setColumnWidth(col, w)
        matched = sum(1 for r in everything if r.transaction_id)
        self.summary.setText(f"{len(everything)} e-Transfer(s) in history · {matched} matched · "
                             f"{len(everything) - matched} waiting" + bank_note)

    def _fill_bank(self, txs: list) -> None:
        """Bank e-Transfer lines no history record is matched to: name them here, or add the history."""
        from .. import parties

        names = parties.names(self.books, [t.id for t in txs])
        self.items = txs
        self.table.setRowCount(len(txs))
        for row, t in enumerate(txs):
            n = names.get(t.id)
            vals = ["No history", t.date, "Sent" if t.amount_cents < 0 else "Received", n.name if n else "",
                    fmt_money(abs(t.amount_cents)), "…" + et.ref_suffix(t.description) if et.ref_suffix(t.description)
                    else "", "", f"{t.description}  —  add its history, or double-click to name it"]
            for col, v in enumerate(vals):
                cell = QTableWidgetItem(v)
                cell.setToolTip(v)
                if col == 0:
                    cell.setForeground(MUTED)
                if col == 4:
                    cell.setTextAlignment(ALIGN_RIGHT)
                self.table.setItem(row, col, cell)

    def _name_bank_line(self, t) -> None:
        from PySide6.QtWidgets import QInputDialog

        from .. import parties

        n = parties.name_of(self.books, t.id)
        names = parties.all_names(self.books)
        current = n.name if n else ""
        if current and current not in names:
            names.insert(0, current)
        text, ok = QInputDialog.getItem(self, "Who was this e-Transfer?",
                                        f"{t.date} · {t.description} · {fmt_money(t.amount_cents, parens=False)}\n\n"
                                        f"{'Sent to' if t.amount_cents < 0 else 'Received from'}:", [""] + names,
                                        (names.index(current) + 1) if current in names else 0, True)
        if ok:
            parties.set_name(self.books, [t.id], text)
            parties.apply_learned(self.books)
            self.refresh()

    def _selected(self) -> et.Record | None:
        rows = self.table.selectionModel().selectedRows()
        it = self.items[rows[0].row()] if rows else None
        return it if isinstance(it, et.Record) else None

    def _report(self, added: int, known: int, res: et.MatchResult, what: str) -> None:
        self.refresh()
        msg = f"{what}: {added} new e-Transfer(s)" + (f", {known} already known" if known else "") + \
              f". {res.describe()}."
        if res.ambiguous:
            msg += "\n\nMatch these by hand:\n• " + "\n• ".join(res.ambiguous[:10])
        self.summary.setText(msg.split("\n")[0])
        if res.ambiguous or not added:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "e-Transfer History", msg if added or known else
                                    f"{what}: no e-Transfers could be read. Try a clearer screenshot, or copy and "
                                    f"paste the history as text.")

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add e-Transfer history", str(Path.home()), FILE_FILTER)
        if not paths:
            return
        added = known = 0
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            for p in paths:
                a, k = et.add_records(self.books, et.read_file(Path(p)))
                added, known = added + a, known + k
            res = et.match_all(self.books)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            error(self, "Couldn't read the file", str(e))
            return
        QApplication.restoreOverrideCursor()
        self._report(added, known, res, f"{len(paths)} file(s)")

    def paste(self) -> None:
        dlg = PasteDialog(self)
        if dlg.exec() and dlg.text.toPlainText().strip():
            added, known, res = et.import_text(self.books, dlg.text.toPlainText())
            self._report(added, known, res, "Pasted text")

    def match_now(self) -> None:
        res = et.match_all(self.books)
        self.refresh()
        self.summary.setText(res.describe())

    def open_folder(self) -> None:
        drop = et.folder(self.books)
        if drop:
            drop.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(drop)))

    def match_by_hand(self, *_) -> None:
        rows = self.table.selectionModel().selectedRows()
        if rows and self.filter.currentData() == "bank":
            self._name_bank_line(self.items[rows[0].row()])
            return
        rec = self._selected()
        if rec and PickTransactionDialog(self.books, rec, self).exec():
            self.refresh()

    def unmatch(self) -> None:
        rec = self._selected()
        if rec and rec.transaction_id:
            et.unlink(self.books, rec.id)
            self.refresh()

    def delete(self) -> None:
        rec = self._selected()
        if rec:
            et.delete(self.books, rec.id)
            self.refresh()
