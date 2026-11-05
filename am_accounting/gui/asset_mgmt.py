"""Asset Management mode: the holding company's portfolio of operating businesses."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QSplitter, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .. import portfolio as pfm
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, confirm, error, info

PALETTE = ["#2f6fde", "#1b7f3b", "#c2410c", "#7a3cc2", "#0f7d8c", "#9a6700", "#b3261e", "#5b6573"]


def _qd(iso: str | None) -> QDate:
    d = date.fromisoformat(iso) if iso else date.today()
    return QDate(d.year, d.month, d.day)


def _iso(qd: QDate) -> str:
    return qd.toString("yyyy-MM-dd")


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x * 100:.1f}%"


def _multiple(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f}x"


def short(cents: int | None) -> str:
    """$1.23M / $456K / $789 — for dashboard tiles."""
    if cents is None:
        return "—"
    v = cents / 100
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1e9:
        return f"{sign}${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{sign}${v / 1e6:.2f}M"
    if v >= 1e4:
        return f"{sign}${v / 1e3:.0f}K"
    return f"{sign}${v:,.0f}"


def _money_box(maximum: float = 1e13) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(0, maximum)
    s.setDecimals(2)
    s.setPrefix("$ ")
    s.setGroupSeparatorShown(True)
    return s


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class HoldingDialog(QDialog):
    """A company in the portfolio: which holding company holds its shares (and how much), its own company books,
    and the asset manager assigned to manage it (with the management fee)."""

    def __init__(self, pf: pfm.Portfolio, companies: list[tuple[str, str, str]], holding: pfm.Holding | None = None,
                 parent=None, create_company=None):
        from PySide6.QtWidgets import QGroupBox, QListWidget, QListWidgetItem

        super().__init__(parent)
        self.pf = pf
        self.create_company = create_company  # (name) -> path of a new company file, or None
        self.holding = holding or pfm.Holding("", owner_path=pf.holdco_path)
        h = self.holding
        companies = [c if len(c) == 3 else (c[0], c[1], "operating") for c in companies]
        self.setWindowTitle(f"Edit {h.name}" if holding else "New Holding")
        self.resize(640, 820)
        outer = QVBoxLayout(self)
        form = QFormLayout()
        outer.addLayout(form)
        self.name = QLineEdit(h.name)
        self.name.setPlaceholderText("e.g. Northwind OpCo Inc, Lakeshore Apartments LP")
        self.type = QComboBox()
        self.type.setEditable(True)
        self.type.addItems(pfm.TYPES)
        self.type.setCurrentText(h.type)
        self.status = QComboBox()
        self.status.addItem("Active", "active")
        self.status.addItem("Exited (sold / wound up)", "exited")
        self.status.setCurrentIndex(self.status.findData(h.status))
        self.sector = QLineEdit(h.sector)
        self.sector.setPlaceholderText("e.g. Multifamily, Construction, Software")
        self.location = QLineEdit(h.location)
        form.addRow("Name:", self.name)
        form.addRow("Type:", self.type)
        form.addRow("Sector:", self.sector)
        form.addRow("Location:", self.location)
        form.addRow("Status:", self.status)

        # 1. Ownership — a holding company holds its shares
        own_box = QGroupBox("Owned by (shares held by a holding company)")
        of = QFormLayout(own_box)
        self.owner = QComboBox()
        self.owner.addItem("(no holding company)", "")
        for name, path, role in companies:
            if role == "holding":
                self.owner.addItem(name, path)
        if h.owner_path and self.owner.findData(h.owner_path) < 0:
            self.owner.addItem(Path(h.owner_path).stem, h.owner_path)
        self.owner.setCurrentIndex(max(0, self.owner.findData(h.owner_path)))
        self.own = QDoubleSpinBox()
        self.own.setRange(0.01, 100)
        self.own.setDecimals(2)
        self.own.setSuffix(" %")
        self.own.setValue(h.ownership_pct)
        self.acquired = QDateEdit(_qd(h.acquired))
        self.acquired.setCalendarPopup(True)
        self.acquired.setDisplayFormat("MMM d, yyyy")
        of.addRow("Holding company:", self.owner)
        of.addRow("Shares held:", self.own)
        of.addRow("Acquired:", self.acquired)
        self.accounts = QListWidget()
        self.accounts.setMinimumHeight(110)
        self.accounts.setMaximumHeight(150)
        self.acct_label = QLabel()
        self.acct_label.setWordWrap(True)
        self.acct_label.setObjectName("hint")
        acct_row = QHBoxLayout()
        self.make_inv = QPushButton("Create “Investment in …” Account")
        self.make_inv.setToolTip("Add an 'Investment in <company>' account to the holding company's books and tick it")
        self.make_inv.clicked.connect(self._create_investment_account)
        acct_row.addWidget(self.make_inv)
        acct_row.addStretch(1)
        self._QListWidgetItem = QListWidgetItem
        of.addRow(self.acct_label)
        of.addRow(self.accounts)
        of.addRow(acct_row)
        outer.addWidget(own_box)

        # 2. The operating company's own books
        books_box = QGroupBox("Company books (the operating company's own bookkeeping)")
        bf = QFormLayout(books_box)
        row = QHBoxLayout()
        self.company = QComboBox()
        self.company.addItem("(not linked)", "")
        for name, path, role in companies:
            if role == "operating":
                self.company.addItem(name, path)
        if h.company_path and self.company.findData(h.company_path) < 0:
            self.company.addItem(Path(h.company_path).stem, h.company_path)
        self.company.setCurrentIndex(max(0, self.company.findData(h.company_path)))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.company, 1)
        row.addWidget(browse)
        if create_company is not None:
            make = QPushButton("Create Books…")
            make.setToolTip("Start a new company file for this business (its own bookkeeping in Accounting mode)")
            make.clicked.connect(self._create_books)
            row.addWidget(make)
        bf.addRow("Company file:", row)
        hint = QLabel("Its revenue, net income, cash, debt and equity show here automatically, and its book equity is "
                      "the value until you record a valuation.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        bf.addRow(hint)
        outer.addWidget(books_box)

        # 3. The asset manager assigned to it
        mgr_box = QGroupBox("Managed by (asset manager)")
        mf = QFormLayout(mgr_box)
        self.manager = QComboBox()
        self.manager.addItem("(no asset manager)", "")
        for name, path, role in companies:
            if role == "asset_manager":
                self.manager.addItem(name, path)
        if h.manager_path and self.manager.findData(h.manager_path) < 0:
            self.manager.addItem(Path(h.manager_path).stem, h.manager_path)
        self.manager.setCurrentIndex(max(0, self.manager.findData(h.manager_path)))
        self.fee_pct = QDoubleSpinBox()
        self.fee_pct.setRange(0, 100)
        self.fee_pct.setDecimals(3)
        self.fee_pct.setSuffix(" % of value per year")
        self.fee_pct.setValue(h.fee_pct)
        self.fee_fixed = _money_box()
        self.fee_fixed.setSuffix(" per month")
        self.fee_fixed.setValue(h.fee_fixed_cents / 100)
        self.capex = _money_box()
        self.capex.setSuffix(" per year")
        self.capex.setValue(h.capex_req_cents / 100)
        self.capex.setSpecialValueText("Use the last 12 months' actual")
        bf.addRow("CapEx requirement:", self.capex)
        mf.addRow("Asset manager:", self.manager)
        mf.addRow("Management fee:", self.fee_pct)
        mf.addRow("Fixed fee:", self.fee_fixed)
        outer.addWidget(mgr_box)
        self.manager.currentIndexChanged.connect(self._manager_changed)
        self._manager_changed()

        self.notes = QPlainTextEdit(h.notes)
        self.notes.setFixedHeight(50)
        nf = QFormLayout()
        nf.addRow("Notes:", self.notes)
        outer.addLayout(nf)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.owner.currentIndexChanged.connect(lambda *_: (self._keep_ticks(), self._fill_accounts()))
        self._fill_accounts()

    def _manager_changed(self, *_) -> None:
        on = bool(self.manager.currentData())
        self.fee_pct.setEnabled(on)
        self.fee_fixed.setEnabled(on)

    def _owner_path(self) -> str:
        return self.owner.currentData() or ""

    def _fill_accounts(self) -> None:
        h = self.holding
        checked = set(h.invest_accounts) | set(h.income_accounts)
        self.accounts.clear()
        hp = self._owner_path()
        if not hp:
            self.acct_label.setText("Choose the holding company that holds the shares to read the money it puts in "
                                    "and takes out from its books.")
            self.accounts.hide()
            self.make_inv.hide()
            return
        self.accounts.show()
        self.make_inv.show()
        self.acct_label.setText("Accounts in the holding company's books for this company — investment / due-from / "
                                "loan accounts (money out = contribution, money back = return of capital) and dividend "
                                "income (= distributions). Their entries become the capital flows.")
        likely = ("investment", "due from", "loan", "dividend", "subsidiar", "related", "note receivable")
        accts = sorted(pfm.holdco_accounts(hp), key=lambda a: (a[0] not in checked,
                                                                not any(w in a[1].lower() for w in likely), a[1]))
        for acct_id, label, typ in accts:
            it = self._QListWidgetItem(f"{label}   ({'income → distributions' if typ == 'income' else 'investment'})")
            it.setData(Qt.ItemDataRole.UserRole, (acct_id, typ))
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if acct_id in checked else Qt.CheckState.Unchecked)
            self.accounts.addItem(it)

    def _create_investment_account(self) -> None:
        name = self.name.text().strip()
        if not name or not self._owner_path():
            error(self, "Name and holding company first", "Enter the name and choose the holding company first.")
            return
        acct = pfm.create_investment_account(self._owner_path(), name)
        self._keep_ticks()
        if acct not in self.holding.invest_accounts:
            self.holding.invest_accounts.append(acct)
        self._fill_accounts()

    def _keep_ticks(self) -> None:
        if not self.accounts.count():
            return
        inv, inc = [], []
        for i in range(self.accounts.count()):
            it = self.accounts.item(i)
            acct_id, typ = it.data(Qt.ItemDataRole.UserRole)
            if it.checkState() == Qt.CheckState.Checked:
                (inc if typ == "income" else inv).append(acct_id)
        self.holding.invest_accounts, self.holding.income_accounts = inv, inc

    def _create_books(self) -> None:
        name = self.name.text().strip()
        if not name:
            error(self, "Name first", "Enter the company's name first.")
            return
        try:
            path = self.create_company(name)
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't create the company", str(e))
            return
        if path:
            self.company.addItem(name, str(path))
            self.company.setCurrentIndex(self.company.count() - 1)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Company file", str(Path.home()), "AM Accounting company (*.amdb)")
        if path:
            self.company.addItem(Path(path).stem, path)
            self.company.setCurrentIndex(self.company.count() - 1)

    def save(self) -> None:
        h = self.holding
        h.name, h.type = self.name.text().strip(), self.type.currentText().strip() or "Other"
        h.ownership_pct, h.acquired = self.own.value(), _iso(self.acquired.date())
        h.status, h.sector, h.location = self.status.currentData(), self.sector.text(), self.location.text()
        h.company_path, h.notes = self.company.currentData() or "", self.notes.toPlainText()
        self._keep_ticks()
        if not self._owner_path():
            h.invest_accounts, h.income_accounts = [], []
        h.owner_path = self._owner_path()
        h.manager_path = self.manager.currentData() or ""
        h.fee_pct = self.fee_pct.value() if h.manager_path else 0.0
        h.fee_fixed_cents = round(self.fee_fixed.value() * 100) if h.manager_path else 0
        h.capex_req_cents = round(self.capex.value() * 100)
        try:
            self.pf.save_holding(h)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class ValuationDialog(QDialog):
    """Company value and debt in place → equity value (100%) and our NAV."""

    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        self.setWindowTitle(f"Valuation – {holding.name}")
        form = QFormLayout(self)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("MMM d, yyyy")
        self.company_value = _money_box()
        self.debt = _money_box()
        self.equity_only = QCheckBox("Enter the equity value directly instead")
        self.equity = _money_box()
        self.method = QComboBox()
        self.method.addItems(pfm.METHODS)
        self.note = QLineEdit()
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.RichText)
        books_debt = None
        if holding.company_path and Path(holding.company_path).exists():
            f = pfm.company_financials(holding.company_path)
            if not f.error:
                books_debt = f.debt
                self.debt.setValue(f.debt / 100)
        form.addRow("Date:", self.date)
        form.addRow("Company value (100%):", self.company_value)
        form.addRow("Debt in place:", self.debt)
        if books_debt is not None:
            form.addRow("", QLabel(f"<span style='color:gray'>Debt in its books today: {fmt_money(books_debt)}</span>"))
        form.addRow("", self.equity_only)
        form.addRow("Equity value (100%):", self.equity)
        form.addRow("", self.result)
        form.addRow("Method:", self.method)
        form.addRow("Note:", self.note)
        hint = QLabel("Company value = the whole business or property (e.g. appraised property value, or enterprise "
                      "value from a multiple of EBITDA). Equity = company value − debt; our NAV = equity × "
                      f"{holding.ownership_pct:g}%.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        for w in (self.company_value, self.debt, self.equity):
            w.valueChanged.connect(self._update)
        self.equity_only.toggled.connect(self._update)
        self._update()

    def _equity_cents(self) -> int:
        if self.equity_only.isChecked():
            return round(self.equity.value() * 100)
        return round((self.company_value.value() - self.debt.value()) * 100)

    def _update(self, *_) -> None:
        direct = self.equity_only.isChecked()
        self.company_value.setEnabled(not direct)
        self.equity.setEnabled(direct)
        eq = self._equity_cents()
        if not direct:
            self.equity.blockSignals(True)
            self.equity.setValue(eq / 100)
            self.equity.blockSignals(False)
        self.result.setText(f"Equity <b>{fmt_money(eq)}</b> · our NAV <b>"
                            f"{fmt_money(round(eq * self.holding.ownership_pct / 100))}</b>")

    def save(self) -> None:
        eq = self._equity_cents()
        if eq <= 0 and not (self.company_value.value() > 0 and not self.equity_only.isChecked()):
            error(self, "Enter a value", "Enter the company value (and its debt), or the equity value.")
            return
        direct = self.equity_only.isChecked()
        self.pf.add_valuation(self.holding.id, pfm.Valuation(
            _iso(self.date.date()), eq, self.method.currentText(), self.note.text(),
            company_value_cents=None if direct else round(self.company_value.value() * 100),
            debt_cents=round(self.debt.value() * 100)))
        self.accept()


class FlowDialog(QDialog):
    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        self.setWindowTitle(f"Capital flow – {holding.name}")
        form = QFormLayout(self)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("MMM d, yyyy")
        self.kind = QComboBox()
        for k, (label, sign) in pfm.FLOW_KINDS.items():
            self.kind.addItem(f"{label}  ({'money out to the holding' if sign < 0 else 'money back to us'})", k)
        self.amount = _money_box()
        self.note = QLineEdit()
        form.addRow("Date:", self.date)
        form.addRow("Kind:", self.kind)
        form.addRow("Amount:", self.amount)
        form.addRow("Note:", self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        try:
            self.pf.add_flow(self.holding.id, pfm.Flow(_iso(self.date.date()), self.kind.currentData(),
                                                       round(self.amount.value() * 100), self.note.text()))
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


class LoanDialog(QDialog):
    """One loan / mortgage / credit line: lender, amounts, rate, term, payment frequency, interest only or
    amortizing (and over how long) — the payment, maturity and amortization table are worked out."""

    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, loan: pfm.Loan | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        self.loan = loan or pfm.Loan("")
        ln = self.loan
        self.setWindowTitle(f"Loan – {holding.name}")
        self.resize(1000, 640)
        outer = QHBoxLayout(self)
        left = QVBoxLayout()
        form = QFormLayout()
        left.addLayout(form)
        outer.addLayout(left, 0)
        self.lender = QLineEdit(ln.lender if not ln.terms_missing else "")
        self.lender.setPlaceholderText("Lender name, e.g. TD Bank, RBC, First National")
        form.addRow("Lender name:", self.lender)
        self.kind = QComboBox()
        self.kind.addItems(pfm.LOAN_KINDS)
        self.kind.setCurrentText(ln.kind)
        form.addRow("Facility:", self.kind)
        self.account = QComboBox()
        self.account.addItem("(not in the books — enter the balance)", None)
        self._book = {}
        if holding.company_path:
            for i, n, bal in pfm.debt_accounts(holding.company_path):
                self._book[i] = bal
                self.account.addItem(f"{n}  ·  {fmt_money(bal)} owing", i)
        self.account.setCurrentIndex(max(0, self.account.findData(ln.account_id)))
        form.addRow("Account in the books:", self.account)
        self.principal = _money_box()
        self.principal.setValue((ln.principal_cents or (ln.book_balance or 0)) / 100)
        self.principal.setToolTip("The original amount borrowed (or the credit line's limit)")
        form.addRow("Principal (original):", self.principal)
        bal_row = QHBoxLayout()
        self.balance = _money_box()
        self.balance.setValue(ln.balance / 100)
        self.use_sched = QPushButton("Use Scheduled")
        self.use_sched.setToolTip("Set the balance to what the amortization table says is owed today")
        self.use_sched.clicked.connect(self._use_scheduled)
        bal_row.addWidget(self.balance, 1)
        bal_row.addWidget(self.use_sched)
        form.addRow("Balance outstanding:", bal_row)
        rate_row = QHBoxLayout()
        self.rate = QDoubleSpinBox()
        self.rate.setRange(0, 100)
        self.rate.setDecimals(3)
        self.rate.setSuffix(" %")
        self.rate.setValue(ln.rate_pct or 0)
        self.rate_type = QComboBox()
        self.rate_type.addItems(pfm.RATE_TYPES)
        self.rate_type.setCurrentText(ln.rate_type)
        rate_row.addWidget(self.rate, 1)
        rate_row.addWidget(self.rate_type)
        form.addRow("Interest rate (per year):", rate_row)
        self.repayment = QComboBox()
        self.repayment.addItem("Amortizing (principal + interest)", "Amortizing")
        self.repayment.addItem("Interest only", "Interest only")
        self.repayment.setCurrentIndex(max(0, self.repayment.findData(ln.repayment)))
        form.addRow("Type:", self.repayment)
        self.start = QDateEdit(_qd(ln.start_date))
        self.start.setCalendarPopup(True)
        self.start.setDisplayFormat("MMM d, yyyy")
        form.addRow("Start date:", self.start)

        def years_months(total: int) -> tuple[QDoubleSpinBox, QDoubleSpinBox, QHBoxLayout]:
            y, m = QDoubleSpinBox(), QDoubleSpinBox()
            y.setRange(0, 100)
            y.setDecimals(0)
            y.setSuffix(" yrs")
            m.setRange(0, 11)
            m.setDecimals(0)
            m.setSuffix(" mo")
            y.setValue(total // 12)
            m.setValue(total % 12)
            row = QHBoxLayout()
            row.addWidget(y, 1)
            row.addWidget(m, 1)
            return y, m, row

        term = ln.term_months
        if not term and ln.start_date and ln.maturity:  # an older loan: term from its dates
            s_, e_ = date.fromisoformat(ln.start_date), date.fromisoformat(ln.maturity)
            term = max(0, (e_.year - s_.year) * 12 + e_.month - s_.month)
        self.term_y, self.term_m, trow = years_months(term)
        form.addRow("Term:", trow)
        self.amort_y, self.amort_m, arow = years_months(ln.amort_months or term)
        self.amort_label = QLabel("Amortization period:")
        form.addRow(self.amort_label, arow)
        self.freq = QComboBox()
        self.freq.addItems(pfm.PAY_FREQS)
        self.freq.setCurrentText(ln.payment_freq if ln.payment_freq in pfm.PAY_FREQS else "Monthly")
        form.addRow("Payment period:", self.freq)
        pay_row = QHBoxLayout()
        self.payment = _money_box()
        self.own_payment = QCheckBox("Enter by hand")
        self.own_payment.setChecked(bool(ln.payment_cents))
        self.payment.setValue(ln.payment_cents / 100)
        pay_row.addWidget(self.payment, 1)
        pay_row.addWidget(self.own_payment)
        form.addRow("Payment:", pay_row)
        self.maturity = QLabel()
        form.addRow("Maturity:", self.maturity)
        self.note = QLineEdit(ln.note)
        self.note.setPlaceholderText("Covenants, security, renewal terms… (optional)")
        form.addRow("Note:", self.note)
        self.result = QLabel()
        self.result.setTextFormat(Qt.TextFormat.RichText)
        self.result.setWordWrap(True)
        form.addRow(self.result)
        left.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        left.addWidget(buttons)

        right = QVBoxLayout()
        head = QHBoxLayout()
        head.addWidget(QLabel("<b>Amortization table</b>"))
        head.addStretch(1)
        self.sched_view = QComboBox()
        self.sched_view.addItem("Each payment", "payment")
        self.sched_view.addItem("By year", "year")
        head.addWidget(self.sched_view)
        right.addLayout(head)
        self.sched = _table(SCHED_COLS, None)
        right.addWidget(self.sched, 1)
        outer.addLayout(right, 1)

        for w in (self.principal, self.rate, self.term_y, self.term_m, self.amort_y, self.amort_m, self.payment,
                  self.balance):
            w.valueChanged.connect(self._update)
        for c in (self.freq, self.repayment, self.sched_view, self.account):
            c.currentIndexChanged.connect(self._update)
        self.start.dateChanged.connect(self._update)
        self.own_payment.toggled.connect(self._update)
        self._update()

    def draft(self) -> pfm.Loan:
        """The loan as the window has it now (not saved)."""
        acct = self.account.currentData()
        term = int(self.term_y.value() * 12 + self.term_m.value())
        amort = int(self.amort_y.value() * 12 + self.amort_m.value())
        repay = self.repayment.currentData()
        return pfm.Loan(
            self.lender.text().strip(), self.kind.currentText(), round(self.principal.value() * 100),
            None if acct in self._book else round(self.balance.value() * 100), self.rate.value(),
            self.rate_type.currentText(), _iso(self.start.date()), None,
            round(self.payment.value() * 100) if self.own_payment.isChecked() else 0, self.freq.currentText(),
            self.note.text(), acct, self.loan.id, self.loan.holding_id, term_months=term, repayment=repay,
            amort_months=0 if repay == "Interest only" or amort == term else amort)

    def _update(self, *_) -> None:
        acct = self.account.currentData()
        in_books = acct in self._book
        if in_books:
            self.balance.blockSignals(True)
            self.balance.setValue(self._book[acct] / 100)
            self.balance.blockSignals(False)
        self.balance.setEnabled(not in_books)
        self.use_sched.setEnabled(not in_books)
        self.balance.setToolTip("Read from the company's books" if in_books else "")
        amortizing = self.repayment.currentData() == "Amortizing"
        for w in (self.amort_y, self.amort_m, self.amort_label):
            w.setEnabled(amortizing)
        ln = self.draft()
        self.payment.setEnabled(self.own_payment.isChecked())
        if not self.own_payment.isChecked():
            self.payment.blockSignals(True)
            self.payment.setValue(ln.payment / 100)
            self.payment.blockSignals(False)
        self.maturity.setText(ln.maturity_date or "— set the term")
        bits = []
        n = pfm.PER_YEAR.get(ln.payment_freq, 12)
        if ln.payment and n:
            bits.append(f"<b>{fmt_money(ln.payment)}</b> {ln.payment_freq.lower()} "
                        f"({'interest only' if ln.repayment == 'Interest only' else 'principal + interest'}) · "
                        f"<b>{fmt_money(ln.annual_debt_service)}</b> a year")
        rows = pfm.amortization_schedule(ln)
        if rows:
            bits.append(f"Total interest over the term {fmt_money(sum(r.interest for r in rows))}")
        balloon = ln.balloon()
        if balloon:
            bits.append(f"Balloon at maturity <b>{fmt_money(balloon)}</b>")
        sb = ln.scheduled_balance(date.today())
        if sb is not None:
            bits.append(f"Scheduled balance today {fmt_money(sb)}")
        self._scheduled = sb
        self.result.setText("<br>".join(bits))
        fill_schedule_table(self.sched, rows, self.sched_view.currentData())

    def _use_scheduled(self) -> None:
        if getattr(self, "_scheduled", None) is not None:
            self.balance.setValue(self._scheduled / 100)

    def save(self) -> None:
        ln = self.draft()
        ln.maturity = ln.maturity_date if ln.term_months else self.loan.maturity
        try:
            pfm.save_loan(self.pf, self.holding.id, ln)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.loan = ln
        self.accept()


class ShareholderDialog(QDialog):
    """An owner on the cap table: name, their company (if they hold through one) and the amount."""

    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, sh: pfm.Shareholder | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, holding
        # a worked-out line becomes a typed-in one (prefilled) when saved
        self.sh = sh if sh and not sh.worked_out else pfm.Shareholder("")
        src = sh or self.sh
        self.setWindowTitle(f"Owner – {holding.name}")
        self.resize(460, 0)
        form = QFormLayout(self)
        self.name = QLineEdit(src.name)
        self.name.setPlaceholderText("e.g. Alex Morgan")
        form.addRow("Name:", self.name)
        self.company = QLineEdit(src.company)
        self.company.setPlaceholderText("If they hold through a company, e.g. Morgan Holdings Inc (optional)")
        form.addRow("Company name:", self.company)
        self.share_class = QComboBox()
        self.share_class.setEditable(True)
        self.share_class.addItems(pfm.SHARE_CLASSES)
        self.share_class.setCurrentText(src.share_class or "Common")
        form.addRow("Share class:", self.share_class)
        self.amount = _money_box()
        self.amount.setValue(src.amount_cents / 100)
        self.amount.setToolTip("This owner's equity (committed); each owner's % is their amount ÷ everyone's total")
        form.addRow("Amount (equity):", self.amount)
        self.funded = _money_box()
        self.funded.setValue(src.funded / 100)
        self.funded.setToolTip("Paid in so far")
        form.addRow("Funded:", self.funded)
        self.funded_date = QDateEdit(_qd(src.funded_date))
        self.funded_date.setCalendarPopup(True)
        self.funded_date.setDisplayFormat("yyyy-MM-dd")
        self.funded_date.setToolTip("When it was paid in — shown as an equity injection on the Cash Flow")
        form.addRow("Funded on:", self.funded_date)
        self.note = QLineEdit(src.note)
        self.note.setPlaceholderText("Note (optional)")
        form.addRow("Note:", self.note)
        hint = QLabel("Each owner's % is their amount ÷ the total of all owners. Once you enter owners, the cap "
                      "table shows exactly these instead of working it out from the holding's ownership %.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        hint.setMinimumHeight(hint.fontMetrics().lineSpacing() * 3)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        x = self.sh
        x.name, x.company = self.name.text(), self.company.text()
        x.amount_cents, x.note = round(self.amount.value() * 100), self.note.text()
        x.share_class = self.share_class.currentText()
        x.funded_cents = round(self.funded.value() * 100)
        x.funded_date = _iso(self.funded_date.date())
        try:
            pfm.save_shareholder(self.pf, self.holding.id, x)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


LOAN_SUMMARY_COLS = ["Lender", "Facility", "Type", "Balance", "Rate", "Payment", "Maturity"]


def fill_loan_summary(t: QTableWidget, rows: list[pfm.Loan], as_of: date) -> None:
    tot = pfm.financing_totals(rows)
    t.setRowCount(len(rows) + (1 if rows else 0))
    for r, ln in enumerate(rows):
        left = ln.years_left(as_of)
        _set_row(t, r, [ln.lender + ("  — add terms" if ln.terms_missing else ""), ln.kind,
                        "—" if ln.terms_missing else ln.repayment, fmt_money(ln.balance),
                        f"{ln.rate_pct:.3g}%" if ln.rate_pct is not None else "—",
                        f"{fmt_money(ln.payment)} {ln.payment_freq.lower()}" if ln.payment else "—",
                        ln.maturity_date or "—"], {3, 4, 5})
        if ln.terms_missing:
            for c in range(t.columnCount()):
                t.item(r, c).setForeground(MUTED)
        if left is not None and left < 1:
            t.item(r, 6).setForeground(NEGATIVE)
    if rows:
        _set_row(t, len(rows), ["Total", "", "", fmt_money(tot.balance),
                                f"{tot.rate:.3g}% avg" if tot.rate is not None else "—",
                                f"{fmt_money(tot.debt_service)} / yr", f"next {tot.next_maturity}"
                                if tot.next_maturity else ""], {3, 4, 5}, bold=True)
    t.resizeColumnsToContents()
    t.horizontalHeader().setStretchLastSection(True)
    t.resizeRowsToContents()
    rows_h = sum(t.rowHeight(r) for r in range(t.rowCount())) or 28
    t.setFixedHeight(min(t.horizontalHeader().height() + rows_h + 6, 260))


SCHED_COLS = ["#", "Date", "Payment", "Interest", "Principal", "Balance", "In the books"]


def fill_schedule_table(t: QTableWidget, rows: list[pfm.ScheduleRow], view: str = "payment",
                        paid: list[tuple[str, int, int]] | None = None) -> None:
    """``paid`` = [(date, principal, interest)] recorded in the company's books; each scheduled payment is
    ticked when one was recorded within two weeks of its date."""
    shown = pfm.schedule_by_year(rows) if view == "year" else rows
    t.setHorizontalHeaderLabels(["Year" if view == "year" else "#", "" if view == "year" else "Date",
                                 *SCHED_COLS[2:]])
    paid = sorted(paid or [])
    left = list(paid)

    def booked(row) -> str:
        if view == "year":
            n = [x for x in paid if x[0][:4] == str(row.number)]
            return f"{len(n)} paid · interest {fmt_money(sum(x[2] for x in n))}" if n else ""
        d = date.fromisoformat(row.date)
        hit = next((x for x in left if abs((date.fromisoformat(x[0]) - d).days) <= 14), None)
        if hit:
            left.remove(hit)
            return f"✓ {hit[0]} · interest {fmt_money(hit[2])}"
        return ""

    t.setRowCount(len(shown) + (1 if shown else 0))
    for r, x in enumerate(shown):
        label = str(x.number) if view == "year" else ("Balloon" if x.number == 0 else str(x.number))
        _set_row(t, r, [label, "" if view == "year" else x.date, fmt_money(x.payment), fmt_money(x.interest),
                        fmt_money(x.principal), fmt_money(x.balance), booked(x) if paid else ""],
                 {2, 3, 4, 5}, bold=x.number == 0)
        if paid and t.item(r, 6).text():
            t.item(r, 6).setForeground(POSITIVE)
    if shown:
        _set_row(t, len(shown), ["Total", "", fmt_money(sum(x.payment for x in rows)),
                                 fmt_money(sum(x.interest for x in rows)), fmt_money(sum(x.principal for x in rows)),
                                 "", f"{len(paid)} recorded" if paid else ""], {2, 3, 4, 5}, bold=True)
    t.resizeColumnsToContents()


class SourcesUsesDialog(QDialog):
    """Type in a company's sources & uses of funds (100% of the company)."""

    def __init__(self, pf: pfm.Portfolio, perf: pfm.Performance, as_of: date, parent=None):
        super().__init__(parent)
        self.pf, self.holding = pf, perf.holding
        su = pfm.sources_uses(pf, perf, as_of)
        self.setWindowTitle(f"Sources & uses – {perf.holding.name}")
        self.resize(460, 0)
        form = QFormLayout(self)
        hint = QLabel("100% of the company. " + ("These were typed in." if su.entered else
                      "Worked out from the loans, the capital put in, a 'Purchase price' valuation and the capex "
                      "in its books — change any line to fix them."))
        hint.setWordWrap(True)
        hint.setObjectName("hint")
        form.addRow(hint)
        self.boxes: dict[str, QDoubleSpinBox] = {}
        for title, items, vals in (("Sources", pfm.SOURCES, su.sources), ("Uses", pfm.USES, su.uses)):
            form.addRow(QLabel(f"<b>{title}</b>"))
            for k in items:
                b = _money_box()
                b.setValue(vals.get(k, 0) / 100)
                b.valueChanged.connect(self._update)
                self.boxes[k] = b
                form.addRow(k + ":", b)
        self.totals = QLabel()
        form.addRow(self.totals)
        row = QHBoxLayout()
        bal = QPushButton("Balance with Cash Reserve")
        bal.clicked.connect(self.balance)
        reset = QPushButton("Work Out Automatically")
        reset.setToolTip("Forget what was typed in and work the figures out again")
        reset.clicked.connect(self.reset)
        row.addWidget(bal)
        row.addWidget(reset)
        row.addStretch(1)
        form.addRow(row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._update()

    def _cents(self, k: str) -> int:
        return round(self.boxes[k].value() * 100)

    def _update(self, *_) -> None:
        src, use = sum(self._cents(k) for k in pfm.SOURCES), sum(self._cents(k) for k in pfm.USES)
        diff = src - use
        self.totals.setText(f"Sources <b>{fmt_money(src)}</b> · Uses <b>{fmt_money(use)}</b>"
                            + ("" if not diff else f" · <span style='color:#b3261e'>out by {fmt_money(diff)}</span>"))

    def balance(self) -> None:
        diff = sum(self._cents(k) for k in pfm.SOURCES) - sum(self._cents(k) for k in pfm.USES)
        b = self.boxes[pfm.PLUG]
        b.setValue(max(0.0, b.value() + diff / 100))

    def reset(self) -> None:
        pfm.save_sources_uses(self.pf, self.holding.id, None)
        self.accept()

    def save(self) -> None:
        try:
            pfm.save_sources_uses(self.pf, self.holding.id, {k: self._cents(k) for k in self.boxes})
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


SU_ROWS = ["SOURCES", *pfm.SOURCES, "Total sources", "", "USES", *pfm.USES, "Total uses", "", "Basis"]


LOAN_COLS = ["Lender", "Facility", "Type", "Principal", "Balance", "Rate", "Term", "Payment", "Debt service / yr",
             "Annual interest", "Maturity", "Time left", "From the books"]


def fill_loan_table(t: QTableWidget, rows: list[pfm.Loan], as_of: date) -> None:
    t.setRowCount(0)
    tot = pfm.financing_totals(rows)
    t.setRowCount(len(rows) + (1 if rows else 0))

    def term(ln):
        if not ln.term_months:
            return "—"
        y, m = divmod(ln.term_months, 12)
        txt = " ".join(x for x in (f"{y} yr" if y else "", f"{m} mo" if m else "") if x)
        if ln.repayment == "Amortizing" and ln.amort_months and ln.amort_months != ln.term_months:
            txt += f" / {ln.amort_months / 12:g} yr amort."
        return txt

    for r, ln in enumerate(rows):
        left = ln.years_left(as_of)
        _set_row(t, r, [
            ln.lender + ("  — click to add terms" if ln.terms_missing else ""), ln.kind,
            "—" if ln.terms_missing else ln.repayment,
            fmt_money(ln.principal_cents) if ln.principal_cents else "—", fmt_money(ln.balance),
            f"{ln.rate_pct:.3g}% {ln.rate_type.lower()}" if ln.rate_pct is not None else "—", term(ln),
            f"{fmt_money(ln.payment)} {ln.payment_freq.lower()}" if ln.payment else
            (ln.payment_freq if ln.payment_freq == "At maturity" else "—"),
            fmt_money(ln.annual_debt_service) if ln.annual_debt_service else "—",
            fmt_money(ln.annual_interest) if ln.annual_interest is not None else "—",
            ln.maturity_date or "—",
            ("matured" if left < 0 else f"{left:.1f} yrs") if left is not None else "—",
            ln.account_name or "—"], {3, 4, 5, 7, 8, 9, 11})
        if ln.terms_missing:
            for c in range(t.columnCount()):
                t.item(r, c).setForeground(MUTED)
        if left is not None and left < 1:
            t.item(r, 10).setForeground(NEGATIVE)
            t.item(r, 11).setForeground(NEGATIVE)
    if rows:
        _set_row(t, len(rows), ["Total", "", "", fmt_money(tot.principal), fmt_money(tot.balance),
                                f"{tot.rate:.3g}% avg" if tot.rate is not None else "—", "", "",
                                fmt_money(tot.debt_service), fmt_money(tot.annual_interest),
                                f"next {tot.next_maturity}" if tot.next_maturity else "", "", ""],
                 {3, 4, 5, 8, 9}, bold=True)
    t.resizeColumnsToContents()
    t.horizontalHeader().setStretchLastSection(True)


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


PROJ_ROWS = ["Revenue", "Op. Ex", "EBITDA", "", "Non-Op. Ex", "CapEx", "Net cash flow"]


def fill_projection_table(t: QTableWidget, actual: pfm.Financials | None, rows: list[pfm.Projection],
                          capex_actual: int | None = None) -> None:
    """Figures down the side (revenue … net cash flow), last 12 months actual then each projected year across."""
    cols: list[tuple[str, list[int | None]]] = []
    if actual is not None:
        cap = actual.capex_ltm if capex_actual is None else capex_actual
        cols.append(("Last 12 months (actual)", [actual.revenue_ltm, actual.opex_ltm, actual.ebitda_ltm, None,
                                                  actual.nonop_ltm, cap,
                                                  actual.ebitda_ltm - actual.nonop_ltm - cap]))
    for p in rows:
        cols.append((f"{p.year} (projected)", [p.revenue, p.opex, p.ebitda, None, p.nonop, p.capex, p.net_cash_flow]))
    t.clear()
    t.setColumnCount(len(cols) + 1)
    t.setRowCount(len(PROJ_ROWS))
    t.setHorizontalHeaderLabels([""] + [c[0] for c in cols])
    for r_, label in enumerate(PROJ_ROWS):
        head = QTableWidgetItem(label)
        if label in ("EBITDA", "Net cash flow"):
            f = head.font()
            f.setBold(True)
            head.setFont(f)
        t.setItem(r_, 0, head)
        if not label:
            t.setRowHeight(r_, 10)
        for c_, (_title, vals) in enumerate(cols):
            v = vals[r_]
            cell = QTableWidgetItem("" if v is None else fmt_money(v))
            cell.setTextAlignment(ALIGN_RIGHT)
            if label in ("EBITDA", "Net cash flow"):
                f = cell.font()
                f.setBold(True)
                cell.setFont(f)
                if v is not None and v < 0:
                    cell.setForeground(NEGATIVE)
            t.setItem(r_, c_ + 1, cell)
    t.setColumnWidth(0, 130)
    for c_ in range(len(cols)):
        t.setColumnWidth(c_ + 1, 165)
    t.verticalHeader().setVisible(False)


MONTH_ROWS = [("Revenue", "revenue"), ("Op. Ex", "opex"), ("EBITDA", "ebitda"), ("", None),
              ("Non-Op. Ex", "nonop"), ("CapEx", "capex"), ("Net cash flow", "net_cash_flow"), ("", None),
              ("Net income", "net_income")]


def fill_period_table(t: QTableWidget, months: list[dict]) -> None:
    """Months across the top (with a 12-month total), categories down the side."""
    t.clear()
    if not months:
        t.setRowCount(0)
        t.setColumnCount(1)
        t.setHorizontalHeaderLabels(["Link the company's books to see its months"])
        return
    t.setColumnCount(len(months) + 2)
    t.setHorizontalHeaderLabels([""] + [m["label"] for m in months] + ["Total"])
    t.setRowCount(len(MONTH_ROWS))
    for r_, (label, key) in enumerate(MONTH_ROWS):
        head = QTableWidgetItem(label)
        bold = label in ("EBITDA", "Net cash flow", "Net income")
        if bold:
            f = head.font()
            f.setBold(True)
            head.setFont(f)
        t.setItem(r_, 0, head)
        if key is None:
            t.setRowHeight(r_, 10)
            continue
        vals = [m[key] for m in months] + [sum(m[key] for m in months)]
        for c_, v in enumerate(vals):
            cell = QTableWidgetItem(short(v) if c_ < len(months) else fmt_money(v))
            cell.setToolTip(fmt_money(v))
            cell.setTextAlignment(ALIGN_RIGHT)
            if bold or c_ == len(months):
                f = cell.font()
                f.setBold(True)
                cell.setFont(f)
            if v < 0:
                cell.setForeground(NEGATIVE)
            t.setItem(r_, c_ + 1, cell)
    t.setColumnWidth(0, 110)
    for c_ in range(len(months)):
        t.setColumnWidth(c_ + 1, 78)
    t.setColumnWidth(len(months) + 1, 125)
    t.verticalHeader().setVisible(False)


class ProjectionsDialog(QDialog):
    """Future years for one company: revenue, Op. Ex, Non-Op. Ex and CapEx spend (EBITDA and net cash flow follow)."""

    COLS = ["Year", "Revenue", "Op. Ex", "EBITDA", "Non-Op. Ex", "CapEx", "Net cash flow", "Note"]
    EDIT = {0, 1, 2, 4, 5, 7}

    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, actual: pfm.Financials | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding, self.actual = pf, holding, actual
        self.setWindowTitle(f"Projections – {holding.name}")
        self.resize(1020, 460)
        lay = QVBoxLayout(self)
        intro = QLabel("Future years at 100% of the company. Type amounts in (e.g. 420000 or 420,000.00); EBITDA = "
                       "revenue − Op. Ex and net cash flow = EBITDA − Non-Op. Ex − CapEx are worked out. "
                       + (f"Last 12 months actual: revenue {fmt_money(actual.revenue_ltm)}, Op. Ex "
                          f"{fmt_money(actual.opex_ltm)}, Non-Op. Ex {fmt_money(actual.nonop_ltm)}, CapEx "
                          f"{fmt_money(actual.capex_ltm)}." if actual else ""))
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        lay.addWidget(intro)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
        for c_, w in enumerate([70, 130, 130, 130, 130, 130, 130]):
            self.table.setColumnWidth(c_, w)
        self.table.itemChanged.connect(self._recalc)
        lay.addWidget(self.table, 1)
        btns = QHBoxLayout()
        for text, slot in [("Add Year", self.add_year), ("Remove Year", self.remove_year),
                           ("Fill from Last 12 Months…", self.fill_from_actuals)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        for p in pfm.projections(pf, holding.id):
            self._add_row(p)

    def _add_row(self, p: pfm.Projection) -> None:
        self.table.blockSignals(True)
        r_ = self.table.rowCount()
        self.table.insertRow(r_)
        vals = [str(p.year), fmt_money(p.revenue), fmt_money(p.opex), "", fmt_money(p.nonop), fmt_money(p.capex), "",
                p.note]
        for c_, v in enumerate(vals):
            cell = QTableWidgetItem(v)
            if c_ not in self.EDIT:
                cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if 0 < c_ < 7:
                cell.setTextAlignment(ALIGN_RIGHT)
            self.table.setItem(r_, c_, cell)
        self.table.blockSignals(False)
        self._recalc()

    @staticmethod
    def _cents(text: str) -> int:
        from ..money import parse_money

        v = parse_money(text.strip()) if text.strip() else 0
        return abs(v or 0)

    def rows(self) -> list[pfm.Projection]:
        out = []
        for r_ in range(self.table.rowCount()):
            g = lambda c_: (self.table.item(r_, c_).text() if self.table.item(r_, c_) else "")  # noqa: E731
            try:
                year = int(g(0).strip())
            except ValueError:
                raise ValueError(f"Row {r_ + 1}: enter a year like 2027") from None
            out.append(pfm.Projection(year, self._cents(g(1)), self._cents(g(2)), self._cents(g(4)),
                                      self._cents(g(5)), g(7)))
        return out

    def _recalc(self, *_) -> None:
        self.table.blockSignals(True)
        for r_ in range(self.table.rowCount()):
            g = lambda c_: (self.table.item(r_, c_).text() if self.table.item(r_, c_) else "")  # noqa: E731
            try:
                p = pfm.Projection(0, self._cents(g(1)), self._cents(g(2)), self._cents(g(4)), self._cents(g(5)))
            except Exception:  # noqa: BLE001
                continue
            for c_, v in ((3, p.ebitda), (6, p.net_cash_flow)):
                cell = self.table.item(r_, c_)
                cell.setText(fmt_money(v))
                cell.setForeground(NEGATIVE if v < 0 else POSITIVE)
        self.table.blockSignals(False)

    def add_year(self) -> None:
        years = [p.year for p in self.rows()] if self.table.rowCount() else []
        nxt = (max(years) + 1) if years else date.today().year + 1
        last = self.rows()[-1] if years else None
        self._add_row(pfm.Projection(nxt, last.revenue, last.opex, last.nonop, last.capex) if last
                      else pfm.Projection(nxt))

    def remove_year(self) -> None:
        r_ = self.table.currentRow()
        if r_ >= 0:
            self.table.removeRow(r_)

    def fill_from_actuals(self) -> None:
        from PySide6.QtWidgets import QSpinBox

        if self.actual is None:
            error(self, "No actuals", "Link the company's books (Edit…) to start projections from its last 12 months.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Fill from last 12 months")
        form = QFormLayout(dlg)
        first = QSpinBox()
        first.setRange(1990, 2200)
        first.setValue(date.today().year + 1)
        n = QSpinBox()
        n.setRange(1, 30)
        n.setValue(5)
        rg, og, ng = (QDoubleSpinBox() for _ in range(3))
        for sp in (rg, og, ng):
            sp.setRange(-50, 100)
            sp.setDecimals(2)
            sp.setSuffix(" % a year")
        rg.setValue(3)
        og.setValue(3)
        cap = _money_box()
        cap.setValue(self.actual.capex_ltm / 100)
        cap.setSuffix(" a year")
        for label, w in [("First year:", first), ("Number of years:", n), ("Revenue growth:", rg),
                         ("Op. Ex growth:", og), ("Non-Op. Ex growth:", ng), ("CapEx spend:", cap)]:
            form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return
        keep = {p.year: p for p in self.rows()} if self.table.rowCount() else {}
        for p in pfm.project_from_actuals(self.actual, first.value(), n.value(), revenue_growth=rg.value(),
                                          opex_growth=og.value(), nonop_growth=ng.value(),
                                          capex=round(cap.value() * 100)):
            keep[p.year] = p
        self.table.setRowCount(0)
        for y in sorted(keep):
            self._add_row(keep[y])

    def save(self) -> None:
        try:
            pfm.save_projections(self.pf, self.holding.id, self.rows())
        except ValueError as e:
            error(self, "Couldn't save the projections", str(e))
            return
        self.accept()


def _scroll(page: QWidget) -> QWidget:
    """Let a long page scroll instead of squashing its cards when the window is short."""
    from PySide6.QtWidgets import QScrollArea

    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setWidget(page)
    return area


class Card(QFrame):
    def __init__(self, title: str, *, small: bool = False):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6) if small else lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(1 if small else 4)
        t = QLabel(title)
        t.setObjectName("cardtitle")
        self.value = QLabel("—")
        f = self.value.font()
        f.setPointSizeF(f.pointSizeF() * (1.25 if small else 1.6))
        f.setBold(True)
        self.value.setFont(f)
        self.sub = QLabel("")
        self.sub.setObjectName("hint")
        lay.addWidget(t)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)
        from PySide6.QtWidgets import QSizePolicy

        for w in (t, self.value, self.sub):
            w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            w.setMinimumHeight(w.sizeHint().height())
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def set(self, value: str, sub: str = "", colour: QColor | None = None) -> None:
        self.value.setText(value)
        self.value.setStyleSheet(f"color: {colour.name()};" if colour else "")
        self.sub.setText(sub)


