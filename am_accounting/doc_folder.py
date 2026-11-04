"""Invoice & bill folder: keep every invoice and bill file filed, renamed and sorted.

Each company can point to a folder (on this computer or in OneDrive). Files
attached to invoices and bills, and PDFs the app creates for invoices, are kept
there with consistent names, sorted by the layout you choose::

    <folder>/
        Invoices/
            Open/
                2026-10-07 INV-1903 Cedar & Pine Architects $6,100.00.pdf
            Paid/
                2026/
                    2026-08-02 INV-1188 Lakeside Clinic $3,750.00.pdf
        Bills/
            Open/ ...
            Paid/2026/ ...
        Drop files here/      <- put new invoice/bill files here to attach them
        Removed/              <- files of deleted invoices/bills

Whenever an invoice or bill changes (renamed customer, new due date, paid…)
its files are renamed and moved to match, so the folder always mirrors the books.
"""

from __future__ import annotations

import calendar
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import receivables as rx
from .books import Books
from .companies import safe_filename
from .money import fmt_money
from .pdf_to_excel import unique_path

DROP_DIR = "Drop files here"
ARCHIVE_DIR = "Archive"
REMOVED_DIR = "Removed"
KIND_DIRS = {"invoice": "Invoices", "bill": "Bills"}

LAYOUTS = {
    "status": "By status (Open / Paid), paid ones by year",
    "party": "By customer / vendor",
    "party_year": "By customer / vendor, then year",
    "year_month": "By year, then month",
}
NAME_STYLES = {
    "date_number_party_amount": "2026-10-07 INV-1903 Cedar & Pine Architects $6,100.00",
    "party_number_date": "Cedar & Pine Architects INV-1903 2026-10-07",
    "number_party": "INV-1903 Cedar & Pine Architects",
    "original": "Keep the original file name",
}


@dataclass
class FolderConfig:
    folder: str = ""
    layout: str = "status"
    name_style: str = "date_number_party_amount"
    make_pdf: bool = True        # create a PDF for each invoice made in the app
    auto_match: bool = True      # match payments to invoices/bills automatically after imports
    archive_months: int = 12     # paid invoices/bills older than this move to Archive/ (0 = never)

    @property
    def enabled(self) -> bool:
        return bool(self.folder.strip())

    @property
    def root(self) -> Path:
        return Path(self.folder).expanduser()


def get_config(books: Books) -> FolderConfig:
    m = books._meta
    layout = m("docs_layout") or "status"
    style = m("docs_name_style") or "date_number_party_amount"
    try:
        months = int(m("ws_archive_months") or 12)
    except ValueError:
        months = 12
    return FolderConfig(m("docs_folder") or "", layout if layout in LAYOUTS else "status",
                        style if style in NAME_STYLES else "date_number_party_amount",
                        m("docs_make_pdf") != "0", m("docs_auto_match") != "0", months)


def set_config(books: Books, cfg: FolderConfig) -> list[tuple[str, str]]:
    """Save settings and re-file everything to match. Returns the moves made [(old, new)]."""
    old = get_config(books)
    if cfg.layout not in LAYOUTS or cfg.name_style not in NAME_STYLES:
        raise ValueError("Unknown folder layout or file name style")
    with books.conn:
        books._set_meta("docs_folder", cfg.folder.strip())
        books._set_meta("docs_layout", cfg.layout)
        books._set_meta("docs_name_style", cfg.name_style)
        books._set_meta("docs_make_pdf", "1" if cfg.make_pdf else "0")
        books._set_meta("docs_auto_match", "1" if cfg.auto_match else "0")
    if not cfg.enabled:
        return []
    ensure_folders(cfg)
    moves = []
    if old.enabled and old.root.resolve() != cfg.root.resolve():
        moves += _move_root(books, old.root, cfg.root)
    return moves + refile(books)


