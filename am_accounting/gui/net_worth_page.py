"""Net Worth (beside Portfolio Summary and Deal Pipeline): your personal net worth on one page — real estate from the
portfolio at your share, plus your own assets and liabilities, kept as you type — with the statement previewed beside
it and saved as a PDF and an Excel file in the folder you pick for statements."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import longpath as lpth
from .. import personal as pers
from ..money import fmt_money, parse_money
from .common import ALIGN_RIGHT, NEGATIVE, POSITIVE, error, info

ROLE = Qt.ItemDataRole.UserRole


class _Entries(QGroupBox):
    """Rows of kind / description / amount, added and removed by hand (a framed box with its title on the frame)."""

    def __init__(self, title: str, kinds: list[str], on_change, parent=None, *, on_auto=None, buttons: bool = True):
        super().__init__(title, parent)
        self.kinds, self.on_change, self.on_auto = kinds, on_change, on_auto
        self.n_auto = 0                       # rows at the top filled in from the portfolio and the books
        lay = QVBoxLayout(self)
        if buttons:
            head = QHBoxLayout()
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
            if desc or amt:                    # (a line with nothing in it isn't kept)
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
        self.cards = {k: Card(t) for k, t in (("cash", "Cash balance"), ("assets", "Total assets"),
                                              ("liabs", "Total liabilities"), ("worth", "Net worth"),
                                              ("income", "Income this month"), ("spent", "Spent this month"))}
        for c in self.cards.values():
            cards.addWidget(c)
        lay.addLayout(cards)
        # projected (the budget) against actual: this week, month and year
        self.plan = QTableWidget(4, 7)
        self.plan.setHorizontalHeaderLabels(["", "Week — projected", "Week — actual", "Month — projected",
                                             "Month — actual", "Year — projected", "Year — actual"])
        self.plan.verticalHeader().hide()
        self.plan.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.plan.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.plan.setToolTip("Projected = your budget (a week is 12/52 of a month; the year is the whole year's "
                             "budget); actual = so far, from your statements and what you entered. Cash projected = "
                             "your cash at the start (as kept that day) plus what the budget says you'd save.")
        for c in range(1, 7):                 # headings over their numbers: right-aligned like them
            self.plan.horizontalHeaderItem(c).setTextAlignment(ALIGN_RIGHT)
        self.plan.horizontalHeaderItem(0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        rh = self.plan.verticalHeader().defaultSectionSize()
        self.plan.setFixedHeight(rh * 4 + self.plan.horizontalHeader().sizeHint().height() + 4)
        plan_box = QGroupBox("Projected (your budget) vs actual — this week, month and year")
        pv = QVBoxLayout(plan_box)
        pv.addWidget(self.plan)
        lay.addWidget(plan_box)
        split = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        addrow = QHBoxLayout()
        addrow.addStretch(1)
        add = QPushButton("Add…")
        add.setObjectName("primary")
        add.setToolTip("Add an asset or a liability that isn't on another tab — your home and its mortgage, a "
                       "vehicle, a car loan …")
        add.clicked.connect(lambda *_: self.add_item())
        addrow.addWidget(add)
        rem = QPushButton("Remove")
        rem.setToolTip("Remove the line selected (lines filled in from the other tabs stay)")
        rem.clicked.connect(lambda *_: self.remove_item())
        addrow.addWidget(rem)
        ll.addLayout(addrow)
        self.assets = _Entries("Assets", pers.TYPED_ASSET_KINDS, self._preview, on_auto=self._auto_clicked,
                               buttons=False)
        ll.addWidget(self.assets, 3)
        self.liabs = _Entries("Liabilities", pers.TYPED_LIABILITY_KINDS, self._preview, on_auto=self._auto_clicked,
                              buttons=False)
        ll.addWidget(self.liabs, 2)
        self._last_box = self.assets
        for box in (self.assets, self.liabs):
            box.table.currentCellChanged.connect(lambda *_, b=box: setattr(self, "_last_box", b))
        auto_hint = QLabel("<b>Bold</b> lines come from the Bank Accounts and Investments tabs, the portfolio and your "
                           "companies' books — double-click a property to change your share of it. Add anything else "
                           "(your home, vehicles …) below them.")
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
        self.holdco = pers.holdco_part(pf, self.saved, self.as_of)
        self._fill_auto()
        for e in self.saved.assets:
            self.assets.add(e)
        for e in self.saved.liabilities:
            self.liabs.add(e)
        self._show_folder()
        self._loading = False
        self._preview()

    def refresh(self) -> None:
        """Re-read the portfolio (values, loans, holdings) for the date at the top, and your entries as saved (they
        may have changed elsewhere — e.g. cash moved by a shareholder loan recorded on Shareholder Loans)."""
        self._loading = True
        self._month_cache = None
        self._plan_cache = None
        self.as_of = self._as_of()
        self.saved = pers.load(self.pf)
        for box, rows in ((self.assets, self.saved.assets), (self.liabs, self.saved.liabilities)):
            box.clear_entries()
            for e in rows:
                box.add(e)
        self.props = pers.properties(self.pf, self.saved, self.as_of)
        self.loans = self._loans()
        self.holdco = pers.holdco_part(self.pf, self.saved, self.as_of)
        self._fill_auto()
        self._show_folder()
        self._loading = False
        self._preview()

    # ---- your holding company (picked in Personal → Your Name & Folders) ----
    def _holdco_setting(self) -> tuple[str, float]:
        s = pers.load(self.pf)
        return s.holdco, s.holdco_pct

    def _recompute(self) -> None:
        """Your shares of the properties follow your share of your holding company (unless you changed them)."""
        self.props = pers.properties(self.pf, self.saved, self.as_of)
        self.holdco = pers.holdco_part(self.pf, self.saved, self.as_of)
        self._fill_auto()
        self._preview()

    # ---- adding and removing your own lines ----
    def add_item(self, *, run=None) -> bool:
        """Add an asset or a liability (``run`` for tests takes the dialog)."""
        dlg = AddItemDialog(self)
        if not (run(dlg) if run is not None else dlg.exec()):
            return False
        group, e = dlg.item()
        box = self.assets if group == "asset" else self.liabs
        box.add(e)
        box.table.setCurrentCell(box.table.rowCount() - 1, 1)
        return True

    def remove_item(self) -> bool:
        """Remove the line selected in Assets or Liabilities (your own lines only)."""
        for box in sorted((self.assets, self.liabs), key=lambda b: b is not self._last_box):
            r = box.table.currentRow()
            if r >= box.n_auto and box.table.selectionModel().hasSelection():
                box.remove()
                return True
        return False

    # ---- the lines filled in for you ----
    def _fill_auto(self) -> None:
        """Your properties, your holding company and your shareholder loans, as lines in Assets and Liabilities."""
        from .. import personal_accounts as pacc

        a_rows, l_rows = [], []
        acct_a, acct_l = pacc.entries(self.pf)            # Bank Accounts and Investments tabs
        for e in acct_a:
            a_rows.append((e.kind, e.description, e.amount, "From the Bank Accounts / Investments tab", None))
        for e in acct_l:
            l_rows.append((e.kind, e.description, e.amount, "From the Bank Accounts tab", None))
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
        return pers.Saved(pers.load(self.pf).name, self.assets.entries(), self.liabs.entries(), shares, "",
                          *self._holdco_setting())

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
        self.page.setHtml(pers.html(st))
        self.when.setText(f"  as of {self.as_of:%b} {self.as_of.day}, {self.as_of.year}")
        self.cards["assets"].set(fmt_money(st.total_assets), f"real estate {fmt_money(st.real_estate)}")
        self.cards["liabs"].set(fmt_money(st.total_liabilities), f"mortgages {fmt_money(st.real_estate_debt)}")
        self.cards["worth"].set(fmt_money(st.net_worth), "assets − liabilities",
                                NEGATIVE if st.net_worth < 0 else None)
        cash = [e for e in st.assets if e.kind in pers.CASH_KINDS]
        self.cards["cash"].set(fmt_money(sum(e.amount for e in cash)),
                               f"{len(cash)} account(s) — Bank Accounts tab")
        self._show_budget()
        self._fill_plan()
        self.save()                         # kept as you type
        if self.as_of == date.today():      # today's figures: one snapshot a day
            pers.record(self.pf, st)
        self._fill_charts()

    def _month(self):
        """This month's income and spending from the personal budget (read when the page opens or refreshes)."""
        from .. import personal_finance as pfi

        if getattr(self, "_month_cache", None) is None:
            today = date.today()
            try:
                b = pfi.budget(self.pf, today.year)
                m = today.month
                self._month_cache = (b.total(pfi.INCOME, m), b.total(pfi.EXPENSE, m), b.budget_total(pfi.INCOME),
                                     b.budget_total(pfi.EXPENSE), b.total(pfi.INCOME), b.total(pfi.EXPENSE))
            except Exception:  # noqa: BLE001
                self._month_cache = (0, 0, 0, 0, 0, 0)
        return self._month_cache

    def _fill_plan(self) -> None:
        from .. import personal_finance as pfi

        if getattr(self, "_plan_cache", None) is None:
            try:
                self._plan_cache = pfi.periods(self.pf)
            except Exception:  # noqa: BLE001
                self._plan_cache = []
        ps = self._plan_cache
        t = self.plan
        labels = ["Cash", "Income", "Spending", "Saved (income − spending)"]
        for r, lab in enumerate(labels):
            it = QTableWidgetItem(lab)
            f = it.font()
            f.setBold(True)
            it.setFont(f)
            t.setItem(r, 0, it)
            for i, p in enumerate(ps):
                proj, act = {0: (p.cash_proj, p.cash_now), 1: (p.income_proj, p.income),
                             2: (p.spend_proj, p.spend), 3: (p.saved_proj, p.saved)}[r]
                for c, v in ((1 + 2 * i, proj), (2 + 2 * i, act)):
                    cell = QTableWidgetItem("—" if v is None else fmt_money(v))
                    cell.setTextAlignment(ALIGN_RIGHT)
                    if c % 2 == 0 and proj is not None and v is not None and r:   # actual vs projected
                        good = v >= proj if r in (1, 3) else v <= proj
                        if v != proj:
                            cell.setForeground(POSITIVE if good else NEGATIVE)
                    if r == 0 and c % 2 == 1 and v is None:
                        cell.setToolTip("Your cash at the start isn't known yet (it's kept each day you open "
                                        "Net Worth)")
                    t.setItem(r, c, cell)

    def _show_budget(self) -> None:
        inc, out, binc, bout, inc_y, out_y = self._month()
        self.cards["income"].set(fmt_money(inc), (f"budget {fmt_money(binc)} · " if binc else "")
                                 + f"year {fmt_money(inc_y)}")
        self.cards["spent"].set(fmt_money(out), (f"budget {fmt_money(bout)} · " if bout else "")
                                + f"year {fmt_money(out_y)}", NEGATIVE if bout and out > bout else None)

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

    # ---- your settings (name and folders) ----
    def _show_folder(self) -> None:
        """(the folders are shown in Personal Settings)"""

    def settings(self, *, run=None):
        """Personal Settings: your name and the folders. ``run`` (for tests) takes the dialog instead of showing it."""
        dlg = PersonalSettingsDialog(self.pf, self)
        ok = run(dlg) if run is not None else dlg.exec()
        self.refresh()
        return ok

    def generate(self, *, quiet: bool = False) -> list[Path]:
        from .reports_tab import write_pdf

        self.save()
        out = pers.statements_folder(self.pf)
        if out is None and not quiet:
            p = QFileDialog.getExistingDirectory(self, "Folder for net worth statements", "")
            if p:
                pers.set_statements_folder(self.pf, p)
                out = Path(p)
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


