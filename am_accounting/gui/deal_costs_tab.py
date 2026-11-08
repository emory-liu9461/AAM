"""A deal's Deposits & Costs tab: what's paid before closing, which company pays it, and putting it in the books."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel,
                               QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import deal_costs as dc
from ..money import fmt_money
from .common import confirm, error, info

COLS = ["Deposit / Cost", "For", "Vendor", "Amount", "Date", "Paid by", "In the books"]


class DealCostsTab(QWidget):
    """The lines are kept with the deal (saved with it); "Record in the Books…" puts one in the paying company's books
    straight away."""

    def __init__(self, pf, deal, companies=None, parent=None, deal_name=None):
        super().__init__(parent)
        self.pf, self.deal = pf, deal
        self.companies = companies or (lambda: [])
        self.deal_name = deal_name or (lambda: deal.name)
        self.removed: list[int] = []
        lay = QVBoxLayout(self)
        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.totals)
        self.table = t = QTableWidget(0, len(COLS))
        t.setHorizontalHeaderLabels(COLS)
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        t.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        h = t.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, w in ((0, 110), (2, 160), (3, 130), (4, 130), (5, 190), (6, 190)):
            t.setColumnWidth(col, w)
        t.itemSelectionChanged.connect(self._buttons)
        t.itemChanged.connect(lambda *_: self._totals())
        lay.addWidget(t, 1)
        row = QHBoxLayout()
        self.btns = {}
        for key, text, slot, tip in (
                ("add", "Add Line", self._add, "A deposit or a cost paid before closing"),
                ("remove", "Remove Line", self._remove, "Remove a line that isn't in the books"),
                ("usual", "Add the Usual", self._usual, "Deposit, appraisal, building condition, environmental, legal…"),
                ("record", "Record in the Books…", self._record,
                 "Put the selected line in the paying company's books: a new bank payment, owing to the vendor, or a "
                 "payment already in its bank feed"),
                ("unrecord", "Take Out of the Books", self._unrecord, "Undo recording the selected line"),
                ("writeoff", "Write Off…", self._write_off,
                 "The deal isn't going ahead: expense what's been paid as Abandoned Deal Costs")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
            self.btns[key] = b
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("Pick the company that pays each line (usually a holding company). Recorded lines sit in its "
                      "books in “Deal Deposits & Costs – <deal>” until closing: then the new company owes them back "
                      "(“Due to” the payer, and the costs become part of the property's cost). If the deal dies, "
                      "write them off.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        for c in (dc.costs(pf, deal.id) if deal.id else []):
            self._row(c)
        self._buttons()
        self._totals()

    # ---- rows ----
    def _payers(self) -> list[tuple[str, str]]:
        return [(n, p) for n, p, *_ in self.companies()]

    def _row(self, c: dc.DealCost) -> None:
        from .asset_mgmt import _money_box
        from .deals_tab import _date_box

        t = self.table
        t.blockSignals(True)
        r = t.rowCount()
        t.insertRow(r)
        kind = QComboBox()
        for k, label in dc.KINDS.items():
            kind.addItem(label, k)
        kind.setCurrentIndex(max(0, kind.findData(c.kind)))
        kind.currentIndexChanged.connect(self._totals)
        t.setCellWidget(r, 0, kind)
        it = QTableWidgetItem(c.item)
        it.setData(Qt.ItemDataRole.UserRole, c)
        t.setItem(r, 1, it)
        t.setItem(r, 2, QTableWidgetItem(c.vendor))
        amt = _money_box()
        amt.setValue(c.amount_cents / 100)
        amt.valueChanged.connect(self._totals)
        t.setCellWidget(r, 3, amt)
        t.setCellWidget(r, 4, _date_box(c.date))
        payer = QComboBox()
        payer.addItem("— pick a company —", "")
        for name, path in self._payers():
            payer.addItem(name, path)
        if c.payer_path and payer.findData(c.payer_path) < 0:
            payer.addItem(c.payer_name or c.payer_path, c.payer_path)
        payer.setCurrentIndex(max(0, payer.findData(c.payer_path)))
        t.setCellWidget(r, 5, payer)
        st = QTableWidgetItem(c.status)
        st.setFlags(st.flags() & ~Qt.ItemFlag.ItemIsEditable)
        t.setItem(r, 6, st)
        self._lock(r, c.recorded)
        t.blockSignals(False)

    def _lock(self, r: int, locked: bool) -> None:
        t = self.table
        for col in (0, 3, 4, 5):
            t.cellWidget(r, col).setEnabled(not locked)
        for col in (1, 2):
            it = t.item(r, col)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable if locked else it.flags() | Qt.ItemFlag.ItemIsEditable)

    def line(self, r: int) -> dc.DealCost:
        """The row as a line (its saved fields kept)."""
        from .deals_tab import _date_of

        t = self.table
        c: dc.DealCost = t.item(r, 1).data(Qt.ItemDataRole.UserRole)
        if c.recorded:
            return c
        payer = t.cellWidget(r, 5)
        c.kind = t.cellWidget(r, 0).currentData()
        c.item, c.vendor = t.item(r, 1).text().strip(), t.item(r, 2).text().strip()
        c.amount_cents = round(t.cellWidget(r, 3).value() * 100)
        c.date = _date_of(t.cellWidget(r, 4))
        c.payer_path = payer.currentData() or ""
        c.payer_name = payer.currentText() if c.payer_path else ""
        c.sort = r
        return c

    def lines(self) -> list[dc.DealCost]:
        return [self.line(r) for r in range(self.table.rowCount())]

    def _selected(self) -> int | None:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def _buttons(self) -> None:
        r = self._selected()
        c = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole) if r is not None else None
        self.btns["remove"].setEnabled(c is not None and not c.recorded)
        self.btns["record"].setEnabled(c is not None and not c.recorded)
        self.btns["unrecord"].setEnabled(c is not None and c.recorded and not c.settled)
        self.btns["writeoff"].setEnabled(any(x.recorded and not x.settled for x in self._raw()))

    def _raw(self) -> list[dc.DealCost]:
        return [self.table.item(r, 1).data(Qt.ItemDataRole.UserRole) for r in range(self.table.rowCount())]

    def _totals(self, *_) -> None:
        ls = self.lines()
        dep = sum(c.amount_cents for c in ls if c.kind == "deposit")
        cost = sum(c.amount_cents for c in ls if c.kind == "cost")
        booked = sum(c.amount_cents for c in ls if c.recorded and not c.settled)
        todo = sum(1 for c in ls if not c.recorded and c.amount_cents)
        self.totals.setText(f"Deposits <b>{fmt_money(dep)}</b> · Costs <b>{fmt_money(cost)}</b> · In the books, "
                            f"waiting for closing <b>{fmt_money(booked)}</b>"
                            + (f" · <b>{todo}</b> not in the books yet" if todo else ""))

    def _refresh_row(self, r: int, c: dc.DealCost) -> None:
        self.table.blockSignals(True)
        self.table.item(r, 6).setText(c.status)
        self._lock(r, c.recorded)
        self.table.blockSignals(False)
        self._buttons()
        self._totals()

    # ---- actions ----
    def _add(self, *_, kind: str = "cost", item: str = "") -> None:
        payers = self._payers()
        self._row(dc.DealCost(item, kind, date=date.today().isoformat(),
                              payer_path=payers[0][1] if len(payers) == 1 else "",
                              payer_name=payers[0][0] if len(payers) == 1 else ""))
        self.table.selectRow(self.table.rowCount() - 1)
        self._totals()

    def _usual(self) -> None:
        have = {(c.kind, c.item.lower()) for c in self.lines()}
        for kind, item in dc.STARTERS:
            if (kind, item.lower()) not in have:
                self._add(kind=kind, item=item)

    def _remove(self) -> None:
        r = self._selected()
        if r is None:
            return
        c = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
        if c.recorded:
            error(self, "In the books", "Take the line out of the books first.")
            return
        if c.id is not None:
            self.removed.append(c.id)
        self.table.removeRow(r)
        self._buttons()
        self._totals()

    def _record(self) -> None:
        r = self._selected()
        if r is None:
            return
        if self.deal.id is None:
            info(self, "Save the deal first", "Save the deal, then open it again to record its deposits and costs.")
            return
        c = self.line(r)
        if not c.item:
            error(self, "Record in the books", "Enter what the line is for.")
            return
        if not c.payer_path:
            error(self, "Record in the books", "Pick the company that pays it.")
            return
        if not c.amount_cents:
            error(self, "Record in the books", "Enter the amount.")
            return
        dlg = RecordCostDialog(c, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            dc.record(self.pf, self.deal_name(), c, dlg.how, bank_id=dlg.bank_id, tx_id=dlg.tx_id,
                      deal_id=self.deal.id)
        except ValueError as e:
            error(self, "Couldn't record it", str(e))
            return
        self._refresh_row(r, c)

    def _unrecord(self) -> None:
        r = self._selected()
        if r is None:
            return
        c = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
        what = {"bank": "the bank payment entered for it is deleted",
                "owing": "the entry to Accounts Payable is deleted",
                "matched": "the bank payment goes back to uncategorized"}.get(c.how, "")
        if not confirm(self, "Take out of the books", f"Take “{c.item}” out of {c.payer_name}'s books? ({what})"):
            return
        try:
            dc.unrecord(self.pf, self.deal.id, c)
        except ValueError as e:
            error(self, "Couldn't take it out", str(e))
            return
        self._refresh_row(r, c)

    def _write_off(self) -> None:
        open_ = [c for c in self._raw() if c.recorded and not c.settled]
        costs = sum(c.amount_cents for c in open_ if c.kind == "cost")
        deposits = sum(c.amount_cents for c in open_ if c.kind == "deposit")
        include = False
        if deposits:
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setWindowTitle("Write off")
            box.setText(f"Write off {fmt_money(costs)} of costs as Abandoned Deal Costs.\n\nThe deposit "
                        f"({fmt_money(deposits)}): is it lost too, or coming back?")
            lost = box.addButton("Lost – write it off too", QMessageBox.ButtonRole.AcceptRole)
            back = box.addButton("Coming back – leave it", QMessageBox.ButtonRole.AcceptRole)
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.exec()
            if box.clickedButton() not in (lost, back):
                return
            include = box.clickedButton() is lost
        elif not confirm(self, "Write off", f"Write off {fmt_money(costs)} of costs as Abandoned Deal Costs in the "
                                            "paying companies' books?"):
            return
        done = dc.write_off(self.pf, self.deal.id, self.deal_name(), include_deposits=include)
        saved = {c.id: c for c in dc.costs(self.pf, self.deal.id)}
        for r in range(self.table.rowCount()):
            c = self.table.item(r, 1).data(Qt.ItemDataRole.UserRole)
            if c.id in saved:
                c.settled = saved[c.id].settled
                self._refresh_row(r, c)
        info(self, "Written off", f"{fmt_money(done)} written off.")

    def save(self, deal_id: int) -> None:
        """Called by the deal's Save: lines kept, removed ones deleted."""
        for cid in self.removed:
            dc.delete(self.pf, cid)
        self.removed.clear()
        for c in self.lines():
            if c.item or c.amount_cents:
                dc.save(self.pf, deal_id, c)


