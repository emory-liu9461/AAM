"""Picker for the Accounts Receivable / Payable account an invoice or bill is recorded in."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QInputDialog, QPushButton, QWidget

from .. import receivables as rx
from ..books import Books
from .common import error


class ControlAccountPicker(QWidget):
    """Receivable (invoices) or payable (bills) account, with “New…” to add a sub-account such as
    “Accounts Receivable – Related Party”. Follows the customer's / vendor's usual account until changed."""

    def __init__(self, books: Books, kind: str, current: int | None = None, parent=None):
        super().__init__(parent)
        self.books, self.kind = books, kind
        self.touched = current is not None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setToolTip("Which receivable / payable account this is owed in — e.g. a separate account for "
                              "a related company, a shareholder or a credit card")
        self.combo.activated.connect(lambda *_: setattr(self, "touched", True))
        new = QPushButton("New…")
        new.setToolTip("Add a sub-account under Accounts Receivable / Payable")
        new.clicked.connect(self.add_account)
        lay.addWidget(self.combo, 1)
        lay.addWidget(new)
        self._fill(current or rx.control_account(books, kind).id)

    def _fill(self, select: int) -> None:
        self.combo.clear()
        main = rx.control_account(self.books, self.kind)
        for a in rx.control_choices(self.books, self.kind):
            indent = "    " if a.parent_id == main.id else ""
            self.combo.addItem(f"{indent}{a.code} · {a.name}", a.id)
        i = self.combo.findData(select)
        self.combo.setCurrentIndex(max(0, i))

    def value(self) -> int:
        return self.combo.currentData()

    def set_value(self, account_id: int) -> None:
        i = self.combo.findData(account_id)
        if i >= 0:
            self.combo.setCurrentIndex(i)

    def follow_party(self, party: str) -> None:
        """Use this customer's / vendor's usual account, unless the user already picked one."""
        if not self.touched:
            self.set_value(rx.default_control(self.books, self.kind, party))

    def add_account(self) -> None:
        main = rx.control_account(self.books, self.kind)
        name, ok = QInputDialog.getText(self, "New account", f"Name of the new account under {main.name}:",
                                        text=f"{main.name} – ")
        if not ok or not name.strip() or name.strip() == f"{main.name} –":
            return
        try:
            acct = rx.add_control_account(self.books, self.kind, name)
        except ValueError as e:
            error(self, "Couldn't add the account", str(e))
            return
        self.touched = True
        self._fill(acct.id)