def ensure_folders(cfg: FolderConfig) -> None:
    for sub in (*KIND_DIRS.values(), DROP_DIR):
        (cfg.root / sub).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def file_stem(doc: rx.Document, style: str) -> str:
    amount = fmt_money(doc.amount_cents)
    parts = {
        "date_number_party_amount": [doc.issue_date, doc.number, doc.party, amount],
        "party_number_date": [doc.party, doc.number, doc.issue_date],
        "number_party": [doc.number, doc.party],
    }[style]
    return safe_filename(" ".join(p for p in parts if p))


def months_ago(d: date, n: int) -> date:
    m = d.month - 1 - n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def is_archived(cfg: FolderConfig, doc: rx.Document, paid: bool, today: date | None = None) -> bool:
    """Paid invoices/bills issued more than ``archive_months`` ago belong in the Archive folder."""
    if not paid or cfg.archive_months <= 0:
        return False
    return date.fromisoformat(doc.issue_date) < months_ago(today or date.today(), cfg.archive_months)


def target_dir(cfg: FolderConfig, doc: rx.Document, paid: bool, today: date | None = None) -> Path:
    issued = date.fromisoformat(doc.issue_date)
    party = safe_filename(doc.party)
    if is_archived(cfg, doc, paid, today):
        base = cfg.root / ARCHIVE_DIR / KIND_DIRS[doc.kind]
        if cfg.layout == "status":
            return base / str(issued.year)
    else:
        base = cfg.root / KIND_DIRS[doc.kind]
    if cfg.layout == "status":
        return base / "Paid" / str(issued.year) if paid else base / "Open"
    if cfg.layout == "party":
        return base / party
    if cfg.layout == "party_year":
        return base / party / str(issued.year)
    return base / str(issued.year) / f"{issued.month:02d} - {calendar.month_name[issued.month]}"


def target_path(cfg: FolderConfig, doc: rx.Document, original_name: str, *, paid: bool, index: int = 0,
                today: date | None = None) -> Path:
    ext = Path(original_name).suffix.lower() or ".pdf"
    if cfg.name_style == "original":
        name = original_name
    else:
        name = file_stem(doc, cfg.name_style) + (f" ({index + 1})" if index else "") + ext
    return target_dir(cfg, doc, paid, today) / name


def describe_layout(cfg: FolderConfig) -> str:
    """Example path for the settings screen."""
    sample = rx.Document(0, "invoice", "Cedar & Pine Architects", "INV-1903", "2026-10-07", "2026-11-06", 610000,
                         0, 0, "", 0)
    p = target_path(FolderConfig("<folder>", cfg.layout, cfg.name_style), sample, "scan_0042.pdf", paid=False)
    return p.as_posix().replace("<folder>/", "")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------


@dataclass
class DocFile:
    id: int
    document_id: int
    rel_path: str
    original_name: str
    generated: bool

    def path(self, cfg: FolderConfig) -> Path:
        return cfg.root / self.rel_path


def files_for(books: Books, doc_id: int | None = None) -> list[DocFile]:
    rx.ensure_schema(books)
    sql = "SELECT * FROM document_files"
    args: tuple = ()
    if doc_id is not None:
        sql += " WHERE document_id=?"
        args = (doc_id,)
    sql += " ORDER BY document_id, generated DESC, id"
    return [DocFile(r["id"], r["document_id"], r["rel_path"], r["original_name"], bool(r["generated"]))
            for r in books.conn.execute(sql, args)]


def _require(books: Books) -> FolderConfig:
    cfg = get_config(books)
    if not cfg.enabled:
        raise ValueError("Choose an invoice & bill folder first (Invoices & Bills → Folder Settings)")
    ensure_folders(cfg)
    return cfg


