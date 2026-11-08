"""Deal Pipeline: new deals, deals under due diligence and deals under contract, side by side."""

from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QProgressBar,
                               QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import deals as dl
from ..money import fmt_money
from .common import MUTED, NEGATIVE, confirm, error, info

NO_DATE = QDate(2000, 1, 1)


def _date_box(iso: str | None) -> QDateEdit:
    """A date that can be left blank ("—")."""
    w = QDateEdit()
    w.setCalendarPopup(True)
    w.setDisplayFormat("MMM d, yyyy")
    w.setMinimumDate(NO_DATE)
    w.setSpecialValueText("—")
    if iso:
        d = date.fromisoformat(iso)
        w.setDate(QDate(d.year, d.month, d.day))
    else:
        w.setDate(NO_DATE)
    return w


def _date_of(w: QDateEdit) -> str | None:
    return None if w.date() == NO_DATE else w.date().toString("yyyy-MM-dd")


def _short_date(iso: str | None) -> str:
    """"Nov 12" this year, "Nov 12, 2027" otherwise."""
    if not iso:
        return "—"
    d = date.fromisoformat(iso)
    return d.strftime("%b %d") if d.year == date.today().year else d.strftime("%b %d, %Y")


class DealDialog(QDialog):
    """A deal: details, its due-diligence checklist and notes."""

    def __init__(self, pf, deal: dl.Deal | None = None, parent=None, companies=None):
        super().__init__(parent)
        from .asset_mgmt import _money_box
        from .deal_costs_tab import DealCostsTab

        self.pf = pf
        self.deal = d = deal or dl.Deal("", received=date.today().isoformat())
        self.setWindowTitle(f"Deal – {d.name}" if d.name else "New Deal")
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # details: two columns
        det = QWidget()
        cols = QHBoxLayout(det)
        left, right = QFormLayout(), QFormLayout()
        cols.addLayout(left, 1)
        cols.addSpacing(18)
        cols.addLayout(right, 1)
        self.name = QLineEdit(d.name)
        self.name.setPlaceholderText("e.g. 45 Elm Street Apartments")
        self.address = QLineEdit(d.address)
        self.asset_type = QComboBox()
        self.asset_type.setEditable(True)
        self.asset_type.addItems(dl.ASSET_TYPES)
        self.asset_type.setCurrentText(d.asset_type)
        self.stage = QComboBox()
        self.stage.addItems(dl.STAGES)
        self.stage.setCurrentText(d.stage)
        self.price = _money_box()
        self.price.setValue(d.price_cents / 100)
        self.price.setToolTip("Asking price for a new deal; the offer / agreed price once under contract")
        self.units = QSpinBox()
        self.units.setRange(0, 100_000)
        self.units.setValue(d.units)
        self.sqft = QDoubleSpinBox()
        self.sqft.setRange(0, 1e9)
        self.sqft.setDecimals(0)
        self.sqft.setGroupSeparatorShown(True)
        self.sqft.setSuffix(" sq ft")
        self.sqft.setValue(d.sqft or 0)
        self.noi = _money_box()
        self.noi.setValue(d.noi_cents / 100)
        self.equity = _money_box()
        self.equity.setValue(d.equity_cents / 100)
        self.metrics = QLabel()
        self.metrics.setObjectName("hint")
        for label, w in (("Deal name:", self.name), ("Address:", self.address), ("Asset type:", self.asset_type),
                         ("Stage:", self.stage), ("Price:", self.price), ("Units:", self.units), ("Size:", self.sqft),
                         ("NOI (a year):", self.noi), ("Equity needed:", self.equity), ("", self.metrics)):
            left.addRow(label, w)
        self.source = QLineEdit(d.source)
        self.source.setPlaceholderText("Broker, off-market, referral…")
        self.contact = QLineEdit(d.contact)
        self.contact.setPlaceholderText("Name, phone, email")
        self.lead = QLineEdit(d.lead)
        self.lead.setPlaceholderText("Who on our side")
        self.received = _date_box(d.received)
        self.offer = _date_box(d.offer_date)
        self.dd_deadline = _date_box(d.dd_deadline)
        self.firm = _date_box(d.firm_date)
        self.closing = _date_box(d.closing_date)
        self.deposit = _money_box()
        self.deposit.setValue(d.deposit_cents / 100)
        self.next_step = QLineEdit(d.next_step)
        self.next_step.setPlaceholderText("e.g. Send LOI by Friday")
        for label, w in (("Source:", self.source), ("Contact:", self.contact), ("Deal lead:", self.lead),
                         ("Received:", self.received), ("Offer / LOI:", self.offer),
                         ("Due-diligence deadline:", self.dd_deadline), ("Firm (conditions waived):", self.firm),
                         ("Closing:", self.closing), ("Deposit:", self.deposit), ("Next step:", self.next_step)):
            right.addRow(label, w)
        tabs.addTab(det, "Details")

        # the deal checklist: sections → groups → items, each with a status, a due date and notes
        ddw = QWidget()
        dlay = QVBoxLayout(ddw)
        top = QHBoxLayout()
        self.progress = QLabel()
        self.progress.setTextFormat(Qt.TextFormat.RichText)
        top.addWidget(self.progress, 1)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setMaximumWidth(260)
        top.addWidget(self.bar)
        dlay.addLayout(top)
        self.dd = QTreeWidget()
        self.dd.setColumnCount(4)
        self.dd.setHeaderLabels(["Deliverable", "Status", "Due", "Notes"])
        self.dd.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.dd.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.dd.setColumnWidth(1, 130)
        self.dd.setColumnWidth(2, 130)
        self.dd.setEditTriggers(QTreeWidget.EditTrigger.DoubleClicked | QTreeWidget.EditTrigger.EditKeyPressed
                                | QTreeWidget.EditTrigger.SelectedClicked)
        dlay.addWidget(self.dd, 1)
        brow = QHBoxLayout()
        for text, slot, tip in (
                ("Add Item", self._add_item_here, "A deliverable under the selected group or section"),
                ("Add Group", self._add_group_here, "A group under the selected section (e.g. Operating Bills)"),
                ("Add Section", self._add_section, "A new section (e.g. Insurance)"),
                ("Remove", self._remove_item, "Remove the selected item, group or section"),
                ("Load the Standard Checklist", self._usual, "Add the standard items that aren't here yet"),
                ("Export to Excel…", self._export, "The checklist as a spreadsheet to send or print")):
            btn = QPushButton(text)
            btn.setToolTip(tip)
            btn.clicked.connect(slot)
            brow.addWidget(btn)
        brow.addStretch(1)
        dlay.addLayout(brow)
        hint = QLabel("Set each deliverable's status — In Progress, Done or N/A — and add notes. Double-click a name to "
                      "rename it. Progress counts Done out of everything that applies (N/A left out).")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        dlay.addWidget(hint)
        tabs.addTab(ddw, "Deal Checklist")
        self.tabs = tabs
        for it in (dl.checklist(pf, d.id) if d.id else []):
            self._add_item(it)
        self.dd.expandAll()

        # deposits & costs paid before closing, and which company pays them
        self.costs = DealCostsTab(pf, d, companies, deal_name=lambda: self.name.text().strip() or d.name)
        tabs.addTab(self.costs, "Deposits && Costs")

        # documents: LOI, APS, appraisal, BCA, ESA, closing documents …
        if d.id is not None:
            from .. import attachments as at
            from .attachments_box import AttachmentsBox

            self.docs = AttachmentsBox(pf.conn, at.portfolio_folder(pf), "deal", d.id, sub=d.name)
            tabs.addTab(self.docs, "Documents")
        else:
            self.docs = QLabel("Save the deal, then open it again to attach its documents (LOI, APS, reports …).")
            self.docs.setWordWrap(True)
            self.docs.setAlignment(Qt.AlignmentFlag.AlignTop)
            tabs.addTab(self.docs, "Documents")

        # notes
        self.notes = QPlainTextEdit(d.notes)
        self.notes.setPlaceholderText("Anything about the deal: seller's story, pricing guidance, issues found…")
        tabs.addTab(self.notes, "Notes")
        self._build_summary(tabs, dl.underwriting(d))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for w in (self.price, self.noi, self.equity):
            w.valueChanged.connect(self._update)
        self.units.valueChanged.connect(self._update)
        self.name.textChanged.connect(self._update)
        self.sqft.valueChanged.connect(self._update)
        self._update()
        tabs.setCurrentIndex(0)

    # ---- the one-page summary ----
    def _build_summary(self, tabs: QTabWidget, u: dl.Underwriting) -> None:
        """Assumptions on the left, the one-pager on the right (it follows every change)."""
        from PySide6.QtWidgets import QScrollArea, QTextBrowser

        from .asset_mgmt import _money_box

        sw = QWidget()
        row = QHBoxLayout(sw)
        side = QWidget()
        form = QFormLayout(side)
        form.setContentsMargins(0, 0, 8, 0)

        def money(cents: int):
            b = _money_box()
            b.setValue(cents / 100)
            b.valueChanged.connect(self._render)
            return b

        def pct(v: float, suffix: str = " %", hi: float = 100, dec: int = 2):
            b = QDoubleSpinBox()
            b.setRange(0, hi)
            b.setDecimals(dec)
            b.setSuffix(suffix)
            b.setValue(v)
            b.valueChanged.connect(self._render)
            return b

        def head(text: str) -> QLabel:
            lbl = QLabel(f"<b>{text}</b>")
            lbl.setTextFormat(Qt.TextFormat.RichText)
            return lbl

        self.u_rent = money(u.rent_inplace)
        self.u_rent.setToolTip("Average rent a unit a month today (with the units on the Details tab)")
        self.u_market = money(u.rent_market)
        self.u_market.setToolTip("Average market rent a unit a month — the potential")
        self.u_other = money(u.other_income)
        self.u_other.setToolTip("Parking, laundry, storage … a month, all units")
        self.u_vac = pct(u.vacancy_pct, hi=50)
        self.u_opex = money(u.opex_annual)
        self.u_opex.setToolTip("Operating expenses a year: taxes, insurance, utilities, repairs, management, wages… "
                               "(leave at $0 to use the % of revenue below)")
        self.u_opex_pct = pct(u.opex_pct)
        self.u_res = money(u.reserve_per_unit)
        self.u_close = pct(u.closing_pct, hi=20)
        self.u_close.setToolTip("Land transfer tax, legal, reports, lender fees … as % of the price")
        self.u_ltv = pct(u.ltv_pct, hi=95, dec=1)
        self.u_rate = pct(u.rate_pct, hi=25, dec=3)
        self.u_amort = pct(u.amort_years, " yrs", 50, 1)
        self.u_term = pct(u.term_years, " yrs", 50, 1)
        self.u_io = QCheckBox("Interest only")
        self.u_io.setChecked(u.interest_only)
        self.u_io.toggled.connect(self._render)
        form.addRow(head("Revenue"))
        form.addRow("Rent / unit / month:", self.u_rent)
        form.addRow("Market rent / unit:", self.u_market)
        form.addRow("Other income / month:", self.u_other)
        form.addRow("Vacancy && bad debt:", self.u_vac)
        form.addRow(head("Expenses"))
        form.addRow("Operating expenses / yr:", self.u_opex)
        form.addRow("… or % of revenue:", self.u_opex_pct)
        form.addRow("Capital reserve / unit / yr:", self.u_res)
        form.addRow(head("Purchase"))
        form.addRow("Closing costs:", self.u_close)
        form.addRow(head("Debt"))
        form.addRow("Loan-to-value:", self.u_ltv)
        form.addRow("Interest rate:", self.u_rate)
        form.addRow("Amortization:", self.u_amort)
        form.addRow("Term:", self.u_term)
        form.addRow("", self.u_io)
        pdf = QPushButton("Export PDF…")
        pdf.setToolTip("The one-pager as a PDF to share")
        pdf.clicked.connect(self._export_pdf)
        form.addRow("", pdf)
        scroll = QScrollArea()
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setMinimumWidth(330)
        scroll.setMaximumWidth(360)
        row.addWidget(scroll)
        self.summary = QTextBrowser()
        self.summary.setFrameShape(QTextBrowser.Shape.NoFrame)
        row.addWidget(self.summary, 1)
        tabs.insertTab(0, sw, "Summary")

    def underwriting_now(self) -> dl.Underwriting:
        c = lambda b: round(b.value() * 100)  # noqa: E731
        return dl.Underwriting(c(self.u_rent), c(self.u_market), c(self.u_other), self.u_vac.value(), c(self.u_opex),
                               self.u_opex_pct.value(), c(self.u_res), self.u_close.value(), self.u_ltv.value(),
                               self.u_rate.value(), self.u_amort.value(), self.u_io.isChecked(), self.u_term.value())

    def draft(self) -> dl.Deal:
        """The deal as the window has it now (not saved)."""
        from dataclasses import replace

        return replace(self.deal, name=self.name.text().strip(), address=self.address.text().strip(),
                       asset_type=self.asset_type.currentText(), stage=self.stage.currentText(),
                       price_cents=round(self.price.value() * 100), units=self.units.value(),
                       sqft=self.sqft.value() or None, noi_cents=round(self.noi.value() * 100))

    def _render(self, *_) -> None:
        if hasattr(self, "summary"):
            self.summary.setHtml(summary_html(self.draft(), self.underwriting_now(), self.summary))
            self.u_amort.setEnabled(not self.u_io.isChecked())
            self.u_opex_pct.setEnabled(not self.u_opex.value())

    def _export_pdf(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        from .reports_tab import write_pdf

        name = self.name.text().strip() or "Deal"
        path, _ = QFileDialog.getSaveFileName(self, "Export the summary", f"{name} - Summary.pdf", "PDF (*.pdf)")
        if path:
            write_pdf(summary_html(self.draft(), self.underwriting_now(), None, printing=True), path, landscape=True,
                      font_pt=7)
            info(self, "Exported", f"The summary is saved to {path}.")

    def _update(self, *_) -> None:
        price, noi, units = self.price.value(), self.noi.value(), self.units.value()
        parts = []
        if price and noi:
            parts.append(f"cap rate {noi / price * 100:.2f}%")
        if price and units:
            parts.append(f"{fmt_money(round(price * 100 / units))} per unit")
        if price and self.equity.value():
            parts.append(f"LTV {(price - self.equity.value()) / price * 100:.0f}% if the rest is debt")
        self.metrics.setText(" · ".join(parts))
        self._progress()
        self._render()

    # ---- the checklist tree ----
    KIND = Qt.ItemDataRole.UserRole

    def _node(self, parent, text: str, kind: str) -> QTreeWidgetItem:
        from .theme import c as tc

        n = QTreeWidgetItem([text])
        n.setData(0, self.KIND, kind)
        n.setFlags(n.flags() | Qt.ItemFlag.ItemIsEditable)
        f = n.font(0)
        f.setBold(True)
        n.setFont(0, f)
        if kind == "section":
            from PySide6.QtGui import QColor

            n.setForeground(0, QColor(tc("link")))
        (parent.addChild(n) if parent is not None else self.dd.addTopLevelItem(n))
        return n

    def _find(self, parent, text: str, kind: str) -> QTreeWidgetItem | None:
        kids = ([parent.child(i) for i in range(parent.childCount())] if parent is not None else
                [self.dd.topLevelItem(i) for i in range(self.dd.topLevelItemCount())])
        return next((k for k in kids if k.data(0, self.KIND) == kind and k.text(0) == text), None)

    def _add_item(self, it: dl.DDItem, parent: QTreeWidgetItem | None = None) -> QTreeWidgetItem:
        if parent is None:
            sec = self._find(None, it.section or "Other", "section") or self._node(None, it.section or "Other",
                                                                                   "section")
            parent = sec
            if it.grp:
                parent = self._find(sec, it.grp, "group") or self._node(sec, it.grp, "group")
        leaf = QTreeWidgetItem([it.item, "", "", it.note])
        leaf.setData(0, self.KIND, "item")
        leaf.setFlags(leaf.flags() | Qt.ItemFlag.ItemIsEditable)
        parent.addChild(leaf)
        box = QComboBox()
        box.addItems([x or "—" for x in dl.STATUSES])
        box.setCurrentIndex(dl.STATUSES.index(it.status) if it.status in dl.STATUSES else 0)
        box.currentIndexChanged.connect(lambda *_, b=box: (self._paint(b), self._progress()))
        self._paint(box)
        self.dd.setItemWidget(leaf, 1, box)
        self.dd.setItemWidget(leaf, 2, _date_box(it.due))
        parent.setExpanded(True)
        self._progress()
        return leaf

    @staticmethod
    def _paint(box: QComboBox) -> None:
        """Done green, In Progress amber, N/A grey — like the spreadsheet."""
        from .theme import is_dark

        status = dl.STATUSES[box.currentIndex()]
        light = {"Done": ("#c6efce", "#006100"), "In Progress": ("#ffeb9c", "#9c5700"), "N/A": ("#ededed", "#595959")}
        dark = {"Done": ("#1f4d2b", "#9be3ae"), "In Progress": ("#5a4a12", "#ffd970"), "N/A": ("#3a3a3a", "#b0b0b0")}
        bg, fg = (dark if is_dark() else light).get(status, (None, None))
        box.setStyleSheet(f"QComboBox {{ background: {bg}; color: {fg}; }}" if bg else "")

    def _status(self, leaf: QTreeWidgetItem) -> str:
        return dl.STATUSES[self.dd.itemWidget(leaf, 1).currentIndex()]

    def _leaves(self, node=None):
        kids = ([node.child(i) for i in range(node.childCount())] if node is not None else
                [self.dd.topLevelItem(i) for i in range(self.dd.topLevelItemCount())])
        for k in kids:
            if k.data(0, self.KIND) == "item":
                yield k
            else:
                yield from self._leaves(k)

    def _progress(self) -> None:
        if not hasattr(self, "bar"):
            return
        items = self.items()
        p = dl.progress(items)
        self.bar.setValue(round((p.pct or 0) * 100))
        self.progress.setText(f"<b>{p.done} of {p.needed}</b> done" + (f" · {p.in_progress} in progress"
                                                                         if p.in_progress else "")
                              + (f" · {p.na} N/A" if p.na else "") if p.total else
                              "No checklist yet — Load the Standard Checklist, or add your own. It starts by itself "
                              "when the deal moves to due diligence.")
        for i in range(self.dd.topLevelItemCount()):          # each section's progress beside its name
            sec = self.dd.topLevelItem(i)
            sp = dl.progress([dl.DDItem("x", status=self._status(x)) for x in self._leaves(sec)])
            sec.setText(1, f"{sp.done} / {sp.needed}" if sp.total else "")
            sec.setText(3, f"{sp.in_progress} in progress" if sp.in_progress else "")

    def _add_item_here(self) -> None:
        n = self.dd.currentItem()
        parent = n.parent() if n is not None and n.data(0, self.KIND) == "item" else n
        if parent is None:
            parent = self._find(None, "Other", "section") or self._node(None, "Other", "section")
        leaf = self._add_item(dl.DDItem("New item"), parent)
        self.dd.setCurrentItem(leaf)
        self.dd.editItem(leaf, 0)

    def _add_group_here(self) -> None:
        n = self.dd.currentItem()
        while n is not None and n.data(0, self.KIND) != "section":
            n = n.parent()
        if n is None:
            n = self._find(None, "Other", "section") or self._node(None, "Other", "section")
        g = self._node(n, "New group", "group")
        n.setExpanded(True)
        self.dd.setCurrentItem(g)
        self.dd.editItem(g, 0)

    def _add_section(self) -> None:
        s_ = self._node(None, "New section", "section")
        self.dd.setCurrentItem(s_)
        self.dd.editItem(s_, 0)

    def _remove_item(self) -> None:
        n = self.dd.currentItem()
        if n is None:
            return
        many = n.data(0, self.KIND) != "item" and n.childCount()
        if many and not confirm(self, "Remove", f"Remove “{n.text(0)}” and everything under it?"):
            return
        (n.parent().removeChild(n) if n.parent() else self.dd.takeTopLevelItem(self.dd.indexOfTopLevelItem(n)))
        self._progress()

    def _usual(self) -> None:
        have = {(it.section.lower(), it.grp.lower(), it.item.lower()) for it in self.items()}
        for it in dl.standard_items(_date_of(self.dd_deadline)):
            if (it.section.lower(), it.grp.lower(), it.item.lower()) not in have:
                self._add_item(it)
        self.dd.expandAll()

    def items(self) -> list[dl.DDItem]:
        out = []
        for i in range(self.dd.topLevelItemCount()):
            sec = self.dd.topLevelItem(i)
            for leaf in self._leaves(sec):
                parent = leaf.parent()
                grp = parent.text(0).strip() if parent is not sec else ""
                out.append(dl.DDItem(leaf.text(0), due=_date_of(self.dd.itemWidget(leaf, 2)), note=leaf.text(3),
                                     section=sec.text(0).strip(), grp=grp, status=self._status(leaf)))
        return out

    def _export(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        name = self.name.text().strip() or "Deal"
        path, _ = QFileDialog.getSaveFileName(self, "Export the deal checklist", f"Deal Checklist - {name}.xlsx",
                                              "Excel (*.xlsx)")
        if path:
            dl.export_checklist(path, name, self.items())
            info(self, "Exported", f"The checklist is saved to {path}.")

    def save(self) -> None:
        d = self.deal
        d.name, d.address, d.asset_type = self.name.text(), self.address.text().strip(), self.asset_type.currentText()
        d.stage = self.stage.currentText()
        d.price_cents, d.noi_cents = round(self.price.value() * 100), round(self.noi.value() * 100)
        d.equity_cents, d.deposit_cents = round(self.equity.value() * 100), round(self.deposit.value() * 100)
        d.units, d.sqft = self.units.value(), (self.sqft.value() or None)
        d.source, d.contact, d.lead = self.source.text().strip(), self.contact.text().strip(), self.lead.text().strip()
        d.received, d.offer_date = _date_of(self.received), _date_of(self.offer)
        d.dd_deadline, d.firm_date, d.closing_date = (_date_of(self.dd_deadline), _date_of(self.firm),
                                                      _date_of(self.closing))
        d.next_step, d.notes = self.next_step.text().strip(), self.notes.toPlainText().strip()
        d.uw = self.underwriting_now().to_json()
        try:
            new = d.id is None
            dl.save(self.pf, d)
            items = self.items()
            if new and d.stage == "Due Diligence" and not items:
                dl.start_checklist(self.pf, d.id)
            else:
                dl.save_checklist(self.pf, d.id, items)
            self.costs.save(d.id)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
            return
        self.accept()


def summary_html(d: dl.Deal, u: dl.Underwriting, widget=None, *, printing: bool = False) -> str:
    """The deal's one-pager: property, operations (in place and at market), returns, debt & equity, and two
    sensitivity tables — clean, no lines, like the Dashboard."""
    from .asset_mgmt import short
    from .theme import c as tc

    esc = html.escape
    if printing or widget is None:
        text, muted, red, accent, green, shade = "#111111", "#666666", "#b3261e", "#1f4e9e", "#1b7f3b", "#eef3fb"
    else:
        from .theme import is_dark

        text, muted, red, accent = widget.palette().text().color().name(), tc("muted"), tc("red"), tc("link")
        green, shade = tc("green"), ("#20303c" if is_dark() else "#eef3fb")
    now, pot = dl.analyze(d, u), dl.analyze(d, u, market=True)

    def m(c):
        """Whole dollars; negatives red in brackets."""
        if c is None:
            return "—"
        whole = f"${abs(round(c / 100)):,}"
        return f"<span style='color:{red}'>({whole})</span>" if round(c / 100) < 0 else whole

    def p(x, dec=1):
        if x is None:
            return "—"
        v = round(x * 100, dec)
        return f"{0.0 if v == 0 else v:.{dec}f}%"

    def x_(v):
        return "—" if v is None else f"{v:.2f}x"

    def title(t: str, sub: str = "") -> str:
        return (f"<p style='margin:{10 if printing else 16}px 0 {2 if printing else 4}px 0; color:{accent}; "
                f"font-size:{7 if printing else 8.5}pt; font-weight:700'>{esc(t.upper())}"
                + (f" &nbsp;<span style='color:{muted}; font-weight:400'>{esc(sub)}</span>" if sub else "") + "</p>")

    def rows(pairs, bold_last=False) -> str:
        out = [f"<table width='100%' cellspacing='0' cellpadding='{1 if printing else 2}'>"]
        for k, (a, b) in enumerate(pairs):
            w = "font-weight:700;" if bold_last and k == len(pairs) - 1 else ""
            out.append(f"<tr><td style='{w}color:{text if w else muted}'>{a}</td><td align='right' "
                       f"style='{w}color:{text}'>{b}</td></tr>")
        return "".join(out) + "</table>"

    def two(pairs, bolds=()) -> str:
        out = [f"<table width='100%' cellspacing='0' cellpadding='{1 if printing else 2}'><tr><td></td>"
               f"<td align='right' style='color:"
               f"{muted}; font-size:8pt'>In place</td><td align='right' style='color:{muted}; font-size:8pt'>"
               f"Potential</td></tr>"]
        for label, a, b in pairs:
            w = "font-weight:700;" if label in bolds else ""
            out.append(f"<tr><td style='{w}color:{text if w else muted}'>{label}</td><td align='right' style='{w}"
                       f"color:{text}'>{a}</td><td align='right' style='{w}color:{text}'>{b}</td></tr>")
        return "".join(out) + "</table>"

    units = d.units or 0
    head = (f"<table width='100%' cellspacing='0'><tr><td><span style='font-size:16pt; font-weight:700; color:{text}'>"
            f"{esc(d.name or 'New deal')}</span> <span style='font-size:11pt; color:{muted}'>Deal summary</span><br>"
            f"<span style='color:{muted}'>{esc(' · '.join(x for x in (d.address, d.asset_type, d.stage) if x))}"
            f"</span></td><td align='right' style='color:{muted}'>{date.today():%b %d, %Y}</td></tr></table>")
    tiles = [("Price", short(now.price) if now.price else "—", f"{fmt_money(round(now.price / units))} / unit"
              if units and now.price else ""),
             ("Units", str(units or "—"), f"{d.sqft:,.0f} sq ft" if d.sqft else ""),
             ("NOI", short(now.noi) if now.noi else "—", f"cap rate {p(now.cap_rate, 2)}"),
             ("Cash flow", short(now.cash_flow), "after debt service, a year"),
             ("Cash-on-cash", p(now.coc), f"on {short(now.equity)} equity"),
             ("DSCR", x_(now.dscr), f"debt yield {p(now.debt_yield)}")]
    tile_html = ("<table width='100%' cellspacing='6'><tr>" + "".join(
        f"<td style='background:{shade}; padding:8px'><span style='color:{muted}; font-size:8pt'>{esc(a)}</span><br>"
        f"<span style='font-size:14pt; font-weight:700; color:"
        f"{(red if now.cash_flow < 0 else green) if a == 'Cash flow' and now.noi else text}'>{esc(b)}</span><br>"
        f"<span style='color:{muted}; font-size:8pt'>{esc(c)}</span></td>" for a, b, c in tiles) + "</tr></table>")

    ops = [("Gross potential rent", m(now.gpr), m(pot.gpr)),
           (f"Vacancy ({u.vacancy_pct:g}%)", m(-now.vacancy) if now.vacancy else "—", m(-pot.vacancy) if pot.vacancy
            else "—"),
           ("Other income", m(now.other), m(pot.other)),
           ("Effective gross income", m(now.egi), m(pot.egi)),
           ("Operating expenses", m(-now.opex) if now.opex else "—", m(-pot.opex) if pot.opex else "—"),
           ("Net operating income", m(now.noi), m(pot.noi)),
           ("Capital reserve", m(-now.reserve) if now.reserve else "—", m(-pot.reserve) if pot.reserve else "—"),
           ("Debt service", m(-now.debt_service) if now.debt_service else "—",
            m(-pot.debt_service) if pot.debt_service else "—"),
           ("Cash flow after debt", m(now.cash_flow), m(pot.cash_flow)),
           ("", "", ""),
           ("Cap rate", p(now.cap_rate, 2), p(pot.cap_rate, 2)),
           ("Cash-on-cash", p(now.coc), p(pot.coc)),
           ("DSCR", x_(now.dscr), x_(pot.dscr)),
           ("Debt yield", p(now.debt_yield), p(pot.debt_yield)),
           ("Expense ratio", p(now.expense_ratio), p(pot.expense_ratio)),
           ("Break-even occupancy", p(now.breakeven), p(pot.breakeven))]
    if units:
        ops.insert(6, ("NOI / unit", m(round(now.noi / units)), m(round(pot.noi / units))))
    note = ("" if not now.from_deal_noi else
            f"<p style='color:{muted}; font-size:8pt'>NOI as entered on the deal — enter the rents and expenses on the "
            "left for the full picture.</p>")
    left = (title("Property") + rows([("Units", str(units or "—")), ("Size", f"{d.sqft:,.0f} sq ft" if d.sqft else "—"),
                                      ("Purchase price", m(now.price)),
                                      ("Price / unit", m(round(now.price / units)) if units and now.price else "—"),
                                      ("Price / sq ft", m(round(now.price / d.sqft)) if d.sqft and now.price else "—"),
                                      (f"Closing costs ({u.closing_pct:g}%)", m(now.closing)),
                                      ("All-in cost", m(now.all_in))], bold_last=True)
            + title("Debt") + rows([("Loan", m(now.loan)), ("Loan-to-value", f"{u.ltv_pct:g}%"),
                                    ("Interest rate", f"{u.rate_pct:g}%"),
                                    ("Repayment", "Interest only" if u.interest_only else
                                     f"{u.amort_years:g}-yr amortization"),
                                    ("Term", f"{u.term_years:g} yrs"), ("Debt service / yr", m(now.debt_service))])
            + title("Equity") + rows([("All-in cost", m(now.all_in)), ("Less loan", m(-now.loan) if now.loan else "—"),
                                      ("Equity required", m(now.equity))], bold_last=True))
    mid = title("Operations", "a year — in place and at market rents") + two(
        ops, bolds=("Effective gross income", "Net operating income", "Cash flow after debt")) + note

    # sensitivities
    rates, rents, grid = dl.sensitivity_rate_rent(d, u)

    def grid_html(corner, cols, rows_, cells, base_r, base_c, fmt):
        out = [f"<table cellspacing='0' cellpadding='{2 if printing else 4}'><tr><td style='color:{muted}; "
               f"font-size:8pt'>{corner}</td>"]
        out += [f"<td align='right' style='color:{muted}; font-size:8pt'>{c}</td>" for c in cols]
        out.append("</tr>")
        for i, (label, line) in enumerate(zip(rows_, cells)):
            out.append(f"<tr><td style='color:{muted}; white-space:nowrap'>{label}</td>")
            for j, v in enumerate(line):
                base = i == base_r and j == base_c
                bg = f"background:{shade}; font-weight:700;" if base else ""
                colour = red if isinstance(v, (int, float)) and v is not None and v < 0 else text
                out.append(f"<td align='right' style='{bg}color:{colour}; white-space:nowrap'>{fmt(v)}</td>")
            out.append("</tr>")
        return "".join(out) + "</table>"

    s1 = grid_html("Rate ↓ · Rent →", [f"{c:+.0%}" if c else "Base" for c in rents], [f"{r:.2f}%" for r in rates],
                   grid, 2, 2, lambda v: short(v) if v is not None else "—")
    prices, ltvs, grid2 = dl.sensitivity_price_ltv(d, u)
    s2 = grid_html("Price ↓ · LTV →", [f"{v:g}%" for v in ltvs], [short(x) for x in prices], grid2, 2, 2,
                   lambda v: p(v))
    t1, t2 = (title("Sensitivity", "cash flow after debt service, a year") + s1,
              title("Sensitivity", "cash-on-cash return") + s2)
    if printing:        # on paper: the two tables side by side under the figures
        body = (f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td width='45%' valign='top' style='padding-right:24px'>{left}</td>"
                f"<td width='55%' valign='top'>{mid}</td></tr></table>"
                f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
                f"<td width='50%' valign='top' style='padding-right:24px'>{t1}</td>"
                f"<td width='50%' valign='top'>{t2}</td></tr></table>")
        return f"<div style='color:{text}'>{head}{tile_html}{body}</div>"
    sens = t1 + t2
    body = (f"<table width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td width='30%' valign='top' style='padding-right:18px'>{left}</td>"
            f"<td width='38%' valign='top' style='padding-right:18px'>{mid}</td>"
            f"<td width='32%' valign='top'>{sens}</td></tr></table>")
    return f"<div style='color:{text}'>{head}{tile_html}{body}</div>"


class CloseDealDialog(QDialog):
    """Close a deal under contract: set up its new operating company and its opening balance sheet — the purchase
    price and closing costs, the debt and the equity (see closing.py)."""

    def __init__(self, pf, deal: dl.Deal, create_company, parent=None):
        super().__init__(parent)
        from .. import closing as cl
        from .. import portfolio as pfm

        self.pf, self.deal, self.create_company = pf, deal, create_company
        self.holding_id: int | None = None
        s = cl.default_setup(deal, pf)
        self.prepaid = s.prepaid          # paid before closing by other companies (Deposits & Costs)
        self.setWindowTitle(f"Close Deal – {deal.name}")
        self.resize(980, 620)
        lay = QVBoxLayout(self)
        intro = QLabel(f"Closing <b>{html.escape(deal.name)}</b> sets up a new operating company with its own books "
                       "and posts its opening balance sheet: the property at what it cost (price + closing costs), "
                       "the loans, the owners' equity, and any cash left over in the bank. It's added to the portfolio "
                       "with the loans on Financing & Cap Table and the owners on the cap table.")
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(intro)
        tabs = self.tabs = QTabWidget()
        lay.addWidget(tabs, 1)

        # 1 company
        cw = QWidget()
        form = QFormLayout(cw)
        self.company = QLineEdit()
        self.company.setPlaceholderText("The corporation, e.g. 1001234567 Ontario Inc.")
        self.prop = QLineEdit(s.property_name)
        self.entity = QComboBox()
        self.entity.setEditable(True)
        self.entity.addItems(["Corporation", "Limited partnership", "Partnership", "LLC", "Trust"])
        self.fiscal = QComboBox()
        for m in range(1, 13):
            self.fiscal.addItem(date(2000, m, 1).strftime("%B"), m)
        self.closing = _date_box(s.closing_date)
        self.own = QDoubleSpinBox()
        self.own.setRange(0.01, 100)
        self.own.setDecimals(2)
        self.own.setSuffix(" %")
        self.own.setValue(100)
        self.own.setToolTip("The portfolio's share of the company")
        for label, w in (("Company (legal name):", self.company), ("Property name:", self.prop),
                         ("Entity type:", self.entity), ("Fiscal year starts:", self.fiscal),
                         ("Closing date:", self.closing), ("Portfolio's share:", self.own)):
            form.addRow(label, w)
        tabs.addTab(cw, "1 · Company")

        # 2 purchase & closing costs
        uw = QWidget()
        ul = QVBoxLayout(uw)
        self.costs = QTableWidget(0, 2)
        self.costs.setHorizontalHeaderLabels(["Cost", "Amount"])
        self.costs.verticalHeader().setVisible(False)
        self.costs.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.costs.setColumnWidth(1, 200)
        ul.addWidget(self.costs, 1)
        crow = QHBoxLayout()
        add = QPushButton("Add Cost")
        add.clicked.connect(lambda: self._add_cost("", 0))
        rem = QPushButton("Remove Cost")
        rem.clicked.connect(lambda: self._remove(self.costs))
        ltt = QPushButton("Work Out Land Transfer Tax (Ontario)")
        ltt.setToolTip("Ontario's provincial land transfer tax on the purchase price — an estimate; Toronto adds its "
                       "own municipal tax. Check the figure with your lawyer.")
        ltt.clicked.connect(self._ltt)
        self.single = QCheckBox("1–2 single-family homes")
        self.land = QDoubleSpinBox()
        self.land.setRange(0, 99)
        self.land.setDecimals(1)
        self.land.setSuffix(" % land")
        self.land.setToolTip("Share of the purchase price that is land (not depreciated); the rest is the building")
        for w in (add, rem, ltt, self.single):
            crow.addWidget(w)
        crow.addStretch(1)
        crow.addWidget(QLabel("Purchase price split:"))
        crow.addWidget(self.land)
        ul.addLayout(crow)
        tabs.addTab(uw, "2 · Purchase && Closing Costs")
        for label, cents in s.costs:
            self._add_cost(label, cents)

        # 3 debt
        dw = QWidget()
        dlay = QVBoxLayout(dw)
        self.loans = QTableWidget(0, 7)
        self.loans.setHorizontalHeaderLabels(["Lender", "Facility", "Amount", "Rate", "Term (yrs)",
                                              "Amortization (yrs)", "Interest only"])
        self.loans.verticalHeader().setVisible(False)
        self.loans.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        dlay.addWidget(self.loans, 1)
        drow = QHBoxLayout()
        b = QPushButton("Add Loan")
        b.clicked.connect(lambda: self._add_loan(pfm.Loan("", "Mortgage", 0, term_months=60, amort_months=300)))
        drow.addWidget(b)
        b = QPushButton("Remove Loan")
        b.clicked.connect(lambda: self._remove(self.loans))
        drow.addWidget(b)
        drow.addStretch(1)
        dlay.addLayout(drow)
        tabs.addTab(dw, "3 · Debt")
        for ln in s.loans or [pfm.Loan("", "Mortgage", 0, term_months=60, amort_months=300)]:
            self._add_loan(ln)

        # 4 equity
        ew = QWidget()
        elay = QVBoxLayout(ew)
        self.eq = QTableWidget(0, 5)
        self.eq.setHorizontalHeaderLabels(["Owner", "Through company", "Class", "Amount", "As"])
        self.eq.verticalHeader().setVisible(False)
        self.eq.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.eq.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        elay.addWidget(self.eq, 1)
        erow = QHBoxLayout()
        b = QPushButton("Add Owner")
        b.clicked.connect(lambda: self._add_equity(cl.EquityLine("", 0)))
        erow.addWidget(b)
        b = QPushButton("Remove Owner")
        b.clicked.connect(lambda: self._remove(self.eq))
        erow.addWidget(b)
        b = QPushButton("Equity Covers the Rest")
        b.setToolTip("Set the selected owner's amount so the money in equals closing costs")
        b.clicked.connect(self._equity_rest)
        erow.addWidget(b)
        erow.addStretch(1)
        elay.addLayout(erow)
        hint = QLabel("Share capital is equity (by class); a shareholder loan is owed back to the owner (a liability). "
                      "Both are on the cap table as money put in on closing.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        elay.addWidget(hint)
        tabs.addTab(ew, "4 · Equity")
        for e in s.equity or [cl.EquityLine("", 0)]:
            self._add_equity(e)

        # 5 review
        self.review = QLabel()
        self.review.setTextFormat(Qt.TextFormat.RichText)
        self.review.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.review.setWordWrap(True)
        rv = QWidget()
        QVBoxLayout(rv).addWidget(self.review)
        tabs.addTab(rv, "5 · Review")
        tabs.currentChanged.connect(self._update)

        self.totals = QLabel()
        self.totals.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.totals)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.go = buttons.addButton("Close Deal && Create Company", QDialogButtonBox.ButtonRole.AcceptRole)
        self.go.setObjectName("primary")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        self._update()

    # ---- rows ----
    def _money(self, cents: int):
        from .asset_mgmt import _money_box

        box = _money_box()
        box.setValue(cents / 100)
        box.valueChanged.connect(self._update)
        return box

    def _add_cost(self, label: str, cents: int) -> None:
        r = self.costs.rowCount()
        self.costs.insertRow(r)
        self.costs.setItem(r, 0, QTableWidgetItem(label))
        self.costs.setCellWidget(r, 1, self._money(cents))
        self._update()

    def _add_loan(self, ln) -> None:
        from .. import portfolio as pfm

        t = self.loans
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(ln.lender))
        kind = QComboBox()
        kind.addItems(pfm.LOAN_KINDS)
        kind.setCurrentText(ln.kind)
        t.setCellWidget(r, 1, kind)
        t.setCellWidget(r, 2, self._money(ln.principal_cents))
        rate = QDoubleSpinBox()
        rate.setRange(0, 100)
        rate.setDecimals(3)
        rate.setSuffix(" %")
        rate.setValue(ln.rate_pct or 0)
        t.setCellWidget(r, 3, rate)
        for c, months in ((4, ln.term_months), (5, ln.amort_months or ln.term_months)):
            y = QDoubleSpinBox()
            y.setRange(0, 50)
            y.setDecimals(1)
            y.setValue(months / 12)
            t.setCellWidget(r, c, y)
        io = QCheckBox()
        io.setChecked(ln.repayment == "Interest only")
        t.setCellWidget(r, 6, io)
        t.resizeColumnsToContents()
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._update()

    def _add_equity(self, e) -> None:
        from .. import closing as cl

        t = self.eq
        r = t.rowCount()
        t.insertRow(r)
        t.setItem(r, 0, QTableWidgetItem(e.owner))
        t.setItem(r, 1, QTableWidgetItem(e.company))
        cls = QComboBox()
        cls.setEditable(True)
        cls.addItems(["Class A", "Class B", "Common", "Preferred"])
        cls.setCurrentText(e.share_class)
        t.setCellWidget(r, 2, cls)
        t.setCellWidget(r, 3, self._money(e.amount_cents))
        form = QComboBox()
        form.addItems(cl.EQUITY_FORMS)
        form.setCurrentText(e.form)
        t.setCellWidget(r, 4, form)
        t.setColumnWidth(3, 170)
        self._update()

    def _remove(self, t: QTableWidget) -> None:
        r = t.currentRow()
        if r >= 0:
            t.removeRow(r)
            self._update()

    def _ltt(self) -> None:
        from .. import closing as cl

        s = self.setup()
        for r in range(self.costs.rowCount()):
            if (self.costs.item(r, 0).text() if self.costs.item(r, 0) else "") == "Land transfer tax":
                self.costs.cellWidget(r, 1).setValue(cl.ontario_ltt(s.price, single_family=self.single.isChecked())
                                                     / 100)
                return
        self._add_cost("Land transfer tax", cl.ontario_ltt(s.price, single_family=self.single.isChecked()))

    def _equity_rest(self) -> None:
        r = max(0, self.eq.currentRow())
        if self.eq.rowCount() == 0:
            return
        box = self.eq.cellWidget(r, 3)
        s = self.setup()
        box.setValue(max(0, box.value() * 100 - s.cash_left) / 100)

    # ---- the setup as entered ----
    def setup(self):
        from .. import closing as cl
        from .. import portfolio as pfm

        costs = []
        for r in range(self.costs.rowCount()):
            label = self.costs.item(r, 0).text().strip() if self.costs.item(r, 0) else ""
            cents = round(self.costs.cellWidget(r, 1).value() * 100)
            if label or cents:
                costs.append((label or "Other closing costs", cents))
        loans = []
        for r in range(self.loans.rowCount()):
            t = self.loans
            amt = round(t.cellWidget(r, 2).value() * 100)
            if not amt:
                continue
            io = t.cellWidget(r, 6).isChecked()
            term, amort = round(t.cellWidget(r, 4).value() * 12), round(t.cellWidget(r, 5).value() * 12)
            loans.append(pfm.Loan(t.item(r, 0).text().strip() if t.item(r, 0) else "", t.cellWidget(r, 1).currentText(),
                                  amt, rate_pct=t.cellWidget(r, 3).value() or None, start_date=_date_of(self.closing),
                                  term_months=term, repayment="Interest only" if io else "Amortizing",
                                  amort_months=0 if io or amort == term else amort))
        equity = []
        for r in range(self.eq.rowCount()):
            t = self.eq
            amt = round(t.cellWidget(r, 3).value() * 100)
            owner = t.item(r, 0).text().strip() if t.item(r, 0) else ""
            comp = t.item(r, 1).text().strip() if t.item(r, 1) else ""
            if amt or owner or comp:
                equity.append(cl.EquityLine(owner, amt, t.cellWidget(r, 2).currentText().strip(), comp,
                                            t.cellWidget(r, 4).currentText()))
        return cl.ClosingSetup(self.company.text(), self.prop.text(), _date_of(self.closing) or "", costs, loans,
                               equity, self.land.value(), self.entity.currentText(), self.fiscal.currentData(),
                               self.own.value(), getattr(self, "prepaid", []))

    def _update(self, *_) -> None:
        from .theme import c as tc

        if not hasattr(self, "totals"):
            return
        s = self.setup()
        left = s.cash_left
        tail = (f"<span style='color:{tc('red')}'>short by <b>{fmt_money(-left)}</b></span>" if left < 0 else
                f"cash left for the bank <b>{fmt_money(left)}</b>")
        self.totals.setText(f"Uses <b>{fmt_money(s.uses)}</b> · Debt <b>{fmt_money(s.debt)}</b> · Equity "
                            f"<b>{fmt_money(s.equity_total)}</b>"
                            + (f" · Paid before closing <b>{fmt_money(s.prepaid_total)}</b>" if s.prepaid_total else "")
                            + f" · {tail}"
                            + (f" · LTV {s.debt / s.price * 100:.0f}%" if s.price else ""))
        if self.tabs.currentIndex() == 4:
            self.review.setText(self._review(s))

    def _review(self, s) -> str:
        esc = html.escape
        p = esc(s.property_name or "the property")
        rows = []
        for label, cents in s.costs:
            if not cents:
                continue
            if label == "Purchase price" and s.land_pct:
                land = round(cents * s.land_pct / 100)
                rows += [(f"Property – {p} – Land", land, 0), (f"Property – {p} – Building", cents - land, 0)]
            else:
                rows.append((f"Property – {p} – {esc(label)}", cents, 0))
        if s.cash_left > 0:
            rows.append(("Bank (starting cash)", s.cash_left, 0))
        for ln in s.loans:
            rows.append((f"{esc(ln.kind)} – {esc(ln.lender or '?')}", 0, ln.principal_cents))
        classes: dict[str, int] = {}
        for e in s.equity:
            if e.form == "Shareholder loan":
                rows.append((f"Shareholder Loan – {esc(e.owner or e.company or '?')}", 0, e.amount_cents))
            else:
                classes[e.share_class] = classes.get(e.share_class, 0) + e.amount_cents
        rows += [(f"Share Capital – {esc(c)}", 0, v) for c, v in classes.items()]
        rows += [(f"Due to {esc(p.payer_name)} (paid before closing)", 0, p.total) for p in s.prepaid if p.total]
        body = "".join(f"<tr><td style='padding:2px 24px 2px 0'>{a}</td><td align='right' style='padding:2px 12px'>"
                       f"{fmt_money(d) if d else ''}</td><td align='right' style='padding:2px 12px'>"
                       f"{fmt_money(c) if c else ''}</td></tr>" for a, d, c in rows)
        dr, cr = sum(r[1] for r in rows), sum(r[2] for r in rows)
        return (f"<p><b>{esc(s.company_name or 'The new company')}</b> — opening balance sheet on "
                f"{esc(s.closing_date)}:</p><table cellspacing='0'><tr><td></td><td align='right'><b>Debit</b></td>"
                f"<td align='right'><b>Credit</b></td></tr>{body}<tr><td><b>Total</b></td><td align='right'><b>"
                f"{fmt_money(dr)}</b></td><td align='right'><b>{fmt_money(cr)}</b></td></tr></table>"
                f"<p>Also: {p} in the portfolio ({s.ownership_pct:g}% ours) with the purchase price as its first "
                f"valuation; {len(s.loans)} loan(s) on Financing &amp; Cap Table; {len(s.equity)} owner(s) on the cap "
                f"table; sources &amp; uses filled in.</p>")

    def save(self) -> None:
        from .. import closing as cl

        s = self.setup()
        try:
            s.check()
        except ValueError as e:
            error(self, "Can't close yet", str(e))
            return
        try:
            self.holding_id = cl.close_deal(self.pf, self.deal.id, s, self.create_company)
        except Exception as e:  # noqa: BLE001
            error(self, "Couldn't close the deal", str(e))
            return
        self.accept()


STAGE_TITLES = {"New": "New deals", "Due Diligence": "Under due diligence", "Under Contract": "Under contract"}
STAGE_COLS = {"New": ["Deal", "Price", "Units", "Received"],
              "Due Diligence": ["Deal", "Price", "DD deadline", "Checklist"],
              "Under Contract": ["Deal", "Price", "Deposit", "Closing"],
              "Closed": ["Deal", "Stage", "Price", "Date", "In the portfolio"]}


class DealPipelinePage(QWidget):
    """New deals, under due diligence and under contract side by side (closed and dead deals below on request).
    Double-click a deal to open it; the buttons move the selected deal along."""

    def __init__(self, pf, parent=None, on_added=None, create_company=None, companies=None):
        super().__init__(parent)
        from .asset_mgmt import Card, _table

        self.pf = pf
        self.companies = companies or (lambda: [])   # () -> [(name, path, role)]: who can pay deposits and costs
        self.on_added = on_added          # called with a new holding's id when a deal is added to the portfolio
        self.create_company = create_company   # (name, entity_type=, fiscal_year_start_month=) -> company file
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, slot, tip in (
                ("New Deal…", self.new_deal, "Add a deal you're looking at"),
                ("Open…", self.open_deal, "The selected deal's details, due-diligence checklist and notes"),
                ("Checklist…", self.open_checklist, "The selected deal's checklist: deliverables, status, notes"),
                ("◂ Back", self.move_back, "Move the selected deal back a stage"),
                ("Advance ▸", self.advance, "New → Due Diligence → Under Contract → Closed"),
                ("Mark Dead", self.mark_dead, "The deal didn't go ahead (kept under Closed & dead)"),
                ("Close Deal…", self.close_deal, "Under contract: close it — set up its new operating company and "
                                                 "opening balance sheet (price, closing costs, debt, equity)"),
                ("Delete", self.delete, "Delete the selected deal")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            if text == "New Deal…":
                b.setObjectName("primary")
            bar.addWidget(b)
        bar.addStretch(1)
        self.show_closed = QCheckBox("Show closed && dead")
        self.show_closed.toggled.connect(self.refresh)
        bar.addWidget(self.show_closed)
        lay.addLayout(bar)
        cards = QHBoxLayout()
        self.cards = {}
        for k, title in (("New", "New deals"), ("Due Diligence", "Under due diligence"),
                         ("Under Contract", "Under contract"), ("equity", "Equity needed"),
                         ("next", "Next closing")):
            c = Card(title, small=True)
            self.cards[k] = c
            cards.addWidget(c)
        lay.addLayout(cards)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.tables: dict[str, QTableWidget] = {}
        self.heads: dict[str, QLabel] = {}
        self.rows: dict[str, list[dl.Deal]] = {}
        for stage in dl.PIPELINE:
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(0, 0, 6, 0)
            head = QLabel()
            head.setTextFormat(Qt.TextFormat.RichText)
            wl.addWidget(head)
            t = _table(STAGE_COLS[stage], 0)
            for c in range(1, len(STAGE_COLS[stage])):
                t.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
            t.doubleClicked.connect(lambda *_: self.open_deal())
            t.itemSelectionChanged.connect(lambda s=stage: self._only(s))
            wl.addWidget(t, 1)
            self.tables[stage], self.heads[stage] = t, head
            split.addWidget(w)
        lay.addWidget(split, 3)
        self.closed_box = QWidget()
        cl = QVBoxLayout(self.closed_box)
        cl.setContentsMargins(0, 6, 0, 0)
        self.heads["Closed"] = QLabel()
        self.heads["Closed"].setTextFormat(Qt.TextFormat.RichText)
        cl.addWidget(self.heads["Closed"])
        t = _table(STAGE_COLS["Closed"], 0)
        t.doubleClicked.connect(lambda *_: self.open_deal())
        t.itemSelectionChanged.connect(lambda: self._only("Closed"))
        cl.addWidget(t, 1)
        self.tables["Closed"] = t
        lay.addWidget(self.closed_box, 1)
        self.closed_box.setVisible(False)
        self.detail = QLabel()
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        self.detail.setWordWrap(True)
        self.detail.setObjectName("hint")
        lay.addWidget(self.detail)
        self._picked = None
        self.refresh()

    # ---- filling ----
    def refresh(self, *_) -> None:
        from .asset_mgmt import short
        from .theme import c as tc

        keep = self.selected().id if self.selected() else None
        everything = dl.deals(self.pf)
        tot = dl.totals(everything)
        accent, muted = tc("link"), tc("muted")
        for stage in dl.PIPELINE:
            rows = self.rows[stage] = [d for d in everything if d.stage == stage]
            t = tot[stage]
            self.heads[stage].setText(
                f"<span style='color:{accent}; font-weight:700; font-size:9pt'>{STAGE_TITLES[stage].upper()}</span>"
                f" &nbsp;<span style='color:{muted}'>{t.count} · {short(t.price) if t.price else '$0'}</span>")
            self.cards[stage].set(str(t.count), f"{fmt_money(t.price)}" + (f" · {t.units:,} units" if t.units else ""))
            self._fill(stage, rows)
        live = [d for d in everything if d.stage in ("Due Diligence", "Under Contract")]
        eq = sum(d.equity_cents for d in live)
        self.cards["equity"].set(fmt_money(eq), "under DD and under contract")
        nxt = min((d for d in everything if d.stage == "Under Contract" and d.closing_date),
                  key=lambda d: d.closing_date, default=None)
        if nxt:
            days = (date.fromisoformat(nxt.closing_date) - date.today()).days
            self.cards["next"].set(_short_date(nxt.closing_date), f"{nxt.name} · " + (
                f"in {days} days" if days >= 0 else f"{-days} days ago"), NEGATIVE if 0 <= days <= 14 else None)
        else:
            self.cards["next"].set("—", "nothing under contract with a closing date")
        closed = [d for d in everything if d.stage in ("Closed", "Dead")]
        self.rows["Closed"] = closed
        self.heads["Closed"].setText(
            f"<span style='color:{accent}; font-weight:700; font-size:9pt'>CLOSED &amp; DEAD</span>"
            f" &nbsp;<span style='color:{muted}'>{tot['Closed'].count} closed · {tot['Dead'].count} dead</span>")
        self._fill("Closed", closed)
        self.closed_box.setVisible(self.show_closed.isChecked())
        if keep is not None:
            self.select(keep)
        self._show_detail()

    def _fill(self, stage: str, rows: list[dl.Deal]) -> None:
        from .asset_mgmt import short
        from .operations_tab import _cell

        t = self.tables[stage]
        t.blockSignals(True)
        t.setRowCount(len(rows))
        for r, d in enumerate(rows):
            days = d.days_to()
            soon = days is not None and days <= 7
            if stage == "New":
                vals = [d.name, short(d.price_cents) if d.price_cents else "—", str(d.units or ""),
                        _short_date(d.received)]
            elif stage == "Due Diligence":
                left = "" if days is None else (f" ({days} d)" if days >= 0 else " (past)")
                need = d.dd_total - d.dd_na
                vals = [d.name, short(d.price_cents) if d.price_cents else "—", _short_date(d.dd_deadline) + left,
                        (f"{d.dd_done} / {need}" + (f" · {d.dd_progress} in progress" if d.dd_progress else ""))
                        if d.dd_total else "—"]
            elif stage == "Under Contract":
                what, when = d.key_date
                left = "" if days is None else (f" ({days} d)" if days >= 0 else " (past)")
                vals = [d.name, short(d.price_cents) if d.price_cents else "—",
                        short(d.deposit_cents) if d.deposit_cents else "—",
                        (_short_date(when) + left) if when else "—"]
                if what == "Firm":
                    vals[3] = "firm " + vals[3]
            else:
                vals = [d.name, d.stage, fmt_money(d.price_cents) if d.price_cents else "—",
                        _short_date(d.closing_date if d.stage == "Closed" else d.stage_date),
                        "yes" if d.holding_id else ""]
            tip = " · ".join(x for x in (d.address, d.asset_type, d.source,
                                         f"next: {d.next_step}" if d.next_step else "") if x)
            for c, v in enumerate(vals):
                colour = MUTED if stage == "Closed" and (c == 4 or d.stage == "Dead") else None
                if soon and ((stage == "Due Diligence" and c == 2) or (stage == "Under Contract" and c == 3)):
                    colour = NEGATIVE
                it = _cell(v, right=c in (1, 2) and stage != "Closed" or (stage == "Closed" and c == 2),
                           colour=colour)
                it.setToolTip(tip)
                t.setItem(r, c, it)
        t.blockSignals(False)

    # ---- selection ----
    def _only(self, stage: str) -> None:
        """One deal selected on the page at a time."""
        t = self.tables[stage]
        if not t.selectedItems():
            return
        self._picked = stage
        for s, other in self.tables.items():
            if s != stage:
                other.blockSignals(True)
                other.clearSelection()
                other.blockSignals(False)
        self._show_detail()

    def selected(self) -> dl.Deal | None:
        s = self._picked
        if s is None or s not in self.tables:
            return None
        r = self.tables[s].currentRow()
        rows = self.rows.get(s, [])
        if not self.tables[s].selectedItems() or not 0 <= r < len(rows):
            return None
        return rows[r]

    def select(self, deal_id: int) -> None:
        for s, rows in self.rows.items():
            for r, d in enumerate(rows):
                if d.id == deal_id:
                    self.tables[s].selectRow(r)
                    self._picked = s
                    self._only(s)
                    return

    def _show_detail(self) -> None:
        d = self.selected()
        if d is None:
            self.detail.setText("Select a deal; double-click to open it.")
            return
        bits = [f"<b>{html.escape(d.name)}</b>", html.escape(d.stage)]
        if d.address:
            bits.append(html.escape(d.address))
        if d.cap_rate is not None:
            bits.append(f"cap rate {d.cap_rate * 100:.2f}%")
        if d.per_unit:
            bits.append(f"{fmt_money(d.per_unit)} / unit")
        if d.lead:
            bits.append(f"lead {html.escape(d.lead)}")
        if d.next_step:
            bits.append(f"next: {html.escape(d.next_step)}")
        self.detail.setText(" · ".join(bits))

    def _need(self) -> dl.Deal | None:
        d = self.selected()
        if d is None:
            info(self, "Pick a deal", "Select a deal first.")
        return d

    # ---- actions ----
    def new_deal(self) -> None:
        dlg = DealDialog(self.pf, parent=self, companies=self.companies)
        if dlg.exec():
            self.refresh()
            self.select(dlg.deal.id)

    def open_deal(self, tab: int = 0) -> None:
        d = self._need()
        if not d:
            return
        dlg = DealDialog(self.pf, dl.deal(self.pf, d.id), self, companies=self.companies)
        dlg.tabs.setCurrentIndex(tab)
        if dlg.exec():
            self.refresh()
            self.select(d.id)

    def open_checklist(self) -> None:
        self.open_deal(tab=2)

    def advance(self) -> None:
        d = self._need()
        if not d:
            return
        if d.stage == "Under Contract":
            self.close_deal()
            return
        if d.stage in ("Closed", "Dead"):
            return
        dl.advance(self.pf, d.id)
        self.refresh()
        self.select(d.id)

    def move_back(self) -> None:
        d = self._need()
        if d and d.stage != "New":
            dl.back(self.pf, d.id)
            self.refresh()
            self.select(d.id)

    def mark_dead(self) -> None:
        d = self._need()
        if d and confirm(self, "Dead deal", f"Mark “{d.name}” dead? (It's kept under Closed & dead.)"):
            dl.move(self.pf, d.id, "Dead")
            from .. import deal_costs as dc

            paid = dc.prepaid(self.pf, d.id)
            costs_ = sum(p.costs for p in paid)
            deposits = sum(p.deposit for p in paid)
            if costs_ and confirm(self, "Write off the deal's costs",
                                  f"Write off the {fmt_money(costs_)} of costs paid on “{d.name}” as Abandoned Deal "
                                  "Costs in the paying companies' books?"
                                  + (f"\n\nThe deposit ({fmt_money(deposits)}) is left as it is — if it's refunded, "
                                     "categorize the refund to the deal's account; if it's lost, write it off from the "
                                     "deal's Deposits & Costs." if deposits else "")):
                dc.write_off(self.pf, d.id, d.name)
            self.refresh()

    def close_deal(self) -> None:
        """Under contract → closed: the new company, its opening balance sheet, and into the portfolio."""
        d = self._need()
        if not d:
            return
        if d.holding_id is not None and any(h.id == d.holding_id for h in self.pf.holdings()):
            info(self, "Already closed", f"“{d.name}” is already in the portfolio.")
            return
        if d.stage not in ("Under Contract", "Closed"):
            info(self, "Not under contract", "Move the deal to Under Contract first (Advance ▸).")
            return
        if self.create_company is None:
            error(self, "Can't create companies here", "Open the Deal Pipeline from Asset Management.")
            return
        dlg = CloseDealDialog(self.pf, dl.deal(self.pf, d.id), self.create_company, self)
        if not dlg.exec():
            return
        self.show_closed.setChecked(True)
        self.refresh()
        self.select(d.id)
        info(self, "Deal closed", f"“{d.name}” is closed: its company and opening balance sheet are set up, and it's "
                                  "in the portfolio (Dashboard, Financing & Cap Table). Switch to it in Accounting to "
                                  "start its bookkeeping.")
        if self.on_added:
            self.on_added(dlg.holding_id)

    def delete(self) -> None:
        d = self._need()
        if d and confirm(self, "Delete deal", f"Delete “{d.name}” and its checklist? (To keep it, Mark Dead.)"):
            dl.delete(self.pf, d.id)
            self.refresh()

