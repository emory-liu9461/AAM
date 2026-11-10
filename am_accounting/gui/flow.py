"""Rows of buttons that wrap onto the next line when the window is narrow (instead of making it wider than the
screen)."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLayout, QSizePolicy, QWidget


class FlowLayout(QLayout):
    """Lays its widgets left to right, starting a new line when the next one doesn't fit."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, width):  # noqa: N802
        return self._do(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):  # noqa: N802
        w = h = 0
        for it in self._items:
            s = it.sizeHint()
            w += s.width() + self.spacing()
            h = max(h, s.height())
        return QSize(w, h)

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return size + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect: QRect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            w = it.widget()
            if w is not None and w.isHidden():
                continue
            hint = it.sizeHint()
            if hint.width() > rect.width() > 0:        # (a long label: as wide as the row)
                hint = QSize(rect.width(), hint.height())
            nx = x + hint.width() + sp
            if nx - sp > rect.right() + 1 and line > 0:
                x, y = rect.x(), y + line + sp
                nx, line = x + hint.width() + sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x, line = nx, max(line, hint.height())
        return y + line - rect.y()


def _row_widgets(row: QHBoxLayout) -> list[QWidget] | None:
    """The row's widgets, when it holds only widgets, spacing and stretch (nothing nested)."""
    out = []
    for i in range(row.count()):
        it = row.itemAt(i)
        if it.layout() is not None:
            return None
        if it.widget() is not None:
            out.append(it.widget())
    return out


def wrap_rows(root: QWidget, limit: int = 760) -> int:
    """Rows of widgets (buttons, labels, pickers) wider than ``limit`` become wrapping rows. Returns how many."""
    n = 0
    for lay in root.findChildren(QBoxLayout):
        n += _wrap_children(lay, limit)
    return n


def _wrap_children(parent: QBoxLayout, limit: int) -> int:
    n = 0
    for i in range(parent.count()):
        it = parent.itemAt(i)
        row = it.layout()
        if not isinstance(row, QHBoxLayout) or isinstance(row, FlowLayout):
            continue
        if row.minimumSize().width() <= limit:
            continue
        widgets = _row_widgets(row)
        if not widgets or len(widgets) < 3:
            continue
        holder = QWidget()
        flow = FlowLayout(holder, spacing=max(6, row.spacing()))
        for w in widgets:
            row.removeWidget(w)
            w.setSizePolicy(QSizePolicy.Policy.Preferred, w.sizePolicy().verticalPolicy())
            flow.addWidget(w)
        pol = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        pol.setHeightForWidth(True)
        holder.setSizePolicy(pol)
        parent.takeAt(i)
        row.deleteLater()
        parent.insertWidget(i, holder)
        n += 1
    return n


def scroll_inside(page: QWidget) -> bool:
    """The page's contents move into a scroll area inside it, so the page can be narrower than its contents (it
    scrolls sideways) without changing which widget the tab holds."""
    from PySide6.QtWidgets import QScrollArea, QVBoxLayout

    lay = page.layout()
    if lay is None or getattr(page, "_scrolls_inside", False):
        return False
    inner = QWidget()
    inner.setLayout(lay)                 # (moves the page's layout, and with it the page's contents, into inner)
    area = QScrollArea()
    area.setWidget(inner)
    area.setWidgetResizable(True)
    area.setFrameShape(QScrollArea.Shape.NoFrame)
    outer = QVBoxLayout(page)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.addWidget(area)
    page._scrolls_inside = True
    return True


def fit_pages(root: QWidget, limit: int = 1000) -> int:
    """Every tab page still wider than ``limit`` scrolls inside its tab. Returns how many."""
    from PySide6.QtWidgets import QTabWidget

    n = 0
    for tw in root.findChildren(QTabWidget):
        for i in range(tw.count()):
            page = tw.widget(i)
            if isinstance(page, QTabWidget) or page.minimumSizeHint().width() <= limit:
                continue
            n += scroll_inside(page)
    return n
