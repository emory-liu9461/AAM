"""Asset Management → Operations (rent roll, unit mix, monthly operating statement) and CapEx (budget vs cost to
date from the books) for one company."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import capex as cx
from .. import operations as op
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, confirm, error, info


def _money_box() -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, 1e12)
    s.setDecimals(2)
    s.setPrefix("$ ")
    s.setGroupSeparatorShown(True)
    return s


def _table(cols: list[str], stretch: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setAlternatingRowColors(True)
    if stretch is not None:
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


def _cell(text: str, *, right: bool = False, bold: bool = False, colour=None) -> QTableWidgetItem:
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


def _qd(iso: str | None) -> QDate:
    d = date.fromisoformat(iso) if iso else date.today()
    return QDate(d.year, d.month, d.day)


def fill_statement(t: QTableWidget, stmt: op.Statement) -> None:
    """Months across (projected ones marked and greyed), categories down, then last-12 and next-12 totals."""
    n = len(stmt.months)
    heads = [m.strftime("%b %y") + ("\nproj." if p else "") for m, p in zip(stmt.months, stmt.projected)]
    t.clear()
    t.setColumnCount(n + 3)
    t.setHorizontalHeaderLabels([""] + heads + ["Last 12\nactual", "Next 12\nprojected"])
    t.setRowCount(len(stmt.lines))
    balance_rows = ("Opening cash", "Ending cash")
    for r, ln in enumerate(stmt.lines):
        bold = ln.kind in ("total", "head")
        t.setItem(r, 0, _cell(ln.label, bold=bold))
        if ln.kind == "gap":
            t.setRowHeight(r, 8)
            continue
        if ln.kind == "head":
            continue
        if ln.kind == "ratio":
            vals = [f"{x:.2f}x" if x is not None else "" for x in ln.ratios]
        else:
            vals = [fmt_money(v) if v is not None else "" for v in ln.values]
        for i, v in enumerate(vals):
            colour = MUTED if stmt.projected[i] else None
            if ln.kind != "ratio" and ln.values[i] is not None and ln.values[i] < 0:
                colour = NEGATIVE
            t.setItem(r, i + 1, _cell(v, right=True, bold=bold, colour=colour))
        if ln.kind != "ratio" and ln.label not in balance_rows:
            act = [v for v, p in zip(ln.values, stmt.projected) if not p and v is not None]
            proj = [v for v, p in zip(ln.values, stmt.projected) if p and v is not None]
            t.setItem(r, n + 1, _cell(fmt_money(sum(act[-12:])) if act else "", right=True, bold=True))
            t.setItem(r, n + 2, _cell(fmt_money(sum(proj[:12])) if proj else "", right=True, bold=True,
                                      colour=MUTED))
    t.resizeColumnsToContents()
    t.setColumnWidth(0, max(t.columnWidth(0), 200))


class _CompanyPage(QWidget):
    """A page showing one company of the portfolio (picked here, or by Viewing)."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.pick = QComboBox()
        self.pick.setMinimumWidth(260)
        self.pick.currentIndexChanged.connect(lambda *_: self.fill())

    @property
    def pf(self):
        return self.view.pf

    def perf(self):
        hid = self.pick.currentData()
        return next((p for p in (self.view.summary.performances if self.view.summary else [])
                     if p.holding.id == hid), None)

    def fill_pick(self, perfs, want_id=None) -> None:
        want = want_id if want_id is not None else self.pick.currentData()
        self.pick.blockSignals(True)
        self.pick.clear()
        for p in perfs:
            self.pick.addItem(p.holding.name, p.holding.id)
        i = self.pick.findData(want)
        self.pick.setCurrentIndex(i if i >= 0 else 0)
        self.pick.blockSignals(False)
        self.fill()

    def fill(self) -> None:  # pragma: no cover - overridden
        pass


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------


