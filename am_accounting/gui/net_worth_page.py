"""Net Worth (beside Portfolio Summary and Deal Pipeline): your personal net worth on one page — real estate from the
portfolio at your share, plus your own assets and liabilities, kept as you type — with the statement previewed beside
it and saved as a PDF and an Excel file in the folder you pick for statements."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import longpath as lpth
from .. import personal as pers
from ..money import fmt_money, parse_money
from .common import ALIGN_RIGHT, NEGATIVE, error, info

ROLE = Qt.ItemDataRole.UserRole


class _Entries(QWidget):
    """Rows of kind / description / amount, added and removed by hand."""

    def __init__(self, title: str, kinds: list[str], on_change, parent=None):
        super().__init__(parent)
        self.kinds, self.on_change = kinds, on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(QLabel(f"<b>{title}</b>"))
        head.addStretch(1)
        for text, slot in (("Add", self.add), ("Remove", self.remove)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, s=slot: s())
            head.addWidget(b)
        lay.addLayout(head)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["What", "Description", "Amount"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0, 250)
        self.table.setColumnWidth(2, 130)
        self.table.verticalHeader().hide()
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table)

    def add(self, e: pers.Entry | None = None) -> None:
        e = e or pers.Entry(self.kinds[0])
        t = self.table
        r = t.rowCount()
        t.blockSignals(True)
        t.insertRow(r)
        kind = QComboBox()
        kind.setEditable(True)
        kind.addItems(self.kinds)
        kind.setCurrentText(e.kind)
        kind.currentTextChanged.connect(lambda *_: self.on_change())
        t.setCellWidget(r, 0, kind)
        t.setItem(r, 1, QTableWidgetItem(e.description))
        amt = QTableWidgetItem(fmt_money(e.amount) if e.amount else "")
        amt.setTextAlignment(ALIGN_RIGHT)
        t.setItem(r, 2, amt)
        t.blockSignals(False)
        self.on_change()

    def remove(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.on_change()

    def _edited(self, it: QTableWidgetItem) -> None:
        if it.column() == 2:
            v = parse_money(it.text()) if it.text().strip() else None
            self.table.blockSignals(True)
            if v is not None:
                it.setText(fmt_money(v))
            it.setForeground(NEGATIVE if v is None and it.text().strip() else self.palette().text().color())
            it.setToolTip("Not an amount" if v is None and it.text().strip() else "")
            self.table.blockSignals(False)
        self.on_change()

    def entries(self) -> list[pers.Entry]:
        out = []
        for r in range(self.table.rowCount()):
            kind = self.table.cellWidget(r, 0).currentText().strip()
            desc = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            raw = self.table.item(r, 2).text() if self.table.item(r, 2) else ""
            amt = parse_money(raw) if raw.strip() else 0
            if kind or desc or amt:
                out.append(pers.Entry(kind, desc, amt or 0))
        return out


class NetWorthPage(QWidget):
    """The Net Worth page. ``as_of`` is a callable giving the date (the As of box at the top)."""

    def __init__(self, pf, parent=None, *, as_of=None):
        super().__init__(parent)
        from .asset_mgmt import Card

        self.pf = pf
        self._as_of = as_of if callable(as_of) else (lambda d=as_of: d or date.today())
        self.as_of = self._as_of()
        self.saved = pers.load(pf)
        self.made: list[Path] = []
        self._loading = True
        lay = QVBoxLayout(self)
        head = QHBoxLayout()
        title = QLabel("Personal Net Worth")
        f = title.font()
        f.setPointSizeF(f.pointSizeF() * 1.6)
        f.setBold(True)
        title.setFont(f)
        head.addWidget(title)
        self.when = QLabel()
        self.when.setObjectName("hint")
        head.addWidget(self.when)
        head.addStretch(1)
        go = QPushButton("Generate Statement")
        go.setObjectName("primary")
        go.setToolTip("Save it as a PDF and an Excel file in the folder for statements (earlier ones move to its "
                      "Archive)")
        go.clicked.connect(lambda *_: self.generate())
        head.addWidget(go)
        opn = QPushButton("Open Statements Folder")
        opn.clicked.connect(lambda *_: self.open_statements())
        head.addWidget(opn)
        lay.addLayout(head)
        cards = QHBoxLayout()
        self.cards = {k: Card(t) for k, t in (("assets", "Total assets"), ("liabs", "Total liabilities"),
                                              ("worth", "Net worth"), ("re", "Real estate equity"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        top = QHBoxLayout()
        top.addWidget(QLabel("Your name:"))
        self.name = QLineEdit(self.saved.name)
        self.name.setPlaceholderText("As it should appear on the statement")
        self.name.textChanged.connect(lambda *_: self._preview())
        top.addWidget(self.name, 1)
        lay.addLayout(top)
        # the personal folder
        fbox = QGroupBox("Personal folder")
        fl = QHBoxLayout(fbox)
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        fl.addWidget(self.folder_label, 1)
        for text, slot, tip in (("Choose…", self.choose_folder, "The folder for your own documents: net worth "
                                                                  "statements, T1s, T4s, notices of assessment …"),
                                ("Set Up Folders", self.setup_folder, "Make its folders that aren't there yet"),
                                ("Open", self.open_folder, "Open it")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            fl.addWidget(b)
        sl = QHBoxLayout()
        sl.addWidget(QLabel("Statements saved to:"))
        self.out_label = QLabel()
        self.out_label.setWordWrap(True)
        sl.addWidget(self.out_label, 1)
        for text, slot, tip in (("Change…", self.choose_output, "Pick the folder the net worth statements go in"),
                                ("Use Personal Folder", self.reset_output,
                                 "Save them in the personal folder's Net Worth Statements")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, s_=slot: s_())
            sl.addWidget(b)
        lay.addWidget(fbox)
        lay.addLayout(sl)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(QLabel("<b>Real estate from the portfolio</b> — set <b>Your share</b> when you own only part of "
                            "the holding company"))
        self.re = QTableWidget(0, 6)
        self.re.setHorizontalHeaderLabels(["Property", "Market value (100%)", "Mortgages (100%)", "Your share %",
                                           "Your equity", "Lender(s)"])
        self.re.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.re.verticalHeader().hide()
        self.re.itemChanged.connect(self._share_edited)
        ll.addWidget(self.re, 2)
        self.assets = _Entries("Other assets", pers.ASSET_KINDS, self._preview)
        ll.addWidget(self.assets, 2)
        self.liabs = _Entries("Liabilities", pers.LIABILITY_KINDS, self._preview)
        ll.addWidget(self.liabs, 2)
        split.addWidget(left)
        from PySide6.QtWidgets import QScrollArea, QTabWidget

        from .net_worth_charts import TimeChart

        self.right = QTabWidget()
        self.page = QTextBrowser()
        self.right.addTab(self.page, "Statement")
        trends = QWidget()
        tl = QVBoxLayout(trends)
        note = QLabel("Your figures are kept each day you update this page, so these grow over time.")
        note.setObjectName("hint")
        note.setWordWrap(True)
        tl.addWidget(note)
        self.chart_worth = TimeChart("Net worth over time")
        self.chart_cash = TimeChart("Cash balance over time")
        self.chart_assets = TimeChart("Assets by type", stacked=True)
        for c in (self.chart_worth, self.chart_cash, self.chart_assets):
            tl.addWidget(c, 1)
        area = QScrollArea()
        area.setWidget(trends)
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.right.addTab(area, "Trends")
        split.addWidget(self.right)
        split.setSizes([680, 500])
        lay.addWidget(split, 1)
        hint = QLabel("What you enter is kept as you type. Real estate comes from the portfolio (market value from "
                      "the latest valuation, mortgages from Financing & Cap Table) — refreshed each time you open this "
                      "page.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.props = pers.properties(pf, self.saved, self.as_of)
        self.loans = self._loans()
        self._fill_re()
        for e in self.saved.assets:
            self.assets.add(e)
        for e in self.saved.liabilities:
            self.liabs.add(e)
        if not self.saved.assets:
            for k in ("Cash and bank accounts", "RRSP / RRIF", "TFSA", "Principal residence"):
                self.assets.add(pers.Entry(k))
        if not self.saved.liabilities:
            for k in ("Mortgage on principal residence", "Lines of credit", "Credit cards"):
                self.liabs.add(pers.Entry(k))
        self._show_folder()
        self._loading = False
        self._preview()

    def refresh(self) -> None:
        """Re-read the portfolio (values, loans, holdings) for the date at the top, and your entries as saved (they
        may have changed elsewhere — e.g. cash moved by a shareholder loan recorded on Shareholder Loans)."""
        self._loading = True
        self.as_of = self._as_of()
        self.saved = pers.load(self.pf)
        for box, rows in ((self.assets, self.saved.assets), (self.liabs, self.saved.liabilities)):
            box.table.setRowCount(0)
            for e in rows:
                box.add(e)
        self.props = pers.properties(self.pf, self.saved, self.as_of)
        self.loans = self._loans()
        self._fill_re()
        self._show_folder()
        self._loading = False
        self._preview()

    # ---- real estate ----
    def _fill_re(self) -> None:
        t = self.re
        t.blockSignals(True)
        t.setRowCount(len(self.props))
        for r, p in enumerate(self.props):
            vals = [p.name, fmt_money(p.value), fmt_money(p.debt), f"{p.share_pct:g}", fmt_money(p.my_equity),
                    p.lenders]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (1, 2, 3, 4):
                    it.setTextAlignment(ALIGN_RIGHT)
                if c != 3:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                else:
                    it.setToolTip("Your share of this property, % (the holding's ownership to start with)")
                t.setItem(r, c, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        t.blockSignals(False)

    def _share_edited(self, it: QTableWidgetItem) -> None:
        if it.column() != 3:
            return
        p = self.props[it.row()]
        try:
            v = float(it.text().replace("%", "").strip())
            if not 0 <= v <= 100:
                raise ValueError
            p.share_pct = v
        except ValueError:
            pass
        self.re.blockSignals(True)
        it.setText(f"{p.share_pct:g}")
        self.re.item(it.row(), 4).setText(fmt_money(p.my_equity))
        self.re.blockSignals(False)
        self._preview()

    # ---- the statement ----
    def current(self) -> pers.Saved:
        shares = {p.holding_id: p.share_pct for p in self.props}
        return pers.Saved(self.name.text().strip(), self.assets.entries(), self.liabs.entries(), shares)

    def _loans(self) -> list:
        """Shareholder loans with your companies, from their books (read when the page opens, not as you type)."""
        from .. import shareholder_loans as shl

        try:
            return [p for p in shl.positions(self.pf, self.as_of) if p.owed_to_you]
        except Exception:  # noqa: BLE001
            return []

    def statement(self) -> pers.Statement:
        return pers.statement(self.pf, self.current(), self.as_of, self.props, getattr(self, "loans", []))

    def _preview(self) -> None:
        if getattr(self, "_loading", True) or not hasattr(self, "props"):
            return
        st = self.statement()
        self.page.setHtml(pers.html(st))
        self.when.setText(f"  as of {self.as_of:%b} {self.as_of.day}, {self.as_of.year}")
        n_other = len(st.assets)
        self.cards["assets"].set(fmt_money(st.total_assets), f"real estate {fmt_money(st.real_estate)} · "
                                                             f"{n_other} other")
        self.cards["liabs"].set(fmt_money(st.total_liabilities), f"mortgages {fmt_money(st.real_estate_debt)} · "
                                                                f"{len(st.liabilities)} other")
        self.cards["worth"].set(fmt_money(st.net_worth), "assets − liabilities",
                                NEGATIVE if st.net_worth < 0 else None)
        self.cards["re"].set(fmt_money(st.real_estate - st.real_estate_debt),
                             f"{len(st.properties)} propert{'y' if len(st.properties) == 1 else 'ies'} at your share")
        self.save()                         # kept as you type
        if self.as_of == date.today():      # today's figures: one snapshot a day
            pers.record(self.pf, st)
        self._fill_charts()

    def _fill_charts(self) -> None:
        hist = pers.history(self.pf)
        dates = [h.date for h in hist]
        self.chart_worth.set_data(dates, [("Net worth", [h.net_worth for h in hist]),
                                          ("Assets", [h.assets for h in hist]),
                                          ("Liabilities", [h.liabilities for h in hist])])
        self.chart_cash.set_data(dates, [("Cash", [h.cash for h in hist])])
        kinds = list(dict.fromkeys(k for h in hist for k in h.by_kind))
        self.chart_assets.set_data(dates, [(k, [h.by_kind.get(k, 0) for h in hist]) for k in kinds])

    def save(self) -> None:
        self.saved = self.current()
        pers.save(self.pf, self.saved)

    # ---- the personal folder ----
    def _show_folder(self) -> None:
        f = pers.folder(self.pf)
        self.folder_label.setText(f"<b>{f}</b>" if f else "<span style='color:#b3261e'>Not chosen yet</span> — pick "
                                  "a folder for your own documents (net worth statements, T1s, T4s, notices of "
                                  "assessment …)")
        out = pers.statements_folder(self.pf)
        self.out_label.setText(f"<b>{out}</b>" if out else "<span style='color:#b3261e'>Not chosen yet</span> — "
                               "you'll be asked when you generate")

    def choose_output(self, path: str | None = None) -> Path | None:
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Folder for net worth statements",
                                                    str(pers.statements_folder(self.pf) or ""))
        if not path:
            return None
        pers.set_statements_folder(self.pf, path)
        self._show_folder()
        return Path(path)

    def reset_output(self) -> None:
        pers.set_statements_folder(self.pf, None)
        self._show_folder()

    def choose_folder(self, path: str | None = None) -> Path | None:
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Your personal folder", str(pers.folder(self.pf) or ""))
        if not path:
            return None
        pers.set_folder(self.pf, path)
        try:
            pers.create(Path(path))
        except OSError as e:
            error(self, "Couldn't make the folders", str(e))
        self._show_folder()
        return Path(path)

    def setup_folder(self) -> None:
        f = pers.folder(self.pf) or self.choose_folder()
        if f is None:
            return
        try:
            n = pers.create(f)
        except OSError as e:
            error(self, "Couldn't make the folders", str(e))
            return
        info(self, "Personal folder", f"{n} folder(s) made." if n else "All its folders are there.")

    def open_folder(self) -> None:
        f = pers.folder(self.pf)
        if f is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f)))

    def generate(self, *, quiet: bool = False) -> list[Path]:
        from .reports_tab import write_pdf

        self.save()
        out = pers.statements_folder(self.pf) or (None if quiet else self.choose_output())
        if out is None:
            return []
        st = self.statement()
        try:
            lpth.mkdir(out)
            pdf = out / pers.file_name(st, "pdf")
            xlsx = out / pers.file_name(st, "xlsx")
            pers.archive_older(out, [pdf, xlsx])
            write_pdf(pers.html(st), lpth.lp(pdf), font_pt=8.5)
            pers.write_xlsx(st, xlsx)
        except OSError as e:
            error(self, "Couldn't save the statement", str(e))
            return []
        self.made = [pdf, xlsx]
        self._show_folder()
        if not quiet:
            info(self, "Net worth statement", f"Saved in {out}:\n\n• {pdf.name}\n• {xlsx.name}\n\n"
                                              "Earlier statements are in its Archive.")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf)))
        return self.made

    def open_statements(self) -> None:
        out = pers.statements_folder(self.pf)
        if out is not None:
            lpth.mkdir(out)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(out)))
