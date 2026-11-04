"""Statement inbox: import bank statements dropped into a folder (local disk or OneDrive).

Each company can have its own inbox folder. Inside it the app creates one
subfolder per bank account, and files imported statements into an organized
archive (the layout is a setting)::

    <inbox>/
        Business Checking/      <- statements dropped here go into Business Checking
        Business Savings/
        Statements/             <- imported statements, filed and renamed, e.g.
            Business Checking/2026/2026-01 Business Checking.pdf
        Failed/                 <- files that couldn't be imported, with a .error.txt note

Files dropped directly in the inbox go to the company's default bank account,
if one is chosen. CSV, Excel, OFX/QFX/QBO and PDF are supported. Duplicate
transactions are skipped, so dropping the same statement twice is harmless.
New transactions are auto-categorized and left "to review".

OneDrive (and Dropbox, Google Drive, iCloud Drive) sync to an ordinary folder
on the computer, so pointing the inbox at a folder inside OneDrive lets you
drop statements in from any device, or from OneDrive on the web, and they are
imported the next time this app is running.
"""

from __future__ import annotations

import argparse
import calendar
import os
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Iterable

from .books import Account, Books
from .categorizer import Categorizer
from .companies import safe_filename
from .importers import parse_statement_bytes
from .pdf_to_excel import unique_path

ARCHIVE_DIR = "Statements"   # where imported statements are filed, organized
PROCESSED_DIR = "Processed"  # used when "keep as received" is chosen
FAILED_DIR = "Failed"
OLD_DIR = "Archive"           # statements older than the archive age (see workspace.archive_old)
RESERVED = {ARCHIVE_DIR.lower(), PROCESSED_DIR.lower(), FAILED_DIR.lower(), OLD_DIR.lower()}

# How imported statements are filed (InboxConfig.organize)
ORGANIZE_OPTIONS = {
    "account_year": "By bank account, then year",
    "year_month": "By year, then month",
    "account": "By bank account only",
    "as_received": "Keep as received (Processed folder, original names)",
}
EXTENSIONS = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".ofx", ".qfx", ".qbo", ".pdf"}
_IGNORE_SUFFIXES = (".tmp", ".partial", ".crdownload", ".download", ".part", ".error.txt")


@dataclass
class InboxConfig:
    enabled: bool = False
    folder: str = ""
    default_account_code: str = ""  # bank account for files dropped in the inbox root ("" = none)
    organize: str = "account_year"  # one of ORGANIZE_OPTIONS
    rename: bool = True             # rename to "<period> <account>.<ext>"
    save_excel: bool = False        # also save an Excel copy next to each filed PDF
    combined_excel: bool = True     # add PDF statements to one workbook in the Statements folder


def get_config(books: Books) -> InboxConfig:
    organize = books._meta("inbox_organize") or "account_year"
    return InboxConfig(books._meta("inbox_enabled") == "1", books._meta("inbox_folder") or "",
                       books._meta("inbox_default_account") or "",
                       organize if organize in ORGANIZE_OPTIONS else "account_year",
                       books._meta("inbox_rename") != "0", books._meta("inbox_save_excel") == "1",
                       books._meta("inbox_combined_excel") != "0")


def set_config(books: Books, cfg: InboxConfig) -> None:
    if cfg.enabled and not cfg.folder.strip():
        raise ValueError("Choose a folder for the statement inbox")
    if cfg.default_account_code:
        acct = books.account_by_code(cfg.default_account_code)
        if not acct.is_bank:
            raise ValueError("The default account must be a bank account")
    with books.conn:
        books._set_meta("inbox_enabled", "1" if cfg.enabled else "0")
        books._set_meta("inbox_folder", cfg.folder.strip())
        books._set_meta("inbox_default_account", cfg.default_account_code)
        books._set_meta("inbox_organize", cfg.organize if cfg.organize in ORGANIZE_OPTIONS else "account_year")
        books._set_meta("inbox_rename", "1" if cfg.rename else "0")
        books._set_meta("inbox_save_excel", "1" if cfg.save_excel else "0")
        books._set_meta("inbox_combined_excel", "1" if cfg.combined_excel else "0")
    if cfg.enabled:
        ensure_folders(books, cfg)


