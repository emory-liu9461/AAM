"""A deal's Rent Roll & Expenses tab: the seller's rent roll unit by unit, and the operating expenses one by one.
They feed the deal's summary (and become the property's rent roll and operating costs when the deal closes)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QPushButton, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import deals as dl
from ..money import fmt_money
from .common import ALIGN_RIGHT, error

ROLL_COLS = ["Unit", "Type", "Tenant", "Status", "Rent / month", "Market / month"]
EXP_COLS = ["Expense", "Per year", "Per unit / year"]


def _cents(text: str) -> int:
    t = (text or "").replace("$", "").replace(",", "").strip()
    if not t:
        return 0
    return round(float(t.strip("()")) * 100) * (-1 if t.startswith("(") else 1)


def _money_item(cents: int) -> QTableWidgetItem:
    it = QTableWidgetItem(f"{cents / 100:,.2f}" if cents else "")
    it.setTextAlignment(ALIGN_RIGHT)
    return it


class RollExpensesTab(QWidget):
    def __init__(self, u: dl.Underwriting, on_change=None, parent=None):
        super().__init__(parent)
        self.on_change = on_change or (lambda: None)
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        # the rent roll
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 6, 0)
        ll.addWidget(QLabel("<b>Rent roll</b> — each unit's rent and market rent a month"))
        self.roll = QTableWidget(0, len(ROLL_COLS))
        self.roll.setHorizontalHeaderLabels(ROLL_COLS)
        self.roll.verticalHeader().setVisible(False)
        self.roll.horizontalHeader().setStretchLastSection(True)
        self.roll.itemChanged.connect(self._changed)
        ll.addWidget(self.roll, 1)
        rb = QHBoxLayout()
        for text, slot, tip in (("Add Unit", self.add_unit, "Another unit"),
                                ("Add Units…", self.add_units, "Several units at once: 101, 102 …"),
                                ("Remove", self.remove_unit, "Remove the selected unit(s)"),
                                ("Import Rent Roll…", self.import_roll,
                                 "Read the seller's rent roll from Excel or CSV (Unit, Type, Status, Rent, Market Rent …)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            rb.addWidget(b)
        rb.addStretch(1)
        ll.addLayout(rb)
        self.roll_total = QLabel()
        self.roll_total.setTextFormat(Qt.TextFormat.RichText)
        ll.addWidget(self.roll_total)
        split.addWidget(left)
        # operating expenses
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(6, 0, 0, 0)
        rl.addWidget(QLabel("<b>Operating expenses</b> — each one a year"))
        self.exp = QTableWidget(0, len(EXP_COLS))
        self.exp.setHorizontalHeaderLabels(EXP_COLS)
        self.exp.verticalHeader().setVisible(False)
        self.exp.setColumnWidth(0, 220)
        self.exp.horizontalHeader().setStretchLastSection(True)
        self.exp.itemChanged.connect(self._changed)
        rl.addWidget(self.exp, 1)
        eb = QHBoxLayout()
        for text, slot, tip in (("Add Expense", lambda: self.add_expense(), "Another expense line"),
                                ("Remove", self.remove_expense, "Remove the selected expense(s)"),
                                ("Add the Usual", self.usual,
                                 "Property tax, insurance, utilities, repairs, management, wages …")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            eb.addWidget(b)
        eb.addStretch(1)
        rl.addLayout(eb)
        self.exp_total = QLabel()
        self.exp_total.setTextFormat(Qt.TextFormat.RichText)
        rl.addWidget(self.exp_total)
        split.addWidget(right)
        split.setSizes([560, 440])
        lay.addWidget(split, 1)
        hint = QLabel("Entered here, the rent roll replaces the average rents on the Summary (and sets the number of "
                      "units); the expenses' total replaces the operating expenses there. A vacant unit counts at its "
                      "market rent in the rent it could bring in (vacancy is the allowance on the Summary). When the "
                      "deal closes they become the property's rent roll and operating costs.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self._loading = True
        for x in u.rent_roll:
            self._roll_row(x)
        for x in u.expenses:
            self.add_expense(x.get("item", ""), int(x.get("amount") or 0))
        self._loading = False
        self._totals()

    # ---- rent roll ----
    def _roll_row(self, x: dict) -> None:
        t = self.roll
        t.blockSignals(True)
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(str(x.get("unit", ""))))
        t.setItem(r, 1, QTableWidgetItem(x.get("type", "")))
        t.setItem(r, 2, QTableWidgetItem(x.get("tenant", "")))
        st = QComboBox()
        st.setEditable(True)
        st.addItems(dl.UNIT_STATUSES)
        st.setCurrentText(x.get("status") or "Occupied")
        st.currentTextChanged.connect(lambda *_: self._changed())
        t.setCellWidget(r, 3, st)
        t.setItem(r, 4, _money_item(int(x.get("rent") or 0)))
        t.setItem(r, 5, _money_item(int(x.get("market") or 0)))
        t.blockSignals(False)

    def add_unit(self, *_, unit: str = "") -> None:
        if not unit and self.roll.rowCount():
            last = self.roll.item(self.roll.rowCount() - 1, 0).text().strip()
            unit = str(int(last) + 1) if last.isdigit() else ""
        prev = self.rent_roll()[-1] if self.roll.rowCount() else {}
        self._roll_row({"unit": unit, "type": prev.get("type", ""), "status": "Occupied", "rent": 0,
                        "market": prev.get("market", 0)})
        self._changed()

    def add_units(self) -> None:
        text, ok = QInputDialog.getText(self, "Add units", "Unit numbers (e.g. 101-110, 201-210, PH1):")
        if not ok:
            return
        for u in expand_units(text):
            self._roll_row({"unit": u, "status": "Occupied"})
        self._changed()

    def remove_unit(self) -> None:
        for r in sorted({i.row() for i in self.roll.selectedIndexes()}, reverse=True):
            self.roll.removeRow(r)
        self._changed()

    def import_roll(self, path: str | None = None) -> int:
        from .. import operations as op

        path = path or QFileDialog.getOpenFileName(self, "Import the rent roll", "",
                                                   "Rent roll (*.xlsx *.xlsm *.csv)")[0]
        if not path:
            return 0
        try:
            units, _note = op.read_rent_roll(path)
        except (OSError, ValueError) as e:
            error(self, "Couldn't read the rent roll", str(e))
            return 0
        self.roll.setRowCount(0)
        for x in dl.roll_from_units(units):
            self._roll_row(x)
        self._changed()
        return len(units)

    def rent_roll(self) -> list[dict]:
        t = self.roll
        out = []
        for r in range(t.rowCount()):
            unit = t.item(r, 0).text().strip() if t.item(r, 0) else ""
            if not unit:
                continue
            out.append({"unit": unit, "type": t.item(r, 1).text().strip() if t.item(r, 1) else "",
                        "tenant": t.item(r, 2).text().strip() if t.item(r, 2) else "",
                        "status": t.cellWidget(r, 3).currentText(),
                        "rent": self._money(t, r, 4), "market": self._money(t, r, 5)})
        return out

    # ---- expenses ----
    def add_expense(self, item: str = "", amount: int = 0) -> None:
        t = self.exp
        t.blockSignals(True)
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(item))
        t.setItem(r, 1, _money_item(amount))
        per = QTableWidgetItem("")
        per.setFlags(per.flags() & ~Qt.ItemFlag.ItemIsEditable)
        per.setTextAlignment(ALIGN_RIGHT)
        t.setItem(r, 2, per)
        t.blockSignals(False)
        if not self._loading:
            self._changed()

    def remove_expense(self) -> None:
        for r in sorted({i.row() for i in self.exp.selectedIndexes()}, reverse=True):
            self.exp.removeRow(r)
        self._changed()

    def usual(self) -> None:
        have = {x["item"].lower() for x in self.expenses()}
        self._loading = True
        for item in dl.EXPENSE_STARTERS:
            if item.lower() not in have:
                self.add_expense(item)
        self._loading = False
        self._changed()

    def expenses(self) -> list[dict]:
        t = self.exp
        out = []
        for r in range(t.rowCount()):
            item = t.item(r, 0).text().strip() if t.item(r, 0) else ""
            if item:
                out.append({"item": item, "amount": self._money(t, r, 1)})
        return out

    # ---- both ----
    @staticmethod
    def _money(t: QTableWidget, r: int, c: int) -> int:
        it = t.item(r, c)
        try:
            return max(0, _cents(it.text())) if it else 0
        except ValueError:
            return 0

    def _changed(self, *_) -> None:
        if self._loading:
            return
        self._totals()
        self.on_change()

    def _totals(self) -> None:
        u = dl.Underwriting(rent_roll=self.rent_roll(), expenses=self.expenses())
        n = u.roll_units
        self.roll_total.setText(
            f"<b>{n}</b> unit(s) · {n - u.roll_vacant} occupied · in place <b>{fmt_money(u.roll_occupied_rent)}</b> a "
            f"month · at market <b>{fmt_money(u.roll_market)}</b> a month"
            + (f" · average {fmt_money(round(u.roll_occupied_rent / (n - u.roll_vacant)))} in place"
               if n - u.roll_vacant else "") if n else "No units entered — the Summary uses its average rents.")
        total = u.expenses_total
        self.exp_total.setText(f"Total <b>{fmt_money(total)}</b> a year" + (f" · {fmt_money(round(total / n))} a unit"
                                                                             if n else "")
                               if u.expenses else "No expenses entered — the Summary uses its operating expenses.")
        self.exp.blockSignals(True)
        for r in range(self.exp.rowCount()):
            amt = self._money(self.exp, r, 1)
            self.exp.item(r, 2).setText(fmt_money(round(amt / n)) if n and amt else "")
        self.exp.blockSignals(False)


def expand_units(text: str) -> list[str]:
    """"101-104, 201, PH1" → ["101", "102", "103", "104", "201", "PH1"]."""
    out = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            a, b = a.strip(), b.strip()
            if a.isdigit() and b.isdigit() and int(a) <= int(b) and int(b) - int(a) < 2000:
                out += [str(i) for i in range(int(a), int(b) + 1)]
                continue
        out.append(part)
    return out
