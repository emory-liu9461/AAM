"""Investor Reports window: pick a quarter, see each investor's statement, save them all as PDFs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QPushButton, QSpinBox, QSplitter, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import investor_reports as ir
from ..money import fmt_money
from .common import ALIGN_RIGHT, error, info

COLS = ["Investor", "Companies", "Paid in", "Distributions (quarter)", "Distributions (total)", "Value today",
        "MOIC", "IRR"]


class InvestorReportsDialog(QDialog):
    def __init__(self, pf, parent=None, holding_id: int | None = None, sender: str = ""):
        super().__init__(parent)
        from .asset_mgmt import _table

        self.pf = pf
        self.setWindowTitle("Investor Reports")
        self.resize(1200, 720)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        y, q = ir.last_quarter()
        row.addWidget(QLabel("Quarter:"))
        self.q = QComboBox()
        for i in range(1, 5):
            self.q.addItem(f"Q{i}", i)
        self.q.setCurrentIndex(q - 1)
        self.year = QSpinBox()
        self.year.setRange(2000, 2100)
        self.year.setValue(y)
        row.addWidget(self.q)
        row.addWidget(self.year)
        row.addSpacing(12)
        row.addWidget(QLabel("From:"))
        self.sender = QLineEdit(sender)
        self.sender.setPlaceholderText("Your company's name, shown on each statement")
        self.sender.setMinimumWidth(240)
        row.addWidget(self.sender)
        row.addStretch(1)
        self.save_btn = QPushButton("Save PDFs to a Folder…")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save_all)
        row.addWidget(self.save_btn)
        lay.addLayout(row)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Companies</b> (with owners on their cap table)"))
        self.companies = QListWidget()
        self.companies.setMaximumHeight(140)
        for h in pf.holdings():
            if h.status != "active":
                continue
            it = QListWidgetItem(h.name)
            it.setData(Qt.ItemDataRole.UserRole, h.id)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(Qt.CheckState.Checked if holding_id in (None, h.id) else Qt.CheckState.Unchecked)
            self.companies.addItem(it)
        ll.addWidget(self.companies)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        ll.addWidget(self.note)
        self.table = _table(COLS, 1)
        self.table.itemSelectionChanged.connect(self._preview)
        ll.addWidget(self.table, 1)
        split.addWidget(left)
        self.view = QTextBrowser()
        split.addWidget(self.view)
        split.setSizes([640, 560])
        lay.addWidget(split, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self.rows: list[ir.Statement] = []
        self.q.currentIndexChanged.connect(lambda *_: self.fill())
        self.year.valueChanged.connect(lambda *_: self.fill())
        self.companies.itemChanged.connect(lambda *_: self.fill())
        self.sender.textChanged.connect(lambda *_: self._preview())
        self.fill()

    def _picked(self) -> list[int]:
        return [self.companies.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.companies.count())
                if self.companies.item(i).checkState() == Qt.CheckState.Checked]

    def fill(self) -> None:
        rows = self.rows = ir.statements(self.pf, self.year.value(), self.q.currentData(), self._picked())
        t = self.table
        t.setRowCount(len(rows))
        for r, st in enumerate(rows):
            vals = [st.label, ", ".join(p.company for p in st.positions), fmt_money(st.total("paid_in")),
                    fmt_money(st.total("distributed")), fmt_money(st.total("total_distributed")),
                    fmt_money(st.total("value")), ir._x(st.moic), ir._pct(st.irr)]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c >= 2:
                    it.setTextAlignment(ALIGN_RIGHT)
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        self.note.setText(f"{len(rows)} investor(s). Each statement covers every picked company the investor owns part "
                          "of. Owners come from each company's cap table (Financing & Cap Table)."
                          if rows else "No owners on the picked companies' cap tables — add them on Financing & Cap "
                                       "Table.")
        self.save_btn.setEnabled(bool(rows))
        if rows:
            t.selectRow(0)
            self._preview()            # (already selected: no selection change to show it)
        else:
            self.view.clear()

    def _preview(self) -> None:
        sel = self.table.selectionModel().selectedRows()
        if sel and sel[0].row() < len(self.rows):
            self.view.setHtml(ir.statement_html(self.rows[sel[0].row()], self.sender.text().strip()))

    def save_all(self, folder: str | None = None) -> list[Path]:
        from .reports_tab import write_pdf

        folder = folder or QFileDialog.getExistingDirectory(self, "Save the statements in")
        if not folder:
            return []
        out = []
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            for st in self.rows:
                path = Path(folder) / ir.file_name(st)
                write_pdf(ir.statement_html(st, self.sender.text().strip()), str(path))
                out.append(path)
        except OSError as e:
            error(self, "Couldn't save", str(e))
            return out
        info(self, "Saved", f"{len(out)} statement(s) saved to {folder}.")
        return out