class RecordCostDialog(QDialog):
    """How the paying company paid it: a new bank payment, owing to the vendor, or a payment already in its feed."""

    def __init__(self, c: dc.DealCost, parent=None):
        super().__init__(parent)
        from ..books import Books

        self.setWindowTitle(f"Record in {c.payer_name}'s books")
        self.resize(640, 420)
        self.how, self.bank_id, self.tx_id = "bank", None, None
        lay = QVBoxLayout(self)
        head = QLabel(f"<b>{c.item}</b> · {fmt_money(c.amount_cents)}" + (f" · {c.vendor}" if c.vendor else "")
                      + f"<br>Paid by <b>{c.payer_name}</b>")
        head.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(head)
        books = Books.open(c.payer_path, create=False)
        try:
            banks = [(a.id, a.name) for a in books.bank_accounts()]
            cands = dc.candidates(books, c)[:50]
        finally:
            books.close()
        self.group = QButtonGroup(self)
        self.r_bank = QRadioButton("Paid from the bank — enter the payment")
        self.r_owing = QRadioButton(f"Not paid yet — owing to {c.vendor or 'the vendor'} (Accounts Payable)")
        self.r_match = QRadioButton("Already in the bank feed — pick the payment")
        self.bank = QComboBox()
        for bid, name in banks:
            self.bank.addItem(name, bid)
        self.match = QTableWidget(len(cands), 3)
        self.match.setHorizontalHeaderLabels(["Date", "Description", "Amount"])
        self.match.verticalHeader().setVisible(False)
        self.match.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.match.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.match.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.match.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i, tx in enumerate(cands):
            d = QTableWidgetItem(tx.date)
            d.setData(Qt.ItemDataRole.UserRole, tx.id)
            self.match.setItem(i, 0, d)
            self.match.setItem(i, 1, QTableWidgetItem(tx.description))
            a = QTableWidgetItem(fmt_money(-tx.amount_cents))
            a.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.match.setItem(i, 2, a)
        if cands:
            self.match.selectRow(0)
        for b in (self.r_bank, self.r_owing, self.r_match):
            self.group.addButton(b)
            b.toggled.connect(self._enable)
        lay.addWidget(self.r_bank)
        row = QHBoxLayout()
        row.addSpacing(24)
        row.addWidget(QLabel("From:"))
        row.addWidget(self.bank, 1)
        lay.addLayout(row)
        lay.addWidget(self.r_owing)
        lay.addWidget(self.r_match)
        self.none = QLabel("No payment of about this amount within 60 days in its bank feed.")
        self.none.setObjectName("hint")
        if cands:
            lay.addWidget(self.match, 1)
        else:
            lay.addWidget(self.none)
            lay.addStretch(1)
        if not cands:
            self.r_match.setEnabled(False)
        if not banks:
            self.r_bank.setEnabled(False)
        (self.r_match if cands else self.r_bank if banks else self.r_owing).setChecked(True)
        self._enable()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Record")
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _enable(self, *_) -> None:
        self.bank.setEnabled(self.r_bank.isChecked())
        self.match.setEnabled(self.r_match.isChecked())

    def _ok(self) -> None:
        if self.r_bank.isChecked():
            self.how, self.bank_id = "bank", self.bank.currentData()
        elif self.r_owing.isChecked():
            self.how = "owing"
        else:
            rows = self.match.selectionModel().selectedRows()
            if not rows:
                error(self, "Pick the payment", "Select the bank payment that paid it.")
                return
            self.how, self.tx_id = "matched", self.match.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        self.accept()
