"""A list of the documents attached to a record, with Add / Open / Remove — files can be dropped on it."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout,
                               QWidget)

from .. import attachments as at
from .common import confirm, error


class _DropList(QListWidget):
    def __init__(self, on_files):
        super().__init__()
        self.on_files = on_files
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if files:
            self.on_files(files)
            e.acceptProposedAction()


class AttachmentsBox(QWidget):
    def __init__(self, conn, base: Path, kind: str, ref, parent=None, *, sub: str = "", compact: bool = False):
        super().__init__(parent)
        self.conn, self.base, self.kind, self.ref, self.sub = conn, Path(base), kind, ref, sub
        lay = QHBoxLayout(self) if compact else QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = _DropList(self.add_files)
        self.list.setToolTip("Drop files here to attach them")
        self.list.itemDoubleClicked.connect(lambda *_: self.open())
        if compact:
            self.list.setMaximumHeight(72)
        lay.addWidget(self.list, 1)
        bl = QVBoxLayout() if compact else QHBoxLayout()
        for text, slot in (("Attach Files…", self._pick), ("Open", self.open), ("Remove", self.remove),
                           ("Show Folder", self.show_folder)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            bl.addWidget(b)
        if not compact:
            bl.addStretch(1)
        lay.addLayout(bl)
        self.rows: list[at.Attachment] = []
        self.fill()

    def fill(self) -> None:
        self.rows = at.listed(self.conn, self.base, self.kind, self.ref)
        self.list.clear()
        for a in self.rows:
            it = QListWidgetItem(("" if a.exists else "⚠ missing: ") + a.name)
            it.setToolTip(f"{a.path}\nadded {a.added.replace('T', ' ')}")
            self.list.addItem(it)
        if not self.rows:
            hint = QListWidgetItem("No documents — Attach Files… or drop them here")
            hint.setFlags(Qt.ItemFlag.NoItemFlags)
            self.list.addItem(hint)

    def _pick(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Attach documents")
        if files:
            self.add_files(files)

    def add_files(self, files) -> int:
        n = 0
        for f in files:
            try:
                at.add(self.conn, self.base, self.kind, self.ref, f, sub=self.sub)
                n += 1
            except (OSError, ValueError) as e:
                error(self, "Couldn't attach", f"{Path(f).name}: {e}")
        self.fill()
        return n

    def _selected(self) -> at.Attachment | None:
        r = self.list.currentRow()
        return self.rows[r] if 0 <= r < len(self.rows) else None

    def open(self) -> None:
        a = self._selected()
        if a is not None and a.exists:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(a.path)))

    def remove(self) -> None:
        a = self._selected()
        if a is not None and confirm(self, "Remove", f"Remove {a.name}? (Its copy in the attachments folder is "
                                                     "deleted; the original isn't touched.)"):
            at.remove(self.conn, a)
            self.fill()

    def show_folder(self) -> None:
        a = self._selected()
        folder = a.path.parent if a is not None else self.base / at.FOLDERS.get(self.kind, "")
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
