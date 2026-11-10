"""Statement inbox settings and the background service that runs it."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPushButton, QVBoxLayout)

from ..books import Books
from ..companies import CompanyRegistry
from ..inbox import (FAILED_DIR, ORGANIZE_OPTIONS, InboxConfig, describe_layout, InboxEvent, InboxWatcher, account_folder_name,
                     detect_cloud_folders, get_config, set_config, suggested_inbox)
from .common import NEGATIVE, POSITIVE, error


class InboxService(QObject):
    """Watches the inbox folders of all companies; events arrive on the GUI thread."""

    event = Signal(object)  # InboxEvent

    def __init__(self, registry: CompanyRegistry, parent=None, interval: float = 10.0):
        super().__init__(parent)
        self.registry = registry
        self.history: list[tuple[str, InboxEvent]] = []
        self._paths: list[str] = []
        self.event.connect(self._record)
        self.watcher = InboxWatcher(lambda: list(self._paths), self.event.emit, interval)
        self.refresh_companies()
        self.watcher.start()

    def refresh_companies(self) -> None:
        """Re-read the company list (call from the GUI thread after companies change)."""
        self._paths = [e.path for e in self.registry.companies() if e.exists]
        self.watcher.scan_now()

    def stop(self) -> None:
        self.watcher.stop()

    def _record(self, e: InboxEvent) -> None:
        self.history.insert(0, (datetime.now().strftime("%H:%M:%S"), e))
        del self.history[200:]


class InboxDialog(QDialog):
    def __init__(self, books: Books, service: InboxService, parent=None):
        super().__init__(parent)
        self.books = books
        self.service = service
        self.setWindowTitle(f"Statement Inbox — {books.company_name}")
        self.resize(760, 600)
        cfg = get_config(books)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Drop bank statements (CSV, Excel, OFX/QFX or PDF) into a folder and they're imported into "
            f"<b>{books.company_name}</b> automatically while Aedalus Nexus is running. Use a folder inside "
            "<b>OneDrive</b> to add statements from your phone, another computer or OneDrive on the web.")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self.enabled = QCheckBox("Import statements from this folder automatically")
        self.enabled.setChecked(cfg.enabled)
        form.addRow(self.enabled)

        row = QHBoxLayout()
        self.folder = QLineEdit(cfg.folder)
        self.folder.setPlaceholderText("Choose a folder on this computer or in OneDrive")
        row.addWidget(self.folder, 1)
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        self.cloud_btn = QPushButton("Use OneDrive")
        self.cloud_menu = QMenu(self)
        self.cloud_btn.setMenu(self.cloud_menu)
        self._fill_cloud_menu()
        row.addWidget(self.cloud_btn)
        open_btn = QPushButton("Open")
        open_btn.clicked.connect(self._open_folder)
        row.addWidget(open_btn)
        form.addRow("Inbox folder:", row)

        self.default = QComboBox()
        self.default.addItem("None — use the bank account folders", "")
        for a in books.bank_accounts():
            self.default.addItem(f"{a.code} · {a.name}", a.code)
        i = self.default.findData(cfg.default_account_code)
        self.default.setCurrentIndex(max(0, i))
        form.addRow("Files dropped in the\ninbox itself go to:", self.default)

        self.organize = QComboBox()
        for key, label in ORGANIZE_OPTIONS.items():
            self.organize.addItem(label, key)
        self.organize.setCurrentIndex(max(0, self.organize.findData(cfg.organize)))
        self.rename = QCheckBox("Rename files to the statement month and bank account")
        self.rename.setChecked(cfg.rename)
        self.save_excel = QCheckBox("Also save an Excel copy next to each PDF statement")
        self.save_excel.setChecked(cfg.save_excel)
        self.example = QLabel()
        self.example.setObjectName("hint")
        self.example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow("File imported statements:", self.organize)
        form.addRow("", self.rename)
        form.addRow("", self.save_excel)
        from ..pdf_to_excel import COMBINED_NAME

        self.combined_excel = QCheckBox(f"Add PDF statements to one workbook: Statements/{COMBINED_NAME}")
        self.combined_excel.setChecked(cfg.combined_excel)
        form.addRow("", self.combined_excel)
        form.addRow("Example:", self.example)
        layout.addLayout(form)

        self.layout_hint = QLabel()
        self.layout_hint.setObjectName("hint")
        self.layout_hint.setWordWrap(True)
        self.layout_hint.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.layout_hint)
        self.folder.textChanged.connect(self._update_hint)
        self.organize.currentIndexChanged.connect(self._update_hint)
        self.rename.toggled.connect(self._update_hint)
        self._update_hint()

        head = QHBoxLayout()
        head.addWidget(QLabel("Recent activity (all companies):"), 1)
        scan = QPushButton("Check Now")
        scan.clicked.connect(self.service.watcher.scan_now)
        head.addWidget(scan)
        layout.addLayout(head)
        self.log = QListWidget()
        layout.addWidget(self.log, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        service.event.connect(self._refresh_log)
        self._refresh_log()

    # -- folder helpers -----------------------------------------------------

    def _fill_cloud_menu(self) -> None:
        self.cloud_menu.clear()
        folders = detect_cloud_folders()
        onedrive = [f for f in folders if f[0].startswith("OneDrive")]
        if not onedrive:
            a = self.cloud_menu.addAction("OneDrive not found on this computer")
            a.setEnabled(False)
            hint = self.cloud_menu.addAction("Install/sign in to OneDrive, then choose its folder with “Choose…”")
            hint.setEnabled(False)
        for label, path in folders:
            target = suggested_inbox(path, self.books.company_name)
            a = self.cloud_menu.addAction(f"{label}  —  {target}")
            a.triggered.connect(lambda _=False, t=target: self.folder.setText(str(t)))
        self.cloud_btn.setText("Use OneDrive" if onedrive else "Cloud Folders")

    def _browse(self) -> None:
        start = self.folder.text() or str(Path.home())
        d = QFileDialog.getExistingDirectory(self, "Choose inbox folder", start)
        if d:
            self.folder.setText(d)

    def _open_folder(self) -> None:
        f = self.folder.text().strip()
        if f:
            Path(f).expanduser().mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(f).expanduser())))

    def _update_hint(self) -> None:
        subs = "".join(f"<br>&nbsp;&nbsp;📁 <b>{account_folder_name(a)}</b> → imports into {a.name}"
                       for a in self.books.bank_accounts())
        self.layout_hint.setText(
            "Inside the inbox, each bank account gets its own subfolder:" + subs +
            f"<br>After import, each statement is filed as shown in the example; files that can't be imported "
            f"move to <b>{FAILED_DIR}</b> with a note explaining why. Duplicate transactions are skipped.")
        cfg = self._draft()
        self.rename.setEnabled(cfg.organize != "as_received")
        accounts = self.books.bank_accounts()
        example = describe_layout(cfg, accounts[0].name if accounts else "Business Checking")
        self.example.setText(example.replace("<inbox>", "Inbox") + "   (a January 2026 statement)")

    def _draft(self) -> InboxConfig:
        return InboxConfig(self.enabled.isChecked(), self.folder.text().strip(), self.default.currentData() or "",
                           self.organize.currentData(), self.rename.isChecked(), self.save_excel.isChecked(),
                           self.combined_excel.isChecked())

    # -- log / save ---------------------------------------------------------

    def _refresh_log(self, *_):
        self.log.clear()
        for ts, e in self.service.history:
            item = QListWidgetItem(f"{ts}  {e.describe()}")
            item.setForeground(POSITIVE if e.ok else NEGATIVE)
            self.log.addItem(item)

    def save(self) -> None:
        folder = self.folder.text().strip()
        if self.enabled.isChecked() and not folder:
            error(self, "Folder required", "Choose an inbox folder.")
            return
        if folder:
            for e in self.service.registry.companies():
                if e.exists and Path(e.path).resolve() != Path(self.books.path).resolve():
                    try:
                        other = Books.open(e.path, create=False)
                        ocfg = get_config(other)
                        other.close()
                    except Exception:  # noqa: BLE001
                        continue
                    if ocfg.enabled and ocfg.folder and Path(ocfg.folder).expanduser().resolve() == \
                            Path(folder).expanduser().resolve():
                        error(self, "Folder already used", f"“{e.name}” already uses this inbox folder. "
                                                           "Each company needs its own folder.")
                        return
        try:
            set_config(self.books, self._draft())
        except Exception as e:  # noqa: BLE001
            error(self, "Could not save", str(e))
            return
        self.service.refresh_companies()
        self.accept()

    def done(self, r):
        self.service.event.disconnect(self._refresh_log)
        super().done(r)
