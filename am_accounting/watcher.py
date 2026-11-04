"""Background PDF → Excel conversion for a watched "inbox" folder.

Drop PDF statements into the inbox and each one is converted to an Excel
workbook in the output folder. Afterwards the PDF is moved to
``<inbox>/Processed`` (or ``<inbox>/Failed`` with an error note), so every file
is handled exactly once and the inbox shows only what's still waiting.

A file is converted only after its size has stayed the same for one full scan,
so PDFs that are still downloading or copying aren't picked up half-written.

Runs inside the desktop app (Bookkeeping → Automatic PDF → Excel), or on its own
without the app open::

    python -m am_accounting.watcher --inbox ~/Statements/Inbox --out ~/Statements/Excel
"""

from __future__ import annotations

import argparse
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .pdf_to_excel import ConversionResult, convert_pdf_to_excel, unique_path

PROCESSED_DIR = "Processed"
FAILED_DIR = "Failed"


@dataclass
class WatchEvent:
    pdf_name: str
    ok: bool
    xlsx_path: Path | None = None
    count: int = 0
    warnings: tuple[str, ...] = ()
    error: str = ""


class PdfFolderWatcher:
    """Polls ``inbox`` for PDFs and converts them. ``scan_once`` is safe to call directly."""

    def __init__(self, inbox: str | Path, out_dir: str | Path, *, dayfirst: bool = False,
                 on_event: Callable[[WatchEvent], None] | None = None, interval: float = 5.0):
        self.inbox = Path(inbox).expanduser()
        self.out_dir = Path(out_dir).expanduser()
        self.dayfirst = dayfirst
        self.on_event = on_event or (lambda e: None)
        self.interval = interval
        self._sizes: dict[Path, int] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- one pass -----------------------------------------------------------

    def _pending(self) -> list[Path]:
        if not self.inbox.is_dir():
            return []
        return sorted(p for p in self.inbox.iterdir()
                      if p.is_file() and p.suffix.lower() == ".pdf" and not p.name.startswith((".", "~$")))

    def scan_once(self, *, require_stable: bool = True) -> list[WatchEvent]:
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        events = []
        seen: dict[Path, int] = {}
        for pdf in self._pending():
            try:
                size = pdf.stat().st_size
            except OSError:
                continue
            seen[pdf] = size
            if require_stable and (self._sizes.get(pdf) != size or size == 0):
                continue  # new or still being written; check again next scan
            events.append(self._convert(pdf))
            seen.pop(pdf, None)
        self._sizes = seen
        return events

    def _convert(self, pdf: Path) -> WatchEvent:
        try:
            result: ConversionResult = convert_pdf_to_excel(pdf, self.out_dir / f"{pdf.stem}.xlsx",
                                                            dayfirst=self.dayfirst)
        except Exception as e:  # noqa: BLE001 - any failure is reported, never crashes the loop
            event = WatchEvent(pdf.name, False, error=f"{type(e).__name__}: {e}")
            self._move(pdf, FAILED_DIR, note=event.error)
        else:
            ok = result.count > 0
            event = WatchEvent(pdf.name, ok, result.xlsx_path, result.count, tuple(result.warnings),
                               "" if ok else "No transactions found (scanned PDF or unrecognised layout)")
            self._move(pdf, PROCESSED_DIR if ok else FAILED_DIR, note=event.error)
        self.on_event(event)
        return event

    def _move(self, pdf: Path, sub: str, *, note: str = "") -> None:
        dest_dir = self.inbox / sub
        dest_dir.mkdir(exist_ok=True)
        dest = unique_path(dest_dir / pdf.name)
        try:
            shutil.move(str(pdf), dest)
            if note:
                dest.with_suffix(".error.txt").write_text(note + "\n", encoding="utf-8")
        except OSError:
            pass  # file locked or removed meanwhile; it will be retried next scan

    # -- background thread --------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="pdf-to-excel-watcher", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except Exception as e:  # noqa: BLE001
                self.on_event(WatchEvent("(folder)", False, error=str(e)))
            self._stop.wait(self.interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Convert PDF bank statements dropped in a folder to Excel.")
    ap.add_argument("--inbox", required=True, help="folder to watch for PDF statements")
    ap.add_argument("--out", required=True, help="folder to write Excel workbooks to")
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between scans (default 5)")
    ap.add_argument("--dayfirst", action="store_true", help="dates are day/month/year")
    ap.add_argument("--once", action="store_true", help="convert what's there now and exit")
    args = ap.parse_args(argv)

    def report(e: WatchEvent) -> None:
        if e.ok:
            print(f"✓ {e.pdf_name} → {e.xlsx_path} ({e.count} transactions)", flush=True)
            for w in e.warnings:
                print(f"    warning: {w}", flush=True)
        else:
            print(f"✗ {e.pdf_name}: {e.error}", flush=True)

    w = PdfFolderWatcher(args.inbox, args.out, dayfirst=args.dayfirst, on_event=report, interval=args.interval)
    if args.once:
        w.scan_once(require_stable=False)
        return 0
    print(f"Watching {w.inbox} → {w.out_dir} (Ctrl+C to stop)", flush=True)
    w.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        w.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
