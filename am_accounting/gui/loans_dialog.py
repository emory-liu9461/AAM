"""Accounting side of the loans: find and record loan payments (principal + interest) and advances."""

from __future__ import annotations

import html

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QHBoxLayout, QHeaderView, QLabel, QPushButton, QRadioButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from .. import loan_link as ll
from .. import portfolio as pfm
from ..books import Books
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, error, info


def _open_portfolio() -> pfm.Portfolio | None:
    path = ll.portfolio_path()
    return pfm.Portfolio(path) if path else None


class LoanPaymentsDialog(QDialog):
    """This company's loans (from Asset Management) and the bank lines that look like their payments."""

    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.changed = False
        self.setWindowTitle(f"Loans – {books.company_name}")
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        intro = QLabel("Loans entered in Asset Management → Financing & Cap Table. Each loan payment is split: the "
                       "<b>principal</b> lowers the loan's balance and the <b>interest</b> goes to Interest Expense "
                       "(the balance owed × the rate for one payment period). The advance goes to the loan account.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        intro.setObjectName("hint")
        lay.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("<b>Loans</b>"))
        row.addStretch(1)
        self.create_btn = QPushButton("Create Account in the Books")
        self.create_btn.setToolTip("Adds a liability account for the selected loan (with an opening balance when the "
                                   "loan is older than these books) and links it to the loan")
        self.create_btn.clicked.connect(self.create_account)
        row.addWidget(self.create_btn)
        lay.addLayout(row)
        self.loans = QTableWidget(0, 7)
        self.loans.setHorizontalHeaderLabels(["Lender", "Facility", "Type", "Payment", "Account in the books",
                                              "Balance (books)", "Recorded"])
        self.loans.verticalHeader().setVisible(False)
        self.loans.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.loans.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.loans.horizontalHeader().setStretchLastSection(True)
        self.loans.setMaximumHeight(170)
        lay.addWidget(self.loans)
        row2 = QHBoxLayout()
        self.found_label = QLabel("<b>Found in the bank lines</b>")
        row2.addWidget(self.found_label)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.found = QTableWidget(0, 8)
        self.found.setHorizontalHeaderLabels(["Record", "Date", "Description", "Amount", "Loan", "As", "Principal",
                                              "Interest"])
        self.found.verticalHeader().setVisible(False)
        self.found.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.found.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.found.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.found, 1)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        bottom = QHBoxLayout()
        rec = QPushButton("Record Ticked")
        rec.setObjectName("primary")
        rec.clicked.connect(self.record)
        bottom.addWidget(rec)
        bottom.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        lay.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        pf = _open_portfolio()
        try:
            self.cls = ll.company_loans(self.books, pf) if pf else []
            self.sugg = ll.suggestions(self.books, self.cls) if pf else []
        finally:
            if pf:
                pf.close()
        accts = {a.id: a for a in self.books.accounts()}
        self.loans.setRowCount(len(self.cls))
        for r, cl in enumerate(self.cls):
            ln = cl.loan
            linked = ln.account_id in accts
            done = ll.recorded(self.books, ln.id) if ln.id else []
            vals = [ln.lender, ln.kind, ln.repayment,
                    f"{fmt_money(ln.payment)} {ln.payment_freq.lower()}" if ln.payment else "—",
                    accts[ln.account_id].name if linked else "— not in the books yet",
                    fmt_money(-self.books.ledger_totals().get(ln.account_id, 0)) if linked else "—",
                    f"{len(done)} payment(s)" + (f", last {done[-1][0].date}" if done else "")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (3, 5):
                    it.setTextAlignment(ALIGN_RIGHT)
                if c == 4 and not linked:
                    it.setForeground(MUTED)
                self.loans.setItem(r, c, it)
        self.loans.resizeColumnsToContents()
        if self.cls and self.loans.currentRow() < 0:
            self.loans.selectRow(0)
        self.found.setRowCount(len(self.sugg))
        for r, sg in enumerate(self.sugg):
            linked = sg.cl.loan.account_id in accts
            tick = QTableWidgetItem("")
            tick.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            tick.setCheckState(Qt.CheckState.Checked if linked else Qt.CheckState.Unchecked)
            tick.setToolTip(sg.why)
            self.found.setItem(r, 0, tick)
            vals = [sg.tx.date, sg.tx.description, fmt_money(sg.tx.amount_cents), sg.cl.loan.lender,
                    "Payment" if sg.kind == "payment" else "Loan advance",
                    fmt_money(sg.principal), fmt_money(sg.interest) if sg.kind == "payment" else "—"]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                if c in (3, 6, 7):
                    it.setTextAlignment(ALIGN_RIGHT)
                it.setToolTip(sg.why)
                self.found.setItem(r, c, it)
        self.found.resizeColumnsToContents()
        self.found.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.found_label.setText(f"<b>Found in the bank lines</b> ({len(self.sugg)})")
        if not self.cls:
            self.note.setText("No loans for this company yet — add them in Asset Management → Financing & Cap Table. "
                              "(The company has to be in the portfolio.)")
        elif any(c.loan.account_id not in accts for c in self.cls):
            self.note.setText("Select a loan without an account and click Create Account in the Books before "
                              "recording its payments. Any bank line can also be recorded by hand: double-click it "
                              "in Transactions → Loan Payment….")
        else:
            self.note.setText("Untick anything that isn't a loan payment. Any other bank line can be recorded by "
                              "hand: double-click it in Transactions → Loan Payment….")

    def create_account(self) -> None:
        r = self.loans.currentRow()
        if not 0 <= r < len(self.cls):
            return
        pf = _open_portfolio()
        if not pf:
            return
        try:
            acct, _ = ll.ensure_account(pf, self.books, self.cls[r])
            done = ll.book_loan(pf, self.books, self.cls[r])
        except ValueError as e:
            error(self, "Couldn't create the account", str(e))
            return
        finally:
            pf.close()
        self.changed = True
        msg = f"“{self.books.account(acct).name}” is in the chart of accounts and linked to the loan."
        if done in ("posted", "updated"):
            msg += ("\n\nIt's on the balance sheet, matched to Assets Held (see Journal Entries) — record the advance "
                    "from the bank instead if the money came into the bank account.")
        info(self, "Account created", msg)
        self.refresh()

    def record(self) -> None:
        n, problems = 0, []
        ticked = [sg for r, sg in enumerate(self.sugg) if self.found.item(r, 0).checkState() == Qt.CheckState.Checked]
        for sg in sorted(ticked, key=lambda x: x.tx.date):  # oldest first: each uses the balance the last one left
            try:
                if sg.kind == "payment":
                    p, i = ll.split_payment(self.books, sg.cl.loan, sg.tx.date, abs(sg.tx.amount_cents))
                    ll.record_payment(self.books, sg.tx.id, sg.cl, p, i)
                else:
                    ll.record_proceeds(self.books, sg.tx.id, sg.cl)
                n += 1
            except ValueError as e:
                problems.append(f"{sg.tx.date} {sg.tx.description}: {e}")
        if n:
            self.changed = True
        if problems:
            error(self, "Some weren't recorded", "\n".join(problems[:10]))
        self.refresh()
        if n:
            self.note.setText(f"✓ Recorded {n}. " + self.note.text())


class LoanSplitDialog(QDialog):
    """Record one bank line as a loan payment (principal + interest) or a loan advance."""

    def __init__(self, books: Books, tx_id: int, parent=None):
        super().__init__(parent)
        self.books = books
        self.tx = tx = books.transaction(tx_id)
        pf = _open_portfolio()
        try:
            self.cls = ll.company_loans(books, pf) if pf else []
        finally:
            if pf:
                pf.close()
        self.setWindowTitle("Loan Payment")
        self.resize(520, 0)
        form = QFormLayout(self)
        head = QLabel(f"<b>{html.escape(tx.description)}</b><br>{tx.date} · {fmt_money(tx.amount_cents)}")
        head.setTextFormat(Qt.TextFormat.RichText)
        form.addRow(head)
        self.loan = QComboBox()
        for i, cl in enumerate(self.cls):
            self.loan.addItem(f"{cl.loan.lender} – {cl.loan.kind}", i)
        form.addRow("Loan:", self.loan)
        kinds = QHBoxLayout()
        self.as_payment = QRadioButton("Payment (principal + interest)")
        self.as_advance = QRadioButton("Loan advance (money received)")
        (self.as_advance if tx.amount_cents > 0 else self.as_payment).setChecked(True)
        kinds.addWidget(self.as_payment)
        kinds.addWidget(self.as_advance)
        form.addRow("", kinds)
        self.principal = QDoubleSpinBox()
        self.interest = QDoubleSpinBox()
        for s in (self.principal, self.interest):
            s.setRange(0, abs(tx.amount_cents) / 100)
            s.setDecimals(2)
            s.setPrefix("$ ")
            s.setGroupSeparatorShown(True)
        form.addRow("Principal:", self.principal)
        form.addRow("Interest:", self.interest)
        self.status = QLabel()
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        form.addRow(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.loan.currentIndexChanged.connect(self._suggest)
        self.as_payment.toggled.connect(self._suggest)
        self.interest.valueChanged.connect(self._interest_changed)
        if not self.cls:
            self.status.setText("This company has no loans yet — add them in Asset Management → Financing & Cap "
                                "Table.")
        self._suggest()

    def _cl(self):
        i = self.loan.currentData()
        return self.cls[i] if isinstance(i, int) and 0 <= i < len(self.cls) else None

    def _suggest(self, *_) -> None:
        cl = self._cl()
        amt = abs(self.tx.amount_cents)
        pay = self.as_payment.isChecked()
        self.interest.setEnabled(pay)
        self.principal.setEnabled(False)
        if cl is None:
            return
        if pay and cl.loan.account_id is not None:
            p, i = ll.split_payment(self.books, cl.loan, self.tx.date, amt)
        else:
            p, i = amt, 0
        self.interest.blockSignals(True)
        self.interest.setValue(i / 100)
        self.interest.blockSignals(False)
        self.principal.setValue(p / 100)
        if cl.loan.account_id is None:
            self.status.setText("This loan has no account in the books yet: Transactions → Loans… → Create Account "
                                "in the Books.")
        else:
            self.status.setText("Interest is worked out from the balance owed before this payment; change it to "
                                "match the lender's statement if needed." if pay else "")

    def _interest_changed(self, v: float) -> None:
        self.principal.setValue(abs(self.tx.amount_cents) / 100 - v)

    def save(self) -> None:
        cl = self._cl()
        if cl is None:
            self.reject()
            return
        try:
            if self.as_payment.isChecked():
                i = round(self.interest.value() * 100)
                ll.record_payment(self.books, self.tx.id, cl, abs(self.tx.amount_cents) - i, i)
            else:
                ll.record_proceeds(self.books, self.tx.id, cl)
        except ValueError as e:
            error(self, "Couldn't record", str(e))
            return
        self.accept()
