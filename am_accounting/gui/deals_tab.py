"""Deal Pipeline: new deals, deals under due diligence and deals under contract, side by side."""

from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import deals as dl
from ..money import fmt_money
from .common import MUTED, NEGATIVE, confirm, error, info

NO_DATE = QDate(2000, 1, 1)


def _date_box(iso: str | None) -> QDateEdit:
    """A date that can be left blank ("—")."""
    w = QDateEdit()
    w.setCalendarPopup(True)
    w.setDisplayFormat("MMM d, yyyy")
    w.setMinimumDate(NO_DATE)
    w.setSpecialValueText("—")
    if iso:
        d = date.fromisoformat(iso)
        w.setDate(QDate(d.year, d.month, d.day))
    else:
        w.setDate(NO_DATE)
    return w


def _date_of(w: QDateEdit) -> str | None:
    return None if w.date() == NO_DATE else w.date().toString("yyyy-MM-dd")


def _short_date(iso: str | None) -> str:
    """"Nov 12" this year, "Nov 12, 2027" otherwise."""
    if not iso:
        return "—"
    d = date.fromisoformat(iso)
    return d.strftime("%b %d") if d.year == date.today().year else d.strftime("%b %d, %Y")


class DealDialog(QDialog):
    """A deal: details, its due-diligence checklist and notes."""

    def __init__(self, pf, deal: dl.Deal | None = None, parent=None):
        super().__init__(parent)
        from .asset_mgmt import _money_box

        self.pf = pf
        self.deal = d = deal or dl.Deal("", received=date.today().isoformat())
        self.setWindowTitle(f"Deal – {d.name}" if d.name else "New Deal")
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # details: two columns
        det = QWidget()
        cols = QHBoxLayout(det)
        left, right = QFormLayout(), QFormLayout()
        cols.addLayout(left, 1)
        cols.addSpacing(18)
        cols.addLayout(right, 1)
        self.name = QLineEdit(d.name)
        self.name.setPlaceholderText("e.g. 45 Elm Street Apartments")
        self.address = QLineEdit(d.address)
        self.asset_type = QComboBox()
        self.asset_type.setEditable(True)
        self.asset_type.addItems(dl.ASSET_TYPES)
        self.asset_type.setCurrentText(d.asset_type)
        self.stage = QComboBox()
        self.stage.addItems(dl.STAGES)
        self.stage.setCurrentText(d.stage)
        self.price = _money_box()
        self.price.setValue(d.price_cents / 100)
        self.price.setToolTip("Asking price for a new deal; the offer / agreed price once under contract")
        self.units = QSpinBox()
        self.units.setRange(0, 100_000)
        self.units.setValue(d.units)
        self.sqft = QDoubleSpinBox()
        self.sqft.setRange(0, 1e9)
        self.sqft.setDecimals(0)
        self.sqft.setGroupSeparatorShown(True)
        self.sqft.setSuffix(" sq ft")
        self.sqft.setValue(d.sqft or 0)
        self.noi = _money_box()
        self.noi.setValue(d.noi_cents / 100)
        self.equity = _money_box()
        self.equity.setValue(d.equity_cents / 100)
        self.metrics = QLabel()
        self.metrics.setObjectName("hint")
        for label, w in (("Deal name:", self.name), ("Address:", self.address), ("Asset type:", self.asset_type),
                         ("Stage:", self.stage), ("Price:", self.price), ("Units:", self.units), ("Size:", self.sqft),
                         ("NOI (a year):", self.noi), ("Equity needed:", self.equity), ("", self.metrics)):
            left.addRow(label, w)
        self.source = QLineEdit(d.source)
        self.source.setPlaceholderText("Broker, off-market, referral…")
        self.contact = QLineEdit(d.contact)
        self.contact.setPlaceholderText("Name, phone, email")
        self.lead = QLineEdit(d.lead)
        self.lead.setPlaceholderText("Who on our side")
        self.received = _date_box(d.received)
        self.offer = _date_box(d.offer_date)
        self.dd_deadline = _date_box(d.dd_deadline)
        self.firm = _date_box(d.firm_date)
        self.closing = _date_box(d.closing_date)
        self.deposit = _money_box()
        self.deposit.setValue(d.deposit_cents / 100)
        self.next_step = QLineEdit(d.next_step)
        self.next_step.setPlaceholderText("e.g. Send LOI by Friday")
        for label, w in (("Source:", self.source), ("Contact:", self.contact), ("Deal lead:", self.lead),
                         ("Received:", self.received), ("Offer / LOI:", self.offer),
                         ("Due-diligence deadline:", self.dd_deadline), ("Firm (conditions waived):", self.firm),
                         ("Closing:", self.closing), ("Deposit:", self.deposit), ("Next step:", self.next_step)):
            right.addRow(label, w)
        tabs.addTab(det, "Details")

        # due-diligence checklist
        ddw = QWidget()
        dlay = QVBoxLayout(ddw)
        self.progress = QLabel()
        dlay.addWidget(self.progress)
        self.dd = QTableWidget(0, 4)
        self.dd.setHorizontalHeaderLabels(["Done", "Item", "Due", "Note"])
        self.dd.verticalHeader().setVisible(False)
        self.dd.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.dd.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.dd.setColumnWidth(0, 50)
        self.dd.setColumnWidth(2, 140)
        dlay.addWidget(self.dd, 1)
        brow = QHBoxLayout()
        for text, slot in (("Add Item", lambda: self._add_item(dl.DDItem(""))), ("Remove Item", self._remove_item),
                           ("Add the Usual Items", self._usual)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            brow.addWidget(b)
        brow.addStretch(1)
        dlay.addLayout(brow)
        tabs.addTab(ddw, "Due Diligence")
        for it in (dl.checklist(pf, d.id) if d.id else []):
            self._add_item(it)

        # notes
        self.notes = QPlainTextEdit(d.notes)
        self.notes.setPlaceholderText("Anything about the deal: seller's story, pricing guidance, issues found…")
        tabs.addTab(self.notes, "Notes")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for w in (self.price, self.noi, self.equity):
            w.valueChanged.connect(self._update)
        self.units.valueChanged.connect(self._update)
        self._update()

    def _update(self, *_) -> None:
        price, noi, units = self.price.value(), self.noi.value(), self.units.value()
        parts = []
        if price and noi:
            parts.append(f"cap rate {noi / price * 100:.2f}%")
        if price and units:
            parts.append(f"{fmt_money(round(price * 100 / units))} per unit")
        if price and self.equity.value():
            parts.append(f"LTV {(price - self.equity.value()) / price * 100:.0f}% if the rest is debt")
        self.metrics.setText(" · ".join(parts))
        done = sum(1 for r in range(self.dd.rowCount()) if self.dd.cellWidget(r, 0).isChecked())
        self.progress.setText(f"<b>{done} of {self.dd.rowCount()}</b> done" if self.dd.rowCount() else
                              "No checklist yet — Add the Usual Items, or your own. It starts by itself when the deal "
                              "moves to due diligence.")

    def _add_item(self, it: dl.DDItem) -> None:
        r = self.dd.rowCount()
        self.dd.insertRow(r)
        box = QCheckBox()
        box.setChecked(it.done)
        box.toggled.connect(self._update)
        self.dd.setCellWidget(r, 0, box)
        self.dd.setItem(r, 1, QTableWidgetItem(it.item))
        self.dd.setCellWidget(r, 2, _date_box(it.due))
        self.dd.setItem(r, 3, QTableWidgetItem(it.note))
        self._update()

    def _remove_item(self) -> None:
        r = self.dd.currentRow()
        if r >= 0:
            self.dd.removeRow(r)
            self._update()

    def _usual(self) -> None:
        have = {(self.dd.item(r, 1).text() if self.dd.item(r, 1) else "").lower() for r in range(self.dd.rowCount())}
        for x in dl.DD_ITEMS:
            if x.lower() not in have:
                self._add_item(dl.DDItem(x, due=_date_of(self.dd_deadline)))

    def items(self) -> list[dl.DDItem]:
        out = []
        for r in range(self.dd.rowCount()):
            text = self.dd.item(r, 1).text() if self.dd.item(r, 1) else ""
            note = self.dd.item(r, 3).text() if self.dd.item(r, 3) else ""
            out.append(dl.DDItem(text, self.dd.cellWidget(r, 0).isChecked(), _date_of(self.dd.cellWidget(r, 2)), note))
        return out

    def save(self) -> None:
        d = self.deal
        d.name, d.address, d.asset_type = self.name.text(), self.address.text().strip(), self.asset_type.currentText()
        d.stage = self.stage.currentText()
        d.price_cents, d.noi_cents = round(self.price.value() * 100), round(self.noi.value() * 100)
        d.equity_cents, d.deposit_cents = round(self.equity.value() * 100), round(self.deposit.value() * 100)
        d.units, d.sqft = self.units.value(), (self.sqft.value() or None)
        d.source, d.contact, d.lead = self.source.text().strip(), self.contact.text().strip(), self.lead.text().strip()
        d.received, d.offer_date = _date_of(self.received), _date_of(self.offer)
        d.dd_deadline, d.firm_date, d.closing_date = (_date_of(self.dd_deadline), _date_of(self.firm),
                                                      _date_of(self.closing))
        d.next_step, d.notes = self.next_step.text().strip(), self.notes.toPlainText().strip()
        try:
            new = d.id is None
            dl.save(self.pf, d)
            items = self.items()
            if new and d.stage == "Due Diligence" and not items:
                dl.start_checklist(self.pf, d.id)
            else:
                dl.save_checklist(self.pf, d.id, items)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class CloseDealDialog(QDialog):
    """Close a deal under contract: set up its new operating company and its opening balance sheet — the purchase
    price and closing costs, the debt and the equity (see closing.py)."""

    def __init__(self, pf, deal: dl.Deal, create_company, parent=None):
        super().__init__(parent)
        from .. import closing as cl
        from .. import portfolio as pfm

        self.pf, self.deal, self.create_company = pf, deal, create_company
        self.holding_id: int | None = None
        s = cl.default_setup(deal)
        self.setWindowTitle(f"Close Deal – {deal.name}")
        self.resize(980, 620)
        lay = QVBoxLayout(self)
        intro = QLabel(f"Closing <b>{html.escape(deal.name)}</b> sets up a new operating company with its own books "
                       "and posts its opening balance sheet: the property at what it cost (price + closing costs), "
                       "the loans, the owners' equity, and any cash left over in the bank. It's added to the portfolio "
                       "with the loans on Financing & Cap Table and the owners on the cap table.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        tabs = self.tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # 1 company
        cw = QWidget()
        form = QFormLayout(cw)
        self.company = QLineEdit()
        self.company.setPlaceholderText("The corporation, e.g. 1001234567 Ontario Inc.")
        self.prop = QLineEdit(s.property_name)
        self.entity = QComboBox()
        self.entity.setEditable(True)
        self.entity.addItems(["Corporation", "Limited partnership", "Partnership", "LLC", "Trust"])
        self.fiscal = QComboBox()
        for m in range(1, 13):
            self.fiscal.addItem(date(2000, m, 1).strftime("%B"), m)
        self.closing = _date_box(s.closing_date)
        self.own = QDoubleSpinBox()
        self.own.setRange(0.01, 100)
        self.own.setDecimals(2)
        self.own.setSuffix(" %")
        self.own.setValue(100)
        self.own.setToolTip("The portfolio's share of the company")
        for label, w in (("Company (legal name):", self.company), ("Property name:", self.prop),
                         ("Entity type:", self.entity), ("Fiscal year starts:", self.fiscal),
                         ("Closing date:", self.closing), ("Portfolio's share:", self.own)):
            form.addRow(label, w)
        tabs.addTab(cw, "1 · Company")

        # 2 purchase & closing costs
        uw = QWidget()
        ul = QVBoxLayout(uw)
        self.costs = QTableWidget(0, 2)
        self.costs.setHorizontalHeaderLabels(["Cost", "Amount"])
        self.costs.verticalHeader().setVisible(False)
        self.costs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.costs.setColumnWidth(1, 200)
        ul.addWidget(self.costs, 1)
        crow = QHBoxLayout()
        add = QPushButton("Add Cost")
        add.clicked.connect(lambda: self._add_cost("", 0))
        rem = QPushButton("Remove Cost")
        rem.clicked.connect(lambda: self._remove(self.costs))
        ltt = QPushButton("Work Out Land Transfer Tax (Ontario)")
        ltt.setToolTip("Ontario's provincial land transfer tax on the purchase price — an estimate; Toronto adds its "
                       "own municipal tax. Check the figure with your lawyer.")
        ltt.clicked.connect(self._ltt)
        self.single = QCheckBox("1–2 single-family homes")
        self.land = QDoubleSpinBox()
        self.land.setRange(0, 99)
        self.land.setDecimals(1)
        self.land.setSuffix(" % land")
        self.land.setToolTip("Share of the purchase price that is land (not depreciated); the rest is the building")
        for w in (add, rem, ltt, self.single):
            crow.addWidget(w)
        crow.addStretch(1)
        crow.addWidget(QLabel("Purchase price split:"))
        crow.addWidget(self.land)
        ul.addLayout(crow)
        tabs.addTab(uw, "2 · Purchase && Closing Costs")
        for label, cents in s.costs:
            self._add_cost(label, cents)

        # 3 debt
        dw = QWidget()
        dlay = QVBoxLayout(dw)
        self.loans = QTableWidget(0, 7)
        self.loans.setHorizontalHeaderLabels(["Lender", "Facility", "Amount", "Rate", "Term (yrs)",
                                              "Amortization (yrs)", "Interest only"])
        self.loans.verticalHeader().setVisible(False)
        self.loans.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        dlay.addWidget(self.loans, 1)
        drow = QHBoxLayout()
        b = QPushButton("Add Loan")
        b.clicked.connect(lambda: self._add_loan(pfm.Loan("", "Mortgage", 0, term_months=60, amort_months=300)))
        drow.addWidget(b)
        b = QPushButton("Remove Loan")
        b.clicked.connect(lambda: self._remove(self.loans))
        drow.addWidget(b)
        drow.addStretch(1)
        dlay.addLayout(drow)
        tabs.addTab(dw, "3 · Debt")
        for ln in s.loans or [pfm.Loan("", "Mortgage", 0, term_months=60, amort_months=300)]:
            self._add_loan(ln)

        # 4 equity
        ew = QWidget()
        elay = QVBoxLayout(ew)
        self.eq = QTableWidget(0, 5)
        self.eq.setHorizontalHeaderLabels(["Owner", "Through company", "Class", "Amount", "As"])
        self.eq.verticalHeader().setVisible(False)
        self.eq.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.eq.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        elay.addWidget(self.eq, 1)
        erow = QHBoxLayout()
        b = QPushButton("Add Owner")
        b.clicked.connect(lambda: self._add_equity(cl.EquityLine("", 0)))
        erow.addWidget(b)
        b = QPushButton("Remove Owner")
        b.clicked.connect(lambda: self._remove(self.eq))
        erow.addWidget(b)
        b = QPushButton("Equity Covers the Rest")
        b.setToolTip("Set the selected owner's amount so the money in equals closing costs")
        b.clicked.connect(self._equity_rest)
        erow.addWidget(b)
        erow.addStretch(1)
        elay.addLayout(erow)
        hint = QLabel("Share capital is equity (by class); a shareholder loan is owed back to the owner (a liability). "
                      "Both are on the cap table as money put in on closing.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        elay.addWidget(hint)
        tabs.addTab(ew, "4 · Equity")
        for e in s.equity or [cl.EquityLine("", 0)]:
            self._add_equity(e)

        # 5 review
        self.review = QLabel()
        self.review.setTextFormat(Qt.TextFormat.RichText)
        self.review.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.review.setWordWrap(True)
        rv = QWidget()
        QVBoxLayout(rv).addWidget(self.review)
        tabs.addTab(rv, "5 · Review")
        tabs.currentChanged.connect(self._update)

        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.totals)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Close Deal && Create Company", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setObjectName("primary")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update()

    # ---- rows ----
    def _money(self, cents: int):
        from .asset_mgmt import _money_box

        box = _money_box()
        box.setValue(cents / 100)
        box.valueChanged.connect(self._update)
        return box

    def _add_cost(self, label: str, cents: int) -> None:
        r = self.costs.rowCount()
        self.costs.insertRow(r)
        self.costs.setItem(r, 0, QTableWidgetItem(label))
        self.costs.setCellWidget(r, 1, self._money(cents))
        self._update()

    def _add_loan(self, ln) -> None:
        from .. import portfolio as pfm

        t = self.loans
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(ln.lender))
        kind = QComboBox()
        kind.addItems(pfm.LOAN_KINDS)
        kind.setCurrentText(ln.kind)
        t.setCellWidget(r, 1, kind)
        t.setCellWidget(r, 2, self._money(ln.principal_cents))
        rate = QDoubleSpinBox()
        rate.setRange(0, 100)
        rate.setDecimals(3)
        rate.setSuffix(" %")
        rate.setValue(ln.rate_pct or 0)
        t.setCellWidget(r, 3, rate)
        for c, months in ((4, ln.term_months), (5, ln.amort_months or ln.term_months)):
            y = QDoubleSpinBox()
            y.setRange(0, 50)
            y.setDecimals(1)
            y.setValue(months / 12)
            t.setCellWidget(r, c, y)
        io = QCheckBox()
        io.setChecked(ln.repayment == "Interest only")
        t.setCellWidget(r, 6, io)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._update()

    def _add_equity(self, e) -> None:
        from .. import closing as cl

        t = self.eq
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(e.owner))
        t.setItem(r, 1, QTableWidgetItem(e.company))
        cls = QComboBox()
        cls.setEditable(True)
        cls.addItems(["Class A", "Class B", "Common", "Preferred"])
        cls.setCurrentText(e.share_class)
        t.setCellWidget(r, 2, cls)
        t.setCellWidget(r, 3, self._money(e.amount_cents))
        form = QComboBox()
        form.addItems(cl.EQUITY_FORMS)
        form.setCurrentText(e.form)
        t.setCellWidget(r, 4, form)
        t.setColumnWidth(3, 170)
        self._update()

    def _remove(self, t: QTableWidget) -> None:
        r = t.currentRow()
        if r >= 0:
            t.removeRow(r)
            self._update()

    def _ltt(self) -> None:
        from .. import closing as cl

        s = self.setup()
        for r in range(self.costs.rowCount()):
            if (self.costs.item(r, 0).text() if self.costs.item(r, 0) else "") == "Land transfer tax":
                self.costs.cellWidget(r, 1).setValue(cl.ontario_ltt(s.price, single_family=self.single.isChecked())
                                                     / 100)
                return
        self._add_cost("Land transfer tax", cl.ontario_ltt(s.price, single_family=self.single.isChecked()))

    def _equity_rest(self) -> None:
        r = max(0, self.eq.currentRow())
        if self.eq.rowCount() == 0:
            return
        box = self.eq.cellWidget(r, 3)
        s = self.setup()
        box.setValue(max(0, box.value() * 100 - s.cash_left) / 100)

    # ---- the setup as entered ----
    def setup(self):
        from .. import closing as cl
        from .. import portfolio as pfm

        costs = []
        for r in range(self.costs.rowCount()):
            label = self.costs.item(r, 0).text().strip() if self.costs.item(r, 0) else ""
            cents = round(self.costs.cellWidget(r, 1).value() * 100)
            if label or cents:
                costs.append((label or "Other closing costs", cents))
        loans = []
        for r in range(self.loans.rowCount()):
            t = self.loans
            amt = round(t.cellWidget(r, 2).value() * 100)
            if not amt:
                continue
            io = t.cellWidget(r, 6).isChecked()
            term, amort = round(t.cellWidget(r, 4).value() * 12), round(t.cellWidget(r, 5).value() * 12)
            loans.append(pfm.Loan(t.item(r, 0).text().strip() if t.item(r, 0) else "", t.cellWidget(r, 1).currentText(),
                                  amt, rate_pct=t.cellWidget(r, 3).value() or None, start_date=_date_of(self.closing),
                                  term_months=term, repayment="Interest only" if io else "Amortizing",
                                  amort_months=0 if io or amort == term else amort))
        equity = []
        for r in range(self.eq.rowCount()):
            t = self.eq
            amt = round(t.cellWidget(r, 3).value() * 100)
            owner = t.item(r, 0).text().strip() if t.item(r, 0) else ""
            comp = t.item(r, 1).text().strip() if t.item(r, 1) else ""
            if amt or owner or comp:
                equity.append(cl.EquityLine(owner, amt, t.cellWidget(r, 2).currentText().strip(), comp,
                                            t.cellWidget(r, 4).currentText()))
        return cl.ClosingSetup(self.company.text(), self.prop.text(), _date_of(self.closing) or "", costs, loans,
                               equity, self.land.value(), self.entity.currentText(), self.fiscal.currentData(),
                               self.own.value())

    def _update(self, *_) -> None:
        from .theme import c as tc

        if not hasattr(self, "totals"):
            return
        s = self.setup()
        left = s.cash_left
        tail = (f"<span style='color:{tc('red')}'>short by <b>{fmt_money(-left)}</b></span>" if left < 0 else
                f"cash left for the bank <b>{fmt_money(left)}</b>")
        self.totals.setText(f"Uses <b>{fmt_money(s.uses)}</b> · Debt <b>{fmt_money(s.debt)}</b> · Equity "
                            f"<b>{fmt_money(s.equity_total)}</b> · {tail}"
                            + (f" · LTV {s.debt / s.price * 100:.0f}%" if s.price else ""))
        if self.tabs.currentIndex() == 4:
            self.review.setText(self._review(s))

    def _review(self, s) -> str:
        esc = html.escape
        p = esc(s.property_name or "the property")
        rows = []
        for label, cents in s.costs:
            if not cents:
                continue
            if label == "Purchase price" and s.land_pct:
                land = round(cents * s.land_pct / 100)
                rows += [(f"Property – {p} – Land", land, 0), (f"Property – {p} – Building", cents - land, 0)]
            else:
                rows.append((f"Property – {p} – {esc(label)}", cents, 0))
        if s.cash_left > 0:
            rows.append(("Bank (starting cash)", s.cash_left, 0))
        for ln in s.loans:
            rows.append((f"{esc(ln.kind)} – {esc(ln.lender or '?')}", 0, ln.principal_cents))
        classes: dict[str, int] = {}
        for e in s.equity:
            if e.form == "Shareholder loan":
                rows.append((f"Shareholder Loan – {esc(e.owner or e.company or '?')}", 0, e.amount_cents))
            else:
                classes[e.share_class] = classes.get(e.share_class, 0) + e.amount_cents
        rows += [(f"Share Capital – {esc(c)}", 0, v) for c, v in classes.items()]
        body = "".join(f"<tr><td style='padding:2px 24px 2px 0'>{a}</td><td align='right' style='padding:2px 12px'>"
                       f"{fmt_money(d) if d else ''}</td><td align='right' style='padding:2px 12px'>"
                       f"{fmt_money(c) if c else ''}</td></tr>" for a, d, c in rows)
        dr, cr = sum(r[1] for r in rows), sum(r[2] for r in rows)
        return (f"<p><b>{esc(s.company_name or 'The new company')}</b> — opening balance sheet on "
                f"{esc(s.closing_date)}:</p><table cellspacing='0'><tr><td></td><td align='right'><b>Debit</b></td>"
                f"<td align='right'><b>Credit</b></td></tr>{body}<tr><td><b>Total</b></td><td align='right'><b>"
                f"{fmt_money(dr)}</b></td><td align='right'><b>{fmt_money(cr)}</b></td></tr></table>"
                f"<p>Also: {p} in the portfolio ({s.ownership_pct:g}% ours) with the purchase price as its first "
                f"valuation; {len(s.loans)} loan(s) on Financing &amp; Cap Table; {len(s.equity)} owner(s) on the cap "
                f"table; sources &amp; uses filled in.</p>")

    def save(self) -> None:
        from .. import closing as cl

        s = self.setup()
        try:
            s.check()
        except ValueError as e:
            error(self, "Can't close yet", str(e))
            return
        try:
            self.holding_id = cl.close_deal(self.pf, self.deal.id, s, self.create_company)
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't close the deal", str(e))
            return
        self.accept()


