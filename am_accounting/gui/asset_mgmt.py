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
        path, _ = QFileDialog.getOpenFileName(self, "Company file", str(Path.home()), "Aedalus Nexus company (*.amdb)")
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
        loan_debt = pfm.loan_debt(pf, holding)
        if loan_debt:                 # the loans on Financing & Cap Table
            self.debt.setValue(loan_debt / 100)
        form.addRow("Date:", self.date)
        form.addRow("Company value (100%):", self.company_value)
        form.addRow("Debt in place:", self.debt)
        if loan_debt:
            form.addRow("", QLabel(f"<span style='color:gray'>Loans on Financing &amp; Cap Table today: "
                                   f"{fmt_money(loan_debt)}</span>"))
        elif books_debt is not None:
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
        if holding.company_path:
            self.account.setItemText(0, "(new account — created in the books)")
        form.addRow("Account in the books:", self.account)
        self.asset = QComboBox()        # the other side on the balance sheet
        self.asset.addItem("Assets Held", None)
        if holding.company_path and Path(holding.company_path).exists():
            import sqlite3

            con = sqlite3.connect(f"file:{Path(holding.company_path)}?mode=ro", uri=True)
            try:
                for i, code, name in con.execute("SELECT id, code, name FROM accounts WHERE type='asset' AND is_bank=0 "
                                                 "AND active=1 AND lower(name)!='assets held' ORDER BY code"):
                    self.asset.addItem(f"{code} · {name}", i)
            finally:
                con.close()
        self.asset.setCurrentIndex(max(0, self.asset.findData(ln.asset_account_id)))
        self.asset.setToolTip("On the balance sheet the loan is matched to this asset (the property or business it "
                              "paid for). Record the advance from the bank instead if the money came into the bank.")
        if holding.company_path:
            form.addRow("Balance sheet: matched to", self.asset)
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
            amort_months=0 if repay == "Interest only" or amort == term else amort,
            asset_account_id=self.asset.currentData(), book_entry_id=self.loan.book_entry_id)

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
        from .. import personal as pers

        saved = pers.load(pf)
        self.link = QComboBox()
        self.link.addItem("Someone else", "")
        self.link.addItem(f"Me{' (' + saved.name + ')' if saved.name else ''}", "me")
        hc = pers.holdco_name(saved)
        self.link.addItem(f"My holding company{' (' + hc + ')' if hc else ' — pick it under Personal'}", "holdco")
        guess = src.link or (pers.guess_link(src, saved) if src.id is None or src.worked_out else "")
        self.link.setCurrentIndex(max(0, self.link.findData(guess)))
        self.link.setToolTip("Owners that are you or your holding company make up your ownership of this company "
                             "(shown across the app and used on your net worth)")
        form.addRow("This owner is:", self.link)
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
        x.link = self.link.currentData() or ""
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


def _loan_term(ln) -> str:
    if not ln.term_months:
        return "—"
    y, m = divmod(ln.term_months, 12)
    txt = " ".join(x for x in (f"{y} yr" if y else "", f"{m} mo" if m else "") if x)
    if ln.repayment == "Amortizing" and ln.amort_months and ln.amort_months != ln.term_months:
        txt += f" / {ln.amort_months / 12:g} yr amort."
    return txt


def loan_cells(rows: list[pfm.Loan], as_of: date) -> list[list[str]]:
    """One list of texts per loan (LOAN_COLS order), then the total line."""
    out = []
    for ln in rows:
        left = ln.years_left(as_of)
        out.append([
            ln.lender + ("  — click to add terms" if ln.terms_missing else ""), ln.kind,
            "—" if ln.terms_missing else ln.repayment,
            fmt_money(ln.principal_cents) if ln.principal_cents else "—", fmt_money(ln.balance),
            f"{ln.rate_pct:.3g}% {ln.rate_type.lower()}" if ln.rate_pct is not None else "—", _loan_term(ln),
            f"{fmt_money(ln.payment)} {ln.payment_freq.lower()}" if ln.payment else
            (ln.payment_freq if ln.payment_freq == "At maturity" else "—"),
            fmt_money(ln.annual_debt_service) if ln.annual_debt_service else "—",
            fmt_money(ln.annual_interest) if ln.annual_interest is not None else "—",
            ln.maturity_date or "—",
            ("matured" if left < 0 else f"{left:.1f} yrs") if left is not None else "—",
            ln.account_name or "—"])
    if rows:
        tot = pfm.financing_totals(rows)
        out.append(["Total", "", "", fmt_money(tot.principal), fmt_money(tot.balance),
                    f"{tot.rate:.3g}% avg" if tot.rate is not None else "—", "", "",
                    fmt_money(tot.debt_service), fmt_money(tot.annual_interest),
                    f"next {tot.next_maturity}" if tot.next_maturity else "", "", ""])
    return out


def loans_html(widget, rows: list[pfm.Loan], as_of: date, selected: int | None) -> str:
    """The loans as one clean page (no grid or lines, figures right-aligned): each lender is a link that selects
    the loan; the selected one is shaded; maturities under a year away in red."""
    import html as _html

    from .theme import c as tc
    from .theme import is_dark

    text, muted, red, accent = widget.palette().text().color().name(), tc("muted"), tc("red"), tc("link")
    shade = "#203040" if is_dark() else "#eef4fb"
    esc = _html.escape
    right = {3, 4, 5, 7, 8, 9, 11}
    td = "padding:3px 10px"
    head = "<tr>" + "".join(f"<td align='{'right' if c in right else 'left'}' style='{td}; color:{muted}; "
                            f"font-size:8pt'>{esc(h)}</td>" for c, h in enumerate(LOAN_COLS)) + "</tr>"
    body = []
    cells = loan_cells(rows, as_of)
    for r, vals in enumerate(cells):
        total = r == len(rows)
        ln = rows[r] if not total else None
        left = ln.years_left(as_of) if ln else None
        bg = f" background:{shade};" if (selected == r and not total) else ""
        out = []
        for c, v in enumerate(vals):
            colour = muted if (ln is not None and ln.terms_missing) else text
            if left is not None and left < 1 and c in (10, 11):
                colour = red
            weight = " font-weight:700;" if total else ""
            content = esc(v)
            if c == 0 and not total:
                content = f"<a href='loan:{r}' style='color:{accent}; text-decoration:none'>{esc(v)}</a>"
            out.append(f"<td align='{'right' if c in right else 'left'}' style='{td}; color:{colour};{weight}{bg}'>"
                       f"{content}</td>")
        if total:
            body.append(f"<tr><td colspan='{len(LOAN_COLS)}' style='font-size:4pt'>&nbsp;</td></tr>")
        body.append("<tr>" + "".join(out) + "</tr>")
    if not rows:
        body.append(f"<tr><td colspan='{len(LOAN_COLS)}' style='color:{muted}; {td}'>No loans yet — Add Loan…"
                    "</td></tr>")
    return f"<table cellspacing='0' cellpadding='0'>{head}{''.join(body)}</table>"


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------


PROJ_ROWS = ["Revenue", "Op. Ex", "EBITDA", "", "Non-Op. Ex", "CapEx", "Net cash flow", "",
             "Money in: loans & equity", "Money out: debt service & distributions", "Net cash flow after financing"]


def fill_projection_table(t: QTableWidget, actual: pfm.Financials | None, rows: list[pfm.Projection],
                          capex_actual: int | None = None) -> None:
    """Figures down the side (revenue … net cash flow), last 12 months actual then each projected year across."""
    cols: list[tuple[str, list[int | None]]] = []
    if actual is not None:
        cap = actual.capex_ltm if capex_actual is None else capex_actual
        cols.append(("Last 12 months (actual)", [actual.revenue_ltm, actual.opex_ltm, actual.ebitda_ltm, None,
                                                  actual.nonop_ltm, cap,
                                                  actual.ebitda_ltm - actual.nonop_ltm - cap, None, None, None,
                                                  None]))
    for p in rows:
        cols.append((f"{p.year} (projected)", [p.revenue, p.opex, p.ebitda, None, p.nonop, p.capex, p.net_cash_flow,
                                               None, p.fin_in, p.fin_out, p.cash_after_financing]))
    t.clear()
    t.setColumnCount(len(cols) + 1)
    t.setRowCount(len(PROJ_ROWS))
    t.setHorizontalHeaderLabels([""] + [c[0] for c in cols])
    for r_, label in enumerate(PROJ_ROWS):
        head = QTableWidgetItem(label)
        if label in ("EBITDA", "Net cash flow", "Net cash flow after financing"):
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
            if label in ("EBITDA", "Net cash flow", "Net cash flow after financing"):
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


