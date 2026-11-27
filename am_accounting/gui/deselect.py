"""Click a selected line again to unselect it (the highlight goes off), in every list and table of the app.

A double-click still opens the line: the unselect waits the double-click interval and is cancelled by a double-click.
A whole column or several lines selected (blue): clicking in them, or the column's heading again, turns it all off.
Clicking a selected cell you can type into still starts typing; Ctrl/Shift clicks are left as they are.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication, QHeaderView

# a click that starts editing (a selected cell clicked again) is left alone; double-click / typing to edit is fine
_EDIT = QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.CurrentChanged


def unselect(view: QAbstractItemView) -> None:
    sm = view.selectionModel()
    if sm is not None:
        sm.clearSelection()
        sm.clearCurrentIndex()


class ClickToUnselect(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._view = None

    def _fire(self) -> None:
        v, self._view = self._view, None
        if v is not None:
            try:
                unselect(v)
            except RuntimeError:          # the view was deleted meanwhile
                pass

    def eventFilter(self, obj, ev):  # noqa: N802
        t = ev.type()
        if t not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            return False
        view = obj.parent() if obj.parent() is not None else None
        if not isinstance(view, QAbstractItemView) or obj is not view.viewport():
            return False
        if t == QEvent.Type.MouseButtonDblClick:
            self._timer.stop()
            self._view = None
            return False
        if ev.button() != Qt.MouseButton.LeftButton or ev.modifiers() & (
                Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier):
            return False
        pos = ev.position().toPoint()
        if isinstance(view, QHeaderView):
            return self._header_click(view, pos)
        sm = view.selectionModel()
        idx = view.indexAt(pos)
        if sm is None or not idx.isValid():
            return False
        rows = view.selectionBehavior() == QAbstractItemView.SelectionBehavior.SelectRows
        picked = sm.isRowSelected(idx.row(), idx.parent()) if rows else sm.isSelected(idx)
        if not picked:
            return False
        n = len({(i.row(), i.parent()) for i in sm.selectedIndexes()}) if rows else len(sm.selectedIndexes())
        if n > 1:
            # a whole column / several lines are blue: clicking in them turns it all off
            unselect(view)
            return True
        if view.editTriggers() & _EDIT and idx.flags() & Qt.ItemFlag.ItemIsEditable:
            return False                  # clicking the selected cell starts typing in it
        self._view = view
        self._timer.start(QApplication.doubleClickInterval() + 20)
        return False

    def _header_click(self, header: QHeaderView, pos) -> bool:
        """A column (or row) heading clicked when its column is already selected: unselect it."""
        table = header.parentWidget()
        if not isinstance(table, QAbstractItemView) or table.selectionModel() is None:
            return False
        section = header.logicalIndexAt(pos)
        if section < 0:
            return False
        sm, root = table.selectionModel(), table.rootIndex()
        if header.orientation() == Qt.Orientation.Horizontal:
            if not (sm.isColumnSelected(section, root) and table.model().rowCount(root)):
                return False
        elif not sm.isRowSelected(section, root):
            return False
        unselect(table)
        return True


def install(app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None or getattr(app, "_click_to_unselect", None) is not None:
        return
    app._click_to_unselect = ClickToUnselect(app)
    app.installEventFilter(app._click_to_unselect)
