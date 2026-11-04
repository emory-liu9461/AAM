"""Dashboard: last updated, recent income, balance sheet summary, cash balance."""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (QDateEdit, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
                               QSizePolicy, QVBoxLayout, QWidget)

from ..books import Books
from ..dashboard import DashboardData, PeriodIncome, build_dashboard
from ..money import fmt_money

from .theme import c as tc

CSS = """
QFrame#card { background: palette(base); border: 1px solid palette(midlight); border-radius: 10px; }
QLabel#kicker { color: palette(placeholder-text); font-size: 9pt; font-weight: 600; letter-spacing: 1px; }
QLabel#big { font-size: 22pt; font-weight: 700; }
QLabel#title { font-size: 18pt; font-weight: 700; }
QLabel#muted { color: palette(placeholder-text); }
QFrame#attention { background: @attention_bg@; border: 1px solid @attention_border@; border-radius: 8px; }
QFrame#attention QLabel { color: @attention_text@; }
"""


def css() -> str:
    from .theme import colors

    out = CSS
    for k, v in colors().items():
        out = out.replace(f"@{k}@", v)
    return out


def _fmt_dt(iso: str | None) -> str:
    if not iso:
        return "never"
    dt = datetime.fromisoformat(iso)
    return f"{dt.strftime('%b')} {dt.day}, {dt.year} at {dt.strftime('%I:%M %p').lstrip('0')}"


def _fmt_d(d: date | str | None) -> str:
    if not d:
        return "—"
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _label(text: str = "", name: str = "", *, color: str | None = None, align_right=False) -> QLabel:
    l = QLabel(text)
    if name:
        l.setObjectName(name)
    if color:
        l.setStyleSheet(f"color: {color};")
    if align_right:
        l.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    return l