def account_folder_name(acct: Account) -> str:
    return safe_filename(acct.name)


def ensure_folders(books: Books, cfg: InboxConfig) -> Path:
    root = Path(cfg.folder).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    for acct in books.bank_accounts():
        (root / account_folder_name(acct)).mkdir(exist_ok=True)
    return root


def statement_period(dates: list[date]) -> str:
    """``2026-01`` when a statement sits within one month, else ``2026-01-15 to 2026-02-14``."""
    first, last = min(dates), max(dates)
    if (first.year, first.month) == (last.year, last.month):
        return f"{last.year}-{last.month:02d}"
    return f"{first.isoformat()} to {last.isoformat()}"


def archive_path(root: Path, cfg: InboxConfig, account_name: str, dates: list[date], original: str,
                 from_subfolder: str = "") -> Path:
    """Where an imported statement is filed. Pure function, so the layout is easy to preview and test."""
    if cfg.organize == "as_received" or not dates:
        base = root / PROCESSED_DIR
        return (base / from_subfolder if from_subfolder else base) / original
    last = max(dates)
    acct = safe_filename(account_name)
    year = str(last.year)
    month = f"{last.month:02d} - {calendar.month_name[last.month]}"
    if cfg.organize == "year_month":
        folder = root / ARCHIVE_DIR / year / month
    elif cfg.organize == "account":
        folder = root / ARCHIVE_DIR / acct
    else:
        folder = root / ARCHIVE_DIR / acct / year
    name = f"{statement_period(dates)} {acct}{Path(original).suffix.lower()}" if cfg.rename else original
    return folder / name


def describe_layout(cfg: InboxConfig, account_name: str = "Business Checking") -> str:
    """Example of where a January 2026 statement would be filed, for the settings screen."""
    p = archive_path(Path("<inbox>"), cfg, account_name, [date(2026, 1, 2), date(2026, 1, 30)],
                     "Statement_8812_0131.pdf", account_name)
    return p.as_posix()


def _account_for_folder(books: Books, name: str) -> Account | None:
    n = name.strip().lower()
    for acct in books.bank_accounts():
        if n in (account_folder_name(acct).lower(), acct.name.lower(), acct.code.lower(),
                 f"{acct.code} {acct.name}".lower()):
            return acct
    return None


def _is_candidate(p: Path) -> bool:
    name = p.name.lower()
    return (p.is_file() and p.suffix.lower() in EXTENSIONS and not name.startswith((".", "~$", "~"))
            and not name.endswith(_IGNORE_SUFFIXES) and name != "desktop.ini")


# ---------------------------------------------------------------------------
# OneDrive / cloud folder detection
# ---------------------------------------------------------------------------


def detect_cloud_folders(home: Path | None = None, env: dict | None = None) -> list[tuple[str, Path]]:
    """Locally synced cloud-drive folders (OneDrive first), as (label, path)."""
    home = home or Path.home()
    env = os.environ if env is None else env
    found: list[tuple[str, Path]] = []

    def add(label: str, p: Path | str | None) -> None:
        if not p:
            return
        p = Path(p)
        if p.is_dir() and all(p.resolve() != q.resolve() for _, q in found):
            found.append((label, p))

    # Windows sets these when the OneDrive client is signed in.
    add("OneDrive (work or school)", env.get("OneDriveCommercial"))
    add("OneDrive (personal)", env.get("OneDriveConsumer"))
    add("OneDrive", env.get("OneDrive"))
    # macOS File Provider locations and classic home-folder locations.
    cloud = home / "Library" / "CloudStorage"
    if cloud.is_dir():
        for p in sorted(cloud.iterdir()):
            if p.name.startswith("OneDrive"):
                suffix = p.name.partition("-")[2]
                add(f"OneDrive ({suffix})" if suffix else "OneDrive", p)
    for p in sorted(home.glob("OneDrive*")):
        suffix = p.name.partition(" - ")[2]
        add(f"OneDrive ({suffix})" if suffix else "OneDrive", p)
    for label, rel in [("Dropbox", "Dropbox"), ("Google Drive", "Google Drive"),
                       ("iCloud Drive", "Library/Mobile Documents/com~apple~CloudDocs")]:
        add(label, home / rel)
    if cloud.is_dir():
        for p in sorted(cloud.iterdir()):
            if p.name.startswith(("Dropbox", "GoogleDrive")):
                add(p.name.replace("GoogleDrive", "Google Drive"), p)
    return found