def attach_file(books: Books, doc_id: int, source: str | Path, *, move: bool = True, generated: bool = False) -> Path:
    """File a document's file into the folder (renamed and sorted). Returns where it went."""
    cfg = _require(books)
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(source)
    doc = rx.get_document(books, doc_id)
    with books.conn:
        cur = books.conn.execute("INSERT INTO document_files(document_id, rel_path, original_name, generated) "
                                 "VALUES (?,?,?,?)", (doc_id, "", source.name, int(generated)))
    file_id = cur.lastrowid
    index = len([f for f in files_for(books, doc_id) if f.id != file_id])
    dest = unique_path(target_path(cfg, doc, source.name, paid=doc.balance_cents <= 0, index=index))
    dest.parent.mkdir(parents=True, exist_ok=True)
    (shutil.move if move else shutil.copy2)(str(source), dest)
    with books.conn:
        books.conn.execute("UPDATE document_files SET rel_path=? WHERE id=?",
                           (dest.relative_to(cfg.root).as_posix(), file_id))
    return dest


def replace_generated(books: Books, doc_id: int, new_pdf: str | Path) -> Path:
    """Swap in a freshly generated invoice PDF (e.g. after the invoice was edited)."""
    cfg = _require(books)
    for f in files_for(books, doc_id):
        if f.generated:
            f.path(cfg).unlink(missing_ok=True)
            with books.conn:
                books.conn.execute("DELETE FROM document_files WHERE id=?", (f.id,))
    return attach_file(books, doc_id, new_pdf, move=True, generated=True)


def detach_file(books: Books, file_id: int, *, to_drop_folder: bool = True) -> Path | None:
    """Unlink a file from its document; it goes back to the drop folder (or is deleted)."""
    cfg = _require(books)
    f = next(x for x in files_for(books) if x.id == file_id)
    p = f.path(cfg)
    dest = None
    if p.exists():
        if to_drop_folder:
            dest = unique_path(cfg.root / DROP_DIR / (f.original_name or p.name))
            shutil.move(str(p), dest)
        else:
            p.unlink()
    with books.conn:
        books.conn.execute("DELETE FROM document_files WHERE id=?", (file_id,))
    _prune_empty(cfg)
    return dest


def release_files(books: Books, doc_id: int) -> None:
    """Before deleting a document: move its files to Removed/ (generated PDFs are deleted)."""
    cfg = get_config(books)
    for f in files_for(books, doc_id):
        p = f.path(cfg) if cfg.enabled else None
        if p and p.exists():
            if f.generated:
                p.unlink()
            else:
                dest = unique_path(cfg.root / REMOVED_DIR / p.name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(p), dest)
        with books.conn:
            books.conn.execute("DELETE FROM document_files WHERE id=?", (f.id,))
    if cfg.enabled:
        _prune_empty(cfg)


def refile(books: Books, doc_id: int | None = None, *, today: date | None = None) -> list[tuple[str, str]]:
    """Rename/move files so they match their document's current details and status."""
    cfg = get_config(books)
    if not cfg.enabled:
        return []
    moves = []
    by_doc: dict[int, list[DocFile]] = {}
    for f in files_for(books, doc_id):
        by_doc.setdefault(f.document_id, []).append(f)
    for did, files in by_doc.items():
        doc = rx.get_document(books, did)
        paid = doc.balance_cents <= 0
        for index, f in enumerate(files):
            current = f.path(cfg)
            wanted = target_path(cfg, doc, f.original_name or current.name, paid=paid, index=index, today=today)
            if current == wanted:
                continue
            if not current.exists():
                continue  # missing on disk (moved by hand?) — leave the record, nothing to move
            dest = unique_path(wanted)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), dest)
            with books.conn:
                books.conn.execute("UPDATE document_files SET rel_path=? WHERE id=?",
                                   (dest.relative_to(cfg.root).as_posix(), f.id))
            moves.append((str(current), str(dest)))
    _prune_empty(cfg)
    return moves


