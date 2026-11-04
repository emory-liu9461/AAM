from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QMarginsF, Signal
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtWidgets import (QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QLabel, QPushButton, QTextBrowser,
                               QVBoxLayout, QWidget)

from .. import reports
from ..books import Books
from .common import error


def _qd(d: date) -> QDate:
    return QDate(d.year, d.month, d.day)


def _add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def fiscal_year_start(d: date, fy_month: int = 1) -> date:
    return date(d.year if d.month >= fy_month else d.year - 1, fy_month, 1)


def _quarter_start(d: date, fy_month: int = 1) -> date:
    fy = fiscal_year_start(d, fy_month)
    months_in = (d.year - fy.year) * 12 + d.month - fy.month
    return _add_months(fy, 3 * (months_in // 3))


def period_presets(today: date, fy_month: int = 1) -> dict[str, tuple[date, date]]:
    """Report periods. Quarters and years follow the company's fiscal year."""
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    q = _quarter_start(today, fy_month)
    lq_end = q - timedelta(days=1)
    fy = fiscal_year_start(today, fy_month)
    last_fy_end = fy - timedelta(days=1)
    calendar = fy_month == 1
    return {
        "This month": (first, today),
        "Last month": (last_month_end.replace(day=1), last_month_end),
        "This quarter": (q, today),
        "Last quarter": (_quarter_start(lq_end, fy_month), lq_end),
        "Year to date": (fy, today),
        ("Last year" if calendar else "Last fiscal year"): (fiscal_year_start(last_fy_end, fy_month), last_fy_end),
        "All time": (date(1900, 1, 1), today),
        "Custom": (first, today),
    }


class ReportsTab(QWidget):
    changed = Signal()  # a transaction was edited from an expanded statement line
    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        self.current: reports.Report | None = None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(list(reports.REPORTS))
        self.period = QComboBox()
        self.start = QDateEdit(calendarPopup=True)
        self.end = QDateEdit(calendarPopup=True)
        for w in (self.start, self.end):
            w.setDisplayFormat("yyyy-MM-dd")
        bar.addWidget(QLabel("Report:"))
        bar.addWidget(self.kind)
        bar.addWidget(QLabel("Period:"))
        bar.addWidget(self.period)
        bar.addWidget(self.start)
        bar.addWidget(QLabel("to"))
        bar.addWidget(self.end)
        bar.addWidget(QLabel("Columns:"))
        self.columns = QComboBox()
        for key, label in reports.PERIODS.items():
            self.columns.addItem(label, key)
        self.columns.setToolTip("Periods across the top (week, month, quarter, year), accounts down the side")
        self.columns.currentIndexChanged.connect(lambda *_: self._kind_changed())
        bar.addWidget(self.columns)
        bar.addStretch()
        run = QPushButton("Run Report")
        run.setObjectName("primary")
        run.clicked.connect(self.run)
        bar.addWidget(run)
        csv_btn = QPushButton("Export CSV…")
        csv_btn.clicked.connect(self.export_csv)
        pdf_btn = QPushButton("Export PDF…")
        pdf_btn.clicked.connect(self.export_pdf)
        xlsx_btn = QPushButton("Export Excel…")
        xlsx_btn.clicked.connect(self.export_xlsx)
        self.tb_btn = QPushButton("Save to Trial Balance Folder")
        self.tb_btn.setObjectName("primary")
        self.tb_btn.setToolTip("Save this trial balance as an Excel workbook in the company folder's "
                               "Trial Balance/<year>/ folder and open it")
        self.tb_btn.clicked.connect(self.save_trial_balance)
        bar.addWidget(csv_btn)
        bar.addWidget(pdf_btn)
        bar.addWidget(xlsx_btn)
        bar.addWidget(self.tb_btn)
        layout.addLayout(bar)
        self.view = QTextBrowser()
        from .theme import PAPER_CSS

        self.view.setStyleSheet(PAPER_CSS)  # statements are shown on white "paper" in light and dark mode
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._clicked)
        self.expanded: set[int] = set()
        drill = QHBoxLayout()
        hint = QLabel("Click an account (▸) to see the individual entries that make it up.")
        hint.setObjectName("hint")
        drill.addWidget(hint, 1)
        for text, slot in [("Expand All", self.expand_all), ("Collapse All", self.collapse_all)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            drill.addWidget(b)
        layout.addLayout(drill)
        layout.addWidget(self.view, 1)

        self.period.currentTextChanged.connect(self.apply_preset)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self.refresh_presets()

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh_presets()

    def refresh_presets(self) -> None:
        _, last = self.books.date_range()
        today = max(date.today(), date.fromisoformat(last)) if last else date.today()
        self.presets = period_presets(today, self.books.fiscal_year_start_month)
        self.period.blockSignals(True)
        self.period.clear()
        self.period.addItems(list(self.presets))
        self.period.setCurrentText("Year to date")
        self.period.blockSignals(False)
        self.apply_preset("Year to date")

    def apply_preset(self, name: str) -> None:
        if name not in self.presets:
            return
        s, e = self.presets[name]
        if name == "All time":
            first, _ = self.books.date_range()
            if first:
                s = date.fromisoformat(first)
        custom = name == "Custom"
        self.start.setEnabled(custom)
        self.end.setEnabled(custom)
        if not custom:
            self.start.setDate(_qd(s))
            self.end.setDate(_qd(e))
        self._kind_changed()

    def show_report(self, name: str, start: date, end: date) -> None:
        """Open a specific report for an exact period (used by the dashboard)."""
        self.kind.blockSignals(True)
        self.kind.setCurrentText(name)
        self.kind.blockSignals(False)
        self.period.blockSignals(True)
        self.period.setCurrentText("Custom")
        self.period.blockSignals(False)
        self.start.setEnabled(True)
        self.end.setEnabled(True)
        self.start.setDate(_qd(start))
        self.end.setDate(_qd(end))
        self._kind_changed()

    def _kind_changed(self) -> None:
        fn = reports.REPORTS[self.kind.currentText()]
        by_period = (self.columns.currentData() or "total") != "total"
        self.columns.setEnabled(fn != "trial_balance")
        self.start.setVisible(fn not in ("balance_sheet", "trial_balance") or (fn == "balance_sheet" and by_period))
        self.tb_btn.setVisible(reports.REPORTS[self.kind.currentText()] == "trial_balance")
        self.run()

    def run(self) -> None:
        start = self.start.date().toString("yyyy-MM-dd")
        end = self.end.date().toString("yyyy-MM-dd")
        self.current = reports.build_by_period(self.books, self.kind.currentText(), start, end,
                                               self.columns.currentData() or "total")
        self.render()

    def _details(self, account_id: int) -> list:
        rep = self.current
        return reports.account_entries(self.books, account_id, rep.start, rep.end)

    def _html(self, *, links: bool) -> str:
        return self.current.to_html(links=links, expanded=self.expanded, details=self._details)

    def render(self) -> None:
        """Redraw the statement, keeping the scroll position (used when expanding / collapsing)."""
        if not self.current:
            return
        bar = self.view.verticalScrollBar()
        pos = bar.value()
        self.view.setHtml(self._html(links=True))
        bar.setValue(pos)

    def _clicked(self, url) -> None:
        text = url.toString()
        if text.startswith("acct:"):
            acct = int(text[5:])
            self.expanded.symmetric_difference_update({acct})
            self.render()
        elif text.startswith("tx:"):  # an entry under an expanded account: edit who / category / note
            from .transaction_dialog import TransactionDialog

            if TransactionDialog(self.books, int(text[3:]), self).exec():
                self.run()
                self.changed.emit()

    def expand_all(self) -> None:
        if self.current:
            self.expanded = {l.account_id for l in self.current.lines if l.account_id is not None}
            self.render()

    def collapse_all(self) -> None:
        self.expanded = set()
        self.render()

    def _default_name(self, ext: str) -> str:
        return f"{self.current.title} {self.end.date().toString('yyyy-MM-dd')}.{ext}" if self.current else f"report.{ext}"

    def export_csv(self) -> None:
        if not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", self._default_name("csv"), "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as f:
                f.write(self.current.to_csv())

    def export_xlsx(self) -> None:
        if not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export Excel", self._default_name("xlsx"), "Excel (*.xlsx)")
        if not path:
            return
        try:
            if reports.REPORTS[self.kind.currentText()] == "trial_balance":
                from .. import trial_balance as tb

                tb.write_xlsx(self.books, Path(path), self._end(), self._tb_start())
            else:
                from ..workspace import report_to_xlsx

                report_to_xlsx(self.current, Path(path))
        except PermissionError:
            error(self, "Couldn't save", "The file is open in another program. Close it and try again.")

    def _end(self) -> date:
        return date.fromisoformat(self.end.date().toString("yyyy-MM-dd"))

    def _tb_start(self) -> date | None:
        s = date.fromisoformat(self.start.date().toString("yyyy-MM-dd"))
        return s if s <= self._end() else None

    def save_trial_balance(self) -> Path | None:
        """Save the trial balance (as of the end date) into Trial Balance/<year>/ and open it."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from .. import trial_balance as tb

        try:
            path = tb.save_to_folder(self.books, self._end(), self._tb_start())
        except PermissionError:
            error(self, "Couldn't save", "The workbook is open in Excel. Close it and try again.")
            return None
        if path is None:
            error(self, "No company folder", "Set up the company folder first (File → Company Folder & Auto-Save), "
                                             "or use Export Excel… to save it anywhere.")
            return None
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        return path

    def export_pdf(self) -> None:
        if not self.current:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", self._default_name("pdf"), "PDF (*.pdf)")
        if path:
            write_pdf(self._html(links=False), path)  # includes the entries you expanded


def write_pdf(html: str, path: str) -> None:
    from PySide6.QtGui import QPdfWriter

    writer = QPdfWriter(path)
    writer.setPageLayout(QPageLayout(QPageSize(QPageSize.PageSizeId.Letter), QPageLayout.Orientation.Portrait,
                                     QMarginsF(18, 18, 18, 18), QPageLayout.Unit.Millimeter))
    doc = QTextDocument()
    doc.setHtml(html)
    doc.setPageSize(writer.pageLayout().paintRectPoints().size())
    doc.print_(writer)
