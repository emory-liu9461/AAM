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

COLS = ["Name", "Company", "Phone", "Email", "Address", "Type", "Works on", "Notes"]


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
        self.phone = QLineEdit(p.phone)
        form.addRow("Phone:", self.phone)
        self.email = QLineEdit(p.email)
        form.addRow("Email:", self.email)
        self.address = QLineEdit(p.address)
        form.addRow("Address:", self.address)
        self.kind = QComboBox()
        self.kind.setEditable(True)
        self.kind.addItems(dr.KINDS)
        self.kind.setCurrentText(p.kind or "Other")
        self.kind.setToolTip("Investor, contractor, lender, broker … — pick one or type your own")
        form.addRow("Type:", self.kind)
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
        bar.addWidget(QLabel("Type:"))
        self.kind = QComboBox()
        self.kind.currentIndexChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.kind)
        bar.addWidget(QLabel("Project:"))
        self.project = QComboBox()
        self.project.setMinimumWidth(160)
        self.project.currentIndexChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.project)
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
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
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

    def _projects(self) -> None:
        want = self.project.currentData()
        names = list(dict.fromkeys(dr.places(self.pf) + [w for p in dr.people(self.pf) for w in p.works_on]))
        self.project.blockSignals(True)
        self.project.clear()
        self.project.addItem("All", None)
        for n in names:
            self.project.addItem(n, n)
        self.project.setCurrentIndex(max(0, self.project.findData(want)))
        self.project.blockSignals(False)

    def fill(self) -> None:
        self._kinds()
        self._projects()
        self.rows = dr.people(self.pf, self.kind.currentData(), self.search.text())
        if self.project.currentData():
            want = self.project.currentData().lower()
            self.rows = [p for p in self.rows if want in {w.lower() for w in p.works_on}]
        t = self.table
        t.setRowCount(len(self.rows))
        for r, p in enumerate(self.rows):
            for c, v in enumerate([p.name, p.company, p.phone, p.email, p.address.replace("\n", ", "), p.kind,
                                   ", ".join(p.works_on),
                                   p.notes.replace("\n", " ")]):
                t.setItem(r, c, QTableWidgetItem(v))
        t.resizeColumnsToContents()
        for c in range(7):
            t.setColumnWidth(c, min(max(t.columnWidth(c), 90), 260))
        t.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeMode.Stretch)
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


PROJECT_COLS = ["Name", "Company", "Phone", "Email", "Address", "Type", "Notes"]


