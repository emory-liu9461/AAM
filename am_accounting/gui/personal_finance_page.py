"""Personal Finance (beside Portfolio Summary and Deal Pipeline): your own money in one place — Net Worth, a monthly
Budget, and Income from Businesses (see personal.py and personal_finance.py)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import personal_finance as pfi
from ..money import fmt_money, parse_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE
from .net_worth_charts import TimeChart
from .net_worth_page import NetWorthPage

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ROLE = Qt.ItemDataRole.UserRole


def _cell(text: str, *, right=False, bold=False, colour=None, editable=False) -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    if right:
        it.setTextAlignment(ALIGN_RIGHT)
    if bold:
        f = it.font()
        f.setBold(True)
        it.setFont(f)
    if colour is not None:
        it.setForeground(colour)
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


def _year_box(on_change) -> QSpinBox:
    y = QSpinBox()
    y.setRange(2000, 2100)
    y.setValue(date.today().year)
    y.valueChanged.connect(lambda *_: on_change())
    return y


class BudgetTab(QWidget):
    """A monthly budget per category against what actually came in and went out each month."""

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        self.pf = pf
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Year:"))
        self.year = _year_box(self.fill)
        top.addWidget(self.year)
        top.addStretch(1)
        for text, slot, tip in (("Add Category…", self.add_category, "A category of your own"),
                                ("Remove Category", self.remove_category, "Take the selected category off the budget")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s=slot: s())
            top.addWidget(b)
        lay.addLayout(top)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (("income", "Income this year"), ("spent", "Spent this year"),
                                                           ("net", "Saved this year"), ("vs", "Against budget"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = QTableWidget()
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.itemChanged.connect(self._edited)
        split.addWidget(self.table)
        self.chart = TimeChart("Income and spending by month (totals for the year)", legend_total=True)
        split.addWidget(self.chart)
        split.setSizes([520, 220])
        lay.addWidget(split, 1)
        hint = QLabel("Type the monthly budget and each month's actual in the table. Business income comes from "
                      "Income from Businesses (your distributions and what you took from your companies).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._rows: list = []
        self.fill()

    COLS = ["Category", "Budget / month"] + MONTHS + ["Year actual", "Budget to date", "Variance"]

    def fill(self) -> None:
        b = self.data = pfi.budget(self.pf, self.year.value())
        t = self.table
        t.blockSignals(True)
        t.clear()
        t.setColumnCount(len(self.COLS))
        t.setHorizontalHeaderLabels(self.COLS)
        rows: list = []
        for group, title in ((pfi.INCOME, "INCOME"), (pfi.EXPENSE, "EXPENSES")):
            rows.append(("head", title))
            rows += [("line", r) for r in b.rows if r.line.group == group]
            rows.append(("total", group))
        rows.append(("net", None))
        self._rows = rows
        t.setRowCount(len(rows))
        n = max(b.through, 0)
        for i, (kind, x) in enumerate(rows):
            if kind == "head":
                t.setItem(i, 0, _cell(x, bold=True, colour=MUTED))
                for c in range(1, len(self.COLS)):
                    t.setItem(i, c, _cell(""))
                continue
            if kind == "line":
                r = x
                spend = r.line.group == pfi.EXPENSE
                t.setItem(i, 0, _cell("    " + r.line.category))
                t.item(i, 0).setToolTip("From Income from Businesses" if r.auto else "")
                t.setItem(i, 1, _cell(fmt_money(r.line.budget) if r.line.budget else "", right=True, editable=True))
                for m in range(1, 13):
                    v = r.months.get(m, 0)
                    t.setItem(i, 1 + m, _cell(fmt_money(v) if v else "", right=True, editable=not r.auto,
                                              colour=MUTED if r.auto else None))
                to_date = r.budget_to(n)
                var = (to_date - r.actual) if spend else (r.actual - to_date)
                t.setItem(i, 14, _cell(fmt_money(r.actual), right=True, bold=True))
                t.setItem(i, 15, _cell(fmt_money(to_date) if r.line.budget else "", right=True))
                t.setItem(i, 16, _cell(fmt_money(var) if r.line.budget else "", right=True,
                                       colour=(NEGATIVE if var < 0 else POSITIVE) if r.line.budget else None))
                continue
            if kind == "total":
                g = x
                label = "Total income" if g == pfi.INCOME else "Total expenses"
                bud = b.budget_total(g)
                act = b.total(g)
                vals = [label, fmt_money(bud)] + [fmt_money(b.total(g, m)) for m in range(1, 13)] + \
                       [fmt_money(act), fmt_money(bud * n), fmt_money((act - bud * n) if g == pfi.INCOME
                                                                     else (bud * n - act))]
            else:
                bud = b.budget_total(pfi.INCOME) - b.budget_total(pfi.EXPENSE)
                vals = ["Saved (income − expenses)", fmt_money(bud)] + [fmt_money(b.net(m)) for m in range(1, 13)] + \
                       [fmt_money(b.net()), fmt_money(bud * n), fmt_money(b.net() - bud * n)]
            for c, v in enumerate(vals):
                t.setItem(i, c, _cell(v, right=c > 0, bold=True))
        t.resizeColumnsToContents()
        t.setColumnWidth(0, max(t.columnWidth(0), 240))
        for c in range(1, len(self.COLS)):
            t.setColumnWidth(c, max(t.columnWidth(c), 92))
        t.blockSignals(False)
        inc, exp = b.total(pfi.INCOME), b.total(pfi.EXPENSE)
        bud_net = (b.budget_total(pfi.INCOME) - b.budget_total(pfi.EXPENSE)) * n
        self.cards["income"].set(fmt_money(inc), f"budget {fmt_money(b.budget_total(pfi.INCOME) * n)} to date")
        self.cards["spent"].set(fmt_money(exp), f"budget {fmt_money(b.budget_total(pfi.EXPENSE) * n)} to date")
        self.cards["net"].set(fmt_money(b.net()), f"{b.net() / inc * 100:.0f}% of income" if inc else "",
                              NEGATIVE if b.net() < 0 else None)
        self.cards["vs"].set(fmt_money(b.net() - bud_net), "saved more than planned" if b.net() >= bud_net
                             else "saved less than planned", NEGATIVE if b.net() < bud_net else POSITIVE)
        yr = self.year.value()
        ms = range(1, max(n, 1) + 1)                  # the months so far (the whole year for a past one)
        self.chart.set_data([f"{yr}-{m:02d}-01" for m in ms],
                            [("Income", [b.total(pfi.INCOME, m) for m in ms]),
                             ("Spending", [b.total(pfi.EXPENSE, m) for m in ms]),
                             ("Saved", [b.net(m) for m in ms])], labels=[MONTHS[m - 1] for m in ms], keep_empty=True)

    def _edited(self, it: QTableWidgetItem) -> None:
        kind, r = self._rows[it.row()]
        if kind != "line":
            return
        raw = it.text().strip()
        v = parse_money(raw) if raw else 0
        if v is None:
            self.fill()
            return
        if it.column() == 1:
            lines = pfi.budget_lines(self.pf)
            for ln in lines:
                if ln.category == r.line.category and ln.group == r.line.group:
                    ln.budget = v
            pfi.save_budget_lines(self.pf, lines)
        elif 2 <= it.column() <= 13 and not r.auto:
            pfi.set_actual(self.pf, self.year.value(), r.line.category, it.column() - 1, v)
        self.fill()

    def add_category(self, name: str | None = None, group: str | None = None) -> None:
        if name is None:
            group, ok = QInputDialog.getItem(self, "Add category", "Income or expense?", [pfi.EXPENSE, pfi.INCOME],
                                             0, False)
            if not ok:
                return
            name, ok = QInputDialog.getText(self, "Add category", "Category name:")
            if not ok:
                return
        name = (name or "").strip()
        if not name:
            return
        lines = pfi.budget_lines(self.pf)
        if any(ln.category.lower() == name.lower() for ln in lines):
            return
        group = group or pfi.EXPENSE
        last = max((i for i, ln in enumerate(lines) if ln.group == group), default=len(lines) - 1)
        lines.insert(last + 1, pfi.Line(group, name))
        pfi.save_budget_lines(self.pf, lines)
        self.fill()

    def remove_category(self, category: str | None = None) -> None:
        if category is None:
            r = self.table.currentRow()
            if r < 0 or self._rows[r][0] != "line":
                return
            category = self._rows[r][1].line.category
        if category == pfi.BUSINESS:
            return
        pfi.save_budget_lines(self.pf, [ln for ln in pfi.budget_lines(self.pf) if ln.category != category])
        self.fill()


class IncomeTab(QWidget):
    """What you took from your businesses: distributions from the portfolio at your share, and your own entries."""

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        self.pf = pf
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Year:"))
        self.year = _year_box(self.fill)
        top.addWidget(self.year)
        top.addStretch(1)
        lay.addLayout(top)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (("total", "Income from businesses"),
                                                           ("dist", "Distributions / dividends"),
                                                           ("other", "Salary, fees and other"),
                                                           ("capital", "Capital returned (not income)"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        lay.addWidget(QLabel("<b>From the portfolio</b> — distributions recorded on each holding, at your share (set "
                             "your share on the Net Worth tab)"))
        self.port = QTableWidget(0, 5)
        self.port.setHorizontalHeaderLabels(["Company", "Your share", "Distributions to you", "Capital returned",
                                             "Months paid"])
        self.port.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.port.verticalHeader().hide()
        self.port.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.port.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.port, 2)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Other income from your businesses</b> — salary, management fees, bonuses …"))
        head.addStretch(1)
        for text, slot in (("Add", self.add), ("Remove", self.remove)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            head.addWidget(b)
        lay.addLayout(head)
        self.mine = QTableWidget(0, 5)
        self.mine.setHorizontalHeaderLabels(["Date", "Company", "Kind", "Amount", "Note"])
        self.mine.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.mine.verticalHeader().hide()
        self.mine.itemChanged.connect(lambda *_: self._save())
        lay.addWidget(self.mine, 2)
        self.chart = TimeChart("Income from businesses by month (total for the year)", legend_total=True)
        lay.addWidget(self.chart, 2)
        self._loading = False
        self.fill()

    def _companies(self) -> list[str]:
        return [h.name for h in self.pf.holdings()]

    def fill(self) -> None:
        self._loading = True
        bi = self.data = pfi.business_income(self.pf, self.year.value())
        t = self.port
        rows = [h for h in bi.holdings]
        t.setRowCount(len(rows))
        for i, h in enumerate(rows):
            vals = [h.name, f"{h.share_pct:g}%", fmt_money(h.distributions), fmt_money(h.capital),
                    ", ".join(MONTHS[m - 1] for m in sorted(h.by_month)) or "—"]
            for c, v in enumerate(vals):
                t.setItem(i, c, _cell(v, right=c in (1, 2, 3), colour=MUTED if c == 4 else None))
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        m = self.mine
        m.blockSignals(True)
        m.setRowCount(0)
        for r in bi.entered:
            self._add_row(r)
        m.blockSignals(False)
        self._totals()
        self._loading = False

    def _add_row(self, r: pfi.Received) -> None:
        m = self.mine
        i = m.rowCount()
        m.insertRow(i)
        m.setItem(i, 0, _cell(r.date, editable=True))
        comp = QComboBox()
        comp.setEditable(True)
        comp.addItems(self._companies())
        comp.setCurrentText(r.company)
        comp.currentTextChanged.connect(lambda *_: self._save())
        m.setCellWidget(i, 1, comp)
        kind = QComboBox()
        kind.addItems(pfi.INCOME_KINDS)
        kind.setCurrentText(r.kind)
        kind.currentTextChanged.connect(lambda *_: self._save())
        m.setCellWidget(i, 2, kind)
        m.setItem(i, 3, _cell(fmt_money(r.amount) if r.amount else "", right=True, editable=True))
        m.setItem(i, 4, _cell(r.note, editable=True))
        m.setColumnWidth(1, 220)
        m.setColumnWidth(2, 160)
        m.setColumnWidth(3, 120)

    def rows(self) -> list[pfi.Received]:
        out = []
        m = self.mine
        for i in range(m.rowCount()):
            d = (m.item(i, 0).text() if m.item(i, 0) else "").strip()
            try:
                d = date.fromisoformat(d).isoformat()
            except ValueError:
                d = date(self.year.value(), 1, 1).isoformat() if not d else d
            raw = m.item(i, 3).text() if m.item(i, 3) else ""
            out.append(pfi.Received(d, m.cellWidget(i, 1).currentText().strip(), m.cellWidget(i, 2).currentText(),
                                    (parse_money(raw) or 0) if raw.strip() else 0,
                                    (m.item(i, 4).text() if m.item(i, 4) else "").strip()))
        return out

    def _save(self) -> None:
        if self._loading:
            return
        yr = str(self.year.value())
        mine = [r for r in self.rows() if len(r.date) == 10]
        keep = [r for r in pfi.received(self.pf) if not r.date.startswith(yr)]
        pfi.save_received(self.pf, keep + mine)
        self.mine.blockSignals(True)
        for i in range(self.mine.rowCount()):                   # tidy amounts as typed
            it = self.mine.item(i, 3)
            v = parse_money(it.text()) if it and it.text().strip() else None
            if v is not None:
                it.setText(fmt_money(v))
        self.mine.blockSignals(False)
        self.data = pfi.business_income(self.pf, self.year.value())
        self._totals()

    def _totals(self) -> None:
        bi = self.data
        self.cards["total"].set(fmt_money(bi.total), f"{fmt_money(round(bi.total / 12))} a month on average")
        n_co = sum(1 for h in bi.holdings if h.distributions)
        self.cards["dist"].set(fmt_money(bi.distributions), f"from {n_co} compan{'y' if n_co == 1 else 'ies'}")
        n_in = len(bi.entered)
        self.cards["other"].set(fmt_money(bi.other), f"{n_in} entr{'y' if n_in == 1 else 'ies'}")
        self.cards["capital"].set(fmt_money(bi.capital), "return of capital, loans repaid, sales")
        bm = bi.by_month()
        yr = self.year.value()
        today = date.today()
        last = 12 if yr < today.year else (today.month if yr == today.year else 1)
        last = max([last] + [m for m, v in bm.items() if v])
        ms = range(1, last + 1)
        self.chart.set_data([f"{yr}-{m:02d}-01" for m in ms], [("Income", [bm[m] for m in ms])],
                            labels=[MONTHS[m - 1] for m in ms], keep_empty=True)

    def add(self, r: pfi.Received | None = None) -> None:
        today = date.today()
        yr = self.year.value()
        d = today if today.year == yr else date(yr, 12, 31)
        self.mine.blockSignals(True)
        self._add_row(r or pfi.Received(d.isoformat(), "", "Salary", 0))
        self.mine.blockSignals(False)
        self._save()

    def remove(self) -> None:
        r = self.mine.currentRow()
        if r >= 0:
            self.mine.removeRow(r)
            self._save()


class PersonalFinancePage(QWidget):
    """Net Worth, Budget and Income from Businesses."""

    def __init__(self, pf, parent=None, *, as_of=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.net_worth = NetWorthPage(pf, as_of=as_of)
        self.budget = BudgetTab(pf)
        self.income = IncomeTab(pf)
        self.tabs.addTab(self.net_worth, "Net Worth")
        self.tabs.addTab(self.budget, "Budget")
        self.tabs.addTab(self.income, "Income from Businesses")
        self.tabs.currentChanged.connect(lambda *_: self._shown())
        lay.addWidget(self.tabs)

    def _shown(self) -> None:
        w = self.tabs.currentWidget()
        if w is self.net_worth:
            self.net_worth.refresh()
        else:
            w.fill()

    def refresh(self) -> None:
        self._shown()
