"""Contacts (beside Personal Finance): contractors, investors, lenders and everyone else you work with (see
directory.py)."""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import directory as dr
from .common import confirm, error, info

COLS = ["Name", "Company", "Kind", "Phone", "Email", "Works on", "Notes"]


class PersonDialog(QDialog):
    def __init__(self, pf, person: dr.Person | None = None, parent=None):
        super().__init__(parent)
        self.pf = pf
        self.person = person or dr.Person("")
        p = self.person
        self.setWindowTitle("Contact" if p.id else "New Contact")
        self.resize(560, 0)
        form = QFormLayout(self)
        self.name = QLineEdit(p.name)
        self.name.setPlaceholderText("e.g. Sam Roe")
        form.addRow("Name:", self.name)
        self.company = QLineEdit(p.company)
        self.company.setPlaceholderText("e.g. Roe Renovations Inc.")
        form.addRow("Company:", self.company)
        self.kind = QComboBox()
        self.kind.setEditable(True)
        self.kind.addItems(dr.KINDS)
        self.kind.setCurrentText(p.kind or "Other")
        form.addRow("Kind:", self.kind)
        self.phone = QLineEdit(p.phone)
        form.addRow("Phone:", self.phone)
        self.email = QLineEdit(p.email)
        form.addRow("Email:", self.email)
        self.address = QLineEdit(p.address)
        form.addRow("Address:", self.address)
        self.website = QLineEdit(p.website)
        form.addRow("Website:", self.website)
        self.places = QListWidget()
        self.places.setMaximumHeight(130)
        for name in list(dict.fromkeys(dr.places(pf) + p.works_on)):
            it = QListWidgetItem(name)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if name in p.works_on else Qt.CheckState.Unchecked)
            self.places.addItem(it)
        form.addRow("Works on:", self.places)
        self.notes = QPlainTextEdit(p.notes)
        self.notes.setMaximumHeight(90)
        self.notes.setPlaceholderText("Rates, what they did, how they were to work with …")
        form.addRow("Notes:", self.notes)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> bool:
        p = self.person
        p.name, p.company, p.kind = self.name.text(), self.company.text(), self.kind.currentText().strip() or "Other"
        p.phone, p.email, p.address = self.phone.text(), self.email.text(), self.address.text()
        p.website, p.notes = self.website.text(), self.notes.toPlainText()
        p.works_on = [self.places.item(i).text() for i in range(self.places.count())
                      if self.places.item(i).checkState() == Qt.CheckState.Checked]
        try:
            dr.save(self.pf, p)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return False
        self.accept()
        return True


class DirectoryPage(QWidget):
    """Everyone you work with: search, filter by kind, add and change, email."""

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        self.pf = pf
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Contacts")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        title.setFont(f)
        head.addWidget(title)
        self.count = QLabel()
        self.count.setObjectName("hint")
        head.addWidget(self.count)
        head.addStretch(1)
        lay.addLayout(head)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, company, phone, email, property …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.search, 1)
        bar.addWidget(QLabel("Kind:"))
        self.kind = QComboBox()
        self.kind.currentIndexChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.kind)
        for text, slot, tip in (("New Contact…", self.new, "Add someone"),
                                ("Edit…", self.edit, "Change the selected contact"),
                                ("Delete", self.delete, "Remove the selected contact"),
                                ("Email", self.email, "Write to the selected contact (your email program)"),
                                ("Add from the Portfolio", self.from_portfolio,
                                 "Add the investors on the cap tables and the lenders on the loans")):
            b = QPushButton(text)
            b.setToolTip(tip)
            if text.startswith("New"):
                b.setObjectName("primary")
            b.clicked.connect(lambda *_, f_=slot: f_())
            bar.addWidget(b)
        lay.addLayout(bar)
        box = QGroupBox("Everyone you work with")
        bl = QVBoxLayout(box)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        bl.addWidget(self.table)
        lay.addWidget(box, 1)
        hint = QLabel("Double-click someone to change their details. Kept in the portfolio file, on this computer.")
        hint.setObjectName("hint")
        lay.addWidget(hint)
        self.rows: list[dr.Person] = []
        self.fill()

    def _kinds(self) -> None:
        want = self.kind.currentData()
        c = dr.counts(self.pf)
        self.kind.blockSignals(True)
        self.kind.clear()
        self.kind.addItem(f"All ({sum(c.values())})", None)
        for k in list(dict.fromkeys(dr.KINDS + sorted(c))):
            self.kind.addItem(f"{k} ({c.get(k, 0)})", k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(want)))
        self.kind.blockSignals(False)

    def fill(self) -> None:
        self._kinds()
        self.rows = dr.people(self.pf, self.kind.currentData(), self.search.text())
        t = self.table
        t.setRowCount(len(self.rows))
        for r, p in enumerate(self.rows):
            for c, v in enumerate([p.name, p.company, p.kind, p.phone, p.email, ", ".join(p.works_on),
                                   p.notes.replace("\n", " ")]):
                t.setItem(r, c, QTableWidgetItem(v))
        t.resizeColumnsToContents()
        for c in range(6):
            t.setColumnWidth(c, min(max(t.columnWidth(c), 90), 260))
        t.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        total = sum(dr.counts(self.pf).values())
        self.count.setText(f"  {len(self.rows)} shown · {total} in all")

    def refresh(self) -> None:
        self.fill()

    def _selected(self) -> dr.Person | None:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) and self.table.selectionModel().hasSelection() else None

    def new(self, *, run=None):
        dlg = PersonDialog(self.pf, None, self)
        ok = run(dlg) if run is not None else dlg.exec()
        if ok:
            self.fill()
        return ok

    def edit(self, *, run=None):
        p = self._selected()
        if p is None:
            return None
        dlg = PersonDialog(self.pf, p, self)
        ok = run(dlg) if run is not None else dlg.exec()
        if ok:
            self.fill()
        return ok

    def delete(self, *, ask: bool = True) -> bool:
        p = self._selected()
        if p is None or (ask and not confirm(self, "Delete contact", f"Remove {p.label} from your contacts?")):
            return False
        dr.delete(self.pf, p.id)
        self.fill()
        return True

    def email(self) -> None:
        p = self._selected()
        if p and p.email:
            QDesktopServices.openUrl(QUrl(f"mailto:{p.email}"))

    def from_portfolio(self, *, quiet: bool = False) -> int:
        n = dr.from_portfolio(self.pf)
        self.fill()
        if not quiet:
            info(self, "Contacts", f"{n} investor(s) and lender(s) added from the portfolio." if n else
                 "Everyone on the cap tables and loans is already here.")
        return n
