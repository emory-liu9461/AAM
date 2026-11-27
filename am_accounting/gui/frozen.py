"""Keep a table's first column (e.g. the Budget's Category) in place while the rest scrolls sideways.

A second view of the same table — same rows, same selection — showing only the first column, laid over the table's
left edge and kept in step with its column width, row heights, header and up/down scrolling.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QTableView


class _Frozen(QObject):
    def __init__(self, table: QTableView):
        super().__init__(table)
        self.table = t = table
        self.view = v = QTableView(t)
        v.setObjectName("frozenColumn")
        v.setModel(t.model())
        v.setSelectionModel(t.selectionModel())
        v.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        v.verticalHeader().hide()
        v.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        v.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.setVerticalScrollMode(t.verticalScrollMode())
        v.setSelectionBehavior(t.selectionBehavior())
        v.setSelectionMode(t.selectionMode())
        v.setEditTriggers(t.editTriggers())
        v.setItemDelegate(t.itemDelegate())
        v.setShowGrid(t.showGrid())
        v.setFrameShape(QTableView.Shape.NoFrame)
        v.setStyleSheet("QTableView#frozenColumn { border-right: 1px solid palette(mid); }")
        t.viewport().stackUnder(v)
        v.horizontalHeader().setDefaultAlignment(t.horizontalHeader().defaultAlignment())
        # in step with the table
        t.horizontalHeader().sectionResized.connect(self._col_resized)
        t.verticalHeader().sectionResized.connect(lambda i, _o, n: v.setRowHeight(i, n))
        v.verticalScrollBar().valueChanged.connect(t.verticalScrollBar().setValue)
        t.verticalScrollBar().valueChanged.connect(v.verticalScrollBar().setValue)
        v.doubleClicked.connect(t.doubleClicked.emit)
        v.clicked.connect(t.clicked.emit)
        m = t.model()
        for sig in (m.columnsInserted, m.columnsRemoved, m.modelReset, m.layoutChanged, m.rowsInserted,
                    m.rowsRemoved, m.headerDataChanged):
            sig.connect(lambda *_: self._safe(self.sync))
        t.installEventFilter(self)
        self.sync()

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except RuntimeError:              # the table is being closed
            pass

    def _col_resized(self, i: int, _old: int, new: int) -> None:
        if i == 0:
            self.view.setColumnWidth(0, new)
            self.place()

    def sync(self) -> None:
        t, v = self.table, self.view
        for c in range(t.model().columnCount()):
            v.setColumnHidden(c, c != 0)
        v.setColumnWidth(0, t.columnWidth(0))
        for r in range(t.model().rowCount()):
            v.setRowHeight(r, t.rowHeight(r))
            if isinstance(t, QTableView) and t.columnSpan(r, 0) > 1:
                v.setSpan(r, 0, 1, 1)
        v.horizontalHeader().setHidden(t.horizontalHeader().isHidden())
        self.place()

    def place(self) -> None:
        t, v = self.table, self.view
        fw = t.frameWidth()
        head = t.horizontalHeader().height() if not t.horizontalHeader().isHidden() else 0
        v.horizontalHeader().setFixedHeight(head) if head else None
        left = fw + (t.verticalHeader().width() if not t.verticalHeader().isHidden() else 0)
        v.setGeometry(left, fw, t.columnWidth(0), t.viewport().height() + head)
        v.setVisible(t.model().columnCount() > 1)

    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is self.table and ev.type() in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            self._safe(self.place)
        return False


def freeze_first_column(table: QAbstractItemView) -> QTableView:
    """Lock ``table``'s first column in place when it scrolls sideways. Returns the overlay view."""
    f = getattr(table, "_frozen", None)
    if f is None:
        f = table._frozen = _Frozen(table)
    return f.view


class PinnedLast(QTableView):
    """The table's last column (the portfolio total) shown on its own at the right, beside the table, so it stays in
    the same spot when the companies scroll sideways. The table itself hides that column."""

    def __init__(self, table: QTableView):
        super().__init__()
        self.table = t = table
        self.setObjectName("pinnedColumn")
        self.setModel(t.model())
        self.setSelectionModel(t.selectionModel())
        self.verticalHeader().hide()
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(t.selectionBehavior())
        self.setFrameShape(QTableView.Shape.NoFrame)
        self.horizontalHeader().setDefaultAlignment(t.horizontalHeader().defaultAlignment())
        self.doubleClicked.connect(t.doubleClicked.emit)
        t.verticalScrollBar().valueChanged.connect(self.verticalScrollBar().setValue)
        self.hide()

    def sync(self) -> None:
        t = self.table
        n = t.model().columnCount()
        last = n - 1
        if n < 3:                          # nothing to pin (no companies yet)
            for c in range(n):
                t.setColumnHidden(c, False)
            self.hide()
            return
        self.setItemDelegate(t.itemDelegate())
        self.setShowGrid(t.showGrid())
        t.setColumnHidden(last, False)                # measured while shown (a hidden column has no width)
        width = max(t.columnWidth(last), 160)
        for c in range(n):
            t.setColumnHidden(c, c == last)
            self.setColumnHidden(c, c != last)
        self.setColumnWidth(last, width)
        for r in range(t.model().rowCount()):
            self.setRowHeight(r, t.rowHeight(r))
        head = t.horizontalHeader().sizeHint().height()
        t.horizontalHeader().setFixedHeight(head)
        self.horizontalHeader().setFixedHeight(head)
        self.setFixedWidth(width + 2)
        self.setFixedHeight(t.height() if t.minimumHeight() == t.maximumHeight() else t.sizeHint().height())
        self.show()


def with_pinned_total(table: QTableView):
    """``table`` beside its last column pinned at the right (see PinnedLast); returns the widget to lay out."""
    from PySide6.QtWidgets import QHBoxLayout, QWidget

    box = QWidget()
    lay = QHBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    lay.addWidget(table, 1)
    table._pinned = PinnedLast(table)
    lay.addWidget(table._pinned, 0, Qt.AlignmentFlag.AlignTop)
    return box
