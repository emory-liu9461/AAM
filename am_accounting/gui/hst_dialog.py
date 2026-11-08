"""GST/HST Return window: the figures for a filing period, what makes them up, and which periods are filed."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QTableWidgetItem, QVBoxLayout)

from .. import hst
from ..money import fmt_money
from .common import ALIGN_RIGHT, NEGATIVE, POSITIVE, confirm, error, info

LINE_COLS = ["Date", "What", "Account", "Collected (103)", "ITC (106)", "Paid to CRA"]


class HstDialog(QDialog):
    def __init__(self, books, parent=None, today: date | None = None):
        super().__init__(parent)
        from .asset_mgmt import _table

        self.books, self.today = books, today or date.today()
        self.s = hst.settings(books)
        self.setWindowTitle(f"GST/HST Return – {books.company_name}")
        self.resize(1000, 680)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Files:"))
        self.freq = QComboBox()
        for k, label in hst.FREQUENCIES.items():
            self.freq.addItem(label, k)
        self.freq.setCurrentIndex(self.freq.findData(self.s.frequency))
        self.freq.currentIndexChanged.connect(self._freq_changed)
        row.addWidget(self.freq)
        row.addWidget(QLabel("Period:"))
        self.period = QComboBox()
        self.period.setMinimumWidth(260)
        self.period.currentIndexChanged.connect(lambda *_: self.fill())
        row.addWidget(self.period)
        row.addWidget(QLabel("BN / RT:"))
        self.number = QLineEdit(self.s.number)
        self.number.setPlaceholderText("123456789 RT0001")
        self.number.editingFinished.connect(self._save_settings)
        row.addWidget(self.number)
        row.addStretch(1)
        acc = QPushButton("HST Accounts…")
        acc.setToolTip("Which accounts hold the HST collected and paid")
        acc.clicked.connect(self.pick_accounts)
        row.addWidget(acc)
        lay.addLayout(row)
        self.accounts_note = QLabel()
        self.accounts_note.setObjectName("hint")
        self.accounts_note.setWordWrap(True)
        lay.addWidget(self.accounts_note)
        self.figures = QLabel()
        self.figures.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.figures)
        self.table = _table(LINE_COLS, 1)
        lay.addWidget(self.table, 1)
        hint = QLabel("Collected = credits to the HST accounts; input tax credits = debits to them (record the HST on a "
                      "bill by splitting it between the expense and the HST account). Payments to and refunds from the "
                      "CRA (Receiver General) are left out. Check the figures before filing.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        b = QHBoxLayout()
        self.filed_btn = QPushButton("Mark as Filed")
        self.filed_btn.clicked.connect(self.toggle_filed)
        b.addWidget(self.filed_btn)
        exp = QPushButton("Export to Excel…")
        exp.clicked.connect(self.export)
        b.addWidget(exp)
        b.addStretch(1)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        b.addWidget(close)
        lay.addLayout(b)
        self.ret: hst.Return | None = None
        self._fill_periods()

    def _freq_changed(self) -> None:
        self._save_settings()
        self._fill_periods()

    def _save_settings(self) -> None:
        self.s.frequency, self.s.number = self.freq.currentData(), self.number.text()
        hst.save_settings(self.books, self.s)

    def _fill_periods(self) -> None:
        self.period.blockSignals(True)
        self.period.clear()
        done = hst.filed(self.books)
        for a, b in hst.periods(self.s.frequency, self.books.fiscal_year_start_month, self.today, back=12):
            mark = " ✓ filed" if (a.isoformat(), b.isoformat()) in done else ""
            self.period.addItem(f"{a:%b} {a.day}, {a.year} – {b:%b} {b.day}, {b.year}{mark}", (a, b))
        # the latest period that has ended
        self.period.setCurrentIndex(1 if self.period.count() > 1 else 0)
        self.period.blockSignals(False)
        self.fill()

    def fill(self) -> None:
        a, b = self.period.currentData()
        r = self.ret = hst.compute(self.books, a, b, self.s)
        self.accounts_note.setText(("HST accounts: " + ", ".join(r.accounts)) if r.accounts else
                                   "No HST account found — click HST Accounts… to pick or create them.")
        net_col = NEGATIVE.name() if r.owing else POSITIVE.name()

        def row(label, cents, bold=False, colour=None):
            v = fmt_money(cents)
            v = f"<span style='color:{colour}'>{v}</span>" if colour else v
            return (f"<tr><td>{label}</td><td align='right' style='padding-left:24px'>"
                    f"{'<b>' + v + '</b>' if bold else v}</td></tr>")

        self.figures.setText(
            "<table>" + row("Line 101 — sales and other revenue", r.line101)
            + row("&nbsp;&nbsp;&nbsp;of which on sales that charged HST", r.taxable_revenue)
            + row("Line 103 — GST/HST collected", r.line103) + row("Line 106 — input tax credits", r.line106)
            + row("Line 109 — net tax " + ("(owing)" if r.owing else "(refund)"), r.line109, True, net_col)
            + row("Paid to / (refunded by) the CRA during the period", r.paid)
            + row("Owing on the HST accounts at the end of the period", r.balance_end)
            + "</table>" + (f"<p><b>Filed on {r.filed_on}</b></p>" if r.filed_on else ""))
        t = self.table
        t.setRowCount(len(r.lines))
        for i, ln in enumerate(r.lines):
            vals = [ln.date, ln.what, ln.account, fmt_money(ln.collected) if ln.collected else "",
                    fmt_money(ln.itc) if ln.itc else "", fmt_money(ln.payment) if ln.payment else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 3:
                    it.setTextAlignment(ALIGN_RIGHT)
                t.setItem(i, c, it)
        t.resizeColumnsToContents()
        self.filed_btn.setText("Not Filed After All" if r.filed_on else "Mark as Filed")

    def toggle_filed(self) -> None:
        r = self.ret
        if r is None:
            return
        if r.filed_on:
            if confirm(self, "Not filed", "Take the filed mark off this period?"):
                hst.unmark_filed(self.books, r)
        else:
            hst.mark_filed(self.books, r)
        i = self.period.currentIndex()
        self._fill_periods()
        self.period.setCurrentIndex(i)

    def export(self, path: str | None = None) -> None:
        if self.ret is None:
            return
        path = path or QFileDialog.getSaveFileName(self, "Export", f"GST-HST {self.ret.start} to {self.ret.end}.xlsx",
                                                   "Excel (*.xlsx)")[0]
        if path:
            hst.write_xlsx(self.books, path, [self.ret])
            info(self, "Exported", f"Saved to {path}.")

    def pick_accounts(self) -> None:
        dlg = QDialog(self)
        dlg.setWindowTitle("HST accounts")
        form = QVBoxLayout(dlg)
        form.addWidget(QLabel("The accounts that hold HST collected and HST paid (none ticked: any account named HST, "
                              "GST or Sales Tax):"))
        lst = QListWidget()
        chosen = set(self.s.account_ids) or {a.id for a in hst.hst_accounts(self.books)}
        for a in self.books.accounts():
            if a.type not in ("asset", "liability") or a.is_bank:
                continue
            it = QListWidgetItem(f"{a.code} · {a.name}")
            it.setData(Qt.ItemDataRole.UserRole, a.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if a.id in chosen else Qt.CheckState.Unchecked)
            lst.addItem(it)
        form.addWidget(lst, 1)
        mk = QPushButton("Create “HST Collected” and “HST Paid (Input Tax Credits)”")
        mk.setEnabled(not hst.hst_accounts(self.books, hst.Settings()))

        def create():
            try:
                hst.ensure_accounts(self.books)
            except ValueError as e:
                error(dlg, "Couldn't create", str(e))
                return
            dlg.accept()
            self.s.account_ids = []
            self._save_settings()
            self.fill()

        mk.clicked.connect(create)
        form.addWidget(mk)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addWidget(bb)
        dlg.resize(460, 480)
        if dlg.exec() and lst.count():
            ids = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())
                   if lst.item(i).checkState() == Qt.CheckState.Checked]
            auto = {a.id for a in hst.hst_accounts(self.books, hst.Settings())}
            self.s.account_ids = [] if set(ids) == auto else ids
            self._save_settings()
            self.fill()

