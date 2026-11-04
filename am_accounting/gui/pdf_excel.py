"""Desktop integration for the background PDF → Excel converter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Qt, QThreadPool, QUrl, Signal, QRunnable
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from ..pdf_to_excel import convert_pdf_to_excel
from ..watcher import FAILED_DIR, PROCESSED_DIR, PdfFolderWatcher, WatchEvent
from .common import NEGATIVE, POSITIVE, error

KEY = "pdf_watch/"


def default_folders() -> tuple[str, str]:
    docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation) or str(Path.home())
    base = Path(docs) / "AM Accounting"
    return str(base / "PDF Inbox"), str(base / "Excel Statements")


class PdfWatchService(QObject):
    """Owns the background watcher thread and re-emits its events on the GUI thread."""

    event = Signal(object)  # WatchEvent
    state_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("AM Accounting", "AM Accounting")
        self.watcher: PdfFolderWatcher | None = None
        self.history: list[tuple[str, WatchEvent]] = []
        self.event.connect(self._record)

    # settings
    def config(self) -> dict:
        inbox, out = default_folders()
        s = self.settings
        return {
            "enabled": s.value(KEY + "enabled", False, type=bool),
            "inbox": s.value(KEY + "inbox", inbox),
            "out": s.value(KEY + "out", out),
            "interval": s.value(KEY + "interval", 5, type=int),
            "dayfirst": s.value(KEY + "dayfirst", False, type=bool),
        }

    def save_config(self, **cfg) -> None:
        for k, v in cfg.items():
            self.settings.setValue(KEY + k, v)
        self.apply()

    # lifecycle
    def apply(self) -> None:
        cfg = self.config()
        self.stop()
        if cfg["enabled"]:
            self.watcher = PdfFolderWatcher(cfg["inbox"], cfg["out"], dayfirst=cfg["dayfirst"],
                                            interval=cfg["interval"], on_event=self.event.emit)
            self.watcher.start()
        self.state_changed.emit(self.running)

    def stop(self) -> None:
        if self.watcher:
            self.watcher.stop()
            self.watcher = None

    @property
    def running(self) -> bool:
        return bool(self.watcher and self.watcher.running)

    def _record(self, e: WatchEvent) -> None:
        self.history.insert(0, (datetime.now().strftime("%H:%M:%S"), e))
        del self.history[200:]


def describe(e: WatchEvent) -> str:
    if e.ok:
        extra = f" · {len(e.warnings)} warning(s)" if e.warnings else ""
        return f"{e.pdf_name} → {e.xlsx_path.name} ({e.count} transactions){extra}"
    return f"{e.pdf_name} — failed: {e.error}"


def _folder_row(edit: QLineEdit, parent: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(edit, 1)
    browse = QPushButton("Choose…")
    browse.clicked.connect(lambda: (d := QFileDialog.getExistingDirectory(parent, "Choose folder", edit.text()))
                           and edit.setText(d))
    open_btn = QPushButton("Open")
    open_btn.clicked.connect(lambda: (Path(edit.text()).mkdir(parents=True, exist_ok=True),
                                      QDesktopServices.openUrl(QUrl.fromLocalFile(edit.text()))))
    row.addWidget(browse)
    row.addWidget(open_btn)
    return row


class PdfWatchDialog(QDialog):
    def __init__(self, service: PdfWatchService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setWindowTitle("Automatic PDF → Excel")
        self.resize(720, 520)
        cfg = service.config()
        layout = QVBoxLayout(self)
        intro = QLabel("While AM Accounting is open, any PDF bank statement saved to the inbox folder is converted "
                       "to an Excel workbook in the output folder automatically. Converted PDFs move to "
                       f"“{PROCESSED_DIR}” inside the inbox; ones that can't be read move to “{FAILED_DIR}”.")
        intro.setWordWrap(True)
        intro.setObjectName("hint")
        layout.addWidget(intro)

        form = QFormLayout()
        self.enabled = QCheckBox("Convert PDFs automatically in the background")
        self.enabled.setChecked(cfg["enabled"])
        self.inbox = QLineEdit(cfg["inbox"])
        self.out = QLineEdit(cfg["out"])
        self.interval = QSpinBox()
        self.interval.setRange(2, 3600)
        self.interval.setSuffix(" seconds")
        self.interval.setValue(cfg["interval"])
        self.dayfirst = QCheckBox("Dates are day/month/year")
        self.dayfirst.setChecked(cfg["dayfirst"])
        form.addRow(self.enabled)
        form.addRow("Inbox folder:", _folder_row(self.inbox, self))
        form.addRow("Excel output folder:", _folder_row(self.out, self))
        form.addRow("Check every:", self.interval)
        form.addRow("", self.dayfirst)
        layout.addLayout(form)

        self.status = QLabel()
        layout.addWidget(self.status)
        layout.addWidget(QLabel("Recent activity (double-click to open the workbook):"))
        self.log = QListWidget()
        self.log.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.log, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        service.event.connect(self._refresh)
        service.state_changed.connect(self._refresh)
        self._refresh()

    def _refresh(self, *_):
        running = self.service.running
        self.status.setText(("● Running — watching " + self.service.watcher.inbox.as_posix())
                            if running else "○ Not running")
        self.status.setStyleSheet(f"color: {(POSITIVE if running else NEGATIVE).name()}; font-weight: 600;")
        self.log.clear()
        for ts, e in self.service.history:
            item = QListWidgetItem(f"{ts}  {describe(e)}")
            item.setForeground(POSITIVE if e.ok else NEGATIVE)
            item.setData(Qt.ItemDataRole.UserRole, str(e.xlsx_path) if e.xlsx_path else "")
            self.log.addItem(item)

    def _open_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def save(self) -> None:
        inbox, out = self.inbox.text().strip(), self.out.text().strip()
        if not inbox or not out:
            error(self, "Folders required", "Choose both an inbox folder and an output folder.")
            return
        if Path(inbox).resolve() == Path(out).resolve():
            error(self, "Use two folders", "The output folder must be different from the inbox folder.")
            return
        try:
            Path(inbox).mkdir(parents=True, exist_ok=True)
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            error(self, "Can't use folder", str(e))
            return
        self.service.save_config(enabled=self.enabled.isChecked(), inbox=inbox, out=out,
                                 interval=self.interval.value(), dayfirst=self.dayfirst.isChecked())
        self._refresh()

    def done(self, r):
        self.service.event.disconnect(self._refresh)
        self.service.state_changed.disconnect(self._refresh)
        super().done(r)


class _ConvertJob(QRunnable):
    """One-off conversion off the GUI thread."""

    def __init__(self, pdfs: list[str], out_dir: str | None, signal: Signal):
        super().__init__()
        self.pdfs, self.out_dir, self.signal = pdfs, out_dir, signal

    def run(self):
        for p in self.pdfs:
            try:
                r = convert_pdf_to_excel(p, Path(self.out_dir) if self.out_dir else None)
                self.signal.emit(WatchEvent(Path(p).name, r.count > 0, r.xlsx_path, r.count, tuple(r.warnings),
                                            "" if r.count else "No transactions found"))
            except Exception as e:  # noqa: BLE001
                self.signal.emit(WatchEvent(Path(p).name, False, error=f"{type(e).__name__}: {e}"))


def convert_files_in_background(service: PdfWatchService, pdfs: list[str], out_dir: str | None = None) -> None:
    QThreadPool.globalInstance().start(_ConvertJob(pdfs, out_dir, service.event))
