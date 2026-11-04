"""Company management: create, switch, edit and remove companies."""

from __future__ import annotations

import calendar
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout)

from ..books import Books, CompanyProfile
from ..companies import EXTENSION, CompanyEntry, CompanyRegistry
from .common import MUTED, confirm, error

ENTITY_TYPES = ["", "Sole proprietor", "Single-member LLC", "Multi-member LLC", "Partnership", "S corporation",
                "C corporation", "Nonprofit", "Personal / household"]
FILE_FILTER = f"AM Accounting company files (*{EXTENSION});;All files (*)"


def default_registry() -> CompanyRegistry:
    data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or str(
        Path.home() / ".am-accounting")
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    return CompanyRegistry(Path(data) / "companies.json", Path(docs) / "AM Accounting" / "Companies")


class CompanyProfileDialog(QDialog):
    """New company (``books`` is None) or edit an existing company's details."""

    def __init__(self, registry: CompanyRegistry, books: Books | None = None, parent=None, *, first_run=False):
        super().__init__(parent)
        self.registry = registry
        self.books = books
        self.created_path: Path | None = None
        self.folder = registry.companies_dir
        self.setWindowTitle("Company Settings" if books else "New Company")
        self.resize(520, 0)
        form = QFormLayout(self)
        if first_run:
            welcome = QLabel("<b>Welcome to AM Accounting.</b><br>Set up your first company. You can add more "
                             "companies at any time from <i>File → New Company</i>.")
            welcome.setWordWrap(True)
            form.addRow(welcome)

        p = books.profile() if books else CompanyProfile("")
        from PySide6.QtWidgets import QButtonGroup, QRadioButton, QVBoxLayout

        from .. import company_roles

        roles_box = QVBoxLayout()
        self.role_group = QButtonGroup(self)
        self.role_buttons = {}
        for i, (key, (title, desc)) in enumerate(company_roles.ROLES.items()):
            rb = QRadioButton(f"{title} — {desc}")
            rb.setChecked(key == (p.role or "operating"))
            self.role_group.addButton(rb, i)
            self.role_buttons[key] = rb
            roles_box.addWidget(rb)
        role_hint = QLabel("Holding companies and asset managers get the accounts they need (investments in "
                           "subsidiaries, due from related parties, dividend / management-fee income…) and connect to "
                           "Asset Management.")
        role_hint.setObjectName("hint")
        role_hint.setWordWrap(True)
        roles_box.addWidget(role_hint)
        form.addRow("This company is a:", roles_box)
        self.name = QLineEdit(p.name)
        self.name.setPlaceholderText("Shown on financial statements")
        self.legal_name = QLineEdit(p.legal_name)
        self.legal_name.setPlaceholderText("Optional")
        self.entity = QComboBox()
        self.entity.setEditable(True)
        self.entity.addItems(ENTITY_TYPES)
        self.entity.setCurrentText(p.entity_type)
        self.tax_id = QLineEdit(p.tax_id)
        self.tax_id.setPlaceholderText("Optional (EIN / tax number)")
        self.fy = QComboBox()
        for m in range(1, 13):
            self.fy.addItem(calendar.month_name[m], m)
        self.fy.setCurrentIndex(p.fiscal_year_start_month - 1)
        form.addRow("Company name:", self.name)
        form.addRow("Legal name:", self.legal_name)
        form.addRow("Business type:", self.entity)
        form.addRow("Tax ID:", self.tax_id)
        form.addRow("Fiscal year starts in:", self.fy)

        if books is None:
            from .. import chart_of_accounts as coa

            self.setup = QComboBox()
            for key, (label, _entries) in coa.TEMPLATES.items():
                self.setup.addItem(label, f"template:{key}")
            for e in registry.companies():
                if e.exists:
                    self.setup.addItem(f"Copy accounts, headings & rules from “{e.name}”", f"copy:{e.path}")
            form.addRow("Chart of accounts:", self.setup)
            self._setup_touched = False
            self.setup.activated.connect(lambda _i: setattr(self, "_setup_touched", True))
            self.entity.currentTextChanged.connect(self._entity_changed)
            loc = QHBoxLayout()
            self.location = QLabel()
            self.location.setObjectName("hint")
            self._show_location()
            change = QPushButton("Change…")
            change.clicked.connect(self._choose_folder)
            loc.addWidget(self.location, 1)
            loc.addWidget(change)
            form.addRow("Save in:", loc)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        ok = buttons.addButton("Save" if books else "Create Company", QDialogButtonBox.ButtonRole.AcceptRole)
        ok.setObjectName("primary")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _entity_changed(self, entity: str) -> None:
        """Suggest the chart of accounts that fits the business type, unless one was picked by hand."""
        if self._setup_touched:
            return
        from .. import chart_of_accounts as coa

        i = self.setup.findData(f"template:{coa.template_for_entity(entity)}")
        if i >= 0:
            self.setup.setCurrentIndex(i)

    def _choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save company file in", str(self.folder))
        if d:
            self.folder = Path(d)
            self._show_location()

    def _show_location(self) -> None:
        full = str(self.folder)
        self.location.setToolTip(full)
        self.location.setText(self.location.fontMetrics().elidedText(full, Qt.TextElideMode.ElideMiddle, 300))

    def role(self) -> str:
        return next((k for k, b in self.role_buttons.items() if b.isChecked()), "operating")

    def profile(self) -> CompanyProfile:
        return CompanyProfile(self.name.text().strip(), self.legal_name.text(), self.entity.currentText(),
                              self.tax_id.text(), self.fy.currentData(), self.role())

    def save(self) -> None:
        p = self.profile()
        if not p.name:
            error(self, "Name required", "Enter a company name.")
            return
        try:
            if self.books:
                from .. import company_roles

                self.books.set_profile(p)
                if p.role != company_roles.role_of(self.books):
                    company_roles.apply(self.books, p.role)
                self.registry.rename(self.books.path, p.name)
            else:
                kind, _, value = (self.setup.currentData() or "template:general").partition(":")
                self.created_path = self.registry.create(
                    p, folder=self.folder, copy_setup_from=value if kind == "copy" else None,
                    template=value if kind == "template" else None)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save company", str(e))
            return
        self.accept()


