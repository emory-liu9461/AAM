"""Click a selected line again to unselect it (the highlight goes off), in every list and table of the app.

A double-click still opens the line: the unselect waits the double-click interval and is cancelled by a double-click.
Grids you type into (where clicking a selected cell starts editing) and Ctrl/Shift clicks are left as they are.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QAbstractItemView, QApplication

_EDIT = (QAbstractItemView.EditTrigger.SelectedClicked | QAbstractItemView.EditTrigger.CurrentChanged
         | QAbstractItemView.EditTrigger.AnyKeyPressed | QAbstractItemView.EditTrigger.EditKeyPressed)


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
        if view.selectionBehavior() != QAbstractItemView.SelectionBehavior.SelectRows or view.editTriggers() & _EDIT:
            return False
        sm = view.selectionModel()
        idx = view.indexAt(ev.position().toPoint())
        if sm is None or not idx.isValid() or not sm.isRowSelected(idx.row(), idx.parent()):
            return False
        if len({(i.row(), i.parent()) for i in sm.selectedIndexes()}) != 1:
            return False                  # several lines selected: a click picks just this one (as usual)
        self._view = view
        self._timer.start(QApplication.doubleClickInterval() + 20)
        return False


def install(app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None or getattr(app, "_click_to_unselect", None) is not None:
        return
    app._click_to_unselect = ClickToUnselect(app)
    app.installEventFilter(app._click_to_unselect)
