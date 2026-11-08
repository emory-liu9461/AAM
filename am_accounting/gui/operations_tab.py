"""Asset Management → Operations (rent roll, unit mix, monthly operating statement) and CapEx (budget vs cost to
date from the books) for one company."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import capex as cx
from .. import operations as op
from .. import portfolio as pfm
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


class _CompanyPage(QWidget):
    """A page showing one company of the portfolio (picked here, or by Viewing)."""

    def __init__(self, view):
        super().__init__()
        self.view = view
        self.pick = QComboBox()
        self.pick.setMinimumWidth(260)
        self.pick.currentIndexChanged.connect(lambda *_: self.fill_if_shown())

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
        self.fill_if_shown()

    def shown(self) -> bool:
        """The tab on screen in Asset Management (other pages fill when they're opened)."""
        tabs = getattr(self.view, "tabs", None)
        return tabs is None or tabs.currentWidget() is self

    def fill_if_shown(self) -> None:
        """Fill now if this page is on screen; otherwise when it's opened — so refreshing the portfolio doesn't
        rebuild pages nobody is looking at."""
        if self.shown():
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
        import dataclasses

        self.before = dataclasses.replace(unit) if unit else None   # to record a move-out / move-in
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
        self.docs = None
        if u.id is not None:     # leases, notices, inspection photos …
            from .. import attachments as at
            from .attachments_box import AttachmentsBox

            self.docs = AttachmentsBox(pf.conn, at.portfolio_folder(pf), "unit", f"{holding.id}:{u.id}",
                                       sub=f"{holding.name} - {u.unit}", compact=True)
            form.addRow("Documents:", self.docs)
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
        from .. import turnover as tv

        try:
            tv.record_change(self.pf, self.holding.id, self.before, u)
        except ValueError:
            pass
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

        srow = QHBoxLayout()
        srow.addWidget(QLabel("Show:"))
        self.stmt_show = QComboBox()
        for label, key in (("Pro-Forma & Actual", "both"), ("Actual", "actual"), ("Pro-Forma", "pro_forma")):
            self.stmt_show.addItem(label, key)
        self.stmt_show.currentIndexChanged.connect(lambda *_: self.fill())
        srow.addWidget(self.stmt_show)
        srow.addStretch(1)
        sl.addLayout(srow)
        self.stmt = MonthTable()            # one clean page, like the Dashboard and Cash Flow
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
        # operating costs: the major cost categories and who provides each
        cw = QWidget()
        cwl = QVBoxLayout(cw)
        cwl.setContentsMargins(0, 4, 0, 0)
        cb = QHBoxLayout()
        self.costs_info = QLabel()
        self.costs_info.setObjectName("hint")
        cb.addWidget(self.costs_info, 1)
        for text, slot, tip in (
                ("Add Cost…", self.add_cost, "Another cost category and its provider"),
                ("Edit…", self.edit_cost, "The selected category: provider, account #, contact, contract, budget"),
                ("Delete", self.delete_cost, "Take the selected category off the list"),
                ("Add the Usual Categories", self.usual_costs, "Power, water, gas, insurance, snow removal, waste …"),
                ("Fill Providers from the Books", self.providers_from_books,
                 "Blank providers: the vendor paid the most for that category in the last 12 months"),
                ("Name Payments with the Provider", self.name_with_providers,
                 "Unnamed payments in each category's account: named after the provider entered here"),
                ("Assign Vendors in the Books…", self.assign_vendors,
                 "Opens the company's Transactions showing the ones with no customer / vendor")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            cb.addWidget(b)
        cwl.addLayout(cb)
        self.vendor_status = QLabel()               # the vendor check at a glance
        self.vendor_status.setTextFormat(Qt.TextFormat.RichText)
        self.vendor_status.setWordWrap(True)
        cwl.addWidget(self.vendor_status)
        self.costs = _table(COST_COLS, 1)
        self.costs.doubleClicked.connect(lambda *_: self.edit_cost())
        cwl.addWidget(self.costs, 1)
        self.sub.addTab(cw, "Operating Costs")
        self._cost_rows: list = []
        # unit mix
        self.mix = _table(["Unit type", "Status", "Units", "Occupied", "Average rent", "Average market rent",
                           "Monthly rent", "Gap to market"], None)
        self.sub.addTab(self.mix, "Unit Mix")
        from .ops_extra import BudgetTab, TurnoverTab

        self.turn_tab = TurnoverTab(self)
        self.sub.addTab(self.turn_tab, "Unit Turnover")
        self.budget_tab = BudgetTab(self)
        self.sub.addTab(self.budget_tab, "Budget vs Actual")
        self.sub.currentChanged.connect(lambda *_: self._fill_extra())
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
        self.stmt.setHtml("")
        for t in (self.roll, self.mix):
            t.setRowCount(0)
        if p is None:
            self.stmt.setHtml("<p>No operating companies in this view.</p>")
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
        self._fill_costs()
        self._fill_extra()

    def _fill_extra(self) -> None:
        """Budget vs Actual and Unit Turnover read the books: filled when they're the sub-tab on screen."""
        w = self.sub.currentWidget()
        if w in (getattr(self, "budget_tab", None), getattr(self, "turn_tab", None)) and self.perf() is not None:
            w.fill()

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
        p = self.perf()
        f = op.master_cash_flow(self.pf, p, self.view._as_of_date(), back=HISTORY_MONTHS)
        rows = op.operating_rows(f, self._units, op.settings(self.pf, p.holding.id))
        note = ("Pro-Forma: rent from the rent roll ahead (annual increases on move-in anniversaries, less vacancy), "
                "other lines as the same month a year earlier, scheduled debt payments. Actual: the books.")
        self.stmt.set_parts(*pf_actual_parts(self, f.months, f.past, rows, self.stmt_show.currentData() or "both",
                                             note))

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

    def _fill_costs(self) -> None:
        """The cost list with the books; then every other operating expense account with spending; and a check that
        every operating expense has a vendor named."""
        from .. import op_costs as oc
        from .theme import c as tc

        t = self.costs
        t.setRowCount(0)
        self._cost_rows = []
        self.vendor_status.setText("")
        p = self.perf()
        if p is None:
            return
        h = p.holding
        oc.start_once(self.pf, h.id)
        chk = self.vendor_check = oc.vendor_check(self.pf, h, self.view._as_of_date())
        has_books = not chk.error

        def add(ln, *, other=False):
            r = t.rowCount()
            t.insertRow(r)
            provider = ln.provider or (f"{ln.top_vendor} (from the books)" if ln.top_vendor else "")
            vals = [ln.category + (" (not on the list)" if other else ""), provider, ln.account_no, ln.contact,
                    ln.contract_end or "", fmt_money(ln.actual_cents) if ln.actual_cents else ("—" if has_books else ""),
                    fmt_money(ln.unassigned_cents) if ln.unassigned_cents else ("✓" if ln.actual_cents else ""),
                    fmt_money(ln.budget_cents) if ln.budget_cents else "",
                    ln.matched_account or ("not found" if has_books else ""), ln.notes]
            soon = bool(ln.contract_end and (date.fromisoformat(ln.contract_end) - date.today()).days <= 60)
            for c, v in enumerate(vals):
                colour = MUTED if (c == 1 and not ln.provider) or c == 8 or (other and c == 0) else None
                if (c == 4 and soon) or (c == 6 and ln.unassigned_cents):
                    colour = NEGATIVE
                if c == 6 and not ln.unassigned_cents and ln.actual_cents:
                    colour = POSITIVE
                it = _cell(v, right=c in (5, 6, 7), colour=colour)
                if c == 1 and not ln.provider:
                    ex = oc.EXAMPLES.get(ln.category)
                    it.setToolTip(f"e.g. {ex}" if ex else "")
                if c in (1, 6) and ln.vendors:
                    it.setToolTip("Paid in the last 12 months: " + ", ".join(
                        f"{v} {fmt_money(cents)}" for v, cents in ln.vendors[:6]))
                t.setItem(r, c, it)
            self._cost_rows.append(None if other else ln)

        for ln in chk.lines:
            add(ln)
        for ln in chk.other_accounts:
            add(ln, other=True)
        rows = chk.all_rows
        if rows:
            r = t.rowCount()
            t.insertRow(r)
            for c, v in enumerate(["Total", "", "", "", "", fmt_money(chk.total) if has_books else "",
                                   fmt_money(chk.unassigned) if chk.unassigned else "",
                                   fmt_money(sum(ln.budget_cents for ln in chk.lines)) or "", "", ""]):
                t.setItem(r, c, _cell(v, right=c in (5, 6, 7), bold=True))
            self._cost_rows.append(None)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        named = sum(1 for ln in chk.lines if ln.provider)
        self.costs_info.setText(f"{len(chk.lines)} cost categories · {named} with a provider · last 12 months from "
                                "the books" + ("" if has_books else " (not linked to its books)")
                                + ". Contracts ending within 60 days are in red.")
        red, green = tc("red"), tc("green")
        if chk.error:
            self.vendor_status.setText(f"<span style='color:{tc('muted')}'>Vendor check: {chk.error.lower()}.</span>")
        elif not chk.unassigned and not chk.other_accounts:
            self.vendor_status.setText(f"<b style='color:{green}'>✓ Every operating expense has a vendor and is on the "
                                       f"cost list.</b>")
        else:
            bits = []
            if chk.unassigned:
                n = len(chk.accounts_unassigned)
                share = chk.unassigned / chk.total * 100 if chk.total else 0
                bits.append(f"<b style='color:{red}'>⚠ {fmt_money(chk.unassigned)} of operating expenses "
                            f"({share:.0f}%) in {n} account(s) have no vendor</b> — Assign Vendors in the Books… "
                            f"(then set the customer / vendor on those transactions)")
            if chk.other_accounts:
                bits.append(f"{len(chk.other_accounts)} operating expense account(s) aren't on the cost list: "
                            + ", ".join(ln.category for ln in chk.other_accounts[:6])
                            + (" …" if len(chk.other_accounts) > 6 else "")
                            + " — Add Cost… with that account to track them")
            self.vendor_status.setText("<br>".join(bits))

    def name_with_providers(self) -> None:
        from .. import op_costs as oc

        p = self.perf()
        if not p:
            return
        found = oc.provider_matches(self.pf, p.holding)
        if not found:
            info(self, "Nothing to name", "No unnamed payments in the accounts of categories with a provider. Enter "
                                          "the providers first (Edit…), or Assign Vendors in the Books….")
            return
        lines_ = "\n".join(f"• {ln.category}: {len(ids)} payment(s) → {ln.provider}" for ln, ids in found)
        if not confirm(self, "Name payments", f"Name these unnamed payments in the books after the provider?\n\n"
                                              f"{lines_}\n\nYou can change any of them in Transactions."):
            return
        n = oc.name_with_providers(self.pf, p.holding)
        self._fill_costs()
        info(self, "Named", f"{n} payment(s) named after their provider.")

    def assign_vendors(self) -> None:
        p = self.perf()
        if p and p.holding.company_path and hasattr(self.view, "open_unnamed"):
            self.view.open_unnamed.emit(p.holding.company_path)

    def _cost(self):
        r = self.costs.currentRow()
        return self._cost_rows[r] if 0 <= r < len(self._cost_rows) else None

    def add_cost(self) -> None:
        p = self.perf()
        if p and CostDialog(self.pf, p.holding, parent=self).exec():
            self._fill_costs()

    def edit_cost(self) -> None:
        p, ln = self.perf(), self._cost()
        if p and ln and CostDialog(self.pf, p.holding, ln, self).exec():
            self._fill_costs()

    def delete_cost(self) -> None:
        from .. import op_costs as oc

        ln = self._cost()
        if ln and confirm(self, "Delete", f"Take “{ln.category}” off the operating costs?"):
            oc.delete(self.pf, ln.id)
            self._fill_costs()

    def usual_costs(self) -> None:
        from .. import op_costs as oc

        p = self.perf()
        if p:
            n = oc.add_standard(self.pf, p.holding.id)
            self._fill_costs()
            info(self, "Usual categories", f"{n} added." if n else "They're all on the list already.")

    def providers_from_books(self) -> None:
        from .. import op_costs as oc

        p = self.perf()
        if not p:
            return
        rows = [ln for ln in self._cost_rows if ln is not None]
        n = oc.fill_providers(self.pf, p.holding.id, rows)
        self._fill_costs()
        info(self, "Providers", f"{n} provider(s) filled in from the books." if n else
             "Nothing to fill in: the blank categories have no named vendor in the books (set the customer / vendor "
             "on their payments in Transactions).")

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


COST_COLS = ["Category", "Provider", "Account #", "Contact", "Contract ends", "Last 12 months", "No vendor",
             "Budget / yr", "Account in the books", "Notes"]


class CostDialog(QDialog):
    """One operating cost: its category, provider (with the account number and contact), contract and budget."""

    def __init__(self, pf, holding, line=None, parent=None):
        super().__init__(parent)
        from .. import op_costs as oc

        self.pf, self.holding = pf, holding
        self.ln = ln = line or oc.CostLine("")
        self.setWindowTitle(f"Operating cost – {holding.name}")
        self.resize(560, 0)
        form = QFormLayout(self)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems([c for c, _w, _e in oc.CATEGORIES])
        self.category.setCurrentText(ln.category)
        self.provider = QComboBox()
        self.provider.setEditable(True)
        self.provider.addItems([""] + [v for v, _c in ln.vendors])
        self.provider.setCurrentText(ln.provider)
        self.account_no = QLineEdit(ln.account_no)
        self.account_no.setPlaceholderText("Your account number with them")
        self.contact = QLineEdit(ln.contact)
        self.contact.setPlaceholderText("Name, phone, email")
        self.has_end = QCheckBox("Contract ends:")
        self.end = QDateEdit(_qd(ln.contract_end))
        self.end.setCalendarPopup(True)
        self.end.setDisplayFormat("MMM d, yyyy")
        self.has_end.setChecked(bool(ln.contract_end))
        self.end.setEnabled(bool(ln.contract_end))
        self.has_end.toggled.connect(self.end.setEnabled)
        self.budget = _money_box()
        self.budget.setValue(ln.budget_cents / 100)
        self.account = QComboBox()
        self.account.addItem("(found by its name)", "")
        for name in oc.expense_accounts(holding.company_path):
            self.account.addItem(name, name)
        self.account.setCurrentIndex(max(0, self.account.findData(ln.book_account)))
        self.notes = QLineEdit(ln.notes)
        self.category.currentTextChanged.connect(self._example)
        form.addRow("Category:", self.category)
        form.addRow("Provider:", self.provider)
        form.addRow("Account #:", self.account_no)
        form.addRow("Contact:", self.contact)
        form.addRow(self.has_end, self.end)
        form.addRow("Budget a year:", self.budget)
        form.addRow("Account in the books:", self.account)
        form.addRow("Notes:", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._example()

    def _example(self, *_) -> None:
        from .. import op_costs as oc

        ex = oc.EXAMPLES.get(self.category.currentText())
        self.provider.lineEdit().setPlaceholderText(f"e.g. {ex}" if ex else "Who provides it")

    def save(self) -> None:
        from .. import op_costs as oc

        ln = self.ln
        ln.category, ln.provider = self.category.currentText(), self.provider.currentText()
        ln.account_no, ln.contact, ln.notes = self.account_no.text(), self.contact.text(), self.notes.text()
        ln.contract_end = self.end.date().toString("yyyy-MM-dd") if self.has_end.isChecked() else None
        ln.budget_cents = round(self.budget.value() * 100)
        ln.book_account = self.account.currentData() or ""
        try:
            oc.save(self.pf, self.holding.id, ln)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


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
        if ln.parent_id is not None:
            self.grp.setEnabled(False)
            self.grp.setToolTip(f"A unit of “{ln.parent_item}” — in its group")
            self.setWindowTitle(f"{ln.parent_item} – {ln.item} – {holding.name}")
        form.addRow("Group:", self.grp)
        form.addRow("Unit:" if ln.parent_id is not None else "Cost item:", self.item)
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
        for text, slot in (("Add Line…", self.add_line), ("Add Unit…", self.add_unit), ("Edit…", self.edit_line),
                           ("Delete", self.delete_line),
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
                         ("pct", "Complete"), ("gap", "Gap to balance sheet")):
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
        from PySide6.QtWidgets import QTextBrowser

        self.months = QTextBrowser()          # one clean page, like the Dashboard
        self.months.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.months.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        ml.addWidget(self.months, 1)
        self.lower = QTabWidget()
        self.lower.setDocumentMode(True)
        self.lower.addTab(mw, "Monthly Capex Spending")
        # reconciling to the balance sheet: the bridge, then each fixed-asset account
        rw = QWidget()
        rl = QHBoxLayout(rw)
        rl.setContentsMargins(0, 4, 0, 0)
        self.recon_page = QTextBrowser()
        self.recon_page.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.recon_page.setMinimumWidth(420)
        rl.addWidget(self.recon_page, 2)
        right = QVBoxLayout()
        right.addWidget(QLabel("<b>Fixed assets on the balance sheet</b>"))
        self.recon = _table(["Account", "Balance sheet", "At closing / opening", "Spent since", "On the schedule"], 0)
        self.recon.doubleClicked.connect(lambda *_: self.recon_link())
        right.addWidget(self.recon, 1)
        rb = QHBoxLayout()
        for text, slot, tip in (
                ("Add to Schedule", self.recon_add, "Put the selected account on the schedule as its own line"),
                ("Link to a Line…", self.recon_link, "Link the selected account to a line already on the schedule")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            rb.addWidget(b)
        rb.addStretch(1)
        right.addLayout(rb)
        rl.addLayout(right, 3)
        self.lower.addTab(rw, "Reconcile to Balance Sheet")
        split.addWidget(self.lower)
        split.setSizes([420, 260])
        self._recon_rows: list = []
        lay.addWidget(split, 1)
        self._rows: list = []   # table row -> CapexLine | None

    def fill(self) -> None:
        p = self.perf()
        t = self.table
        t.setRowCount(0)
        self.months.setHtml("")
        for c in self.cards.values():
            c.set("—")
        self._rows = []
        if p is None:
            return
        as_of = self.view._as_of_date()
        cx.sync_units(self.pf, p.holding.id)      # a sub-line for each unit on the rent roll
        try:
            cx.link_unit_accounts(self.pf, p.holding)   # and, once in-suite is in the books, a sub-account each
        except Exception:  # noqa: BLE001 — the books may be busy or read-only; try again next time
            pass
        lines = cx.lines(self.pf, p.holding, as_of)
        tot = cx.totals(lines)
        n_units = sum(1 for ln in lines if ln.parent_id is not None)
        self.cards["budget"].set(fmt_money(tot.budget), f"{len(lines) - n_units} line(s)"
                                 + (f" · {n_units} unit(s)" if n_units else ""))
        self.cards["to_date"].set(fmt_money(tot.to_date), "books + entered by hand")
        self.cards["to_complete"].set(fmt_money(tot.to_complete), "budget − cost to date",
                                      NEGATIVE if tot.to_complete < 0 else None)
        self.cards["pct"].set(f"{tot.to_date / tot.budget * 100:.0f}%" if tot.budget else "—", "of the budget spent")
        order = list(dict.fromkeys([g for g in cx.GROUPS if any(ln.grp == g for ln in lines)]
                                   + [ln.grp for ln in lines]))
        for g in order:
            members = [ln for ln in lines if ln.grp == g]
            self._add(t, [g] + [""] * 7, head=True)
            for k, ln in enumerate(members):
                unit = ln.parent_id is not None
                self._add(t, [("          " if unit else "    ") + ln.item, fmt_money(ln.budget_cents),
                              fmt_money(ln.book_cents) if ln.account_id is not None else "—",
                              fmt_money(ln.manual_cents) if ln.manual_cents else "",
                              fmt_money(ln.to_date), fmt_money(ln.to_complete),
                              f"{ln.pct_complete * 100:.0f}%" if ln.pct_complete is not None else "",
                              ln.account_name or "not linked"], line=ln, unit=unit)
                last_unit = unit and (k + 1 == len(members) or members[k + 1].parent_id != ln.parent_id)
                if last_unit:   # the line with its units
                    fam = [x for x in members if x.id == ln.parent_id or x.parent_id == ln.parent_id]
                    b_, d_ = sum(x.budget_cents for x in fam), sum(x.to_date for x in fam)
                    self._add(t, [f"    Total {ln.parent_item}", fmt_money(b_), "", "", fmt_money(d_),
                                  fmt_money(b_ - d_), f"{d_ / b_ * 100:.0f}%" if b_ else "", ""], bold=True)
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
        self._fill_recon(p, as_of)

    def _add(self, t, vals, *, head=False, bold=False, line=None, unit=False) -> None:
        r = t.rowCount()
        t.insertRow(r)
        for c, v in enumerate(vals):
            colour = MUTED if unit and c == 0 else None
            if line is not None and c == 5 and line.to_complete < 0:
                colour = NEGATIVE
            if line is not None and c == 7 and line.account_id is None:
                colour = MUTED
            t.setItem(r, c, _cell(v, right=0 < c < 7, bold=head or bold, colour=colour))
        if head:
            t.setSpan(r, 0, 1, len(vals))
        self._rows.append(line)

    def _fill_recon(self, p, as_of: date) -> None:
        """The schedule against the fixed assets on the balance sheet: the bridge and each account."""
        import html as _html

        from .theme import c as tc

        r = self.recon_data = cx.reconcile(self.pf, p.holding, as_of)
        t = self.recon
        t.setRowCount(0)
        self._recon_rows = []
        if r.error:
            self.cards["gap"].set("—", r.error.lower())
            self.recon_page.setHtml(f"<p>{_html.escape(r.error)} — link the company to its books to reconcile.</p>")
            return
        n = len(r.not_on_schedule)
        self.cards["gap"].set(fmt_money(r.gap), (f"{n} fixed-asset account(s) not on the schedule" if n else
                                                 "schedule = balance sheet") if not r.difference else
                              f"{fmt_money(r.difference)} unexplained", NEGATIVE if r.gap or r.difference else POSITIVE)
        text, muted, red, green, accent = (self.palette().text().color().name(), tc("muted"), tc("red"), tc("green"),
                                           tc("link"))
        esc = _html.escape

        def m(c):
            whole = fmt_money(abs(c))
            return f"<span style='color:{red}'>({whole})</span>" if c < 0 else whole

        def line(label, cents, *, bold=False, indent=0, colour=None, note=""):
            w = "font-weight:700;" if bold else ""
            return (f"<tr><td style='{w}padding:2px 24px 2px {indent}px; color:{colour or (text if bold else muted)}'>"
                    f"{label}{f' <span style=color:{muted};font-size:8pt>{esc(note)}</span>' if note else ''}</td>"
                    f"<td align='right' style='{w}padding:2px 0; color:{text}'>{m(cents)}</td></tr>")

        rows = [line("Cost to date on the schedule", r.schedule_total, bold=True)]
        if r.by_hand:
            rows.append(line("less entered by hand (not in the books)", -r.by_hand, indent=14))
        if r.expensed:
            rows.append(line("less lines linked to expense accounts (expensed)", -r.expensed, indent=14))
        if r.other_assets:
            rows.append(line("less lines linked to other asset accounts", -r.other_assets, indent=14))
        rows.append(line("Schedule in Fixed Assets", r.schedule_fixed, bold=True))
        if r.not_on_schedule:
            rows.append(f"<tr><td colspan='2' style='padding:8px 0 2px 0; color:{accent}; font-size:8.5pt; "
                        f"font-weight:700'>FIXED ASSETS NOT ON THE SCHEDULE</td></tr>")
            for a in r.not_on_schedule:
                note = ("at closing / opening" if a.acquisition and not a.since else
                        f"{fmt_money(a.since)} spent since — capex missing from the schedule?" if a.since else "")
                rows.append(line(esc(a.name), a.balance, indent=14, note=note))
            rows.append(line("Total not on the schedule", r.unscheduled, bold=True))
        rows.append(line("Fixed assets on the balance sheet (at cost)", r.balance_sheet, bold=True))
        if r.accumulated:
            rows.append(line("Accumulated depreciation (not part of the schedule)", r.accumulated, indent=14))
        ok = not r.difference
        status = (f"<p style='color:{green}; font-weight:700'>✓ Reconciled — the gap is the fixed assets not on the "
                  f"schedule.</p>" if ok else
                  f"<p style='color:{red}; font-weight:700'>{fmt_money(r.difference)} unexplained — check the lines "
                  f"linked to accounts that no longer exist.</p>")
        hint = ""
        if r.unscheduled_since:
            hint = (f"<p style='color:{red}'>{fmt_money(r.unscheduled_since)} was spent since closing on fixed assets "
                    f"the schedule doesn't track — Add to Schedule or Link to a Line…</p>")
        self.recon_page.setHtml(
            f"<p style='color:{accent}; font-size:8.5pt; font-weight:700; margin:0 0 4px 0'>CAPEX SCHEDULE → BALANCE "
            f"SHEET <span style='color:{muted}; font-weight:400'>at {esc(r.as_of)}</span></p>"
            f"<table cellspacing='0' cellpadding='0' style='color:{text}'>{''.join(rows)}</table>"
            f"{status}{hint}<p style='color:{muted}; font-size:8pt'>Gap to the balance sheet: "
            f"<b>{fmt_money(r.gap)}</b> (fixed assets at cost − the schedule's cost to date).</p>")
        for a in r.accounts or []:
            row = t.rowCount()
            t.insertRow(row)
            vals = [a.name, fmt_money(a.balance), fmt_money(a.acquisition) if a.acquisition else "",
                    fmt_money(a.since) if a.since else "",
                    ", ".join(a.lines) if a.lines else ("— (contra)" if a.accumulated else "not on the schedule")]
            for c, v in enumerate(vals):
                colour = None
                if c == 4 and not a.lines and not a.accumulated:
                    colour = NEGATIVE if a.since else MUTED
                t.setItem(row, c, _cell(v, right=c in (1, 2, 3), colour=colour))
            self._recon_rows.append(a)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)

    def _recon_selected(self):
        r = self.recon.currentRow()
        a = self._recon_rows[r] if 0 <= r < len(self._recon_rows) else None
        if a is None:
            info(self, "Pick an account", "Select a fixed-asset account in the list first.")
        return a

    def recon_add(self) -> None:
        p, a = self.perf(), self._recon_selected()
        if not p or not a:
            return
        if a.on_schedule:
            info(self, "On the schedule", f"{a.name} is already on the schedule ({', '.join(a.lines)}).")
            return
        cx.add_account_line(self.pf, p.holding, a)
        self.fill()

    def recon_link(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        p, a = self.perf(), self._recon_selected()
        if not p or not a:
            return
        rows = [ln for ln in cx.lines(self.pf, p.holding) if ln.account_id != a.id]
        if not rows:
            info(self, "No lines", "Add a line first (Add Line…), or Add to Schedule.")
            return
        labels = [f"{ln.label}" + (f"  (now: {ln.account_name})" if ln.account_id else "  (not linked)")
                  for ln in rows]
        pick, ok = QInputDialog.getItem(self, "Link to a line", f"Link {a.name} to:", labels, 0, False)
        if ok and pick:
            ln = rows[labels.index(pick)]
            if ln.account_id is not None and not confirm(self, "Replace the link",
                                                         f"“{ln.label}” is linked to {ln.account_name}. Link it to "
                                                         f"{a.name} instead?"):
                return
            cx.link_account(self.pf, p.holding, ln.id, a.id)
            self.fill()

    def _fill_months(self, lines, as_of: date) -> None:
        """Spending recorded in the books, last 12 months: cost items down (by group), months across ("Mar 26");
        no grid or lines, figures right-aligned, totals in bold."""
        import html as _html

        from .theme import c as tc

        months = op._month_starts(as_of, 12, 0)
        keys = [m.strftime("%Y-%m") for m in months]
        active = [ln for ln in lines if ln.monthly and any(ln.monthly.get(k) for k in keys)]
        self.month_rows = [[ln.label, *[ln.monthly.get(k, 0) for k in keys]] for ln in active]
        tot = cx.monthly_outflows(lines, keys)
        text, muted, accent = self.palette().text().color().name(), tc("muted"), tc("link")
        esc = _html.escape
        td = "padding:2px 10px"
        head = ("<tr><td></td>" + "".join(f"<td align='right' style='{td}; color:{text}; font-weight:700'>"
                                            f"{m.strftime('%b %y')}</td>" for m in months)
                + f"<td align='right' style='{td}; color:{text}; font-weight:700'>Total</td></tr>")
        body = []
        for grp in dict.fromkeys(ln.grp for ln in active):
            body.append(f"<tr><td colspan='{len(keys) + 2}' style='padding:10px 0 2px 0; color:{accent}; "
                        f"font-size:8.5pt; font-weight:700'>{esc(grp.upper())}</td></tr>")
            for ln in (x for x in active if x.grp == grp):
                vals = [ln.monthly.get(k, 0) for k in keys]
                body.append(f"<tr><td style='padding:2px 24px 2px 14px; color:{muted}; white-space:nowrap'>"
                            f"{esc(ln.label)}</td>"
                            + "".join(f"<td align='right' style='{td}; color:{text}'>{_whole(v) if v else ''}</td>"
                                      for v in vals)
                            + f"<td align='right' style='{td}; color:{text}; font-weight:700'>{_whole(sum(vals))}</td>"
                            "</tr>")
        if active:
            body.append(f"<tr><td colspan='{len(keys) + 2}' style='font-size:4pt'>&nbsp;</td></tr>")
            body.append(f"<tr><td style='padding:2px 24px 2px 0; color:{text}; font-weight:700'>Total</td>"
                        + "".join(f"<td align='right' style='{td}; color:{text}; font-weight:700'>"
                                  f"{_whole(tot[k]) if tot[k] else ''}</td>" for k in keys)
                        + f"<td align='right' style='{td}; color:{text}; font-weight:700'>"
                          f"{_whole(sum(tot.values()))}</td></tr>")
        else:
            body.append(f"<tr><td colspan='{len(keys) + 2}' style='color:{muted}; padding-top:8px'>Nothing recorded "
                        "in the books for the capex lines in the last 12 months — link lines to accounts (Edit… or "
                        "Create Account in the Books).</td></tr>")
        self.months.setHtml(f"<table cellspacing='0' cellpadding='0'>{head}{''.join(body)}</table>")

    def _selected(self):
        r = self.table.currentRow()
        return self._rows[r] if 0 <= r < len(self._rows) else None

    def add_line(self) -> None:
        p = self.perf()
        if p and CapexLineDialog(self.pf, p.holding, parent=self).exec():
            self.fill()

    def add_unit(self) -> None:
        p = self.perf()
        if p and AddUnitDialog(self.pf, p.holding, self).exec():
            self.fill()

    def edit_line(self) -> None:
        p, ln = self.perf(), self._selected()
        if p and ln and CapexLineDialog(self.pf, p.holding, ln, self).exec():
            self.fill()

    def delete_line(self) -> None:
        ln = self._selected()
        units = sum(1 for x in self._rows if x is not None and x.parent_id == ln.id) if ln else 0
        extra = f" and its {units} unit(s)" if units else ""
        note = ("" if ln is None or ln.parent_id is None or not ln.unit or
                not any(u.unit.strip().lower() == ln.unit.strip().lower() for u in op.units(self.pf, self.perf().holding.id))
                else " It's on the rent roll, so it comes back unless the unit is removed from the rent roll too.")
        if ln and confirm(self, "Delete line", f"Delete “{ln.label}”{extra} from the capex schedule? (The books "
                                               f"aren't touched.){note}"):
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
        units = sum(1 for x in self._rows if x is not None and x.parent_id == ln.id)
        more = (f" Each of its {units} units has a sub-account under it, so the balance sheet shows a line per unit "
                "— categorize a unit's payments to its own account." if units else "")
        info(self, "Linked to the books", f"“CapEx – {ln.label}” is in {p.holding.name}'s chart of accounts. "
                                          f"Categorize the payments for it to that account and they show here.{more}")
        self.fill()


class AddUnitDialog(QDialog):
    """A unit that isn't on the rent roll yet (e.g. newly built) under in-suite renovations."""

    def __init__(self, pf, holding, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QCheckBox

        self.pf, self.holding = pf, holding
        self.setWindowTitle(f"Add Unit – {holding.name}")
        self.resize(440, 0)
        form = QFormLayout(self)
        self.unit = QLineEdit()
        self.unit.setPlaceholderText("e.g. 108 — or several: 108, 109, 110")
        self.budget = _money_box()
        self.roll = QCheckBox("Also add to the rent roll (status Under construction)")
        self.roll.setChecked(True)
        form.addRow("Unit number:", self.unit)
        form.addRow("Budget per unit:", self.budget)
        form.addRow(self.roll)
        hint = QLabel("Every unit on the rent roll is already listed under In-suite renovations — add a unit here "
                      "when it isn't on the rent roll yet, e.g. one being built.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setMinimumHeight(hint.fontMetrics().lineSpacing() * 2)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        import re

        names = [x.strip() for x in re.split(r"[,;]", self.unit.text()) if x.strip()]
        if not names:
            error(self, "Couldn't add", "Enter the unit number")
            return
        try:
            for u in names:
                cx.add_unit(self.pf, self.holding.id, u, round(self.budget.value() * 100),
                            to_rent_roll=self.roll.isChecked())
        except ValueError as e:
            error(self, "Couldn't add", str(e))
            return
        self.accept()


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------


def _whole(cents) -> str:
    """Whole dollars, negatives in brackets, nothing as a dash."""
    if cents is None:
        return ""
    d = round(cents / 100)
    if d == 0:
        return "–"
    return f"({abs(d):,})" if d < 0 else f"{d:,}"


HISTORY_MONTHS = 36   # months of history loaded (scroll left to see them)
SHOW_BACK = 3         # the page opens on the 3 months before this one, this month and the year ahead


def pf_actual_parts(widget, months, past, rows, show: str, note: str) -> tuple[str, str, str, str]:
    """(note, labels column html, months html, the month to open on) — months across ("Nov 25"), each with
    Pro-Forma and / or Actual columns; categories down; no lines. The labels are a separate column so they stay put
    while the months scroll."""
    import html as _html

    from .theme import c as tc
    from .theme import is_dark

    text, muted, red, accent = widget.palette().text().color().name(), tc("muted"), tc("red"), tc("link")
    pf_bg, act_bg = ("#3a2f3a", "#23352b") if is_dark() else ("#fbeef7", "#eaf6ee")
    tint = "#20303c" if is_dark() else "#eef6fb"
    esc = _html.escape
    # leave out the oldest months the books and the projection have nothing for
    current = max((i for i, pa in enumerate(past) if pa), default=0)
    default_start = max(0, current - SHOW_BACK)
    first = default_start
    for i in range(default_start):
        if any(r.actual[i] for r in rows if r.kind not in ("gap", "head", "ratio")):   # the books begin
            first = i
            break
    cols = []   # (month index, 'pro_forma' | 'actual')
    for i, pa in enumerate(past):
        if i < first:
            continue
        if show in ("both", "pro_forma"):
            cols.append((i, "pro_forma"))
        if show in ("both", "actual") and pa:
            cols.append((i, "actual"))
    head1, head2 = [], []
    i_prev = None
    for i, kind in cols:
        span = sum(1 for j, _k in cols if j == i)
        if i != i_prev:
            head1.append(f"<td colspan='{span}' align='center' style='font-weight:700; color:{text}; "
                         f"padding:2px 6px'>{months[i].strftime('%b %y')}</td>")
        i_prev = i
        label, bg = ("Pro-Forma", pf_bg) if kind == "pro_forma" else ("Actual", act_bg)
        head2.append(f"<td align='right' style='background:{bg}; color:{muted}; font-size:8pt; "
                     f"padding:2px 8px'>{label}</td>")
    table = f"<table cellspacing='0' cellpadding='0' style='color:{text}'>"
    left = [table, "<tr><td style='font-weight:700; padding:2px 6px'>&nbsp;</td></tr>",
            "<tr><td style='font-size:8pt; padding:2px 8px'>&nbsp;</td></tr>"]
    body = [table, "<tr>" + "".join(head1) + "</tr>", "<tr>" + "".join(head2) + "</tr>"]
    for r in rows:
        if r.kind == "gap":
            gap = "<tr><td colspan='{}' style='font-size:4pt; padding:2px 0'>&nbsp;</td></tr>"
            left.append(gap.format(1))
            body.append(gap.format(max(1, len(cols))))
            continue
        bold = " font-weight:700;" if r.kind in ("total", "head") else ""
        pad = {0: 0, 1: 18, 2: 34}.get(r.level, 0)
        colour = accent if r.kind == "head" else (text if r.level == 0 else muted)
        left.append(f"<tr><td style='padding:2px 24px 2px {pad + 2}px; white-space:nowrap; color:{colour};{bold}'>"
                    f"{esc(r.label)}</td></tr>")
        cells = []
        for i, kind in cols:
            v = (r.pro_forma if kind == "pro_forma" else r.actual)[i]
            if r.kind == "head":
                s = "&nbsp;"
            elif r.kind == "ratio":
                s = f"{v:.2f}x" if v is not None else "&nbsp;"
            else:
                s = _whole(v) or "&nbsp;"
            c = red if s.startswith("(") else text
            bg = f" background:{tint};" if kind == "actual" and show == "both" else ""
            cells.append(f"<td align='right' style='padding:2px 8px; color:{c};{bg}{bold}'>{s}</td>")
        body.append("<tr>" + "".join(cells) + "</tr>")
    left.append("</table>")
    body.append("</table>")
    start = months[default_start].strftime("%b %y") if months else ""
    return (f"<span style='color:{muted}; font-size:8.5pt'>{esc(note)}</span>", "".join(left), "".join(body), start)


class MonthTable(QWidget):
    """A months-across statement: the line names stay on the left while the months scroll sideways. Opens on
    ``SHOW_BACK`` months before this one; scroll left for earlier months."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTextBrowser

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.note = QLabel()
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        row = QHBoxLayout()
        row.setSpacing(0)
        lay.addLayout(row, 1)
        self.labels = QTextBrowser()
        self.body = QTextBrowser()
        for b in (self.labels, self.body):
            b.setFrameShape(QTextBrowser.Shape.NoFrame)
            b.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        self.labels.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.labels.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        left.addWidget(self.labels, 1)
        left.addSpacing(self.body.horizontalScrollBar().sizeHint().height())   # lines up with the months
        row.addLayout(left)
        row.addWidget(self.body, 1)
        self.body.verticalScrollBar().valueChanged.connect(self.labels.verticalScrollBar().setValue)
        self.labels.verticalScrollBar().valueChanged.connect(self.body.verticalScrollBar().setValue)
        self._start = ""

    def set_parts(self, note: str, labels: str, body: str, start: str) -> None:
        self.note.setText(note)
        self.note.setVisible(bool(note))
        self.labels.setHtml(labels)
        self.labels.setFixedWidth(int(self.labels.document().idealWidth()) + 6)
        self.body.setHtml(body)
        self._start = start
        self._go_to_start()
        QTimer.singleShot(0, self._go_to_start)

    def setHtml(self, html: str) -> None:   # a message instead of a statement
        self.note.setText(html)
        self.note.setVisible(bool(html))
        self.labels.setHtml("")
        self.labels.setFixedWidth(0)
        self.body.setHtml("")
        self._start = ""

    def toPlainText(self) -> str:
        return "\n".join((self.note.text(), self.labels.toPlainText(), self.body.toPlainText()))

    def toHtml(self) -> str:
        return self.labels.toHtml() + self.body.toHtml()

    def _go_to_start(self) -> None:
        """Scroll so the first month on screen is the one the page opens on."""
        if not self._start:
            return
        cur = self.body.document().find(self._start)
        if cur.isNull():
            return
        bar = self.body.horizontalScrollBar()
        # the month's heading cell spans its columns: its left edge is where the month starts
        left = self.body.document().documentLayout().blockBoundingRect(cur.block()).left()
        bar.setValue(max(0, int(left) - 10))

    def showEvent(self, e) -> None:
        super().showEvent(e)
        QTimer.singleShot(0, self._go_to_start)


class CashFlowPage(_CompanyPage):
    """The master cash flow: months across (each with Pro-Forma and Actual), categories down — one clean page."""

    def __init__(self, view):
        super().__init__(view)

        from .asset_mgmt import Card

        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        row.addWidget(self.pick)
        row.addSpacing(12)
        row.addWidget(QLabel("Show:"))
        self.show_cols = QComboBox()
        for label, key in (("Pro-Forma & Actual", "both"), ("Actual", "actual"), ("Pro-Forma", "pro_forma")):
            self.show_cols.addItem(label, key)
        self.show_cols.currentIndexChanged.connect(lambda *_: self.fill())
        row.addWidget(self.show_cols)
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
        self.page = MonthTable()
        lay.addWidget(self.page, 1)
        self.flow: op.MasterCashFlow | None = None

    def fill(self) -> None:


        p = self.perf()
        for c in self.cards.values():
            c.set("—")
        if p is None:
            self.flow = None
            self.page.setHtml("<p>No operating companies in this view.</p>")
            return
        as_of = self.view._as_of_date()
        f = self.flow = op.master_cash_flow(self.pf, p, as_of, back=HISTORY_MONTHS)
        self.page.set_parts(*self._parts(f, self.show_cols.currentData() or "both"))
        by = {r.label: r for r in f.rows}
        net = by["Net property cash flow"]
        last12 = [i for i, pa in enumerate(f.past) if pa][-12:]
        act = sum(net.actual[i] for i in last12 if net.actual[i] is not None)
        proj = sum(v for v, pa in zip(net.pro_forma, f.past) if not pa)
        self.cards["t12"].set(fmt_money(act), "actual, last 12 months",
                              POSITIVE if act > 0 else NEGATIVE if act < 0 else None)
        self.cards["n12"].set(fmt_money(proj), "pro-forma", POSITIVE if proj > 0 else NEGATIVE if proj < 0 else None)
        ending = by["Ending balance"]
        now = [v for v, pa in zip(ending.actual, f.past) if pa and v is not None]
        if now:
            self.cards["cash"].set(fmt_money(now[-1]), "in the bank (books)")
        future = [(v, m) for v, m, pa in zip(ending.pro_forma, f.months, f.past) if not pa]
        if future:
            low, when = min(future)
            self.cards["low"].set(fmt_money(low), when.strftime("%b %Y"), NEGATIVE if low < 0 else None)
        owners = [x for x in pfm.cap_table(self.pf, p) if not x.worked_out]
        if owners:
            self.cards["equity"].set(fmt_money(sum(x.funded for x in owners)),
                                     f"of {fmt_money(sum(x.amount_cents for x in owners))} committed")

    def _parts(self, f: op.MasterCashFlow, show: str) -> tuple[str, str, str, str]:
        note = ("Pro-Forma: rent from the rent roll ahead (annual increases, less vacancy), other lines as the same "
                "month a year earlier, scheduled debt payments, capex to complete spread over the coming months. "
                "Actual: the books — the ending balance is the bank balance; what the lines don't explain is "
                "Other / timing.")
        return pf_actual_parts(self, f.months, f.past, f.rows, show, note)


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
                           ("Weekly Notes…", self.edit_notes), ("Add Valuation…", self.add_valuation),
                           ("Edit Holding…", self.edit_holding), ("Open Company Books", self.open_books)):
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

    def add_valuation(self) -> None:
        from .asset_mgmt import ValuationDialog

        p = self.perf()
        if p and ValuationDialog(self.pf, p.holding, self).exec():
            self.view.refresh()

    def edit_holding(self) -> None:
        from .asset_mgmt import HoldingDialog

        p = self.perf()
        if p and HoldingDialog(self.pf, self.view.companies_fn(), p.holding, self,
                               create_company=self.view.create_company).exec():
            self.view.refresh()

    def open_books(self) -> None:
        p = self.perf()
        if p and p.holding.company_path:
            self.view.open_company.emit(p.holding.company_path)
        elif p:
            error(self, "Not linked", "This company isn't linked to its books yet — use Edit Holding….")

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
                      ["Total debt", fmt_money(p.debt)], ["LTV", f"{p.ltv * 100:.1f}%" if p.ltv is not None else "—"],
                      [f"NAV ({h.ownership_pct:g}% owned)", fmt_money(p.nav)]]
        d["returns"] = [["Invested", fmt_money(p.invested)], ["Distributions to date", fmt_money(p.returned)],
                        ["Total profit", fmt_money(p.gain)], ["MOIC", f"{p.moic:.2f}x" if p.moic is not None else "—"],
                        ["IRR", f"{p.irr * 100:.1f}%" if p.irr is not None else "—"],
                        ["Cash on cash", f"{p.cash_on_cash * 100:.1f}%" if p.cash_on_cash is not None else "—"]]
        fin = p.financials if p.has_books else None
        d["operations"] = ([["Revenue", fmt_money(fin.revenue_ltm)], ["Op. Ex", fmt_money(fin.opex_ltm)],
                            ["EBITDA", fmt_money(fin.ebitda_ltm)], ["Non-Op. Ex", fmt_money(fin.nonop_ltm)],
                            ["CapEx", fmt_money(p.capex_req)],
                            ["Net cash flow", fmt_money(p.net_cash_flow) if p.net_cash_flow is not None else "—"]]
                           if fin else [])
        loans = pfm.loans(self.pf, h, as_of)
        d["financing"] = [[ln.lender, fmt_money(ln.balance), f"{ln.rate_pct:.2f}%" if ln.rate_pct is not None else "—",
                           ln.maturity_date or "—"] for ln in loans]
        if loans:
            ft = pfm.financing_totals(loans)
            d["financing"].append(["Total", fmt_money(ft.balance), f"{ft.rate:.2f}%" if ft.rate is not None else "",
                                   ft.next_maturity or ""])
        vals = list(reversed(self.pf.valuations(h.id)))[:4]
        d["valuations"] = []
        for v_ in vals:
            cv, _debt, eq, _ = pfm.valuation_figures(self.pf, h, v_)
            d["valuations"].append([v_.date, fmt_money(cv if cv is not None else eq), fmt_money(eq), v_.method])
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
                                 f"{esc(x) or '&nbsp;'}</td>")   # an empty cell would drop the gap below
                out.append("<tr>" + "".join(cells) + "</tr>")
            out.append("</table>")
            return "".join(out)

        late_rows = {r for r, m in enumerate(ms) if m.original and m.current and m.current > m.original}
        left = (section("Property", d["property"]) +
                section("Timeline", d["timeline"], ["", "Original", "Current"], late=late_rows,
                        empty="No milestones yet — Timeline…") +
                section("Value", d["value"], bold_last=True) + section("Returns", d["returns"]))
        mid = (section("Rental status", d["rental"], ["", "Last 12 months", "Run-rate", "Next 12"], bold_last=True) +
               section("Operations", d["operations"], sub="last 12 months, from the books",
                       empty="Not linked to its books") +
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
                 section("Financing", d["financing"], ["", "Balance", "Rate", "Maturity"], bold_last=bool(loans),
                         empty="No loans (Financing & Cap Table)") +
                 section("Valuations", d["valuations"], ["", "Company value", "Equity", "Method"],
                         empty="No valuations yet — Add Valuation…") +
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
