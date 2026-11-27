"""Company Pages → Distributions: the company's cash, the reserve to keep, what's available, distributions planned
(with the cash left after each) and paid (see distributions.py)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import distributions as ds
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, confirm, error
from .operations_tab import _CompanyPage

PLAN_COLS = ["Date", "Amount (100%)", "Our share", "Cash before", "Cash after", "Status", "Note"]


def _money(v: float = 0) -> QDoubleSpinBox:
    b = QDoubleSpinBox()
    b.setRange(0, 1e12)
    b.setDecimals(2)
    b.setGroupSeparatorShown(True)
    b.setPrefix("$")
    b.setValue(v)
    return b


class PlanDialog(QDialog):
    def __init__(self, plan: ds.Planned | None = None, parent=None, suggest: int = 0):
        super().__init__(parent)
        self.plan = plan or ds.Planned(date.today().isoformat(), suggest)
        self.setWindowTitle("Planned distribution")
        form = QFormLayout(self)
        self.when = QDateEdit(QDate.fromString(self.plan.date, "yyyy-MM-dd"))
        self.when.setCalendarPopup(True)
        self.when.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Date:", self.when)
        self.amount = _money(self.plan.amount / 100)
        self.amount.setToolTip("The whole distribution (100%) — each owner gets their share from the cap table")
        form.addRow("Amount (100%):", self.amount)
        self.note = QLineEdit(self.plan.note)
        self.note.setPlaceholderText("e.g. Q1 distribution, refinance proceeds …")
        form.addRow("Note:", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def result_plan(self) -> ds.Planned:
        p = self.plan
        p.date = self.when.date().toString("yyyy-MM-dd")
        p.amount = round(self.amount.value() * 100)
        p.note = self.note.text()
        return p


class DistributionsPage(_CompanyPage):
    def __init__(self, view):
        super().__init__(view)
        from .asset_mgmt import Card

        lay = QVBoxLayout(self)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (
            ("cash", "Cash on hand"), ("reserve", "Reserve to keep"), ("available", "Available to distribute"),
            ("ncf", "Net cash flow a month"), ("planned", "Planned ahead"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        rbox = QGroupBox("Reserve — what the company keeps before anything is paid out")
        rl = QHBoxLayout(rbox)
        self.reserve_kind = QComboBox()
        self.reserve_kind.addItem("A set amount", "amount")
        self.reserve_kind.addItem("Months of expenses", "months")
        rl.addWidget(self.reserve_kind)
        self.reserve_amount = _money()
        rl.addWidget(self.reserve_amount)
        self.reserve_months = QDoubleSpinBox()
        self.reserve_months.setRange(0, 60)
        self.reserve_months.setDecimals(1)
        self.reserve_months.setSuffix(" months")
        rl.addWidget(self.reserve_months)
        rl.addStretch(1)
        for w in (self.reserve_amount, self.reserve_months):
            w.editingFinished.connect(self._save_reserve)
        self.reserve_kind.currentIndexChanged.connect(lambda *_: self._save_reserve())
        lay.addWidget(rbox)
        pbox = QGroupBox("Planned and paid distributions")
        pl = QVBoxLayout(pbox)
        head = QHBoxLayout()
        head.addStretch(1)
        for text, slot, tip in (("Plan a Distribution…", self.add, "A distribution to pay on a date"),
                                ("Edit…", self.edit, "Change the selected one"),
                                ("Mark Paid", self.toggle_paid,
                                 "Paid: recorded on the holding at our share — in the returns and your personal income "
                                 "(click again to undo)"),
                                ("Remove", self.remove, "Take the selected one off")):
            b = QPushButton(text)
            b.setToolTip(tip)
            if text.startswith("Plan"):
                b.setObjectName("primary")
            b.clicked.connect(lambda *_, s=slot: s())
            head.addWidget(b)
        pl.addLayout(head)
        self.table = QTableWidget(0, len(PLAN_COLS))
        self.table.setHorizontalHeaderLabels(PLAN_COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(len(PLAN_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        pl.addWidget(self.table)
        self.split = QLabel()
        self.split.setObjectName("hint")
        self.split.setWordWrap(True)
        pl.addWidget(self.split)
        self.table.itemSelectionChanged.connect(self._show_split)
        lay.addWidget(pbox, 1)
        hbox = QGroupBox("Paid out — the last 12 months, from the company's books (Dividends Paid / Distributions)")
        hl = QVBoxLayout(hbox)
        self.history = QTableWidget(1, 0)
        self.history.verticalHeader().hide()
        self.history.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.history.setMaximumHeight(80)
        hl.addWidget(self.history)
        lay.addWidget(hbox)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.pos: ds.Position | None = None

    # ---- filling ----
    def fill(self) -> None:
        p = self.perf()
        if p is None:
            self.note.setText("No company picked.")
            return
        h = p.holding
        r = ds.reserve(self.pf, h.id)
        for w in (self.reserve_kind, self.reserve_amount, self.reserve_months):
            w.blockSignals(True)
        self.reserve_kind.setCurrentIndex(1 if r.months else 0)
        self.reserve_amount.setValue(r.amount / 100)
        self.reserve_months.setValue(r.months)
        for w in (self.reserve_kind, self.reserve_amount, self.reserve_months):
            w.blockSignals(False)
        self._reserve_fields()
        pos = self.pos = ds.position(self.pf, p)
        self.cards["cash"].set(fmt_money(pos.cash) if pos.has_books else "—",
                               "from its books" if pos.has_books else "link its books")
        self.cards["reserve"].set(fmt_money(pos.reserve), pos.reserve_basis)
        self.cards["available"].set(fmt_money(pos.available), "cash − reserve",
                                    POSITIVE if pos.available > 0 else None)
        self.cards["ncf"].set(fmt_money(pos.monthly_ncf), pos.ncf_basis or "no books or projections",
                              NEGATIVE if pos.monthly_ncf < 0 else None)
        self.cards["planned"].set(fmt_money(pos.planned_ahead),
                                  f"{sum(1 for x in pos.rows if not x.plan.paid)} planned")
        t = self.table
        t.setRowCount(len(pos.rows))
        for i, row in enumerate(pos.rows):
            pl = row.plan
            vals = [pl.date, fmt_money(pl.amount), fmt_money(row.ours),
                    "" if pl.paid else fmt_money(row.cash_before), "" if pl.paid else fmt_money(row.cash_after),
                    "Paid" if pl.paid else ("Planned" if row.ok else "Planned — below the reserve"), pl.note]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (1, 2, 3, 4):
                    it.setTextAlignment(ALIGN_RIGHT)
                if pl.paid:
                    it.setForeground(MUTED)
                elif not row.ok and c in (4, 5):
                    it.setForeground(NEGATIVE)
                t.setItem(i, c, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(len(PLAN_COLS) - 1, QHeaderView.ResizeMode.Stretch)
        hist = self.history
        months = sorted(pos.paid_by_month)
        hist.setColumnCount(max(len(months), 1))
        hist.setHorizontalHeaderLabels([date.fromisoformat(m + "-01").strftime("%b %Y") for m in months] or [""])
        for c, m in enumerate(months):
            it = QTableWidgetItem(fmt_money(pos.paid_by_month[m]))
            it.setTextAlignment(ALIGN_RIGHT)
            hist.setItem(0, c, it)
        if not months:
            hist.setItem(0, 0, QTableWidgetItem("Nothing paid out in the last 12 months"))
        hist.resizeColumnsToContents()
        self.note.setText(f"{h.name}: cash before / after = cash now + its net cash flow a month until then − the "
                          "distributions planned up to it. Mark one paid and it's recorded on the holding at our "
                          f"share ({h.ownership_pct:g}%) — in the portfolio's returns and your personal income.")
        self._show_split()

    def _reserve_fields(self) -> None:
        months = self.reserve_kind.currentData() == "months"
        self.reserve_months.setVisible(months)
        self.reserve_amount.setVisible(not months)

    def _save_reserve(self) -> None:
        p = self.perf()
        if p is None:
            return
        months = self.reserve_kind.currentData() == "months"
        ds.set_reserve(self.pf, p.holding.id, ds.Reserve(0 if months else round(self.reserve_amount.value() * 100),
                                                         self.reserve_months.value() if months else 0.0))
        self.fill()

    def _selected(self) -> ds.Planned | None:
        r = self.table.currentRow()
        if not self.pos or not (0 <= r < len(self.pos.rows)) or not self.table.selectionModel().hasSelection():
            return None
        return self.pos.rows[r].plan

    def _show_split(self) -> None:
        pl = self._selected()
        p = self.perf()
        if pl is None or p is None:
            self.split.setText("Select a distribution to see each owner's part (from the cap table).")
            return
        parts = ds.owners_split(self.pf, p.holding, pl.amount)
        self.split.setText(f"{fmt_money(pl.amount)} on {pl.date}: " +
                           " · ".join(f"{n} {fmt_money(v)}" for n, v in parts))

    # ---- actions ----
    def add(self, *, run=None) -> bool:
        p = self.perf()
        if p is None:
            return False
        dlg = PlanDialog(None, self, suggest=self.pos.available if self.pos else 0)
        if not (run(dlg) if run is not None else dlg.exec()):
            return False
        try:
            ds.save_plan(self.pf, p.holding.id, dlg.result_plan())
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return False
        self.fill()
        return True

    def edit(self, *, run=None) -> bool:
        pl, p = self._selected(), self.perf()
        if pl is None or p is None:
            return False
        dlg = PlanDialog(pl, self)
        if not (run(dlg) if run is not None else dlg.exec()):
            return False
        pl = dlg.result_plan()
        try:
            if pl.paid:                          # a paid one changed: its recorded distribution follows
                ds.mark_paid(self.pf, p.holding, pl, False)
                ds.mark_paid(self.pf, p.holding, pl, True)
            else:
                ds.save_plan(self.pf, p.holding.id, pl)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return False
        self.fill()
        return True

    def toggle_paid(self) -> None:
        pl, p = self._selected(), self.perf()
        if pl is None or p is None:
            return
        ds.mark_paid(self.pf, p.holding, pl, not pl.paid)
        self.fill()

    def remove(self, *, ask: bool = True) -> None:
        pl = self._selected()
        if pl is None or (ask and not confirm(self, "Remove", f"Remove the {fmt_money(pl.amount)} distribution on "
                                                             f"{pl.date}?")):
            return
        ds.delete_plan(self.pf, pl.id)
        self.fill()
