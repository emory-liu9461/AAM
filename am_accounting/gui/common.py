from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QMessageBox, QWidget

from ..books import Account, Books

POSITIVE = QColor("#1b7f3b")
NEGATIVE = QColor("#b3261e")
MUTED = QColor("#6b7380")

TYPE_LABELS = {"asset": "Asset", "liability": "Liability", "equity": "Equity", "income": "Income", "expense": "Expense"}

STYLE = """
QMainWindow, QDialog { background: palette(window); }
QTabWidget::pane { border: none; }
QTabBar::tab { padding: 8px 18px; }
QPushButton { padding: 5px 12px; }
QPushButton#primary { background: #2f6fde; color: white; border: none; border-radius: 4px; padding: 6px 14px; font-weight: 600; }
QPushButton#primary:hover { background: #255fc4; }
QPushButton#primary:disabled { background: #9db8ea; }
QTableView { gridline-color: palette(midlight); }
QLabel#hint { color: palette(placeholder-text); }
QLabel#stat { font-size: 13pt; font-weight: 600; }
"""


def fill_account_combo(combo: QComboBox, books: Books, *, include_none: str | None = None,
                       exclude: set[int] | None = None, banks_only: bool = False) -> None:
    current = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    if include_none is not None:
        combo.addItem(include_none, None)
    accounts = [(a, 0) for a in books.bank_accounts()] if banks_only else _tree_order(books.accounts(active_only=True))
    last_type = None
    for a, depth in accounts:
        if exclude and a.id in exclude:
            continue
        if not banks_only and a.type != last_type:
            combo.addItem(f"— {TYPE_LABELS[a.type]} —", "__sep__")
            idx = combo.count() - 1
            item = combo.model().item(idx)
            item.setEnabled(False)
            last_type = a.type
        combo.addItem("      " * depth + a.label, a.id)
    i = combo.findData(current)
    combo.setCurrentIndex(i if i >= 0 else (0 if include_none is not None or banks_only else 1))
    combo.blockSignals(False)


def _tree_order(accounts: list[Account]) -> list[tuple[Account, int]]:
    """Accounts grouped by type, with sub-accounts directly under their parent: [(account, depth)]."""
    ids = {a.id for a in accounts}
    kids: dict[int | None, list[Account]] = {}
    for a in accounts:
        kids.setdefault(a.parent_id if a.parent_id in ids else None, []).append(a)
    out: list[tuple[Account, int]] = []

    def walk(a: Account, depth: int) -> None:
        out.append((a, depth))
        for c in kids.get(a.id, []):
            walk(c, depth + 1)

    from ..chart_of_accounts import ACCOUNT_TYPES

    for t in ACCOUNT_TYPES:
        for a in kids.get(None, []):
            if a.type == t:
                walk(a, 0)
    return out


def account_label(books: Books, account_id: int | None) -> str:
    if account_id is None:
        return ""
    a: Account = books.account(account_id)
    return a.label


def error(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


def info(parent: QWidget, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def confirm(parent: QWidget, title: str, message: str) -> bool:
    return QMessageBox.question(parent, title, message) == QMessageBox.StandardButton.Yes


ALIGN_RIGHT = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