class PickContactsDialog(QDialog):
    """Choose people from your contacts (the whole list, searchable by kind) to put on a project."""

    def __init__(self, pf, exclude: set[int], parent=None, kind: str | None = None):
        super().__init__(parent)
        self.pf, self.exclude = pf, exclude
        self.setWindowTitle("Add from Contacts")
        self.resize(640, 460)
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, company, phone …")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.search, 1)
        bar.addWidget(QLabel("Type:"))
        self.kind = QComboBox()
        self.kind.addItem("All", None)
        for k in dr.KINDS:
            self.kind.addItem(k, k)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.kind.currentIndexChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.kind)
        lay.addLayout(bar)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        hint = QLabel("Tick everyone to put on this project. Someone new? Use New Contact… on the project instead.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.ticked: set[int] = set()
        self.fill()

    def _remember(self) -> None:
        for i in range(self.list.count()):
            it = self.list.item(i)
            pid = it.data(Qt.ItemDataRole.UserRole)
            if it.checkState() == Qt.CheckState.Checked:
                self.ticked.add(pid)
            else:
                self.ticked.discard(pid)

    def fill(self) -> None:
        self._remember()
        self.list.clear()
        self.rows = [p for p in dr.people(self.pf, self.kind.currentData(), self.search.text())
                     if p.id not in self.exclude]
        for p in self.rows:
            extra = dr.details(p)
            it = QListWidgetItem(f"{p.name}  —  {p.kind}" + (f"  ·  {extra}" if extra else ""))
            it.setData(Qt.ItemDataRole.UserRole, p.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if p.id in self.ticked else Qt.CheckState.Unchecked)
            self.list.addItem(it)

    def chosen(self) -> list[int]:
        self._remember()
        return sorted(self.ticked)


class ProjectContacts(QWidget):
    """The people on one project (a deal, or the property it became): brokers, lenders, contractors, investors …
    Picked from your contacts, or added new (and so kept in Contacts too). Investors on the cap table and lenders on
    the loans come in by themselves."""

    def __init__(self, pf, primary: str, names: list[str], parent=None, *, sync: bool = True):
        super().__init__(parent)
        self.pf, self.primary, self.names = pf, primary, list(dict.fromkeys([primary] + names))
        self.sync = sync
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Type:"))
        self.kind = QComboBox()
        self.kind.addItem("All", None)
        for k in dr.KINDS:
            self.kind.addItem(k, k)
        self.kind.currentIndexChanged.connect(lambda *_: self.fill())
        bar.addWidget(self.kind)
        for text, slot, tip in (("Add from Contacts…", self.add_existing,
                                 "Put people already in Contacts (contractors, brokers, lenders …) on this project"),
                                ("New Contact…", self.new, "Someone new: kept in Contacts and put on this project"),
                                ("Edit…", self.edit, "Change the selected person's details"),
                                ("Remove from Project", self.remove,
                                 "Take the selected person off this project (they stay in Contacts)"),
                                ("Email", self.email, "Write to the selected person (your email program)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            if text.startswith("Add"):
                b.setObjectName("primary")
            b.clicked.connect(lambda *_, f_=slot: f_())
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        box = QGroupBox(f"People on {primary}" if primary else "People on this project")
        bl = QVBoxLayout(box)
        self.table = QTableWidget(0, len(PROJECT_COLS))
        self.table.setHorizontalHeaderLabels(PROJECT_COLS)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.table.doubleClicked.connect(lambda *_: self.edit())
        bl.addWidget(self.table)
        self.empty = QLabel()
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        bl.addWidget(self.empty)
        lay.addWidget(box, 1)
        hint = QLabel("Everyone here is also in Contacts (beside Personal Finance at the top). Investors on the cap "
                      "table and lenders on the loans are added by themselves.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.rows: list[dr.Person] = []
        self.fill()

    def fill(self) -> None:
        if self.sync:
            try:
                dr.from_portfolio(self.pf)
            except Exception:  # noqa: BLE001
                pass
        self.rows = dr.on_project(self.pf, self.names, self.kind.currentData())
        t = self.table
        t.setRowCount(len(self.rows))
        for r, p in enumerate(self.rows):
            for c, v in enumerate([p.name, p.company, p.phone, p.email, p.address.replace("\n", ", "), p.kind,
                                   p.notes.replace("\n", " ")]):
                it = QTableWidgetItem(v)
                it.setToolTip("\n".join(x for x in (p.label, p.phone, p.email, p.address, p.website) if x))
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        for c in range(6):
            t.setColumnWidth(c, min(max(t.columnWidth(c), 90), 240))
        t.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        self.empty.setVisible(not self.rows)
        self.empty.setText("No one on this project yet — Add from Contacts… (e.g. your mortgage broker or "
                           "contractors), or New Contact….")

    def refresh(self) -> None:
        self.fill()

    def _selected(self) -> dr.Person | None:
        r = self.table.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) and self.table.selectionModel().hasSelection() else None

    def add_existing(self, *, run=None) -> list[int]:
        have = {p.id for p in dr.on_project(self.pf, self.names)}
        dlg = PickContactsDialog(self.pf, have, self, self.kind.currentData())
        ok = run(dlg) if run is not None else dlg.exec()
        if not ok:
            return []
        ids = dlg.chosen()
        for p in dr.people(self.pf):
            if p.id in ids:
                dr.link(self.pf, p, self.primary)
        self.fill()
        return ids

    def new(self, *, run=None):
        p = dr.Person("", kind=self.kind.currentData() or "Other", works_on=[self.primary] if self.primary else [])
        dlg = PersonDialog(self.pf, p, self)
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

    def remove(self, *, ask: bool = True) -> bool:
        p = self._selected()
        if p is None or (ask and not confirm(self, "Remove from project",
                                             f"Take {p.label} off {self.primary}? They stay in your contacts.")):
            return False
        dr.unlink(self.pf, p, self.names)
        self.fill()
        return True

    def email(self) -> None:
        p = self._selected()
        if p and p.email:
            QDesktopServices.openUrl(QUrl(f"mailto:{p.email}"))
