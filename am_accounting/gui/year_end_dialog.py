"""Year-End Package window: pick the fiscal year, check the CCA classes, make the folder for the accountant."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTableWidgetItem, QVBoxLayout)

from .. import year_end as ye
from ..money import fmt_money
from .common import ALIGN_RIGHT, error, info


class YearEndDialog(QDialog):
    def __init__(self, books, parent=None, today: date | None = None):
        super().__init__(parent)
        from .asset_mgmt import _table

        self.books = books
        self.setWindowTitle(f"Year-End Package – {books.company_name}")
        self.resize(1050, 700)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Fiscal year:"))
        self.year = QComboBox()
        start, end = ye.last_fiscal_year(books, today)
        for _ in range(6):
            self.year.addItem(f"{start:%b} {start.day}, {start.year} – {end:%b} {end.day}, {end.year}", end)
            start, end = ye.fiscal_year(books, start - timedelta(days=1))
        self.year.currentIndexChanged.connect(lambda *_: self.fill())
        row.addWidget(self.year)
        row.addWidget(QLabel("First-year rule:"))
        self.rule = QComboBox()
        self.rule.addItem("Accelerated Investment Incentive (no half-year rule)", "aiip")
        self.rule.addItem("Half-year rule", "half-year")
        self.rule.currentIndexChanged.connect(lambda *_: self._fill_cca())
        row.addWidget(self.rule)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(QLabel("<b>Fixed-asset accounts</b> — pick each one's CCA class (saved with the company)"))
        self.assets = _table(["Account", "CCA class", "Cost at start", "Additions", "Disposals", "Cost at end"], 0)
        lay.addWidget(self.assets, 1)
        lay.addWidget(QLabel("<b>Draft CCA schedule</b> — double-click an opening UCC to change it"))
        self.cca = _table(["Class", "Rate", "Opening UCC", "Additions", "Disposals", "CCA", "Closing UCC",
                           "Opening UCC from"], 0)
        self.cca.setEditTriggers(self.cca.EditTrigger.DoubleClicked)
        self.cca.itemChanged.connect(self._ucc_changed)
        lay.addWidget(self.cca, 1)
        self.checks = QLabel()
        self.checks.setWordWrap(True)
        lay.addWidget(self.checks)
        frow = QHBoxLayout()
        frow.addWidget(QLabel("Save in:"))
        from .. import workspace as ws

        cfg = ws.get_config(books)
        self.folder = QLineEdit(str(cfg.dir("year_end")) if cfg.enabled else str(Path.home() / "Documents"))
        frow.addWidget(self.folder, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        frow.addWidget(browse)
        lay.addLayout(frow)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        make = buttons.addButton("Make the Package", QDialogButtonBox.ButtonRole.AcceptRole)
        make.setObjectName("primary")
        buttons.accepted.connect(self.make)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.ucc: dict[str, int] = {}
        self.package: ye.Package | None = None
        self.fill()

    def _span(self) -> tuple[date, date]:
        return ye.fiscal_year(self.books, self.year.currentData())

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save the package in", self.folder.text())
        if d:
            self.folder.setText(d)

    def fill(self) -> None:
        start, end = self._span()
        self.rule.blockSignals(True)
        self.rule.setCurrentIndex(self.rule.findData(ye.first_year_rule(end)))
        self.rule.blockSignals(False)
        self.ucc = {}
        rows = ye.asset_rows(self.books, start, end)
        t = self.assets
        t.setRowCount(len(rows))
        for r, a in enumerate(rows):
            t.setItem(r, 0, QTableWidgetItem(f"{a.code} · {a.name}"))
            if a.accumulated:
                t.setItem(r, 1, QTableWidgetItem("— (accumulated depreciation)"))
            else:
                box = QComboBox()
                for k, (_rate, label) in ye.CCA_CLASSES.items():
                    box.addItem(label, k)
                box.setCurrentIndex(max(0, box.findData(a.cca_class)))
                box.currentIndexChanged.connect(lambda _i, aid=a.account_id, b=box: self._class_changed(aid, b))
                t.setCellWidget(r, 1, box)
            for c, v in ((2, a.opening), (3, a.additions), (4, a.disposals), (5, a.closing)):
                it = QTableWidgetItem(fmt_money(v))
                it.setTextAlignment(ALIGN_RIGHT)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        t.setColumnWidth(1, 360)
        self._fill_cca()
        self.checks.setText("<br>".join(ye.checks(self.books, start, end)))

    def _class_changed(self, account_id: int, box: QComboBox) -> None:
        m = ye.class_map(self.books)
        m[account_id] = box.currentData()
        ye.save_class_map(self.books, m)
        self._fill_cca()

    def schedule(self) -> list[ye.CcaRow]:
        start, end = self._span()
        return ye.cca_schedule(self.books, start, end, rule=self.rule.currentData(), opening_ucc=self.ucc)

    def _fill_cca(self) -> None:
        rows = self.schedule()
        t = self.cca
        t.blockSignals(True)
        t.setRowCount(len(rows))
        for r, c in enumerate(rows):
            vals = [c.label, f"{c.rate * 100:g}%", fmt_money(c.opening_ucc), fmt_money(c.additions),
                    fmt_money(c.disposals), fmt_money(c.cca), fmt_money(c.closing_ucc),
                    "entered / last year" if c.ucc_entered else "assumed = cost (check)"]
            for col, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setData(Qt.ItemDataRole.UserRole, c.cca_class)
                if 2 <= col <= 6:
                    it.setTextAlignment(ALIGN_RIGHT)
                if col != 2:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                t.setItem(r, col, it)
        t.resizeColumnsToContents()
        t.blockSignals(False)

    def _ucc_changed(self, it: QTableWidgetItem) -> None:
        if it.column() != 2:
            return
        try:
            v = round(float(it.text().replace("$", "").replace(",", "").strip() or 0) * 100)
        except ValueError:
            error(self, "Opening UCC", "Enter an amount.")
            self._fill_cca()
            return
        self.ucc[it.data(Qt.ItemDataRole.UserRole)] = v
        self._fill_cca()

    def make(self, folder: str | None = None) -> ye.Package | None:
        folder = folder or self.folder.text().strip()
        if not folder:
            error(self, "Year-end package", "Pick a folder to save it in.")
            return None
        try:
            pkg = self.package = ye.build(self.books, Path(folder), self.year.currentData(),
                                          rule=self.rule.currentData(), opening_ucc=self.ucc)
        except (OSError, ValueError) as e:
            error(self, "Couldn't make the package", str(e))
            return None
        info(self, "Year-end package", f"Saved {len(pkg.files)} files to {pkg.folder}.\n\n" + "\n".join(pkg.checks))
        return pkg
