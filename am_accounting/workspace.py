"""Company folder: one folder the user picks, organised and maintained by the app.

::

    <Company folder>/
        Bank Statements/         statement inbox (one subfolder per bank account; imported files
                                 are filed under Statements/, PDFs also go into one Excel workbook)
        Invoices & Bills/        invoice and bill files, sorted Open / Paid
        Financial Statements/    saved automatically on the chosen schedule
            2026/
                2026-10 October/
                    Income Statement - October 2026.pdf
                    Balance Sheet - 31 Oct 2026.pdf
                    ...
        Trial Balance/           trial balance Excel workbook for each period end, same schedule
            2026/Trial Balance - 31 Oct 2026.xlsx
        Backups/                 copies of the company file, on the same schedule
        Cheques & Drafts/        drop cheque / bank draft scans and photos: matched to the bank's
                                 "CHQ#00126" / "DRAFT 05551234" lines and renamed (see cheques.py)
        E-Transfer History/      drop e-Transfer history (screenshots, PDF, CSV): matched to the
                                 bank's "SEND E-TFR ***k9q" lines (see etransfer.py); read files go to Read/
        Invoice Dump/            drop invoices, bills and receipts: each is matched to its bank
                                 transaction and filed under Recognized/; unmatched ones the user
                                 marks go to Future Payables/ or Unrelated/ (see invoice_dump.py)
        Saved Invoices/          invoices matched AND recorded, renamed
                                 "<transaction date> <invoice number> <vendor>"

Each of these has an Archive/ folder: anything dated more than 12 months ago
(configurable) is moved there automatically, keeping its year/month structure.
Open invoices and bills are never archived.

The schedule (daily, weekly, monthly, quarterly, yearly) saves each *completed*
period once; periods missed while the app was closed are caught up next time it
runs (up to a year back).
"""

from __future__ import annotations

import calendar
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from .books import Books
from .companies import safe_filename

BANK_DIR = "Bank Statements"
DOCS_DIR = "Invoices & Bills"
REPORTS_DIR = "Financial Statements"
BACKUP_DIR = "Backups"

FREQUENCIES = {
    "off": "Don't save automatically",
    "daily": "Daily (previous day)",
    "weekly": "Weekly (Monday–Sunday)",
    "monthly": "Monthly",
    "quarterly": "Quarterly",
    "yearly": "Yearly",
}
REPORT_CHOICES = {
    "income_statement": "Income Statement (P&L)",
    "balance_sheet": "Balance Sheet",
    "cash_flow": "Statement of Cash Flows",
}
FORMATS = {"pdf": "PDF", "xlsx": "Excel"}
MAX_CATCH_UP = {"daily": 31, "weekly": 26, "monthly": 12, "quarterly": 4, "yearly": 2}


@dataclass
class WorkspaceConfig:
    root: str = ""
    frequency: str = "monthly"
    reports: list[str] = field(default_factory=lambda: ["income_statement", "balance_sheet", "cash_flow"])
    formats: list[str] = field(default_factory=lambda: ["pdf", "xlsx"])
    year_to_date: bool = True      # also save income statement / cash flow for the year to date
    backups: bool = True
    keep_backups: int = 24
    archive_months: int = 12       # move anything older than this into each folder's Archive/ (0 = never)
    trial_balance: bool = True     # save a trial balance workbook (Excel) into Trial Balance/ each period

    @property
    def enabled(self) -> bool:
        return bool(self.root.strip())

    @property
    def path(self) -> Path:
        return Path(self.root).expanduser()


def get_config(books: Books) -> WorkspaceConfig:
    m = books._meta
    freq = m("ws_frequency") or "monthly"
    reports = [r for r in (m("ws_reports") or "income_statement,balance_sheet,cash_flow").split(",")
               if r in REPORT_CHOICES]
    formats = [f for f in (m("ws_formats") or "pdf,xlsx").split(",") if f in FORMATS]
    try:
        keep = int(m("ws_keep_backups") or 24)
    except ValueError:
        keep = 24
    try:
        months = int(m("ws_archive_months") or 12)
    except ValueError:
        months = 12
    return WorkspaceConfig(m("ws_root") or "", freq if freq in FREQUENCIES else "monthly", reports, formats,
                           m("ws_ytd") != "0", m("ws_backups") != "0", keep, months, m("ws_tb") != "0")