class Card(QFrame):
    def __init__(self, kicker: str):
        super().__init__()
        self.setObjectName("card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 14, 18, 14)
        self.lay.setSpacing(6)
        self.kicker = _label(kicker.upper(), "kicker")
        self.lay.addWidget(self.kicker)

    def clear_body(self) -> None:
        while self.lay.count() > 1:
            item = self.lay.takeAt(1)
            if item.widget():
                _discard(item.widget())
            elif item.layout():
                _delete_layout(item.layout())

    def rows(self, rows: list[tuple[str, str, str | None]], *, bold_last=False) -> QGridLayout:
        g = QGridLayout()
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(4)
        for r, (label, value, color) in enumerate(rows):
            l, v = _label(label), _label(value, color=color, align_right=True)
            if bold_last and r == len(rows) - 1:
                for w in (l, v):
                    f = w.font()
                    f.setBold(True)
                    w.setFont(f)
            g.addWidget(l, r, 0)
            g.addWidget(v, r, 1)
        g.setColumnStretch(0, 1)
        self.lay.addLayout(g)
        return g


def _discard(w: QWidget) -> None:
    """Remove a widget now (deleteLater alone leaves it painted until the event loop runs)."""
    w.hide()
    w.setParent(None)
    w.deleteLater()


def _delete_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            _discard(item.widget())
        elif item.layout():
            _delete_layout(item.layout())


def _link(text: str, slot) -> QPushButton:
    b = QPushButton(text)
    b.setFlat(True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setStyleSheet(f"QPushButton {{ color: {tc('link')}; text-align: left; padding: 2px 0; border: none; }}"
                    "QPushButton:hover { text-decoration: underline; }")
    b.clicked.connect(slot)
    return b


class DashboardTab(QWidget):
    request_report = Signal(str)
    request_documents = Signal(str)  # "invoice" or "bill"
    request_review = Signal()
    request_import = Signal()
    request_workspace = Signal()
    request_process_bills = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        self.data: DashboardData | None = None
        self.setStyleSheet(css())

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        page = QWidget()
        scroll.setWidget(page)
        root = QVBoxLayout(page)
        root.setContentsMargins(20, 16, 20, 20)
        root.setSpacing(14)

        # Header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(2)
        self.title = _label("", "title")
        self.updated = _label("", "muted")
        self.updated.setTextFormat(Qt.TextFormat.RichText)
        titles.addWidget(self.title)
        titles.addWidget(self.updated)
        head.addLayout(titles, 1)
        head.addWidget(QLabel("As of"))
        self.as_of = QDateEdit(calendarPopup=True)
        self.as_of.setDisplayFormat("MMM d, yyyy")
        self.as_of.setDate(QDate.currentDate())
        self.as_of.dateChanged.connect(self.refresh)
        head.addWidget(self.as_of)
        today = QPushButton("Today")
        today.clicked.connect(lambda: self.as_of.setDate(QDate.currentDate()))
        head.addWidget(today)
        invoice_btn = QPushButton("Create Invoice")
        invoice_btn.setToolTip("Make an itemized invoice for a customer (PDF, email, tracked in Accounts Receivable)")
        invoice_btn.clicked.connect(lambda: self.request_documents.emit("new:invoice"))
        head.addWidget(invoice_btn)
        self.bills_btn = QPushButton("Process All Bills")
        self.bills_btn.setToolTip("Read every invoice, bill and receipt in the Invoice Dump, match it to its bank "
                                  "payment and record the expense")
        self.bills_btn.clicked.connect(self.request_process_bills)
        head.addWidget(self.bills_btn)
        folder_btn = QPushButton("Company Folder…")
        folder_btn.setToolTip("Company folder and automatic saving of financial statements")
        folder_btn.clicked.connect(self.request_workspace)
        head.addWidget(folder_btn)
        imp = QPushButton("Import Statement…")
        imp.setObjectName("primary")
        imp.clicked.connect(self.request_import)
        head.addWidget(imp)
        root.addLayout(head)

        # Attention strip
        self.attention = QFrame()
        self.attention.setObjectName("attention")
        att = QHBoxLayout(self.attention)
        att.setContentsMargins(14, 8, 14, 8)
        self.attention_text = QLabel()
        att.addWidget(self.attention_text, 1)
        review = QPushButton("Review Transactions")
        review.clicked.connect(self.request_review)
        att.addWidget(review)
        root.addWidget(self.attention)

        # Row 1: cash + balance sheet
        row1 = QHBoxLayout()
        row1.setSpacing(14)
        self.cash_card = Card("Cash balance")
        self.bs_card = Card("Balance sheet summary")
        row1.addWidget(self.cash_card, 1)
        row1.addWidget(self.bs_card, 1)
        root.addLayout(row1)

        # Row 2: receivables + payables
        self.arap_card = Card("Receivables & payables")
        root.addWidget(self.arap_card)

        # Row 3: income periods
        root.addWidget(_label("INCOME", "kicker"))
        self.income_row = QHBoxLayout()
        self.income_row.setSpacing(14)
        self.income_cards: list[Card] = []
        root.addLayout(self.income_row)
        root.addStretch(1)
        self.refresh()

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def refresh(self) -> None:
        self.setStyleSheet(css())  # follows light / dark appearance
        qd = self.as_of.date()
        d = self.data = build_dashboard(self.books, date(qd.year(), qd.month(), qd.day()))
        self.title.setText(d.company)

        through = _fmt_d(d.data_through) if d.data_through else "no transactions yet"
        text = f"Last updated {_fmt_dt(d.last_import)} · Transactions through {through}"
        if d.data_through:
            lag = (d.as_of - date.fromisoformat(d.data_through)).days
            if lag > 7:
                text += f" · <span style='color:{tc('amber')}'><b>{lag} days behind</b> — import recent statements</span>"
        from ..inbox import get_config

        inbox = get_config(self.books)
        if inbox.enabled and inbox.folder:
            where = "OneDrive" if "onedrive" in inbox.folder.lower() else "folder"
            parts = [p for p in inbox.folder.replace("\\", "/").split("/") if p]
            short = ("…/" if len(parts) > 2 else "") + "/".join(parts[-2:])
            text += f"<br>📥 Auto-importing statements from {where}: {short}"
            self.updated.setToolTip(inbox.folder)
        from .. import workspace as ws

        wcfg = ws.get_config(self.books)
        from .. import invoice_dump as _dump

        new_bills = len(_dump.pending_files(self.books)) if wcfg.enabled else 0
        self.bills_btn.setEnabled(wcfg.enabled)
        self.bills_btn.setText(f"Process All Bills ({new_bills} new)" if new_bills else "Process All Bills")
        if wcfg.enabled:
            parts = [p for p in wcfg.root.replace("\\", "/").split("/") if p]
            short = ("…/" if len(parts) > 2 else "") + "/".join(parts[-2:])
            text += f"<br>📁 Company folder: {short}"
            if wcfg.frequency != "off":
                nxt = ws.next_due(self.books)
                text += (f" · statements saved {wcfg.frequency}" +
                         (f", next on {_fmt_d(nxt)}" if nxt else ""))
        else:
            text += (f"<br><span style='color:{tc('amber')}'>📁 No company folder yet — click "
                     "<b>Company Folder…</b> to keep statements, invoices and reports organized automatically</span>")
        self.updated.setText(text)

        bits = []
        if d.uncategorized:
            bits.append(f"<b>{d.uncategorized}</b> uncategorized transaction{'s' * (d.uncategorized != 1)}")
        if d.unreviewed:
            bits.append(f"<b>{d.unreviewed}</b> to review")
        from .. import invoice_dump

        n_dump = sum(1 for i in invoice_dump.items(self.books, "unmatched"))
        if n_dump:
            bits.append(f"<b>{n_dump}</b> invoice{'s' * (n_dump != 1)} in the Invoice Dump waiting for a match")
        from ..receivables import find_matches

        n_match = len(find_matches(self.books))
        if n_match:
            bits.append(f"<b>{n_match}</b> payment{'s' * (n_match != 1)} to match to invoices/bills")
        self.attention.setVisible(bool(bits))
        self.attention_text.setText("Needs attention: " + " · ".join(bits) +
                                    (" — figures may change once they're categorized." if d.uncategorized else ""))

        self._fill_cash(d)
        self._fill_bs(d)
        self._fill_ar_ap(d)
        self._fill_income(d)

    def _fill_cash(self, d: DashboardData) -> None:
        c = self.cash_card
        c.clear_body()
        c.kicker.setText(f"CASH BALANCE · {_fmt_d(d.as_of).upper()}")
        c.lay.addWidget(_label(fmt_money(d.cash_total), "big", color=tc('red') if d.cash_total < 0 else None))
        c.lay.addWidget(_label(f"Across {len(d.cash_accounts)} bank account{'s' * (len(d.cash_accounts) != 1)}",
                               "muted"))
        c.rows([(name, fmt_money(bal), tc('red') if bal < 0 else None) for name, bal in d.cash_accounts])
        c.lay.addStretch(1)
        c.lay.addWidget(_link("View cash flow statement →", lambda: self.request_report.emit("Statement of Cash Flows")))

    def _fill_bs(self, d: DashboardData) -> None:
        c = self.bs_card
        c.clear_body()
        L = self.books.labels()
        c.kicker.setText(f"BALANCE SHEET SUMMARY · {_fmt_d(d.as_of).upper()}")

        def section(title: str) -> None:
            lbl = _label(title.upper(), "kicker")
            lbl.setContentsMargins(0, 6, 0, 0)
            c.lay.addWidget(lbl)

        neg = lambda v: tc('red') if v < 0 else None  # noqa: E731
        section(L["assets"])
        c.rows([("Cash", fmt_money(d.cash_total), neg(d.cash_total)),
                ("Short-term assets", fmt_money(d.short_term_assets), neg(d.short_term_assets)),
                ("Long-term assets", fmt_money(d.long_term_assets), neg(d.long_term_assets)),
                (f"Total {L['assets'].lower()}", fmt_money(d.total_assets), None)], bold_last=True)
        section(f"{L['liabilities']} & {L['equity']}")
        c.rows([("Short-term debt", fmt_money(d.short_term_debt), None),
                ("Long-term debt", fmt_money(d.long_term_debt), None),
                (L["equity"], fmt_money(d.total_equity), neg(d.total_equity)),
                (f"Total {L['liabilities'].lower()} & {L['equity'].lower()}",
                 fmt_money(d.total_liabilities + d.total_equity), None)], bold_last=True)
        ok = d.balanced
        c.lay.addWidget(_label("✓ Balanced" if ok else "✗ Books out of balance", color=tc('green') if ok else tc('red')))
        c.lay.addStretch(1)
        c.lay.addWidget(_link("View balance sheet →", lambda: self.request_report.emit("Balance Sheet")))

    def _fill_ar_ap(self, d: DashboardData) -> None:
        """Accounts receivable and payable side by side, aged into 30 / 60 / 90-day buckets."""
        c = self.arap_card
        c.clear_body()
        ar, ap = d.receivables, d.payables
        c.kicker.setText(f"ACCOUNTS RECEIVABLE & PAYABLE · {_fmt_d(d.as_of).upper()}")

        # Headline: what's owed each way and the difference
        head = QGridLayout()
        head.setHorizontalSpacing(28)
        for col, (title, value, sub, color) in enumerate([
            ("Owed to you (AR)", ar.ledger_balance, f"{ar.open_count} open invoice{'s' * (ar.open_count != 1)}", None),
            ("You owe (AP)", ap.ledger_balance, f"{ap.open_count} open bill{'s' * (ap.open_count != 1)}", None),
            ("Net (AR − AP)", ar.ledger_balance - ap.ledger_balance, "", tc('green') if ar.ledger_balance >= ap.ledger_balance
             else tc('red')),
        ]):
            head.addWidget(_label(title, "muted"), 0, col)
            head.addWidget(_label(fmt_money(value), "big", color=color), 1, col)
            if sub:
                head.addWidget(_label(sub, "muted"), 2, col)
        head.setColumnStretch(3, 1)
        c.lay.addLayout(head)

        # Aging table: rows = age, columns = AR / AP / Net
        g = QGridLayout()
        g.setHorizontalSpacing(24)
        g.setVerticalSpacing(5)
        for col, text in enumerate(["Age (days past due)", "Receivable (AR)", "Payable (AP)", "Net"]):
            h = _label(text, "kicker")
            if col:
                h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            g.addWidget(h, 0, col)
        names = {"Current": "Current (not yet due)", "1–30 days": "1–30 days", "31–60 days": "31–60 days",
                 "61–90 days": "61–90 days", "Over 90 days": "Over 90 days"}
        ar_b, ap_b = dict(ar.buckets), dict(ap.buckets)
        rows = [(names[k], ar_b.get(k, 0), ap_b.get(k, 0), k != "Current") for k in names]
        if ar.not_itemized or ap.not_itemized:
            rows.append(("Not from invoices/bills (e.g. opening balance)", ar.not_itemized, ap.not_itemized, False))
        rows.append(("Total", ar.ledger_balance, ap.ledger_balance, False))
        for r, (label, a, b, overdue) in enumerate(rows, start=1):
            total = label == "Total"
            cells = [_label(label)]
            for v, warn in ((a, overdue), (b, overdue), (a - b, False)):
                cells.append(_label(fmt_money(v) if v else "—", color=tc('red') if warn and v else None, align_right=True))
            for col, w in enumerate(cells):
                if total:
                    f = w.font()
                    f.setBold(True)
                    w.setFont(f)
                    w.setStyleSheet(w.styleSheet() + "border-top: 1px solid palette(mid); padding-top: 3px;")
                g.addWidget(w, r, col)
        for col in range(4):
            g.setColumnStretch(col, 2 if col == 0 else 1)
        c.lay.addLayout(g)

        notes = []
        if ar.overdue_total:
            notes.append(f"⚠ {fmt_money(ar.overdue_total)} overdue from customers ({ar.overdue_count} invoice"
                         f"{'s' * (ar.overdue_count != 1)})")
        if ap.overdue_total:
            notes.append(f"⚠ {fmt_money(ap.overdue_total)} overdue to vendors ({ap.overdue_count} bill"
                         f"{'s' * (ap.overdue_count != 1)})")
        if notes:
            c.lay.addWidget(_label("   ·   ".join(notes), color=tc('red')))
        parties = []
        if ar.top_parties:
            parties.append("Largest customers: " + ", ".join(f"{n} {fmt_money(v)}" for n, v in ar.top_parties))
        if ap.top_parties:
            parties.append("Largest vendors: " + ", ".join(f"{n} {fmt_money(v)}" for n, v in ap.top_parties))
        if parties:
            p = _label("\n".join(parties), "muted")
            p.setWordWrap(True)
            c.lay.addWidget(p)
        if not ar.open_count and not ap.open_count:
            c.lay.addWidget(_label("No open invoices or bills — create them to track who owes you and what you owe.",
                                   "muted"))
        links = QHBoxLayout()
        for text, spec in [("View invoices →", "invoice"), ("New invoice…", "new:invoice"),
                           ("View bills →", "bill"), ("New bill…", "new:bill")]:
            links.addWidget(_link(text, lambda _=False, s=spec: self.request_documents.emit(s)))
        links.addStretch(1)
        c.lay.addLayout(links)

    def _fill_income(self, d: DashboardData) -> None:
        while len(self.income_cards) < len(d.income):
            card = Card("")
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.income_cards.append(card)
            self.income_row.addWidget(card, 1)
        for card, p in zip(self.income_cards, d.income):
            self._fill_period(card, p)

    def _fill_period(self, c: Card, p: PeriodIncome) -> None:
        c.clear_body()
        c.kicker.setText(f"LAST {p.days} DAYS")
        c.lay.addWidget(_label(f"{_fmt_d(p.start)} – {_fmt_d(p.end)}", "muted"))
        c.lay.addWidget(_label(fmt_money(p.net), "big", color=tc('green') if p.net >= 0 else tc('red')))
        c.lay.addWidget(_label("Net income", "muted"))
        margin = f"{p.margin:.0%}" if p.margin is not None else "—"
        c.rows([("Revenue", fmt_money(p.revenue), None),
                ("Expenses", fmt_money(-p.expenses) if p.expenses else fmt_money(0), None),
                ("Profit margin", margin, None)])
        c.lay.addStretch(1)
        c.lay.addWidget(_link("View income statement →",
                              lambda _=False, days=p.days: self.request_report.emit(f"Income Statement (P&L)|{days}")))
