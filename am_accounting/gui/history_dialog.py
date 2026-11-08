"""Change History and Backups & Restore windows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QTableWidgetItem, QVBoxLayout)

from .. import backups as bk
from .. import history as hs
from .common import confirm, error, info


class HistoryDialog(QDialog):
    def __init__(self, conn, title: str, parent=None):
        super().__init__(parent)
        from .asset_mgmt import _table

        self.conn = conn
        self.setWindowTitle(f"Change History – {title}")
        self.resize(1100, 640)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show:"))
        self.what = QComboBox()
        self.what.addItem("Everything", "")
        for k in hs.kinds(conn):
            self.what.addItem(k, k)
        self.what.currentIndexChanged.connect(lambda *_: self.fill())
        row.addWidget(self.what)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search: a name, an amount, who…")
        self.search.textChanged.connect(lambda *_: self.fill())
        row.addWidget(self.search, 1)
        lay.addLayout(row)
        self.table = _table(["When", "Who", "What", "Action", "Details"], 4)
        lay.addWidget(self.table, 1)
        self.note = QLabel()
        self.note.setObjectName("hint")
        lay.addWidget(self.note)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.fill()

    def fill(self) -> None:
        rows = hs.changes(self.conn, search=self.search.text(), what=self.what.currentData() or "")
        t = self.table
        t.setRowCount(len(rows))
        for r, c in enumerate(rows):
            for col, v in enumerate([c.at, c.who, c.what, c.action, c.summary]):
                it = QTableWidgetItem(v)
                if col == 4:
                    it.setToolTip(v)
                t.setItem(r, col, it)
        t.resizeColumnsToContents()
        t.horizontalHeader().setStretchLastSection(True)
        self.note.setText(f"{len(rows)} change(s), newest first (up to 2,000 shown). Recorded from version 0.9.3 on.")


class BackupsDialog(QDialog):
    """The file's backups; restore one (the file as it is now is kept first)."""

    def __init__(self, file_path, name: str, parent=None, *, root: Path | None = None, on_restore=None):
        super().__init__(parent)
        from .asset_mgmt import _table

        self.file_path, self.name, self.root, self.on_restore = Path(file_path), name, root, on_restore
        self.setWindowTitle(f"Backups & Restore – {name}")
        self.resize(820, 520)
        lay = QVBoxLayout(self)
        self.where = QLabel()
        self.where.setWordWrap(True)
        lay.addWidget(self.where)
        self.table = _table(["Date", "Kind", "Size", "File"], 3)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        now = QPushButton("Back Up Now")
        now.clicked.connect(self.backup_now)
        row.addWidget(now)
        self.restore_btn = QPushButton("Restore This Backup…")
        self.restore_btn.clicked.connect(self.restore)
        row.addWidget(self.restore_btn)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        hint = QLabel("A copy is made automatically the first time the file is opened each day (the last 30 are "
                      "kept), besides the company folder's scheduled backups. Restoring keeps the file as it is now in "
                      "Before Restore, so it can be undone.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.rows: list[bk.Backup] = []
        self.fill()

    def _root(self) -> Path:
        return self.root or bk.backup_root(self.file_path)

    def fill(self) -> None:
        self.where.setText(f"Backups of <b>{self.name}</b> in {self._root()}")
        rows = self.rows = bk.backups(self.file_path, self.name, self._root())
        t = self.table
        t.setRowCount(len(rows))
        for r, b in enumerate(rows):
            for c, v in enumerate([b.when.strftime("%Y-%m-%d %H:%M"), b.kind, f"{b.size / 1024:,.0f} KB",
                                   b.path.name]):
                t.setItem(r, c, QTableWidgetItem(v))
        t.resizeColumnsToContents()
        self.restore_btn.setEnabled(bool(rows) and self.on_restore is not None)
        if rows:
            t.selectRow(0)

    def backup_now(self) -> Path | None:
        import sqlite3
        from datetime import datetime

        try:
            dest = self._root() / bk.DAILY_DIR / f"{bk._stem(self.name)} {datetime.now():%Y-%m-%d %H%M%S}" \
                f"{self.file_path.suffix or '.amdb'}"
            p = bk._copy(None, self.file_path, dest)
        except (OSError, sqlite3.Error) as e:
            error(self, "Couldn't back up", str(e))
            return None
        self.fill()
        return p

    def restore(self) -> None:
        sel = self.table.selectionModel().selectedRows()
        if not sel or self.on_restore is None:
            return
        b = self.rows[sel[0].row()]
        if not confirm(self, "Restore", f"Replace {self.name} with the backup from {b.when:%Y-%m-%d %H:%M}?\n\n"
                                        "Everything entered since then is replaced; the file as it is now is kept in "
                                        "Before Restore, so this can be undone."):
            return
        try:
            before = self.on_restore(str(b.path))
        except Exception as e:  # noqa: BLE001 - a locked or unreadable file
            error(self, "Couldn't restore", str(e))
            return
        info(self, "Restored", f"{self.name} is back to {b.when:%Y-%m-%d %H:%M}.\n\nThe file as it was is kept in "
                               f"{before}.")
        self.fill()