def set_config(books: Books, cfg: WorkspaceConfig, *, connect_inbox: bool = True,
               connect_documents: bool = True) -> Path | None:
    """Save the company-folder settings, create the folders and point the inbox and invoice folder at them."""
    if cfg.frequency not in FREQUENCIES:
        raise ValueError(f"Unknown frequency {cfg.frequency!r}")
    with books.conn:
        books._set_meta("ws_root", cfg.root.strip())
        books._set_meta("ws_frequency", cfg.frequency)
        books._set_meta("ws_reports", ",".join(r for r in cfg.reports if r in REPORT_CHOICES))
        books._set_meta("ws_formats", ",".join(f for f in cfg.formats if f in FORMATS))
        books._set_meta("ws_ytd", "1" if cfg.year_to_date else "0")
        books._set_meta("ws_backups", "1" if cfg.backups else "0")
        books._set_meta("ws_keep_backups", str(cfg.keep_backups))
        books._set_meta("ws_archive_months", str(max(0, cfg.archive_months)))
        books._set_meta("ws_tb", "1" if cfg.trial_balance else "0")
    if not cfg.enabled:
        return None
    root = cfg.path
    from .invoice_dump import DUMP_DIR, ensure_folders as ensure_dump

    from .trial_balance import TB_DIR

    from .cheques import DIR_NAME as CHEQUE_DIR, ensure_folders as ensure_cheques
    from .etransfer import DIR_NAME as ETRANSFER_DIR

    for sub in (BANK_DIR, DOCS_DIR, REPORTS_DIR, TB_DIR, BACKUP_DIR, ETRANSFER_DIR):
        (root / sub).mkdir(parents=True, exist_ok=True)
    ensure_cheques(root / CHEQUE_DIR)
    ensure_dump(root / DUMP_DIR)
    if connect_inbox:
        from . import inbox

        icfg = inbox.get_config(books)
        icfg.enabled, icfg.folder = True, str(root / BANK_DIR)
        inbox.set_config(books, icfg)
    if connect_documents:
        from . import doc_folder

        dcfg = doc_folder.get_config(books)
        dcfg.folder = str(root / DOCS_DIR)
        doc_folder.set_config(books, dcfg)
    archive_old(books)
    return root


def suggested_root(base: Path, company_name: str) -> Path:
    return base / "AM Accounting" / safe_filename(company_name)


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Period:
    start: date
    end: date
    label: str      # "October 2026", "Week of 26 Oct 2026", "Q3 2026", "2026", "1 Nov 2026"
    key: str        # sortable id: "2026-10", "2026-W44", "2026-Q3", "2026", "2026-11-01"
    folder: str     # sub-folder name under the year


def _period_of(freq: str, d: date) -> Period:
    if freq == "daily":
        return Period(d, d, f"{d.day} {d:%b %Y}", d.isoformat(), f"{d:%Y-%m} {d:%B}")
    if freq == "weekly":
        start = d - timedelta(days=d.weekday())
        end = start + timedelta(days=6)
        iso = start.isocalendar()
        return Period(start, end, f"Week of {start.day} {start:%b %Y}", f"{iso[0]}-W{iso[1]:02d}",
                      f"Week {iso[1]:02d} ({start:%b} {start.day})")
    if freq == "monthly":
        start = d.replace(day=1)
        end = date(d.year, d.month, calendar.monthrange(d.year, d.month)[1])
        return Period(start, end, f"{d:%B %Y}", f"{d:%Y-%m}", f"{d:%Y-%m} {d:%B}")
    if freq == "quarterly":
        q = (d.month - 1) // 3 + 1
        start = date(d.year, 3 * q - 2, 1)
        end_month = 3 * q
        end = date(d.year, end_month, calendar.monthrange(d.year, end_month)[1])
        return Period(start, end, f"Q{q} {d.year}", f"{d.year}-Q{q}", f"Q{q}")
    if freq == "yearly":
        return Period(date(d.year, 1, 1), date(d.year, 12, 31), str(d.year), str(d.year), "Full year")
    raise ValueError(freq)