def change_company_type(parent, path: str, open_books: Books | None = None) -> str | None:
    """Ask for a company's new type (operating company, holding company, asset manager) and set it. Uses
    ``open_books`` when that's the file already open. Returns the new role, or None if nothing changed."""
    from PySide6.QtWidgets import QInputDialog

    from .. import company_roles

    own = open_books is None or Path(str(open_books.path)).resolve() != Path(path).resolve()
    books = Books.open(path, create=False) if own else open_books
    try:
        current = company_roles.role_of(books)
        keys = list(company_roles.ROLES)
        labels = [f"{company_roles.label(k)} — {company_roles.ROLES[k][1]}" for k in keys]
        choice, ok = QInputDialog.getItem(parent, "Change type of business",
                                          f"{books.company_name} is a {company_roles.label(current).lower()}. "
                                          f"Change it to:", labels, keys.index(current), False)
        if not ok or keys[labels.index(choice)] == current:
            return None
        role = keys[labels.index(choice)]
        added = company_roles.apply(books, role)
    finally:
        if own:
            books.close()
    from .common import info

    msg = f"{Path(path).stem if own else open_books.company_name} is now a {company_roles.label(role).lower()}."
    if added:
        msg += "\n\nAccounts added: " + ", ".join(added) + "."
    if role in ("holding", "asset_manager"):
        msg += "\n\nIt's no longer counted in the portfolio summary or the portfolio's financials."
    info(parent, "Type changed", msg)
    return role