GROUPS = [("Value", [("value", "Value"), ("debt", "Debt"), ("nav", "NAV")]),
          ("Operations", [("revenue", "Revenue"), ("opex", "Op. Ex"), ("ebitda", "EBITDA")]),
          ("Cash flow", [("nonop", "Non-Op. Ex"), ("capex", "CapEx"), ("ncf", "Net cash flow")]),
          ("Returns", [("moic", "MOIC"), ("irr", "IRR"), ("coc", "Cash on cash return")])]
GROUP_TIPS = {
    "value": "Company value: the whole business or property (latest valuation), else book equity + debt",
    "debt": "Debt in place: from the valuation, else the company's loans / mortgages / long-term liabilities",
    "nav": "Net asset value: (value − debt) × ownership",
    "revenue": "Operating revenue, last 12 months",
    "opex": "Cost of sales + operating expenses, excluding depreciation & amortization (last 12 months)",
    "ebitda": "Revenue − Op. Ex",
    "nonop": "Interest, income taxes and other non-operating expenses, excluding D&A (last 12 months)",
    "capex": "CapEx requirement: the annual amount set on the holding, else the last 12 months' fixed-asset purchases",
    "ncf": "Net cash flow = EBITDA − Non-Op. Ex − CapEx",
    "moic": "Multiple on invested capital: (NAV + returned) ÷ invested",
    "irr": "Annualized return on the dated capital flows plus today's NAV",
    "coc": "Cash on cash return: our share of the annual net cash flow ÷ the capital invested",
}