def completed_periods(freq: str, today: date, *, after_key: str | None = None) -> list[Period]:
    """Completed periods (oldest first) not yet saved, newest no later than yesterday."""
    if freq == "off":
        return []
    out: list[Period] = []
    current = _period_of(freq, today)
    p = _period_of(freq, current.start - timedelta(days=1))
    while len(out) < MAX_CATCH_UP[freq]:
        if after_key is not None and p.key <= after_key:
            break
        out.append(p)
        p = _period_of(freq, p.start - timedelta(days=1))
    return list(reversed(out))


def due_periods(books: Books, today: date | None = None) -> list[Period]:
    cfg = get_config(books)
    if not cfg.enabled:
        return []
    last = books._meta(f"ws_last_{cfg.frequency}")
    periods = completed_periods(cfg.frequency, today or date.today(), after_key=last)
    if last is None and periods:
        periods = periods[-1:]  # first time: just the most recent completed period, no flood of history
    first, _ = books.date_range()
    if first:
        periods = [p for p in periods if p.end >= date.fromisoformat(first)]
    return periods


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------

PdfWriter = Callable[[str, str], None]  # (html, path)


def report_to_xlsx(report, path: Path) -> None:
    """Write a financial statement to an Excel workbook (numbers as numbers, not text)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    wb = Workbook()
    ws = wb.active
    ws.title = report.title[:31]
    ws.append([report.company])
    ws.append([report.title])
    ws.append([report.subtitle])
    ws.append([])
    ws.append([""] + report.columns)
    for cell in ws[1] + ws[2]:
        cell.font = Font(bold=True, size=13)
    for cell in ws[5]:
        cell.font = Font(bold=True)
        cell.border = Border(bottom=Side(style="thin"))
    money = '#,##0.00;[Red](#,##0.00)'
    for line in report.lines:
        if line.kind == "spacer":
            ws.append([])
            continue
        ws.append([line.label] + [v / 100 if v is not None else None for v in line.values])
        row = ws.max_row
        ws.cell(row=row, column=1).alignment = Alignment(indent=line.indent)
        bold = line.kind in ("header", "total", "subtotal", "group", "groupsum")
        for col in range(1, len(report.columns) + 2):
            cell = ws.cell(row=row, column=col)
            if col > 1:
                cell.number_format = money
            if bold:
                cell.font = Font(bold=True, italic=line.kind == "note")
            if line.kind == "total" and col > 1:
                cell.border = Border(top=Side(style="thin"), bottom=Side(style="double"))
            elif line.kind in ("subtotal", "groupsum") and col > 1:
                cell.border = Border(top=Side(style="thin"))
    ws.column_dimensions["A"].width = 48
    from openpyxl.utils import get_column_letter

    for i in range(len(report.columns)):
        ws.column_dimensions[get_column_letter(i + 2)].width = 18 if len(report.columns) <= 4 else 14
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


@dataclass
class SaveResult:
    period: Period
    files: list[Path] = field(default_factory=list)
    backup: Path | None = None
    errors: list[str] = field(default_factory=list)


def save_period(books: Books, period: Period, *, pdf_writer: PdfWriter | None = None,
                cfg: WorkspaceConfig | None = None) -> SaveResult:
    """Save the chosen financial statements for one period into Financial Statements/<year>/<period>/."""
    from . import reports

    cfg = cfg or get_config(books)
    result = SaveResult(period)
    folder = cfg.path / REPORTS_DIR / str(period.end.year) / period.folder
    end_label = f"{period.end.day} {period.end:%b %Y}"
    jobs = []
    for r in cfg.reports:
        if r == "income_statement":
            jobs.append((f"Income Statement - {period.label}",
                         reports.income_statement(books, period.start.isoformat(), period.end.isoformat())))
        elif r == "cash_flow":
            jobs.append((f"Cash Flow - {period.label}",
                         reports.cash_flow(books, period.start.isoformat(), period.end.isoformat())))
        elif r == "balance_sheet":
            jobs.append((f"Balance Sheet - {end_label}",
                         reports.balance_sheet(books, period.end.isoformat(),
                                               fiscal_year_start_month=books.fiscal_year_start_month)))
    if cfg.year_to_date and cfg.frequency not in ("yearly",):
        fy = books.fiscal_year_start_month
        ytd_start = date(period.end.year if period.end.month >= fy else period.end.year - 1, fy, 1)
        if ytd_start < period.start:
            if "income_statement" in cfg.reports:
                jobs.append((f"Income Statement - Year to Date {end_label}",
                             reports.income_statement(books, ytd_start.isoformat(), period.end.isoformat())))
            if "cash_flow" in cfg.reports:
                jobs.append((f"Cash Flow - Year to Date {end_label}",
                             reports.cash_flow(books, ytd_start.isoformat(), period.end.isoformat())))
    for name, report in jobs:
        stem = safe_filename(name)
        try:
            if "pdf" in cfg.formats and pdf_writer is not None:
                path = folder / f"{stem}.pdf"
                folder.mkdir(parents=True, exist_ok=True)
                pdf_writer(report.to_html(), str(path))
                result.files.append(path)
            if "xlsx" in cfg.formats:
                path = folder / f"{stem}.xlsx"
                report_to_xlsx(report, path)
                result.files.append(path)
        except PermissionError:
            result.errors.append(f"{stem}: the file is open in another program; it will be saved next time")
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{stem}: {e}")
    if cfg.trial_balance:
        from . import trial_balance as tb

        path = tb.folder_path(cfg.path, period.end) / tb.file_name(period.end)
        try:
            result.files.append(tb.write_xlsx(books, path, period.end, period.start))
        except PermissionError:
            result.errors.append(f"{path.stem}: the file is open in another program; it will be saved next time")
        except Exception as e:  # noqa: BLE001
            result.errors.append(f"{path.stem}: {e}")
    return result


def backup_books(books: Books, cfg: WorkspaceConfig | None = None, *, stamp: str | None = None) -> Path | None:
    """Copy the company file into Backups/ (a consistent snapshot, safe while the books are open)."""
    cfg = cfg or get_config(books)
    if not cfg.enabled or books.path == ":memory:":
        return None
    folder = cfg.path / BACKUP_DIR
    folder.mkdir(parents=True, exist_ok=True)
    stamp = stamp or datetime.now().strftime("%Y-%m-%d %H%M")
    dest = folder / f"{safe_filename(books.company_name)} {stamp}.amdb"
    target = sqlite3.connect(dest)
    try:
        books.conn.backup(target)
    finally:
        target.close()
    backups = sorted(folder.glob(f"{safe_filename(books.company_name)} *.amdb"))
    for old in backups[:-cfg.keep_backups] if cfg.keep_backups > 0 else []:
        old.unlink(missing_ok=True)
    return dest


def run_schedule(books: Books, *, pdf_writer: PdfWriter | None = None, today: date | None = None
                 ) -> list[SaveResult]:
    """Save everything that's due. Safe to call often: each completed period is saved once."""
    cfg = get_config(books)
    if not cfg.enabled or cfg.frequency == "off":
        return []
    results = []
    for period in due_periods(books, today):
        res = save_period(books, period, pdf_writer=pdf_writer, cfg=cfg)
        if cfg.backups and period is not None:
            try:
                res.backup = backup_books(books, cfg, stamp=period.key)
            except Exception as e:  # noqa: BLE001
                res.errors.append(f"Backup: {e}")
        if not res.errors:
            with books.conn:
                books._set_meta(f"ws_last_{cfg.frequency}", period.key)
        results.append(res)
    if results:
        with books.conn:
            books._set_meta("ws_last_run", datetime.now().isoformat(timespec="seconds"))
    return results