class CompaniesDialog(QDialog):
    """List of companies. ``chosen_path`` is set when the user opens one."""

    def __init__(self, registry: CompanyRegistry, current_path: str, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.current_path = current_path
        self.chosen_path: str | None = None
        self.setWindowTitle("Companies")
        self.resize(900, 460)
        layout = QVBoxLayout(self)
        intro = QLabel("Each company has its own separate books, bank accounts, rules and reports, stored in "
                       "its own company file.")
        intro.setObjectName("hint")
        layout.addWidget(intro)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Company", "Type", "Transactions", "Covers", "Last opened", "File"])
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.doubleClicked.connect(self.open_selected)
        layout.addWidget(self.table, 1)

        bar = QHBoxLayout()
        for label, slot in [("New Company…", self.new_company), ("Change Type…", self.change_type),
                            ("Add Existing File…", self.add_existing),
                            ("Show File", self.show_file), ("Remove from List", self.remove_selected),
                            ("Delete…", self.delete_selected)]:
            b = QPushButton(label)
            b.clicked.connect(slot)
            bar.addWidget(b)
        bar.addStretch()
        self.open_btn = QPushButton("Open")
        self.open_btn.setObjectName("primary")
        self.open_btn.clicked.connect(self.open_selected)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        bar.addWidget(self.open_btn)
        bar.addWidget(close)
        layout.addLayout(bar)
        self.refresh()

    def refresh(self) -> None:
        """Companies grouped by type: operating companies, holding companies, asset managers."""
        from .. import company_roles

        entries = self.registry.companies()
        roles = {e.path: (company_roles.read_role(e.path) if e.exists else "operating") for e in entries}
        rows: list = []
        for key, title in company_roles.GROUPS:
            group = [e for e in entries if roles[e.path] == key]
            if group:
                rows.append(f"{title} ({len(group)})")
                rows += group
        self.table.clearSpans()
        self.table.setRowCount(len(rows))
        first = None
        for r, e in enumerate(rows):
            if isinstance(e, str):  # a group heading
                head = QTableWidgetItem(e)
                f = head.font()
                f.setBold(True)
                head.setFont(f)
                head.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.table.setItem(r, 0, head)
                for c in range(1, 6):
                    blank = QTableWidgetItem("")
                    blank.setFlags(Qt.ItemFlag.ItemIsEnabled)
                    self.table.setItem(r, c, blank)
                self.table.setSpan(r, 0, 1, 6)
                continue
            first = r if first is None else first
            count = covers = ""
            if e.exists:
                try:
                    b = Books.open(e.path, create=False)
                    st = b.stats()
                    b.close()
                    count = f"{st['transactions']:,}"
                    if st["first"]:
                        covers = f"{st['first']} – {st['last']}"
                except Exception:  # noqa: BLE001
                    covers = "Can't read file"
            else:
                covers = "File not found"
            opened = datetime.fromisoformat(e.last_opened).strftime("%Y-%m-%d %H:%M") if e.last_opened else ""
            current = e.exists and Path(e.path).resolve() == Path(self.current_path).resolve()
            items = [QTableWidgetItem("    " + e.name + ("  (open)" if current else "")),
                     QTableWidgetItem(company_roles.label(roles[e.path])), QTableWidgetItem(count),
                     QTableWidgetItem(covers), QTableWidgetItem(opened), QTableWidgetItem(e.path)]
            items[0].setData(Qt.ItemDataRole.UserRole, e.path)
            if current:
                f = items[0].font()
                f.setBold(True)
                items[0].setFont(f)
            if not e.exists:
                for it in items:
                    it.setForeground(MUTED)
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)
        self.table.setColumnWidth(0, 240)
        self.table.setColumnWidth(1, 130)
        self.table.setColumnWidth(2, 95)
        self.table.setColumnWidth(3, 190)
        self.table.setColumnWidth(4, 130)
        if first is not None:
            self.table.selectRow(first)

    def change_type(self) -> None:
        e = self._selected()
        if e and e.exists and change_company_type(self, e.path):
            self.changed_types = True
            self.refresh()

    def _selected(self) -> CompanyEntry | None:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        return self.registry.find(path) if path else None  # None on a group heading

    def _choose(self, path: str) -> None:
        self.chosen_path = path
        self.accept()

    def open_selected(self) -> None:
        e = self._selected()
        if not e:
            return
        if not e.exists:
            error(self, "File not found", f"The company file is missing:\n{e.path}\n\nUse “Add Existing File…” if "
                                          "it was moved, or “Remove from List”.")
            return
        self._choose(e.path)

    def new_company(self) -> None:
        dlg = CompanyProfileDialog(self.registry, parent=self)
        if dlg.exec() and dlg.created_path:
            self._choose(str(dlg.created_path))

    def add_existing(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Add company file", str(self.registry.companies_dir), FILE_FILTER)
        if not path:
            return
        try:
            Books.open(path, create=False).close()
        except Exception as e:  # noqa: BLE001
            error(self, "Not a company file", str(e))
            return
        self.registry.add(path)
        self.refresh()

    def show_file(self) -> None:
        e = self._selected()
        if e:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(e.path).parent)))

    def _is_current(self, e: CompanyEntry) -> bool:
        return Path(e.path).resolve() == Path(self.current_path).resolve()

    def remove_selected(self) -> None:
        e = self._selected()
        if not e:
            return
        if self._is_current(e):
            error(self, "Company is open", "Switch to another company before removing this one.")
            return
        if confirm(self, "Remove from list", f"Remove “{e.name}” from the list?\n\nThe company file is kept on disk "
                                             "and can be added back later."):
            self.registry.remove(e.path)
            self.refresh()

    def delete_selected(self) -> None:
        e = self._selected()
        if not e:
            return
        if self._is_current(e):
            error(self, "Company is open", "Switch to another company before deleting this one.")
            return
        text, ok = QInputDialog.getText(
            self, "Delete company",
            f"This permanently deletes “{e.name}” and all of its books:\n{e.path}\n\n"
            "Type the company name to confirm:")
        if not ok:
            return
        if text.strip() != e.name:
            error(self, "Not deleted", "The name didn't match, so nothing was deleted.")
            return
        try:
            self.registry.delete(e.path)
        except Exception as ex:  # noqa: BLE001
            error(self, "Could not delete", str(ex))
        self.refresh()
