"""Portfolio → Intercompany: what the group's companies owe each other, checked from both sides."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSplitter, QTableWidgetItem, QVBoxLayout, QWidget

from .. import intercompany as ic
from ..money import fmt_money
from .common import ALIGN_RIGHT, NEGATIVE, POSITIVE

PAIR_COLS = ["Company A", "Company B", "A's books: B owes A", "B's books: B owes A", "Difference", "Check"]
ACC_COLS = ["In the books of", "Account", "With", "Balance", "Meaning"]


class IntercompanyPage(QWidget):
    def __init__(self, companies, as_of, parent=None, open_books=None):
        super().__init__(parent)
        from .asset_mgmt import _table

        self.companies, self.as_of, self.open_books = companies, as_of, open_books
        self.result: ic.ICResult | None = None
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setTextFormat(Qt.TextFormat.RichText)
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        split = QSplitter(Qt.Orientation.Vertical)
        self.pairs = _table(PAIR_COLS, 5)
        self.pairs.itemSelectionChanged.connect(self._fill_accounts)
        split.addWidget(self.pairs)
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 6, 0, 0)
        bl.addWidget(QLabel("<b>The accounts</b> (double-click one to open that company's books)"))
        self.accounts = _table(ACC_COLS, 4)
        self.accounts.doubleClicked.connect(self._open)
        bl.addWidget(self.accounts, 1)
        split.addWidget(bottom)
        split.setSizes([260, 220])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        hint = QLabel("An account is intercompany when its name names another company in the group (“Due to …”, "
                      "“Due from …”, “Shareholder Loan – …”, “Loan to …”). Both sides should agree; the balances are "
                      "taken out of the portfolio's combined figures on Consolidated Financials.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        row.addWidget(hint, 1)
        b = QPushButton("Check Again")
        b.clicked.connect(lambda: self.fill(force=True))
        row.addWidget(b)
        lay.addLayout(row)

    def fill(self, *, force: bool = False) -> None:
        if force:
            ic._CACHE.clear()
        res = self.result = ic.check(self.companies(), self.as_of())
        bad = res.problems
        txt = (f"<b>{len(res.pairs)}</b> pair(s) of companies with balances between them at {res.as_of} · "
               + (f"<span style='color:{NEGATIVE.name()}'><b>{len(bad)} don't match</b></span>" if bad else
                  f"<span style='color:{POSITIVE.name()}'><b>all match</b></span>" if res.pairs else
                  "no intercompany accounts found"))
        if res.errors:
            txt += "<br>" + "<br>".join("⚠ " + e for e in res.errors)
        self.head.setText(txt)
        t = self.pairs
        t.setRowCount(len(res.pairs))
        for r, p in enumerate(res.pairs):
            vals = [p.a, p.b, fmt_money(p.a_says) if p.a_accounts else "—",
                    fmt_money(p.b_says) if p.b_accounts else "—", fmt_money(p.difference),
                    ("✓ " if p.ok else "⚠ ") + p.status]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if 2 <= c <= 4:
                    it.setTextAlignment(ALIGN_RIGHT)
                if c == 5:
                    it.setForeground(POSITIVE if p.ok else NEGATIVE)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        if res.pairs:
            t.selectRow(0)
        else:
            self.accounts.setRowCount(0)

    def _fill_accounts(self) -> None:
        rows = self.pairs.selectionModel().selectedRows()
        if not rows or self.result is None:
            return
        p = self.result.pairs[rows[0].row()]
        accs = p.a_accounts + p.b_accounts
        t = self.accounts
        t.setRowCount(len(accs))
        for r, a in enumerate(accs):
            mean = (f"{a.other} owes {a.company} {fmt_money(a.balance)}" if a.balance > 0 else
                    f"{a.company} owes {a.other} {fmt_money(-a.balance)}" if a.balance < 0 else "Nothing owed")
            for c, v in enumerate([a.company, a.account, a.other, fmt_money(a.balance), mean]):
                it = QTableWidgetItem(v)
                if c == 3:
                    it.setTextAlignment(ALIGN_RIGHT)
                it.setData(Qt.ItemDataRole.UserRole, a.path)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()

    def _open(self, index) -> None:
        it = self.accounts.item(index.row(), 0)
        if it is not None and self.open_books:
            self.open_books(it.data(Qt.ItemDataRole.UserRole))