def suggested_inbox(base: Path, company_name: str) -> Path:
    return base / "AM Accounting Inbox" / safe_filename(company_name)


# ---------------------------------------------------------------------------
# Processing
# ---------------------------------------------------------------------------


@dataclass
class InboxEvent:
    company: str
    company_path: str
    file: str
    account: str = ""
    ok: bool = True
    imported: int = 0
    duplicates: int = 0
    categorized: int = 0
    error: str = ""
    filed_as: str = ""
    matched: int = 0
    workbook: str = ""
    note: str = ""  # free-form message (used for Invoice Dump results)

    def describe(self) -> str:
        if self.note:
            return f"{self.company}: {self.note}"
        if not self.ok:
            return f"{self.company}: {self.file} — not imported: {self.error}"
        dup = f", {self.duplicates} already in the books" if self.duplicates else ""
        filed = f" · filed as {Path(self.filed_as).name}" if self.filed_as else ""
        filed += f" · {self.matched} matched to invoices/bills" if self.matched else ""
        return (f"{self.company}: {self.file} → {self.account}: {self.imported} new transaction(s)"
                f"{dup}, {self.categorized} auto-categorized{filed}")


def _auto_match(books: Books) -> int:
    """Match new payments to open invoices/bills (if switched on for this company) and re-file their files."""
    from . import doc_folder as df
    from . import receivables as rx

    if not df.get_config(books).auto_match:
        return 0
    applied = rx.auto_match(books)
    if applied:
        df.refile(books)
    return len(applied)