class GroupedSummary(QWidget):
    """Value · Operations · Cash flow · Returns — three figures each, with space between the groups."""

    def __init__(self):
        from PySide6.QtWidgets import QGroupBox

        super().__init__()
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(12)
        self.cards: dict[str, Card] = {}
        for i, (title, items) in enumerate(GROUPS):
            box = QGroupBox(f"{i + 1}. {title}")
            row = QHBoxLayout(box)
            row.setContentsMargins(6, 6, 6, 6)
            row.setSpacing(6)
            for key, label in items:
                card = Card(label)
                card.setToolTip(GROUP_TIPS.get(key, ""))
                self.cards[key] = card
                row.addWidget(card)
            grid.addWidget(box, i // 2, i % 2)

    def set(self, key: str, value: str, sub: str = "", colour: QColor | None = None) -> None:
        self.cards[key].set(value, sub, colour)

    def clear(self) -> None:
        for c in self.cards.values():
            c.set("—")

    def fill(self, *, value, debt, nav, f: pfm.Financials | None, capex, ncf, moic, irr, coc,
             value_sub: str = "", debt_sub: str = "", nav_sub: str = "", ops_sub: str = "last 12 months",
             moic_sub: str = "", irr_sub: str = "") -> None:
        def money(v):
            return fmt_money(v) if v is not None else "—"

        def sign(v):
            return None if v is None else POSITIVE if v >= 0 else NEGATIVE

        self.set("value", money(value), value_sub)
        self.set("debt", money(debt), debt_sub)
        self.set("nav", money(nav), nav_sub)
        rev, opex, ebitda, nonop = ((f.revenue_ltm, f.opex_ltm, f.ebitda_ltm, f.nonop_ltm) if f is not None
                                    else (None, None, None, None))
        sub = ops_sub if f is not None else "link company books"
        self.set("revenue", money(rev), sub)
        self.set("opex", money(opex), sub)
        self.set("ebitda", money(ebitda), sub, sign(ebitda))
        self.set("nonop", money(nonop), sub)
        self.set("capex", money(capex), "per year" if capex is not None else "")
        self.set("ncf", money(ncf), "EBITDA − Non-Op − CapEx" if ncf is not None else "", sign(ncf))
        self.set("moic", _multiple(moic), moic_sub)
        self.set("irr", _pct(irr), irr_sub, sign(irr))
        self.set("coc", _pct(coc), "net cash flow ÷ invested" if coc is not None else "", sign(coc))


class MonthlyChart(QWidget):
    """Revenue bars and net income markers for the last 12 months."""

    def __init__(self):
        super().__init__()
        self.data: list[tuple[str, int, int]] = []
        self.setMinimumHeight(170)

    def set_data(self, data: list[tuple[str, int, int]]) -> None:
        self.data = data
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.palette().text().color()
        if not self.data:
            p.setPen(self.palette().placeholderText().color())
            p.drawText(6, 24, "Link the company's books to see its monthly revenue and net income")
            return
        top, bottom, left = 22, self.height() - 22, 8
        vals = [v for _, a, b in self.data for v in (a, b)]
        hi, lo = max(max(vals), 0), min(min(vals), 0)
        span = (hi - lo) or 1
        zero_y = top + (bottom - top) * hi / span
        n = len(self.data)
        slot = (self.width() - left * 2) / n
        p.setPen(self.palette().mid().color())
        p.drawLine(int(left), int(zero_y), int(self.width() - left), int(zero_y))
        for i, (label, rev, ni) in enumerate(self.data):
            x = left + i * slot
            h = (bottom - top) * rev / span
            p.fillRect(QRectF(x + slot * 0.18, zero_y - max(h, 0), slot * 0.5, abs(h)), QColor(PALETTE[0]))
            ny = zero_y - (bottom - top) * ni / span
            p.setBrush(QColor(PALETTE[1] if ni >= 0 else "#b3261e"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x + slot * 0.43 - 4, ny - 4, 8, 8))
            p.setPen(text)
            p.drawText(QRectF(x, bottom + 2, slot, 18), int(Qt.AlignmentFlag.AlignHCenter), label)
        p.setPen(text)
        p.fillRect(QRectF(left, 4, 10, 10), QColor(PALETTE[0]))
        p.drawText(int(left + 14), 13, f"Revenue (max {short(hi)})")
        p.setBrush(QColor(PALETTE[1]))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QRectF(left + 170, 4, 10, 10))
        p.setPen(text)
        p.drawText(int(left + 186), 13, "Net income")


