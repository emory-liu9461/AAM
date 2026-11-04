"""Side panel listing transactions that need a category: transfers, wires, checks, deposits, etc."""

from __future__ import annotations

from datetime import date
import html

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAbstractTextDocumentLayout, QPalette, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QPushButton, QSplitter, QStyle,
                               QStyledItemDelegate, QStyleOptionViewItem, QVBoxLayout, QWidget)

from .. import receivables as rx
from .. import review_queue as rq
from ..books import Books
from ..money import fmt_money
from .common import NEGATIVE, POSITIVE, error, fill_account_combo
from .theme import c as tc

KIND_COLORS = {"e-Transfer": "#c2410c", "Bank draft": "#8a4b00", "Wire": "#7a3cc2", "Transfer": "#2f6fde", "Zelle / Venmo / PayPal": "#0f7d8c", "Check": "#9a6700",
               "ATM / cash": "#5b6573", "ACH / electronic": "#3d6b2f", "Deposit": "#1b7f3b", "Other": "#5b6573"}

CSS = """
QLabel#desc { font-weight: 700; font-size: 11pt; }
QLabel#badge { color: white; border-radius: 4px; padding: 1px 6px; font-size: 8pt; font-weight: 700; }
QLabel#muted { color: palette(placeholder-text); }
QLabel#count { background: #b3261e; color: white; border-radius: 9px; padding: 1px 7px; font-weight: 700; }
QFrame#detail { background: palette(base); border: 1px solid palette(midlight); border-radius: 8px; }
QPushButton#quick { text-align: left; padding: 5px 8px; }
QListWidget::item:selected { background: @select_bg@; border-left: 3px solid @select_border@; }
QFrame#match { background: @match_bg@; border: 1px solid @match_border@; border-radius: 6px; }
QFrame#match QLabel { color: @match_text@; }
"""


HTML_ROLE = Qt.ItemDataRole.UserRole + 1


