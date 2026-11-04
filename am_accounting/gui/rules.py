from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from ..books import Books, Rule
from ..categorizer import Categorizer, rule_matches
from .common import account_label, confirm, error, fill_account_combo

MATCH_LABELS = {"contains": "Contains", "exact": "Is exactly", "regex": "Matches regex"}
DIR_LABELS = {"any": "Money in or out", "in": "Money in only", "out": "Money out only"}


class RuleDialog(QDialog):
    def __init__(self, books: Books, rule: Rule | None = None, parent=None, *, pattern: str = ""):
        super().__init__(parent)
        self.books = books
        self.rule = rule
        self.setWindowTitle("Edit Rule" if rule else "New Rule")
        self.resize(480, 0)
        form = QFormLayout(self)
        self.match = QComboBox()
        for k, v in MATCH_LABELS.items():
            self.match.addItem(v, k)
        self.pattern = QLineEdit(pattern)
        self.direction = QComboBox()
        for k, v in DIR_LABELS.items():
            self.direction.addItem(v, k)
        self.account = QComboBox()
        fill_account_combo(self.account, books)
        self.priority = QSpinBox()
        self.priority.setRange(1, 9999)
        self.priority.setValue(100)
        self.preview = QLabel()
        self.preview.setObjectName("hint")
        form.addRow("When description", self.match)
        form.addRow("", self.pattern)
        form.addRow("Applies to", self.direction)
        form.addRow("Categorize as", self.account)
        form.addRow("Priority (lower runs first)", self.priority)
        form.addRow(self.preview)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        if rule:
            self.match.setCurrentIndex(self.match.findData(rule.match_type))
            self.pattern.setText(rule.pattern)
            self.direction.setCurrentIndex(self.direction.findData(rule.direction))
            self.account.setCurrentIndex(self.account.findData(rule.account_id))
            self.priority.setValue(rule.priority)
        for w in (self.pattern.textChanged, self.match.currentIndexChanged, self.direction.currentIndexChanged):
            w.connect(self.update_preview)
        self.update_preview()

    def _draft(self) -> Rule:
        return Rule(0, self.pattern.text(), self.match.currentData(), self.account.currentData() or 0,
                    self.direction.currentData(), self.priority.value())

    def update_preview(self) -> None:
        if not self.pattern.text().strip():
            self.preview.setText("")
            return
        draft = self._draft()
        n = sum(1 for t in self.books.transactions() if rule_matches(draft, t.description, t.amount_cents))
        self.preview.setText(f"Matches {n} existing transaction(s).")

    def save(self) -> None:
        d = self._draft()
        if not d.pattern.strip() or self.account.currentData() in (None, "__sep__"):
            error(self, "Incomplete rule", "Enter the text to match and choose a category.")
            return
        try:
            if d.match_type == "regex":
                re.compile(d.pattern)
            if self.rule:
                self.books.update_rule(self.rule.id, d.pattern, d.account_id, match_type=d.match_type,
                                       direction=d.direction, priority=d.priority)
            else:
                self.books.add_rule(d.pattern, d.account_id, match_type=d.match_type, direction=d.direction,
                                    priority=d.priority)
        except re.error as e:
            error(self, "Invalid regular expression", str(e))
            return
        self.accept()


class RulesTab(QWidget):
    changed = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        layout = QVBoxLayout(self)
        intro = QLabel("Rules categorize transactions automatically and take priority over learned and suggested "
                       "categories. Transactions you categorized by hand are never changed.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        layout.addWidget(intro)
        bar = QHBoxLayout()
        bar.addStretch()
        for label, slot in [("New Rule…", self.new_rule), ("Edit…", self.edit_rule), ("Delete", self.delete_rule),
                            ("Run Rules Now", self.run_rules)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        layout.addLayout(bar)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Priority", "When description", "Text", "Applies to", "Categorize as"])
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.doubleClicked.connect(self.edit_rule)
        layout.addWidget(self.table)
        self.refresh()

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def refresh(self) -> None:
        rules = self.books.rules()
        self.table.setRowCount(len(rules))
        for r, rule in enumerate(rules):
            vals = [str(rule.priority), MATCH_LABELS[rule.match_type], rule.pattern, DIR_LABELS[rule.direction],
                    account_label(self.books, rule.account_id)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, rule.id)
                self.table.setItem(r, c, it)
        self.table.setColumnWidth(0, 70)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(3, 130)
        self.table.setColumnWidth(4, 240)

    def _selected(self) -> Rule | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        rid = self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return next((r for r in self.books.rules() if r.id == rid), None)

    def new_rule(self, pattern: str = "") -> None:
        if RuleDialog(self.books, parent=self, pattern=pattern if isinstance(pattern, str) else "").exec():
            self.refresh()
            self.run_rules()

    def edit_rule(self) -> None:
        rule = self._selected()
        if rule and RuleDialog(self.books, rule, parent=self).exec():
            self.refresh()
            self.run_rules()

    def delete_rule(self) -> None:
        rule = self._selected()
        if rule and confirm(self, "Delete rule", f"Delete the rule for “{rule.pattern}”?"):
            self.books.delete_rule(rule.id)
            self.refresh()

    def run_rules(self) -> None:
        stats = Categorizer(self.books).run(overwrite_auto=True)
        self.changed.emit()
        self.window().statusBar().showMessage(f"Rules applied to {stats.get('rule', 0)} transaction(s).", 8000)