class UnitDialog(QDialog):
    def __init__(self, pf, holding, unit: op.Unit | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        self.u = unit or op.Unit("")
        u = self.u
        self.setWindowTitle(f"Unit – {holding.name}")
        self.resize(480, 0)
        form = QFormLayout(self)
        self.unit = QLineEdit(u.unit)
        self.tenant = QLineEdit(u.tenant)
        self.unit_type = QComboBox()
        self.unit_type.setEditable(True)
        self.unit_type.addItems(["Bachelor", "1 Bedroom", "2 Bedroom", "3 Bedroom", "Commercial"])
        self.unit_type.setCurrentText(u.unit_type)
        self.size = QDoubleSpinBox()
        self.size.setRange(0, 1e6)
        self.size.setDecimals(0)
        self.size.setSuffix(" sq ft")
        self.size.setValue(u.size_sqft or 0)
        self.status = QComboBox()
        self.status.setEditable(True)
        self.status.addItems(op.STATUSES)
        self.status.setCurrentText(u.status)
        self.rent, self.parking, self.locker, self.market = (_money_box() for _ in range(4))
        for box, cents in ((self.rent, u.rent_cents), (self.parking, u.parking_cents), (self.locker, u.locker_cents),
                           (self.market, u.market_cents)):
            box.setValue(cents / 100)
        self.move_in = QDateEdit(_qd(u.move_in))
        self.move_in.setCalendarPopup(True)
        self.move_in.setDisplayFormat("yyyy-MM-dd")
        self.lease_end = QLineEdit(u.lease_end)
        self.lease_end.setPlaceholderText("YYYY-MM-DD, or MTM for month to month")
        self.notes = QLineEdit(u.notes)
        for label, w in (("Unit:", self.unit), ("Tenant:", self.tenant), ("Unit type:", self.unit_type),
                         ("Size:", self.size), ("Status:", self.status), ("Base rent / month:", self.rent),
                         ("Parking / month:", self.parking), ("Locker / month:", self.locker),
                         ("Market rent / month:", self.market), ("Move-in date:", self.move_in),
                         ("Lease end:", self.lease_end), ("Notes:", self.notes)):
            form.addRow(label, w)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        u = self.u
        u.unit, u.tenant, u.unit_type = self.unit.text(), self.tenant.text(), self.unit_type.currentText()
        u.size_sqft = self.size.value() or None
        u.status = self.status.currentText()
        u.rent_cents, u.parking_cents = round(self.rent.value() * 100), round(self.parking.value() * 100)
        u.locker_cents, u.market_cents = round(self.locker.value() * 100), round(self.market.value() * 100)
        u.move_in = self.move_in.date().toString("yyyy-MM-dd")
        end = self.lease_end.text().strip()
        if end and end.upper() not in ("MTM", "M2M"):
            try:
                date.fromisoformat(end)
            except ValueError:
                error(self, "Couldn't save", "Lease end is a date (YYYY-MM-DD) or MTM")
                return
        u.lease_end, u.notes = end.upper() if end.upper() in ("MTM", "M2M") else end, self.notes.text()
        try:
            op.save_unit(self.pf, self.holding.id, u)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


CARD_KEYS = [("units", "Units"), ("occupancy", "Occupancy"), ("in_place", "In-place rent"),
             ("avg", "Average rent"), ("ltl", "Loss to lease"), ("leases", "Leases"), ("noi", "NOI"),
             ("dscr", "DSCR")]
ROLL_COLS = ["Unit", "Tenant", "Type", "Size", "Status", "Rent", "Parking", "Locker", "Market rent", "Move-in",
             "Lease end", "Notes"]


class OperationsPage(_CompanyPage):
    def __init__(self, view):
        super().__init__(view)
        from .asset_mgmt import Card

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        row.addWidget(self.pick)
        row.addSpacing(12)
        row.addWidget(QLabel("Rent increase:"))
        self.increase = QDoubleSpinBox()
        self.increase.setRange(0, 50)
        self.increase.setDecimals(2)
        self.increase.setSuffix(" % / yr")
        self.increase.setToolTip("Each lease goes up by this on its move-in anniversary (e.g. the rent increase "
                                 "guideline)")
        row.addWidget(self.increase)
        row.addWidget(QLabel("Vacancy:"))
        self.vacancy = QDoubleSpinBox()
        self.vacancy.setRange(0, 100)
        self.vacancy.setDecimals(1)
        self.vacancy.setSuffix(" %")
        self.vacancy.setToolTip("Allowance taken off projected rent")
        row.addWidget(self.vacancy)
        row.addWidget(QLabel("Rents as of:"))
        self.as_of = QDateEdit(QDate.currentDate())
        self.as_of.setCalendarPopup(True)
        self.as_of.setDisplayFormat("yyyy-MM-dd")
        self.as_of.setToolTip("The date the rent roll's rents are current at; increases apply after it")
        row.addWidget(self.as_of)
        for w in (self.increase, self.vacancy):
            w.editingFinished.connect(self._save_settings)
        self.as_of.dateChanged.connect(lambda *_: self._save_settings())
        row.addStretch(1)
        imp = QPushButton("Import Rent Roll…")
        imp.setToolTip("Read a rent-roll table (Unit, Tenant, Type, Size, Status, Rent, Market Rent, Move-in, "
                       "Lease End…) from an Excel workbook or CSV")
        imp.clicked.connect(self.import_roll)
        row.addWidget(imp)
        lay.addLayout(row)
        cards = QHBoxLayout()
        self.cards = {}
        for k, title in CARD_KEYS:
            c = Card(title, small=True)
            self.cards[k] = c
            cards.addWidget(c)
        lay.addLayout(cards)
        self.sub = QTabWidget()
        lay.addWidget(self.sub, 1)
        # monthly operating statement
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(0, 4, 0, 0)
        self.stmt_note = QLabel()
        self.stmt_note.setObjectName("hint")
        self.stmt_note.setWordWrap(True)
        sl.addWidget(self.stmt_note)
        self.stmt = _table([""])
        self.stmt.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        sl.addWidget(self.stmt, 1)
        self.sub.addTab(sw, "Monthly Operating Statement")
        # rent roll
        rw = QWidget()
        rl = QVBoxLayout(rw)
        rl.setContentsMargins(0, 4, 0, 0)
        rb = QHBoxLayout()
        self.roll_info = QLabel()
        rb.addWidget(self.roll_info, 1)
        for text, slot in (("Add Unit…", self.add_unit), ("Edit…", self.edit_unit), ("Delete", self.delete_unit)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            rb.addWidget(b)
        rl.addLayout(rb)
        self.roll = _table(ROLL_COLS, 11)
        self.roll.doubleClicked.connect(lambda *_: self.edit_unit())
        rl.addWidget(self.roll, 1)
        self.sub.addTab(rw, "Rent Roll")
        # unit mix
        self.mix = _table(["Unit type", "Status", "Units", "Occupied", "Average rent", "Average market rent",
                           "Monthly rent", "Gap to market"], None)
        self.sub.addTab(self.mix, "Unit Mix")
        self._units: list[op.Unit] = []

    def _settings_widgets(self, st: op.Settings) -> None:
        for w in (self.increase, self.vacancy, self.as_of):
            w.blockSignals(True)
        self.increase.setValue(st.rent_increase_pct)
        self.vacancy.setValue(st.vacancy_pct)
        self.as_of.setDate(_qd(st.as_of))
        for w in (self.increase, self.vacancy, self.as_of):
            w.blockSignals(False)

    def _save_settings(self) -> None:
        p = self.perf()
        if not p:
            return
        try:
            op.save_settings(self.pf, p.holding.id, op.Settings(self.increase.value(), self.vacancy.value(),
                                                                self.as_of.date().toString("yyyy-MM-dd")))
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.fill()

    def fill(self) -> None:
        p = self.perf()
        for c in self.cards.values():
            c.set("—")
        for t in (self.stmt, self.roll, self.mix):
            t.setRowCount(0)
        if p is None:
            self.stmt_note.setText("No operating companies in this view.")
            return
        h = p.holding
        st = op.settings(self.pf, h.id)
        self._settings_widgets(st)
        rows = self._units = op.units(self.pf, h.id)
        as_of = self.view._as_of_date()
        s = op.summary(rows, as_of)
        stmt = op.monthly_statement(self.pf, p, as_of)
        self._fill_cards(s, stmt)
        self._fill_statement(stmt)
        self._fill_roll(rows, s)
        self._fill_mix(rows)

    def _fill_cards(self, s: op.RentRollSummary, stmt: op.Statement) -> None:
        c = self.cards
        if s.units:
            c["units"].set(str(s.units), f"{s.occupied} occupied · {s.units - s.occupied} vacant")
            c["occupancy"].set(f"{s.occupancy * 100:.1f}%", f"{s.sqft:,.0f} sq ft" if s.sqft else "physical")
            c["in_place"].set(fmt_money(s.in_place), f"a month · {fmt_money(s.in_place * 12)} a year")
            c["avg"].set(fmt_money(s.avg_rent) if s.avg_rent is not None else "—",
                         f"market {fmt_money(s.avg_market)}" if s.avg_market else "no market rents")
            ltl = s.loss_to_lease
            c["ltl"].set(fmt_money(ltl), (f"{ltl / s.market_occupied * 100:.0f}% below market a month"
                                          if s.market_occupied else "a month"), NEGATIVE if ltl else None)
            c["leases"].set(f"{s.mtm} MTM", f"{s.expiring_90} expiring in 90 days")
        noi = op.t12(stmt, "Net operating income")
        ds = op.t12(stmt, "Debt service")
        c["noi"].set(fmt_money(noi), "last 12 months (books)", POSITIVE if noi > 0 else NEGATIVE if noi < 0 else None)
        c["dscr"].set(f"{noi / ds:.2f}x" if ds else "—", "NOI ÷ debt service, last 12 months")

    def _fill_statement(self, stmt: op.Statement) -> None:
        fill_statement(self.stmt, stmt)
        self.stmt_note.setText(stmt.note)

    def _fill_roll(self, rows: list[op.Unit], s: op.RentRollSummary) -> None:
        t = self.roll
        t.setRowCount(len(rows) + (1 if rows else 0))
        for r, u in enumerate(rows):
            vals = [u.unit, u.tenant, u.unit_type, f"{u.size_sqft:,.0f}" if u.size_sqft else "", u.status,
                    fmt_money(u.rent_cents), fmt_money(u.parking_cents) if u.parking_cents else "",
                    fmt_money(u.locker_cents) if u.locker_cents else "",
                    fmt_money(u.market_cents) if u.market_cents else "", u.move_in or "", u.lease_end, u.notes]
            for c, v in enumerate(vals):
                t.setItem(r, c, _cell(v, right=c in (3, 5, 6, 7, 8), colour=None if u.occupied else MUTED))
        if rows:
            r = len(rows)
            for c, v in ((0, "Total"), (3, f"{s.sqft:,.0f}" if s.sqft else ""), (5, fmt_money(s.in_place)),
                         (8, fmt_money(s.market))):
                t.setItem(r, c, _cell(v, right=c > 0, bold=True))
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        self.roll_info.setText(f"{s.units} units · {s.occupied} occupied · in-place {fmt_money(s.in_place)} a month"
                               if rows else "No rent roll yet — Import Rent Roll… or Add Unit….")

    def _fill_mix(self, rows: list[op.Unit]) -> None:
        mix = op.unit_mix(rows)
        t = self.mix
        t.setRowCount(len(mix))
        for r, g in enumerate(mix):
            gap = (g.avg_market - g.avg_rent) if g.avg_market and g.avg_rent is not None else None
            vals = [g.unit_type, g.status, str(g.units), str(g.occupied),
                    fmt_money(g.avg_rent) if g.avg_rent is not None else "—",
                    fmt_money(g.avg_market) if g.avg_market else "—", fmt_money(g.rent),
                    fmt_money(gap) if gap is not None else "—"]
            for c, v in enumerate(vals):
                t.setItem(r, c, _cell(v, right=c >= 2))
        t.resizeColumnsToContents()

    def _selected_unit(self) -> op.Unit | None:
        r = self.roll.currentRow()
        return self._units[r] if 0 <= r < len(self._units) else None

    def add_unit(self) -> None:
        p = self.perf()
        if p and UnitDialog(self.pf, p.holding, parent=self).exec():
            self.fill()

    def edit_unit(self) -> None:
        p, u = self.perf(), self._selected_unit()
        if p and u and UnitDialog(self.pf, p.holding, u, self).exec():
            self.fill()

    def delete_unit(self) -> None:
        u = self._selected_unit()
        if u and confirm(self, "Delete unit", f"Delete unit {u.unit} from the rent roll?"):
            op.delete_unit(self.pf, u.id)
            self.fill()

    def import_roll(self) -> None:
        p = self.perf()
        if not p:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import rent roll", "", "Rent roll (*.xlsx *.xlsm *.csv)")
        if not path:
            return
        self.import_from(path)

    def import_from(self, path: str) -> int:
        p = self.perf()
        try:
            rows, where = op.read_rent_roll(path)
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't read the rent roll", str(e))
            return 0
        existing = op.units(self.pf, p.holding.id)
        if existing and not confirm(self, "Replace rent roll",
                                    f"Replace the {len(existing)} units on file with the {len(rows)} units in "
                                    f"“{where}”?"):
            return 0
        n = op.replace_units(self.pf, p.holding.id, rows)
        st = op.settings(self.pf, p.holding.id)
        st.as_of = st.as_of or date.today().isoformat()
        op.save_settings(self.pf, p.holding.id, st)
        self.fill()
        self.sub.setCurrentIndex(1)
        info(self, "Rent roll imported", f"{n} units read from “{where}”.")
        return n


# ---------------------------------------------------------------------------
# CapEx
# ---------------------------------------------------------------------------


class CapexLineDialog(QDialog):
    def __init__(self, pf, holding, line: cx.CapexLine | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        self.ln = line or cx.CapexLine("")
        ln = self.ln
        self.setWindowTitle(f"CapEx line – {holding.name}")
        self.resize(520, 0)
        form = QFormLayout(self)
        self.grp = QComboBox()
        self.grp.setEditable(True)
        self.grp.addItems(cx.GROUPS)
        self.grp.setCurrentText(ln.grp)
        self.item = QLineEdit(ln.item)
        self.item.setPlaceholderText("e.g. Roofing, In-suite renovations, Permits")
        self.budget = _money_box()
        self.budget.setValue(ln.budget_cents / 100)
        self.account = QComboBox()
        self.account.addItem("(not linked — enter cost to date by hand)", None)
        for acct_id, label in cx.book_accounts(holding.company_path):
            self.account.addItem(label, acct_id)
        self.account.setCurrentIndex(max(0, self.account.findData(ln.account_id)))
        self.manual = _money_box()
        self.manual.setValue(ln.manual_cents / 100)
        self.manual.setToolTip("Cost to date that isn't in this company's books (paid by another entity, etc.)")
        self.notes = QLineEdit(ln.notes)
        form.addRow("Group:", self.grp)
        form.addRow("Cost item:", self.item)
        form.addRow("Budget:", self.budget)
        form.addRow("Account in the books:", self.account)
        form.addRow("Cost to date not in the books:", self.manual)
        form.addRow("Notes:", self.notes)
        hint = QLabel("Linked to an account, the cost to date and monthly spending come from the company's books — "
                      "categorize the contractor payments to that account.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setMinimumHeight(hint.fontMetrics().lineSpacing() * 2)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        ln = self.ln
        ln.grp, ln.item = self.grp.currentText(), self.item.text()
        ln.budget_cents, ln.manual_cents = round(self.budget.value() * 100), round(self.manual.value() * 100)
        acct = self.account.currentData()
        ln.account_id = acct if isinstance(acct, int) else None
        ln.notes = self.notes.text()
        try:
            cx.save_line(self.pf, self.holding.id, ln)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


CAPEX_COLS = ["Cost item", "Budget", "To date (books)", "To date (by hand)", "Cost to date", "Cost to complete",
              "% complete", "Account in the books"]


class CapexPage(_CompanyPage):
    def __init__(self, view):
        super().__init__(view)
        from .asset_mgmt import Card

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        row.addWidget(self.pick)
        row.addStretch(1)
        for text, slot in (("Add Line…", self.add_line), ("Edit…", self.edit_line), ("Delete", self.delete_line),
                           ("Start from Template", self.template),
                           ("Create Account in the Books", self.create_account)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            if text.startswith("Add"):
                b.setObjectName("primary")
            row.addWidget(b)
        lay.addLayout(row)
        cards = QHBoxLayout()
        self.cards = {}
        for k, title in (("budget", "Total budget"), ("to_date", "Cost to date"), ("to_complete", "Cost to complete"),
                         ("pct", "Complete")):
            c = Card(title, small=True)
            self.cards[k] = c
            cards.addWidget(c)
        lay.addLayout(cards)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = _table(CAPEX_COLS, 7)
        self.table.doubleClicked.connect(lambda *_: self.edit_line())
        split.addWidget(self.table)
        mw = QWidget()
        ml = QVBoxLayout(mw)
        ml.setContentsMargins(0, 4, 0, 0)
        ml.addWidget(QLabel("<b>Monthly capex spending</b> (recorded in the books, last 12 months)"))
        self.months = _table([""])
        self.months.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        ml.addWidget(self.months, 1)
        split.addWidget(mw)
        split.setSizes([420, 220])
        lay.addWidget(split, 1)
        self._rows: list = []   # table row -> CapexLine | None

    def fill(self) -> None:
        p = self.perf()
        t = self.table
        t.setRowCount(0)
        self.months.setRowCount(0)
        for c in self.cards.values():
            c.set("—")
        self._rows = []
        if p is None:
            return
        as_of = self.view._as_of_date()
        lines = cx.lines(self.pf, p.holding, as_of)
        tot = cx.totals(lines)
        self.cards["budget"].set(fmt_money(tot.budget), f"{len(lines)} line(s)")
        self.cards["to_date"].set(fmt_money(tot.to_date), "books + entered by hand")
        self.cards["to_complete"].set(fmt_money(tot.to_complete), "budget − cost to date",
                                      NEGATIVE if tot.to_complete < 0 else None)
        self.cards["pct"].set(f"{tot.to_date / tot.budget * 100:.0f}%" if tot.budget else "—", "of the budget spent")
        order = list(dict.fromkeys([g for g in cx.GROUPS if any(ln.grp == g for ln in lines)]
                                   + [ln.grp for ln in lines]))
        for g in order:
            members = [ln for ln in lines if ln.grp == g]
            self._add(t, [g] + [""] * 7, head=True)
            for ln in members:
                self._add(t, ["    " + ln.item, fmt_money(ln.budget_cents),
                              fmt_money(ln.book_cents) if ln.account_id is not None else "—",
                              fmt_money(ln.manual_cents) if ln.manual_cents else "",
                              fmt_money(ln.to_date), fmt_money(ln.to_complete),
                              f"{ln.pct_complete * 100:.0f}%" if ln.pct_complete is not None else "",
                              ln.account_name or "not linked"], line=ln)
            b, d, c_ = tot.by_group[g]
            self._add(t, [f"Total {g}", fmt_money(b), "", "", fmt_money(d), fmt_money(c_),
                          f"{d / b * 100:.0f}%" if b else "", ""], bold=True)
        if lines:
            self._add(t, ["Total project cost", fmt_money(tot.budget), "", "", fmt_money(tot.to_date),
                          fmt_money(tot.to_complete), f"{tot.to_date / tot.budget * 100:.0f}%" if tot.budget else "",
                          ""], bold=True)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        self._fill_months(lines, as_of)

    def _add(self, t, vals, *, head=False, bold=False, line=None) -> None:
        r = t.rowCount()
        t.insertRow(r)
        for c, v in enumerate(vals):
            colour = None
            if line is not None and c == 5 and line.to_complete < 0:
                colour = NEGATIVE
            if line is not None and c == 7 and line.account_id is None:
                colour = MUTED
            t.setItem(r, c, _cell(v, right=0 < c < 7, bold=head or bold, colour=colour))
        if head:
            t.setSpan(r, 0, 1, len(vals))
        self._rows.append(line)

    def _fill_months(self, lines, as_of: date) -> None:
        months = op._month_starts(as_of, 12, 0)
        keys = [m.strftime("%Y-%m") for m in months]
        active = [ln for ln in lines if ln.monthly and any(ln.monthly.get(k) for k in keys)]
        t = self.months
        t.clear()
        t.setColumnCount(len(keys) + 2)
        t.setHorizontalHeaderLabels(["", *[m.strftime("%b %y") for m in months], "Total"])
        t.setRowCount(len(active) + 1)
        for r, ln in enumerate(active):
            t.setItem(r, 0, _cell(ln.item))
            vals = [ln.monthly.get(k, 0) for k in keys]
            for c, v in enumerate(vals, start=1):
                t.setItem(r, c, _cell(fmt_money(v) if v else "", right=True))
            t.setItem(r, len(keys) + 1, _cell(fmt_money(sum(vals)), right=True, bold=True))
        tot = cx.monthly_outflows(lines, keys)
        r = len(active)
        t.setItem(r, 0, _cell("Total", bold=True))
        for c, k in enumerate(keys, start=1):
            t.setItem(r, c, _cell(fmt_money(tot[k]) if tot[k] else "", right=True, bold=True))
        t.setItem(r, len(keys) + 1, _cell(fmt_money(sum(tot.values())), right=True, bold=True))
        t.resizeColumnsToContents()
        t.setColumnWidth(0, max(t.columnWidth(0), 180))

    def _selected(self):
        r = self.table.currentRow()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def add_line(self) -> None:
        p = self.perf()
        if p and CapexLineDialog(self.pf, p.holding, parent=self).exec():
            self.fill()

    def edit_line(self) -> None:
        p, ln = self.perf(), self._selected()
        if p and ln and CapexLineDialog(self.pf, p.holding, ln, self).exec():
            self.fill()

    def delete_line(self) -> None:
        ln = self._selected()
        if ln and confirm(self, "Delete line", f"Delete “{ln.item}” from the capex schedule? (The books aren't "
                                               "touched.)"):
            cx.delete_line(self.pf, ln.id)
            self.fill()

    def template(self) -> None:
        p = self.perf()
        if p and confirm(self, "Start from template", "Add the usual cost items (hard costs, fees, soft costs, "
                                                      "closing costs, purchase price) with zero budgets?"):
            cx.add_template(self.pf, p.holding.id)
            self.fill()

    def create_account(self) -> None:
        p, ln = self.perf(), self._selected()
        if not p or not ln:
            info(self, "Pick a line", "Select a cost item first.")
            return
        if ln.account_id is not None:
            info(self, "Already linked", f"“{ln.item}” is linked to {ln.account_name}.")
            return
        try:
            cx.create_account(self.pf, p.holding, ln)
        except ValueError as e:
            error(self, "Couldn't create the account", str(e))
            return
        info(self, "Linked to the books", f"“CapEx – {ln.item}” is in {p.holding.name}'s chart of accounts. "
                                          "Categorize the payments for it to that account and they show here.")
        self.fill()


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------


class CashFlowPage(_CompanyPage):
    def __init__(self, view):
        super().__init__(view)
        from .asset_mgmt import Card

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        row.addWidget(self.pick)
        row.addStretch(1)
        lay.addLayout(row)
        cards = QHBoxLayout()
        self.cards = {}
        for k, title in (("cash", "Cash today"), ("t12", "Net cash flow"), ("n12", "Next 12 months"),
                         ("low", "Lowest projected cash"), ("equity", "Equity funded")):
            c = Card(title, small=True)
            self.cards[k] = c
            cards.addWidget(c)
        lay.addLayout(cards)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        self.table = _table([""])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        lay.addWidget(self.table, 1)

    def fill(self) -> None:
        from .. import portfolio as pfm

        p = self.perf()
        self.table.setRowCount(0)
        for c in self.cards.values():
            c.set("—")
        if p is None:
            self.note.setText("No operating companies in this view.")
            return
        stmt = op.cash_flow(self.pf, p, self.view._as_of_date())
        fill_statement(self.table, stmt)
        self.note.setText(stmt.note)
        by = {x.label: x for x in stmt.lines}
        net = by["Net cash flow"].values
        act = sum([v for v, pr in zip(net, stmt.projected) if not pr][-12:])
        proj = sum([v for v, pr in zip(net, stmt.projected) if pr][:12])
        self.cards["t12"].set(fmt_money(act), "last 12 months", POSITIVE if act > 0 else NEGATIVE if act < 0 else None)
        self.cards["n12"].set(fmt_money(proj), "net cash flow, projected",
                              POSITIVE if proj > 0 else NEGATIVE if proj < 0 else None)
        if "Ending cash" in by:
            ending = by["Ending cash"].values
            now = [v for v, pr in zip(ending, stmt.projected) if not pr and v is not None]
            self.cards["cash"].set(fmt_money(now[-1]) if now else "—", "in the bank (books)")
            future = [(v, m) for v, m, pr in zip(ending, stmt.months, stmt.projected) if pr and v is not None]
            if future:
                low, when = min(future)
                self.cards["low"].set(fmt_money(low), when.strftime("%b %Y"), NEGATIVE if low < 0 else None)
        owners = [x for x in pfm.cap_table(self.pf, p) if not x.worked_out]
        if owners:
            self.cards["equity"].set(fmt_money(sum(x.funded for x in owners)),
                                     f"of {fmt_money(sum(x.amount_cents for x in owners))} committed")


# ---------------------------------------------------------------------------
# Dashboard (one page per company: timeline, value, rental / construction / cash status, unit types, notes)
# ---------------------------------------------------------------------------


class MilestoneDialog(QDialog):
    def __init__(self, pf, holding, m: op.Milestone | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        self.m = m or op.Milestone("")
        self.setWindowTitle(f"Milestone – {holding.name}")
        form = QFormLayout(self)
        self.name = QLineEdit(self.m.name)
        self.name.setPlaceholderText("e.g. Turnover milestone – 10 units, Refinance, Construction complete")
        self.original = QDateEdit(_qd(self.m.original))
        self.current = QDateEdit(_qd(self.m.current or self.m.original))
        for d in (self.original, self.current):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
        form.addRow("Milestone:", self.name)
        form.addRow("Original date:", self.original)
        form.addRow("Current date:", self.current)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        self.m.name = self.name.text()
        self.m.original = self.original.date().toString("yyyy-MM-dd")
        self.m.current = self.current.date().toString("yyyy-MM-dd")
        try:
            op.save_milestone(self.pf, self.holding.id, self.m)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class _ListDialog(QDialog):
    """Timeline: the milestones (from the records, and the ones typed in) with Add / Edit / Delete."""

    def __init__(self, page, p):
        super().__init__(page)
        self.page, self.p = page, p
        self.setWindowTitle(f"Timeline – {p.holding.name}")
        self.resize(560, 380)
        lay = QVBoxLayout(self)
        self.table = _table(["Milestone", "Original", "Current"], 0)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("Add Milestone…", self.add), ("Edit…", self.edit), ("Delete", self.delete)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.fill()

    def fill(self) -> None:
        self.rows = op.milestones(self.page.pf, self.p.holding)
        self.table.setRowCount(len(self.rows))
        for r, m in enumerate(self.rows):
            for c, v in enumerate([m.name + ("  (from the records)" if m.auto else ""), m.original or "",
                                   m.current or ""]):
                self.table.setItem(r, c, _cell(v, colour=MUTED if m.auto else None))

    def _selected(self):
        r = self.table.currentRow()
        m = self.rows[r] if 0 <= r < len(self.rows) else None
        if m is not None and m.auto:
            info(self, "From the records", f"“{m.name}” comes from the holding's acquisition date, its loans or the "
                                           "exit date (Details…) — change it there.")
            return None
        return m

    def add(self) -> None:
        if MilestoneDialog(self.page.pf, self.p.holding, parent=self).exec():
            self.fill()

    def edit(self) -> None:
        m = self._selected()
        if m and MilestoneDialog(self.page.pf, self.p.holding, m, self).exec():
            self.fill()

    def delete(self) -> None:
        m = self._selected()
        if m and confirm(self, "Delete milestone", f"Delete “{m.name}”?"):
            op.delete_milestone(self.page.pf, m.id)
            self.fill()


class _DetailsDialog(QDialog):
    """Address and planned exit date."""

    def __init__(self, page, p, info_: op.DashInfo):
        super().__init__(page)
        self.setWindowTitle(f"Details – {p.holding.name}")
        self.resize(460, 0)
        form = QFormLayout(self)
        self.address = QLineEdit(info_.address or p.holding.location)
        self.exit = QDateEdit(_qd(info_.exit_date))
        self.exit.setCalendarPopup(True)
        self.exit.setDisplayFormat("yyyy-MM-dd")
        from PySide6.QtWidgets import QCheckBox

        self.use_exit = QCheckBox("Planned exit date:")
        self.use_exit.setChecked(bool(info_.exit_date))
        form.addRow("Address:", self.address)
        form.addRow(self.use_exit, self.exit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class _NotesDialog(QDialog):
    def __init__(self, page, p, text: str):
        super().__init__(page)
        from PySide6.QtWidgets import QPlainTextEdit

        self.setWindowTitle(f"Weekly activity – {p.holding.name}")
        self.resize(620, 420)
        lay = QVBoxLayout(self)
        self.text = QPlainTextEdit(text)
        self.text.setPlaceholderText("Operating activity\n- …\n\nConstruction activity\n- …")
        lay.addWidget(self.text, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)


class DashboardPage(_CompanyPage):
    """One page, no boxes or grid lines: three columns of sections; every figure right-aligned in its column."""

    def __init__(self, view):
        super().__init__(view)
        from PySide6.QtWidgets import QTextBrowser

        outer = QVBoxLayout(self)
        head = QHBoxLayout()
        head.addWidget(QLabel("Company:"))
        head.addWidget(self.pick)
        head.addStretch(1)
        for text, slot in (("Details…", self.edit_details), ("Timeline…", self.edit_timeline),
                           ("Weekly Notes…", self.edit_notes)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            head.addWidget(b)
        outer.addLayout(head)
        self.page = QTextBrowser()
        self.page.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.page.setOpenLinks(False)
        self.page.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.page, 1)
        self.title = QLabel()   # (kept for the window's state; the page carries the title)
        self.data: dict[str, list[list[str]]] = {}

    # -- editing -------------------------------------------------------------

    def edit_details(self) -> None:
        p = self.perf()
        if not p:
            return
        info_ = op.dash_info(self.pf, p.holding.id)
        d = _DetailsDialog(self, p, info_)
        if d.exec():
            info_.address = d.address.text()
            info_.exit_date = d.exit.date().toString("yyyy-MM-dd") if d.use_exit.isChecked() else None
            op.save_dash_info(self.pf, p.holding.id, info_)
            self.fill()

    def edit_timeline(self) -> None:
        p = self.perf()
        if p:
            _ListDialog(self, p).exec()
            self.fill()

    def edit_notes(self) -> None:
        p = self.perf()
        if not p:
            return
        info_ = op.dash_info(self.pf, p.holding.id)
        d = _NotesDialog(self, p, info_.notes)
        if d.exec():
            self.save_notes(d.text.toPlainText())

    def save_notes(self, text: str) -> None:
        p = self.perf()
        if not p:
            return
        info_ = op.dash_info(self.pf, p.holding.id)
        info_.notes, info_.notes_date = text, date.today().isoformat()
        op.save_dash_info(self.pf, p.holding.id, info_)
        self.fill()

    # -- the page ------------------------------------------------------------

    def fill(self) -> None:
        import html as _html

        from .theme import c as tc

        p = self.perf()
        self.data = {}
        if p is None:
            self.title.setText("")
            self.page.setHtml("<p>No operating companies in this view.</p>")
            return
        h = p.holding
        as_of = self.view._as_of_date()
        self.title.setText(f"{h.name} Dashboard")
        info_ = op.dash_info(self.pf, h.id)
        rows = op.units(self.pf, h.id)
        s = op.summary(rows, as_of)
        d = self.data
        d["property"] = [["Property", h.name], ["Address", info_.address or h.location or "—"], ["Type", h.type],
                         ["Units", str(s.units) if s.units else "—"],
                         ["Occupancy", f"{s.occupancy * 100:.1f}%" if s.occupancy is not None else "—"]]
        ms = op.milestones(self.pf, h)
        d["timeline"] = [[m.name, m.original or "", m.current or ""] for m in ms]
        d["value"] = [["Current value", fmt_money(p.company_value) if p.company_value is not None else "—"],
                      ["Total debt", fmt_money(p.debt)], ["NAV", fmt_money(p.nav)]]
        d["returns"] = [["Invested", fmt_money(p.invested)], ["Distributions to date", fmt_money(p.returned)],
                        ["Total profit", fmt_money(p.gain)], ["MOIC", f"{p.moic:.2f}x" if p.moic is not None else "—"],
                        ["IRR", f"{p.irr * 100:.1f}%" if p.irr is not None else "—"]]
        stmt = op.monthly_statement(self.pf, p, as_of)
        by = {x.label: x for x in stmt.lines}

        def nxt(label):
            return sum(v for v, pr in zip(by[label].values, stmt.projected) if pr and v is not None)

        st = op.settings(self.pf, h.id)
        egi, opx = op.t12(stmt, "Total revenue"), op.t12(stmt, "Total operating expenses")
        other_rev = egi - op.t12(stmt, "Rental revenue") if "Rental revenue" in by else egi
        run_egi = round(s.in_place * 12 * (1 - st.vacancy_pct / 100)) + other_rev if rows else egi
        d["rental"] = [["EGI", fmt_money(egi), fmt_money(run_egi), fmt_money(nxt("Total revenue"))],
                       ["Op. Ex", fmt_money(opx), fmt_money(opx), fmt_money(nxt("Total operating expenses"))],
                       ["NOI", fmt_money(egi - opx), fmt_money(run_egi - opx), fmt_money(nxt("Net operating income"))]]
        tot = cx.totals(cx.lines(self.pf, h, as_of))
        d["construction"] = [[g, fmt_money(b), fmt_money(dd), fmt_money(c_)] for g, (b, dd, c_) in tot.by_group.items()]
        if d["construction"]:
            d["construction"].append(["Total budget", fmt_money(tot.budget), fmt_money(tot.to_date),
                                      fmt_money(tot.to_complete)])
        cf = op.cash_flow(self.pf, p, as_of)
        cby = {x.label: x for x in cf.lines}
        i = max((k for k, pr in enumerate(cf.projected) if not pr), default=0)

        def v(label, sign=1):
            x = cby.get(label)
            return fmt_money(sign * x.values[i]) if x and x.values[i] is not None else "—"

        d["cash"] = [["Starting cash", v("Opening cash")], ["Rent collection", v("Total revenue")],
                     ["Operating costs", v("Total operating expenses", -1)], ["CapEx", v("CapEx")],
                     ["Debt service", v("Debt service")], ["Equity", v("Equity injections")],
                     ["Debt", v("Loan advances")], ["Working capital change", v("Other / working capital (books)")],
                     ["Ending cash", v("Ending cash")]]
        types = op.by_unit_type(rows)
        d["types"] = [[t.unit_type, str(t.existing), fmt_money(t.existing_rent) if t.existing else "—",
                       str(t.renovated), fmt_money(t.renovated_rent) if t.renovated else "—",
                       fmt_money(t.market) if t.market else "—"] for t in types]
        d["leasing"] = [[t.unit_type, str(t.vacant), fmt_money(t.market) if t.market else "—"] for t in types]
        if types:
            d["leasing"].append(["Total", str(sum(t.vacant for t in types)), ""])

        # ---- render: one page, no lines; titles quiet, figures right-aligned ----
        text, muted, red, accent = self.palette().text().color().name(), tc("muted"), tc("red"), tc("link")
        esc = _html.escape

        def section(title: str, rows_: list[list[str]], heads: list[str] | None = None, *, bold_last=False,
                    empty="—", sub: str = "", late: set[int] = frozenset()) -> str:
            out = [f"<p style='margin:18px 0 4px 0; color:{accent}; font-size:8.5pt; font-weight:700'>"
                   f"{esc(title.upper())}{f' &nbsp;<span style=\"color:{muted}; font-weight:400\">{esc(sub)}</span>' if sub else ''}"
                   "</p>"]
            out.append("<table width='100%' cellspacing='0' cellpadding='3'>")
            if heads:
                out.append("<tr>" + "".join(
                    f"<td align='{'left' if k == 0 else 'right'}' style='color:{muted}; font-size:8pt'>{esc(x)}</td>"
                    for k, x in enumerate(heads)) + "</tr>")
            if not rows_:
                out.append(f"<tr><td style='color:{muted}'>{esc(empty)}</td></tr>")
            for r, row in enumerate(rows_):
                weight = "font-weight:700;" if bold_last and r == len(rows_) - 1 else ""
                cells = []
                for k, x in enumerate(row):
                    colour = red if (k > 0 and (x.startswith("(") or (r in late and k == 2))) else \
                        (muted if k == 0 and not weight else text)
                    width = " width='42%'" if k == 0 and len(row) == 2 else ""
                    cells.append(f"<td{width} align='{'left' if k == 0 else 'right'}' style='{weight}color:{colour}'>"
                                 f"{esc(x)}</td>")
                out.append("<tr>" + "".join(cells) + "</tr>")
            out.append("</table>")
            return "".join(out)

        late_rows = {r for r, m in enumerate(ms) if m.original and m.current and m.current > m.original}
        left = (section("Property", d["property"]) +
                section("Timeline", d["timeline"], ["", "Original", "Current"], late=late_rows,
                        empty="No milestones yet — Timeline…") +
                section("Value", d["value"], bold_last=True) + section("Returns", d["returns"]))
        mid = (section("Rental status", d["rental"], ["", "Last 12 months", "Run-rate", "Next 12"], bold_last=True) +
               section("Construction status", d["construction"], ["", "Budgeted", "Cost to date", "To complete"],
                       bold_last=True, empty="No capex schedule yet (CapEx tab)") +
               section("Cash balance", d["cash"], bold_last=True, sub=cf.months[i].strftime("%B %Y")))
        notes = info_.notes.strip()
        notes_html = (f"<p style='margin:2px 0; color:{text}'>" +
                      "<br>".join(esc(x) for x in notes.splitlines()) + "</p>") if notes else \
            f"<p style='color:{muted}'>No notes yet — Weekly Notes…</p>"
        right = (section("Rental status by unit type", d["types"], ["", "Existing", "Rent", "Reno", "Rent",
                                                                     "Market"], empty="No rent roll yet") +
                 (f"<p style='margin:2px 0 0 3px; color:{muted}; font-size:8.5pt'>Monthly rent {fmt_money(s.in_place)}"
                  f" · average {fmt_money(s.avg_rent)}</p>" if rows and s.avg_rent is not None else "") +
                 section("Leasing update", d["leasing"], ["", "Available", "Market rate"], bold_last=bool(types),
                         empty="No rent roll yet") +
                 f"<p style='margin:18px 0 4px 0; color:{accent}; font-size:8.5pt; font-weight:700'>WEEKLY ACTIVITY"
                 f"{f' &nbsp;<span style=\"color:{muted}; font-weight:400\">saved {info_.notes_date}</span>' if info_.notes_date else ''}"
                 f"</p>{notes_html}")
        page = (f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td valign='middle'><span style='font-size:17pt; font-weight:700; color:{text}'>{esc(h.name)}"
                f"</span> <span style='font-size:12pt; color:{muted}'>Dashboard</span></td>"
                f"<td align='right' valign='middle' style='color:{muted}'>For the week of "
                f"{as_of.strftime('%b %d, %Y')}</td></tr></table>"
                f"<table width='100%' cellspacing='0' cellpadding='0' style='margin-top:8px'><tr>"
                f"<td width='32%' valign='top'>{left}</td><td width='2%'></td>"
                f"<td width='32%' valign='top'>{mid}</td><td width='2%'></td>"
                f"<td width='32%' valign='top'>{right}</td></tr></table>")
        self.page.setHtml(page)