class StatementInbox:
    """Scans company inbox folders and imports what it finds.

    A file is imported only once its size and modification time have stayed the
    same for one full scan, so files still downloading or syncing are left alone.
    """

    def __init__(self, on_event: Callable[[InboxEvent], None] | None = None):
        self.on_event = on_event or (lambda e: None)
        self._seen: dict[Path, tuple[int, float]] = {}

    def _stable(self, p: Path, require_stable: bool, still_here: dict) -> bool:
        try:
            st = p.stat()
        except OSError:
            return False
        sig = (st.st_size, st.st_mtime)
        still_here[p] = sig
        if not require_stable:
            return st.st_size > 0
        return st.st_size > 0 and self._seen.get(p) == sig

    def scan_company(self, company_path: str | Path, *, require_stable: bool = True) -> list[InboxEvent]:
        try:
            books = Books.open(company_path, create=False)
        except Exception:  # noqa: BLE001 - missing or unreadable company file: nothing to do
            return []
        try:
            cfg = get_config(books)
            if not cfg.enabled or not cfg.folder:
                return []
            root = Path(cfg.folder).expanduser()
            if not root.is_dir():
                return []
            ensure_folders(books, cfg)
            default = None
            if cfg.default_account_code:
                try:
                    default = books.account_by_code(cfg.default_account_code)
                except KeyError:
                    default = None

            jobs: list[tuple[Path, Account | None, str]] = []
            for p in sorted(root.iterdir()):
                if p.is_dir() and p.name.lower() not in RESERVED:
                    acct = _account_for_folder(books, p.name)
                    for f in sorted(p.iterdir()):
                        if _is_candidate(f):
                            jobs.append((f, acct, "" if acct else
                                         f"Folder “{p.name}” doesn't match a bank account in this company"))
                elif _is_candidate(p):
                    jobs.append((p, default, "" if default else
                                 "No default bank account is set; put the file in a bank account's folder"))

            events = []
            still_here: dict[Path, tuple[int, float]] = {}
            for f, acct, problem in jobs:
                if not self._stable(f, require_stable, still_here):
                    continue
                still_here.pop(f, None)
                events.append(self._process(books, root, cfg, f, acct, problem))
            # Remember files still waiting (plus those of other companies) for the next scan.
            self._seen = {p: s for p, s in self._seen.items() if not str(p).startswith(str(root))}
            self._seen.update(still_here)
            events += self._invoice_dump(books, require_stable)
            events += self._etransfers(books, require_stable)
            events += self._cheques(books, require_stable)
            try:
                from .parties import apply_learned

                apply_learned(books)
            except Exception:  # noqa: BLE001
                pass
            return events
        finally:
            books.close()

    def _etransfers(self, books: Books, require_stable: bool) -> list[InboxEvent]:
        """Read e-Transfer history dropped in the company folder and match it (also after each import)."""
        try:
            from .etransfer import process_folder

            msgs = process_folder(books, min_age_seconds=5.0 if require_stable else 0.0)
        except Exception as e:  # noqa: BLE001
            return [InboxEvent(books.company_name, books.path, "(e-Transfer history)", ok=False, error=str(e))]
        out = []
        for m in msgs:
            ev = InboxEvent(books.company_name, books.path, "(e-Transfer history)", note=m)
            self.on_event(ev)
            out.append(ev)
        return out

    def _cheques(self, books: Books, require_stable: bool) -> list[InboxEvent]:
        """Read cheques / drafts dropped in the company folder and match them (also after each import)."""
        try:
            from .cheques import process_folder

            results = process_folder(books, min_age_seconds=5.0 if require_stable else 0.0)
        except Exception as e:  # noqa: BLE001
            return [InboxEvent(books.company_name, books.path, "(cheques & drafts)", ok=False, error=str(e))]
        out = []
        for r in results:
            ev = InboxEvent(books.company_name, books.path, r.file, ok=r.matched, note=r.describe())
            self.on_event(ev)
            out.append(ev)
        return out

    def _invoice_dump(self, books: Books, require_stable: bool) -> list[InboxEvent]:
        """Read invoices/receipts dropped in the company's Invoice Dump and match them to bank transactions."""
        try:
            from .invoice_dump import process_dump

            results = process_dump(books, min_age_seconds=5.0 if require_stable else 0.0)
        except Exception as e:  # noqa: BLE001 - never stop statement imports over the dump
            return [InboxEvent(books.company_name, books.path, "(invoice dump)", ok=False, error=str(e))]
        out = []
        for r in results:
            ev = InboxEvent(books.company_name, books.path, r.file, ok=r.matched, note=r.describe())
            self.on_event(ev)
            out.append(ev)
        return out

    def _process(self, books: Books, root: Path, cfg: InboxConfig, f: Path, acct: Account | None,
                 problem: str) -> InboxEvent:
        ev = InboxEvent(books.company_name, books.path, f.name, acct.name if acct else "")
        dates: list[date] = []
        txs: list = []
        if problem:
            ev.ok, ev.error = False, problem
        else:
            try:
                data = f.read_bytes()
                txs, warnings = parse_statement_bytes(data, f.name)
                if not txs:
                    raise ValueError("; ".join(warnings) or "No transactions found in this file")
                dates = [t.date for t in txs]
                ev.imported, ev.duplicates = books.import_transactions(acct.id, txs, filename=f.name, file_bytes=data)
                if ev.imported:
                    new = [t for t in books.transactions(bank_account_id=acct.id, status="uncategorized")]
                    stats = Categorizer(books).run(new)
                    ev.categorized = sum(v for k, v in stats.items() if k != "unmatched")
                    ev.matched = _auto_match(books)
            except OSError as e:  # file locked / still syncing: try again next scan
                ev.ok, ev.error = False, f"Could not read file yet ({e}); will retry"
                self.on_event(ev)
                return ev
            except Exception as e:  # noqa: BLE001
                ev.ok, ev.error = False, str(e) or type(e).__name__
        if ev.ok:
            self._file_statement(root, cfg, f, ev, dates, txs)
        else:
            self._file_failed(root, f, ev)
        self.on_event(ev)
        return ev

    @staticmethod
    def _file_failed(root: Path, f: Path, ev: InboxEvent) -> None:
        dest_dir = root / FAILED_DIR
        if f.parent != root:
            dest_dir = dest_dir / f.parent.name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = unique_path(dest_dir / f.name)
        try:
            shutil.move(str(f), dest)
            dest.with_name(dest.name + ".error.txt").write_text(ev.error + "\n", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _file_statement(root: Path, cfg: InboxConfig, f: Path, ev: InboxEvent, dates: list[date],
                        txs: list | None = None) -> None:
        sub = f.parent.name if f.parent != root else ""
        target = archive_path(root, cfg, ev.account, dates, f.name, sub)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            if target.exists() and target.read_bytes() == f.read_bytes():
                f.unlink()  # identical statement already filed: don't keep a second copy
                ev.filed_as = str(target)
                return
            dest = unique_path(target)
            shutil.move(str(f), dest)
            ev.filed_as = str(dest)
        except OSError:
            return
        if cfg.save_excel and dest.suffix.lower() == ".pdf":
            try:
                from .pdf_to_excel import convert_pdf_to_excel

                convert_pdf_to_excel(dest, dest.with_suffix(".xlsx"), overwrite=True)
            except Exception:  # noqa: BLE001 - the import already succeeded; the Excel copy is a bonus
                pass
        if cfg.combined_excel and dest.suffix.lower() == ".pdf" and txs:
            try:
                from .pdf_to_excel import COMBINED_NAME, add_to_combined_workbook

                base = root / (PROCESSED_DIR if cfg.organize == "as_received" else ARCHIVE_DIR)
                ev.workbook = str(add_to_combined_workbook(base / COMBINED_NAME, dest.name, txs, account=ev.account))
            except Exception:  # noqa: BLE001 - never let the workbook block the import
                pass

    def scan_all(self, company_paths: Iterable[str | Path], *, require_stable: bool = True) -> list[InboxEvent]:
        events = []
        for p in company_paths:
            events += self.scan_company(p, require_stable=require_stable)
        return events


class InboxWatcher:
    """Runs ``StatementInbox.scan_all`` on a background thread every ``interval`` seconds."""

    def __init__(self, companies: Callable[[], list[str]], on_event: Callable[[InboxEvent], None],
                 interval: float = 10.0):
        self.companies = companies
        self.inbox = StatementInbox(on_event)
        self.interval = interval
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="statement-inbox", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout)
        self._thread = None

    def scan_now(self) -> None:
        self._wake.set()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.inbox.scan_all(self.companies())
            except Exception as e:  # noqa: BLE001
                self.inbox.on_event(InboxEvent("", "", "(inbox)", ok=False, error=str(e)))
            self._wake.wait(self.interval)
            self._wake.clear()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Import statements from company inbox folders.")
    ap.add_argument("companies", nargs="+", help="company files (.amdb) whose inbox should be processed")
    ap.add_argument("--once", action="store_true", help="process what's there now and exit")
    ap.add_argument("--interval", type=float, default=30.0, help="seconds between scans (default 30)")
    args = ap.parse_args(argv)
    report = lambda e: print(("✓ " if e.ok else "✗ ") + e.describe(), flush=True)  # noqa: E731
    if args.once:
        StatementInbox(report).scan_all(args.companies, require_stable=False)
        return 0
    w = InboxWatcher(lambda: args.companies, report, args.interval)
    print("Watching inbox folders (Ctrl+C to stop)", flush=True)
    w.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        w.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