class PersonalSettingsDialog(QDialog):
    """Your name (as it goes on the statement), your personal folder and where net worth statements are saved."""

    def __init__(self, pf, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QFormLayout

        self.pf = pf
        self.setWindowTitle("Personal Settings — Your Name, Holding Company and Folders")
        self.resize(720, 0)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(pers.load(pf).name)
        self.name.setPlaceholderText("Your full name, as it should appear on your net worth statement")
        form.addRow("Your name:", self.name)
        lay.addLayout(form)
        fbox = QGroupBox("Personal folder — for your own documents: net worth statements, T1s, T4s, notices of "
                         "assessment, ID …")
        fv = QVBoxLayout(fbox)
        self.folder_label = QLabel()
        self.folder_label.setWordWrap(True)
        fv.addWidget(self.folder_label)
        fb = QHBoxLayout()
        for text, slot, tip in (("Change Folder…", self.choose_folder, "Pick a different folder to use as your "
                                                                         "personal folder"),
                                ("Create Its Subfolders", self.setup_folder, "Make Net Worth Statements, Income Tax "
                                                                             "(T1, T4, NOA) … inside it, where "
                                                                             "they aren't there yet"),
                                ("Open in Explorer", self.open_folder, "Open the folder")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, f=slot: f())
            fb.addWidget(b)
        fb.addStretch(1)
        fv.addLayout(fb)
        lay.addWidget(fbox)
        hbox = QGroupBox("Your holding company — the company you own your properties through; its own cash, other "
                         "assets and liabilities count in your net worth at your share")
        hv = QVBoxLayout(hbox)
        hr = QHBoxLayout()
        self.holdco_box = QComboBox()
        self.holdco_box.setMinimumWidth(300)
        self.holdco_box.activated.connect(lambda *_: self.pick_holdco())
        hr.addWidget(self.holdco_box, 1)
        hr.addWidget(QLabel("Your share:"))
        from PySide6.QtWidgets import QDoubleSpinBox

        saved = pers.load(pf)
        self.holdco = saved.holdco
        self.holdco_pct = QDoubleSpinBox()
        self.holdco_pct.setRange(0.01, 100)
        self.holdco_pct.setDecimals(2)
        self.holdco_pct.setSuffix(" %")
        self.holdco_pct.setValue(saved.holdco_pct)
        self.holdco_pct.valueChanged.connect(lambda *_: self._show_holdco())
        hr.addWidget(self.holdco_pct)
        hv.addLayout(hr)
        self.holdco_label = QLabel()
        self.holdco_label.setObjectName("hint")
        self.holdco_label.setWordWrap(True)
        hv.addWidget(self.holdco_label)
        lay.addWidget(hbox)
        obox = QGroupBox("Net worth statements are saved to")
        ov = QVBoxLayout(obox)
        self.out_label = QLabel()
        self.out_label.setWordWrap(True)
        ov.addWidget(self.out_label)
        ob = QHBoxLayout()
        for text, slot, tip in (("Change Folder…", self.choose_output, "Pick the folder the statements go in"),
                                ("Use the Personal Folder", self.reset_output,
                                 "Save them in the personal folder's Net Worth Statements")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(lambda *_, f=slot: f())
            ob.addWidget(b)
        ob.addStretch(1)
        ov.addLayout(ob)
        lay.addWidget(obox)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        lay.addWidget(self.note)
        btns = QHBoxLayout()
        btns.addStretch(1)
        ok = QPushButton("Save")
        ok.setObjectName("primary")
        ok.clicked.connect(lambda *_: self.save())
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self._show()
        self._fill_holdco_box()

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
        if self.holdco and b.findData(self.holdco) < 0:
            from ..companies import _read_name

            b.addItem(_read_name(self.holdco) or Path(self.holdco).stem, self.holdco)
        b.addItem("Another company file…", "other")
        b.setCurrentIndex(max(0, b.findData(self.holdco)))
        b.blockSignals(False)
        self.holdco_pct.setEnabled(bool(self.holdco))
        self._show_holdco()

    def pick_holdco(self, path: str | None = None) -> None:
        """Pick your holding company (``path`` for tests; '' for none)."""
        data = path if path is not None else self.holdco_box.currentData()
        if data == "other":
            data, _ = QFileDialog.getOpenFileName(self, "Your holding company's file", "",
                                                  "Company files (*.amdb);;All files (*)")
            if not data:
                self._fill_holdco_box()
                return
        self.holdco = data or ""
        self._fill_holdco_box()

    def _show_holdco(self) -> None:
        if not self.holdco:
            self.holdco_label.setText("None picked.")
            return
        s = pers.load(self.pf)
        s.holdco, s.holdco_pct = self.holdco, self.holdco_pct.value()
        hc = pers.holdco_part(self.pf, s)
        if hc.error:
            self.holdco_label.setText(f"<span style='color:#b3261e'>{hc.name}: {hc.error}</span>")
            return
        self.holdco_label.setText(
            f"{hc.name}: cash {fmt_money(hc.cash)} · other assets {fmt_money(hc.other_assets)} · liabilities "
            f"{fmt_money(hc.liabilities)} → your {hc.share_pct:g}%: <b>{fmt_money(hc.my_net)}</b>"
            + (f" (its {fmt_money(hc.left_out)} invested in the properties counts in your real estate instead)"
               if hc.left_out else ""))

    def _show(self) -> None:
        f = pers.folder(self.pf)
        self.folder_label.setText(f"<b>{f}</b>" if f else "<span style='color:#b3261e'>Not chosen yet</span> — "
                                  "click Change Folder… to pick one")
        out = pers.statements_folder(self.pf)
        self.out_label.setText(f"<b>{out}</b>" if out else "<span style='color:#b3261e'>Not chosen yet</span> — "
                               "pick a personal folder, or a folder for them")

    def choose_folder(self, path: str | None = None) -> Path | None:
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Pick your personal folder", str(pers.folder(self.pf) or ""))
        if not path:
            return None
        pers.set_folder(self.pf, path)
        try:
            n = pers.create(Path(path))
        except OSError as e:
            error(self, "Couldn't make the folders", str(e))
            n = 0
        self._show()
        self.note.setText(f"Personal folder set to {path}" + (f" — {n} subfolder(s) made in it." if n else "."))
        return Path(path)

    def setup_folder(self) -> int:
        f = pers.folder(self.pf) or self.choose_folder()
        if f is None:
            return 0
        try:
            n = pers.create(f)
        except OSError as e:
            error(self, "Couldn't make the folders", str(e))
            return 0
        self.note.setText(f"{n} subfolder(s) made in {f}." if n else f"All its subfolders are already in {f}.")
        return n

    def open_folder(self) -> None:
        f = pers.folder(self.pf)
        if f is not None:
            lpth.mkdir(f)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f)))

    def choose_output(self, path: str | None = None) -> Path | None:
        if path is None:
            path = QFileDialog.getExistingDirectory(self, "Folder for net worth statements",
                                                    str(pers.statements_folder(self.pf) or ""))
        if not path:
            return None
        pers.set_statements_folder(self.pf, path)
        self._show()
        self.note.setText(f"Net worth statements will be saved to {path}.")
        return Path(path)

    def reset_output(self) -> None:
        pers.set_statements_folder(self.pf, None)
        self._show()

    def save(self) -> bool:
        s = pers.load(self.pf)
        s.name = self.name.text().strip()
        if (s.holdco, s.holdco_pct) != (self.holdco, self.holdco_pct.value()):
            s.holdco, s.holdco_pct = self.holdco, self.holdco_pct.value()
            s.shares = {}               # your shares of its properties follow your share of it again
        pers.save(self.pf, s)
        self.accept()
        return True


