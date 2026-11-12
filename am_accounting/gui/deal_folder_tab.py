"""A deal's Deal Folder tab: its folders laid out as the standard (Underwriting, then Asset Management), what's
missing, and filing documents into them."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import deal_folders as df
from .common import MUTED, NEGATIVE, POSITIVE, confirm, error, info


def ask_where(parent, pf, deal, *, choose=None, pick_dir=None) -> Path | None:
    """A deal without a folder: choose its folder (e.g. "123 ABC St"). That folder *is* the deal's folder —
    Underwriting (and Asset Management once it closes) go straight inside it; anything already in it goes into
    Previous Files. Returns it; None when skipped."""
    from PySide6.QtWidgets import QMessageBox

    box = QMessageBox(parent)
    box.setWindowTitle("Deal folder")
    box.setIcon(QMessageBox.Icon.Question)
    box.setText(f"Choose the folder for “{deal.name}”")
    box.setInformativeText(f"Pick (or make) the deal's own folder — e.g. “{df.folder_name(deal)}”. Underwriting goes "
                           "in it now, and Asset Management when the deal closes. Anything already in it is kept in "
                           "Previous Files.")
    pick = box.addButton("Choose Folder…", QMessageBox.ButtonRole.AcceptRole)
    later = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
    if choose is None:
        box.exec()
        clicked = box.clickedButton()
    else:                                  # (tests: "pick" | "later")
        clicked = pick if choose() in ("pick", "here", "other") else later
    if clicked is not pick:
        return None
    pick_dir = pick_dir or (lambda title, start="": QFileDialog.getExistingDirectory(parent, title, start))
    cur = df.folder_of(pf, deal)
    d = pick_dir(f"The folder for {deal.name}", str(cur.parent) if cur else "")
    if not d:
        return None
    target = Path(d)
    df.set_folder_of(pf, deal, target)
    try:
        moved = df.move_to_previous(target) if df.lpth.exists(target) else 0
        df.create(target, df.context(pf, deal), asset_management=df.wants_asset_management(deal))
    except OSError as e:
        error(parent, "Couldn't make all the folders", str(e))
        return target if df.lpth.exists(target) else None
    if moved and choose is None:
        info(parent, "Previous files", f"The folder already had {moved} file(s) / folder(s) in it — they're in "
                                       f"“{df.PREVIOUS}”, as they were.")
    return target


class _Tree(QTreeWidget):
    def __init__(self, on_drop):
        super().__init__()
        self.on_drop = on_drop
        self.setAcceptDrops(True)
        self.setDragDropMode(QTreeWidget.DragDropMode.DropOnly)

    def dragEnterEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dragMoveEvent(self, e):  # noqa: N802
        if e.mimeData().hasUrls():
            it = self.itemAt(e.position().toPoint())
            if it is not None:
                self.setCurrentItem(it)
            e.acceptProposedAction()

    def dropEvent(self, e):  # noqa: N802
        it = self.itemAt(e.position().toPoint())
        files = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if it is not None and files:
            self.on_drop(it, files)
            e.acceptProposedAction()


class DealFolderTab(QWidget):
    def __init__(self, pf, deal, parent=None):
        super().__init__(parent)
        self.pf, self.deal = pf, deal
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Folder:"))
        self.path = QLineEdit()
        self.path.setReadOnly(True)
        self.path.setPlaceholderText("No folder chosen for this deal yet")
        row.addWidget(self.path, 1)
        own = QPushButton("Change Folder…")
        own.setToolTip("Use another folder for this deal (e.g. one that already exists)")
        own.clicked.connect(self.pick_own)
        row.addWidget(own)
        lay.addLayout(row)
        self.where = QLabel()
        self.where.setTextFormat(Qt.TextFormat.RichText)
        self.where.setWordWrap(True)
        lay.addWidget(self.where)
        from PySide6.QtWidgets import QFrame

        self.banner = QFrame()                  # no folder for this deal yet: pick one
        self.banner.setObjectName("card")
        bl = QHBoxLayout(self.banner)
        self.banner_text = QLabel("This deal has no folder yet.")
        self.banner_text.setWordWrap(True)
        bl.addWidget(self.banner_text, 1)
        pick = QPushButton("Choose a Folder for This Deal…")
        pick.setObjectName("primary")
        pick.setToolTip("Pick (or make) the deal's own folder: Underwriting and Asset Management go straight in it")
        pick.clicked.connect(self.choose)
        bl.addWidget(pick)
        lay.addWidget(self.banner)
        brow = QHBoxLayout()
        self.btns = {}
        for key, text, slot, tip in (
                ("setup", "Set Up Folders", self.setup, "Make the folders that aren't there yet (nothing is removed)"),
                ("open", "Open Folder", self.open_selected, "Open the selected folder (or the deal's)"),
                ("add", "Add Files Here…", self.add_files, "Copy files into the selected folder, named as its "
                                                            "naming convention says"),
                ("package", "Lender Package…", self.lender_package,
                 "Copy the documents a lender asks for into a new folder in Underwriting → Financing → Lender "
                 "Submission Package — you pick the files"),
                ("archive", "Archive Old Versions…", self.archive,
                 "Keep only the newest version of each document; move the older ones to the folder's Archive"),
                ("link", "Link the Company's Accounting", self.link,
                 "Make Asset Management → Accounting the company's folder: statements, bills, financial statements "
                 "and backups are saved into it (done automatically when the deal closes)"),
                ("close", "Set Up Asset Management", self.asset_management,
                 "Make the Asset Management folders and copy the underwriting's APS and corporate documents over "
                 "(done automatically when the deal closes)")):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            brow.addWidget(b)
            self.btns[key] = b
        brow.addStretch(1)
        from PySide6.QtWidgets import QCheckBox

        self.show_files = QCheckBox("Show files")
        self.show_files.setChecked(True)
        self.show_files.setToolTip("Show the files and your own subfolders in each folder (double-click a file to open "
                                   "it)")
        self.show_files.toggled.connect(lambda *_: self.fill())
        brow.addWidget(self.show_files)
        lay.addLayout(brow)
        self.tree = _Tree(self._dropped)
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Folder", "Check", "Files", "Notes / naming"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.tree.itemDoubleClicked.connect(lambda *_: self.open_selected())
        lay.addWidget(self.tree, 1)
        hint = QLabel("Only the live file stays in a folder; older versions go in its Archive. Required items (⚠ when "
                      "empty) need a file. 📄 files and 📁 your own subfolders are listed in each folder — double-click "
                      "one to open it. Drop files on a folder to file them there.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.items: list[df.Item] = []
        self.fill()

    # ---- state ----
    def folder(self) -> Path | None:
        return df.folder_of(self.pf, self.deal) if self.deal.id is not None else None

    def _am(self) -> bool:
        f = self.folder()
        return df.wants_asset_management(self.deal) or bool(f and (f / "Asset Management").exists())

    def _key(self, node: QTreeWidgetItem) -> str:
        it = self.items[node.data(0, Qt.ItemDataRole.UserRole)]
        return f"{it.rel}|{it.label}"

    def fill(self) -> None:
        keep_open, keep_cur = set(), None        # what was open and selected, kept across the refresh
        had = bool(self.items)
        if self.items:
            stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
            while stack:
                n = stack.pop()
                if n.isExpanded():
                    keep_open.add(self._key(n))
                stack += [n.child(i) for i in range(n.childCount())]
            if self.tree.currentItem() is not None:
                keep_cur = self._key(self.tree.currentItem())
        self.tree.clear()
        f = self.folder()
        ready = f is not None and df.lpth.exists(f)
        self.path.setText(str(f) if f else "")
        self.banner.setVisible(self.deal.id is not None and not ready)
        self.banner_text.setText("This deal has no folder yet — choose its folder (e.g. “"
                                 f"{df.folder_name(self.deal)}”): Underwriting and Asset Management go straight in it."
                                 if f is None else
                                 f"This deal's folder isn't there anymore ({f}) — choose its folder again.")
        for k, b in self.btns.items():
            b.setEnabled(f is not None if k == "setup" else ready)
        if self.deal.id is None:
            self.where.setText("Save the deal, then open it again to set up its folder.")
            return
        if f is None:
            self.where.setText("")          # (the box below asks where it goes)
            return
        ctx = df.context(self.pf, self.deal)
        am = self._am()
        rows = df.check(f, ctx, asset_management=am)
        done, total = df.summary(rows)
        state = (f"<span style='color:{POSITIVE.name()}'>all {total} required items in place</span>" if done == total
                 else f"<span style='color:{NEGATIVE.name()}'><b>{total - done}</b> of {total} required items still "
                      f"needed</span>")
        self.where.setText(f"This deal: <b>{f}</b>" + (" · " + state if ready else " · not set up yet — "
                                                        "click <b>Set Up Folders</b>"))
        too_long = df.long_paths(f, ctx, asset_management=True)
        if too_long:
            self.where.setText(self.where.text() + (
                f"<br><span style='color:{NEGATIVE.name()}'>⚠ {len(too_long)} folder(s) here have paths over 260 "
                "characters — the app handles them, but Explorer and Excel may not open files there. A shorter Deals "
                "folder (higher up in OneDrive, or a shorter name) avoids it.</span>"))
        self.btns["close"].setText("Update Asset Management" if am else "Set Up Asset Management")
        books = df.company_path(self.pf, self.deal)
        try:
            is_linked = bool(books) and df.linked(self.pf, self.deal)
        except Exception:  # noqa: BLE001
            is_linked = False
        self.btns["link"].setEnabled(ready and bool(books) and not is_linked)
        self.btns["link"].setText("Accounting Linked ✓" if is_linked else "Link the Company's Accounting")
        self.where.setText(self.where.text() + (
            "<br>Accounting: the company's books save into <b>Asset Management → Accounting</b>" if is_linked else
            "<br>Accounting: the company's books aren't linked to this folder yet" if books else
            "<br>Accounting: linked when the deal closes (its company's books save into Asset Management → "
            "Accounting)"))
        parents: list[QTreeWidgetItem] = []
        self.items = []
        folder_nodes: dict[str, QTreeWidgetItem] = {}      # the standard's folders, by path
        for s in rows:
            it = s.item
            node = QTreeWidgetItem([("📄 " if it.node.kind != "folder" else "") + it.label, "", "", ""])
            node.setData(0, Qt.ItemDataRole.UserRole, len(self.items))
            self.items.append(it)
            if s.missing:
                node.setText(1, "⚠ needs a file" if it.node.kind == "folder" else "⚠ missing")
                node.setForeground(1, NEGATIVE)
            elif it.node.required:
                node.setText(1, "✓")
                node.setForeground(1, POSITIVE)
            if it.is_folder:
                node.setText(2, str(s.files) if s.files else "")
                if not s.exists:
                    node.setForeground(0, MUTED)
            note = " · ".join(x for x in (it.node.note, f"Name: {it.naming}" if it.naming else "",
                                          f"{it.node.ext} file" if it.node.ext else "") if x)
            node.setText(3, note)
            node.setToolTip(0, it.rel)
            del parents[it.depth:]
            (parents[-1].addChild(node) if parents else self.tree.addTopLevelItem(node))
            if it.is_folder:
                parents.append(node)
                folder_nodes[it.rel] = node
        if ready and self.show_files.isChecked():          # what's actually in the folders: files and subfolders
            standard = {}
            for it in self.items:
                if it.is_folder:
                    parent = it.rel.rsplit("/", 1)[0] if "/" in it.rel else ""
                    standard.setdefault(parent, set()).add(it.rel.rsplit("/", 1)[-1].lower())
            self._add_disk(None, f, "", 0, standard.get("", set()))
            for rel, node in folder_nodes.items():
                self._add_disk(node, f / rel, rel, rel.count("/") + 1, standard.get(rel, set()))
        if had:
            stack = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
            while stack:
                n = stack.pop()
                key = self._key(n)
                n.setExpanded(key in keep_open)
                if key == keep_cur:
                    self.tree.setCurrentItem(n)
                stack += [n.child(i) for i in range(n.childCount())]
        else:
            self.tree.collapseAll()          # just the top: Underwriting, Asset Management

    def _add_disk(self, parent: QTreeWidgetItem | None, path: Path, rel: str, depth: int, skip: set[str]) -> None:
        """The folders and files in ``path`` that aren't the standard's own folders (those are already shown)."""
        from .. import longpath as lpth

        entries = sorted(lpth.listdir(path), key=lambda p: (not lpth.is_dir(p), p.name.lower()))
        for e in entries:
            is_dir = lpth.is_dir(e)
            if e.name.startswith((".", "~$")) or (is_dir and e.name.lower() in skip):
                continue
            r = f"{rel}/{e.name}" if rel else e.name
            it = df.Item(r, df.Node(e.name, "disk_dir" if is_dir else "disk_file"), depth, e.name)
            node = QTreeWidgetItem([("📁 " if is_dir else "📄 ") + e.name, "", "", ""])
            node.setData(0, Qt.ItemDataRole.UserRole, len(self.items))
            self.items.append(it)
            node.setToolTip(0, str(e))
            if is_dir:
                node.setText(3, "your folder")
                node.setForeground(3, MUTED)
            else:
                try:
                    st = lpth.stat(e)
                    from datetime import datetime

                    size = st.st_size
                    node.setText(2, f"{size / 1024:,.0f} KB" if size >= 1024 else f"{size} B")
                    node.setText(3, f"changed {datetime.fromtimestamp(st.st_mtime):%b %d, %Y %H:%M}")
                    node.setForeground(3, MUTED)
                except OSError:
                    pass
            (parent.addChild(node) if parent is not None else self.tree.addTopLevelItem(node))
            if is_dir:
                self._add_disk(node, e, r, depth + 1, set())

    def _selected(self) -> df.Item | None:
        cur = self.tree.currentItem()
        if cur is None:
            return None
        return self.items[cur.data(0, Qt.ItemDataRole.UserRole)]

    # ---- actions ----
    def choose(self, **kw) -> Path | None:
        """No folder yet: ask where it goes (as for a new deal)."""
        if self.deal.id is None:
            info(self, "Deal folder", "Save the deal first.")
            return None
        f = ask_where(self, self.pf, self.deal, **kw)
        self.fill()
        return f

    def pick_own(self, path: str | None = None) -> None:
        if self.deal.id is None:
            info(self, "Deal folder", "Save the deal first.")
            return
        cur = self.folder()
        path = path or QFileDialog.getExistingDirectory(self, f"The folder for {self.deal.name}",
                                                        str(cur.parent) if cur else "")
        if path:
            df.set_folder_of(self.pf, self.deal, path)
            self.setup()

    def setup(self, quiet: bool = False) -> int:
        """Make the standard folders (anything already in the folder that isn't the standard's goes into Previous
        Files first)."""
        f = self.folder()
        if f is None:
            return 0
        try:
            moved = df.move_to_previous(f) if df.lpth.exists(f) else 0
            n = df.create(f, df.context(self.pf, self.deal), asset_management=self._am())
        except OSError as e:
            error(self, "Couldn't make the folders", str(e))
            return 0
        self.fill()
        if moved and not quiet:
            info(self, "Previous files", f"{moved} file(s) / folder(s) that were already in the deal's folder are in "
                                         f"“{df.PREVIOUS}”, as they were.")
        return n

    def asset_management(self) -> None:
        f = self.folder()
        if f is None:
            return
        try:
            made = df.create(f, df.context(self.pf, self.deal), asset_management=True)
            copied = df.transfer_on_closing(f)
        except OSError as e:
            error(self, "Couldn't set it up", str(e))
            return
        self.fill()
        info(self, "Asset Management", f"{made} folder(s) made; {copied} underwriting document(s) copied into "
                                       "Legal and Admin.")

    def link(self) -> None:
        try:
            root = df.link_books(self.pf, self.deal)
        except (OSError, ValueError) as e:
            error(self, "Couldn't link the accounting", str(e))
            return
        self.fill()
        info(self, "Accounting linked", f"The company's books now save into {root}: bank statements dropped in "
                                        "Banking/Monthly Statements are imported, financial statements go in "
                                        "Financial Statements, bills are filed by vendor in Invoices, and the latest "
                                        "copy of the books is kept in QuickBooks File.")

    def open_selected(self) -> None:
        f = self.folder()
        if f is None:
            return
        it = self._selected()
        from .. import longpath as lpth

        p = f / it.rel if it is not None else f
        while not lpth.exists(p) and p != f:
            p = p.parent
        if lpth.too_long(p):
            info(self, "Path too long", f"Windows programs may not open this — its path is {len(str(p))} characters "
                                        f"(most stop at 260):\n\n{p}\n\nMove the Deals folder somewhere shorter "
                                        "(e.g. closer to the top of OneDrive) to open it from here.")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))

    def _target(self) -> df.Item | None:
        it = self._selected()
        if it is None:
            info(self, "Add files", "Select the folder to file them in.")
            return None
        if it.node.kind == "disk_file":                  # a file: into the folder it's in
            parent = it.rel.rsplit("/", 1)[0] if "/" in it.rel else ""
            return df.Item(parent, df.Node(Path(parent).name), it.depth, Path(parent).name)
        if it.node.kind == "disk_dir":
            return df.Item(it.rel, df.Node(it.label), it.depth, it.label)
        if it.node.kind == "file":                       # a named file: into its folder
            return df.Item(str(Path(it.rel).parent).replace("\\", "/"), it.node, it.depth, it.label, it.naming)
        return it

    def add_files(self, files=None) -> int:
        it = self._target()
        if it is None:
            return 0
        files = files or QFileDialog.getOpenFileNames(self, f"File into {it.label}")[0]
        return self._file(it, files)

    def _dropped(self, node: QTreeWidgetItem, files) -> None:
        self.tree.setCurrentItem(node)
        self.add_files(files)

    def _file(self, it: df.Item, files, ask=None) -> int:
        f = self.folder()
        ask = ask or QInputDialog.getText
        n = 0
        for src in files:
            name = Path(src).name
            if it.naming or it.node.kind == "file":
                guess = it.label if it.node.kind == "file" else df.suggested_name(it, Path(src))
                if it.node.kind == "file":
                    guess = Path(it.label).stem + Path(src).suffix
                name, ok = ask(self, "Name the file", f"Naming: {it.naming or it.label}", QLineEdit.EchoMode.Normal,
                               guess)
                if not ok:
                    continue
            try:
                df.file_into(f, it, src, name)
                n += 1
            except OSError as e:
                error(self, "Couldn't file it", f"{Path(src).name}: {e}")
        self.fill()
        return n

    def lender_package(self, *, run=None) -> Path | None:
        """Pick the files for a lender submission package and copy them into a new folder (see
        lender_package.py). ``run`` (for tests) takes the dialog instead of showing it."""
        from .lender_package_dialog import LenderPackageDialog

        f = self.folder()
        if f is None:
            info(self, "No folder yet", "Choose a folder for this deal first.")
            return None
        from .. import personal as pers

        dlg = LenderPackageDialog(f, self, personal=pers.folder(self.pf),
                                  statements=pers.statements_folder(self.pf))
        if not (run(dlg) if run is not None else dlg.exec()) or dlg.package is None:
            return None
        self.fill()
        from .. import longpath as lpth

        n = sum(1 for _ in lpth.walk_files(dlg.package)) - 1
        if run is None and confirm(self, "Package made", f"{n} file(s) copied into\n{dlg.package}\n\nwith "
                                                         "a checklist of what's included and what's missing. "
                                                         "Open the folder?"):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(dlg.package)))
        return dlg.package

    def archive(self, *, ask: bool = True) -> int:
        f = self.folder()
        if f is None:
            return 0
        plan = df.archive_plan(f, df.context(self.pf, self.deal), asset_management=self._am())
        if not plan:
            if ask:
                info(self, "Archive old versions", "Every folder has just its live file — nothing to archive.")
            return 0
        lines = "\n".join(f"• {src.relative_to(f)}" for src, _d in plan[:25])
        more = f"\n… and {len(plan) - 25} more" if len(plan) > 25 else ""
        if ask and not confirm(self, "Archive old versions", f"Move {len(plan)} older version(s) into their folder's "
                                                             f"Archive?\n\n{lines}{more}"):
            return 0
        n = df.archive(plan)
        self.fill()
        return n
