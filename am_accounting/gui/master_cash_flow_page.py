"""Portfolio → Master Cash Flow: the next twelve months of the businesses and your own money together (see
master_cash_flow.py)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QLabel, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from .. import master_cash_flow as mcf
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, NEGATIVE


class MasterCashFlowPage(QWidget):
    def __init__(self, pf, performances=None, parent=None):
        super().__init__(parent)
        self.pf = pf
        self.performances = performances or (lambda: None)     # () -> the portfolio's performances (or None)
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setTextFormat(Qt.TextFormat.RichText)
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        self.table = QTableWidget()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        lay.addWidget(self.table, 1)
        from .frozen import freeze_first_column

        freeze_first_column(self.table)
        hint = QLabel("Businesses: each one's projections for the year ÷ 12 (else its last 12 months' net cash flow), "
                      "at our share. Personal: your budget (Personal Finance → Budget), without Business income — the "
                      "distributions to you only move cash from the businesses to you, so they're shown below as a "
                      "memo, not counted twice. Cash starts from the businesses' books (at our share) and your bank "
                      "accounts.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.data: mcf.MasterCashFlow | None = None

    def fill(self) -> None:
        d = self.data = mcf.build(self.pf, self.performances())
        cols = ["", *d.labels, "12 months"]
        t = self.table
        t.clear()
        t.setColumnCount(len(cols))
        t.setHorizontalHeaderLabels(cols)
        rows: list[tuple] = [("head", "BUSINESSES (our share)")]
        rows += [("line", f"    {b.name}", b.by_month, b.basis) for b in d.businesses]
        rows += [("total", "Cash flow from businesses", d.business_total, ""),
                 ("head", "PERSONAL (your budget)"),
                 ("line", "    Income (not from your businesses)", d.personal_income, ""),
                 ("line", "    Spending", [-x for x in d.personal_spend], ""),
                 ("total", "Personal cash flow", d.personal_net, ""),
                 ("head", "EVERYTHING"),
                 ("total", "Net cash flow", d.net, ""),
                 ("cash", "Cash at the end of the month", d.cash_end, ""),
                 ("memo", "Memo: distributions to you (budgeted; already in the businesses' cash)", d.distributions,
                  "")]
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            kind, label = row[0], row[1]
            it = QTableWidgetItem(label)
            f = it.font()
            f.setBold(kind in ("head", "total", "cash"))
            it.setFont(f)
            if kind in ("head", "memo"):
                it.setForeground(MUTED)
            if len(row) > 3 and row[3]:
                it.setToolTip(f"From its {row[3]}")
            t.setItem(r, 0, it)
            if kind == "head":
                continue
            vals = row[2]
            total = None if kind == "cash" else sum(vals)
            for c, v in enumerate([*vals, total]):
                cell = QTableWidgetItem("" if v is None else fmt_money(v))
                cell.setTextAlignment(ALIGN_RIGHT)
                cf = cell.font()
                cf.setBold(kind in ("total", "cash"))
                cell.setFont(cf)
                if v is not None and v < 0:
                    cell.setForeground(NEGATIVE)
                elif kind == "memo":
                    cell.setForeground(MUTED)
                t.setItem(r, 1 + c, cell)
        t.resizeColumnsToContents()
        t.setColumnWidth(0, max(t.columnWidth(0), 300))
        low = min(d.cash_end) if d.cash_end else 0
        when = d.labels[d.cash_end.index(low)] if d.cash_end else ""
        self.head.setText(
            f"<b>Next 12 months</b> — cash now <b>{fmt_money(d.cash_start)}</b> (businesses "
            f"{fmt_money(d.cash_start_business)}, personal {fmt_money(d.cash_start_personal)}) · net cash flow "
            f"<b>{fmt_money(sum(d.net))}</b> · cash in 12 months <b>{fmt_money(d.cash_end[-1] if d.cash_end else 0)}"
            f"</b> · lowest <b>{fmt_money(low)}</b> ({when})")
