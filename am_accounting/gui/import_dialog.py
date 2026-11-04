from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
                               QVBoxLayout)

from ..books import Books, ParsedTransaction
from ..categorizer import Categorizer
from ..importers import parse_statement_bytes
from ..money import fmt_money
from .theme import c as tc
from .common import ALIGN_RIGHT, MUTED, NEGATIVE, POSITIVE, error, fill_account_combo


class ImportDialog(QDialog):
    """Preview a statement, choose the bank account, then import."""

    def __init__(self, books: Books, path: str, parent=None):
        super().__init__(parent)
        self.books = books
        self.path = Path(path)
        self.data = self.path.read_bytes()
        self.parsed: list[ParsedTransaction] = []
        self.result_counts = (0, 0)
        self.setWindowTitle(f"Import – {self.path.name}")
        self.resize(900, 600)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        row = QHBoxLayout()
        self.bank = QComboBox()
        fill_account_combo(self.bank, books, banks_only=True)
        row.addWidget(self.bank, 1)
        if self.path.suffix.lower() == ".pdf":
            show_text = QPushButton("Show Extracted Text…")
            show_text.setToolTip("See exactly what was read from the PDF")
            show_text.clicked.connect(self.show_text)
        new_bank = QPushButton("New Bank Account…")
        new_bank.clicked.connect(self.new_bank_account)
        row.addWidget(new_bank)
        if self.path.suffix.lower() == ".pdf":
            row.addWidget(show_text)
        form.addRow("Import into:", row)
        opts = QHBoxLayout()
        self.flip = QCheckBox("Flip signs (use for credit-card exports where charges are positive)")
        self.dayfirst = QCheckBox("Dates are day/month/year")
        opts.addWidget(self.flip)
        opts.addWidget(self.dayfirst)
        opts.addStretch()
        form.addRow("Options:", opts)
        self.auto = QCheckBox("Auto-categorize after import")
        self.auto.setChecked(True)
        form.addRow("", self.auto)
        from PySide6.QtCore import QSettings

        from ..pdf_to_excel import COMBINED_NAME

        self.settings = QSettings("AM Accounting", "AM Accounting")
        self.to_excel = QCheckBox(f"Also add this PDF to “{COMBINED_NAME}” in the same folder")
        self.to_excel.setToolTip("One workbook per folder: an All Transactions sheet, a Summary sheet and one "
                                 "sheet per PDF statement. Re-importing a PDF replaces its sheet.")
        self.to_excel.setChecked(self.settings.value("import/pdf_to_excel", True, type=bool))
        self.to_excel.setVisible(self.path.suffix.lower() == ".pdf")
        self.to_excel.toggled.connect(lambda _on: self.render() if hasattr(self, "table") else None)
        form.addRow("", self.to_excel)
        layout.addLayout(form)

        self.info = QLabel()
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Date", "Description", "Amount", "Status"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.import_btn = self.buttons.addButton("Import", QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_btn.setObjectName("primary")
        self.buttons.accepted.connect(self.do_import)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.flip.toggled.connect(self.reparse)
        self.dayfirst.toggled.connect(self.reparse)
        self.bank.currentIndexChanged.connect(self.render)
        self.reparse()

    def new_bank_account(self) -> None:
        name, ok = QInputDialog.getText(self, "New bank account", "Account name (e.g. \"Chase Checking ••4421\"):")
        if ok and name.strip():
            acct = self.books.add_account(self.books.next_account_code("asset", is_bank=True), name, "asset",
                                          "bank", is_bank=True)
            fill_account_combo(self.bank, self.books, banks_only=True)
            self.bank.setCurrentIndex(self.bank.findData(acct.id))

    def show_text(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication, QPlainTextEdit

        from ..importers.pdf_importer import best_text

        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            method, text = best_text(self.data)
        finally:
            QApplication.restoreOverrideCursor()
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Text read from {self.path.name} ({method})")
        dlg.resize(820, 620)
        lay = QVBoxLayout(dlg)
        hint = QLabel("This is what the app could read. Transaction lines need a date at the start and an amount "
                      "at the end. If they look different, send this text (with personal details removed) so "
                      "the reader can be adapted to your bank's layout.")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        box = QPlainTextEdit(text or "(no text could be read)")
        box.setReadOnly(True)
        box.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        lay.addWidget(box, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy = buttons.addButton("Copy Text", QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda: QApplication.clipboard().setText(box.toPlainText()))
        buttons.rejected.connect(dlg.reject)
        lay.addWidget(buttons)
        dlg.exec()

    def reparse(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        if self.path.suffix.lower() == ".pdf":
            self.info.setText("Reading the PDF… (scanned statements are read with OCR, which takes a few seconds "
                              "per page)")
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            QApplication.processEvents()
        try:
            parsed, self.warnings = parse_statement_bytes(self.data, self.path.name, dayfirst=self.dayfirst.isChecked())
        except Exception as e:  # noqa: BLE001 - show any parser failure to the user
            parsed, self.warnings = [], [f"Could not read this file: {e}"]
        finally:
            if self.path.suffix.lower() == ".pdf":
                QApplication.restoreOverrideCursor()
        if self.flip.isChecked():
            parsed = [ParsedTransaction(t.date, t.description, -t.amount_cents, t.memo) for t in parsed]
        self.parsed = parsed
        self.render()

    def render(self) -> None:
        bank_id = self.bank.currentData()
        existing = self.books.existing_fingerprints(bank_id) if bank_id else set()
        fps = Books.fingerprints_for(self.parsed)
        self.table.setRowCount(len(self.parsed))
        new = 0
        money_in = money_out = 0
        for r, (tx, fp) in enumerate(zip(self.parsed, fps)):
            dup = fp in existing
            new += not dup
            if tx.amount_cents > 0:
                money_in += tx.amount_cents
            else:
                money_out += tx.amount_cents
            items = [QTableWidgetItem(tx.date.isoformat()), QTableWidgetItem(tx.description),
                     QTableWidgetItem(fmt_money(tx.amount_cents, parens=False)),
                     QTableWidgetItem("Already imported" if dup else "New")]
            items[2].setTextAlignment(ALIGN_RIGHT)
            items[2].setForeground(POSITIVE if tx.amount_cents > 0 else NEGATIVE)
            if dup:
                for it in items:
                    it.setForeground(MUTED)
            for c, it in enumerate(items):
                self.table.setItem(r, c, it)
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(2, 120)
        self.table.setColumnWidth(3, 130)
        text = (f"<b>{len(self.parsed)}</b> transactions found · <b>{new}</b> new · "
                f"money in {fmt_money(money_in)} · money out {fmt_money(-money_out)}")
        if self.parsed:
            text += f" · {self.parsed[0].date} to {self.parsed[-1].date}"
        checks = [w for w in self.warnings if w.startswith("Checked:")]
        problems = [w for w in self.warnings if not w.startswith("Checked:")]
        if checks:
            text += f"<br><span style='color:{tc('green')}'>✓ " + "<br>".join(checks) + "</span>"
        if problems:
            text += f"<br><span style='color:{tc('red')}'>" + "<br>".join(problems[:5]) + "</span>"
        self.info.setText(text)
        excel_only = (new == 0 and bool(self.parsed) and self.path.suffix.lower() == ".pdf"
                      and self.to_excel.isChecked())
        self.import_btn.setText("Add to Excel Workbook" if excel_only else "Import")
        self.import_btn.setEnabled((new > 0 or excel_only) and bank_id is not None)

    def do_import(self) -> None:
        bank_id = self.bank.currentData()
        if bank_id is None:
            error(self, "Choose an account", "Choose which bank account this statement belongs to.")
            return
        self.result_counts = self.books.import_transactions(bank_id, self.parsed, filename=self.path.name,
                                                            file_bytes=self.data)
        if self.auto.isChecked():
            Categorizer(self.books).run()
        self.excel_path = None
        if self.path.suffix.lower() == ".pdf":
            self.settings.setValue("import/pdf_to_excel", self.to_excel.isChecked())
            if self.to_excel.isChecked() and self.parsed:
                from ..pdf_to_excel import COMBINED_NAME, add_to_combined_workbook

                try:
                    self.excel_path = add_to_combined_workbook(self.path.parent / COMBINED_NAME, self.path.name,
                                                               self.parsed, account=self.books.account(bank_id).name)
                except Exception as e:  # noqa: BLE001 - the import itself already succeeded
                    error(self, "Excel workbook not updated",
                          f"The statement was imported, but the Excel workbook couldn't be written:\n{e}")
        self.accept()