def _move_root(books: Books, old_root: Path, new_root: Path) -> list[tuple[str, str]]:
    moves = []
    for f in files_for(books):
        src = old_root / f.rel_path
        if src.exists():
            dest = unique_path(new_root / f.rel_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), dest)
            moves.append((str(src), str(dest)))
    return moves


def _prune_empty(cfg: FolderConfig) -> None:
    """Remove folders left empty by moves (never the top-level ones)."""
    keep = {cfg.root / d for d in (*KIND_DIRS.values(), DROP_DIR)}
    for kind_dir in (*KIND_DIRS.values(), ARCHIVE_DIR):
        base = cfg.root / kind_dir
        if not base.is_dir():
            continue
        for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            if d not in keep and not any(d.iterdir()):
                d.rmdir()


def unfiled(books: Books) -> list[Path]:
    """Files waiting in the drop folder."""
    cfg = get_config(books)
    if not cfg.enabled:
        return []
    drop = cfg.root / DROP_DIR
    if not drop.is_dir():
        return []
    return sorted(p for p in drop.iterdir() if p.is_file() and not p.name.startswith((".", "~$")))


def missing_files(books: Books) -> list[DocFile]:
    cfg = get_config(books)
    return [f for f in files_for(books) if cfg.enabled and not f.path(cfg).exists()]


# ---------------------------------------------------------------------------
# Invoice document (rendered to PDF by the desktop app)
# ---------------------------------------------------------------------------


def invoice_html(books: Books, doc: rx.Document) -> str:
    import html as h

    from . import invoicing

    if doc.kind == "invoice":
        return invoicing.invoice_html(books, doc.id)
    p = books.profile()
    e = h.escape
    title = "INVOICE" if doc.kind == "invoice" else "BILL"
    who = "Bill to" if doc.kind == "invoice" else "From"
    item = e(doc.memo or books.account(doc.category_id).name)
    paid_row = (f"<tr><td>Paid</td><td class='r'>({e(fmt_money(doc.paid_cents))})</td></tr>"
                if doc.paid_cents else "")
    status = "PAID" if doc.balance_cents <= 0 else ""
    return f"""<html><head><style>
body {{ font-family: Helvetica, Arial, sans-serif; color: #1c2430; font-size: 10.5pt; }}
h1 {{ font-size: 22pt; margin: 0; color: #1f3a5f; }}
.company {{ font-size: 14pt; font-weight: bold; }}
.muted {{ color: #5b6573; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ padding: 6px 8px; }}
th {{ text-align: left; border-bottom: 1.5px solid #1c2430; }}
.r {{ text-align: right; }}
.total td {{ font-weight: bold; border-top: 1.5px solid #1c2430; font-size: 12pt; }}
.stamp {{ color: #1b7f3b; font-size: 20pt; font-weight: bold; }}
</style></head><body>
<table><tr>
<td><div class="company">{e(p.name)}</div><div class="muted">{e(p.legal_name)}</div>
{f'<div class="muted">Tax ID {e(p.tax_id)}</div>' if p.tax_id else ''}</td>
<td class="r"><h1>{title}</h1><div class="stamp">{status}</div></td></tr></table>
<br>
<table><tr>
<td><b>{who}</b><br>{e(doc.party)}</td>
<td class="r"><b>Number</b> {e(doc.number or '—')}<br><b>Date</b> {e(doc.issue_date)}<br>
<b>Due</b> {e(doc.due_date)}</td></tr></table>
<br><br>
<table><tr><th>Description</th><th class="r">Amount</th></tr>
<tr><td>{item}</td><td class="r">{e(fmt_money(doc.amount_cents))}</td></tr>
<tr class="total"><td>Total</td><td class="r">{e(fmt_money(doc.amount_cents))}</td></tr>
{paid_row}
<tr class="total"><td>Balance due</td><td class="r">{e(fmt_money(max(doc.balance_cents, 0)))}</td></tr>
</table>
<br><p class="muted">Please include the invoice number with your payment. Thank you for your business.</p>
</body></html>"""
