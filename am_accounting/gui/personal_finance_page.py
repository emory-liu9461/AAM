"""Personal Finance (beside Portfolio Summary and Deal Pipeline): your own money in one place — Net Worth, a monthly
Budget, Statements, Income from Businesses and Shareholder Loans (see personal.py, personal_finance.py,
personal_statements.py, shareholder_loans.py)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
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
                    c = _cell(fmt_money(v) if v else "", right=True, editable=not r.auto,
                              colour=MUTED if r.auto else None)
                    if r.statements.get(m):
                        c.setToolTip(f"{fmt_money(r.statements[m])} from your statements"
                                     + (f" + {fmt_money(v - r.statements[m])} typed" if v != r.statements[m] else ""))
                    t.setItem(i, 1 + m, c)
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
        elif 2 <= it.column() <= 13 and not r.auto:   # what you type is the month's total; the statements are part
            m = it.column() - 1
            pfi.set_actual(self.pf, self.year.value(), r.line.category, m, v - r.statements.get(m, 0))
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
        from .. import shareholder_loans as shl

        try:                                   # your companies' shareholder loan accounts, found by their names
            if not shl.links(pf):
                shl.auto_link(pf)
        except Exception:  # noqa: BLE001
            pass
        self.net_worth = NetWorthPage(pf, as_of=as_of)
        self.budget = BudgetTab(pf)
        self.income = IncomeTab(pf)
        self.loans = LoansTab(pf)
        self.tabs.addTab(self.net_worth, "Net Worth")
        self.tabs.addTab(self.budget, "Budget")
        self.tabs.addTab(self.income, "Income from Businesses")
        self.tabs.addTab(self.loans, "Shareholder Loans")
        self.loans.on_change = self.net_worth.refresh
        self.statements = StatementsTab(pf)
        self.tabs.insertTab(2, self.statements, "Statements")
        self.statements.on_change = self.budget.fill
        self.tabs.currentChanged.connect(lambda *_: self._shown())
        lay.addWidget(self.tabs)

    def _shown(self) -> None:
        w = self.tabs.currentWidget()
        if w is self.net_worth:
            self.net_worth.refresh()
        elif w is self.statements:
            self.statements.read_now()          # new statements in the folder are read when you open it
        else:
            w.fill()

    def refresh(self) -> None:
        self._shown()


class RecordLoanDialog(QDialog):
    """Money between you and a company: posted in the company's books against your shareholder loan account."""

    def __init__(self, pf, positions: list, parent=None, *, company: str | None = None):
        super().__init__(parent)
        from PySide6.QtCore import QDate
        from PySide6.QtWidgets import QCheckBox, QDateEdit, QFormLayout

        from .. import shareholder_loans as shl

        self.pf, self.shl = pf, shl
        self.positions = [p for p in positions if p.account_id is not None]
        self.entry_id: int | None = None
        self.setWindowTitle("Record Money Between You and a Company")
        self.resize(560, 0)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.company = QComboBox()
        for p in self.positions:
            self.company.addItem(f"{p.company} — {p.account}", p.path)
        if company:
            self.company.setCurrentIndex(max(0, self.company.findData(company)))
        self.company.currentIndexChanged.connect(lambda *_: self._accounts())
        form.addRow("Company:", self.company)
        self.direction = QComboBox()
        for k, label in shl.DIRECTIONS.items():
            self.direction.addItem(label, k)
        self.direction.currentIndexChanged.connect(lambda *_: self._accounts())
        form.addRow("What happened:", self.direction)
        self.on = QDateEdit(QDate.currentDate())
        self.on.setCalendarPopup(True)
        self.on.setDisplayFormat("MMM d, yyyy")
        form.addRow("Date:", self.on)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("$0.00")
        form.addRow("Amount:", self.amount)
        self.other_label = QLabel()
        self.other = QComboBox()
        form.addRow(self.other_label, self.other)
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("optional")
        form.addRow("Note:", self.memo)
        lay.addLayout(form)
        self.cash = QCheckBox("Also change my cash on the Net Worth page")
        self.cash.setChecked(True)
        lay.addWidget(self.cash)
        self.hint = QLabel()
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        lay.addWidget(self.hint)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Record")
        ok.setObjectName("primary")
        ok.clicked.connect(lambda *_: self.record())
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self._accounts()

    def _accounts(self) -> None:
        path = self.company.currentData()
        d = self.direction.currentData()
        self.other.clear()
        if not path:
            return
        expense = d == self.shl.PAID_EXPENSE
        self.other_label.setText("Company expense:" if expense else "Company bank account:")
        for i, n in (self.shl.expense_accounts(path) if expense else self.shl.bank_accounts(path)):
            self.other.addItem(n, i)
        self.cash.setEnabled(d != self.shl.PAID_FOR_YOU)
        self.hint.setText(
            "Posted in the company's books now. If its bank statement will bring this deposit or payment in too, "
            "categorize that line to the shareholder loan instead — or it's counted twice." if not expense else
            "Posted in the company's books: the expense, owed to you.")

    def record(self) -> int | None:
        from PySide6.QtWidgets import QMessageBox

        p = next((x for x in self.positions if x.path == self.company.currentData()), None)
        v = parse_money(self.amount.text()) if self.amount.text().strip() else None
        if p is None or not v or v <= 0 or self.other.currentData() is None:
            QMessageBox.warning(self, "Not finished", "Pick the company and its account, and enter the amount.")
            return None
        d = self.direction.currentData()
        qd = self.on.date()
        try:
            self.entry_id = self.shl.record(p.path, p.account_id, d, v, date(qd.year(), qd.month(), qd.day()),
                                            other_account=self.other.currentData(), memo=self.memo.text().strip())
        except (ValueError, OSError) as e:
            QMessageBox.warning(self, "Couldn't record it", str(e))
            return None
        if self.cash.isChecked() and self.cash.isEnabled():
            self.shl.adjust_cash(self.pf, d, v)
        self.accept()
        return self.entry_id


