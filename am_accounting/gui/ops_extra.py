"""Rent Roll & Operations → Budget vs Actual and Unit Turnover."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import budget as bg
from .. import turnover as tv
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, confirm, error, info


def _cell(text, *, right=False, bold=False, colour=None):
    it = QTableWidgetItem(text)
    if right:
        it.setTextAlignment(ALIGN_RIGHT)
    if bold:
        f = it.font()
        f.setBold(True)
        it.setFont(f)
    if colour is not None:
        it.setForeground(colour)
    return it


def _parse_money(text: str) -> int:
    t = text.replace("$", "").replace(",", "").strip()
    if not t:
        return 0
    neg = t.startswith("(") and t.endswith(")")
    v = round(float(t.strip("()")) * 100)
    return -v if neg else v


# ---------------------------------------------------------------------------
# Budget vs actual
# ---------------------------------------------------------------------------

BVA_COLS = ["", "Budget (month)", "Actual (month)", "Variance", "Budget YTD", "Actual YTD", "Variance YTD",
            "Variance %", "Budget (year)", "Check"]


class BudgetTab(QWidget):
    def __init__(self, page):
        super().__init__()
        from .operations_tab import _table

        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Year:"))
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(date.today().year)
        self.year.valueChanged.connect(self._year_changed)
        row.addWidget(self.year)
        row.addWidget(QLabel("Through:"))
        self.month = QComboBox()
        for i, m in enumerate(bg.MONTHS, 1):
            self.month.addItem(m, i)
        self.month.setCurrentIndex(bg.default_month(self.year.value()) - 1)
        self.month.currentIndexChanged.connect(lambda *_: self.fill())
        row.addWidget(self.month)
        row.addWidget(QLabel("Flag when off by"))
        self.pct = QDoubleSpinBox()
        self.pct.setRange(0, 100)
        self.pct.setSuffix(" %")
        self.pct.setValue(bg.FLAG_PCT)
        self.minimum = QSpinBox()
        self.minimum.setRange(0, 10_000_000)
        self.minimum.setPrefix("and $")
        self.minimum.setSingleStep(100)
        self.minimum.setValue(bg.FLAG_MIN // 100)
        for w in (self.pct, self.minimum):
            w.valueChanged.connect(lambda *_: self.fill())
            row.addWidget(w)
        row.addStretch(1)
        for text, slot, tip in (
                ("Create from Projections", self.from_projections,
                 "The year's revenue and operating-expense projections, split evenly over the months"),
                ("Create from Last Year…", self.from_last_year, "Each account's months last year, grown by a %"),
                ("Edit Budget…", self.edit, "Change the budget month by month and pick the accounts for each line")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        self.head = QLabel()
        self.head.setTextFormat(Qt.TextFormat.RichText)
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.table = _table(BVA_COLS, None)
        lay.addWidget(self.table, 1)
        hint = QLabel("Variance = actual − budget. For revenue more is good, for expenses less is (green good, red "
                      "not). Each line is compared with the accounts picked for it in Edit Budget…, or the accounts "
                      "with the same name. Operating accounts no line takes show as “not in the budget”.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.cmp: bg.Comparison | None = None

    def _year_changed(self) -> None:
        self.month.blockSignals(True)
        self.month.setCurrentIndex(bg.default_month(self.year.value()) - 1)
        self.month.blockSignals(False)
        self.fill()

    def fill(self) -> None:
        p = self.page.perf()
        t = self.table
        t.setRowCount(0)
        if p is None:
            self.head.setText("No company picked.")
            return
        h = p.holding
        y, m = self.year.value(), self.month.currentData()
        have = bg.lines(self.page.pf, h.id, y)
        if not have:
            self.head.setText(f"No budget for {y} yet — <b>Create from Projections</b>, <b>Create from Last Year…</b> "
                              "or <b>Edit Budget…</b>. Actuals from the books are shown below.")
        c = self.cmp = bg.compare(self.page.pf, h, y, m)
        flagged = c.flagged(self.pct.value(), self.minimum.value() * 100)
        if have:
            noi = c.rows[-1]
            self.head.setText(
                f"<b>{h.name}</b> · {y} through {bg.MONTHS[m - 1]} · NOI year to date {fmt_money(noi.actual_ytd)} "
                f"vs budget {fmt_money(noi.budget_ytd)} ({'+' if noi.var_ytd >= 0 else '−'}"
                f"{fmt_money(abs(noi.var_ytd))}) · "
                + (f"<span style='color:{NEGATIVE.name()}'><b>{len(flagged)} line(s) off budget</b></span>"
                   if flagged else f"<span style='color:{POSITIVE.name()}'>no lines off budget</span>"))
        t.setHorizontalHeaderLabels([""] + [f"{x}" for x in BVA_COLS[1:]])
        t.setRowCount(len(c.rows) + 2)
        r = 0
        for kind, label in (("revenue", "Revenue"), ("opex", "Operating expenses")):
            t.setItem(r, 0, _cell(label.upper(), bold=True, colour=MUTED))
            r += 1
            for row in [x for x in c.rows if x.kind == kind]:
                self._row(r, row, flagged)
                r += 1
        self._row(r, c.rows[-1], flagged)
        t.setRowCount(r + 1)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def _row(self, r: int, x: bg.Row, flagged) -> None:
        good = POSITIVE if x.favourable else NEGATIVE

        def var(v):
            return _cell(("+" if v > 0 else "") + fmt_money(v) if v else "—", right=True, bold=x.bold,
                         colour=(good if v else None))
        name = ("    " if not x.bold else "") + x.item + (" (not in the budget)" if x.unbudgeted else "")
        cells = [_cell(name, bold=x.bold, colour=MUTED if x.unbudgeted else None),
                 _cell(fmt_money(x.budget_month), right=True, bold=x.bold),
                 _cell(fmt_money(x.actual_month), right=True, bold=x.bold), var(x.var_month),
                 _cell(fmt_money(x.budget_ytd), right=True, bold=x.bold),
                 _cell(fmt_money(x.actual_ytd), right=True, bold=x.bold), var(x.var_ytd),
                 _cell(f"{x.var_pct:+.1f}%" if x.var_pct is not None else "—", right=True, bold=x.bold,
                       colour=good if x.var_ytd else None),
                 _cell(fmt_money(x.budget_year), right=True, bold=x.bold),
                 _cell("⚠ Off budget" if x in flagged else "", colour=NEGATIVE)]
        if x.accounts:
            cells[0].setToolTip("Accounts: " + ", ".join(x.accounts))
        for c, it in enumerate(cells):
            self.table.setItem(r, c, it)

    def _save(self, rows) -> bool:
        p = self.page.perf()
        if p is None:
            return False
        y = self.year.value()
        if bg.lines(self.page.pf, p.holding.id, y) and not confirm(self, "Replace the budget",
                                                                   f"Replace the {y} budget?"):
            return False
        try:
            bg.save(self.page.pf, p.holding.id, y, rows)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return False
        self.fill()
        return True

    def from_projections(self) -> None:
        p = self.page.perf()
        if p is None:
            return
        rows = bg.from_projections(self.page.pf, p.holding.id, self.year.value())
        if not rows:
            info(self, "No projections", f"There are no projections for {self.year.value()} — enter them on "
                                         "Projections, or use Create from Last Year….")
            return
        self._save(rows)

    def from_last_year(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        p = self.page.perf()
        if p is None or not p.holding.company_path:
            info(self, "No books", "This company isn't linked to books.")
            return
        g, ok = QInputDialog.getDouble(self, "Create from last year", "Grow last year's figures by (%):", 3.0,
                                       -50, 100, 1)
        if not ok:
            return
        rows = bg.from_last_year(p.holding.company_path, self.year.value(), g)
        if not rows:
            info(self, "Nothing last year", f"The books have no operating income or expenses in "
                                            f"{self.year.value() - 1}.")
            return
        self._save(rows)

    def edit(self) -> None:
        p = self.page.perf()
        if p is None:
            return
        dlg = BudgetDialog(self.page.pf, p.holding, self.year.value(), self)
        if dlg.exec():
            self.fill()


class BudgetDialog(QDialog):
    """The year's budget, a line per row and a column per month; each line's accounts in the books."""

    def __init__(self, pf, holding, year: int, parent=None):
        super().__init__(parent)
        self.pf, self.holding, self.year = pf, holding, year
        self.setWindowTitle(f"Budget {year} – {holding.name}")
        self.resize(1250, 560)
        lay = QVBoxLayout(self)
        self.table = t = QTableWidget(0, 16)
        t.setHorizontalHeaderLabels(["Section", "Line", "Accounts in the books"] + bg.MONTHS + ["Year"])
        t.verticalHeader().setVisible(False)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.cellDoubleClicked.connect(self._cell_clicked)
        t.itemChanged.connect(self._changed)
        lay.addWidget(t, 1)
        self.accounts = bg.account_names(holding.company_path or "")
        row = QHBoxLayout()
        for text, slot in (("Add Revenue Line", lambda: self._add(bg.BudgetLine("revenue", ""))),
                           ("Add Expense Line", lambda: self._add(bg.BudgetLine("opex", ""))),
                           ("Remove Line", self._remove), ("Spread the Year Evenly", self._spread)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        hint = QLabel("Type amounts for each month, or a year's total in Year and click Spread the Year Evenly. "
                      "Double-click Accounts to pick which accounts in the books a line is compared with.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for ln in bg.lines(pf, holding.id, year):
            self._add(ln)
        t.resizeColumnsToContents()
        t.setColumnWidth(1, 220)
        t.setColumnWidth(2, 220)

    def _add(self, ln: bg.BudgetLine) -> None:
        t = self.table
        t.blockSignals(True)
        r = t.rowCount()
        t.insertRow(r)
        kind = QComboBox()
        for k, label in bg.KINDS.items():
            kind.addItem(label, k)
        kind.setCurrentIndex(kind.findData(ln.kind))
        t.setCellWidget(r, 0, kind)
        t.setItem(r, 1, QTableWidgetItem(ln.item))
        acc = QTableWidgetItem(", ".join(ln.accounts) or "(same name)")
        acc.setFlags(acc.flags() & ~Qt.ItemFlag.ItemIsEditable)
        acc.setData(Qt.ItemDataRole.UserRole, list(ln.accounts))
        t.setItem(r, 2, acc)
        for m in range(12):
            t.setItem(r, 3 + m, _cell(f"{ln.months[m] / 100:,.2f}", right=True))
        t.setItem(r, 15, _cell(f"{ln.total / 100:,.2f}", right=True))
        t.blockSignals(False)

    def _changed(self, it: QTableWidgetItem) -> None:
        r, c = it.row(), it.column()
        if 3 <= c < 15:
            self.table.blockSignals(True)
            try:
                tot = sum(_parse_money(self.table.item(r, 3 + m).text()) for m in range(12))
                self.table.item(r, 15).setText(f"{tot / 100:,.2f}")
            except ValueError:
                pass
            self.table.blockSignals(False)

    def _spread(self) -> None:
        rows = {i.row() for i in self.table.selectedIndexes()} or set(range(self.table.rowCount()))
        self.table.blockSignals(True)
        for r in rows:
            try:
                months = bg.split(_parse_money(self.table.item(r, 15).text()))
            except ValueError:
                continue
            for m in range(12):
                self.table.item(r, 3 + m).setText(f"{months[m] / 100:,.2f}")
        self.table.blockSignals(False)

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def _cell_clicked(self, r: int, c: int) -> None:
        if c != 2:
            return
        it = self.table.item(r, 2)
        dlg = QDialog(self)
        dlg.setWindowTitle("Accounts for this line")
        dl = QVBoxLayout(dlg)
        dl.addWidget(QLabel("Compare this line with these accounts (none ticked: the accounts with the same name):"))
        lst = QListWidget()
        have = set(it.data(Qt.ItemDataRole.UserRole) or [])
        for name, typ in self.accounts:
            li = QListWidgetItem(f"{name}  ({'income' if typ == 'income' else 'expense'})")
            li.setData(Qt.ItemDataRole.UserRole, name)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked if name in have else Qt.CheckState.Unchecked)
            lst.addItem(li)
        dl.addWidget(lst, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        dl.addWidget(bb)
        dlg.resize(420, 480)
        if dlg.exec():
            names = [lst.item(i).data(Qt.ItemDataRole.UserRole) for i in range(lst.count())
                     if lst.item(i).checkState() == Qt.CheckState.Checked]
            it.setData(Qt.ItemDataRole.UserRole, names)
            it.setText(", ".join(names) or "(same name)")

    def rows(self) -> list[bg.BudgetLine]:
        t = self.table
        out = []
        for r in range(t.rowCount()):
            months = [_parse_money(t.item(r, 3 + m).text()) for m in range(12)]
            out.append(bg.BudgetLine(t.cellWidget(r, 0).currentData(), t.item(r, 1).text().strip(), months,
                                     list(t.item(r, 2).data(Qt.ItemDataRole.UserRole) or [])))
        return out

    def save(self) -> None:
        try:
            bg.save(self.pf, self.holding.id, self.year, self.rows())
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


# ---------------------------------------------------------------------------
# Unit turnover
# ---------------------------------------------------------------------------

TURN_COLS = ["Unit", "Moved out", "Old tenant", "Old rent", "Moved in", "New tenant", "New rent", "Rent change",
             "Days empty", "Rent lost", "Cost to turn over", "Notes"]


class TurnoverTab(QWidget):
    def __init__(self, page):
        super().__init__()
        from .asset_mgmt import Card
        from .operations_tab import _table

        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        cards = QHBoxLayout()
        self.cards = {}
        for k, title in (("turns", "Turnovers (12 months)"), ("vacant", "Empty now"), ("days", "Average days empty"),
                         ("cost", "Average cost to turn over"), ("lift", "Average rent change"),
                         ("lost", "Rent lost (12 months)")):
            c = Card(title, small=True)
            self.cards[k] = c
            cards.addWidget(c)
        lay.addLayout(cards)
        row = QHBoxLayout()
        self.info = QLabel()
        self.info.setObjectName("hint")
        self.info.setWordWrap(True)
        row.addWidget(self.info, 1)
        for text, slot in (("Add…", self.add), ("Edit…", self.edit), ("Delete", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        lay.addLayout(row)
        self.table = _table(TURN_COLS, 11)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        lay.addWidget(self.table, 1)
        self._rows: list[tv.Turn] = []

    def fill(self) -> None:
        p = self.page.perf()
        t = self.table
        t.setRowCount(0)
        for c in self.cards.values():
            c.set("—")
        if p is None:
            return
        as_of = self.page.view._as_of_date()
        rows = self._rows = tv.with_costs(self.page.pf, p.holding, tv.turns(self.page.pf, p.holding.id), as_of)
        s = tv.summary(rows, as_of)
        self.cards["turns"].set(str(s.turns_12), "move-outs and move-ins")
        self.cards["vacant"].set(str(s.vacant_now), "units waiting for a tenant")
        self.cards["days"].set(f"{s.avg_days:.0f}" if s.avg_days is not None else "—", "move-out to move-in")
        self.cards["cost"].set(fmt_money(s.avg_cost) if s.avg_cost is not None else "—", "capex + other")
        self.cards["lift"].set(f"{s.avg_lift_pct:+.1f}%" if s.avg_lift_pct is not None else "—",
                               "new rent vs old", POSITIVE if (s.avg_lift_pct or 0) > 0 else None)
        self.cards["lost"].set(fmt_money(s.lost_rent_12), "at the old rent while empty",
                               NEGATIVE if s.lost_rent_12 else None)
        t.setRowCount(len(rows))
        for r, x in enumerate(rows):
            ch = x.rent_change
            vals = [x.unit, x.move_out or "—", x.prev_tenant, fmt_money(x.prev_rent) if x.prev_rent else "",
                    x.move_in or "still empty", x.new_tenant, fmt_money(x.new_rent) if x.new_rent else "",
                    (f"{'+' if ch >= 0 else ''}{fmt_money(ch)} ({x.rent_change_pct:+.1f}%)" if ch is not None else ""),
                    str(x.days_vacant(as_of)) if x.days_vacant(as_of) is not None else "",
                    fmt_money(x.lost_rent(as_of)) if x.lost_rent(as_of) else "",
                    fmt_money(x.cost) if x.cost else "", x.notes]
            for c, v in enumerate(vals):
                colour = None
                if c == 7 and ch:
                    colour = POSITIVE if ch > 0 else NEGATIVE
                if c == 4 and x.open:
                    colour = NEGATIVE
                it = _cell(v, right=c in (3, 6, 7, 8, 9, 10), colour=colour)
                if c == 10 and x.cost:
                    it.setToolTip(f"From the unit's in-suite renovation line: {fmt_money(x.capex_cost)} · "
                                  f"other: {fmt_money(x.extra_cost)}")
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        self.info.setText("Recorded automatically when a unit on the rent roll goes vacant or gets a new tenant; "
                          "add or change one by hand here. Cost to turn over is the unit's in-suite renovation spending "
                          "(CapEx) between move-out and move-in, plus other costs entered here.")

    def _selected(self) -> tv.Turn | None:
        rows = self.table.selectionModel().selectedRows()
        return self._rows[rows[0].row()] if rows and rows[0].row() < len(self._rows) else None

    def _dialog(self, t: tv.Turn) -> None:
        p = self.page.perf()
        if p is None:
            return
        if TurnDialog(self.page.pf, p.holding, t, [u.unit for u in self.page._units], self).exec():
            self.fill()

    def add(self) -> None:
        self._dialog(tv.Turn("", date.today().isoformat()))

    def edit(self) -> None:
        t = self._selected()
        if t:
            self._dialog(t)

    def delete(self) -> None:
        t = self._selected()
        if t and confirm(self, "Delete", f"Delete this turnover of {t.unit}?"):
            tv.delete(self.page.pf, t.id)
            self.fill()


def _date_edit(iso: str | None, blank: bool) -> QDateEdit:
    from .deals_tab import _date_box

    if blank:
        return _date_box(iso)
    w = QDateEdit(QDate.currentDate())
    w.setCalendarPopup(True)
    w.setDisplayFormat("MMM d, yyyy")
    if iso:
        d = date.fromisoformat(iso)
        w.setDate(QDate(d.year, d.month, d.day))
    return w


class TurnDialog(QDialog):
    def __init__(self, pf, holding, t: tv.Turn, units: list[str], parent=None):
        super().__init__(parent)
        from .asset_mgmt import _money_box

        self.pf, self.holding, self.t = pf, holding, t
        self.setWindowTitle(f"Unit turnover – {holding.name}")
        form = QFormLayout(self)
        self.unit = QComboBox()
        self.unit.setEditable(True)
        self.unit.addItems(units)
        self.unit.setCurrentText(t.unit)
        self.move_out = _date_edit(t.move_out, True)
        self.prev_tenant = QLineEdit(t.prev_tenant)
        self.prev_rent = _money_box()
        self.prev_rent.setValue(t.prev_rent / 100)
        self.move_in = _date_edit(t.move_in, True)
        self.new_tenant = QLineEdit(t.new_tenant)
        self.new_rent = _money_box()
        self.new_rent.setValue(t.new_rent / 100)
        self.extra = _money_box()
        self.extra.setValue(t.extra_cost / 100)
        self.extra.setToolTip("Turnover costs not on the CapEx schedule (cleaning, painting paid as repairs, "
                              "commissions …)")
        self.notes = QLineEdit(t.notes)
        for label, w in (("Unit:", self.unit), ("Moved out:", self.move_out), ("Old tenant:", self.prev_tenant),
                         ("Old rent / month:", self.prev_rent), ("Moved in (blank = still empty):", self.move_in),
                         ("New tenant:", self.new_tenant), ("New rent / month:", self.new_rent),
                         ("Other cost to turn over:", self.extra), ("Notes:", self.notes)):
            form.addRow(label, w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        from .deals_tab import _date_of

        t = self.t
        t.unit, t.prev_tenant, t.new_tenant = self.unit.currentText(), self.prev_tenant.text(), self.new_tenant.text()
        t.move_out, t.move_in = _date_of(self.move_out), _date_of(self.move_in)
        t.prev_rent, t.new_rent = round(self.prev_rent.value() * 100), round(self.new_rent.value() * 100)
        t.extra_cost, t.notes = round(self.extra.value() * 100), self.notes.text()
        try:
            tv.save(self.pf, self.holding.id, t)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()