def next_due(books: Books, today: date | None = None) -> date | None:
    """The day the next automatic save happens (the day after the current period ends)."""
    cfg = get_config(books)
    if not cfg.enabled or cfg.frequency == "off":
        return None
    if due_periods(books, today):
        return today or date.today()
    return _period_of(cfg.frequency, today or date.today()).end + timedelta(days=1)


# ---------------------------------------------------------------------------
# Archiving: anything older than N months moves into <folder>/Archive/
# ---------------------------------------------------------------------------

ARCHIVE = "Archive"
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def date_from_name(name: str) -> date | None:
    """The date a filed document covers, read from its name.

    '2026-01 Business Checking.pdf' -> 31 Jan 2026; '2026-01-15 to 2026-02-14 …' -> 14 Feb 2026;
    'Income Statement - October 2026' -> 31 Oct 2026; 'Balance Sheet - 31 Oct 2026' -> 31 Oct 2026;
    'Cash Flow - Q3 2026' -> 30 Sep 2026; 'Week of 26 Oct 2026' -> 1 Nov 2026; '… - 2025' -> 31 Dec 2025.
    """
    import re

    stem = Path(name).stem
    m = re.search(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", stem)
    if m:
        return date.fromisoformat(m.group(2))
    m = re.search(r"Week of (\d{1,2}) ([A-Za-z]{3,9}) (\d{4})", stem)
    if m and m.group(2).lower() in _MONTHS:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1))) + timedelta(days=6)
    m = re.search(r"(\d{1,2}) ([A-Za-z]{3,9}) (\d{4})", stem)
    if m and m.group(2).lower() in _MONTHS:
        return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
    m = re.search(r"\bQ([1-4]) (\d{4})", stem)
    if m:
        return _month_end(int(m.group(2)), int(m.group(1)) * 3)
    m = re.search(r"\b([A-Za-z]{3,9}) (\d{4})\b", stem)
    if m and m.group(1).lower() in _MONTHS:
        return _month_end(int(m.group(2)), _MONTHS[m.group(1).lower()])
    m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", stem)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.search(r"\b(\d{4})-W(\d{2})\b", stem)
    if m:
        return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 7)
    m = re.search(r"\b(\d{4})-Q([1-4])\b", stem)
    if m:
        return _month_end(int(m.group(1)), int(m.group(2)) * 3)
    m = re.search(r"\b(\d{4})-(\d{2})\b", stem)
    if m and 1 <= int(m.group(2)) <= 12:
        return _month_end(int(m.group(1)), int(m.group(2)))
    m = re.search(r"(?:- | )(\d{4})$", stem)
    if m and 1990 < int(m.group(1)) < 2200:
        return date(int(m.group(1)), 12, 31)
    return None