class LoansTab(QWidget):
    """Shareholder loans between you and your companies: the account in each company's books, its balance, and
    money recorded between you and a company (posted in its books)."""

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        self.pf = pf
        self.on_change = None                   # called after something here changed your net worth
        lay = QVBoxLayout(self)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (("to_you", "Your companies owe you"),
                                                           ("by_you", "You owe your companies"),
                                                           ("net", "Net"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Your companies</b> — the account in each company's books for money between it and "
                              "you; its balance counts in your net worth"))
        head.addStretch(1)
        find = QPushButton("Find Accounts")
        find.setToolTip("Link each company to its shareholder loan account, found by its name (or yours)")
        find.clicked.connect(lambda *_: self.find())
        head.addWidget(find)
        rec = QPushButton("Record…")
        rec.setObjectName("primary")
        rec.setToolTip("Money between you and a company — posted in the company's books")
        rec.clicked.connect(lambda *_: self.record())
        head.addWidget(rec)
        lay.addLayout(head)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Company", "Shareholder loan account", "Owed to you"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda *_: self._history())
        lay.addWidget(self.table, 2)
        self.hist_label = QLabel("<b>Entries</b>")
        lay.addWidget(self.hist_label)
        self.hist = QTableWidget(0, 3)
        self.hist.setHorizontalHeaderLabels(["Date", "Entry", "Change"])
        self.hist.horizontalHeaderItem(2).setToolTip("+ the company owes you more, − less")
        self.hist.setColumnWidth(2, 150)
        self.hist.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.hist.verticalHeader().hide()
        self.hist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.hist, 2)
        hint = QLabel("A credit balance in the company's account (it owes you) is an asset on your net worth; a debit "
                      "balance (you owe it) is a liability. Entries from the bank statements categorized to the "
                      "account count too.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.positions: list = []
        self.fill()

    def fill(self) -> None:
        from .. import shareholder_loans as shl

        self.positions = shl.positions(self.pf)
        person = pers_name(self.pf)
        t = self.table
        t.setRowCount(len(self.positions))
        for r, p in enumerate(self.positions):
            t.setItem(r, 0, _cell(p.company))
            box = QComboBox()
            box.addItem("(not linked)", None)
            try:
                found = {a.id for a in shl.candidates(p.path, person)}
                for i, label, _typ in shl.all_accounts(p.path):
                    box.addItem(("★ " if i in found else "") + label, i)
            except Exception:  # noqa: BLE001
                pass
            box.addItem(f"Create “Shareholder Loan – {person or 'you'}”…", "create")
            box.setCurrentIndex(max(0, box.findData(p.account_id)))
            box.setToolTip("★ looks like a shareholder loan")
            box.activated.connect(lambda _i, path=p.path, b=box: self._linked(path, b.currentData()))
            t.setCellWidget(r, 1, box)
            t.setItem(r, 2, _cell(fmt_money(p.owed_to_you) if p.account_id else "—", right=True,
                                  colour=NEGATIVE if p.owed_to_you < 0 else None))
        t.resizeColumnToContents(0)
        t.setColumnWidth(2, 150)
        to_you = sum(p.owed_to_you for p in self.positions if p.owed_to_you > 0)
        by_you = -sum(p.owed_to_you for p in self.positions if p.owed_to_you < 0)
        self.cards["to_you"].set(fmt_money(to_you), "an asset on your net worth")
        self.cards["by_you"].set(fmt_money(by_you), "a liability on your net worth")
        self.cards["net"].set(fmt_money(to_you - by_you), "", NEGATIVE if to_you < by_you else None)
        if self.positions and t.currentRow() < 0:
            t.setCurrentCell(0, 0)
        self._history()

    def _linked(self, path: str, data) -> None:
        from .. import shareholder_loans as shl

        if data == "create":
            data = shl.create_account(path, pers_name(self.pf))
        shl.set_link(self.pf, path, data)
        self.fill()
        if self.on_change:
            self.on_change()

    def _history(self) -> None:
        from .. import shareholder_loans as shl

        r = self.table.currentRow()
        self.hist.setRowCount(0)
        if not 0 <= r < len(self.positions):
            return
        p = self.positions[r]
        self.hist_label.setText(f"<b>Entries</b> — {p.company}" + (f", {p.account}" if p.account else ""))
        if p.account_id is None:
            return
        rows = shl.history(p.path, p.account_id)
        self.hist.setRowCount(len(rows))
        for i, ln in enumerate(rows):
            self.hist.setItem(i, 0, _cell(ln.date))
            self.hist.setItem(i, 1, _cell(ln.memo))
            self.hist.setItem(i, 2, _cell(("+" if ln.amount > 0 else "") + fmt_money(ln.amount), right=True,
                                          colour=NEGATIVE if ln.amount < 0 else POSITIVE))

    def find(self) -> int:
        from .. import shareholder_loans as shl

        n = shl.auto_link(self.pf)
        self.fill()
        return n

    def record(self, *, run=None):
        r = self.table.currentRow()
        company = self.positions[r].path if 0 <= r < len(self.positions) else None
        dlg = RecordLoanDialog(self.pf, self.positions, self, company=company)
        if not dlg.positions:
            from PySide6.QtWidgets import QMessageBox

            QMessageBox.information(self, "Link an account first", "Pick each company's shareholder loan account "
                                                                   "(or create one) in the list first.")
            return None
        ok = run(dlg) if run is not None else dlg.exec()
        if ok:
            self.fill()
            if self.on_change:
                self.on_change()
        return dlg.entry_id if ok else None


def pers_name(pf) -> str:
    from .. import personal as pers

    return pers.load(pf).name


class StatementsTab(QWidget):
    """Your personal bank and credit card statements: a folder you pick, read into transactions sorted into your
    budget's categories (see personal_statements.py)."""

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        self.pf = pf
        self.on_change = None                   # called when the budget's actuals may have changed
        lay = QVBoxLayout(self)
        fold = QHBoxLayout()
        fold.addWidget(QLabel("Statements folder:"))
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        fold.addWidget(self.folder_label, 1)
        for text, slot, tip in (("Choose…", self.choose, "The folder you put your personal statements in"),
                                ("Open", self.open_folder, "Open it (drop new statements in)"),
                                ("Read New Statements", self.read_now, "Read the statements not read yet")):
            b = QPushButton(text)
            b.setToolTip(tip)
            if text.startswith("Read"):
                b.setObjectName("primary")
            b.clicked.connect(lambda *_, f=slot: f())
            fold.addWidget(b)
        lay.addLayout(fold)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (("in", "Money in"), ("out", "Money out"),
                                                           ("unsorted", "Not sorted yet"), ("files", "Statements read"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        row = QHBoxLayout()
        row.addWidget(QLabel("Year:"))
        self.year = _year_box(self.fill)
        row.addWidget(self.year)
        self.show_which = QComboBox()
        self.show_which.addItems(["All transactions", "Not sorted yet"])
        self.show_which.currentIndexChanged.connect(lambda *_: self.fill())
        row.addWidget(self.show_which)
        row.addStretch(1)
        self.result = QLabel()
        self.result.setObjectName("hint")
        row.addWidget(self.result)
        lay.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Date", "Description", "Amount", "Category", "Statement"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        hint = QLabel("Put your bank and credit card statements (PDF, CSV, Excel or OFX) in the folder — they're read "
                      "when you open this tab. Sort a transaction and the others from the same place follow. Money "
                      "out counts in its expense category on the Budget, money in in its income category; transfers "
                      "between your accounts, card payments and money from your own companies aren't counted.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.txs: list = []
        self._show_folder()
        self.fill()

    def _show_folder(self) -> None:
        from .. import personal_statements as pst

        f = pst.folder(self.pf)
        self.folder_label.setText(f"<b>{f}</b>" if f else "<span style='color:#b3261e'>Not chosen yet</span> — pick "
                                  "the folder you'll put your statements in")

    def choose(self, path: str | None = None):
        from PySide6.QtWidgets import QFileDialog

        from .. import longpath as lpth
        from .. import personal_statements as pst

        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Folder for your personal statements",
                                                    str(pst.folder(self.pf) or ""))
        if not path:
            return None
        lpth.mkdir(path)
        pst.set_folder(self.pf, path)
        self._show_folder()
        self.read_now()
        return path

    def open_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from .. import longpath as lpth
        from .. import personal_statements as pst

        f = pst.folder(self.pf)
        if f is not None:
            lpth.mkdir(f)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f)))

    def read_now(self):
        from .. import personal_statements as pst

        res = pst.scan(self.pf)
        bits = [f"{res.added} new transaction(s) from {res.files} statement(s)" if res.files else
                "No new statements"]
        if res.duplicates:
            bits.append(f"{res.duplicates} already read from another statement")
        if res.errors:
            bits.append(f"{len(res.errors)} couldn't be read: " + "; ".join(res.errors[:3]))
        self.result.setText(" · ".join(bits))
        self.result.setToolTip("\n".join(res.errors))
        self.fill()
        if res.added and self.on_change:
            self.on_change()
        return res

    def fill(self) -> None:
        from .. import personal_statements as pst

        yr = self.year.value()
        unsorted = self.show_which.currentIndex() == 1
        self.txs = pst.transactions(self.pf, yr, unsorted_only=unsorted)
        allyr = pst.transactions(self.pf, yr)
        counted = [x for x in allyr if x.category not in ("", pst.TRANSFER)]
        self.cards["in"].set(fmt_money(sum(x.amount for x in counted if x.amount > 0)), f"in {yr}, not transfers")
        self.cards["out"].set(fmt_money(-sum(x.amount for x in counted if x.amount < 0)), f"in {yr}, not transfers")
        n_un = sum(1 for x in allyr if not x.category)
        self.cards["unsorted"].set(str(n_un), "to sort below" if n_un else "all sorted",
                                   NEGATIVE if n_un else POSITIVE)
        self.cards["files"].set(str(len(pst.files(self.pf))), "in the folder")
        t = self.table
        t.setRowCount(len(self.txs))
        cats = [""] + pst.categories()
        for r, x in enumerate(self.txs):
            t.setItem(r, 0, _cell(x.date))
            t.setItem(r, 1, _cell(x.description))
            t.setItem(r, 2, _cell(fmt_money(x.amount), right=True, colour=POSITIVE if x.amount > 0 else None))
            box = QComboBox()
            for c in cats:
                box.addItem(c or "— not sorted —", c)
            box.setCurrentIndex(max(0, box.findData(x.category)))
            box.activated.connect(lambda _i, tid=x.id, b=box: self.sort(tid, b.currentData()))
            t.setCellWidget(r, 3, box)
            t.setItem(r, 4, _cell(x.source, colour=MUTED))
        t.resizeColumnToContents(0)
        t.setColumnWidth(2, 120)
        t.setColumnWidth(3, 240)
        t.setColumnWidth(4, 220)

    def sort(self, tx_id: int, category: str) -> int:
        from .. import personal_statements as pst

        n = pst.set_category(self.pf, tx_id, category)
        self.fill()
        if self.on_change:
            self.on_change()
        return n
