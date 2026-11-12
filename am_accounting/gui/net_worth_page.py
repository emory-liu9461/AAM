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

    def __init__(self, title: str, kinds: list[str], on_change, parent=None, *, on_auto=None):
        super().__init__(parent)
        self.kinds, self.on_change, self.on_auto = kinds, on_change, on_auto
        self.n_auto = 0                       # rows at the top filled in from the portfolio and the books
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
        self.table.cellDoubleClicked.connect(self._double)
        lay.addWidget(self.table)

    def set_auto(self, rows: list[tuple]) -> None:
        """The rows filled in for you — (what, description, amount, tooltip, key) — at the top, not editable here
        (double-click one with a key to change it)."""
        t = self.table
        t.blockSignals(True)
        for _ in range(self.n_auto):
            t.removeRow(0)
        for r, (what, desc, amount, tip, key) in enumerate(rows):
            t.insertRow(r)
            for c, v in enumerate((what, desc, fmt_money(amount))):
                it = QTableWidgetItem(v)
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                it.setData(Qt.ItemDataRole.UserRole, key)
                it.setToolTip(tip)
                if c == 2:
                    it.setTextAlignment(ALIGN_RIGHT)
                if c == 0:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                t.setItem(r, c, it)
        self.n_auto = len(rows)
        t.blockSignals(False)

    def auto_rows(self) -> list[tuple[str, str, str]]:
        t = self.table
        return [tuple(t.item(r, c).text() for c in range(3)) for r in range(self.n_auto)]

    def _double(self, r: int, _c: int) -> None:
        if r < self.n_auto and self.on_auto:
            key = self.table.item(r, 0).data(Qt.ItemDataRole.UserRole)
            if key:
                self.on_auto(key)

    def clear_entries(self) -> None:
        while self.table.rowCount() > self.n_auto:
            self.table.removeRow(self.n_auto)

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
        if r >= self.n_auto:                  # (the rows filled in for you stay)
            self.table.removeRow(r)
            self.on_change()

    def _edited(self, it: QTableWidgetItem) -> None:
        if it.row() < self.n_auto:
            return
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
        for r in range(self.n_auto, self.table.rowCount()):
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
        hrow = QHBoxLayout()
        hrow.addWidget(QLabel("<b>Your holding company:</b>"))
        self.holdco_box = QComboBox()
        self.holdco_box.setMinimumWidth(260)
        self.holdco_box.setToolTip("The company you own your properties through: its own cash, other assets and "
                                   "liabilities count at your share (its investments in the properties are in the "
                                   "real estate below, at market value)")
        self.holdco_box.activated.connect(lambda *_: self._holdco_picked())
        hrow.addWidget(self.holdco_box, 1)
        hrow.addWidget(QLabel("Your share:"))
        from PySide6.QtWidgets import QDoubleSpinBox

        self.holdco_pct = QDoubleSpinBox()
        self.holdco_pct.setRange(0.01, 100)
        self.holdco_pct.setDecimals(2)
        self.holdco_pct.setSuffix(" %")
        self.holdco_pct.setValue(self.saved.holdco_pct)
        self.holdco_pct.valueChanged.connect(lambda *_: self._holdco_share())
        hrow.addWidget(self.holdco_pct)
        ll.addLayout(hrow)
        self.holdco_label = QLabel()
        self.holdco_label.setObjectName("hint")
        self.holdco_label.setWordWrap(True)
        ll.addWidget(self.holdco_label)
        self.assets = _Entries("Assets", pers.ASSET_KINDS, self._preview, on_auto=self._auto_clicked)
        ll.addWidget(self.assets, 3)
        self.liabs = _Entries("Liabilities", pers.LIABILITY_KINDS, self._preview, on_auto=self._auto_clicked)
        ll.addWidget(self.liabs, 2)
        auto_hint = QLabel("<b>Bold</b> lines come from the portfolio and your companies' books — double-click a "
                           "property to change your share of it. Add your own lines below them.")
        auto_hint.setObjectName("hint")
        auto_hint.setWordWrap(True)
        ll.addWidget(auto_hint)
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
        self._fill_holdco_box()
        self.holdco = pers.holdco_part(pf, self.saved, self.as_of)
        self._fill_auto()
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
            box.clear_entries()
            for e in rows:
                box.add(e)
        self.props = pers.properties(self.pf, self.saved, self.as_of)
        self.loans = self._loans()
        self._fill_holdco_box()
        self.holdco_pct.blockSignals(True)
        self.holdco_pct.setValue(self.saved.holdco_pct)
        self.holdco_pct.blockSignals(False)
        self.holdco = pers.holdco_part(self.pf, self.saved, self.as_of)
        self._fill_auto()
        self._show_folder()
        self._loading = False
        self._preview()

    # ---- your holding company ----
    def _fill_holdco_box(self) -> None:
        from .. import shareholder_loans as shl

        b = self.holdco_box
        b.blockSignals(True)
        b.clear()
        b.addItem("— none —", "")
        try:
            comps = shl.companies(self.pf)
        except Exception:  # noqa: BLE001
            comps = []
        owners = {self.pf.owner_of(h) for h in self.pf.holdings()}
        for name, path in sorted(comps, key=lambda c: (not any(pers._same(c[1], o) for o in owners), c[0].lower())):
            b.addItem(name, path)
        if self.saved.holdco and b.findData(self.saved.holdco) < 0:
            from ..companies import _read_name

            b.addItem(_read_name(self.saved.holdco) or Path(self.saved.holdco).stem, self.saved.holdco)
        b.addItem("Another company file…", "other")
        b.setCurrentIndex(max(0, b.findData(self.saved.holdco)))
        b.blockSignals(False)
        self.holdco_pct.setEnabled(bool(self.saved.holdco))

    def _holdco_picked(self, path: str | None = None) -> None:
        """Pick your holding company (``path`` for tests)."""
        data = path if path is not None else self.holdco_box.currentData()
        if data == "other":
            data, _ = QFileDialog.getOpenFileName(self, "Your holding company's file", "",
                                                  "Company files (*.amdb);;All files (*)")
            if not data:
                self._fill_holdco_box()
                return
        self.saved = self.current()
        self.saved.holdco = data or ""
        pers.save(self.pf, self.saved)
        self._fill_holdco_box()
        self._recompute()

    def _holdco_share(self) -> None:
        if getattr(self, "_loading", True):
            return
        self.saved = self.current()
        pers.save(self.pf, self.saved)
        self._recompute()

    def _recompute(self) -> None:
        """Your shares of the properties follow your share of your holding company (unless you changed them)."""
        self.props = pers.properties(self.pf, self.saved, self.as_of)
        self.holdco = pers.holdco_part(self.pf, self.saved, self.as_of)
        self._fill_auto()
        self._preview()

    def _show_holdco(self, st) -> None:
        hc = st.holdco
        if hc is None:
            self.holdco_label.setText("Pick the company you own your properties through, and its own money counts "
                                      "too.")
        elif hc.error:
            self.holdco_label.setText(f"<span style='color:#b3261e'>{hc.name}: {hc.error}</span>")
        else:
            self.holdco_label.setText(
                f"{hc.name}: cash {fmt_money(hc.cash)} · other assets {fmt_money(hc.other_assets)} · liabilities "
                f"{fmt_money(hc.liabilities)} → your {hc.share_pct:g}%: <b>{fmt_money(hc.my_net)}</b>"
                + (f" (its {fmt_money(hc.left_out)} invested in the properties is in the real estate below)"
                   if hc.left_out else ""))

    # ---- the lines filled in for you ----
    def _fill_auto(self) -> None:
        """Your properties, your holding company and your shareholder loans, as lines in Assets and Liabilities."""
        a_rows, l_rows = [], []
        for p in self.props:
            tip = (f"{p.name}: market value {fmt_money(p.value)} (100%), your share {p.share_pct:g}% — double-click "
                   "to change your share")
            a_rows.append(("Real estate", f"{p.name} — your {p.share_pct:g}% of {fmt_money(p.value)}", p.my_value, tip,
                           ("share", p.holding_id)))
            if p.debt:
                l_rows.append(("Mortgage", f"{p.name} — your {p.share_pct:g}% of {fmt_money(p.debt)}"
                               + (f" ({p.lenders})" if p.lenders else ""), p.my_debt,
                               "From Financing & Cap Table — double-click to change your share",
                               ("share", p.holding_id)))
        hc = getattr(self, "holdco", None)
        if hc is not None and not hc.error:
            if hc.my_assets:
                a_rows.append(("Your holding company", f"{hc.name} — cash and other assets, your {hc.share_pct:g}%",
                               hc.my_assets, "From its books (its investments in the properties are in Real estate)",
                               None))
            if hc.my_liabilities:
                l_rows.append(("Your holding company", f"{hc.name} — liabilities, your {hc.share_pct:g}%",
                               hc.my_liabilities, "From its books", None))
        for ln in getattr(self, "loans", []):
            if ln.owed_to_you > 0:
                a_rows.append(("Shareholder loan", f"to {ln.company}", ln.owed_to_you,
                               "From the company's books (Shareholder Loans tab)", None))
            elif ln.owed_to_you < 0:
                l_rows.append(("Shareholder loan", f"owed to {ln.company}", -ln.owed_to_you,
                               "From the company's books (Shareholder Loans tab)", None))
        self.assets.set_auto(a_rows)
        self.liabs.set_auto(l_rows)

    def _auto_clicked(self, key) -> None:
        if key and key[0] == "share":
            p = next((x for x in self.props if x.holding_id == key[1]), None)
            if p is None:
                return
            from PySide6.QtWidgets import QInputDialog

            v, ok = QInputDialog.getDouble(self, "Your share", f"Your share of {p.name} (%):", p.share_pct, 0, 100, 4)
            if ok:
                self.set_share(p.holding_id, v)

    def set_share(self, holding_id: int, pct: float) -> None:
        p = next((x for x in self.props if x.holding_id == holding_id), None)
        if p is None or not 0 <= pct <= 100:
            return
        p.share_pct = pct
        self._fill_auto()
        self._preview()

    # ---- the statement ----
    def current(self) -> pers.Saved:
        shares = {p.holding_id: p.share_pct for p in self.props if abs(p.share_pct - p.default_pct) > 1e-9}
        return pers.Saved(self.name.text().strip(), self.assets.entries(), self.liabs.entries(), shares, "",
                          self.saved.holdco, self.holdco_pct.value())

    def _loans(self) -> list:
        """Shareholder loans with your companies, from their books (read when the page opens, not as you type)."""
        from .. import shareholder_loans as shl

        try:
            return [p for p in shl.positions(self.pf, self.as_of) if p.owed_to_you]
        except Exception:  # noqa: BLE001
            return []

    def statement(self) -> pers.Statement:
        return pers.statement(self.pf, self.current(), self.as_of, self.props, getattr(self, "loans", []),
                              getattr(self, "holdco", None))

    def _preview(self) -> None:
        if getattr(self, "_loading", True) or not hasattr(self, "props"):
            return
        st = self.statement()
        self._show_holdco(st)
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