def _file_date(path: Path) -> date:
    return date_from_name(path.name) or date.fromtimestamp(path.stat().st_mtime)


def _archive_tree(src: Path, dest: Path, cutoff: date, *, skip: set[str] = frozenset()) -> list[tuple[str, str]]:
    """Move files under ``src`` dated before ``cutoff`` to the same relative place under ``dest``."""
    from .pdf_to_excel import unique_path

    moves = []
    if not src.is_dir():
        return moves
    for f in sorted(p for p in src.rglob("*") if p.is_file()):
        rel = f.relative_to(src)
        if rel.parts[0] == ARCHIVE or f.name in skip or f.name.startswith((".", "~$")):
            continue
        try:
            if _file_date(f) >= cutoff:
                continue
            target = unique_path(dest / rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            f.rename(target)
            moves.append((str(f), str(target)))
        except OSError:
            continue  # in use (e.g. open in Excel): try again next time
    # tidy up folders left empty
    for d in sorted((p for p in src.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        try:
            if d.relative_to(src).parts[0] != ARCHIVE and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    return moves


def archive_old(books: Books, today: date | None = None) -> list[tuple[str, str]]:
    """Move everything older than the archive age into each folder's Archive/. Returns the moves."""
    cfg = get_config(books)
    if not cfg.enabled or cfg.archive_months <= 0:
        return []
    from . import doc_folder
    from .inbox import ARCHIVE_DIR as FILED, PROCESSED_DIR
    from .pdf_to_excel import COMBINED_NAME

    cutoff = doc_folder.months_ago(today or date.today(), cfg.archive_months)
    root = cfg.path
    keep = {COMBINED_NAME}
    moves = []
    bank = root / BANK_DIR
    moves += _archive_tree(bank / FILED, bank / ARCHIVE, cutoff, skip=keep)
    moves += _archive_tree(bank / PROCESSED_DIR, bank / ARCHIVE / PROCESSED_DIR, cutoff)
    moves += _archive_tree(root / REPORTS_DIR, root / REPORTS_DIR / ARCHIVE, cutoff)
    from .trial_balance import TB_DIR

    moves += _archive_tree(root / TB_DIR, root / TB_DIR / ARCHIVE, cutoff)
    moves += _archive_tree(root / BACKUP_DIR, root / BACKUP_DIR / ARCHIVE, cutoff)
    dcfg = doc_folder.get_config(books)
    if dcfg.enabled:
        moves += doc_folder.refile(books, today=today)  # paid invoices/bills move to Invoices & Bills/Archive
    from . import invoice_dump

    moves += invoice_dump.archive(books, cutoff)
    from . import cheques

    moves += cheques.archive(books, cutoff)  # recognized items move to Invoice Dump/Archive
    return moves