class AddItemDialog(QDialog):
    """An asset or a liability of your own (not on another tab): what it is, a description and the amount."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QDialogButtonBox, QFormLayout

        self.setWindowTitle("Add to Your Net Worth")
        self.resize(460, 0)
        form = QFormLayout(self)
        self.group = QComboBox()
        self.group.addItem("An asset (something you own)", "asset")
        self.group.addItem("A liability (something you owe)", "liability")
        self.group.currentIndexChanged.connect(lambda *_: self._kinds())
        form.addRow("This is:", self.group)
        self.kind = QComboBox()
        self.kind.setEditable(True)
        form.addRow("What:", self.kind)
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("e.g. 12 Oak St, 2022 Honda CR-V, RBC mortgage")
        form.addRow("Description:", self.desc)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("$0.00")
        form.addRow("Amount:", self.amount)
        hint = QLabel("Cash, investments, cards and lines of credit go on the Bank Accounts and Investments tabs; "
                      "properties in the portfolio and your companies are filled in for you.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        form.addRow(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._ok)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._kinds()

    def _kinds(self) -> None:
        self.kind.clear()
        self.kind.addItems(pers.TYPED_ASSET_KINDS if self.group.currentData() == "asset"
                           else pers.TYPED_LIABILITY_KINDS)

    def _ok(self) -> None:
        raw = self.amount.text().strip()
        v = parse_money(raw) if raw else 0
        if v is None:
            error(self, "Amount", "That isn't an amount.")
            return
        self.accept()

    def item(self) -> tuple[str, pers.Entry]:
        raw = self.amount.text().strip()
        return self.group.currentData(), pers.Entry(self.kind.currentText().strip(), self.desc.text().strip(),
                                                    (parse_money(raw) or 0) if raw else 0)