class HtmlRowDelegate(QStyledItemDelegate):
    """Draws each row's rich text directly. (A widget per row made the list take tens of seconds to fill once there
    were thousands of transactions to categorize.)"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._heights: dict[tuple[str, int], int] = {}

    def _doc(self, html_text: str, width: int, font) -> QTextDocument:
        doc = QTextDocument()
        doc.setDocumentMargin(6)
        doc.setDefaultFont(font)
        doc.setHtml(html_text)
        doc.setTextWidth(max(80, width))
        return doc

    def paint(self, painter, option, index) -> None:
        html_text = index.data(HTML_ROLE)
        if not html_text:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        (widget.style() if widget else QListWidget().style()).drawControl(
            QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        doc = self._doc(html_text, opt.rect.width(), opt.font)
        ctx = QAbstractTextDocumentLayout.PaintContext()
        ctx.palette.setColor(QPalette.ColorRole.Text, opt.palette.color(QPalette.ColorRole.Text))
        painter.save()
        painter.translate(opt.rect.topLeft())
        painter.setClipRect(0, 0, opt.rect.width(), opt.rect.height())
        doc.documentLayout().draw(painter, ctx)
        painter.restore()

    def sizeHint(self, option, index) -> QSize:
        html_text = index.data(HTML_ROLE)
        if not html_text:
            return super().sizeHint(option, index)
        view = option.widget
        width = (view.viewport().width() if view is not None else 300) - 4
        key = (html_text, width)
        h = self._heights.get(key)
        if h is None:
            if len(self._heights) > 20000:
                self._heights.clear()
            h = self._heights[key] = int(self._doc(html_text, width, option.font).size().height()) + 2
        return QSize(width, h)


def css() -> str:
    from .theme import colors

    out = CSS
    for k, v in colors().items():
        out = out.replace(f"@{k}@", v)
    return out


def _fmt_d(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%b')} {d.day}, {d.year}"


class ReviewPanel(QWidget):
    """'To Categorize': pick a transaction, say what it was, move on to the next."""

    changed = Signal()

    def __init__(self, books: Books):
        super().__init__()
        self.books = books
        self.items: list[rq.QueueItem] = []
        self.current: rq.QueueItem | None = None
        self.setStyleSheet(css())
        self.setMinimumWidth(340)
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 8)

        head = QHBoxLayout()
        title = QLabel("To Categorize")
        f = title.font()
        f.setPointSize(f.pointSize() + 3)
        f.setBold(True)
        title.setFont(f)
        self.count = QLabel("0")
        self.count.setObjectName("count")
        head.addWidget(title)
        head.addWidget(self.count)
        head.addStretch(1)
        root.addLayout(head)
        self.filter = QComboBox()
        for key, label in rq.FILTERS.items():
            self.filter.addItem(label, key)
        self.filter.currentIndexChanged.connect(self.refresh)
        root.addWidget(self.filter)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Name:"))
        self.name_filter = QComboBox()
        self.name_filter.setToolTip("Show only transactions with / from this person or company (from matched "
                                    "e-Transfers, cheques and drafts, and names you set)")
        self.name_filter.currentIndexChanged.connect(lambda *_: self.refresh())
        name_row.addWidget(self.name_filter, 1)
        self.group = QCheckBox("Group by name")
        self.group.toggled.connect(lambda *_: self.refresh())
        name_row.addWidget(self.group)
        root.addLayout(name_row)

        split = QSplitter(Qt.Orientation.Vertical)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setAlternatingRowColors(True)
        self.list.setItemDelegate(HtmlRowDelegate(self.list))
        self.list.setResizeMode(QListView.ResizeMode.Adjust)  # re-wrap the rows when the panel is resized
        self.list.currentRowChanged.connect(self._select)
        split.addWidget(self.list)

        self.detail = QFrame()
        self.detail.setObjectName("detail")
        d = QVBoxLayout(self.detail)
        d.setContentsMargins(12, 10, 12, 10)
        d.setSpacing(6)
        self.desc = QLabel()
        self.desc.setObjectName("desc")
        self.desc.setWordWrap(True)
        self.desc.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        d.addWidget(self.desc)
        meta = QHBoxLayout()
        self.badge = QLabel()
        self.badge.setObjectName("badge")
        self.amount = QLabel()
        f = self.amount.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 2)
        self.amount.setFont(f)
        meta.addWidget(self.badge)
        meta.addStretch(1)
        meta.addWidget(self.amount)
        d.addLayout(meta)
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        d.addWidget(self.info)

        # what the e-Transfer history / cheque scan says about this transaction
        self.found_box = QFrame()
        self.found_box.setObjectName("match")
        fb = QVBoxLayout(self.found_box)
        fb.setContentsMargins(8, 6, 8, 6)
        self.found_label = QLabel()
        self.found_label.setWordWrap(True)
        self.found_label.setTextFormat(Qt.TextFormat.RichText)
        self.found_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        fb.addWidget(self.found_label)
        found_btns = QHBoxLayout()
        self.found_open = QPushButton("Open Scan")
        self.found_open.clicked.connect(self._open_found_file)
        self.found_unmatch = QPushButton("Not this one — Unmatch")
        self.found_unmatch.clicked.connect(self._unmatch_found)
        found_btns.addWidget(self.found_open)
        found_btns.addStretch(1)
        found_btns.addWidget(self.found_unmatch)
        fb.addLayout(found_btns)
        d.addWidget(self.found_box)

        who = QHBoxLayout()
        who.addWidget(QLabel("Name:"))
        self.name_edit = QComboBox()
        self.name_edit.setEditable(True)
        self.name_edit.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.name_edit.lineEdit().setPlaceholderText("Who was it with? e.g. Alex Morgan")
        who.addWidget(self.name_edit, 1)
        self.name_save = QPushButton("Set Name")
        self.name_save.clicked.connect(self._save_name)
        who.addWidget(self.name_save)
        d.addLayout(who)
        self.name_future = QCheckBox("Also for future transactions containing:")
        self.name_pattern = QLineEdit()
        self.name_future.toggled.connect(self.name_pattern.setEnabled)
        fut = QHBoxLayout()
        fut.addWidget(self.name_future)
        fut.addWidget(self.name_pattern, 1)
        d.addLayout(fut)

        self.match_box = QFrame()
        self.match_box.setObjectName("match")
        mb = QVBoxLayout(self.match_box)
        mb.setContentsMargins(8, 6, 8, 6)
        self.match_label = QLabel()
        self.match_label.setWordWrap(True)
        self.match_btn = QPushButton()
        self.match_btn.clicked.connect(self._apply_to_document)
        mb.addWidget(self.match_label)
        mb.addWidget(self.match_btn)
        d.addWidget(self.match_box)
        self.etransfer_btn = QPushButton("Who was this e-Transfer? Read e-Transfer History…")
        self.etransfer_btn.setToolTip("Add a screenshot, PDF, CSV or pasted text of your e-Transfer history; "
                                      "e-Transfers are matched by reference number, amount and date")
        self.etransfer_btn.clicked.connect(self._etransfer_history)
        d.addWidget(self.etransfer_btn)
        self.cheque_btn = QPushButton("Who was this cheque / draft? Read Cheques && Drafts…")
        self.cheque_btn.setToolTip("Add a scan or photo of the cheque or draft; it's matched by number and amount")
        self.cheque_btn.clicked.connect(self._cheques)
        d.addWidget(self.cheque_btn)
        self.invoice_btn = QPushButton("Create Invoice for This Deposit…")
        self.invoice_btn.setToolTip("Revenue with no invoice behind it: make the invoice now. It's recorded as "
                                    "paid by this deposit.")
        self.invoice_btn.clicked.connect(self._invoice_for_deposit)
        d.addWidget(self.invoice_btn)

        self.quick_label = QLabel("What was it?")
        d.addWidget(self.quick_label)
        self.quick_grid = QGridLayout()
        self.quick_grid.setSpacing(4)
        d.addLayout(self.quick_grid)
        d.addWidget(QLabel("Or choose any category:"))
        self.category = QComboBox()
        self.category.setMaxVisibleItems(25)
        d.addWidget(self.category)
        self.memo = QLineEdit()
        self.memo.setPlaceholderText("Note: who / what it was for (optional)")
        d.addWidget(self.memo)
        self.similar = QCheckBox()
        d.addWidget(self.similar)
        self.make_rule = QCheckBox("Make a rule for the future — description contains:")
        self.make_rule.setStyleSheet("QCheckBox { spacing: 6px; }")
        self.rule_pattern = QLineEdit()
        self.make_rule.toggled.connect(self.rule_pattern.setEnabled)
        d.addWidget(self.make_rule)
        d.addWidget(self.rule_pattern)

        btns = QHBoxLayout()
        self.save_btn = QPushButton("Save && Next")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(lambda: self._save(self.category.currentData()))
        self.confirm_btn = QPushButton("Guess is right")
        self.confirm_btn.clicked.connect(self._confirm)
        skip = QPushButton("Skip")
        skip.clicked.connect(self._skip)
        btns.addWidget(self.save_btn, 1)
        btns.addWidget(self.confirm_btn)
        btns.addWidget(skip)
        d.addLayout(btns)
        d.addStretch(1)
        from PySide6.QtWidgets import QScrollArea

        self.detail_scroll = QScrollArea()  # a short window scrolls the details instead of squashing them
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setWidget(self.detail)
        split.addWidget(self.detail_scroll)
        split.setSizes([330, 470])
        root.addWidget(split, 1)

        self.empty = QLabel("🎉  Nothing to categorize.\nNew transfers, wires, checks and other unclear "
                            "transactions from your statements will appear here.")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("muted")
        root.addWidget(self.empty)
        self.refresh()

    # -- data ---------------------------------------------------------------

    def set_books(self, books: Books) -> None:
        self.books = books
        self.refresh()

    def total(self) -> int:
        return rq.counts(self.books)["total"]

    def refresh(self, keep_id: int | None = None) -> None:
        self.setStyleSheet(css())  # follows light / dark appearance
        keep_id = keep_id if keep_id is not None else (self.current.tx.id if self.current else None)
        everyone = rq.queue(self.books, self.filter.currentData())
        counts: dict[str, list] = {}
        for q in everyone:
            if q.name:
                counts.setdefault(q.name.lower(), [q.name, 0])[1] += 1
        want = self.name_filter.currentData()
        self.name_filter.blockSignals(True)
        self.name_filter.clear()
        self.name_filter.addItem(f"Everyone ({len(everyone)})", None)
        for _k, (nm, n) in sorted(counts.items()):
            self.name_filter.addItem(f"{nm} ({n})", nm)
        no_name = sum(1 for q in everyone if not q.name)
        if no_name and counts:
            self.name_filter.addItem(f"No name yet ({no_name})", rq.NO_NAME)
        i = self.name_filter.findData(want)
        self.name_filter.setCurrentIndex(i if i >= 0 else 0)
        self.name_filter.blockSignals(False)
        want = self.name_filter.currentData()
        if want is None:
            self.items = everyone
        elif want == rq.NO_NAME:
            self.items = [q for q in everyone if not q.name]
        else:
            self.items = [q for q in everyone if q.name.lower() == want.lower()]
        if self.group.isChecked():
            self.items.sort(key=lambda q: (q.name == "", q.name.lower()))  # stable: keeps date order inside
        self.row_item: list = []
        self.count.setText(str(self.total()))
        self.list.blockSignals(True)
        self.list.clear()
        row_to_select = 0
        from .. import match_details

        last_group = object()
        group_sizes: dict[str, int] = {}
        for q in self.items:
            group_sizes[q.name.lower()] = group_sizes.get(q.name.lower(), 0) + 1
        for i, it in enumerate(self.items):
            t = it.tx
            if self.group.isChecked() and it.name.lower() != last_group:
                last_group = it.name.lower()
                head = QListWidgetItem(f"{it.name or 'No name yet'}  ·  "
                                       f"{group_sizes[last_group]}")
                hf = head.font()
                hf.setBold(True)
                head.setFont(hf)
                head.setFlags(Qt.ItemFlag.NoItemFlags)
                self.list.addItem(head)
                self.row_item.append(None)
            found = match_details.for_transaction(self.books, t.id)
            line2 = f"{_fmt_d(t.date)} · {self.books.account(t.bank_account_id).name} · {it.kind}"
            if it.reason != "Uncategorized":
                line2 += f" · guessed: {self.books.account(t.category_id).name}"
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, t.id)
            item.setToolTip(t.description)
            color = (POSITIVE if t.amount_cents > 0 else NEGATIVE).name()
            badge = KIND_COLORS.get(it.kind, "#5b6573")
            item.setData(HTML_ROLE, (
                f"<b>{html.escape(t.description)}</b><br>"
                f"<span style='color:{color}; font-weight:600'>"
                f"{'+' if t.amount_cents > 0 else ''}{fmt_money(t.amount_cents, parens=False)}</span>"
                f" <span style='color:{badge}; font-weight:600'>{html.escape(it.kind)}</span>"
                f"<span style='color:{tc('muted')}'> · {html.escape(line2.split(' · ', 1)[0])}"
                f"{' · guessed' if it.reason != 'Uncategorized' else ''}</span>"
                + (f"<br><span style='color:{tc('match_text')}; font-weight:600'>{html.escape(found.short)}</span>"
                   if found else f"<br><span style='color:{tc('match_text')}; font-weight:600'>👤 "
                                 f"{html.escape(it.name)}</span>" if it.name else "")))
            self.list.addItem(item)
            self.row_item.append(it)
            if t.id == keep_id:
                row_to_select = len(self.row_item) - 1
        self.list.blockSignals(False)
        has = bool(self.items)
        self.list.setVisible(has)
        self.detail.setVisible(has)
        self.detail_scroll.setVisible(has)
        self.empty.setVisible(not has)
        if has:
            if self.row_item[min(row_to_select, len(self.row_item) - 1)] is None:
                row_to_select += 1  # a group heading
            self.list.setCurrentRow(min(row_to_select, len(self.row_item) - 1))
            self._select(self.list.currentRow())
        else:
            self.current = None

    def _select(self, row: int) -> None:
        if row < 0 or row >= len(self.row_item) or self.row_item[row] is None:
            return
        it = self.current = self.row_item[row]
        t = it.tx
        self.desc.setText(t.description)
        self.badge.setText(it.kind.upper() + (f" #{it.check_no}" if it.check_no else ""))
        self.badge.setStyleSheet(f"background: {KIND_COLORS.get(it.kind, '#5b6573')};")
        self.amount.setText(("+" if t.amount_cents > 0 else "") + fmt_money(t.amount_cents, parens=False))
        self.amount.setStyleSheet(f"color: {(POSITIVE if t.amount_cents > 0 else NEGATIVE).name()};")
        info = f"{_fmt_d(t.date)} · {self.books.account(t.bank_account_id).name} · " \
               f"{'money in' if t.amount_cents > 0 else 'money out'}"
        if it.reason != "Uncategorized":
            info += f"\nThe app guessed “{self.books.account(t.category_id).name}”. Confirm or change it."
        if t.memo:
            info += f"\nMemo: {t.memo}"
        self.info.setText(info)

        from .. import match_details

        found = self.found = match_details.for_transaction(self.books, t.id)
        if found:
            facts = " · ".join(f"{html.escape(k)} <b>{html.escape(v)}</b>" for k, v in found.facts)
            src = "your e-Transfer history" if found.kind == "e-Transfer" else "the scan in Cheques &amp; Drafts"
            self.found_label.setText(f"<b>✓ {html.escape(found.headline)}</b><br>{facts}<br>"
                                     f"<span style='font-size:small'>Matched from {src}</span>")
            self.found_open.setVisible(bool(found.file and found.file.exists()))
            self.found_box.show()
        else:
            self.found_box.hide()
        if it.open_documents:
            doc = it.open_documents[0]
            label = rx.KIND_LABELS[doc.kind][0].lower()
            more = f" (+{len(it.open_documents) - 1} more)" if len(it.open_documents) > 1 else ""
            self.match_label.setText(f"Same amount as open {label} {doc.number} from {doc.party}, "
                                     f"due {doc.due_date}{more}.")
            self.match_btn.setText(f"Record as payment of {label} {doc.number}".replace("  ", " "))
            self.match_box.show()
        else:
            self.match_box.hide()
        self.invoice_btn.setVisible(t.amount_cents > 0 and not it.open_documents)
        from .. import etransfer as _et

        from .. import cheques as _cq

        self.cheque_btn.setVisible(_cq.line_kind(t.description) is not None
                                   and not _cq.files_for_transaction(self.books, t.id))
        self.etransfer_btn.setVisible(_et.is_etransfer(t.description)
                                      and _et.record_for_transaction(self.books, t.id) is None)

        while self.quick_grid.count():
            w = self.quick_grid.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        choices = rq.quick_choices(self.books, it)
        for i, (label, acct_id) in enumerate(choices):
            b = QPushButton(label)
            b.setObjectName("quick")
            b.setToolTip(self.books.account(acct_id).label)
            b.clicked.connect(lambda _=False, a=acct_id: self._save(a))
            b.setMinimumHeight(b.sizeHint().height())  # never squashed when the panel is short
            self.quick_grid.addWidget(b, i // 2, i % 2)
        self.quick_label.setVisible(bool(choices))
        from PySide6.QtWidgets import QSizePolicy

        for b in self.detail.findChildren(QPushButton):  # long buttons shrink to the panel instead of widening it
            if b.sizeHint().width() > 120:
                b.setSizePolicy(QSizePolicy.Policy.Ignored, b.sizePolicy().verticalPolicy())
                b.setMinimumWidth(100)

        fill_account_combo(self.category, self.books, include_none="Choose category…",
                           exclude={t.bank_account_id})
        i = self.category.findData(t.category_id) if t.category_id else 0
        self.category.setCurrentIndex(max(0, i))
        self.memo.setText(t.memo)
        n = len(it.similar_ids)
        if it.name:
            self.similar.setText(f"Also apply to {n} other{'s' * (n != 1)} with {it.name} in this list")
            self.similar.setChecked(n > 0)  # same person / company: usually the same category
        else:
            self.similar.setText(f"Also apply to {n} similar transaction{'s' * (n != 1)} in this list")
            self.similar.setChecked(n > 0 and found is None)  # matched ones are individual (other payee / person)
        self.similar.setVisible(n > 0)
        from .. import parties

        self.name_edit.blockSignals(True)
        self.name_edit.clear()
        self.name_edit.addItems(parties.all_names(self.books))
        self.name_edit.setCurrentText(it.name)
        self.name_edit.blockSignals(False)
        self.name_edit.setToolTip({"etransfer": "From the matched e-Transfer", "cheque": "From the matched cheque / "
                                   "draft", "rule": "From a name rule", "manual": "Set by you"}.get(it.name_source, ""))
        pattern = parties.suggested_pattern(t.description)
        self.name_pattern.setText(pattern)
        self.name_future.setChecked(False)
        self.name_future.setEnabled(bool(pattern))
        self.name_pattern.setEnabled(False)
        is_check = it.kind in ("Check", "Bank draft", "e-Transfer")
        self.make_rule.setChecked(False)
        self.make_rule.setEnabled(not is_check)
        self.make_rule.setToolTip("Cheques, drafts and e-Transfers differ every time — use the Name (and its "
                                  "remembered category) instead of a description rule" if is_check else "")
        self.rule_pattern.setText("" if is_check else rq.suggested_rule_pattern(t.description))
        self.rule_pattern.setEnabled(False)
        self.confirm_btn.setVisible(it.reason != "Uncategorized")

    # -- actions ------------------------------------------------------------

    def _next_id(self) -> int | None:
        row = next((i for i, q in enumerate(self.items) if self.current and q.tx.id == self.current.tx.id), 0)
        for it in self.items[row + 1:] + self.items[:row]:
            if self.current and it.tx.id != self.current.tx.id and it.tx.id not in self.current.similar_ids:
                return it.tx.id
        return None

    def _after_change(self, next_id: int | None) -> None:
        self.current = None
        self.refresh(keep_id=next_id)
        self.changed.emit()

    def _save(self, category_id) -> None:
        it = self.current
        if it is None:
            return
        if category_id in (None, "__sep__"):
            error(self, "Choose a category", "Pick one of the buttons above or choose a category from the list.")
            return
        ids = [it.tx.id] + (it.similar_ids if it.similar_ids and self.similar.isChecked() else [])
        pattern = self.rule_pattern.text() if self.make_rule.isChecked() else None
        nxt = self._next_id()
        try:
            rq.categorize(self.books, ids, category_id, memo=self.memo.text(), rule_pattern=pattern)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save", str(e))
            return
        self._after_change(nxt)

    def _confirm(self) -> None:
        if self.current:
            nxt = self._next_id()
            if self.memo.text().strip():
                self.books.set_memo(self.current.tx.id, self.memo.text().strip())
            rq.confirm_guess(self.books, self.current.tx.id)
            self._after_change(nxt)

    def _skip(self) -> None:
        nxt = self._next_id()
        if nxt is not None:
            self.refresh(keep_id=nxt)

    def _save_name(self) -> None:
        it = self.current
        if it is None:
            return
        from .. import parties

        name = self.name_edit.currentText().strip()
        try:
            if self.name_future.isChecked() and self.name_pattern.text().strip():
                parties.add_rule(self.books, self.name_pattern.text(), name)
            if name != it.name or it.name_source == "manual":
                parties.set_name(self.books, [it.tx.id], name)  # blank clears your name (automatic one returns)
            parties.apply_learned(self.books)  # a known name brings its usual category
        except ValueError as e:
            error(self, "Couldn't set the name", str(e))
            return
        self.refresh(keep_id=it.tx.id)
        self.changed.emit()

    def _open_found_file(self) -> None:
        f = getattr(self, "found", None)
        if f and f.file and f.file.exists():
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl.fromLocalFile(str(f.file)))

    def _unmatch_found(self) -> None:
        it = self.current
        f = getattr(self, "found", None)
        if not it or not f:
            return
        if f.kind == "e-Transfer":
            from .. import etransfer as et

            rec = et.record_for_transaction(self.books, it.tx.id)
            if rec:
                et.unlink(self.books, rec.id)
        else:
            from .. import cheques as cq

            row = self.books.conn.execute("SELECT id FROM cheques WHERE transaction_id=?", (it.tx.id,)).fetchone()
            if row:
                cq.unlink(self.books, row[0])
        self.refresh(keep_id=it.tx.id)
        self.changed.emit()

    def _cheques(self) -> None:
        from .cheques_dialog import ChequesDialog

        keep = self.current.tx.id if self.current else None
        ChequesDialog(self.books, self).exec()
        self.refresh(keep_id=keep)
        self.changed.emit()

    def _etransfer_history(self) -> None:
        from .etransfer_dialog import ETransferDialog

        keep = self.current.tx.id if self.current else None
        ETransferDialog(self.books, self).exec()
        self.refresh(keep_id=keep)
        self.changed.emit()

    def _invoice_for_deposit(self) -> None:
        it = self.current
        if not it or it.tx.amount_cents <= 0:
            return
        from .invoice_generator import InvoiceGeneratorDialog

        nxt = self._next_id()
        dlg = InvoiceGeneratorDialog(self.books, deposit_tx_id=it.tx.id, parent=self)
        if dlg.exec():
            self._after_change(nxt)

    def _apply_to_document(self) -> None:
        it = self.current
        if not it or not it.open_documents:
            return
        nxt = self._next_id()
        try:
            rx.record_payment(self.books, it.open_documents[0].id, it.tx.id)
            from .doc_files import sync

            sync(self.books, [it.open_documents[0].id])
            if self.memo.text().strip():
                self.books.set_memo(it.tx.id, self.memo.text().strip())
        except Exception as e:  # noqa: BLE001
            error(self, "Could not record payment", str(e))
            return
        self._after_change(nxt)