STAGE_TITLES = {"New": "New deals", "Due Diligence": "Under due diligence", "Under Contract": "Under contract"}
STAGE_COLS = {"New": ["Deal", "Price", "Units", "Received"],
              "Due Diligence": ["Deal", "Price", "DD deadline", "Checklist"],
              "Under Contract": ["Deal", "Price", "Deposit", "Closing"],
              "Closed": ["Deal", "Stage", "Price", "Date", "In the portfolio"]}


class DealPipelinePage(QWidget):
    """New deals, under due diligence and under contract side by side (closed and dead deals below on request).
    Double-click a deal to open it; the buttons move the selected deal along."""

    def __init__(self, pf, parent=None, on_added=None, create_company=None):
        super().__init__(parent)
        from .asset_mgmt import Card, _table

        self.pf = pf
        self.on_added = on_added          # called with a new holding's id when a deal is added to the portfolio
        self.create_company = create_company   # (name, entity_type=, fiscal_year_start_month=) -> company file
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, slot, tip in (
                ("New Deal…", self.new_deal, "Add a deal you're looking at"),
                ("Open…", self.open_deal, "The selected deal's details, due-diligence checklist and notes"),
                ("◂ Back", self.move_back, "Move the selected deal back a stage"),
                ("Advance ▸", self.advance, "New → Due Diligence → Under Contract → Closed"),
                ("Mark Dead", self.mark_dead, "The deal didn't go ahead (kept under Closed & dead)"),
                ("Close Deal…", self.close_deal, "Under contract: close it — set up its new operating company and "
                                                 "opening balance sheet (price, closing costs, debt, equity)"),
                ("Delete", self.delete, "Delete the selected deal")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            if text == "New Deal…":
                b.setObjectName("primary")
            bar.addWidget(b)
        bar.addStretch(1)
        self.show_closed = QCheckBox("Show closed && dead")
        self.show_closed.toggled.connect(self.refresh)
        bar.addWidget(self.show_closed)
        lay.addLayout(bar)
        cards = QHBoxLayout()
        self.cards = {}
        for k, title in (("New", "New deals"), ("Due Diligence", "Under due diligence"),
                         ("Under Contract", "Under contract"), ("equity", "Equity needed"),
                         ("next", "Next closing")):
            c = Card(title, small=True)
            self.cards[k] = c
            cards.addWidget(c)
        lay.addLayout(cards)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tables: dict[str, QTableWidget] = {}
        self.heads: dict[str, QLabel] = {}
        self.rows: dict[str, list[dl.Deal]] = {}
        for stage in dl.PIPELINE:
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(0, 0, 6, 0)
            head = QLabel()
            head.setTextFormat(Qt.TextFormat.RichText)
            wl.addWidget(head)
            t = _table(STAGE_COLS[stage], 0)
            for c in range(1, len(STAGE_COLS[stage])):
                t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            t.doubleClicked.connect(lambda *_: self.open_deal())
            t.itemSelectionChanged.connect(lambda s=stage: self._only(s))
            wl.addWidget(t, 1)
            self.tables[stage], self.heads[stage] = t, head
            split.addWidget(w)
        lay.addWidget(split, 3)
        self.closed_box = QWidget()
        cl = QVBoxLayout(self.closed_box)
        cl.setContentsMargins(0, 6, 0, 0)
        self.heads["Closed"] = QLabel()
        self.heads["Closed"].setTextFormat(Qt.TextFormat.RichText)
        cl.addWidget(self.heads["Closed"])
        t = _table(STAGE_COLS["Closed"], 0)
        t.doubleClicked.connect(lambda *_: self.open_deal())
        t.itemSelectionChanged.connect(lambda: self._only("Closed"))
        cl.addWidget(t, 1)
        self.tables["Closed"] = t
        lay.addWidget(self.closed_box, 1)
        self.closed_box.setVisible(False)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setWordWrap(True)
        self.detail.setObjectName("hint")
        lay.addWidget(self.detail)
        self._picked = None
        self.refresh()

    # ---- filling ----
    def refresh(self, *_) -> None:
        from .asset_mgmt import short
        from .theme import c as tc

        keep = self.selected().id if self.selected() else None
        everything = dl.deals(self.pf)
        tot = dl.totals(everything)
        accent, muted = tc("link"), tc("muted")
        for stage in dl.PIPELINE:
            rows = self.rows[stage] = [d for d in everything if d.stage == stage]
            t = tot[stage]
            self.heads[stage].setText(
                f"<span style='color:{accent}; font-weight:700; font-size:9pt'>{STAGE_TITLES[stage].upper()}</span>"
                f" &nbsp;<span style='color:{muted}'>{t.count} · {short(t.price) if t.price else '$0'}</span>")
            self.cards[stage].set(str(t.count), f"{fmt_money(t.price)}" + (f" · {t.units:,} units" if t.units else ""))
            self._fill(stage, rows)
        live = [d for d in everything if d.stage in ("Due Diligence", "Under Contract")]
        eq = sum(d.equity_cents for d in live)
        self.cards["equity"].set(fmt_money(eq), "under DD and under contract")
        nxt = min((d for d in everything if d.stage == "Under Contract" and d.closing_date),
                  key=lambda d: d.closing_date, default=None)
        if nxt:
            days = (date.fromisoformat(nxt.closing_date) - date.today()).days
            self.cards["next"].set(_short_date(nxt.closing_date), f"{nxt.name} · " + (
                f"in {days} days" if days >= 0 else f"{-days} days ago"), NEGATIVE if 0 <= days <= 14 else None)
        else:
            self.cards["next"].set("—", "nothing under contract with a closing date")
        closed = [d for d in everything if d.stage in ("Closed", "Dead")]
        self.rows["Closed"] = closed
        self.heads["Closed"].setText(
            f"<span style='color:{accent}; font-weight:700; font-size:9pt'>CLOSED &amp; DEAD</span>"
            f" &nbsp;<span style='color:{muted}'>{tot['Closed'].count} closed · {tot['Dead'].count} dead</span>")
        self._fill("Closed", closed)
        self.closed_box.setVisible(self.show_closed.isChecked())
        if keep is not None:
            self.select(keep)
        self._show_detail()

    def _fill(self, stage: str, rows: list[dl.Deal]) -> None:
        from .asset_mgmt import short
        from .operations_tab import _cell

        t = self.tables[stage]
        t.blockSignals(True)
        t.setRowCount(len(rows))
        for r, d in enumerate(rows):
            days = d.days_to()
            soon = days is not None and days <= 7
            if stage == "New":
                vals = [d.name, short(d.price_cents) if d.price_cents else "—", str(d.units or ""),
                        _short_date(d.received)]
            elif stage == "Due Diligence":
                left = "" if days is None else (f" ({days} d)" if days >= 0 else " (past)")
                vals = [d.name, short(d.price_cents) if d.price_cents else "—",
                        _short_date(d.dd_deadline) + left, f"{d.dd_done} / {d.dd_total}" if d.dd_total else "—"]
            elif stage == "Under Contract":
                what, when = d.key_date
                left = "" if days is None else (f" ({days} d)" if days >= 0 else " (past)")
                vals = [d.name, short(d.price_cents) if d.price_cents else "—",
                        short(d.deposit_cents) if d.deposit_cents else "—",
                        (_short_date(when) + left) if when else "—"]
                if what == "Firm":
                    vals[3] = "firm " + vals[3]
            else:
                vals = [d.name, d.stage, fmt_money(d.price_cents) if d.price_cents else "—",
                        _short_date(d.closing_date if d.stage == "Closed" else d.stage_date),
                        "yes" if d.holding_id else ""]
            tip = " · ".join(x for x in (d.address, d.asset_type, d.source,
                                         f"next: {d.next_step}" if d.next_step else "") if x)
            for c, v in enumerate(vals):
                colour = MUTED if stage == "Closed" and (c == 4 or d.stage == "Dead") else None
                if soon and ((stage == "Due Diligence" and c == 2) or (stage == "Under Contract" and c == 3)):
                    colour = NEGATIVE
                it = _cell(v, right=c in (1, 2) and stage != "Closed" or (stage == "Closed" and c == 2),
                           colour=colour)
                it.setToolTip(tip)
                t.setItem(r, c, it)
        t.blockSignals(False)

    # ---- selection ----
    def _only(self, stage: str) -> None:
        """One deal selected on the page at a time."""
        t = self.tables[stage]
        if not t.selectedItems():
            return
        self._picked = stage
        for s, other in self.tables.items():
            if s != stage:
                other.blockSignals(True)
                other.clearSelection()
                other.blockSignals(False)
        self._show_detail()

    def selected(self) -> dl.Deal | None:
        s = self._picked
        if s is None or s not in self.tables:
            return None
        r = self.tables[s].currentRow()
        rows = self.rows.get(s, [])
        if not self.tables[s].selectedItems() or not 0 <= r < len(rows):
            return None
        return rows[r]

    def select(self, deal_id: int) -> None:
        for s, rows in self.rows.items():
            for r, d in enumerate(rows):
                if d.id == deal_id:
                    self.tables[s].selectRow(r)
                    self._picked = s
                    self._only(s)
                    return

    def _show_detail(self) -> None:
        d = self.selected()
        if d is None:
            self.detail.setText("Select a deal; double-click to open it.")
            return
        bits = [f"<b>{html.escape(d.name)}</b>", html.escape(d.stage)]
        if d.address:
            bits.append(html.escape(d.address))
        if d.cap_rate is not None:
            bits.append(f"cap rate {d.cap_rate * 100:.2f}%")
        if d.per_unit:
            bits.append(f"{fmt_money(d.per_unit)} / unit")
        if d.lead:
            bits.append(f"lead {html.escape(d.lead)}")
        if d.next_step:
            bits.append(f"next: {html.escape(d.next_step)}")
        self.detail.setText(" · ".join(bits))

    def _need(self) -> dl.Deal | None:
        d = self.selected()
        if d is None:
            info(self, "Pick a deal", "Select a deal first.")
        return d

    # ---- actions ----
    def new_deal(self) -> None:
        dlg = DealDialog(self.pf, parent=self)
        if dlg.exec():
            self.refresh()
            self.select(dlg.deal.id)

    def open_deal(self) -> None:
        d = self._need()
        if d and DealDialog(self.pf, dl.deal(self.pf, d.id), self).exec():
            self.refresh()
            self.select(d.id)

    def advance(self) -> None:
        d = self._need()
        if not d:
            return
        if d.stage == "Under Contract":
            self.close_deal()
            return
        if d.stage in ("Closed", "Dead"):
            return
        dl.advance(self.pf, d.id)
        self.refresh()
        self.select(d.id)

    def move_back(self) -> None:
        d = self._need()
        if d and d.stage != "New":
            dl.back(self.pf, d.id)
            self.refresh()
            self.select(d.id)

    def mark_dead(self) -> None:
        d = self._need()
        if d and confirm(self, "Dead deal", f"Mark “{d.name}” dead? (It's kept under Closed & dead.)"):
            dl.move(self.pf, d.id, "Dead")
            self.refresh()

    def close_deal(self) -> None:
        """Under contract → closed: the new company, its opening balance sheet, and into the portfolio."""
        d = self._need()
        if not d:
            return
        if d.holding_id is not None and any(h.id == d.holding_id for h in self.pf.holdings()):
            info(self, "Already closed", f"“{d.name}” is already in the portfolio.")
            return
        if d.stage not in ("Under Contract", "Closed"):
            info(self, "Not under contract", "Move the deal to Under Contract first (Advance ▸).")
            return
        if self.create_company is None:
            error(self, "Can't create companies here", "Open the Deal Pipeline from Asset Management.")
            return
        dlg = CloseDealDialog(self.pf, dl.deal(self.pf, d.id), self.create_company, self)
        if not dlg.exec():
            return
        self.show_closed.setChecked(True)
        self.refresh()
        self.select(d.id)
        info(self, "Deal closed", f"“{d.name}” is closed: its company and opening balance sheet are set up, and it's "
                                  "in the portfolio (Dashboard, Financing & Cap Table). Switch to it in Accounting to "
                                  "start its bookkeeping.")
        if self.on_added:
            self.on_added(dlg.holding_id)

    def delete(self) -> None:
        d = self._need()
        if d and confirm(self, "Delete deal", f"Delete “{d.name}” and its checklist? (To keep it, Mark Dead.)"):
            dl.delete(self.pf, d.id)
            self.refresh()

