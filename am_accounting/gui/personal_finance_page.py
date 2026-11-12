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


def _group(title: str, parent_layout, stretch: int = 1):
    """A framed box with its title on the frame (a major part of a tab). Returns the box's own layout."""
    from PySide6.QtWidgets import QGroupBox

    box = QGroupBox(title)
    v = QVBoxLayout(box)
    parent_layout.addWidget(box, stretch)
    return v


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
        box = _group("From your companies — distributions recorded on each holding, at your ownership (from their "
                     "cap tables)", lay, 2)
        self.port = QTableWidget(0, 5)
        self.port.setHorizontalHeaderLabels(["Company", "Your share", "Distributions to you", "Capital returned",
                                             "Months paid"])
        self.port.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.port.verticalHeader().hide()
        self.port.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.port.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        box.addWidget(self.port)
        box = _group("Other income from your businesses — salary, management fees, bonuses …", lay, 2)
        head = QHBoxLayout()
        head.addStretch(1)
        for text, slot in (("Add", self.add), ("Remove", self.remove)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            head.addWidget(b)
        box.addLayout(head)
        self.mine = QTableWidget(0, 5)
        self.mine.setHorizontalHeaderLabels(["Date", "Company", "Kind", "Amount", "Note"])
        self.mine.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.mine.verticalHeader().hide()
        self.mine.itemChanged.connect(lambda *_: self._save())
        box.addWidget(self.mine)
        box = _group("By month", lay, 2)
        self.chart = TimeChart("Income from businesses by month (total for the year)", legend_total=True)
        box.addWidget(self.chart)
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
        from .. import personal_accounts as pacc

        try:                                   # cash, investments and cards typed on Net Worth: to their own tabs
            pacc.move_typed_lines(pf)
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
        self.statements.on_change = lambda: (self.budget.fill(), self.net_worth.refresh())
        self.banks = BankAccountsTab(pf)
        self.investments = InvestmentsTab(pf)
        # Bank Accounts: your accounts and balances, with their statements' transactions below
        self.bank_tab = QSplitter(Qt.Orientation.Vertical)
        self.bank_tab.addWidget(self.banks)
        self.bank_tab.addWidget(self.statements)
        self.bank_tab.setSizes([300, 520])
        self.tabs.insertTab(1, self.bank_tab, "Bank Accounts")
        self.tabs.insertTab(2, self.investments, "Investments")
        self.banks.on_change = self.net_worth.refresh
        self.investments.on_change = self.net_worth.refresh
        self.tabs.currentChanged.connect(lambda *_: self._shown())
        lay.addWidget(self.tabs)

    def _shown(self) -> None:
        w = self.tabs.currentWidget()
        if w is self.net_worth:
            self.net_worth.refresh()
        elif w is self.bank_tab:
            self.banks.fill()
            self.statements.read_now()          # new statements in the folder are read when you open it
        else:
            w.fill()

    def refresh(self) -> None:
        self._shown()

    def settings(self, *, run=None, first: bool = False):
        """Personal Settings (your name and folders). ``first``: opened by itself the first time (doesn't wait)."""
        from .net_worth_page import PersonalSettingsDialog

        dlg = PersonalSettingsDialog(self.net_worth.pf, self)
        if first:
            dlg.setWindowTitle("Personal Settings — first, a few details")
            dlg.finished.connect(lambda *_: self.refresh())
            dlg.open()
            return dlg
        ok = run(dlg) if run is not None else dlg.exec()
        self.refresh()
        return ok


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
        box = _group("Your companies — the account in each company's books for money between it and you; its balance "
                     "counts in your net worth", lay, 2)
        head = QHBoxLayout()
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
        box.addLayout(head)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Company", "Shareholder loan account", "Owed to you"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.currentCellChanged.connect(lambda *_: self._history())
        box.addWidget(self.table)
        hist_box = _group("Entries", lay, 2)
        self.hist_label = QLabel()
        hist_box.addWidget(self.hist_label)
        self.hist = QTableWidget(0, 3)
        self.hist.setHorizontalHeaderLabels(["Date", "Entry", "Change"])
        self.hist.horizontalHeaderItem(2).setToolTip("+ the company owes you more, − less")
        self.hist.setColumnWidth(2, 150)
        self.hist.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.hist.verticalHeader().hide()
        self.hist.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hist_box.addWidget(self.hist)
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
        self.hist_label.setText(f"<b>{p.company}</b>" + (f" — {p.account}" if p.account else ""))
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
        for text, slot, tip in (("Change Folder…", self.choose, "Pick a different folder to put your personal "
                                                                "statements in"),
                                ("Open in Explorer", self.open_folder, "Open the folder (drop new statements in)"),
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


def _money_cell(cents: int, *, editable=True, colour=None) -> QTableWidgetItem:
    return _cell(fmt_money(cents) if cents else "", right=True, editable=editable, colour=colour)


class BankAccountsTab(QWidget):
    """Your bank accounts and cash, and your credit cards, lines of credit and other personal debt — each with its
    balance (what you owe, for a debt) — into your net worth."""

    COLS = ["Account", "Institution", "Type", "Balance", "As of", "Note"]
    DEBT_COLS = ["Account", "Lender", "Type", "Owed", "As of", "Note"]

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        from .. import personal_accounts as pacc

        self.pf = pf
        self.on_change = None
        lay = QVBoxLayout(self)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (("cash", "Cash in your accounts"),
                                                           ("owed", "Owed on cards, lines of credit and loans"),
                                                           ("net", "Net"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        self.table = self._box(lay, "Bank accounts and cash — type each balance", self.COLS, pacc.CASH_TYPES,
                               "Add Account", "Chequing")
        self.debt_table = self._box(lay, "Credit cards, lines of credit and other debt — type what you owe on each",
                                    self.DEBT_COLS, pacc.DEBT_TYPES, "Add Card / Debt", "Credit card")
        self._last = self.table
        self.accts = None
        self._loading = False
        self.fill()

    def _box(self, lay, title: str, cols: list[str], types: list[str], add_text: str, kind: str) -> QTableWidget:
        box = _group(title, lay, 1)
        head = QHBoxLayout()
        head.addStretch(1)
        t = QTableWidget(0, len(cols))
        t.setProperty("types", types)
        for text, slot in ((add_text, lambda: self.add(kind=kind)), ("Remove", lambda tb=t: self.remove(tb))):
            b = QPushButton(text)
            if text.startswith("Add"):
                b.setObjectName("primary")
            b.clicked.connect(lambda *_, f=slot: f())
            head.addWidget(b)
        box.addLayout(head)
        t.setHorizontalHeaderLabels(cols)
        t.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().hide()
        t.itemChanged.connect(lambda *_: self._save())
        t.currentCellChanged.connect(lambda *_, tb=t: setattr(self, "_last", tb))
        box.addWidget(t)
        return t

    def _table_for(self, b) -> QTableWidget:
        return self.debt_table if b.owed else self.table

    def fill(self) -> None:
        from .. import personal_accounts as pacc

        self._loading = True
        self.accts = pacc.load(self.pf)
        for t in (self.table, self.debt_table):
            t.blockSignals(True)
            t.setRowCount(0)
        for b in self.accts.banks:
            self._row(b)
        for t in (self.table, self.debt_table):
            t.blockSignals(False)
            for c, w in enumerate((200, 160, 140, 130, 110)):
                t.setColumnWidth(c, w)
        self._loading = False
        self._totals()

    def _row(self, b) -> None:
        t = self._table_for(b)
        r = t.rowCount()
        t.insertRow(r)
        name = _cell(b.name, editable=True)
        name.setData(ROLE, b.id)
        t.setItem(r, 0, name)
        t.setItem(r, 1, _cell(b.institution, editable=True))
        kind = QComboBox()
        kind.addItems(t.property("types"))
        kind.setCurrentText(b.type)
        kind.currentTextChanged.connect(lambda *_: self._save())
        t.setCellWidget(r, 2, kind)
        t.setItem(r, 3, _money_cell(b.balance, colour=NEGATIVE if b.owed and b.balance else None))
        t.setItem(r, 4, _cell(b.as_of, editable=True))
        t.setItem(r, 5, _cell(b.note, editable=True))

    def _rows(self, t: QTableWidget) -> list:
        from .. import personal_accounts as pacc

        out = []
        t.blockSignals(True)
        for r in range(t.rowCount()):
            raw = t.item(r, 3).text() if t.item(r, 3) else ""
            v = parse_money(raw) if raw.strip() else 0
            if v is None:
                t.item(r, 3).setForeground(NEGATIVE)
                v = 0
            elif raw.strip():
                t.item(r, 3).setText(fmt_money(v))
            as_of = (t.item(r, 4).text() if t.item(r, 4) else "").strip()
            old = next((b for b in self.accts.banks if b.id == t.item(r, 0).data(ROLE)), None)
            if old is not None and v != old.balance and as_of == old.as_of:
                as_of = date.today().isoformat()            # a new balance: as of today unless you say otherwise
                t.item(r, 4).setText(as_of)
            out.append(pacc.BankAccount(t.item(r, 0).text().strip(), t.cellWidget(r, 2).currentText(),
                                        (t.item(r, 1).text() if t.item(r, 1) else "").strip(), v, as_of,
                                        (t.item(r, 5).text() if t.item(r, 5) else "").strip(),
                                        t.item(r, 0).data(ROLE)))
        t.blockSignals(False)
        return out

    def _save(self) -> None:
        from .. import personal_accounts as pacc

        if self._loading:
            return
        self.accts.banks = self._rows(self.table) + self._rows(self.debt_table)
        pacc.save(self.pf, self.accts)
        self._totals()
        if self.on_change:
            self.on_change()

    def _totals(self) -> None:
        banks = self.accts.banks if self.accts else []
        cash = sum(b.balance for b in banks if not b.owed)
        owed = sum(b.balance for b in banks if b.owed)
        self.cards["cash"].set(fmt_money(cash), f"{sum(1 for b in banks if not b.owed)} account(s)")
        self.cards["owed"].set(fmt_money(owed), f"{sum(1 for b in banks if b.owed)} card(s) / loan(s)",
                               NEGATIVE if owed else None)
        self.cards["net"].set(fmt_money(cash - owed), "", NEGATIVE if cash < owed else None)

    def add(self, name: str = "", kind: str = "Chequing", balance: int = 0) -> None:
        from .. import personal_accounts as pacc

        b = pacc.BankAccount(name or ("New account" if kind in pacc.CASH_TYPES else f"New {kind.lower()}"), kind,
                             balance=balance, as_of=date.today().isoformat(), id=self.accts.new_id())
        self.accts.banks.append(b)
        pacc.save(self.pf, self.accts)
        self.fill()
        t = self._table_for(b)
        t.setCurrentCell(t.rowCount() - 1, 0)
        if self.on_change:
            self.on_change()

    def remove(self, table: QTableWidget | None = None) -> None:
        t = table or self._last
        r = t.currentRow()
        if r >= 0:
            t.removeRow(r)
            self._save()


class InvestmentsTab(QWidget):
    """Your investment accounts (RRSP, TFSA …) and what's in them — into your net worth."""

    HCOLS = ["Symbol", "Name", "Quantity", "Price", "Market value", "Book cost", "Gain"]

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        self.pf = pf
        self.on_change = None
        lay = QVBoxLayout(self)
        cards = QHBoxLayout()
        self.cards = {k: Card(t, small=True) for k, t in (("value", "Market value"), ("cost", "Book cost"),
                                                           ("gain", "Gain"), ("by", "By account type"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        box = _group("Your investment accounts", lay, 2)
        head = QHBoxLayout()
        head.addStretch(1)
        for text, slot in (("Add Account", self.add_account), ("Remove Account", self.remove_account)):
            b = QPushButton(text)
            if text.startswith("Add"):
                b.setObjectName("primary")
            b.clicked.connect(lambda *_, f=slot: f())
            head.addWidget(b)
        box.addLayout(head)
        self.accounts = QTableWidget(0, 6)
        self.accounts.setHorizontalHeaderLabels(["Account", "Institution", "Type", "Cash in it", "Market value",
                                                 "Gain"])
        self.accounts.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.accounts.verticalHeader().hide()
        self.accounts.itemChanged.connect(lambda *_: self._save_accounts())
        self.accounts.currentCellChanged.connect(lambda r, *_: self._show_holdings(r))
        box.addWidget(self.accounts)
        hbox = _group("Holdings", lay, 3)
        hh = QHBoxLayout()
        self.hold_label = QLabel()
        hh.addWidget(self.hold_label)
        hh.addStretch(1)
        for text, slot in (("Add Holding", self.add_holding), ("Remove Holding", self.remove_holding)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, f=slot: f())
            hh.addWidget(b)
        hbox.addLayout(hh)
        self.holdings = QTableWidget(0, len(self.HCOLS))
        self.holdings.setHorizontalHeaderLabels(self.HCOLS)
        self.holdings.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.holdings.verticalHeader().hide()
        self.holdings.itemChanged.connect(lambda *_: self._save_holdings())
        hbox.addWidget(self.holdings)
        hint = QLabel("Type each holding's quantity, price and book cost (prices aren't looked up online). An "
                      "account's market value is its cash plus its holdings; it counts in your net worth by type.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.accts = None
        self._loading = False
        self.fill()

    def _cur(self):
        r = self.accounts.currentRow()
        return self.accts.investments[r] if self.accts and 0 <= r < len(self.accts.investments) else None

    def fill(self, keep: int | None = None) -> None:
        from .. import personal_accounts as pacc

        self._loading = True
        cur = self._cur()
        keep = keep if keep is not None else (cur.id if cur else None)
        self.accts = pacc.load(self.pf)
        t = self.accounts
        t.blockSignals(True)
        t.setRowCount(len(self.accts.investments))
        for r, a in enumerate(self.accts.investments):
            name = _cell(a.name, editable=True)
            name.setData(ROLE, a.id)
            t.setItem(r, 0, name)
            t.setItem(r, 1, _cell(a.institution, editable=True))
            kind = QComboBox()
            kind.addItems(pacc.INVEST_TYPES)
            kind.setCurrentText(a.type)
            kind.currentTextChanged.connect(lambda *_: self._save_accounts())
            t.setCellWidget(r, 2, kind)
            t.setItem(r, 3, _money_cell(a.cash))
            t.setItem(r, 4, _cell(fmt_money(a.market_value), right=True, bold=True))
            t.setItem(r, 5, _cell(fmt_money(a.gain), right=True, colour=NEGATIVE if a.gain < 0 else POSITIVE))
        t.blockSignals(False)
        for c, w in enumerate((200, 0, 150, 120, 130, 120)):
            if w:
                t.setColumnWidth(c, w)
        row = next((r for r, a in enumerate(self.accts.investments) if a.id == keep), 0)
        self._loading = False
        if self.accts.investments:
            t.setCurrentCell(row, 0)
        self._show_holdings(t.currentRow())
        self._totals()

    def _show_holdings(self, r: int) -> None:
        a = self.accts.investments[r] if self.accts and 0 <= r < len(self.accts.investments) else None
        h = self.holdings
        h.blockSignals(True)
        h.setRowCount(0)
        self.hold_label.setText(f"In <b>{a.name}</b>" if a else "Pick an account above")
        for x in (a.holdings if a else []):
            i = h.rowCount()
            h.insertRow(i)
            h.setItem(i, 0, _cell(x.symbol, editable=True))
            h.setItem(i, 1, _cell(x.name, editable=True))
            h.setItem(i, 2, _cell(f"{x.quantity:g}" if x.quantity else "", right=True, editable=True))
            h.setItem(i, 3, _money_cell(x.price))
            h.setItem(i, 4, _cell(fmt_money(x.market_value), right=True, bold=True))
            h.setItem(i, 5, _money_cell(x.book_cost))
            g = x.market_value - x.book_cost
            h.setItem(i, 6, _cell(fmt_money(g) if x.book_cost else "", right=True,
                                  colour=NEGATIVE if g < 0 else POSITIVE))
        for c, w in enumerate((90, 0, 90, 110, 130, 120, 110)):
            if w:
                h.setColumnWidth(c, w)
        h.blockSignals(False)

    def _save_accounts(self) -> None:
        from .. import personal_accounts as pacc

        if self._loading:
            return
        t = self.accounts
        for r, a in enumerate(self.accts.investments):
            if r >= t.rowCount():
                break
            a.name = t.item(r, 0).text().strip()
            a.institution = (t.item(r, 1).text() if t.item(r, 1) else "").strip()
            a.type = t.cellWidget(r, 2).currentText()
            raw = t.item(r, 3).text() if t.item(r, 3) else ""
            a.cash = (parse_money(raw) or 0) if raw.strip() else 0
        pacc.save(self.pf, self.accts)
        self.fill()
        if self.on_change:
            self.on_change()

    def _save_holdings(self) -> None:
        from .. import personal_accounts as pacc

        a = self._cur()
        if a is None or self._loading:
            return
        h = self.holdings
        out = []
        for i in range(h.rowCount()):
            def txt(c):
                return (h.item(i, c).text() if h.item(i, c) else "").strip()
            try:
                qty = float(txt(2).replace(",", "")) if txt(2) else 0.0
            except ValueError:
                qty = 0.0
            out.append(pacc.Holding(txt(0).upper(), txt(1), qty, (parse_money(txt(3)) or 0) if txt(3) else 0,
                                    (parse_money(txt(5)) or 0) if txt(5) else 0))
        a.holdings = out
        a.as_of = date.today().isoformat()
        pacc.save(self.pf, self.accts)
        self.fill(keep=a.id)
        if self.on_change:
            self.on_change()

    def _totals(self) -> None:
        inv = self.accts.investments if self.accts else []
        value = sum(a.market_value for a in inv)
        cost = sum(a.book_cost for a in inv)
        self.cards["value"].set(fmt_money(value), f"{len(inv)} account(s)")
        self.cards["cost"].set(fmt_money(cost), "")
        self.cards["gain"].set(fmt_money(value - cost), f"{(value - cost) / cost * 100:.1f}%" if cost else "",
                               NEGATIVE if value < cost else POSITIVE)
        by: dict[str, int] = {}
        for a in inv:
            by[a.type] = by.get(a.type, 0) + a.market_value
        top = sorted(by.items(), key=lambda kv: -kv[1])
        self.cards["by"].set(top[0][0] if top else "—", " · ".join(f"{k} {fmt_money(v)}" for k, v in top[:3]))

    def add_account(self, name: str = "New account", kind: str = "TFSA") -> None:
        from .. import personal_accounts as pacc

        a = pacc.InvestmentAccount(name, kind, as_of=date.today().isoformat(), id=self.accts.new_id())
        self.accts.investments.append(a)
        pacc.save(self.pf, self.accts)
        self.fill(keep=a.id)
        if self.on_change:
            self.on_change()

    def remove_account(self) -> None:
        from .. import personal_accounts as pacc

        a = self._cur()
        if a is None:
            return
        self.accts.investments = [x for x in self.accts.investments if x.id != a.id]
        pacc.save(self.pf, self.accts)
        self.fill()
        if self.on_change:
            self.on_change()

    def add_holding(self, symbol: str = "", quantity: float = 0.0, price: int = 0, book_cost: int = 0) -> None:
        from .. import personal_accounts as pacc

        a = self._cur()
        if a is None:
            self.add_account()
            a = self._cur()
        a.holdings.append(pacc.Holding(symbol.upper(), "", quantity, price, book_cost))
        pacc.save(self.pf, self.accts)
        self.fill(keep=a.id)
        if self.on_change:
            self.on_change()

    def remove_holding(self) -> None:
        a, r = self._cur(), self.holdings.currentRow()
        if a is None or not 0 <= r < len(a.holdings):
            return
        from .. import personal_accounts as pacc

        del a.holdings[r]
        pacc.save(self.pf, self.accts)
        self.fill(keep=a.id)
        if self.on_change:
            self.on_change()
