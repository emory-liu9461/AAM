"""Lender Package…: pick the files for a lender submission package, then copy them into a new folder in
Underwriting → Financing → Lender Submission Package (see lender_package.py)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from .. import lender_package as lpk
from .common import MUTED, NEGATIVE, error

ROLE = Qt.ItemDataRole.UserRole


class LenderPackageDialog(QDialog):
    def __init__(self, folder: Path, parent=None, *, lender: str = ""):
        super().__init__(parent)
        self.folder = Path(folder)
        self.package: Path | None = None
        self.setWindowTitle("Lender Submission Package")
        self.resize(980, 640)
        lay = QVBoxLayout(self)
        intro = QLabel("The documents a lender usually asks for, with the files found in the deal folder for each. "
                       "Tick the files to include — they're <b>copied</b> into a new folder in Underwriting → "
                       "Financing → Lender Submission Package (the originals stay where they are), a numbered folder "
                       "per document, with a checklist of what's included and what's missing.")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        row = QHBoxLayout()
        row.addWidget(QLabel("Package name:"))
        self.name = QLineEdit(lpk.package_name(lender=lender))
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Document / file", "Found", "Modified", "Where in the deal folder"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.setColumnWidth(0, 360)
        self.tree.setColumnWidth(1, 110)
        self.tree.setColumnWidth(2, 120)
        self.tree.itemChanged.connect(self._changed)
        lay.addWidget(self.tree, 1)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        btns = QHBoxLayout()
        for text, slot, tip in (("Add Files…", self.add_files, "Add files from anywhere to the selected document"),
                                ("Pick All", lambda: self._all(True), "Tick every file found"),
                                ("Pick None", lambda: self._all(False), "Untick every file")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        go = QPushButton("Generate Package")
        go.setObjectName("primary")
        go.clicked.connect(self.generate)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(go)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self.picks = lpk.candidates(self.folder)
        self._fill()

    # ---- the tree ----
    def _fill(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        for k, pk in enumerate(self.picks):
            top = QTreeWidgetItem([f"{k + 1:02d}  {pk.doc.name}"])
            top.setData(0, ROLE, k)
            top.setToolTip(0, pk.doc.note or pk.doc.name)
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            f = top.font(0)
            f.setBold(True)
            top.setFont(0, f)
            if pk.doc.note:
                top.setText(3, pk.doc.note)
                top.setForeground(3, MUTED)
            for c in pk.files:
                self._add_file(top, c)
            if not pk.files:
                top.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(top)
            top.setExpanded(bool(pk.files))
        self.tree.blockSignals(False)
        self._update()

    def _add_file(self, top: QTreeWidgetItem, c: lpk.Candidate) -> None:
        it = QTreeWidgetItem([c.path.name, c.why, c.modified.strftime("%Y-%m-%d") if c.modified else "", c.rel])
        it.setData(0, ROLE, c)
        it.setToolTip(0, str(c.path))
        it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(0, Qt.CheckState.Checked if c.picked else Qt.CheckState.Unchecked)
        if c.why != "in its folder":
            it.setForeground(1, MUTED)
        top.addChild(it)

    def _changed(self, *_):
        self._update()

    def _update(self) -> None:
        n_files = n_docs = 0
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            picked = sum(1 for j in range(top.childCount())
                         if top.child(j).checkState(0) == Qt.CheckState.Checked)
            self.tree.blockSignals(True)
            top.setText(1, f"{picked} picked" if picked else ("missing" if not top.childCount() else "none picked"))
            top.setForeground(1, NEGATIVE if not picked else MUTED)
            self.tree.blockSignals(False)
            n_files += picked
            n_docs += bool(picked)
        self.summary.setText(f"{n_files} file(s) for {n_docs} of {len(self.picks)} documents — the rest are listed "
                             "as missing on the checklist.")

    def _all(self, on: bool) -> None:
        self.tree.blockSignals(True)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)
        self.tree.blockSignals(False)
        self._update()

    def chosen(self) -> list[lpk.Pick]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            pk = self.picks[top.data(0, ROLE)]
            out.append(lpk.Pick(pk.doc, [top.child(j).data(0, ROLE) for j in range(top.childCount())
                                         if top.child(j).checkState(0) == Qt.CheckState.Checked]))
        return out

    def add_files(self, files=None) -> int:
        it = self.tree.currentItem()
        if it is None:
            error(self, "Pick a document", "Select the document the files are for first.")
            return 0
        top = it.parent() or it
        if files is None:
            files, _ = QFileDialog.getOpenFileNames(self, f"Add files – {top.text(0).strip()}", str(self.folder))
        self.tree.blockSignals(True)
        for f in files:
            p = Path(f)
            try:
                rel = p.relative_to(self.folder).as_posix()
            except ValueError:
                rel = str(p)
            c = lpk.Candidate(p, rel, True, "added", lpk._modified(p))
            self.picks[top.data(0, ROLE)].files.append(c)
            self._add_file(top, c)
        top.setExpanded(True)
        self.tree.blockSignals(False)
        self._update()
        return len(files)

    def generate(self) -> Path | None:
        picks = self.chosen()
        if not any(pk.files for pk in picks):
            error(self, "Nothing picked", "Tick the files to put in the package.")
            return None
        try:
            self.package = lpk.generate(self.folder, picks, self.name.text().strip() or None)
        except OSError as e:
            error(self, "Couldn't make the package", str(e))
            return None
        self.accept()
        return self.package