class ProjectionGrid(QWidget):
    """Years across, items down for some of the projection sections (e.g. revenue and operating expenses), with
    each section's total and the figures that follow from them. Changes are reported with ``changed``."""

    changed = Signal()
    LABELS = {"revenue": "Total revenue", "opex": "Total operating expenses", "capex": "Total CapEx",
              "fin_in": "Total money in", "fin_out": "Total money out"}

    def __init__(self, kinds: tuple[str, ...], *, nonop: bool = False, parent=None):
        super().__init__(parent)
        self.kinds, self.has_nonop = kinds, nonop
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 0)
        self.table = QTableWidget(0, 1)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        for k in kinds:
            b = QPushButton({"revenue": "Add Revenue Item", "opex": "Add Expense Item", "capex": "Add CapEx Item",
                             "fin_in": "Add Money In", "fin_out": "Add Money Out"}[k])
            b.clicked.connect(lambda _=False, k=k: self.add_item(k))
            row.addWidget(b)
        rm = QPushButton("Remove Item")
        rm.clicked.connect(self.remove_item)
        row.addWidget(rm)
        row.addStretch(1)
        lay.addLayout(row)
        self.years: list[int] = []
        self.roles: list[tuple] = []

    def build(self, years: list[int], lines: list[pfm.ProjectionLine], nonop: dict[int, int]) -> None:
        from .theme import c as tc

        t = self.table
        t.blockSignals(True)
        t.clear()
        self.years = sorted(years)
        self.roles = []
        t.setColumnCount(len(self.years) + 1)
        t.setHorizontalHeaderLabels(["Item"] + [str(y) for y in self.years])
        t.setRowCount(0)

        def row(role, label, values=None, *, editable=False, bold=False, head=False):
            r_ = t.rowCount()
            t.insertRow(r_)
            name = QTableWidgetItem(label)
            if role[0] != "item":
                name.setFlags(name.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if bold or head:
                f = name.font()
                f.setBold(True)
                name.setFont(f)
            if head:
                name.setForeground(QColor(tc("link")))
            t.setItem(r_, 0, name)
            for c_, y in enumerate(self.years, 1):
                cell = QTableWidgetItem("" if head or not editable else fmt_money((values or {}).get(y) or 0))
                cell.setTextAlignment(ALIGN_RIGHT)
                if not editable:
                    cell.setFlags(cell.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if bold:
                    f = cell.font()
                    f.setBold(True)
                    cell.setFont(f)
                t.setItem(r_, c_, cell)
            self.roles.append(role)

        for kind in self.kinds:
            row(("head", kind), pfm.PROJ_KINDS[kind].upper(), head=True)
            for ln in (x for x in lines if x.kind == kind):
                row(("item", kind), ln.item, ln.amounts, editable=True)
            row(("total", kind), self.LABELS[kind], bold=True)
        if "opex" in self.kinds:
            row(("calc", "noi"), "NOI / EBITDA", bold=True)
        if self.has_nonop:
            row(("nonop",), "Non-Op. Ex (taxes, other)", nonop, editable=True)
            row(("calc", "ops"), "Cash flow from operations", bold=True)
        if "fin_out" in self.kinds:
            row(("calc", "fin"), "Net financing & equity", bold=True)
        t.setColumnWidth(0, 340)
        for c_ in range(1, len(self.years) + 1):
            t.setColumnWidth(c_, 140)
        t.blockSignals(False)
        self._recalc()

    @staticmethod
    def _cents(text: str) -> int:
        from ..money import parse_money

        v = parse_money(text.strip()) if text and text.strip() else 0
        return abs(v or 0)

    def _val(self, r_: int, c_: int) -> int:
        it = self.table.item(r_, c_)
        return self._cents(it.text()) if it else 0

    def state(self) -> tuple[list[pfm.ProjectionLine], dict[int, int] | None]:
        lines, nonop = [], None
        for r_, role in enumerate(self.roles):
            if role[0] == "item":
                lines.append(pfm.ProjectionLine(role[1], self.table.item(r_, 0).text().strip(),
                                                {y: self._val(r_, c_) for c_, y in enumerate(self.years, 1)}))
            elif role[0] == "nonop":
                nonop = {y: self._val(r_, c_) for c_, y in enumerate(self.years, 1)}
        return lines, nonop

    def _edited(self, item=None) -> None:
        if item is not None and item.column() > 0:     # typed amounts shown as money
            self.table.blockSignals(True)
            item.setText(fmt_money(self._cents(item.text())))
            self.table.blockSignals(False)
        self._recalc()
        self.changed.emit()

    def _recalc(self) -> None:
        t = self.table
        t.blockSignals(True)
        for c_, _y in enumerate(self.years, 1):
            sums = {k: 0 for k in pfm.PROJ_KINDS}
            nonop = 0
            for r_, role in enumerate(self.roles):
                if role[0] == "item":
                    sums[role[1]] += self._val(r_, c_)
                elif role[0] == "nonop":
                    nonop = self._val(r_, c_)
            noi = sums["revenue"] - sums["opex"]
            calc = {"noi": noi, "ops": noi - nonop, "fin": sums["fin_in"] - sums["fin_out"]}
            for r_, role in enumerate(self.roles):
                v = sums[role[1]] if role[0] == "total" else calc.get(role[1]) if role[0] == "calc" else None
                if v is not None:
                    t.item(r_, c_).setText(fmt_money(v))
                    if role[0] == "calc":
                        t.item(r_, c_).setForeground(NEGATIVE if v < 0 else POSITIVE)
        t.blockSignals(False)

    def add_item(self, kind: str) -> None:
        lines, nonop = self.state()
        names = {ln.item for ln in lines if ln.kind == kind}
        name, n = "New item", 2
        while name in names:
            name, n = f"New item {n}", n + 1
        lines.append(pfm.ProjectionLine(kind, name))
        self.build(self.years, lines, nonop or {})
        r_ = max(i for i, role in enumerate(self.roles) if role == ("item", kind))
        self.table.setCurrentCell(r_, 0)
        self.table.editItem(self.table.item(r_, 0))
        self.changed.emit()

    def remove_item(self) -> None:
        r_ = self.table.currentRow()
        if not 0 <= r_ < len(self.roles) or self.roles[r_][0] != "item":
            return
        self.table.removeRow(r_)
        del self.roles[r_]
        self._recalc()
        self.changed.emit()


SUMMARY_ROWS = [("Revenue", "revenue"), ("Operating expenses", "opex"), ("NOI / EBITDA", "ebitda"),
                ("Non-Op. Ex (taxes, other)", "nonop"), ("CapEx", "capex"),
                ("Cash flow before financing", "net_cash_flow"), ("", None),
                ("Money in: loans & equity", "fin_in"), ("Money out: debt service & distributions", "fin_out"),
                ("Net financing & equity", "net_financing"), ("", None),
                ("Net cash flow after financing", "cash_after_financing")]


class ProjectionEditor(QWidget):
    """A company's projections in four sub-tabs: Summary (everything flowing together), Revenue & OpEx, CapEx and
    Financing & Equity — edited in place, saved together."""

    saved = Signal()

    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, actual: pfm.Financials | None = None, parent=None):
        super().__init__(parent)
        self.pf, self.holding, self.actual = pf, holding, actual
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        for text, slot, tip in [
                ("Add Year", self.add_year, "Another year, the last one carried forward"),
                ("Remove Year", self.remove_year, "The selected year (or the last)"),
                ("Fill from Last 12 Months…", self.fill_from_actuals,
                 "Revenue and expense items from the books, grown by a % a year; capex from the CapEx schedule; "
                 "interest and principal from the loans"),
                ("Fill Financing from the Loans", self.fill_financing,
                 "Each loan's scheduled interest and principal by year (Financing & Cap Table)")]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch(1)
        self.dirty_label = QLabel()
        self.dirty_label.setObjectName("hint")
        bar.addWidget(self.dirty_label)
        self.revert_btn = QPushButton("Revert")
        self.revert_btn.clicked.connect(self.load)
        bar.addWidget(self.revert_btn)
        self.save_btn = QPushButton("Save Projections")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save)
        bar.addWidget(self.save_btn)
        lay.addLayout(bar)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        sw = QWidget()
        swl = QVBoxLayout(sw)
        swl.setContentsMargins(0, 4, 0, 0)
        self.returns_line = QLabel()            # projected IRR / MOIC to the exit
        self.returns_line.setTextFormat(Qt.TextFormat.RichText)
        swl.addWidget(self.returns_line)
        self.summary_table = _table([""], None)
        swl.addWidget(self.summary_table, 1)
        self.tabs.addTab(sw, "Summary")
        self.grids = {"ops": ProjectionGrid(("revenue", "opex"), nonop=True),
                      "capex": ProjectionGrid(("capex",)),
                      "fin": ProjectionGrid(("fin_in", "fin_out"))}
        for key, title in (("ops", "Revenue && OpEx"), ("capex", "CapEx"), ("fin", "Financing && Equity")):
            self.grids[key].changed.connect(self._changed)
            self.tabs.addTab(self.grids[key], title)
        self.tabs.addTab(self._build_exit(), "Exit && Returns")
        lay.addWidget(self.tabs, 1)
        self.years: list[int] = []
        self.load()

    # ---- exit & returns ----
    def _build_exit(self) -> QWidget:
        from PySide6.QtWidgets import QTextBrowser

        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 4, 0, 0)
        side = QWidget()
        form = QFormLayout(side)
        self.x_date = QDateEdit()
        self.x_date.setCalendarPopup(True)
        self.x_date.setDisplayFormat("MMM d, yyyy")
        self.x_date.setToolTip("The planned exit (the same date as the Dashboard's Exit milestone)")
        from .. import exit_returns as er

        self.x_method = QComboBox()
        for k, label in er.METHODS.items():
            self.x_method.addItem(label, k)
        self.x_basis = QComboBox()
        for k, label in er.BASES.items():
            self.x_basis.addItem(label[0].upper() + label[1:], k)
        self.x_basis.setToolTip("Which projected year's NOI / EBITDA the cap rate or multiple is applied to")
        self.x_cap = QDoubleSpinBox()
        self.x_cap.setRange(0, 50)
        self.x_cap.setDecimals(2)
        self.x_cap.setSuffix(" % exit cap rate")
        self.x_mult = QDoubleSpinBox()
        self.x_mult.setRange(0, 99)
        self.x_mult.setDecimals(2)
        self.x_mult.setSuffix(" × NOI / EBITDA")
        self.x_value = _money_box()
        self.x_value.setToolTip("An exit value instead of NOI ÷ cap rate ($0 = use the cap rate)")
        self.x_sell = QDoubleSpinBox()
        self.x_sell.setRange(0, 49)
        self.x_sell.setDecimals(2)
        self.x_sell.setSuffix(" % selling costs")
        self.x_sched = QCheckBox("Loans' scheduled balance")
        self.x_debt = _money_box()
        self.x_debt.setToolTip("The debt repaid from the sale")
        for wdg in (self.x_cap, self.x_mult, self.x_value, self.x_sell, self.x_debt):
            wdg.valueChanged.connect(self._exit_changed)
        for wdg in (self.x_method, self.x_basis):
            wdg.currentIndexChanged.connect(self._exit_changed)
        self.x_date.dateChanged.connect(self._exit_changed)
        self.x_sched.toggled.connect(self._exit_changed)
        form.addRow("Exit date:", self.x_date)
        form.addRow("Valued by:", self.x_method)
        form.addRow("Applied to:", self.x_basis)
        form.addRow("Exit cap rate:", self.x_cap)
        form.addRow("Exit multiple:", self.x_mult)
        form.addRow("… or exit value:", self.x_value)
        form.addRow("Selling costs:", self.x_sell)
        form.addRow("Debt repaid at exit:", self.x_debt)
        form.addRow("", self.x_sched)
        hint = QLabel("The exit value is the projected NOI / EBITDA at the exit ÷ the cap rate (or × the multiple). "
                      "At the exit the asset is sold and the company wound up: the loans and every other debt "
                      "account repaid, receivables collected, payables paid and all its cash paid out. "
                      "The owners' money in is "
                      "the cap table's paid-in amounts on their dates; each year to the exit adds the cash flow "
                      "after debt service; the sale adds what's left after selling costs and repaying the debt.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        side.setMaximumWidth(420)
        row.addWidget(side)
        self.returns_page = QTextBrowser()
        self.returns_page.setFrameShape(QTextBrowser.Shape.NoFrame)
        row.addWidget(self.returns_page, 1)
        return w

    def _load_exit(self) -> None:
        from .. import exit_returns as er

        a = er.get(self.pf, self.holding)
        widgets = (self.x_date, self.x_method, self.x_basis, self.x_cap, self.x_mult, self.x_value, self.x_sell,
                   self.x_debt, self.x_sched)
        for wdg in widgets:
            wdg.blockSignals(True)
        self.x_method.setCurrentIndex(max(0, self.x_method.findData(a.method)))
        self.x_basis.setCurrentIndex(max(0, self.x_basis.findData(a.basis)))
        self.x_mult.setValue(a.multiple)
        d = date.fromisoformat(a.exit_date) if a.exit_date else date(
            (max(self.years) if self.years else date.today().year + 5), 12, 31)
        self.x_date.setDate(QDate(d.year, d.month, d.day))
        self.x_cap.setValue(a.exit_cap_pct)
        self.x_value.setValue(a.exit_value / 100)
        self.x_sell.setValue(a.selling_pct)
        self.x_sched.setChecked(a.debt_at_exit is None)
        self.x_debt.setValue((a.debt_at_exit or 0) / 100)
        for wdg in widgets:
            wdg.blockSignals(False)

    def exit_assumptions(self):
        from .. import exit_returns as er

        return er.ExitAssumptions(_iso(self.x_date.date()), self.x_cap.value(), round(self.x_value.value() * 100),
                                  self.x_sell.value(), None if self.x_sched.isChecked() else
                                  round(self.x_debt.value() * 100), self.x_method.currentData(),
                                  self.x_mult.value(), self.x_basis.currentData())

    def _exit_changed(self, *_) -> None:
        self._fill_returns()
        self._set_dirty(True)

    def returns(self):
        from .. import exit_returns as er

        years, lines, nonop = self.collect()
        if getattr(self, "_ctx", None) is None:          # the owners' money in, the loans, the books — read once
            self._ctx = er.context(self.pf, self.holding)
        return er.projected_returns(self.pf, self.holding, years, lines, nonop, self.exit_assumptions(),
                                    ctx=self._ctx)

    def sensitivity(self):
        from .. import exit_returns as er

        years, lines, nonop = self.collect()
        if getattr(self, "_ctx", None) is None:
            self._ctx = er.context(self.pf, self.holding)
        return er.sensitivity(self.pf, self.holding, years, lines, nonop, self.exit_assumptions(), ctx=self._ctx)

    def _sensitivity_html(self, text: str, muted: str, red: str, accent: str) -> str:
        """IRR · MOIC by exit cap rate (or multiple) down, exit year across; the assumption highlighted."""
        from .theme import is_dark

        sv = self.sensitivity()
        if not sv.years:
            return ""
        shade = "#20303c" if is_dark() else "#eef3fb"
        by_mult = sv.method == "multiple"
        corner = "Multiple ↓ · Exit →" if by_mult else "Exit cap ↓ · Exit →"
        out = [f"<p style='color:{accent}; font-size:8.5pt; font-weight:700; margin:16px 0 4px 0'>SENSITIVITY "
               f"<span style='color:{muted}; font-weight:400'>IRR · MOIC by exit "
               f"{'multiple' if by_mult else 'cap rate'} and exit year</span></p>",
               f"<table cellspacing='0' cellpadding='4' style='color:{text}'><tr>"
               f"<td style='color:{muted}; font-size:8pt'>{corner}</td>"]
        out += [f"<td align='right' style='color:{muted}; font-size:8pt; padding-left:14px'>{y}</td>" for y in sv.years]
        out.append("</tr>")
        for i, (v, line) in enumerate(zip(sv.rows, sv.cells)):
            label = f"{v:g}×" if by_mult else f"{v:.2f}%"
            out.append(f"<tr><td style='color:{muted}'>{label}</td>")
            for j, r in enumerate(line):
                base = i == sv.base_row and j == sv.base_col
                irr = f"{r.irr * 100:.1f}%" if r.irr is not None else "—"
                moic = f"{r.moic:.2f}x" if r.moic is not None else "—"
                colour = red if (r.irr is not None and r.irr < 0) else text
                bg = f"background:{shade}; font-weight:700;" if base else ""
                out.append(f"<td align='right' style='{bg} color:{colour}; padding-left:14px; white-space:nowrap'>"
                           f"{irr} · {moic}</td>")
            out.append("</tr>")
        out.append("</table>")
        note = "An exit value entered by hand is set aside here." if self.x_value.value() else ""
        if note:
            out.append(f"<p style='color:{muted}; font-size:8pt'>{note}</p>")
        return "".join(out)

    def _fill_returns(self) -> None:
        """The owners' cash flows to the exit, and the IRR / MOIC."""
        import html as _html

        from .theme import c as tc

        r = self.returns()
        self.x_debt.setEnabled(not self.x_sched.isChecked())
        if self.x_sched.isChecked():
            self.x_debt.blockSignals(True)
            self.x_debt.setValue(r.debt_repaid / 100)
            self.x_debt.blockSignals(False)
        by_cap = self.x_method.currentData() == "cap"
        self.x_cap.setEnabled(not self.x_value.value() and by_cap)
        self.x_mult.setEnabled(not self.x_value.value() and not by_cap)
        text, muted, red, green, accent = (self.palette().text().color().name(), tc("muted"), tc("red"),
                                           tc("green"), tc("link"))
        esc = _html.escape

        def m(c):
            return f"<span style='color:{red}'>({fmt_money(-c)})</span>" if c < 0 else fmt_money(c)

        irr = f"{r.irr * 100:.1f}%" if r.irr is not None else "—"
        moic = f"{r.moic:.2f}x" if r.moic is not None else "—"
        how = ("" if self.x_value.value() or not r.exit_value else
               f" <span style='color:{muted}'>at a {self.x_cap.value():g}% cap rate</span>" if by_cap else
               f" <span style='color:{muted}'>at {self.x_mult.value():g}×</span>")
        rows = [("Exit date", r.exit_date.strftime("%b %d, %Y")),
                (f"NOI / EBITDA ({r.noi_year} projected)", m(r.exit_noi)),
                ("Exit value", m(r.exit_value) + how),
                ("Less selling costs", m(-r.selling_costs)), ("Less loans repaid", m(-r.debt_repaid))]
        if r.other_debt:
            rows.append(("Less other debt in the books", m(-r.other_debt)))
        if r.receivables:
            rows.append(("Plus receivables collected", m(r.receivables)))
        if r.payables:
            rows.append(("Less payables paid", m(-r.payables)))
        if r.cash:
            rows.append(("Plus the company's cash, paid out", m(r.cash)))
        rows.append(("<b>To the owners at the exit</b>", f"<b>{m(r.net_proceeds)}</b>"))
        out = [f"<p style='color:{accent}; font-size:8.5pt; font-weight:700; margin:0 0 4px 0'>EXIT</p>",
               f"<table cellspacing='0' cellpadding='2' style='color:{text}'>"]
        out += [f"<tr><td style='padding-right:28px; color:{muted}'>{a}</td><td align='right'>{b}</td></tr>"
                for a, b in rows]
        out.append("</table>")
        colour = green if (r.irr or 0) > 0 else red
        out.append(f"<p style='color:{accent}; font-size:8.5pt; font-weight:700; margin:14px 0 4px 0'>RETURNS "
                   f"<span style='color:{muted}; font-weight:400'>to the owners, 100% of the company</span></p>"
                   f"<p style='font-size:13pt; margin:0'><b style='color:{colour}'>IRR {irr}</b> &nbsp; · &nbsp; "
                   f"<b>MOIC {moic}</b> &nbsp; · &nbsp; profit {m(r.profit)}</p>"
                   f"<p style='color:{muted}; margin:2px 0 0 0'>money in {fmt_money(r.money_in)} · money back "
                   f"{fmt_money(r.money_back)}</p>")
        if r.error:
            out.append(f"<p style='color:{red}'>{esc(r.error)}</p>")
        out.append(f"<p style='color:{accent}; font-size:8.5pt; font-weight:700; margin:14px 0 4px 0'>CASH FLOWS TO "
                   f"THE OWNERS</p><table cellspacing='0' cellpadding='2' style='color:{text}'>")
        out += [f"<tr><td style='padding-right:20px; color:{muted}'>{d:%b %d, %Y}</td><td style='padding-right:28px'>"
                f"{esc(w)}</td><td align='right'>{m(v)}</td></tr>" for d, v, w in r.flows]
        out.append("</table>")
        out.append(self._sensitivity_html(text, muted, red, accent))
        self.returns_page.setHtml("".join(out))
        self.returns_line.setText(f"Exit {r.exit_date:%b %Y}: projected <b style='color:{colour}'>IRR {irr}</b> · "
                                  f"<b>MOIC {moic}</b> · profit {m(r.profit)} — Exit && Returns for the details"
                                  .replace("&&", "&amp;"))

    # ---- the model: years, every line, Non-Op. Ex ----
    def collect(self) -> tuple[list[int], list[pfm.ProjectionLine], dict[int, int]]:
        lines: list[pfm.ProjectionLine] = []
        nonop: dict[int, int] = {}
        for g in self.grids.values():
            ls, n = g.state()
            lines += ls
            if n is not None:
                nonop = n
        order = list(pfm.PROJ_KINDS)
        lines.sort(key=lambda ln: order.index(ln.kind))
        return list(self.years), lines, nonop

    def _show(self, years: list[int], lines: list[pfm.ProjectionLine], nonop: dict[int, int]) -> None:
        self.years = sorted(years)
        for g in self.grids.values():
            g.blockSignals(True)
            g.build(self.years, [ln for ln in lines if ln.kind in g.kinds], nonop)
            g.blockSignals(False)
        self._fill_summary()

    def load(self) -> None:
        totals = pfm.projections(self.pf, self.holding.id)
        lines = pfm.projection_lines(self.pf, self.holding.id)
        years = [p.year for p in totals]
        if totals and not lines:      # projections from before items: one line per section
            lines = [pfm.ProjectionLine("revenue", "Revenue", {p.year: p.revenue for p in totals}),
                     pfm.ProjectionLine("opex", "Operating expenses", {p.year: p.opex for p in totals}),
                     pfm.ProjectionLine("capex", "CapEx", {p.year: p.capex for p in totals})]
        for kind, items in pfm.PROJ_STARTERS.items():      # the usual items in any empty section
            if not any(ln.kind == kind for ln in lines):
                lines += [pfm.ProjectionLine(kind, item) for item in items]
        self.years = sorted(years)
        self._ctx = None
        self._load_exit()
        self._show(years, lines, {p.year: p.nonop for p in totals})
        self._set_dirty(False)

    def _set_dirty(self, on: bool) -> None:
        self.dirty = on
        self.dirty_label.setText("Unsaved changes" if on else "")
        self.revert_btn.setEnabled(on)

    def _changed(self) -> None:
        self._fill_summary()
        self._set_dirty(True)

    def totals(self) -> list[pfm.Projection]:
        years, lines, nonop = self.collect()
        out = []
        for y in years:
            get = lambda kind: sum(ln.amounts.get(y, 0) for ln in lines if ln.kind == kind)  # noqa: E731
            out.append(pfm.Projection(y, get("revenue"), get("opex"), nonop.get(y, 0), get("capex"), "",
                                      get("fin_in"), get("fin_out")))
        return out

    def _fill_summary(self) -> None:
        """Everything flowing together: revenue → NOI → capex → financing → net cash flow (and the last 12 months)."""
        rows = self.totals()
        a = self.actual
        heads = (["Last 12 months (actual)"] if a else []) + [f"{p.year} (projected)" for p in rows]
        t = self.summary_table
        t.clear()
        t.setColumnCount(len(heads) + 1)
        t.setRowCount(len(SUMMARY_ROWS))
        t.setHorizontalHeaderLabels([""] + heads)
        act = {"revenue": a.revenue_ltm, "opex": a.opex_ltm, "ebitda": a.ebitda_ltm, "nonop": a.nonop_ltm,
               "capex": a.capex_ltm, "net_cash_flow": a.ebitda_ltm - a.nonop_ltm - a.capex_ltm} if a else {}
        for r_, (label, attr) in enumerate(SUMMARY_ROWS):
            bold = attr in ("ebitda", "net_cash_flow", "net_financing", "cash_after_financing")
            head = QTableWidgetItem(label)
            if bold:
                f = head.font()
                f.setBold(True)
                head.setFont(f)
            t.setItem(r_, 0, head)
            if attr is None:
                t.setRowHeight(r_, 10)
                continue
            vals = ([act.get(attr)] if a else []) + [getattr(p, attr) for p in rows]
            for c_, v in enumerate(vals):
                cell = QTableWidgetItem("" if v is None else fmt_money(v))
                cell.setTextAlignment(ALIGN_RIGHT)
                if bold:
                    f = cell.font()
                    f.setBold(True)
                    cell.setFont(f)
                    if v is not None and v < 0:
                        cell.setForeground(NEGATIVE)
                t.setItem(r_, c_ + 1, cell)
        t.setColumnWidth(0, 280)
        for c_ in range(len(heads)):
            t.setColumnWidth(c_ + 1, 165)
        if hasattr(self, "x_date"):
            self._fill_returns()

    # ---- actions ----
    def add_year(self) -> None:
        years, lines, nonop = self.collect()
        nxt = (max(years) + 1) if years else date.today().year + 1
        if years:                     # the last year carried forward
            for ln in lines:
                ln.amounts[nxt] = ln.amounts.get(years[-1], 0)
            nonop[nxt] = nonop.get(years[-1], 0)
        self._show(years + [nxt], lines, nonop)
        self._set_dirty(True)

    def remove_year(self) -> None:
        years, lines, nonop = self.collect()
        if not years:
            return
        g = self.tabs.currentWidget()
        c_ = g.table.currentColumn() if isinstance(g, ProjectionGrid) else -1
        drop = years[c_ - 1] if 1 <= c_ <= len(years) else years[-1]
        self._show([y for y in years if y != drop], lines, nonop)
        self._set_dirty(True)

    def fill_financing(self) -> None:
        years, lines, nonop = self.collect()
        if not years:
            error(self, "No years yet", "Add the years first (Add Year or Fill from Last 12 Months…).")
            return
        new = pfm.loan_lines(self.pf, self.holding, years)
        if not new:
            info(self, "No loan payments", "No loan with terms on Financing & Cap Table pays anything in these years.")
            return
        names = {ln.item for ln in new}
        lines = [ln for ln in lines if not (ln.kind == "fin_out" and (
            ln.item in names or (ln.item in ("Interest", "Principal repayments") and not ln.total())))] + new
        self._show(years, lines, nonop)
        self.tabs.setCurrentWidget(self.grids["fin"])
        self._set_dirty(True)

    def fill_from_actuals(self) -> None:
        """Revenue and expense items from the books' last 12 months (one per account), grown by a % a year; capex
        from the CapEx schedule's cost to complete, spread over the first years; financing from the loans."""
        from PySide6.QtWidgets import QSpinBox

        from .. import capex as cx

        path = self.holding.company_path
        if self.actual is None and not (path and Path(path).exists()):
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
        spread = QSpinBox()
        spread.setRange(1, 10)
        spread.setValue(2)
        spread.setSuffix(" year(s)")
        spread.setToolTip("The CapEx schedule's cost to complete, spread evenly over this many first years")
        for label, w in [("First year:", first), ("Number of years:", n), ("Revenue growth:", rg),
                         ("Op. Ex growth:", og), ("Non-Op. Ex growth:", ng), ("CapEx to complete over:", spread)]:
            form.addRow(label, w)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return
        years = list(range(first.value(), first.value() + n.value()))

        def grow(base: int, pct: float, i: int) -> int:
            return round(base * (1 + pct / 100) ** (i + 1))

        lines: list[pfm.ProjectionLine] = []
        base = pfm.ltm_lines(path) if path and Path(path).exists() else []
        if not base and self.actual is not None:
            base = [("revenue", "Revenue", self.actual.revenue_ltm), ("opex", "Operating expenses",
                                                                      self.actual.opex_ltm)]
        for kind, name, amt in base:
            pct = rg.value() if kind == "revenue" else og.value()
            lines.append(pfm.ProjectionLine(kind, name, {y: grow(amt, pct, i) for i, y in enumerate(years)}))
        groups: dict[str, int] = {}          # by line (a unit's cost under its line)
        for ln in cx.lines(self.pf, self.holding):
            if ln.to_complete > 0:
                key = ln.label if ln.parent_id is None else ln.parent_item
                groups[key] = groups.get(key, 0) + ln.to_complete
        k = min(spread.value(), len(years))
        for name, left in groups.items():
            each = round(left / k)
            lines.append(pfm.ProjectionLine("capex", name, {y: (each if i < k - 1 else left - each * (k - 1))
                                                            if i < k else 0 for i, y in enumerate(years)}))
        if not any(ln.kind == "capex" for ln in lines) and self.actual is not None:
            lines.append(pfm.ProjectionLine("capex", "CapEx", {y: self.actual.capex_ltm for y in years}))
        lines += pfm.loan_lines(self.pf, self.holding, years)
        _y, old, _n = self.collect()          # keep what was typed for money in and distributions
        lines += [ln for ln in old if ln.kind == "fin_in" or (ln.kind == "fin_out" and ln.item ==
                                                               "Distributions to owners")]
        nonop_base = self.actual.nonop_ltm if self.actual is not None else 0
        self._show(years, lines, {y: grow(nonop_base, ng.value(), i) for i, y in enumerate(years)})
        self._set_dirty(True)

    def save(self) -> bool:
        years, lines, nonop = self.collect()
        lines = [ln for ln in lines if ln.item]
        from .. import exit_returns as er

        try:
            pfm.save_projection_plan(self.pf, self.holding.id, years, lines, nonop)
            er.save(self.pf, self.holding, self.exit_assumptions())
        except ValueError as e:
            error(self, "Couldn't save the projections", str(e))
            return False
        self._set_dirty(False)
        self.saved.emit()
        return True


class ProjectionsDialog(QDialog):
    """The projection editor in a window (Summary, Revenue & OpEx, CapEx, Financing & Equity)."""

    def __init__(self, pf: pfm.Portfolio, holding: pfm.Holding, actual: pfm.Financials | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Projections – {holding.name}")
        self.resize(1180, 660)
        lay = QVBoxLayout(self)
        self.editor = ProjectionEditor(pf, holding, actual, self)
        self.editor.save_btn.hide()
        self.editor.revert_btn.hide()
        lay.addWidget(self.editor, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def save(self) -> None:
        if self.editor.save():
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

HOLD_COLS = ["Holding", "Type", "Own %", "Yours %", "Invested", "Returned", "Value (100%)", "Value date / method",
             "Our value (NAV)", "Gain", "MOIC", "IRR", "Revenue LTM", "Net income LTM", "Linked books"]


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


class AssetManagementView(QWidget):
    open_company = Signal(str)   # switch to Accounting with this company file
    open_loans = Signal(str)     # … and open its Loans window (record loan payments)
    open_unnamed = Signal(str)   # … Transactions with no customer / vendor (to assign vendors)
    open_review = Signal(str)    # … Transactions to categorize / review

    def __init__(self, portfolio_path: str | Path, companies_fn, create_company=None, *, refresh: bool = True):
        super().__init__()
        self.pf = pfm.Portfolio(portfolio_path)
        try:            # today's copy of the portfolio file (see backups.py)
            from .. import backups

            backups.daily(self.pf.conn, self.pf.path, "Portfolio")
        except Exception:  # noqa: BLE001
            pass
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
        bar.addSpacing(10)
        self.summary_btn = QPushButton("Portfolio Summary")
        self.summary_btn.setCheckable(True)
        self.summary_btn.setObjectName("primary")
        self.summary_btn.setToolTip("The whole portfolio (or the group picked under Viewing) at a glance")
        self.summary_btn.clicked.connect(self.show_summary)
        bar.addWidget(self.summary_btn)
        self.deals_btn = QPushButton("Deal Pipeline")
        self.deals_btn.setCheckable(True)
        self.deals_btn.setObjectName("primary")
        self.deals_btn.setToolTip("Deals being looked at: new deals, under due diligence, under contract")
        self.deals_btn.clicked.connect(self.show_deals)
        bar.addWidget(self.deals_btn)
        self.company_btn = QPushButton("Company Pages")
        self.company_btn.setCheckable(True)
        self.company_btn.setObjectName("primary")
        self.company_btn.setToolTip("One company's pages: Dashboard, Rent Roll & Operations, Cash Flow, CapEx, "
                                    "Financing & Cap Table, Waterfall, Projections")
        self.company_btn.clicked.connect(self.show_company_pages)
        bar.addWidget(self.company_btn)
        self.net_worth_btn = QPushButton("Personal Finance")
        self.net_worth_btn.setCheckable(True)
        self.net_worth_btn.setObjectName("primary")
        self.net_worth_btn.setToolTip("Your own money: net worth (and the statement to send a lender), a monthly "
                                      "budget, and the income you take from your businesses")
        self.net_worth_btn.clicked.connect(lambda *_: self.show_personal())
        bar.addWidget(self.net_worth_btn)
        # the portfolio's own controls: only with the portfolio (not over a company's pages, deals or net worth)
        self.portfolio_bar = QWidget()
        pbar = QHBoxLayout(self.portfolio_bar)
        pbar.setContentsMargins(0, 0, 0, 0)
        outer_bar, bar = bar, pbar
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
        bar = outer_bar
        bar.addWidget(self.portfolio_bar, 1)
        lay.addLayout(bar)

        # what needs attention for the company shown (rent roll, expenses, capex) — like Accounting's dashboard
        self.attention = QFrame()
        self.attention.setObjectName("attention")
        att = QHBoxLayout(self.attention)
        att.setContentsMargins(12, 6, 8, 6)
        self.attention_text = QLabel()
        self.attention_text.setTextFormat(Qt.TextFormat.RichText)
        self.attention_text.setWordWrap(True)
        self.attention_text.linkActivated.connect(self._attention_go)
        att.addWidget(self.attention_text, 1)
        self.attention_btn = QPushButton("Review")
        self.attention_btn.clicked.connect(lambda: self._attention_go(self._attention_first))
        att.addWidget(self.attention_btn)
        self.attention.hide()
        self._attention_first = ""
        self._attention_for = None
        lay.addWidget(self.attention)
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
        self.tabs.setTabVisible(0, False)  # opened with the Portfolio Summary button at the top, not a tab
        self.tabs.currentChanged.connect(lambda i: self.summary_btn.setChecked(i == 0))
        self.summary_btn.setChecked(self.tabs.currentIndex() == 0)

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
        from .company_folder_page import CompanyFolderPage

        self.folder_page = CompanyFolderPage(self)
        pages = (self.dash_page, self.ops_page, self.cash_page, self.capex_page, self.folder_page)
        self.tabs.currentChanged.connect(  # each property page shows the latest figures when it's opened
            lambda i: self.tabs.widget(i).fill() if self.tabs.widget(i) in pages else None)
        self.tabs.addTab(self._build_waterfall_tab(), "Waterfall (LP / GP)")
        pv = QWidget()
        pl = QVBoxLayout(pv)
        self.proj_pick = QComboBox(pv)      # hidden: follows the Company box at the very top of the window
        self.proj_pick.currentIndexChanged.connect(lambda *_: self._fill_projections())
        self.proj_pick.hide()
        self.proj_note = QLabel()
        self.proj_note.setObjectName("hint")
        self.proj_note.setWordWrap(True)
        pl.addWidget(self.proj_note)
        from PySide6.QtWidgets import QStackedWidget

        self.proj_stack = QStackedWidget()      # the portfolio's total, or one company's editor
        self.proj_table = _table([""], None)
        self.proj_stack.addWidget(self.proj_table)
        pl.addWidget(self.proj_stack, 1)
        self.proj_editor: ProjectionEditor | None = None
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
        self._arrange_tabs()
        self.setStyleSheet("QFrame#card { background: palette(base); border: 1px solid palette(midlight); "
                           "border-radius: 8px; } QLabel#cardtitle { color: palette(placeholder-text); }")
        if refresh:          # (the main window refreshes it once it's shown)
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

    def _arrange_tabs(self) -> None:
        """The whole portfolio (summary, holdings, consolidated financials, managed companies) sits behind the
        Portfolio Summary button; the tabs are for one company: Dashboard (with what was the Company Overview),
        Rent Roll & Operations (with Operating Costs and Customers & Vendors), Cash Flow, CapEx, Financing & Cap Table,
        Waterfall, Projections."""
        pages = {self.tabs.tabText(i): self.tabs.widget(i) for i in range(self.tabs.count())}
        self.tabs.blockSignals(True)
        while self.tabs.count():
            self.tabs.removeTab(0)
        self.portfolio_tabs = QTabWidget()
        self.portfolio_tabs.setDocumentMode(True)
        self.summary_page = pages["Portfolio Summary"]
        for name, label in (("Portfolio Summary", "Summary"), ("Holdings", "Holdings"),
                            ("Consolidated Financials", "Consolidated Financials"),
                            ("Managed Companies", "Managed Companies")):
            self.portfolio_tabs.addTab(pages[name], label)
        from .intercompany_tab import IntercompanyPage

        self.ic_page = IntercompanyPage(self._companies, self._as_of_date, open_books=self.open_company.emit)
        self.portfolio_tabs.addTab(self.ic_page, "Intercompany")
        self.portfolio_tabs.currentChanged.connect(
            lambda *_: self.ic_page.fill() if self.portfolio_tabs.currentWidget() is self.ic_page else None)
        self.portfolio_area = self.portfolio_tabs
        self.tabs.addTab(self.portfolio_area, "Portfolio")
        self.tabs.setTabVisible(0, False)  # opened with the Portfolio Summary button at the top, not a tab
        self.overview_page = pages["Company Overview"]   # merged into the Dashboard; kept for its figures
        self.overview_page.setParent(self)
        self.overview_page.hide()
        for name in ("Dashboard", "Rent Roll && Operations", "Cash Flow", "CapEx", "Financing && Cap Table",
                     "Waterfall (LP / GP)", "Projections"):
            self.tabs.addTab(pages[name], name)
        self.tabs.addTab(self.folder_page, "Deal Folder")     # the company's deal folder (see deal_folders.py)
        # Customers & Vendors sits in Rent Roll & Operations, following its company
        self.cv_page = pages["Customers && Vendors"]
        after = next(i for i in range(self.ops_page.sub.count()) if self.ops_page.sub.tabText(i) == "Operating Costs")
        self.ops_page.sub.insertTab(after + 1, self.cv_page, "Customers && Vendors")
        self.ops_page.pick.currentIndexChanged.connect(lambda *_: self._cv_follow_ops())
        from .deals_tab import DealPipelinePage

        self.deals_page = DealPipelinePage(self.pf, on_added=lambda _hid: self.refresh(),
                                           create_company=self.create_company, companies=self._companies)
        self.tabs.addTab(self.deals_page, "Deal Pipeline")
        self.tabs.setTabVisible(self.tabs.indexOf(self.deals_page), False)   # opened with its button at the top
        self.tabs.currentChanged.connect(self._top_buttons)
        self.tabs.currentChanged.connect(lambda *_: self._update_attention())
        for combo in (self.dash_page.pick, self.ops_page.pick, self.cash_page.pick, self.capex_page.pick,
                      self.fin_pick, self.folder_page.pick):
            combo.currentIndexChanged.connect(lambda *_: self._update_attention())
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.summary_btn.setChecked(True)
        self._top_buttons()

    def show_portfolio(self, page=None) -> None:
        """The portfolio area (Summary, Holdings, Consolidated Financials, Managed Companies)."""
        self.tabs.setCurrentIndex(0)
        if page is not None:
            self.portfolio_tabs.setCurrentWidget(page)
        self.summary_btn.setChecked(True)

    def show_summary(self) -> None:
        self.show_portfolio(self.summary_page)

    def show_deals(self) -> None:
        """The deal pipeline (beside Portfolio Summary)."""
        self.deals_page.refresh()
        self.tabs.setCurrentWidget(self.deals_page)
        self._top_buttons()

    def show_personal(self, tab: str | None = None) -> None:
        """Personal Finance (beside Portfolio Summary): Net Worth, Budget, Income from Businesses."""
        if getattr(self, "personal_page", None) is None:
            from .personal_finance_page import PersonalFinancePage

            self.personal_page = PersonalFinancePage(self.pf, as_of=self._as_of_date)
            self.net_worth_page = self.personal_page.net_worth
            i = self.tabs.addTab(self.personal_page, "Personal Finance")
            self.tabs.setTabVisible(i, False)          # opened with its button at the top
            from .. import personal as pers

            if not pers.load(self.pf).name:            # the first time: your name and folders
                self._first_settings = self.personal_page.settings(first=True)
        else:
            self.personal_page.refresh()
        if tab is not None:
            pg = self.personal_page
            pg.tabs.setCurrentWidget({"net_worth": pg.net_worth, "budget": pg.budget, "income": pg.income}[tab])
        self.tabs.setCurrentWidget(self.personal_page)
        self._top_buttons()

    def show_net_worth(self) -> None:
        self.show_personal("net_worth")

    def show_company_pages(self) -> None:
        """Back to the company pages (the one open last, or the Dashboard)."""
        page = getattr(self, "_last_company_page", None) or self.dash_page
        self.tabs.setCurrentWidget(page)
        self._top_buttons()

    def _top_buttons(self, *_) -> None:
        page = self.tabs.currentWidget()
        portfolio = self.tabs.currentIndex() == 0
        deals = page is getattr(self, "deals_page", None)
        worth = page is not None and page is getattr(self, "personal_page", None)
        other = portfolio or deals or worth
        self.summary_btn.setChecked(portfolio)
        self.deals_btn.setChecked(deals)
        self.net_worth_btn.setChecked(worth)
        if hasattr(self, "company_btn"):
            self.company_btn.setChecked(not other)
        # the company tabs only with a company's pages (not over the portfolio, deal pipeline or net worth)
        self.tabs.tabBar().setVisible(not other)
        # As of, Viewing, New Holding … only with the portfolio
        self.portfolio_bar.setVisible(portfolio)
        if not other:
            self._last_company_page = page

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
            self.show_portfolio(self.managed_page if role == "asset_manager" else self.summary_page)
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
            self.show_summary()
            return "This company isn't in the portfolio — tick “Pull in all operating companies” or add it with " \
                   "New Holding…."
        self.co_pick.setCurrentIndex(self.co_pick.findData(perf.holding.id))
        self.cv_pick.setCurrentIndex(self.cv_pick.findData(perf.holding.id))
        if self.tabs.currentIndex() == 0:
            self.tabs.setCurrentWidget(self.dash_page)  # its Dashboard (stays on another company tab if open)
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
        self._update_attention(force=True)

    def _fill_holdings(self, t: QTableWidget, s: pfm.Summary) -> None:
        perfs = s.performances
        t.setRowCount(len(perfs) + (1 if perfs else 0))
        money = {4, 5, 6, 8, 9, 10, 11, 12, 13}
        from .. import personal as pers

        saved = pers.load(self.pf)
        for r, p in enumerate(perfs):
            h, f = p.holding, p.financials
            val = "" if p.value_100 is None else fmt_money(p.value_100)
            linked = (f.company if f and not f.error else ("⚠ " + f.error if f else "")) if h.company_path else ""
            mine = pers.my_ownership(self.pf, h, saved)
            _set_row(t, r, [h.name + (" (exited)" if h.status == "exited" else ""), h.type, f"{h.ownership_pct:g}%",
                            f"{mine.pct:.2f}%".replace(".00%", "%") if mine.pct else "—",
                            fmt_money(p.invested), fmt_money(p.returned), val,
                            f"{p.value_date} · {p.value_method}" if p.value_date else "No valuation yet",
                            fmt_money(p.nav), fmt_money(p.gain), _multiple(p.moic), _pct(p.irr),
                            fmt_money(f.revenue_ltm) if f and not f.error else "",
                            fmt_money(f.net_income_ltm) if f and not f.error else "", linked],
                     money, colour_col=9, colour_value=p.gain)
            t.item(r, 3).setToolTip(f"Your ownership — {mine.how}")
            if h.status == "exited":
                t.item(r, 0).setForeground(MUTED)
        if perfs:
            _set_row(t, len(perfs), ["Portfolio", "", "", "", fmt_money(s.invested), fmt_money(s.returned), "", "",
                                     fmt_money(s.nav), fmt_money(s.gain), _multiple(s.moic), _pct(s.irr), "", "", ""],
                     money, bold=True)
        for c, w in enumerate([220, 130, 60, 70, 115, 115, 120, 190, 125, 110, 60, 65, 115, 120]):
            t.setColumnWidth(c, w)

    # -- waterfall ----------------------------------------------------------------

    def _build_waterfall_tab(self) -> QWidget:
        from PySide6.QtWidgets import QGroupBox

        w = QWidget()
        lay = QVBoxLayout(w)
        # always the company picked in the Company box at the very top (the pickers below stay hidden)
        self.wf_for = QLabel()
        self.wf_for.setWordWrap(True)
        lay.addWidget(self.wf_for)
        self.wf_pick = QComboBox(w)
        self.wf_pick.currentIndexChanged.connect(lambda *_: self._wf_owner_changed())
        self.wf_pick.hide()
        self.wf_scope_label = QLabel("Holdings:", w)
        self.wf_scope_label.hide()
        self.wf_scope = QComboBox(w)
        self.wf_scope.currentIndexChanged.connect(lambda *_: self._fill_waterfall())
        self.wf_scope.hide()
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
        self.wf_gp_class = QComboBox()          # a company's cap table: which share class is the GP
        self.wf_gp_class.setToolTip("The share class that is the GP (gets the promote); the other classes are LPs")
        self.wf_gp_class.hide()
        self.wf_gp_class.currentIndexChanged.connect(lambda *_: self._fill_waterfall())
        for wdg in (self.wf_lp, self.wf_pref, self.wf_cu, self.wf_carry):
            wdg.setDecimals(2)
            tl.addWidget(wdg)
        tl.addWidget(self.wf_gp, 1)
        tl.addWidget(self.wf_gp_class, 1)
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
        self.wf_owners = _table(["Owner", "Company", "Class", "GP / LP", "Paid in", "% of capital",
                                 "Share of equity value", "Gain", "Multiple", "Of which promote"], None)
        self.wf_owners.hide()
        lay.addWidget(self.wf_owners, 1)
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
        for p in (self.summary.performances if self.summary else []):   # each company's own cap table
            if any(not x.worked_out for x in pfm.cap_table(self.pf, p)):
                self.wf_pick.addItem(f"{p.holding.name} – cap table (equity value)", f"__co__:{p.holding.id}")
        i = self.wf_pick.findData(want)
        self.wf_pick.setCurrentIndex(i if i >= 0 else 0)
        self.wf_pick.blockSignals(False)
        self._wf_follow()

    def _wf_follow(self) -> None:
        """The waterfall for the company in the Company box at the top: its own cap table when it has one, else its
        holding company's waterfall for this company alone (deal by deal)."""
        hid = self.co_pick.currentData()
        p = next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)
        if p is None:
            self.wf_for.setText("No company picked — choose one in the Company box at the top.")
            self._wf_owner_changed()
            return
        i = self.wf_pick.findData(f"__co__:{hid}")
        scope = None
        if i >= 0:
            how = "its cap table (equity value)"
        else:
            owner = self.pf.owner_of(p.holding) or ""
            i = next((k for k in range(self.wf_pick.count()) if isinstance(self.wf_pick.itemData(k), str)
                      and not self.wf_pick.itemData(k).startswith("__")
                      and pfm._same_file(self.wf_pick.itemData(k), owner)), -1)
            if i < 0:
                self.wf_pick.blockSignals(True)
                self.wf_pick.addItem(p.holding.name, owner)
                self.wf_pick.blockSignals(False)
                i = self.wf_pick.count() - 1
            scope = hid
            n = self.wf_pick.itemText(i)
            how = f"its holding company's terms ({n})" if n != p.holding.name else "the holding company's terms"
        self.wf_for.setText(f"Waterfall for <b>{__import__('html').escape(p.holding.name)}</b> — {how}. Change the company in the "
                            "Company box at the top.")
        self.wf_pick.blockSignals(True)
        self.wf_pick.setCurrentIndex(i)
        self.wf_pick.blockSignals(False)
        self._wf_owner_changed(scope=scope)

    def _wf_company(self) -> int | None:
        owner = self.wf_pick.currentData()
        return int(owner.split(":")[1]) if isinstance(owner, str) and owner.startswith("__co__:") else None

    def _wf_owner_changed(self, scope: int | None = None) -> None:
        owner = self.wf_pick.currentData()
        hid = self._wf_company()
        company = hid is not None
        for wdg in (self.wf_gp, self.wf_parts, self.wf_parts_label):
            wdg.setVisible(not company)
        self.wf_gp_class.setVisible(company)
        self.wf_owners.setVisible(company)
        self.wf_lp.setEnabled(not company)
        self.wf_terms_box.setTitle("Terms (this company's cap table)" if company else "Terms (per holding company)")
        if company:
            p = next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)
            t = pfm.get_terms(self.pf, pfm.company_terms_key(hid))
            classes = list(dict.fromkeys((x.share_class or "Common") for x in pfm.cap_table(self.pf, p))) if p else []
            self.wf_gp_class.blockSignals(True)
            self.wf_gp_class.clear()
            self.wf_gp_class.addItem("No GP (pro rata)", "")
            for c in classes:
                self.wf_gp_class.addItem(f"GP: {c}", c)
            self.wf_gp_class.setCurrentIndex(max(0, self.wf_gp_class.findData(t.gp_name)))
            self.wf_gp_class.blockSignals(False)
            self.wf_pref.setValue(t.pref_pct)
            self.wf_cu.setValue(t.catchup_pct)
            self.wf_carry.setValue(t.carry_pct)
            self.wf_terms_box.setEnabled(True)
            self._fill_waterfall()
            return
        overall = owner in (None, "__all__")
        self.wf_scope.blockSignals(True)
        self.wf_scope.clear()
        if overall:
            self.wf_scope.addItem("All holding companies", None)
        else:
            self.wf_scope.addItem("All its holdings (fund level)", None)
            for h in pfm.select(self.pf, owner=owner):
                self.wf_scope.addItem(f"{h.name} (deal by deal)", h.id)
        self.wf_scope.setCurrentIndex(max(0, self.wf_scope.findData(scope)) if scope is not None else 0)
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
        hid = self._wf_company()
        if hid is not None:
            try:
                pfm.set_terms(self.pf, pfm.company_terms_key(hid),
                              pfm.Terms(100.0, self.wf_pref.value(), self.wf_cu.value(), self.wf_carry.value(),
                                        self.wf_gp_class.currentData() or ""))
            except ValueError as e:
                error(self, "Couldn't save the terms", str(e))
                return
            self._fill_waterfall()
            return
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
        if self._wf_company() is not None:
            self._fill_company_waterfall()
            return
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

    def _fill_company_waterfall(self) -> None:
        """A company's equity value today through the tiers, owner by owner from its cap table."""
        hid = self._wf_company()
        h = next((x for x in self.pf.holdings() if x.id == hid), None)
        if h is None:
            return
        terms = pfm.Terms(100.0, self.wf_pref.value(), self.wf_cu.value(), self.wf_carry.value(),
                          self.wf_gp_class.currentData() or "")
        w, owners, value = pfm.cap_table_waterfall(self.pf, h, self._as_of_date(), terms)
        if w is None:
            for c in self.wf_cards.values():
                c.set("—")
            self.wf_table.setRowCount(0)
            self.wf_owners.setRowCount(0)
            self.wf_cards["dist"].set("—", "no equity value yet (add a valuation)" if value is None else
                                      "no owners paid in on the cap table")
            return
        gp = w.terms.gp_name
        self.wf_lp.blockSignals(True)
        self.wf_lp.setValue(w.terms.lp_pct)          # from the cap table
        self.wf_lp.blockSignals(False)
        self.wf_cards["dist"].set(fmt_money(w.distributable), f"equity value today · capital {short(w.capital)}")
        self.wf_cards["lp"].set(fmt_money(w.lp_total), "LP owners", POSITIVE)
        self.wf_cards["gp"].set(fmt_money(w.gp_total), gp)
        self.wf_cards["promote"].set(fmt_money(w.gp_promote), "catch-up + carried interest")
        self.wf_cards["lpret"].set(f"{_multiple(w.lp_moic)} · {_pct(w.lp_irr)}", f"on {short(w.lp_capital)} in")
        self.wf_cards["gpret"].set(f"{_multiple(w.gp_moic)} · {_pct(w.gp_irr)}",
                                   f"on {short(w.gp_capital)} in" if w.gp_capital else "no GP capital")
        self.wf_bar.set_parts([("LPs", w.lp_total), (gp, w.gp_total)])
        t = self.wf_table
        t.setRowCount(len(w.tiers) + 1)
        for r_, tier in enumerate(w.tiers):
            note = tier.note + (f" · owed {fmt_money(w.pref)}" if tier.name == "Preferred return" else "")
            _set_row(t, r_, [f"{r_ + 1}. {tier.name}", fmt_money(tier.total), fmt_money(tier.lp), fmt_money(tier.gp),
                             note], {1, 2, 3})
        _set_row(t, len(w.tiers), ["Total", fmt_money(w.distributable), fmt_money(w.lp_total), fmt_money(w.gp_total),
                                   ""], {1, 2, 3}, bold=True)
        o = self.wf_owners
        o.setRowCount(len(owners) + 1)
        for r_, x in enumerate(owners):
            _set_row(o, r_, [x.name, x.company or "—", x.share_class, x.role, fmt_money(x.paid_in),
                             f"{x.pct * 100:.2f}%", fmt_money(x.value), fmt_money(x.gain), _multiple(x.multiple),
                             fmt_money(x.promote) if x.promote else ""], {4, 5, 6, 7, 8, 9})
        _set_row(o, len(owners), ["Total", "", "", "", fmt_money(sum(x.paid_in for x in owners)), "100.00%",
                                  fmt_money(sum(x.value for x in owners)), fmt_money(sum(x.gain for x in owners)),
                                  _multiple(w.distributable / w.capital if w.capital else None),
                                  fmt_money(w.gp_promote) if w.gp_promote else ""], {4, 5, 6, 7, 8, 9}, bold=True)
        o.resizeColumnsToContents()

    def _fill_proj_picker(self) -> None:
        want = self.proj_pick.currentData()
        self.proj_pick.blockSignals(True)
        self.proj_pick.clear()
        self.proj_pick.addItem("Portfolio (our share)", None)
        for p in (self.summary.performances if self.summary else []):
            self.proj_pick.addItem(p.holding.name, p.holding.id)
        if self.co_pick.currentData() is not None:
            want = self.co_pick.currentData()
        i = self.proj_pick.findData(want)
        self.proj_pick.setCurrentIndex(i if i >= 0 else 0)
        self.proj_pick.blockSignals(False)
        self._fill_projections()

    def _fill_projections(self) -> None:
        """The portfolio's projections added up (at our share), or one company's: Summary, Revenue & OpEx, CapEx and
        Financing & Equity, edited in place."""
        hid = self.proj_pick.currentData()
        perfs = self.summary.performances if self.summary else []
        if self.proj_editor is not None and self.proj_editor.dirty and \
                self.proj_editor.holding.id != hid and confirm(
                    self, "Unsaved projections", f"Save the changes to {self.proj_editor.holding.name}'s "
                                                 "projections first?"):
            self.proj_editor.save()
        if hid is None:
            rows = list(pfm.portfolio_projections(self.pf, **self._filter()).values())
            c = self.summary.consolidated if self.summary else None
            fill_projection_table(self.proj_table, c, rows, self.summary.capex_req_share if c else None)
            self.proj_stack.setCurrentWidget(self.proj_table)
            self.proj_note.setText("Every active holding's projected years at our ownership share, added up, next to the "
                                   "last 12 months actual. Pick a company to enter its own: revenue & opex, capex, "
                                   "and financing & equity, all flowing into its summary.")
            return
        p = next((x for x in perfs if x.holding.id == hid), None)
        if p is None:
            return
        if self.proj_editor is not None:
            self.proj_stack.removeWidget(self.proj_editor)
            self.proj_editor.deleteLater()
        self.proj_editor = ProjectionEditor(self.pf, p.holding, p.financials if p.has_books else None)
        self.proj_editor.saved.connect(lambda: self.proj_note.setText(
            f"{p.holding.name} at 100% — saved. The portfolio total and the Dashboard use these projections."))
        self.proj_stack.addWidget(self.proj_editor)
        self.proj_stack.setCurrentWidget(self.proj_editor)
        self.proj_note.setText(f"{p.holding.name} at 100%. Enter Revenue & OpEx, CapEx and Financing & Equity by "
                               "year; the Summary shows them flowing together to the net cash flow after financing.")

    def _edit_projections(self) -> None:
        hid = self.proj_pick.currentData()
        perfs = self.summary.performances if self.summary else []
        if hid is None:
            error(self, "Pick a company", "Choose a company in the Company box at the top to enter its future years.")
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
        lay.setContentsMargins(0, 4, 0, 0)
        row = QHBoxLayout()
        # inside Rent Roll & Operations: the company is the one picked at the top of that page (kept in step)
        self.cv_pick = QComboBox()
        self.cv_pick.currentIndexChanged.connect(lambda *_: self._fill_parties())
        self.cv_pick.hide()
        row.addWidget(QLabel("Period:"))
        self.cv_period = QComboBox()
        for label, key in (("Last 12 months", "ltm"), ("Year to date", "ytd"), ("Last year", "last_year"),
                           ("All time", "all")):
            self.cv_period.addItem(label, key)
        self.cv_period.currentIndexChanged.connect(lambda *_: self._fill_parties())
        row.addWidget(self.cv_period)
        row.addStretch(1)
        open_b = QPushButton("Assign Names in the Books…")
        open_b.setToolTip("Opens the company's Transactions with no customer / vendor: select them and Set "
                          "Customer / Vendor… (or name them on invoices and bills), and they show here")
        open_b.clicked.connect(self._cv_open_books)
        row.addWidget(open_b)
        lay.addLayout(row)
        self.cv_info = QLabel()
        self.cv_info.setTextFormat(Qt.TextFormat.RichText)
        self.cv_info.setWordWrap(True)
        lay.addWidget(self.cv_info)
        self.cv_check = QLabel()                 # anything not tied to a customer or vendor
        self.cv_check.setTextFormat(Qt.TextFormat.RichText)
        self.cv_check.setWordWrap(True)
        lay.addWidget(self.cv_check)
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
            self.cv_check.setText("")
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
        from .theme import c as tc

        chk = self.cv_assign = pt.company_assignment_check(p.holding.company_path, end, start=b.start)
        if chk.error:
            self.cv_check.setText("")
        elif chk.ok:
            self.cv_check.setText(f"<b style='color:{tc('green')}'>✓ Every revenue and expense has a customer or "
                                  f"vendor, and everything owing is allocated.</b>")
        else:
            self.cv_check.setText(f"<b style='color:{tc('red')}'>⚠ Not assigned:</b> " + " · ".join(chk.lines())
                                  + " — Assign Names in the Books…")
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
                      self.dash_page.pick, self.folder_page.pick, self.proj_pick):
            i = combo.findData(self.co_pick.currentData())
            if i >= 0 and i != combo.currentIndex():
                combo.setCurrentIndex(i)
        if hasattr(self, "wf_for"):
            self._wf_follow()

    def _company_shown(self):
        """The holding the current company page shows (None on the portfolio / deal pipeline)."""
        w = self.tabs.currentWidget()
        if self.tabs.currentIndex() == 0 or w is getattr(self, "deals_page", None) or \
                w is getattr(self, "personal_page", None):
            return None
        pick = {getattr(self, "dash_page", None): getattr(getattr(self, "dash_page", None), "pick", None),
                getattr(self, "ops_page", None): getattr(getattr(self, "ops_page", None), "pick", None),
                getattr(self, "cash_page", None): getattr(getattr(self, "cash_page", None), "pick", None),
                getattr(self, "capex_page", None): getattr(getattr(self, "capex_page", None), "pick", None),
                getattr(self, "folder_page", None): getattr(getattr(self, "folder_page", None), "pick", None),
                getattr(self, "financing_page", None): getattr(self, "fin_pick", None)}.get(w, self.co_pick)
        hid = pick.currentData() if pick is not None else None
        return next((x.holding for x in (self.summary.performances if self.summary else []) if x.holding.id == hid),
                    None)

    def _update_attention(self, *_, force: bool = False) -> None:
        """The banner: rent roll, expenses and capex issues for the company shown, each a link to fix it."""
        import html as _html

        from .. import health

        h = self._company_shown()
        if h is None:
            self.attention.hide()
            return
        key = (h.id, self._as_of_date())
        if key == self._attention_for and not force:
            self.attention.setVisible(bool(self._attention_first))
            return
        self._attention_for = key
        issues = health.company_issues(self.pf, h, self._as_of_date())
        self._attention_first = issues[0].where if issues else ""
        if not issues:
            self.attention.hide()
            return
        parts = []
        for area in health.AREAS:
            mine = [i for i in issues if i.area == area]
            if mine:
                parts.append(f"<b>{area}:</b> " + " · ".join(
                    f"<a href='{i.where}' style='color:inherit'>{_html.escape(i.text)}</a>" for i in mine))
        from .theme import colors

        col = colors()
        self.attention.setStyleSheet(
            f"QFrame#attention {{ background: {col['attention_bg']}; border: 1px solid {col['attention_border']}; "
            f"border-radius: 8px; }} QFrame#attention QLabel {{ color: {col['attention_text']}; }}")
        self.attention_text.setText(f"Needs attention — {_html.escape(h.name)}: " + " &nbsp;|&nbsp; ".join(parts))
        self.attention.show()

    def _attention_go(self, where: str) -> None:
        """Open the place an issue is fixed."""
        h = self._company_shown()
        if where == "rent_roll":
            self.tabs.setCurrentWidget(self.ops_page)
            self.ops_page.sub.setCurrentIndex(1)
        elif where == "operating_costs":
            self.tabs.setCurrentWidget(self.ops_page)
            self.ops_page.sub.setCurrentIndex(2)
        elif where == "customers_vendors":
            self.tabs.setCurrentWidget(self.ops_page)
            self.ops_page.sub.setCurrentWidget(self.cv_page)
        elif where == "capex":
            self.tabs.setCurrentWidget(self.capex_page)
            self.capex_page.lower.setCurrentIndex(1)
        elif where == "transactions" and h is not None and h.company_path:
            self.open_review.emit(h.company_path)

    def _cv_follow_ops(self) -> None:
        i = self.cv_pick.findData(self.ops_page.pick.currentData())
        if i >= 0 and i != self.cv_pick.currentIndex():
            self.cv_pick.setCurrentIndex(i)

    def _cv_open_books(self) -> None:
        hid = self.cv_pick.currentData()
        p = next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)
        if p and p.holding.company_path:
            self.open_unnamed.emit(p.holding.company_path)

    def _follow_viewing(self) -> None:
        """Viewing one operating company: Company Overview, Customers & Vendors and Financing show it."""
        v = self.viewing.currentData()
        if not v or v[0] != "company" or not self.summary:
            return
        perf = next((p for p in self.summary.performances if pfm._same_file(p.holding.company_path, v[1])), None)
        if perf:
            for combo in (self.co_pick, self.cv_pick, self.fin_pick, self.ops_page.pick, self.cash_page.pick,
                          self.capex_page.pick, self.dash_page.pick, self.folder_page.pick, self.proj_pick):
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
        self._cv_follow_ops() if hasattr(self, "cv_page") else None
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
        for page in (self.dash_page, self.ops_page, self.cash_page, self.capex_page, self.folder_page):
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
            cv, debt, eq, _ = pfm.valuation_figures(self.pf, h, v)
            _set_row(self.co_vals, r, [v.date, fmt_money(cv) if cv is not None else "",
                                       fmt_money(debt) if debt is not None else "",
                                       fmt_money(eq), fmt_money(round(eq * h.ownership_pct / 100)),
                                       v.method, v.note], {1, 2, 3, 4})

    def _open_company_overview(self, index) -> None:
        col = index.column() - 1  # column 0 holds the row labels; one column per company
        if self.summary and 0 <= col < len(self.summary.performances):
            hid = self.summary.performances[col].holding.id
            for combo in (self.co_pick, self.dash_page.pick):
                combo.setCurrentIndex(combo.findData(hid))
            self.tabs.setCurrentWidget(self.dash_page)

    def _co_projections(self) -> None:
        p = self._company_perf()
        if p and ProjectionsDialog(self.pf, p.holding, p.financials if p.has_books else None, self).exec():
            self.refresh()

    # -- financing & cap table -------------------------------------------------

    def _build_financing_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        row = QHBoxLayout()
        self.fin_pick = QComboBox()        # hidden: follows the Company box at the very top of the window
        self.fin_pick.setMinimumWidth(260)
        self.fin_pick.currentIndexChanged.connect(lambda *_: self._fill_financing())
        self.fin_pick.hide()
        row.addWidget(self.fin_pick)
        self.fin_info = QLabel()
        self.fin_info.setTextFormat(Qt.TextFormat.RichText)
        row.addWidget(self.fin_info, 1)
        self.fin_check = QPushButton()          # the balance-sheet check at a glance; opens the full check
        self.fin_check.setFlat(True)
        self.fin_check.setCursor(Qt.CursorShape.PointingHandCursor)
        self.fin_check.setToolTip("Financing & Cap Table against the company's balance sheet — click for the details")
        self.fin_check.clicked.connect(lambda: self.fin_lower.setCurrentIndex(1))
        row.addWidget(self.fin_check)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        lrow = QHBoxLayout()
        lrow.addWidget(QLabel("<b>Loans</b> (balances from the company's books where linked; click a lender to select its loan, "
                              "then Edit Loan…)"))
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
        from PySide6.QtWidgets import QTextBrowser

        self.fin_loans = QTextBrowser()      # one clean page; click a lender to select its loan
        self.fin_loans.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.fin_loans.setLineWrapMode(QTextBrowser.LineWrapMode.NoWrap)
        self.fin_loans.setOpenLinks(False)
        self.fin_loans.anchorClicked.connect(lambda url: self._fin_select(int(url.toString().split(":")[1])))
        self._fin_rows: list[pfm.Loan] = []
        self._fin_sel: int | None = None
        self.fin_loan_cells: list[list[str]] = []
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
        self.fin_lower = QTabWidget()
        self.fin_lower.setDocumentMode(True)
        self.fin_lower.addTab(sw, "Amortization Table")
        self.fin_recon = QTextBrowser()      # the check against the balance sheet
        self.fin_recon.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.fin_lower.addTab(self.fin_recon, "Check Against the Balance Sheet")
        bottom.addWidget(self.fin_lower)
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
                           ("Delete", self._fin_delete_holder), ("Import Equity…", self._fin_import_equity),
                           ("Investor Reports…", self._fin_investor_reports)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            hrow.addWidget(b)
        cl.addLayout(hrow)
        self.fin_holders = _table(["Name", "Company", "Class", "Equity", "% owned", "Funded", "Funded on",
                                   "Value of stake", "Note"], 8)
        self.fin_holders.doubleClicked.connect(lambda *_: self._fin_edit_holder())
        self._fin_holder_rows: list[pfm.Shareholder] = []
        cl.addWidget(self.fin_holders, 1)
        self.fin_mine = QLabel()               # your ownership, from the owners linked to you (★)
        self.fin_mine.setWordWrap(True)
        self.fin_mine.setToolTip("★ marks the owners that are you or your holding company (Edit an owner to set it)")
        cl.addWidget(self.fin_mine)
        bottom.addWidget(cw)
        bottom.setSizes([520, 620])
        split.addWidget(bottom)
        split.setSizes([260, 460])
        lay.addWidget(split, 1)
        return w

    def _fill_fin_recon(self, p) -> None:
        """Debt on Financing vs the balance sheet's loans; the cap table's money paid in vs owners' money on it."""
        import html as _html

        from .. import fin_recon as fr
        from .theme import c as tc

        r = self.fin_recon_data = fr.reconcile(self.pf, p.holding, self._as_of_date())
        text, muted, red, green, accent = (self.palette().text().color().name(), tc("muted"), tc("red"), tc("green"),
                                           tc("link"))
        esc = _html.escape

        def m(c):
            if c is None:
                return f"<span style='color:{muted}'>—</span>"
            return f"<span style='color:{red}'>({fmt_money(-c)})</span>" if c < 0 else fmt_money(c)

        def table(rows, heads, total, gap_label, gap):
            out = [f"<table cellspacing='0' cellpadding='2' style='color:{text}'><tr><td></td>"
                   + "".join(f"<td align='right' style='color:{muted}; font-size:8pt; padding-left:18px'>{h}</td>"
                             for h in heads) + "<td></td></tr>"]
            for x in rows:
                flag = red if x.gap else muted
                out.append(f"<tr><td style='color:{text if x.gap else muted}; padding-right:18px'>{esc(x.name)}</td>"
                           f"<td align='right' style='padding-left:18px'>{m(x.financing)}</td>"
                           f"<td align='right' style='padding-left:18px'>{m(x.books)}</td>"
                           f"<td style='padding-left:18px; color:{flag}; font-size:8pt'>{esc(x.note)}</td></tr>")
            a, b = total
            out.append(f"<tr><td style='font-weight:700'>Total</td><td align='right' style='font-weight:700; "
                       f"padding-left:18px'>{m(a)}</td><td align='right' style='font-weight:700; padding-left:18px'>"
                       f"{m(b)}</td><td></td></tr></table>")
            status = (f"<p style='color:{green}; font-weight:700; margin:4px 0 0 0'>✓ {gap_label} matches the balance "
                      f"sheet</p>" if not gap else
                      f"<p style='color:{red}; font-weight:700; margin:4px 0 0 0'>{gap_label}: {fmt_money(abs(gap))} "
                      f"{'more on Financing & Cap Table' if gap > 0 else 'more on the balance sheet'}</p>")
            return "".join(out) + status

        def title(t, sub=""):
            return (f"<p style='margin:12px 0 4px 0; color:{accent}; font-size:8.5pt; font-weight:700'>{t}"
                    + (f" <span style='color:{muted}; font-weight:400'>{esc(sub)}</span>" if sub else "") + "</p>")

        if r.error and not r.debt:
            self.fin_recon.setHtml(f"<p>{esc(r.error)} — link the company to its books to check.</p>")
            self.fin_check.setText("")
            return
        html = title("DEBT", f"at {r.as_of}") + table(r.debt, ["Financing", "Balance sheet"],
                                                      (r.debt_financing, r.debt_books), "Debt", r.debt_gap)
        eq_rows = r.equity
        html += title("OWNERS' MONEY", "cap table paid in vs share capital and shareholder loans")
        if not r.cap_table_entered:
            html += (f"<p style='color:{muted}'>No owners on the cap table yet — Add Owner… or Import Equity… "
                     f"to check them against the balance sheet.</p>")
        html += table(eq_rows, ["Cap table", "Balance sheet"], (r.equity_cap_table, r.equity_books),
                      "Owners' money", r.equity_gap if r.cap_table_entered else 0)
        notes = []
        if r.opening_equity:
            notes.append(f"Opening Balance Equity {fmt_money(r.opening_equity)} — often owners' money not yet "
                         f"recorded as share capital or a shareholder loan")
        if r.draws:
            notes.append(f"Paid out to owners {fmt_money(r.draws)}")
        if r.retained:
            notes.append(f"Retained earnings and profit {m(r.retained)} (not money put in)")
        if notes:
            html += "".join(f"<p style='color:{muted}; font-size:8pt; margin:2px 0'>{x}</p>" for x in notes)
        self.fin_recon.setHtml(html)
        gaps = []
        if r.debt_gap:
            gaps.append(f"debt {fmt_money(r.debt_gap)}")
        if r.cap_table_entered and r.equity_gap:
            gaps.append(f"owners' money {fmt_money(r.equity_gap)}")
        if r.error:
            gaps.append(r.error.lower())
        self.fin_check.setText(("⚠ Gap to the balance sheet: " + " · ".join(gaps)) if gaps else
                               "✓ Matches the balance sheet")
        self.fin_check.setStyleSheet(f"QPushButton {{ color: {red if gaps else green}; font-weight: 700; "
                                     f"border: none; text-align: right; }}")

    def _fin_perf(self) -> pfm.Performance | None:
        hid = self.fin_pick.currentData()
        return next((x for x in (self.summary.performances if self.summary else []) if x.holding.id == hid), None)

    def _fill_financing(self) -> None:
        p = self._fin_perf()
        keep = self._fin_sel if self._fin_sel is not None else -1
        if p is None:
            self._fin_rows, self._fin_holder_rows = [], []
            self.fin_loans.setHtml("")
            for t in (self.fin_stack, self.fin_holders, self.fin_sched):
                t.setRowCount(0)
            self.fin_info.setText("<i>No holdings yet.</i>")
            return
        as_of = self._as_of_date()
        self._book_loans(p.holding, quiet=True)     # loans entered here are on the company's balance sheet
        self._fin_rows = pfm.loans(self.pf, p.holding, as_of)
        self.fin_loan_cells = loan_cells(self._fin_rows, as_of)
        tot = pfm.financing_totals(self._fin_rows)
        self.fin_info.setText(
            f"{len(self._fin_rows)} loan(s) · balance <b>{fmt_money(tot.balance)}</b> · debt service "
            f"<b>{fmt_money(tot.debt_service)}</b> a year"
            + (f" · LTV {p.ltv * 100:.0f}%" if p.ltv is not None else "")
            + (f" · next maturity {tot.next_maturity}" if tot.next_maturity else ""))
        try:
            self._fill_fin_recon(p)
        except Exception as e:  # noqa: BLE001 — the check never stops the page from showing
            self.fin_recon.setHtml(f"<p>Couldn't check against the books: {e}</p>")
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
        from .. import personal as pers

        saved = pers.load(self.pf)
        mine = pers.my_ownership(self.pf, p.holding, saved)
        self.fin_mine.setText(f"<b>Your ownership: {mine.pct:.2f}%</b> — {mine.how}"
                              + ("" if mine.from_cap_table else " (enter the owners to work it out from the cap "
                                                                 "table)")
                              + ("" if saved.holdco or saved.name else " · set your name and holding company under "
                                                                       "Personal → Your Name & Folders"))
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
                link = x.link or (pers.guess_link(x, saved) if not x.worked_out else "")
                mark = {"me": "★ ", "holdco": "★ "}.get(link, "")
                add([mark + x.name, x.company or "—", cls, fmt_money(x.amount_cents) if x.amount_cents else "—",
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
        self._fin_select(keep if 0 <= keep < len(self._fin_rows) else (0 if self._fin_rows else None))

    def _fin_select(self, r: int | None) -> None:
        """Select a loan (clicking its lender): shaded in the list, its amortization table below."""
        self._fin_sel = r
        self.fin_loans.setHtml(loans_html(self, self._fin_rows, self._as_of_date(), r))
        self._fill_fin_schedule()

    def _selected_loan(self) -> pfm.Loan | None:
        r = self._fin_sel
        return self._fin_rows[r] if r is not None and 0 <= r < len(self._fin_rows) else None

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
            acct, _ = ll.ensure_account(self.pf, books, ll.CompanyLoan(p.holding, ln))
            done = ll.book_loan(self.pf, books, ll.CompanyLoan(p.holding, ln))
            name = books.account(acct).name
        finally:
            books.close()
        info(self, "Linked to the books", f"{ln.lender} is linked to “{name}” in {p.holding.name}'s books."
             + (" It's on the balance sheet, matched to Assets Held (Journal Entries)." if done in ("posted", "updated")
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
            self._fin_select(loan_row)

    def _book_loans(self, holding, quiet: bool = False) -> None:
        """Loans on the company's balance sheet (the loan account against Assets Held)."""
        from .. import loan_link as ll

        try:
            ll.book_loans(self.pf, holding)
        except Exception as e:  # noqa: BLE001 — the books may be busy or read-only; the loan is saved
            if quiet:
                return
            error(self, "Not in the books yet", f"The loan is saved but couldn't be put in the books: {e}")

    def _fin_add_loan(self) -> None:
        p = self._fin_perf()
        if p and LoanDialog(self.pf, p.holding, parent=self).exec():
            self._book_loans(p.holding)
            self.refresh()

    def _fin_edit_loan(self) -> None:
        p, ln = self._fin_perf(), self._selected_loan()
        if p and ln and LoanDialog(self.pf, p.holding, ln, self).exec():
            self._book_loans(p.holding)
            self.refresh()

    def _fin_delete_loan(self) -> None:
        ln = self._selected_loan()
        if not ln:
            return
        if ln.id is None:
            error(self, "In the books", f"{ln.lender} is a debt account in the company's books. Edit it to add "
                                        "its terms; to remove it, change the books.")
            return
        if confirm(self, "Delete loan", f"Delete {ln.lender} from the portfolio? Its entry matching it to Assets Held "
                                        "comes out of the books; its account and payments stay."):
            from .. import loan_link as ll

            books = self._fin_books()
            if books is not None:
                try:
                    ll.unbook_loan(books, ln)
                finally:
                    books.close()
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

    def _fin_investor_reports(self) -> None:
        """Quarterly statements for the owners on the cap tables (this company picked to start)."""
        from .investor_reports_dialog import InvestorReportsDialog

        p = self._fin_perf()
        InvestorReportsDialog(self.pf, self, p.holding.id if p else None).exec()

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
            net = self._without_intercompany(rows, s.consolidated)
            if net is not None:
                cols.append(("Portfolio – without intercompany", net))
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

    def _without_intercompany(self, rows, total):
        """The portfolio total with balances owed between the group's companies taken out (see intercompany.py);
        None when there are none."""
        import dataclasses
        from pathlib import Path as _P

        from .. import intercompany as ic

        try:
            elim = ic.eliminations(ic.check(self._companies(), self._as_of_date()))
        except Exception:  # noqa: BLE001
            return None
        out = dataclasses.replace(total)
        hit = False
        for p in rows:
            e = elim.get(str(_P(p.holding.company_path).resolve())) if p.holding.company_path else None
            if e is None:
                continue
            share = p.holding.ownership_pct / 100
            out.total_assets -= round(e.assets * share)
            out.total_liabilities -= round(e.liabilities * share)
            out.debt -= round(e.debt * share)
            hit = hit or bool(e.assets or e.liabilities)
        out.equity = out.total_assets - out.total_liabilities
        return out if hit else None

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
            self.show_portfolio(self.holdings_page)
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
