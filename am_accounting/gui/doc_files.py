"""Matching payments to invoices/bills, and the invoice & bill folder (settings, files, drop folder)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from .. import doc_folder as df
from .. import receivables as rx
from ..books import Books
from ..inbox import detect_cloud_folders
from ..money import fmt_money
from .common import ALIGN_RIGHT, MUTED, POSITIVE, confirm, error


def open_path(path: str | Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


# ---------------------------------------------------------------------------
# Keeping files in step with the books
# ---------------------------------------------------------------------------


def generate_invoice_pdf(books: Books, doc_id: int) -> Path | None:
    """(Re)create the app-made PDF for an invoice and file it."""
    from .reports_tab import write_pdf

    cfg = df.get_config(books)
    doc = rx.get_document(books, doc_id)
    if not cfg.enabled or doc.kind != "invoice":
        return None
    tmp = Path(tempfile.mkdtemp()) / f"{doc.number or 'invoice'}.pdf"
    write_pdf(df.invoice_html(books, doc), str(tmp))
    return df.replace_generated(books, doc_id, tmp)


def sync(books: Books, doc_ids: list[int] | None = None) -> None:
    """After invoices/bills change: refresh app-made invoice PDFs and re-file everything."""
    cfg = df.get_config(books)
    if not cfg.enabled:
        return
    ids = doc_ids if doc_ids is not None else [f.document_id for f in df.files_for(books) if f.generated]
    for doc_id in set(ids):
        try:
            doc = rx.get_document(books, doc_id)
        except KeyError:
            continue
        has_generated = any(f.generated for f in df.files_for(books, doc_id))
        if doc.kind == "invoice" and (has_generated or cfg.make_pdf):
            try:
                generate_invoice_pdf(books, doc_id)
            except Exception:  # noqa: BLE001 - PDF rendering problems mustn't block bookkeeping
                pass
    df.refile(books)


def auto_match_and_file(books: Books) -> int:
    """Run automatic matching (if switched on) and keep the folder in step. Returns matches made."""
    cfg = df.get_config(books)
    if not cfg.auto_match:
        return 0
    applied = rx.auto_match(books)
    if applied:
        sync(books, [p.doc.id for p in applied])
    return len(applied)


# ---------------------------------------------------------------------------
# Match payments
# ---------------------------------------------------------------------------


class MatchDialog(QDialog):
    """Review proposed matches between bank transactions and open invoices/bills."""

    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Match Payments to Invoices & Bills")
        self.resize(1060, 560)
        layout = QVBoxLayout(self)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        layout.addWidget(self.intro)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Match", "Date", "Bank transaction", "Amount", "", "Invoice / bill",
                                              "Confidence", "Why"])
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        sel_all = QPushButton("Select All")
        sel_all.clicked.connect(lambda: self._check_all(True))
        sel_none = QPushButton("Select None")
        sel_none.clicked.connect(lambda: self._check_all(False))
        row.addWidget(sel_all)
        row.addWidget(sel_none)
        row.addStretch(1)
        self.apply_btn = QPushButton("Match Selected")
        self.apply_btn.setObjectName("primary")
        self.apply_btn.clicked.connect(self.apply)
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        row.addWidget(self.apply_btn)
        row.addWidget(close)
        layout.addLayout(row)
        self.matched = 0
        self.refresh()

    def refresh(self) -> None:
        self.proposals = rx.find_matches(self.books)
        high = sum(p.confidence == "high" for p in self.proposals)
        self.intro.setText(
            f"<b>{len(self.proposals)}</b> bank transaction(s) look like payments of open invoices or bills "
            f"({high} confident). Confident matches are ticked; tick the others you agree with, then "
            "<b>Match Selected</b>. Matching marks the invoice/bill paid and moves the money to Accounts "
            "Receivable/Payable so it isn't counted twice.")
        self.table.setRowCount(len(self.proposals))
        for r, p in enumerate(self.proposals):
            box = QTableWidgetItem()
            box.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            box.setCheckState(Qt.CheckState.Checked if p.confidence == "high" else Qt.CheckState.Unchecked)
            self.table.setItem(r, 0, box)
            label = rx.KIND_LABELS[p.doc.kind][0]
            vals = [p.tx.date, f"{p.tx.description}  ({self.books.account(p.tx.bank_account_id).name})",
                    fmt_money(p.tx.amount_cents, parens=False), "→",
                    f"{label} {p.doc.number} · {p.doc.party} · due {p.doc.due_date}".replace("  ", " "),
                    "Confident" if p.confidence == "high" else "Possible", p.why]
            for c, v in enumerate(vals, start=1):
                it = QTableWidgetItem(v)
                it.setToolTip(v)
                if c == 3:
                    it.setTextAlignment(ALIGN_RIGHT)
                if c == 6:
                    it.setForeground(POSITIVE if p.confidence == "high" else MUTED)
                self.table.setItem(r, c, it)
        for c, w in [(0, 55), (1, 90), (3, 105), (4, 24), (6, 90), (7, 230)]:
            self.table.setColumnWidth(c, w)
        self.apply_btn.setEnabled(bool(self.proposals))

    def _check_all(self, on: bool) -> None:
        for r in range(self.table.rowCount()):
            self.table.item(r, 0).setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def apply(self) -> None:
        chosen = [p for r, p in enumerate(self.proposals)
                  if self.table.item(r, 0).checkState() == Qt.CheckState.Checked]
        if not chosen:
            return
        n = rx.apply_matches(self.books, chosen)
        sync(self.books, [p.doc.id for p in chosen])
        self.matched += n
        self.refresh()
        if not self.proposals:
            self.accept()


# ---------------------------------------------------------------------------
# Folder settings
# ---------------------------------------------------------------------------


class FolderSettingsDialog(QDialog):
    def __init__(self, books: Books, parent=None):
        super().__init__(parent)
        self.books = books
        self.setWindowTitle("Invoice & Bill Folder")
        self.resize(640, 0)
        cfg = df.get_config(books)
        outer = QVBoxLayout(self)
        intro = QLabel("Keep a file for every invoice and bill in one folder — on this computer or in OneDrive. "
                       "Files are renamed and sorted automatically, and move when an invoice is paid or edited.")
        intro.setWordWrap(True)
        outer.addWidget(intro)
        form = QFormLayout()
        outer.addLayout(form)
        row = QHBoxLayout()
        self.folder = QLineEdit(cfg.folder)
        self.folder.setPlaceholderText("Choose a folder")
        row.addWidget(self.folder, 1)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        cloud = QPushButton("Use OneDrive")
        menu = QMenu(self)
        found = detect_cloud_folders()
        if not found:
            menu.addAction("No OneDrive or cloud folder found on this computer").setEnabled(False)
        from ..companies import safe_filename

        for label, path in found:
            target = path / "AM Accounting" / safe_filename(books.company_name) / "Invoices & Bills"
            menu.addAction(f"{label} — {target}").triggered.connect(
                lambda _=False, t=target: self.folder.setText(str(t)))
        cloud.setMenu(menu)
        row.addWidget(cloud)
        form.addRow("Folder:", row)
        self.layout_box = QComboBox()
        for k, v in df.LAYOUTS.items():
            self.layout_box.addItem(v, k)
        self.layout_box.setCurrentIndex(self.layout_box.findData(cfg.layout))
        form.addRow("Sort files:", self.layout_box)
        self.name_box = QComboBox()
        for k, v in df.NAME_STYLES.items():
            self.name_box.addItem(v if k == "original" else f"Rename to: {v}", k)
        self.name_box.setCurrentIndex(self.name_box.findData(cfg.name_style))
        form.addRow("File names:", self.name_box)
        self.example = QLabel()
        self.example.setObjectName("hint")
        self.example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("Example:", self.example)
        self.make_pdf = QCheckBox("Create a PDF for each invoice made in AM Accounting")
        self.make_pdf.setChecked(cfg.make_pdf)
        form.addRow("", self.make_pdf)
        self.auto_match = QCheckBox("Match bank payments to invoices and bills automatically after each import")
        self.auto_match.setChecked(cfg.auto_match)
        form.addRow("", self.auto_match)
        hint = QLabel(f"New invoice or bill files can be dropped into “{df.DROP_DIR}” inside the folder and "
                      "attached from the Invoices & Bills tab.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.layout_box.currentIndexChanged.connect(self._example)
        self.name_box.currentIndexChanged.connect(self._example)
        self._example()
        self.adjustSize()

    def _draft(self) -> df.FolderConfig:
        return df.FolderConfig(self.folder.text().strip(), self.layout_box.currentData(), self.name_box.currentData(),
                               self.make_pdf.isChecked(), self.auto_match.isChecked())

    def _example(self) -> None:
        self.example.setText(df.describe_layout(self._draft()))
        self.example.setToolTip("Paid invoices and bills move to Paid/<year>"
                                if self.layout_box.currentData() == "status" else "")

    def _browse(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Invoice & bill folder", self.folder.text() or str(Path.home()))
        if d:
            self.folder.setText(d)

    def save(self) -> None:
        cfg = self._draft()
        first_time = not df.get_config(self.books).enabled and cfg.enabled
        try:
            moves = df.set_config(self.books, cfg)
            if first_time and cfg.make_pdf:
                for d in rx.documents(self.books, "invoice", status="all", as_of=_far()):
                    if not df.files_for(self.books, d.id):
                        generate_invoice_pdf(self.books, d.id)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save", str(e))
            return
        self.moves = moves
        self.accept()


def _far():
    from datetime import date

    return date(9999, 12, 31)


# ---------------------------------------------------------------------------
# Files of one invoice/bill
# ---------------------------------------------------------------------------


class FilesDialog(QDialog):
    def __init__(self, books: Books, doc_id: int, parent=None):
        super().__init__(parent)
        self.books, self.doc_id = books, doc_id
        doc = rx.get_document(books, doc_id)
        label = rx.KIND_LABELS[doc.kind][0]
        self.setWindowTitle(f"Files – {label} {doc.number} {doc.party}".replace("  ", " "))
        self.resize(720, 360)
        layout = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.open_selected())
        layout.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in [("Open", self.open_selected), ("Show in Folder", self.show_folder),
                           ("Attach File…", self.attach), ("Detach", self.detach)]:
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        if doc.kind == "invoice":
            b = QPushButton("Create / Update Invoice PDF")
            b.clicked.connect(self.regenerate)
            row.addWidget(b)
        row.addStretch(1)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        row.addWidget(done)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        cfg = df.get_config(self.books)
        self.list.clear()
        for f in df.files_for(self.books, self.doc_id):
            p = f.path(cfg)
            text = f.rel_path + ("   (made by AM Accounting)" if f.generated else
                                 f"   (was “{f.original_name}”)" if f.original_name and f.original_name != p.name
                                 else "")
            if not p.exists():
                text += "   — missing on disk"
            it = QListWidgetItem(text)
            it.setData(Qt.ItemDataRole.UserRole, f.id)
            self.list.addItem(it)
        if not self.list.count():
            self.list.addItem("No files yet. Use “Attach File…” to add the invoice or bill document.")

    def _selected(self) -> df.DocFile | None:
        it = self.list.currentItem()
        fid = it.data(Qt.ItemDataRole.UserRole) if it else None
        return next((f for f in df.files_for(self.books, self.doc_id) if f.id == fid), None)

    def open_selected(self) -> None:
        f = self._selected()
        if f:
            open_path(f.path(df.get_config(self.books)))

    def show_folder(self) -> None:
        f = self._selected()
        cfg = df.get_config(self.books)
        open_path(f.path(cfg).parent if f else cfg.root)

    def attach(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach files", str(Path.home()),
                                                "Documents (*.pdf *.png *.jpg *.jpeg *.heic *.docx *.xlsx);;All files (*)")
        if not paths:
            return
        move = confirm(self, "Move or copy?", "Move the file(s) into the invoice & bill folder?\n\n"
                                              "Yes = move (recommended), No = keep the original and file a copy.")
        try:
            for p in paths:
                df.attach_file(self.books, self.doc_id, p, move=move)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not attach", str(e))
        self.refresh()

    def detach(self) -> None:
        f = self._selected()
        if f and confirm(self, "Detach file", f"Detach this file? It goes back to “{df.DROP_DIR}”."
                         if not f.generated else "Delete this app-made PDF?"):
            df.detach_file(self.books, f.id, to_drop_folder=not f.generated)
            self.refresh()

    def regenerate(self) -> None:
        try:
            generate_invoice_pdf(self.books, self.doc_id)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not create PDF", str(e))
        self.refresh()


# ---------------------------------------------------------------------------
# Drop folder
# ---------------------------------------------------------------------------


class UnfiledDialog(QDialog):
    """Attach files waiting in the drop folder to invoices/bills, or start a new bill from one."""

    def __init__(self, books: Books, parent=None, *, new_document_cb=None):
        super().__init__(parent)
        self.books = books
        self.new_document_cb = new_document_cb
        self.setWindowTitle("Files to File")
        self.resize(820, 460)
        layout = QVBoxLayout(self)
        cfg = df.get_config(books)
        intro = QLabel(f"Files waiting in <b>{cfg.root / df.DROP_DIR}</b>. Pick a file, then the invoice or bill "
                       "it belongs to.")
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda i: open_path(i.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.list, 1)
        form = QFormLayout()
        self.target = QComboBox()
        self.target.setMaxVisibleItems(25)
        form.addRow("Belongs to:", self.target)
        layout.addLayout(form)
        row = QHBoxLayout()
        open_btn = QPushButton("Open File")
        open_btn.clicked.connect(lambda: self.list.currentItem() and open_path(
            self.list.currentItem().data(Qt.ItemDataRole.UserRole)))
        row.addWidget(open_btn)
        for kind in ("bill", "invoice"):
            b = QPushButton(f"New {rx.KIND_LABELS[kind][0]} from File…")
            b.clicked.connect(lambda _=False, k=kind: self.new_from_file(k))
            row.addWidget(b)
        row.addStretch(1)
        attach = QPushButton("Attach")
        attach.setObjectName("primary")
        attach.clicked.connect(self.attach)
        done = QPushButton("Done")
        done.clicked.connect(self.accept)
        row.addWidget(attach)
        row.addWidget(done)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for p in df.unfiled(self.books):
            it = QListWidgetItem(p.name)
            it.setData(Qt.ItemDataRole.UserRole, str(p))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.target.clear()
        for kind in ("bill", "invoice"):
            for d in rx.documents(self.books, kind, status="all", as_of=_far()):
                label = rx.KIND_LABELS[kind][0]
                state = "open" if d.balance_cents > 0 else "paid"
                self.target.addItem(f"{label} {d.number} · {d.party} · {fmt_money(d.amount_cents)} · "
                                    f"{d.issue_date} · {state}".replace("  ", " "), d.id)

    def _file(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def attach(self) -> None:
        f, doc_id = self._file(), self.target.currentData()
        if not f or doc_id is None:
            return
        try:
            df.attach_file(self.books, doc_id, f)
        except Exception as e:  # noqa: BLE001
            error(self, "Could not attach", str(e))
        self.refresh()

    def new_from_file(self, kind: str) -> None:
        f = self._file()
        if not f or not self.new_document_cb:
            return
        open_path(f)  # show it while typing in the details
        doc_id = self.new_document_cb(kind)
        if doc_id:
            df.attach_file(self.books, doc_id, f)
        self.refresh()
