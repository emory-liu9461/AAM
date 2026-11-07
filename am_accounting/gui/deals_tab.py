"""Deal Pipeline: new deals, deals under due diligence and deals under contract, side by side."""

from __future__ import annotations

import html
from datetime import date

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

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

    def __init__(self, pf, deal: dl.Deal | None = None, parent=None):
        super().__init__(parent)
        from .asset_mgmt import _money_box

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

        # due-diligence checklist
        ddw = QWidget()
        dlay = QVBoxLayout(ddw)
        self.progress = QLabel()
        dlay.addWidget(self.progress)
        self.dd = QTableWidget(0, 4)
        self.dd.setHorizontalHeaderLabels(["Done", "Item", "Due", "Note"])
        self.dd.verticalHeader().setVisible(False)
        self.dd.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.dd.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.dd.setColumnWidth(0, 50)
        self.dd.setColumnWidth(2, 140)
        dlay.addWidget(self.dd, 1)
        brow = QHBoxLayout()
        for text, slot in (("Add Item", lambda: self._add_item(dl.DDItem(""))), ("Remove Item", self._remove_item),
                           ("Add the Usual Items", self._usual)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            brow.addWidget(b)
        brow.addStretch(1)
        dlay.addLayout(brow)
        tabs.addTab(ddw, "Due Diligence")
        for it in (dl.checklist(pf, d.id) if d.id else []):
            self._add_item(it)

        # notes
        self.notes = QPlainTextEdit(d.notes)
        self.notes.setPlaceholderText("Anything about the deal: seller's story, pricing guidance, issues found…")
        tabs.addTab(self.notes, "Notes")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)
        for w in (self.price, self.noi, self.equity):
            w.valueChanged.connect(self._update)
        self.units.valueChanged.connect(self._update)
        self._update()

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
        done = sum(1 for r in range(self.dd.rowCount()) if self.dd.cellWidget(r, 0).isChecked())
        self.progress.setText(f"<b>{done} of {self.dd.rowCount()}</b> done" if self.dd.rowCount() else
                              "No checklist yet — Add the Usual Items, or your own. It starts by itself when the deal "
                              "moves to due diligence.")

    def _add_item(self, it: dl.DDItem) -> None:
        r = self.dd.rowCount()
        self.dd.insertRow(r)
        box = QCheckBox()
        box.setChecked(it.done)
        box.toggled.connect(self._update)
        self.dd.setCellWidget(r, 0, box)
        self.dd.setItem(r, 1, QTableWidgetItem(it.item))
        self.dd.setCellWidget(r, 2, _date_box(it.due))
        self.dd.setItem(r, 3, QTableWidgetItem(it.note))
        self._update()

    def _remove_item(self) -> None:
        r = self.dd.currentRow()
        if r >= 0:
            self.dd.removeRow(r)
            self._update()

    def _usual(self) -> None:
        have = {(self.dd.item(r, 1).text() if self.dd.item(r, 1) else "").lower() for r in range(self.dd.rowCount())}
        for x in dl.DD_ITEMS:
            if x.lower() not in have:
                self._add_item(dl.DDItem(x, due=_date_of(self.dd_deadline)))

    def items(self) -> list[dl.DDItem]:
        out = []
        for r in range(self.dd.rowCount()):
            text = self.dd.item(r, 1).text() if self.dd.item(r, 1) else ""
            note = self.dd.item(r, 3).text() if self.dd.item(r, 3) else ""
            out.append(dl.DDItem(text, self.dd.cellWidget(r, 0).isChecked(), _date_of(self.dd.cellWidget(r, 2)), note))
        return out

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
        try:
            new = d.id is None
            dl.save(self.pf, d)
            items = self.items()
            if new and d.stage == "Due Diligence" and not items:
                dl.start_checklist(self.pf, d.id)
            else:
                dl.save_checklist(self.pf, d.id, items)
        except ValueError as e:
            error(self, "Couldn't save", str(e))
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

    def __init__(self, pf, parent=None, on_added=None):
        super().__init__(parent)
        from .asset_mgmt import Card, _table

        self.pf = pf
        self.on_added = on_added          # called with a new holding's id when a deal is added to the portfolio
        lay = QVBoxLayout(self)
        bar = QHBoxLayout()
        for text, slot, tip in (
                ("New Deal…", self.new_deal, "Add a deal you're looking at"),
                ("Open…", self.open_deal, "The selected deal's details, due-diligence checklist and notes"),
                ("◂ Back", self.move_back, "Move the selected deal back a stage"),
                ("Advance ▸", self.advance, "New → Due Diligence → Under Contract → Closed"),
                ("Mark Dead", self.mark_dead, "The deal didn't go ahead (kept under Closed & dead)"),
                ("Add to Portfolio…", self.add_to_portfolio, "Closed: add it to the portfolio as a holding"),
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
                vals = [d.name, short(d.price_cents) if d.price_cents else "—",
                        _short_date(d.dd_deadline) + left, f"{d.dd_done} / {d.dd_total}" if d.dd_total else "—"]
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
        dlg = DealDialog(self.pf, parent=self)
        if dlg.exec():
            self.refresh()
            self.select(dlg.deal.id)

    def open_deal(self) -> None:
        d = self._need()
        if d and DealDialog(self.pf, dl.deal(self.pf, d.id), self).exec():
            self.refresh()
            self.select(d.id)

    def advance(self) -> None:
        d = self._need()
        if not d:
            return
        if d.stage == "Under Contract":
            if confirm(self, "Closed", f"Mark “{d.name}” closed?"):
                dl.move(self.pf, d.id, "Closed")
                self.show_closed.setChecked(True)
                self.refresh()
                self.select(d.id)
                if confirm(self, "Add to the portfolio", f"Add “{d.name}” to the portfolio as a holding now?"):
                    self.add_to_portfolio()
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
            self.refresh()

    def add_to_portfolio(self) -> None:
        d = self._need()
        if not d:
            return
        if d.stage != "Closed":
            info(self, "Not closed yet", "Close the deal first (Advance ▸ when it's under contract).")
            return
        try:
            hid = dl.add_to_portfolio(self.pf, d.id)
        except ValueError as e:
            error(self, "Couldn't add", str(e))
            return
        info(self, "In the portfolio", f"“{d.name}” is a holding now, with its purchase price as the first "
                                       "valuation. Link it to its books from its Dashboard (Edit Holding…).")
        self.refresh()
        if self.on_added:
            self.on_added(hid)

    def delete(self) -> None:
        d = self._need()
        if d and confirm(self, "Delete deal", f"Delete “{d.name}” and its checklist? (To keep it, Mark Dead.)"):
            dl.delete(self.pf, d.id)
            self.refresh()