class AllocationBar(QWidget):
    """A stacked horizontal bar with a legend: share of NAV by type or by holding."""

    def __init__(self):
        super().__init__()
        self.parts: list[tuple[str, int]] = []
        self.setMinimumHeight(70)

    def set_parts(self, parts: list[tuple[str, int]]) -> None:
        self.parts = [(n, v) for n, v in parts if v > 0]
        rows = (len(self.parts) + 2) // 3
        self.setMinimumHeight(34 + max(1, rows) * 20)
        self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        total = sum(v for _, v in self.parts)
        w = self.width() - 2
        if not total:
            p.setPen(self.palette().placeholderText().color())
            p.drawText(4, 20, "No value yet — record valuations or link company books")
            return
        x = 1.0
        for i, (_n, v) in enumerate(self.parts):
            seg = w * v / total
            p.fillRect(QRectF(x, 2, max(seg - 2, 1), 22), QColor(PALETTE[i % len(PALETTE)]))
            x += seg
        f = QFont(self.font())
        p.setFont(f)
        col_w = max(1, w // 3)
        for i, (n, v) in enumerate(self.parts):
            cx, cy = 1 + (i % 3) * col_w, 32 + (i // 3) * 20
            p.fillRect(QRectF(cx, cy + 3, 10, 10), QColor(PALETTE[i % len(PALETTE)]))
            p.setPen(self.palette().text().color())
            p.drawText(int(cx + 16), int(cy + 12), f"{n} · {v * 100 / total:.0f}% · {fmt_money(v)}")


def _table(cols: list[str], stretch: int | None = None) -> QTableWidget:
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setAlternatingRowColors(True)
    if stretch is not None:
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


def fit_name_columns(t: QTableWidget, min_width: int = 90, max_lines: int = 2) -> None:
    """Columns as wide as their numbers; each column's heading (a company name) wraps onto up to two lines and is
    cut with "…" if it still doesn't fit — the full name shows when you hover over it."""
    from PySide6.QtGui import QFontMetrics

    head = t.horizontalHeader()
    hfm = QFontMetrics(head.font())
    for c in range(t.columnCount()):
        widest = 0
        for r in range(t.rowCount()):
            it = t.item(r, c)
            if it is not None and it.text():
                widest = max(widest, QFontMetrics(it.font()).horizontalAdvance(it.text()))
        hi = t.horizontalHeaderItem(c)
        if c == 0:  # the row labels
            t.setColumnWidth(0, max(widest + 24, 110))
            continue
        width = max(widest + 24, min_width)
        t.setColumnWidth(c, width)
        if hi is None:
            continue
        full = hi.data(Qt.ItemDataRole.UserRole) or hi.text()
        hi.setData(Qt.ItemDataRole.UserRole, full)
        hi.setToolTip(full)
        room = width - 12
        lines, line = [], ""
        for word in full.split():
            trial = f"{line} {word}".strip()
            if hfm.horizontalAdvance(trial) <= room or not line:
                line = trial
            else:
                lines.append(line)
                line = word
        if line:
            lines.append(line)
        if len(lines) > max_lines:
            lines = lines[:max_lines - 1] + [" ".join(lines[max_lines - 1:])]
        lines = [hfm.elidedText(x, Qt.TextElideMode.ElideRight, room) for x in lines]
        hi.setText("\n".join(lines))


def _set_row(t: QTableWidget, r: int, vals: list, money_cols: set[int], *, bold: bool = False,
             colour_col: int | None = None, colour_value: float | None = None) -> None:
    for c, v in enumerate(vals):
        cell = QTableWidgetItem("" if v is None else str(v))
        if c in money_cols:
            cell.setTextAlignment(ALIGN_RIGHT)
        if bold:
            f = cell.font()
            f.setBold(True)
            cell.setFont(f)
        if c == colour_col and colour_value is not None:
            cell.setForeground(POSITIVE if colour_value >= 0 else NEGATIVE)
        t.setItem(r, c, cell)


SUM_ROWS = ["Type", "Held by", "",
            "Value", "Debt", "NAV", "",
            "Revenue", "Op. Ex", "EBITDA", "",
            "Non-Op. Ex", "CapEx", "Net cash flow", "",
            "MOIC", "IRR", "Cash on cash return", "",
            "Lender(s)", "Principal", "Balance outstanding", "Interest rate", "Annual interest", "Maturity"]

HOLD_COLS = ["Holding", "Type", "Own %", "Invested", "Returned", "Value (100%)", "Value date / method",
             "Our value (NAV)", "Gain", "MOIC", "IRR", "Revenue LTM", "Net income LTM", "Linked books"]


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


class AssetManagementView(QWidget):
    open_company = Signal(str)   # switch to Accounting with this company file
    open_loans = Signal(str)     # … and open its Loans window (record loan payments)

    def __init__(self, portfolio_path: str | Path, companies_fn, create_company=None):
        super().__init__()
        self.pf = pfm.Portfolio(portfolio_path)
        self.companies_fn = companies_fn  # () -> [(name, path, role)]
        self.create_company = create_company
        self.summary: pfm.Summary | None = None
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        title = QLabel("Portfolio")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.5)
        f.setBold(True)
        title.setFont(f)
        bar.addWidget(title)
        bar.addSpacing(16)
        bar.addWidget(QLabel("As of"))
        self.as_of = QDateEdit(QDate.currentDate())
        self.as_of.setCalendarPopup(True)
        self.as_of.setDisplayFormat("MMM d, yyyy")
        self.as_of.dateChanged.connect(self.refresh)
        bar.addWidget(self.as_of)
        self.show_exited = QCheckBox("Include exited")
        self.show_exited.setChecked(True)
        self.show_exited.toggled.connect(self.refresh)
        bar.addWidget(self.show_exited)
        bar.addSpacing(16)
        bar.addWidget(QLabel("Viewing:"))
        self.viewing = QComboBox()
        self.viewing.setMinimumWidth(260)
        self.viewing.setToolTip("All operating companies or one; all holding companies' shareholdings or one's; the "
                                "companies all asset managers manage or one's")
        self.viewing.activated.connect(lambda *_: self.refresh())
        bar.addWidget(self.viewing)
        bar.addSpacing(16)
        self.auto_sync = QCheckBox("Pull in all operating companies")
        self.auto_sync.setToolTip("Every company set up as an operating company is added to the portfolio "
                                  "automatically, linked to its books (revenue, expenses, EBITDA, capex, cash, debt, "
                                  "book value). A holding you delete stays out.")
        self.auto_sync.setChecked(self.pf.auto_sync)
        self.auto_sync.toggled.connect(self._auto_sync_toggled)
        bar.addWidget(self.auto_sync)
        bar.addStretch(1)
        for text, slot, primary in [("New Holding…", self.new_holding, True), ("Export Excel…", self.export, False)]:
            b = QPushButton(text)
            if primary:
                b.setObjectName("primary")
            b.clicked.connect(slot)
            bar.addWidget(b)
        lay.addLayout(bar)

        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        # Overview
        ov = QWidget()
        ol = QVBoxLayout(ov)
        self.grouped = GroupedSummary()
        ol.addWidget(self.grouped)
        ol.addWidget(QLabel("<b>Allocation by type</b>"))
        self.alloc_type = AllocationBar()
        ol.addWidget(self.alloc_type)
        ol.addWidget(QLabel("<b>Allocation by holding</b>"))
        self.alloc_holding = AllocationBar()
        ol.addWidget(self.alloc_holding)
        self.overview_table = _table([""], None)
        self.overview_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectColumns)
        self.overview_table.setToolTip("Double-click a company's column for its overview")
        self.overview_table.doubleClicked.connect(self._open_company_overview)
        osplit = QSplitter(Qt.Orientation.Vertical)
        osplit.addWidget(self.overview_table)
        su_box = QWidget()
        sl = QVBoxLayout(su_box)
        sl.setContentsMargins(0, 6, 0, 0)
        sl.addWidget(QLabel("<b>Sources & uses</b> (each company 100%, portfolio at our share; double-click a "
                            "company's column to change its figures)"))
        self.su_table = _table([""], None)
        self.su_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectColumns)
        self.su_table.doubleClicked.connect(self._edit_sources_uses)
        sl.addWidget(self.su_table, 1)
        osplit.addWidget(su_box)
        osplit.setSizes([500, 380])
        ol.addWidget(osplit, 1)
        self.tabs.addTab(_scroll(ov), "Portfolio Summary")

        # Company overview (one operating company)
        co = QWidget()
        cl0 = QVBoxLayout(co)
        pick = QHBoxLayout()
        self.co_pick = QComboBox()  # hidden: the company shown follows Viewing / the Company list at the top
        self.co_pick.setVisible(False)
        self.co_title = QLabel()
        ft = self.co_title.font()
        ft.setPointSizeF(ft.pointSizeF() * 1.25)
        ft.setBold(True)
        self.co_title.setFont(ft)
        pick.addWidget(self.co_title)
        self.co_pick.currentIndexChanged.connect(lambda *_: self._fill_company())
        self.co_pick.currentIndexChanged.connect(lambda *_: self._sync_cv_pick())
        pick.addWidget(self.co_pick)  # stays hidden
        self.co_info = QLabel()
        self.co_info.setTextFormat(Qt.TextFormat.RichText)
        self.co_info.setWordWrap(True)
        pick.addWidget(self.co_info, 1)
        for text, slot in [("Projections…", self._co_projections), ("Add Valuation…", self._co_add_valuation),
                           ("Edit…", self._co_edit), ("Open Company Books", self._co_open_books)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            pick.addWidget(b)
        cl0.addLayout(pick)
        cc = QHBoxLayout()
        self.co_grouped = GroupedSummary()
        cc.addWidget(self.co_grouped)
        cl0.addLayout(cc)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("<b>Existing financing</b> (summary — terms, amortization tables and the cap table are "
                              "on Financing & Cap Table)"))
        frow.addStretch(1)
        go = QPushButton("Financing && Cap Table →")
        go.clicked.connect(lambda: self._go_financing())
        frow.addWidget(go)
        cl0.addLayout(frow)
        self.co_loans = _table(LOAN_SUMMARY_COLS, None)
        self.co_loans.doubleClicked.connect(lambda i: self._go_financing(i.row()))
        self.co_loans.setToolTip("Double-click a loan for its terms and amortization table")
        self._co_loan_rows: list[pfm.Loan] = []
        cl0.addWidget(self.co_loans)
        cl0.addWidget(QLabel("<b>Last 12 months</b> (from the company's books)"))
        self.co_chart = MonthlyChart()
        cl0.addWidget(self.co_chart)
        cl0.addWidget(QLabel("<b>Month by month</b> (from the company's books)"))
        self.co_months = _table([""], None)
        self.co_months.setMaximumHeight(270)
        cl0.addWidget(self.co_months)
        cl0.addWidget(QLabel("<b>Projections</b> (100% of the company; click Projections… to enter future years)"))
        self.co_proj = _table([""], None)
        self.co_proj.setMaximumHeight(200)
        cl0.addWidget(self.co_proj)
        cl0.addWidget(QLabel("<b>Valuations</b>"))
        self.co_vals = _table(["Date", "Company value", "Debt", "Equity value (100%)", "Our NAV", "Method", "Note"], 6)
        cl0.addWidget(self.co_vals, 1)
        for t_, h_ in ((self.co_months, 150), (self.co_proj, 150), (self.co_vals, 110)):
            t_.setMinimumHeight(h_)
        self.co_chart.setMinimumHeight(max(self.co_chart.minimumHeight(), 150))
        self.overview_table.setMinimumHeight(220)
        self.su_table.setMinimumHeight(200)
        self.tabs.addTab(_scroll(co), "Company Overview")
        self.tabs.addTab(self._build_parties_tab(), "Customers && Vendors")
        self.financing_page = self._build_financing_tab()
        self.tabs.addTab(self.financing_page, "Financing && Cap Table")
        from .operations_tab import CapexPage, CashFlowPage, DashboardPage, OperationsPage

        self.dash_page = DashboardPage(self)
        self.tabs.addTab(self.dash_page, "Dashboard")
        self.ops_page = OperationsPage(self)
        self.tabs.addTab(self.ops_page, "Rent Roll && Operations")
        self.cash_page = CashFlowPage(self)
        self.tabs.addTab(self.cash_page, "Cash Flow")
        self.capex_page = CapexPage(self)
        self.tabs.addTab(self.capex_page, "CapEx")
        pages = (self.dash_page, self.ops_page, self.cash_page, self.capex_page)
        self.tabs.currentChanged.connect(  # each property page shows the latest figures when it's opened
            lambda i: self.tabs.widget(i).fill() if self.tabs.widget(i) in pages else None)
        self.tabs.addTab(self._build_waterfall_tab(), "Waterfall (LP / GP)")
        pv = QWidget()
        pl = QVBoxLayout(pv)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Projections for:"))
        self.proj_pick = QComboBox()
        self.proj_pick.setMinimumWidth(280)
        self.proj_pick.currentIndexChanged.connect(lambda *_: self._fill_projections())
        prow.addWidget(self.proj_pick)
        edit_p = QPushButton("Edit Projections…")
        edit_p.clicked.connect(self._edit_projections)
        prow.addWidget(edit_p)
        prow.addStretch(1)
        pl.addLayout(prow)
        self.proj_note = QLabel()
        self.proj_note.setObjectName("hint")
        self.proj_note.setWordWrap(True)
        pl.addWidget(self.proj_note)
        self.proj_table = _table([""], None)
        pl.addWidget(self.proj_table, 1)
        self.tabs.addTab(pv, "Projections")

        # Holdings
        hv = QWidget()
        hl = QVBoxLayout(hv)
        split = QSplitter(Qt.Orientation.Vertical)
        self.table = _table(HOLD_COLS, len(HOLD_COLS) - 1)
        self.table.itemSelectionChanged.connect(self._show_detail)
        self.table.doubleClicked.connect(self.edit_holding)
        split.addWidget(self.table)
        detail = QWidget()
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 6, 0, 0)
        self.detail_title = QLabel()
        self.detail_title.setTextFormat(Qt.TextFormat.RichText)
        dl.addWidget(self.detail_title)
        self.financials = QLabel()
        self.financials.setTextFormat(Qt.TextFormat.RichText)
        self.financials.setWordWrap(True)
        dl.addWidget(self.financials)
        lists = QHBoxLayout()
        vbox = QVBoxLayout()
        vbox.addWidget(QLabel("<b>Valuations</b> (100% equity value)"))
        self.val_table = _table(["Date", "Value (100%)", "Our share", "Method", "Note"], 4)
        vbox.addWidget(self.val_table)
        vb = QHBoxLayout()
        for text, slot in [("Add Valuation…", self.add_valuation), ("Delete", self.delete_valuation)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            vb.addWidget(b)
        vb.addStretch(1)
        vbox.addLayout(vb)
        fbox = QVBoxLayout()
        fbox.addWidget(QLabel("<b>Capital flows</b> (from the holding company's side; “(books)” = read from the "
                              "holding company's books)"))
        self.flow_table = _table(["Date", "Kind", "Amount", "In (+) / out (−)", "Note"], 4)
        fbox.addWidget(self.flow_table)
        fb = QHBoxLayout()
        for text, slot in [("Add Contribution / Distribution…", self.add_flow), ("Delete", self.delete_flow)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            fb.addWidget(b)
        fb.addStretch(1)
        fbox.addLayout(fb)
        lists.addLayout(vbox, 1)
        lists.addLayout(fbox, 1)
        dl.addLayout(lists, 1)
        split.addWidget(detail)
        split.setSizes([300, 360])
        hl.addWidget(split, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        for text, slot in [("Edit Holding…", self.edit_holding), ("Open Company Books", self.open_books),
                           ("Delete Holding", self.delete_holding)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            actions.addWidget(b)
        hl.addLayout(actions)
        self.holdings_page = hv
        self.tabs.addTab(hv, "Holdings")

        # Consolidated
        cv = QWidget()
        cl = QVBoxLayout(cv)
        note = QLabel("Financials from each holding's linked company books (last twelve months to the as-of date; "
                      "balances at that date), and the portfolio total at our ownership share.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        cl.addWidget(note)
        self.cons_table = _table(["Company", "Own %", "Revenue LTM", "Net income LTM", "Cash", "Receivables",
                                  "Total assets", "Payables", "Debt", "Total liabilities", "Equity"], 0)
        cl.addWidget(self.cons_table, 1)
        self.tabs.addTab(cv, "Consolidated Financials")

        # Managed companies (asset managers)
        mv = QWidget()
        ml = QVBoxLayout(mv)
        mrow = QHBoxLayout()
        mrow.addWidget(QLabel("Asset manager:"))
        self.mgr = QComboBox()
        self.mgr.setMinimumWidth(220)
        self.mgr.activated.connect(lambda *_: self._fill_managed())
        mrow.addWidget(self.mgr)
        mrow.addWidget(QLabel("Fees billed from"))
        self.fee_start = QDateEdit(QDate(QDate.currentDate().year(), 1, 1))
        self.fee_end = QDateEdit(QDate.currentDate())
        for w in (self.fee_start, self.fee_end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("MMM d, yyyy")
            w.dateChanged.connect(lambda *_: self._fill_managed())
        mrow.addWidget(self.fee_start)
        mrow.addWidget(QLabel("to"))
        mrow.addWidget(self.fee_end)
        mrow.addStretch(1)
        bill = QPushButton("Bill Management Fees…")
        bill.setObjectName("primary")
        bill.setToolTip("Invoice each managed company its management fee for a period, in the asset manager's books")
        bill.clicked.connect(self.bill_fees)
        mrow.addWidget(bill)
        ml.addLayout(mrow)
        self.managed_note = QLabel()
        self.managed_note.setObjectName("hint")
        self.managed_note.setWordWrap(True)
        ml.addWidget(self.managed_note)
        self.managed_table = _table(["Company", "Type", "Held by", "Value (100%)", "Fee %", "Fixed / month",
                                     "Annual fee", "Billed (period)", "Paid"], 0)
        ml.addWidget(self.managed_table, 1)
        self.managed_page = mv
        self.tabs.addTab(mv, "Managed Companies")
        self.setStyleSheet("QFrame#card { background: palette(base); border: 1px solid palette(midlight); "
                           "border-radius: 8px; } QLabel#cardtitle { color: palette(placeholder-text); }")
        self.refresh()

    # -- data -----------------------------------------------------------------

    def close_portfolio(self) -> None:
        self.pf.close()

    def _as_of_date(self) -> date:
        return date.fromisoformat(_iso(self.as_of.date()))

    def _companies(self) -> list[tuple[str, str, str]]:
        return [c if len(c) == 3 else (c[0], c[1], "operating") for c in self.companies_fn()]

    def _fill_viewing(self) -> None:
        """Grouped like the Company list: each group as a whole, then its companies one by one."""
        from PySide6.QtGui import QStandardItemModel

        want = self.viewing.currentData()
        self.viewing.blockSignals(True)
        self.viewing.clear()
        comps = self._companies()
        groups = [("operating", "All operating companies", None, "company"),
                  ("holding", "All holding companies", ("owners", ""), "owner"),
                  ("asset_manager", "All asset managers", ("managers", ""), "manager")]
        model = self.viewing.model()
        for role, title, data, kind in groups:
            members = [(n, p) for n, p, r in comps if r == role]
            if role != "operating" and not members:
                continue
            if self.viewing.count():
                self.viewing.insertSeparator(self.viewing.count())
            self.viewing.addItem(title, data)
            if isinstance(model, QStandardItemModel):
                it = model.item(self.viewing.count() - 1)
                f = it.font()
                f.setBold(True)
                it.setFont(f)
            for n, p in members:
                self.viewing.addItem("    " + n, (kind, p))
        want = tuple(want) if want else None  # Qt gives the (kind, path) back as a list
        i = next((k for k in range(self.viewing.count())
                  if want and self.viewing.itemData(k) and tuple(self.viewing.itemData(k)) == want), 0)
        self.viewing.setCurrentIndex(i)
        self.viewing.blockSignals(False)

    def _filter(self) -> dict:
        v = self.viewing.currentData()
        if not v:
            return {}
        kind, path = v[0], v[1]
        return {"owners": {"any_owner": True}, "managers": {"any_manager": True}}.get(kind) or {kind: path}

    def focus_company(self, path: str, role: str) -> str:
        """Show the company picked at the top: an operating company's overview, a holding company's
        shareholdings or an asset manager's managed companies. Returns a note when there's nothing to show."""
        def same(a, b):
            return pfm._same_file(str(a or ""), str(b or ""))

        if role in ("holding", "asset_manager"):
            kind = "owner" if role == "holding" else "manager"
            self._fill_viewing()
            i = next((k for k in range(self.viewing.count()) if self.viewing.itemData(k)
                      and self.viewing.itemData(k)[0] == kind and same(self.viewing.itemData(k)[1], path)), 0)
            self.viewing.setCurrentIndex(i)
            self.refresh()
            self.tabs.setCurrentWidget(self.managed_page if role == "asset_manager" else self.tabs.widget(0))
            return ""
        self._fill_viewing()
        i = next((k for k in range(self.viewing.count()) if self.viewing.itemData(k)
                  and self.viewing.itemData(k)[0] == "company" and same(self.viewing.itemData(k)[1], path)), 0)
        self.viewing.setCurrentIndex(i)  # Viewing: this company (like the Company list)
        self.refresh()
        perf = next((p for p in (self.summary.performances if self.summary else [])
                     if same(p.holding.company_path, path)), None)
        if perf is None:
            self.viewing.setCurrentIndex(0)
            self.refresh()
            self.tabs.setCurrentIndex(0)
            return "This company isn't in the portfolio — tick “Pull in all operating companies” or add it with " \
                   "New Holding…."
        self.co_pick.setCurrentIndex(self.co_pick.findData(perf.holding.id))
        self.cv_pick.setCurrentIndex(self.cv_pick.findData(perf.holding.id))
        if self.tabs.currentIndex() in (0, self.tabs.indexOf(self.managed_page)):
            self.tabs.setCurrentIndex(1)  # Company Overview (stays on Customers & Vendors / Projections… if open)
        return ""

    def _auto_sync_toggled(self, on: bool) -> None:
        self.pf.auto_sync = on
        if on:
            self.pf._set_skipped(set())  # bring back any you removed earlier
        self.refresh()

    def sync_companies(self) -> list[str]:
        try:
            return pfm.sync_operating_companies(self.pf, self._companies())
        except (OSError, ValueError):
            return []

    def refresh(self, *_) -> None:
        self.sync_companies()
        self._fill_viewing()
        keep = self._selected_id()
        s = self.summary = pfm.summary(self.pf, self._as_of_date(), include_exited=self.show_exited.isChecked(),
                                       **self._filter())
        c = s.consolidated
        active = sum(1 for p in s.performances if p.holding.status == "active")
        self.grouped.fill(
            value=s.value_share, debt=s.debt_share, nav=s.nav, f=c,
            capex=s.capex_req_share if c else None, ncf=s.net_cash_flow_share if c else None,
            moic=s.moic, irr=s.irr, coc=s.cash_on_cash,
            value_sub=f"our share · 100%: {short(s.company_value)}",
            debt_sub=(f"our share · LTV {s.debt_share / s.value_share * 100:.0f}%" if s.value_share else "our share"),
            nav_sub=f"{active} active holding(s)", ops_sub="last 12 months, our share",
            moic_sub=f"{short(s.invested)} in · {short(s.returned)} out", irr_sub=f"gain {short(s.gain)}")
        self.alloc_type.set_parts(sorted(s.by_type.items(), key=lambda kv: -kv[1]))
        self.alloc_holding.set_parts(sorted(((p.holding.name, p.nav) for p in s.performances), key=lambda kv: -kv[1]))
        self._fill_holdings(self.table, s)
        self._fill_summary(s)
        self._fill_sources_uses(s)
        self._fill_company_pick(s)
        self._fill_consolidated(s)
        self._fill_managers()
        self._fill_managed()
        self._fill_wf_picker()
        self._fill_proj_picker()
        self._follow_viewing()
        if keep is not None:
            for r, p in enumerate(s.performances):
                if p.holding.id == keep:
                    self.table.selectRow(r)
        self._show_detail()

    def _fill_holdings(self, t: QTableWidget, s: pfm.Summary) -> None:
        perfs = s.performances
        t.setRowCount(len(perfs) + (1 if perfs else 0))
        money = {3, 4, 5, 7, 8, 9, 10, 11, 12}
        for r, p in enumerate(perfs):
            h, f = p.holding, p.financials
            val = "" if p.value_100 is None else fmt_money(p.value_100)
            linked = (f.company if f and not f.error else ("⚠ " + f.error if f else "")) if h.company_path else ""
            _set_row(t, r, [h.name + (" (exited)" if h.status == "exited" else ""), h.type, f"{h.ownership_pct:g}%",
                            fmt_money(p.invested), fmt_money(p.returned), val,
                            f"{p.value_date} · {p.value_method}" if p.value_date else "No valuation yet",
                            fmt_money(p.nav), fmt_money(p.gain), _multiple(p.moic), _pct(p.irr),
                            fmt_money(f.revenue_ltm) if f and not f.error else "",
                            fmt_money(f.net_income_ltm) if f and not f.error else "", linked],
                     money, colour_col=8, colour_value=p.gain)
            if h.status == "exited":
                t.item(r, 0).setForeground(MUTED)
        if perfs:
            _set_row(t, len(perfs), ["Portfolio", "", "", fmt_money(s.invested), fmt_money(s.returned), "", "",
                                     fmt_money(s.nav), fmt_money(s.gain), _multiple(s.moic), _pct(s.irr), "", "", ""],
                     money, bold=True)
        for c, w in enumerate([220, 130, 60, 115, 115, 120, 190, 125, 110, 60, 65, 115, 120]):
            t.setColumnWidth(c, w)

    # -- waterfall ----------------------------------------------------------------

    def _build_waterfall_tab(self) -> QWidget:
        from PySide6.QtWidgets import QGroupBox

        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        top.addWidget(QLabel("Waterfall for:"))
        self.wf_pick = QComboBox()
        self.wf_pick.setMinimumWidth(280)
        self.wf_pick.currentIndexChanged.connect(lambda *_: self._wf_owner_changed())
        top.addWidget(self.wf_pick)
        top.addWidget(QLabel("Holdings:"))
        self.wf_scope = QComboBox()
        self.wf_scope.setMinimumWidth(240)
        self.wf_scope.currentIndexChanged.connect(lambda *_: self._fill_waterfall())
        top.addWidget(self.wf_scope)
        top.addStretch(1)
        lay.addLayout(top)
        terms = QGroupBox("Terms (per holding company)")
        tl = QHBoxLayout(terms)
        self.wf_lp = QDoubleSpinBox()
        self.wf_lp.setRange(0, 100)
        self.wf_lp.setSuffix(" % LP capital")
        self.wf_pref = QDoubleSpinBox()
        self.wf_pref.setRange(0, 99)
        self.wf_pref.setSuffix(" % preferred return")
        self.wf_cu = QDoubleSpinBox()
        self.wf_cu.setRange(0, 100)
        self.wf_cu.setSuffix(" % GP catch-up")
        self.wf_carry = QDoubleSpinBox()
        self.wf_carry.setRange(0, 99)
        self.wf_carry.setSuffix(" % carried interest")
        self.wf_gp = QLineEdit()
        self.wf_gp.setPlaceholderText("GP (blank: the asset manager)")
        for wdg in (self.wf_lp, self.wf_pref, self.wf_cu, self.wf_carry):
            wdg.setDecimals(2)
            tl.addWidget(wdg)
        tl.addWidget(self.wf_gp, 1)
        self.wf_save = QPushButton("Save Terms")
        self.wf_save.clicked.connect(self._save_terms)
        tl.addWidget(self.wf_save)
        self.wf_terms_box = terms
        lay.addWidget(terms)
        cards = QHBoxLayout()
        self.wf_cards = {k: Card(t) for k, t in [("dist", "Distributable"), ("lp", "To LPs"), ("gp", "To GP"),
                                                 ("promote", "GP promote"), ("lpret", "LP MOIC / IRR"),
                                                 ("gpret", "GP MOIC / IRR")]}
        tips = {"dist": "Everything distributed so far plus today's NAV (a hypothetical sale today)",
                "promote": "What the GP gets beyond its share of the capital: catch-up + carried interest"}
        for k, c in self.wf_cards.items():
            c.setToolTip(tips.get(k, ""))
            cards.addWidget(c)
        lay.addLayout(cards)
        self.wf_bar = AllocationBar()
        lay.addWidget(self.wf_bar)
        lay.addWidget(QLabel("<b>Tiers</b>"))
        self.wf_table = _table(["Tier", "Total", "To LPs", "To GP", "How"], 4)
        self.wf_table.setMaximumHeight(190)
        lay.addWidget(self.wf_table)
        self.wf_parts_label = QLabel()
        lay.addWidget(self.wf_parts_label)
        self.wf_parts = _table(["", "Capital", "Distributable", "Preferred return", "To LPs", "To GP", "GP promote",
                                "LP MOIC", "LP IRR", "GP MOIC"], None)
        lay.addWidget(self.wf_parts, 1)
        note = QLabel("A hypothetical sale today: everything distributed so far plus today's NAV goes through the tiers "
                      "— 1. return of capital, 2. preferred return (on each contribution from its date, compounding "
                      "annually), 3. GP catch-up, 4. carried interest. Capital and pro-rata tiers are shared LP / GP "
                      "in proportion to the capital each put in.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        lay.addWidget(note)
        return w

    def _holdcos(self) -> list[tuple[str, str]]:
        return [(n, p) for n, p, role in self._companies() if role == "holding"]

    def _fill_wf_picker(self) -> None:
        want = self.wf_pick.currentData()
        self.wf_pick.blockSignals(True)
        self.wf_pick.clear()
        self.wf_pick.addItem("Overall project (all holding companies)", "__all__")
        for n, p in self._holdcos():
            self.wf_pick.addItem(n, p)
        v = self.viewing.currentData()
        if v and v[0] == "owner":
            want = v[1]
        i = self.wf_pick.findData(want)
        self.wf_pick.setCurrentIndex(i if i >= 0 else 0)
        self.wf_pick.blockSignals(False)
        self._wf_owner_changed()

    def _wf_owner_changed(self) -> None:
        owner = self.wf_pick.currentData()
        overall = owner in (None, "__all__")
        self.wf_scope.blockSignals(True)
        self.wf_scope.clear()
        if overall:
            self.wf_scope.addItem("All holding companies", None)
        else:
            self.wf_scope.addItem("All its holdings (fund level)", None)
            for h in pfm.select(self.pf, owner=owner):
                self.wf_scope.addItem(f"{h.name} (deal by deal)", h.id)
        self.wf_scope.blockSignals(False)
        self.wf_terms_box.setEnabled(not overall)
        if not overall:
            t = pfm.get_terms(self.pf, owner)
            self.wf_lp.setValue(t.lp_pct)
            self.wf_pref.setValue(t.pref_pct)
            self.wf_cu.setValue(t.catchup_pct)
            self.wf_carry.setValue(t.carry_pct)
            self.wf_gp.setText(t.gp_name)
        self._fill_waterfall()

    def _save_terms(self) -> None:
        owner = self.wf_pick.currentData()
        if owner in (None, "__all__"):
            return
        try:
            pfm.set_terms(self.pf, owner, pfm.Terms(self.wf_lp.value(), self.wf_pref.value(), self.wf_cu.value(),
                                                    self.wf_carry.value(), self.wf_gp.text()))
        except ValueError as e:
            error(self, "Couldn't save the terms", str(e))
            return
        self._fill_waterfall()

    def _fill_waterfall(self) -> None:
        owner = self.wf_pick.currentData()
        as_of = self._as_of_date()
        names = {p: n for n, p in self._holdcos()}
        if owner in (None, "__all__"):
            w, parts = pfm.overall_waterfall(self.pf, [p for _n, p in self._holdcos()], as_of)
            rows = [(names.get(o, o), x) for o, x in parts]
            self.wf_parts_label.setText("<b>By holding company</b> (each under its own terms)")
            self.wf_parts.setHorizontalHeaderItem(0, QTableWidgetItem("Holding company"))
        else:
            hid = self.wf_scope.currentData()
            w = pfm.holding_waterfall(self.pf, owner, as_of, holding_id=hid)
            rows = [(h.name, pfm.holding_waterfall(self.pf, owner, as_of, holding_id=h.id))
                    for h in pfm.select(self.pf, owner=owner) if hid is None or h.id == hid]
            self.wf_parts_label.setText("<b>Deal by deal</b> (each holding on its own, same terms)")
            self.wf_parts.setHorizontalHeaderItem(0, QTableWidgetItem("Holding"))
        gp = w.terms.gp_name or "GP"
        self.wf_cards["dist"].set(fmt_money(w.distributable), f"capital {short(w.capital)} · profit {short(w.profit)}")
        self.wf_cards["lp"].set(fmt_money(w.lp_total), w.terms.lp_name if owner not in (None, "__all__") else "",
                                POSITIVE)
        self.wf_cards["gp"].set(fmt_money(w.gp_total), gp)
        self.wf_cards["promote"].set(fmt_money(w.gp_promote), "catch-up + carried interest")
        self.wf_cards["lpret"].set(f"{_multiple(w.lp_moic)} · {_pct(w.lp_irr)}", f"on {short(w.lp_capital)} in")
        self.wf_cards["gpret"].set(f"{_multiple(w.gp_moic)} · {_pct(w.gp_irr)}",
                                   f"on {short(w.gp_capital)} in" if w.gp_capital else "no GP capital")
        self.wf_bar.set_parts([("LPs", w.lp_total), (f"{gp}", w.gp_total)])
        t = self.wf_table
        t.setRowCount(len(w.tiers) + 1)
        for r_, tier in enumerate(w.tiers):
            note = tier.note
            if tier.name == "Preferred return":
                note += f" · owed {fmt_money(w.pref)}"
            _set_row(t, r_, [f"{r_ + 1}. {tier.name}", fmt_money(tier.total), fmt_money(tier.lp), fmt_money(tier.gp),
                             note], {1, 2, 3})
        _set_row(t, len(w.tiers), ["Total", fmt_money(w.distributable), fmt_money(w.lp_total), fmt_money(w.gp_total),
                                   ""], {1, 2, 3}, bold=True)
        for c_, wd in enumerate([200, 140, 140, 140]):
            t.setColumnWidth(c_, wd)
        pt = self.wf_parts
        pt.setRowCount(len(rows))
        for r_, (name, x) in enumerate(rows):
            _set_row(pt, r_, [name, fmt_money(x.capital), fmt_money(x.distributable), fmt_money(x.pref),
                              fmt_money(x.lp_total), fmt_money(x.gp_total), fmt_money(x.gp_promote),
                              _multiple(x.lp_moic), _pct(x.lp_irr), _multiple(x.gp_moic)], set(range(1, 10)))
        for c_, wd in enumerate([220, 120, 130, 130, 130, 120, 120, 80, 80, 80]):
            pt.setColumnWidth(c_, wd)

    def _fill_proj_picker(self) -> None:
        want = self.proj_pick.currentData()
        self.proj_pick.blockSignals(True)
        self.proj_pick.clear()
        self.proj_pick.addItem("Portfolio (our share)", None)
        for p in (self.summary.performances if self.summary else []):
            self.proj_pick.addItem(p.holding.name, p.holding.id)
        i = self.proj_pick.findData(want)
        self.proj_pick.setCurrentIndex(i if i >= 0 else 0)
        self.proj_pick.blockSignals(False)
        self._fill_projections()

    def _fill_projections(self) -> None:
        hid = self.proj_pick.currentData()
        perfs = self.summary.performances if self.summary else []
        if hid is None:
            rows = list(pfm.portfolio_projections(self.pf, **self._filter()).values())
            c = self.summary.consolidated if self.summary else None
            fill_projection_table(self.proj_table, c, rows, self.summary.capex_req_share if c else None)
            self.proj_note.setText("Every active holding's projected years at our ownership share, added up, next to the "
                                   "last 12 months actual. Pick a company to see (and edit) its own.")
        else:
            p = next((x for x in perfs if x.holding.id == hid), None)
            if p is None:
                return
            fill_projection_table(self.proj_table, p.financials if p.has_books else None,
                                  pfm.projections(self.pf, hid), p.capex_req if p.has_books else None)
            self.proj_note.setText(f"{p.holding.name} at 100%. Net cash flow = EBITDA − Non-Op. Ex − CapEx.")

    def _edit_projections(self) -> None:
        hid = self.proj_pick.currentData()
        perfs = self.summary.performances if self.summary else []
        if hid is None:
            error(self, "Pick a company", "Choose a company in “Projections for” to enter its future years.")
            return
        p = next((x for x in perfs if x.holding.id == hid), None)
        if p and ProjectionsDialog(self.pf, p.holding, p.financials if p.has_books else None, self).exec():
            self.refresh()

    @staticmethod
    def _share_financing(perfs, fin) -> pfm.Financing:
        """All active companies' financing at our ownership share (rate weighted by our share of each balance)."""
        out = pfm.Financing([])
        weighted = weight = 0.0
        mats = []
        for p in perfs:
            if p.holding.status != "active":
                continue
            f = pfm.financing_totals(fin[p.holding.id], p.share)
            out.principal += f.principal
            out.balance += f.balance
            out.annual_interest += f.annual_interest
            if f.rate is not None and f.balance:
                weighted += f.rate * f.balance
                weight += f.balance
            if f.next_maturity:
                mats.append(f.next_maturity)
            out.loans += fin[p.holding.id]
        out.rate = weighted / weight if weight else None
        out.next_maturity = min(mats) if mats else None
        n = len({ln.lender for ln in out.loans})
        out.lenders = f"{n} lender(s)" if n else ""
        return out

    @staticmethod
    def _financing_cells(f: pfm.Financing, share: bool = False) -> list[str]:
        if not f.loans:
            return ["—", "—", "—", "—", "—", "—"]
        return [f.lenders, fmt_money(f.principal), fmt_money(f.balance),
                f"{f.rate:.2f}%" if f.rate is not None else "—", fmt_money(f.annual_interest),
                (f"next {f.next_maturity}" if share or len(f.loans) > 1 else f.next_maturity) if f.next_maturity
                else "—"]

    def _fill_sources_uses(self, s: pfm.Summary) -> None:
        t = self.su_table
        perfs = s.performances
        cols = [(p.holding.name, pfm.sources_uses(self.pf, p, self._as_of_date())) for p in perfs]
        if cols:
            total = pfm.SourcesUses(dict.fromkeys(pfm.SOURCES, 0), dict.fromkeys(pfm.USES, 0))
            for p, (_n, su) in zip(perfs, cols):
                if p.holding.status != "active":
                    continue
                part = su.scaled(p.share)
                for k, v in part.sources.items():
                    total.sources[k] += v
                for k, v in part.uses.items():
                    total.uses[k] += v
            cols.append(("Portfolio (our share)", total))
        t.clear()
        t.setColumnCount(len(cols) + 1)
        t.setHorizontalHeaderLabels([""] + [n for n, _ in cols])
        t.setRowCount(len(SU_ROWS))
        for r, label in enumerate(SU_ROWS):
            head = QTableWidgetItem(label)
            hf = head.font()
            hf.setBold(label in ("SOURCES", "USES", "Total sources", "Total uses"))
            head.setFont(hf)
            t.setItem(r, 0, head)
            if label in ("", "SOURCES", "USES"):
                if not label:
                    t.setRowHeight(r, 10)
                continue
            for ci, (_n, su) in enumerate(cols):
                is_total = ci == len(cols) - 1 and len(cols) > 1
                if label == "Basis":
                    text = "" if is_total else ("Entered" if su.entered else "Worked out")
                elif label == "Total sources":
                    text = fmt_money(su.total_sources)
                elif label == "Total uses":
                    text = fmt_money(su.total_uses)
                else:
                    v = su.sources.get(label, su.uses.get(label, 0))
                    text = fmt_money(v) if v else "—"
                cell = QTableWidgetItem(text)
                cell.setTextAlignment(ALIGN_RIGHT)
                if is_total or label.startswith("Total"):
                    cf = cell.font()
                    cf.setBold(True)
                    cell.setFont(cf)
                if label == "Basis":
                    cell.setForeground(MUTED)
                t.setItem(r, ci + 1, cell)
        fit_name_columns(t)

    def _edit_sources_uses(self, index) -> None:
        col = index.column() - 1
        if self.summary and 0 <= col < len(self.summary.performances):
            p = self.summary.performances[col]
            if SourcesUsesDialog(self.pf, p, self._as_of_date(), self).exec():
                self.refresh()

    def _fill_summary(self, s: pfm.Summary) -> None:
        """Portfolio summary, one column per company (100%) and the portfolio at our share; one row per figure."""
        t = self.overview_table
        perfs = s.performances
        owners = {p_: n for n, p_, _r in self._companies()}
        c = s.consolidated
        cols = []  # one list of values per company, in SUM_ROWS order ("" = the gap between groups)
        fin = {p.holding.id: pfm.loans(self.pf, p.holding, self._as_of_date()) for p in perfs}
        for p in perfs:
            h, f = p.holding, (p.financials if p.has_books else None)
            held = owners.get(self.pf.owner_of(h), "")
            cols.append([
                h.type, f"{held} · {h.ownership_pct:g}%" if held else f"{h.ownership_pct:g}%", "",
                fmt_money(p.company_value) if p.company_value is not None else "No value yet", fmt_money(p.debt),
                fmt_money(p.nav), "",
                *([fmt_money(f.revenue_ltm), fmt_money(f.opex_ltm), fmt_money(f.ebitda_ltm), "",
                   fmt_money(f.nonop_ltm)] if f else ["—", "—", "—", "", "—"]),
                fmt_money(p.capex_req) if f or h.capex_req_cents else "—",
                fmt_money(p.net_cash_flow) if p.net_cash_flow is not None else "—", "",
                _multiple(p.moic), _pct(p.irr), _pct(p.cash_on_cash), "",
                *self._financing_cells(pfm.financing_totals(fin[h.id]))])
        total = ["", "", "", fmt_money(s.value_share), fmt_money(s.debt_share), fmt_money(s.nav), "",
                 *([fmt_money(c.revenue_ltm), fmt_money(c.opex_ltm), fmt_money(c.ebitda_ltm), "",
                    fmt_money(c.nonop_ltm), fmt_money(s.capex_req_share), fmt_money(s.net_cash_flow_share)]
                   if c else ["—", "—", "—", "", "—", "—", "—"]), "",
                 _multiple(s.moic), _pct(s.irr), _pct(s.cash_on_cash), "",
                 *self._financing_cells(self._share_financing(perfs, fin), share=True)]
        headers = [p.holding.name + (" (exited)" if p.holding.status == "exited" else "") for p in perfs]
        if perfs:
            headers.append("Portfolio (our share)")
            cols.append(total)
        t.setColumnCount(len(headers) + 1)
        t.setHorizontalHeaderLabels([""] + headers)
        t.setRowCount(len(SUM_ROWS))
        bold_rows = {"NAV", "EBITDA", "Net cash flow", "Cash on cash return"}
        for r, label in enumerate(SUM_ROWS):
            head = QTableWidgetItem(label)
            f = head.font()
            f.setBold(label in bold_rows)
            head.setFont(f)
            key = {lbl: k for _g, items in GROUPS for k, lbl in items}.get(label)
            if key:
                head.setToolTip(GROUP_TIPS.get(key, ""))
            t.setItem(r, 0, head)
            if not label and r > 1:  # the gap between groups
                t.setRowHeight(r, 10)
                for ci in range(len(cols)):
                    t.setItem(r, ci + 1, QTableWidgetItem(""))
                continue
            for ci, values in enumerate(cols):
                cell = QTableWidgetItem(values[r])
                if r >= 2:
                    cell.setTextAlignment(ALIGN_RIGHT)
                is_total = perfs and ci == len(cols) - 1
                if label in bold_rows or is_total:
                    cf = cell.font()
                    cf.setBold(True)
                    cell.setFont(cf)
                if label in ("Net cash flow", "EBITDA", "Annual cash flow") and values[r].startswith("(") \
                        or values[r].startswith("-"):
                    cell.setForeground(NEGATIVE)
                if ci < len(perfs) and perfs[ci].holding.status == "exited":
                    cell.setForeground(MUTED)
                t.setItem(r, ci + 1, cell)
        fit_name_columns(t)
        t.verticalHeader().setVisible(False)

    # -- customers & vendors of one company ------------------------------------

    def _build_parties_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        self.cv_pick = QComboBox()
        self.cv_pick.setMinimumWidth(260)
        self.cv_pick.currentIndexChanged.connect(lambda *_: self._fill_parties())
        row.addWidget(self.cv_pick)
        row.addSpacing(12)
        row.addWidget(QLabel("Period:"))
        self.cv_period = QComboBox()
        for label, key in (("Last 12 months", "ltm"), ("Year to date", "ytd"), ("Last year", "last_year"),
                           ("All time", "all")):
            self.cv_period.addItem(label, key)
        self.cv_period.currentIndexChanged.connect(lambda *_: self._fill_parties())
        row.addWidget(self.cv_period)
        row.addStretch(1)
        open_b = QPushButton("Assign Names in the Books…")
        open_b.setToolTip("Opens the company's books: set the customer / vendor on transactions (double-click a "
                          "transaction) or on invoices and bills, and it shows here")
        open_b.clicked.connect(self._cv_open_books)
        row.addWidget(open_b)
        lay.addLayout(row)
        self.cv_info = QLabel()
        self.cv_info.setTextFormat(Qt.TextFormat.RichText)
        self.cv_info.setWordWrap(True)
        lay.addWidget(self.cv_info)
        split = QSplitter(Qt.Orientation.Horizontal)
        for attr, title in (("cv_customers", "Customers → revenue"), ("cv_vendors", "Vendors → expenses")):
            box = QWidget()
            bl = QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            head = QLabel()
            head.setTextFormat(Qt.TextFormat.RichText)
            setattr(self, attr + "_head", head)
            bl.addWidget(head)
            cols = (["Customer", "Revenue", "% of revenue", "Entries", "Income account", "Receivable", "Last"]
                    if attr == "cv_customers" else
                    ["Vendor", "Expenses", "% of expenses", "Entries", "Expense account", "Payable", "Last"])
            t = _table(cols, 4)
            t.setSortingEnabled(False)
            setattr(self, attr, t)
            bl.addWidget(t, 1)
            split.addWidget(box)
        lay.addWidget(split, 1)
        self.parties = None
        return w

    def _cv_range(self) -> tuple[str | None, date]:
        end = self._as_of_date()
        k = self.cv_period.currentData()
        if k == "ytd":
            return f"{end.year}-01-01", end
        if k == "last_year":
            return f"{end.year - 1}-01-01", date(end.year - 1, 12, 31)
        if k == "all":
            return "0000-01-01", end
        return None, end

    def _fill_parties(self) -> None:
        from .. import party_totals as pt

        hid = self.cv_pick.currentData()
        p = next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)
        for t in (self.cv_customers, self.cv_vendors):
            t.setRowCount(0)
        self.cv_customers_head.setText("<b>Customers → revenue</b>")
        self.cv_vendors_head.setText("<b>Vendors → expenses</b>")
        if p is None or not p.holding.company_path or not Path(p.holding.company_path).exists():
            self.parties = None
            self.cv_info.setText("<i>Pick a company linked to its books.</i>" if p else "<i>No holdings yet.</i>")
            return
        start, end = self._cv_range()
        b = self.parties = pt.company_breakdown(p.holding.company_path, end, start=start)
        if b.error:
            self.cv_info.setText(f"<span style='color:#b3261e'>{b.error}</span>")
            return
        named_rev = b.revenue - b.unassigned("customers")
        named_exp = b.expenses - b.unassigned("vendors")
        cust = [x for x in b.customers if x.name != pt.UNASSIGNED and x.amount]
        top = cust[0] if cust else None
        top5 = sum(x.amount for x in cust[:5])
        bits = [f"{'All time' if start == '0000-01-01' else f'{b.start} – {b.end}'}",
                f"revenue <b>{fmt_money(b.revenue)}</b>, "
                f"{(named_rev / b.revenue * 100 if b.revenue else 0):.0f}% assigned to {len(cust)} customer(s)",
                f"expenses <b>{fmt_money(b.expenses)}</b>, "
                f"{(named_exp / b.expenses * 100 if b.expenses else 0):.0f}% assigned to "
                f"{sum(1 for x in b.vendors if x.name != pt.UNASSIGNED and x.amount)} vendor(s)"]
        if top and b.revenue:
            bits.append(f"largest customer <b>{top.name}</b> {top.amount / b.revenue * 100:.0f}% · "
                        f"top 5 {top5 / b.revenue * 100:.0f}% of revenue")
        self.cv_info.setText(" · ".join(bits))
        self.cv_customers_head.setText(f"<b>Customers → revenue</b> ({fmt_money(b.revenue)})")
        self.cv_vendors_head.setText(f"<b>Vendors → expenses</b> ({fmt_money(b.expenses)})")
        for t, rows, total in ((self.cv_customers, b.customers, b.revenue), (self.cv_vendors, b.vendors, b.expenses)):
            t.setRowCount(len(rows) + 1)
            for r, x in enumerate(rows):
                _set_row(t, r, [x.name + ("" if x.listed or x.name == pt.UNASSIGNED else "  (not on the list)"),
                                fmt_money(x.amount), f"{x.share * 100:.1f}%", x.count or "", x.main_account,
                                fmt_money(x.outstanding) if x.outstanding else "—", x.last_date],
                         {1, 2, 3, 5})
                if x.name == pt.UNASSIGNED:
                    for c in range(t.columnCount()):
                        t.item(r, c).setForeground(MUTED)
                    t.item(r, 0).setToolTip("Income / expenses with no customer or vendor name yet — click "
                                            "Assign Names in the Books…")
            _set_row(t, len(rows), ["Total", fmt_money(total), "100.0%" if total else "", "", "",
                                    fmt_money(sum(x.outstanding for x in rows)), ""], {1, 2, 3, 5}, bold=True)
            t.resizeColumnsToContents()

    def _sync_cv_pick(self) -> None:
        i = self.cv_pick.findData(self.co_pick.currentData())
        if i >= 0 and i != self.cv_pick.currentIndex():
            self.cv_pick.setCurrentIndex(i)
        for combo in (self.fin_pick, self.ops_page.pick, self.cash_page.pick, self.capex_page.pick,
                      self.dash_page.pick):
            i = combo.findData(self.co_pick.currentData())
            if i >= 0 and i != combo.currentIndex():
                combo.setCurrentIndex(i)

    def _cv_open_books(self) -> None:
        hid = self.cv_pick.currentData()
        p = next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)
        if p and p.holding.company_path:
            self.open_company.emit(p.holding.company_path)

    def _follow_viewing(self) -> None:
        """Viewing one operating company: Company Overview, Customers & Vendors and Financing show it."""
        v = self.viewing.currentData()
        if not v or v[0] != "company" or not self.summary:
            return
        perf = next((p for p in self.summary.performances if pfm._same_file(p.holding.company_path, v[1])), None)
        if perf:
            for combo in (self.co_pick, self.cv_pick, self.fin_pick, self.ops_page.pick, self.cash_page.pick,
                          self.capex_page.pick, self.dash_page.pick):
                i = combo.findData(perf.holding.id)
                if i >= 0 and i != combo.currentIndex():
                    combo.setCurrentIndex(i)

    def _fill_company_pick(self, s: pfm.Summary) -> None:
        want_cv = self.cv_pick.currentData()
        self.cv_pick.blockSignals(True)
        self.cv_pick.clear()
        for p in s.performances:
            if p.holding.company_path:
                self.cv_pick.addItem(p.holding.name, p.holding.id)
        i = self.cv_pick.findData(want_cv)
        self.cv_pick.setCurrentIndex(i if i >= 0 else 0)
        self.cv_pick.blockSignals(False)
        self._fill_parties()
        want_fin = self.fin_pick.currentData()
        self.fin_pick.blockSignals(True)
        self.fin_pick.clear()
        for p in s.performances:
            self.fin_pick.addItem(p.holding.name, p.holding.id)
        i = self.fin_pick.findData(want_fin)
        self.fin_pick.setCurrentIndex(i if i >= 0 else 0)
        self.fin_pick.blockSignals(False)
        self._fill_financing()
        for page in (self.dash_page, self.ops_page, self.cash_page, self.capex_page):
            page.fill_pick(s.performances)
        self._fill_company_pick_overview(s)

    def _fill_company_pick_overview(self, s: pfm.Summary) -> None:
        want = self.co_pick.currentData()
        self.co_pick.blockSignals(True)
        self.co_pick.clear()
        for p in s.performances:
            self.co_pick.addItem(p.holding.name, p.holding.id)
        i = self.co_pick.findData(want)
        self.co_pick.setCurrentIndex(i if i >= 0 else 0)
        self.co_pick.blockSignals(False)
        self._fill_company()

    def _company_perf(self) -> pfm.Performance | None:
        hid = self.co_pick.currentData()
        return next((p for p in (self.summary.performances if self.summary else []) if p.holding.id == hid), None)

    def _fill_company(self) -> None:
        p = self._company_perf()
        if p is None:
            self.co_title.setText("")
            self.co_info.setText("<i>No holdings yet — click New Holding….</i>")
            self.co_grouped.clear()
            self.co_chart.set_data([])
            self.co_vals.setRowCount(0)
            self.co_loans.setRowCount(0)
            self._co_loan_rows = []
            return
        h = p.holding
        self.co_title.setText(h.name)
        self._co_loan_rows = pfm.loans(self.pf, h, self._as_of_date())
        fill_loan_summary(self.co_loans, self._co_loan_rows, self._as_of_date())
        names = {p_: n for n, p_, _r in self._companies()}
        bits = [h.type]
        owner = names.get(self.pf.owner_of(h))
        bits.append(f"{h.ownership_pct:g}% held by <b>{owner}</b>" if owner else f"{h.ownership_pct:g}% owned")
        if h.manager_path:
            fee = " + ".join(x for x in (f"{h.fee_pct:g}% of value" if h.fee_pct else "",
                                         f"{fmt_money(h.fee_fixed_cents)}/month" if h.fee_fixed_cents else "") if x)
            bits.append(f"managed by <b>{names.get(h.manager_path, Path(h.manager_path).stem)}</b>"
                        + (f" ({fee})" if fee else ""))
        bits += [x for x in (h.sector, h.location) if x]
        self.co_info.setText(" · ".join(bits))
        self.co_grouped.fill(
            value=p.company_value, debt=p.debt, nav=p.nav, f=p.financials if p.has_books else None,
            capex=p.capex_req if (p.has_books or h.capex_req_cents) else None, ncf=p.net_cash_flow,
            moic=p.moic, irr=p.irr, coc=p.cash_on_cash,
            value_sub=f"{p.value_date} · {p.value_method}" if p.value_date else "No valuation yet",
            debt_sub=f"LTV {_pct(p.ltv)}" if p.ltv is not None else "",
            nav_sub=(f"{h.ownership_pct:g}% of equity {short(p.value_100)}" if p.value_100 is not None else ""),
            moic_sub=f"{short(p.invested)} in · {short(p.returned)} out", irr_sub=f"gain {short(p.gain)}")
        self.co_chart.set_data(pfm.monthly_results(h.company_path, self._as_of_date()) if p.has_books else [])
        fill_projection_table(self.co_proj, p.financials if p.has_books else None, pfm.projections(self.pf, h.id),
                              p.capex_req if p.has_books else None)
        fill_period_table(self.co_months, pfm.monthly_figures(h.company_path, self._as_of_date())
                          if p.has_books else [])
        vals = list(reversed(self.pf.valuations(h.id)))
        self.co_vals.setRowCount(len(vals))
        for r, v in enumerate(vals):
            cv = v.company_value_cents if v.company_value_cents is not None else (
                v.value_cents + v.debt_cents if v.debt_cents is not None else None)
            _set_row(self.co_vals, r, [v.date, fmt_money(cv) if cv is not None else "",
                                       fmt_money(v.debt_cents) if v.debt_cents is not None else "",
                                       fmt_money(v.value_cents), fmt_money(round(v.value_cents * h.ownership_pct / 100)),
                                       v.method, v.note], {1, 2, 3, 4})

    def _open_company_overview(self, index) -> None:
        col = index.column() - 1  # column 0 holds the row labels; one column per company
        if self.summary and 0 <= col < len(self.summary.performances):
            self.co_pick.setCurrentIndex(self.co_pick.findData(self.summary.performances[col].holding.id))
            self.tabs.setCurrentIndex(1)

    def _co_projections(self) -> None:
        p = self._company_perf()
        if p and ProjectionsDialog(self.pf, p.holding, p.financials if p.has_books else None, self).exec():
            self.refresh()

    # -- financing & cap table -------------------------------------------------

    def _build_financing_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        row.addWidget(QLabel("Company:"))
        self.fin_pick = QComboBox()
        self.fin_pick.setMinimumWidth(260)
        self.fin_pick.currentIndexChanged.connect(lambda *_: self._fill_financing())
        row.addWidget(self.fin_pick)
        self.fin_info = QLabel()
        self.fin_info.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.fin_info, 1)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("<b>Loans</b> (balances from the company's books where linked; double-click to edit)"))
        lrow.addStretch(1)
        for text, slot in [("Add Loan…", self._fin_add_loan), ("Edit Loan…", self._fin_edit_loan),
                           ("Delete Loan", self._fin_delete_loan), ("Create Account in the Books", self._fin_link),
                           ("Record Payments in the Books…", self._fin_open_payments)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            if text.startswith("Add"):
                b.setObjectName("primary")
            lrow.addWidget(b)
        tl.addLayout(lrow)
        self.fin_loans = _table(LOAN_COLS, None)
        self.fin_loans.doubleClicked.connect(lambda *_: self._fin_edit_loan())
        self.fin_loans.itemSelectionChanged.connect(self._fill_fin_schedule)
        self._fin_rows: list[pfm.Loan] = []
        tl.addWidget(self.fin_loans, 1)
        split.addWidget(top)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        sw = QWidget()
        sl = QVBoxLayout(sw)
        sl.setContentsMargins(0, 0, 0, 0)
        srow = QHBoxLayout()
        self.fin_sched_title = QLabel("<b>Amortization table</b>")
        srow.addWidget(self.fin_sched_title)
        srow.addStretch(1)
        self.fin_sched_view = QComboBox()
        self.fin_sched_view.addItem("Each payment", "payment")
        self.fin_sched_view.addItem("By year", "year")
        self.fin_sched_view.currentIndexChanged.connect(lambda *_: self._fill_fin_schedule())
        srow.addWidget(self.fin_sched_view)
        sl.addLayout(srow)
        self.fin_sched = _table(SCHED_COLS, None)
        sl.addWidget(self.fin_sched, 1)
        bottom.addWidget(sw)
        cw = QWidget()
        cl = QVBoxLayout(cw)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel("<b>Capital stack</b> (100% of the company: debt by seniority, then equity)"))
        self.fin_stack = _table(["Tranche", "Amount", "% of capital", "Cumulative LTV", "Rate", "Maturity"], 0)
        cl.addWidget(self.fin_stack, 1)
        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("<b>Cap table</b> (owners: name, company and amount)"))
        hrow.addStretch(1)
        for text, slot in [("Add Owner…", self._fin_add_holder), ("Edit…", self._fin_edit_holder),
                           ("Delete", self._fin_delete_holder), ("Import Equity…", self._fin_import_equity)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            hrow.addWidget(b)
        cl.addLayout(hrow)
        self.fin_holders = _table(["Name", "Company", "Class", "Equity", "% owned", "Funded", "Funded on",
                                   "Value of stake", "Note"], 8)
        self.fin_holders.doubleClicked.connect(lambda *_: self._fin_edit_holder())
        self._fin_holder_rows: list[pfm.Shareholder] = []
        cl.addWidget(self.fin_holders, 1)
        bottom.addWidget(cw)
        bottom.setSizes([520, 620])
        split.addWidget(bottom)
        split.setSizes([260, 460])
        lay.addWidget(split, 1)
        return w

    def _fin_perf(self) -> pfm.Performance | None:
        hid = self.fin_pick.currentData()
        return next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)

    def _fill_financing(self) -> None:
        p = self._fin_perf()
        keep = self.fin_loans.currentRow()
        if p is None:
            self._fin_rows, self._fin_holder_rows = [], []
            for t in (self.fin_loans, self.fin_stack, self.fin_holders, self.fin_sched):
                t.setRowCount(0)
            self.fin_info.setText("<i>No holdings yet.</i>")
            return
        as_of = self._as_of_date()
        self._fin_rows = pfm.loans(self.pf, p.holding, as_of)
        fill_loan_table(self.fin_loans, self._fin_rows, as_of)
        tot = pfm.financing_totals(self._fin_rows)
        self.fin_info.setText(
            f"{len(self._fin_rows)} loan(s) · balance <b>{fmt_money(tot.balance)}</b> · debt service "
            f"<b>{fmt_money(tot.debt_service)}</b> a year"
            + (f" · LTV {p.ltv * 100:.0f}%" if p.ltv is not None else "")
            + (f" · next maturity {tot.next_maturity}" if tot.next_maturity else ""))
        stack = pfm.capital_stack(self.pf, p, as_of)
        self.fin_stack.setRowCount(len(stack) + 1)
        for r, x in enumerate(stack):
            _set_row(self.fin_stack, r, [x.name, fmt_money(x.amount), f"{x.pct * 100:.1f}%",
                                         f"{x.cumulative * 100:.1f}%" if x.kind != "Equity" else "",
                                         f"{x.rate:.3g}%" if x.rate is not None else "", x.maturity or ""],
                     {1, 2, 3, 4}, bold=x.kind == "Equity")
        _set_row(self.fin_stack, len(stack), ["Total capitalization", fmt_money(sum(x.amount for x in stack)),
                                              "100.0%" if stack else "", "", "", ""], {1, 2}, bold=True)
        self.fin_stack.resizeColumnsToContents()
        names = {pth: n for n, pth, _r in self._companies()}
        holders = pfm.cap_table(self.pf, p, names.get(self.pf.owner_of(p.holding), ""))
        classes = pfm.class_totals(holders)
        t = self.fin_holders
        t.clearSpans()
        t.setRowCount(0)
        self._fin_holder_rows = []

        def add(vals, *, owner=None, bold=False, muted_note=False):
            r = t.rowCount()
            t.insertRow(r)
            _set_row(t, r, vals, {3, 4, 5, 7}, bold=bold)
            if muted_note:
                t.item(r, 8).setForeground(MUTED)
            self._fin_holder_rows.append(owner)

        many = len(classes) > 1
        for cls, (amt, fund, share) in classes.items():
            members = [x for x in holders if (x.share_class or "Common") == cls]
            for x in members:
                add([x.name, x.company or "—", cls, fmt_money(x.amount_cents) if x.amount_cents else "—",
                     f"{x.pct * 100:.2f}%", fmt_money(x.funded) if x.funded else "—", x.funded_date or "",
                     fmt_money(x.value) if x.value is not None else "—",
                     "worked out from the ownership % — Edit to enter" if x.worked_out else x.note],
                    owner=x, muted_note=x.worked_out)
            if many:
                add([f"Total {cls}", "", "", fmt_money(amt), f"{share * 100:.2f}%", fmt_money(fund),
                     ("over by " + fmt_money(fund - amt)) if fund > amt else "",
                     fmt_money(round(p.value_100 * share)) if p.value_100 is not None else "—", ""], bold=True)
        if holders:
            tot_a = sum(x.amount_cents for x in holders)
            tot_f = sum(x.funded for x in holders)
            add(["Total", "", "", fmt_money(tot_a), "100.00%", fmt_money(tot_f), "",
                 fmt_money(sum(x.value or 0 for x in holders)) if p.value_100 is not None else "—", ""], bold=True)
        self.fin_holders.resizeColumnsToContents()
        if 0 <= keep < len(self._fin_rows):
            self.fin_loans.selectRow(keep)
        elif self._fin_rows:
            self.fin_loans.selectRow(0)
        self._fill_fin_schedule()

    def _selected_loan(self) -> pfm.Loan | None:
        r = self.fin_loans.currentRow()
        return self._fin_rows[r] if 0 <= r < len(self._fin_rows) else None

    def _fin_books(self):
        from ..books import Books

        p = self._fin_perf()
        if not p or not p.holding.company_path or not Path(p.holding.company_path).exists():
            return None
        try:
            return Books.open(p.holding.company_path, create=False)
        except Exception:  # noqa: BLE001
            return None

    def _fin_link(self) -> None:
        from .. import loan_link as ll

        p, ln = self._fin_perf(), self._selected_loan()
        if not p or not ln:
            return
        if ln.id is None:
            info(self, "Already in the books", f"{ln.lender} is an account in the books — Edit Loan… to add its "
                                               "terms.")
            return
        books = self._fin_books()
        if books is None:
            error(self, "No books", "Link this company to its books first (Company Overview → Edit…).")
            return
        try:
            acct, opening = ll.ensure_account(self.pf, books, ll.CompanyLoan(p.holding, ln))
            name = books.account(acct).name
        finally:
            books.close()
        info(self, "Linked to the books", f"{ln.lender} is linked to “{name}” in {p.holding.name}'s books."
             + (f" Opening balance {fmt_money(opening)} (the scheduled balance when the books start)." if opening
                else "") + "\n\nRecord its payments in Accounting → Transactions → Loans….")
        self.refresh()

    def _fin_open_payments(self) -> None:
        p = self._fin_perf()
        if p and p.holding.company_path:
            self.open_loans.emit(p.holding.company_path)

    def _fill_fin_schedule(self) -> None:
        ln = self._selected_loan()
        rows = pfm.amortization_schedule(ln) if ln else []
        paid = []
        if ln and ln.id is not None and ln.account_id is not None:
            from .. import loan_link as ll

            books = self._fin_books()
            if books is not None:
                try:
                    paid = [(t.date, pr, it) for t, pr, it in ll.recorded(books, ln.id)]
                finally:
                    books.close()
        self.fin_sched_title.setText(f"<b>Amortization table</b> — {ln.lender}" if ln else
                                     "<b>Amortization table</b>")
        if ln and not rows:
            self.fin_sched_title.setText(f"<b>Amortization table</b> — {ln.lender}: add the principal, start date "
                                         f"and term (Edit Loan…)")
        fill_schedule_table(self.fin_sched, rows, self.fin_sched_view.currentData(), paid)
        if rows and self.fin_sched_view.currentData() == "payment":  # open at today's payment
            today = self._as_of_date().isoformat()
            r = next((k for k, x in enumerate(rows) if x.date >= today), len(rows) - 1)
            self.fin_sched.scrollToItem(self.fin_sched.item(max(0, r - 3), 0),
                                        QAbstractItemView.ScrollHint.PositionAtTop)

    def _go_financing(self, loan_row: int | None = None) -> None:
        self.fin_pick.setCurrentIndex(max(0, self.fin_pick.findData(self.co_pick.currentData())))
        self.tabs.setCurrentWidget(self.financing_page)
        if loan_row is not None and 0 <= loan_row < len(self._fin_rows):
            self.fin_loans.selectRow(loan_row)

    def _fin_add_loan(self) -> None:
        p = self._fin_perf()
        if p and LoanDialog(self.pf, p.holding, parent=self).exec():
            self.refresh()

    def _fin_edit_loan(self) -> None:
        p, ln = self._fin_perf(), self._selected_loan()
        if p and ln and LoanDialog(self.pf, p.holding, ln, self).exec():
            self.refresh()

    def _fin_delete_loan(self) -> None:
        ln = self._selected_loan()
        if not ln:
            return
        if ln.id is None:
            error(self, "In the books", f"{ln.lender} is a debt account in the company's books. Edit it to add "
                                        "its terms; to remove it, change the books.")
            return
        if confirm(self, "Delete loan", f"Delete {ln.lender}'s terms from the portfolio? (The books aren't touched.)"):
            pfm.delete_loan(self.pf, ln.id)
            self.refresh()

    def _selected_holder(self) -> pfm.Shareholder | None:
        r = self.fin_holders.currentRow()
        return self._fin_holder_rows[r] if 0 <= r < len(self._fin_holder_rows) else None

    def _fin_add_holder(self) -> None:
        p = self._fin_perf()
        if p and ShareholderDialog(self.pf, p.holding, None, self).exec():
            self.refresh()

    def _fin_edit_holder(self) -> None:
        p, x = self._fin_perf(), self._selected_holder()
        if p and x and ShareholderDialog(self.pf, p.holding, x, self).exec():
            self.refresh()

    def _fin_import_equity(self, path: str | None = None) -> int:
        """Equity contributions from a workbook laid out like a Debt-Equity tab (class headings, then Name of
        Investor / Name of Corporation / Equity / Funded rows)."""
        p = self._fin_perf()
        if not p:
            return 0
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Import equity contributions", "",
                                                  "Workbook or CSV (*.xlsx *.xlsm *.csv)")
            if not path:
                return 0
        try:
            rows, where = pfm.read_equity_file(path)
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't read the equity table", str(e))
            return 0
        existing = [x for x in pfm.cap_table(self.pf, p) if not x.worked_out]
        if existing and not confirm(self, "Replace cap table", f"Replace the {len(existing)} owners on file with the "
                                                               f"{len(rows)} contributions in “{where}”?"):
            return 0
        for x in existing:
            pfm.delete_shareholder(self.pf, x.id)
        for x in rows:
            pfm.save_shareholder(self.pf, p.holding.id, x)
        totals = pfm.class_totals(rows)
        info(self, "Equity imported", f"{len(rows)} contributions from “{where}”: " + "; ".join(
            f"{k} {fmt_money(a)} ({sh * 100:.0f}%)" for k, (a, _f, sh) in totals.items()) + ".")
        self.refresh()
        return len(rows)

    def _fin_delete_holder(self) -> None:
        x = self._selected_holder()
        if x and x.id is not None and confirm(self, "Delete owner", f"Remove {x.label} from the cap table?"):
            pfm.delete_shareholder(self.pf, x.id)
            self.refresh()

    def _co_add_valuation(self) -> None:
        p = self._company_perf()
        if p and ValuationDialog(self.pf, p.holding, self).exec():
            self.refresh()

    def _co_edit(self) -> None:
        p = self._company_perf()
        if p and HoldingDialog(self.pf, self.companies_fn(), p.holding, self, create_company=self.create_company).exec():
            self.refresh()

    def _co_open_books(self) -> None:
        p = self._company_perf()
        if p and p.holding.company_path:
            self.open_company.emit(p.holding.company_path)
        elif p:
            error(self, "Not linked", "This company isn't linked to its books yet — use Edit….")

    def _fill_consolidated(self, s: pfm.Summary) -> None:
        """Categories down the side; each linked company (100%) and the portfolio at our share across the top."""
        rows = [p for p in s.performances if p.financials and p.holding.status == "active"]
        cats = [("Revenue", "revenue_ltm"), ("Op. Ex", "opex_ltm"), ("EBITDA", "ebitda_ltm"),
                ("Non-Op. Ex", "nonop_ltm"), ("Net income", "net_income_ltm"), ("", None), ("Cash", "cash"),
                ("Receivables", "receivables"), ("Total assets", "total_assets"), ("", None), ("Payables", "payables"),
                ("Debt", "debt"), ("Total liabilities", "total_liabilities"), ("", None), ("Equity", "equity")]
        t = self.cons_table
        cols = [(f"{p.holding.name} ({p.holding.ownership_pct:g}%)", p.financials) for p in rows]
        if s.consolidated:
            cols.append(("Portfolio – our share", s.consolidated))
        t.clear()
        t.setColumnCount(len(cols) + 1)
        t.setHorizontalHeaderLabels([""] + [c[0] for c in cols])
        t.setRowCount(len(cats))
        for r_, (label, attr) in enumerate(cats):
            head = QTableWidgetItem(label)
            if label in ("EBITDA", "Net income", "Equity"):
                f_ = head.font()
                f_.setBold(True)
                head.setFont(f_)
            t.setItem(r_, 0, head)
            if attr is None:
                t.setRowHeight(r_, 10)
                continue
            for c_, (_n, f) in enumerate(cols):
                cell = QTableWidgetItem("⚠ " + f.error if f.error and r_ == 0 else ("" if f.error else
                                                                                  fmt_money(getattr(f, attr))))
                cell.setTextAlignment(ALIGN_RIGHT)
                if c_ == len(cols) - 1 and s.consolidated or label in ("EBITDA", "Net income", "Equity"):
                    f_ = cell.font()
                    f_.setBold(True)
                    cell.setFont(f_)
                t.setItem(r_, c_ + 1, cell)
        fit_name_columns(t)
        t.verticalHeader().setVisible(False)

    def _fill_managers(self) -> None:
        want = self.mgr.currentData()
        v = self.viewing.currentData()
        if v and v[0] == "manager":
            want = v[1]
        self.mgr.blockSignals(True)
        self.mgr.clear()
        for name, path, role in self._companies():
            if role == "asset_manager":
                self.mgr.addItem(name, path)
        i = self.mgr.findData(want)
        self.mgr.setCurrentIndex(i if i >= 0 else 0)
        self.mgr.blockSignals(False)

    def _fill_managed(self) -> None:
        t = self.managed_table
        path = self.mgr.currentData()
        if not path:
            t.setRowCount(0)
            self.managed_note.setText("No asset manager yet. Create a company and choose “Asset manager” (File → New "
                                      "Company), then assign it to the companies it manages in Edit Holding….")
            return
        start = date.fromisoformat(_iso(self.fee_start.date()))
        end = date.fromisoformat(_iso(self.fee_end.date()))
        rows = pfm.managed_companies(self.pf, path, start, end)
        owners = {p: n for n, p, _r in self._companies()}
        t.setRowCount(len(rows) + (1 if rows else 0))
        for r, m in enumerate(rows):
            h = m.holding
            held = owners.get(self.pf.owner_of(h), Path(self.pf.owner_of(h)).stem if self.pf.owner_of(h) else "")
            _set_row(t, r, [m.company, h.type, f"{held} ({h.ownership_pct:g}%)" if held else "",
                            fmt_money(m.value) if m.value is not None else "No value yet", f"{h.fee_pct:g}%",
                            fmt_money(h.fee_fixed_cents) if h.fee_fixed_cents else "", fmt_money(m.annual_fee),
                            fmt_money(m.billed), fmt_money(m.paid)], {3, 5, 6, 7, 8})
        if rows:
            _set_row(t, len(rows), ["Total", "", "", fmt_money(sum(m.value or 0 for m in rows)), "", "",
                                    fmt_money(sum(m.annual_fee for m in rows)), fmt_money(sum(m.billed for m in rows)),
                                    fmt_money(sum(m.paid for m in rows))], {3, 6, 7, 8}, bold=True)
        for c, w in enumerate([220, 130, 210, 130, 70, 110, 120, 120]):
            t.setColumnWidth(c, w)
        self.managed_note.setText(f"{len(rows)} compan{'y' if len(rows) == 1 else 'ies'} managed · assets under "
                                  f"management {fmt_money(sum(m.value or 0 for m in rows))} · fees are invoiced in "
                                  f"{self.mgr.currentText()}'s books (Management Fee Income) to each company.")

    def bill_fees(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        path = self.mgr.currentData()
        if not path:
            error(self, "No asset manager", "Choose the asset manager first.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("Bill management fees")
        form = QFormLayout(dlg)
        today = QDate.currentDate()
        q_start = QDate(today.year(), ((today.month() - 1) // 3) * 3 + 1, 1)
        start = QDateEdit(q_start.addMonths(-3))
        end = QDateEdit(q_start.addDays(-1))
        for w in (start, end):
            w.setCalendarPopup(True)
            w.setDisplayFormat("MMM d, yyyy")
        form.addRow(QLabel(f"Invoice the companies managed by <b>{self.mgr.currentText()}</b> for:"))
        form.addRow("From:", start)
        form.addRow("To:", end)
        form.addRow(QLabel("Fee = annual % × value × days ÷ 365, plus the fixed fee per month. Companies already "
                           "billed for exactly this period are skipped."))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create Invoices")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        form.addRow(buttons)
        if not dlg.exec():
            return
        try:
            made = pfm.bill_management_fees(self.pf, path, date.fromisoformat(_iso(start.date())),
                                            date.fromisoformat(_iso(end.date())))
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't bill fees", str(e))
            return
        self._fill_managed()
        QMessageBox.information(self, "Management fees",
                                ("Invoices created in the asset manager's books:\n\n" + "\n".join(
                                    f"• {c}: {fmt_money(a)}" for c, a, _ in made)) if made else
                                "Nothing to bill — no fees set, or already billed for this period.")

    # -- selection / detail -------------------------------------------------------

    def _selected_perf(self) -> pfm.Performance | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows or not self.summary or rows[0].row() >= len(self.summary.performances):
            return None
        return self.summary.performances[rows[0].row()]

    def _selected_id(self) -> int | None:
        p = self._selected_perf() if self.summary else None
        return p.holding.id if p else None

    def _goto_holding(self, t: QTableWidget) -> None:
        rows = t.selectionModel().selectedRows()
        if rows and self.summary and rows[0].row() < len(self.summary.performances):
            self.tabs.setCurrentWidget(self.holdings_page)
            self.table.selectRow(rows[0].row())

    def _show_detail(self) -> None:
        p = self._selected_perf()
        if p is None:
            self.detail_title.setText("<i>Select a holding to see its valuations, capital flows and financials.</i>")
            self.financials.setText("")
            self.val_table.setRowCount(0)
            self.flow_table.setRowCount(0)
            return
        h = p.holding
        bits = [h.type, f"{h.ownership_pct:g}% owned"] + [x for x in (h.sector, h.location) if x]
        if h.acquired:
            bits.append(f"acquired {h.acquired}")
        self.detail_title.setText(f"<b style='font-size:13pt'>{h.name}</b> &nbsp; {' · '.join(bits)}")
        f = p.financials
        if not h.company_path:
            self.financials.setText("<span style='color:gray'>Not linked to company books — Edit Holding… to link "
                                    "its company file and see its financials here.</span>")
        elif f is None or f.error:
            self.financials.setText(f"<span style='color:#b3261e'>⚠ {f.error if f else 'Company file not found'}"
                                    f"</span>")
        else:
            self.financials.setText(
                f"From <b>{f.company}</b> books (to {f.as_of}): revenue LTM <b>{fmt_money(f.revenue_ltm)}</b> · "
                f"net income LTM <b>{fmt_money(f.net_income_ltm)}</b> · cash <b>{fmt_money(f.cash)}</b> · "
                f"debt <b>{fmt_money(f.debt)}</b> · total assets {fmt_money(f.total_assets)} · liabilities "
                f"{fmt_money(f.total_liabilities)} · equity <b>{fmt_money(f.equity)}</b>")
        vals = self.pf.valuations(h.id)
        self.val_table.setRowCount(len(vals))
        for r, v in enumerate(reversed(vals)):
            _set_row(self.val_table, r, [v.date, fmt_money(v.value_cents),
                                         fmt_money(round(v.value_cents * h.ownership_pct / 100)), v.method, v.note],
                     {1, 2})
        self._vals = list(reversed(vals))
        flows = self.pf.flows(h.id)
        self.flow_table.setRowCount(len(flows))
        for r, fl in enumerate(reversed(flows)):
            _set_row(self.flow_table, r, [fl.date, fl.label, fmt_money(fl.amount_cents), fmt_money(fl.signed),
                                          fl.note], {2, 3}, colour_col=3, colour_value=fl.signed)
        self._flows = list(reversed(flows))
        books_flows = sorted((f for f in p.flows if f.from_books), key=lambda f: f.date, reverse=True)
        if books_flows:
            start = self.flow_table.rowCount()
            self.flow_table.setRowCount(start + len(books_flows))
            for i, fl in enumerate(books_flows):
                _set_row(self.flow_table, start + i, [fl.date, fl.label + "  (books)", fmt_money(fl.amount_cents),
                                                      fmt_money(fl.signed), fl.note], {2, 3}, colour_col=3,
                         colour_value=fl.signed)
            self._flows += books_flows

    # -- actions ------------------------------------------------------------------

    def new_holding(self) -> None:
        if HoldingDialog(self.pf, self.companies_fn(), parent=self, create_company=self.create_company).exec():
            self.refresh()

    def edit_holding(self, *_) -> None:
        p = self._selected_perf()
        if p and HoldingDialog(self.pf, self.companies_fn(), p.holding, self, create_company=self.create_company).exec():
            self.refresh()

    def delete_holding(self) -> None:
        p = self._selected_perf()
        if p and confirm(self, "Delete holding", f"Delete {p.holding.name} with its valuations and capital flows? "
                                                 f"(Its company books are not touched.)"):
            self.pf.delete_holding(p.holding.id)
            self.refresh()

    def open_books(self) -> None:
        p = self._selected_perf()
        if p is None:
            return
        if not p.holding.company_path:
            error(self, "Not linked", "This holding isn't linked to company books yet — use Edit Holding….")
            return
        self.open_company.emit(p.holding.company_path)

    def add_valuation(self) -> None:
        p = self._selected_perf()
        if p and ValuationDialog(self.pf, p.holding, self).exec():
            self.refresh()

    def delete_valuation(self) -> None:
        rows = self.val_table.selectionModel().selectedRows()
        if rows and confirm(self, "Delete valuation", "Delete the selected valuation?"):
            self.pf.delete_valuation(self._vals[rows[0].row()].id)
            self.refresh()

    def add_flow(self) -> None:
        p = self._selected_perf()
        if p and FlowDialog(self.pf, p.holding, self).exec():
            self.refresh()

    def delete_flow(self) -> None:
        rows = self.flow_table.selectionModel().selectedRows()
        if not rows:
            return
        fl = self._flows[rows[0].row()]
        if fl.from_books:
            error(self, "From the books", "This flow comes from the holding company's books — change or recategorize "
                                          "the transaction there (Open the holding company in Accounting).")
            return
        if confirm(self, "Delete capital flow", "Delete the selected contribution / distribution?"):
            self.pf.delete_flow(fl.id)
            self.refresh()

    def export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export portfolio", str(Path.home() / f"Portfolio "
                                                                                          f"{_iso(self.as_of.date())}.xlsx"),
                                              "Excel (*.xlsx)")
        if not path:
            return
        try:
            pfm.write_xlsx(self.pf, Path(path), self._as_of_date())
        except PermissionError:
            error(self, "Couldn't save", "The file is open in Excel. Close it and try again.")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
