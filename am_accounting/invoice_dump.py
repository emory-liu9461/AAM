"""Invoice Dump: drop invoices, bills and receipts in one folder; they're matched to bank transactions.

::

    <Company folder>/Invoice Dump/
        (drop PDF, JPG or PNG files here)
        Recognized/2026/2026-10/2026-10-25 Harbor Supply Co $2,125.00.pdf   (matched, expense not recorded yet)
        Not Yet Matched/<original name>
    <Company folder>/Saved Invoices/2026/2026-10/2026-10-25 HS-44871 Harbor Supply Co.pdf
                                    (matched AND recorded: renamed with transaction date + invoice number)
        Archive/...                     (recognized items older than the archive age)

For each file the app reads the text (OCR for scans and photos), finds the total,
the date, the invoice number and the names on it, then looks for a bank
transaction of exactly that amount, near that date, preferably mentioning the
number or a name from the document. A confident match is linked to the
transaction (shown with 📎) and the file is renamed into ``Recognized``.
Everything else waits in ``Not Yet Matched`` and is checked again whenever new
statements are imported; it can also be matched by hand.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .books import Books, Transaction, merchant_key
from .companies import safe_filename
from .dates import parse_date
from .money import fmt_money, parse_money
from .pdf_to_excel import unique_path

DUMP_DIR = "Invoice Dump"
SAVED_DIR = "Saved Invoices"      # top-level folder for invoices that are matched AND recorded
SAVED_PREFIX = "@saved/"          # rel_path prefix for files kept under Saved Invoices/
RECOGNIZED_DIR = "Recognized"
PENDING_DIR = "Not Yet Matched"
PAYABLES_DIR = "Future Payables"   # not paid yet: recorded as a bill in Accounts Payable
UNRELATED_DIR = "Unrelated"        # not a bill of this company (wrong invoice, personal, sent by mistake)
ARCHIVE_DIR = "Archive"
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS dump_files (
    id             INTEGER PRIMARY KEY,
    rel_path       TEXT NOT NULL,
    original_name  TEXT NOT NULL,
    file_hash      TEXT NOT NULL UNIQUE,
    status         TEXT NOT NULL CHECK (status IN ('matched','unmatched','payable','unrelated')),
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    doc_date       TEXT,
    amount_cents   INTEGER,
    alt_amounts    TEXT NOT NULL DEFAULT '',
    party          TEXT NOT NULL DEFAULT '',
    number         TEXT NOT NULL DEFAULT '',
    words          TEXT NOT NULL DEFAULT '',
    reason         TEXT NOT NULL DEFAULT '',
    added_at       TEXT NOT NULL,
    document_id    INTEGER
);
CREATE INDEX IF NOT EXISTS ix_dump_tx ON dump_files(transaction_id);
"""


def ensure_schema(books: Books) -> None:
    row = books.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='dump_files'").fetchone()
    if row and "'payable'" not in row[0]:
        # Older books: rebuild the table so it accepts the Future Payable / Unrelated statuses
        cols = [c[1] for c in books.conn.execute("PRAGMA table_info(dump_files)")]
        keep = ", ".join(cols)
        with books.conn:
            books.conn.execute("ALTER TABLE dump_files RENAME TO dump_files_old")
            books.conn.execute("DROP INDEX IF EXISTS ix_dump_tx")
            books.conn.executescript(SCHEMA)
            books.conn.execute(f"INSERT INTO dump_files({keep}) SELECT {keep} FROM dump_files_old")
            books.conn.execute("DROP TABLE dump_files_old")
    books.conn.executescript(SCHEMA)


def dump_root(books: Books) -> Path | None:
    from . import workspace as ws

    cfg = ws.get_config(books)
    return cfg.path / DUMP_DIR if cfg.enabled else None


def ensure_folders(root: Path) -> None:
    for sub in ("", RECOGNIZED_DIR, PENDING_DIR, PAYABLES_DIR, UNRELATED_DIR):
        (root / sub).mkdir(parents=True, exist_ok=True)
    (root.parent / SAVED_DIR).mkdir(parents=True, exist_ok=True)


def saved_root(books: Books) -> Path | None:
    root = dump_root(books)
    return root.parent / SAVED_DIR if root is not None else None


def _abs(books: Books, rel: str) -> Path:
    """Where a tracked file lives: Saved Invoices/… for '@saved/' paths, otherwise inside the Invoice Dump."""
    if rel.startswith(SAVED_PREFIX):
        return saved_root(books) / rel[len(SAVED_PREFIX):]
    return dump_root(books) / rel


def _rel(books: Books, path: Path) -> str:
    saved = saved_root(books)
    try:
        return SAVED_PREFIX + path.relative_to(saved).as_posix()
    except ValueError:
        return path.relative_to(dump_root(books)).as_posix()


def is_recorded(books: Books, tx: Transaction | None) -> bool:
    """True when the bank transaction has a real expense/income category (not Uncategorized)."""
    from . import chart_of_accounts as coa

    if tx is None or tx.category_id is None:
        return False
    try:
        acct = books.account(tx.category_id)
    except KeyError:
        return False
    return acct.key not in (coa.UNCATEGORIZED_EXPENSE, coa.UNCATEGORIZED_INCOME)


# ---------------------------------------------------------------------------
# Reading a document
# ---------------------------------------------------------------------------

AMOUNT_RE = re.compile(r"(?<![\d.])\(?-?[$€£]?\s?\d{1,3}(?:[,\s]\d{3})*\.\d{2}\)?(?![\d])")
TOTAL_KEYS = [  # most specific first
    re.compile(r"(amount|balance|total)\s*(due|payable|owing)|please\s*pay|pay\s*this\s*amount", re.I),
    re.compile(r"grand\s*total|invoice\s*total|total\s*amount|total\s*\(?(usd|cad|eur|gbp)\)?|"
               r"amount\s*paid|total\s*paid|payment\s*amount|total\s*charges", re.I),
    re.compile(r"(?<!sub)(?<!sub-)\btotal\b", re.I),
]
DATE_KEYS = re.compile(r"invoice\s*date|date\s*of\s*issue|issue\s*date|bill\s*date|receipt\s*date|"
                       r"date\s*issued|^date\b|\bdate\s*:", re.I)
NUMBER_RE = re.compile(r"\b(?:invoice|inv|bill|receipt|order|reference|ref)\s*(?:no\.?|number|num|#)?\s*[:#.]?\s*"
                       r"([A-Z]{0,6}[-#]?\d[A-Z0-9\-/]{1,20})", re.I)
DATE_TEXT_RE = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}-\d{1,2}-\d{2,4}|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}|"
    r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?,?\s+\d{4})\b", re.I)
STOP_WORDS = {"INVOICE", "BILL", "RECEIPT", "TOTAL", "AMOUNT", "DATE", "DUE", "PAGE", "TAX", "SUBTOTAL",
              "BALANCE", "PAYMENT", "THANK", "YOU", "PLEASE", "FROM", "INC", "LLC", "LTD", "THE", "AND", "FOR",
              "WITH", "QTY", "PRICE", "DESCRIPTION", "ITEM", "UNIT", "NUMBER", "CUSTOMER", "ACCOUNT", "TERMS",
              "NET", "PAID", "USD", "CAD", "HST", "GST", "VAT", "SALES", "SHIP", "BILLED"}


@dataclass
class DocInfo:
    total: int | None = None
    alternates: list[int] = field(default_factory=list)
    doc_date: date | None = None
    number: str = ""
    party: str = ""
    words: set[str] = field(default_factory=set)
    text: str = ""


def _amounts(line: str) -> list[int]:
    out = []
    for m in AMOUNT_RE.finditer(line):
        v = parse_money(m.group(0).replace(" ", ""))
        if v:
            out.append(abs(v))
    return out


def extract_info(text: str) -> DocInfo:
    info = DocInfo(text=text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    # Total: the amount on the most specific "total"/"amount due" line
    for rx in TOTAL_KEYS:
        found = []
        for i, ln in enumerate(lines):
            if rx.search(ln):
                vals = _amounts(ln) or (_amounts(lines[i + 1]) if i + 1 < len(lines) else [])
                if vals:
                    found.append(vals[-1])
        if found:
            info.total = max(found)
            break
    every = sorted({v for ln in lines for v in _amounts(ln)}, reverse=True)
    if info.total is None and every:
        info.total = every[0]
    info.alternates = [v for v in every if v != info.total][:6]
    # Date: on a date line first, else the first date anywhere
    for ln in lines:
        if DATE_KEYS.search(ln):
            m = DATE_TEXT_RE.search(ln)
            if m and (d := parse_date(m.group(1).replace(",", ", ").replace("  ", " "))):
                info.doc_date = d
                break
    if info.doc_date is None:
        for m in DATE_TEXT_RE.finditer(text):
            d = parse_date(m.group(1).replace(",", ", ").replace("  ", " "))
            if d:
                info.doc_date = d
                break
    m = NUMBER_RE.search(text)
    if m:
        info.number = m.group(1).strip("-#/.")
    # Party: the first line that looks like a name (not a heading, date, amount or address number)
    for ln in lines[:12]:
        clean = re.sub(r"[^A-Za-z&' .]", " ", ln).strip()
        words = [w for w in clean.split() if len(w) > 1]
        if (len(words) >= 1 and len(clean) >= 3 and not re.search(r"\b(invoice|receipt|bill|statement|page|date|"
                                                                   r"tax|total)\b", ln, re.I)
                and not AMOUNT_RE.search(ln) and not re.match(r"^\d", ln)):
            info.party = " ".join(clean.split())[:60]
            break
    info.words = {w for w in re.findall(r"[A-Z][A-Z&']{2,}", text.upper()) if w not in STOP_WORDS}
    return info


def read_document(path: Path) -> tuple[str, str]:
    """(method, text) of an invoice/receipt file — PDF text, or OCR for scans and photos."""
    ext = path.suffix.lower()
    data = path.read_bytes()
    if ext == ".pdf":
        from .importers.pdf_importer import extract_text, text_is_usable

        try:
            text = extract_text(data)
        except Exception:  # noqa: BLE001
            text = ""
        if text_is_usable(text) or len(re.findall(r"[A-Za-z]{3,}", text)) >= 6:
            return "PDF text", text
        from .importers.ocr import ocr_available, ocr_pdf_text

        return ("OCR", ocr_pdf_text(data, max_pages=5)) if ocr_available() else ("none", text)
    from .importers.ocr import ocr_available, ocr_image_text

    return ("OCR", ocr_image_text(path)) if ocr_available() else ("none", "")


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    tx: Transaction
    score: float
    why: str


def linked_transaction_ids(books: Books) -> set[int]:
    ensure_schema(books)
    return {r[0] for r in books.conn.execute(
        "SELECT transaction_id FROM dump_files WHERE status='matched' AND transaction_id IS NOT NULL")}


def candidates(books: Books, info: DocInfo, *, exclude: set[int] | None = None,
               any_amount: bool = False) -> list[Candidate]:
    """Bank transactions that could be this document's payment, best first."""
    exclude = exclude if exclude is not None else linked_transaction_ids(books)
    amounts = [a for a in [info.total, *info.alternates] if a]
    out = []
    for tx in books.transactions():
        if tx.id in exclude:
            continue
        amt = abs(tx.amount_cents)
        if not any_amount and amt not in amounts[:3]:
            continue
        td = date.fromisoformat(tx.date)
        if info.doc_date and not any_amount and not (info.doc_date - timedelta(days=15) <= td
                                                     <= info.doc_date + timedelta(days=120)):
            continue
        score, why = 0.0, []
        if amt == info.total:
            score += 3
            why.append("exact total")
        elif amt in amounts:
            score += 1.5
            why.append("amount on the document")
        desc = tx.description.upper()
        if info.number and re.sub(r"\W", "", info.number.upper()) in re.sub(r"\W", "", desc):
            score += 4
            why.append("invoice number")
        key_words = {w for w in re.findall(r"[A-Z][A-Z&']{2,}", desc)} - STOP_WORDS
        shared = key_words & info.words
        if shared:
            score += min(3, 1.5 * len(shared))
            why.append("name " + " ".join(sorted(shared))[:30].lower())
        if info.doc_date:
            days = abs((td - info.doc_date).days)
            score += max(0.0, 1.0 - days / 60)
        out.append(Candidate(tx, score, ", ".join(why)))
    out.sort(key=lambda c: (-c.score, c.tx.date))
    return out


def best_match(books: Books, info: DocInfo, exclude: set[int] | None = None) -> tuple[Candidate | None, str]:
    """(match, reason if none). Confident = exact total and either unique, or clearly ahead on number/name."""
    if not info.total:
        return None, "No total amount could be read from this document"
    cands = [c for c in candidates(books, info, exclude=exclude) if c.score >= 3]
    if not cands:
        return None, (f"No bank transaction of {fmt_money(info.total)} yet"
                      + (f" around {info.doc_date:%b %d, %Y}" if info.doc_date else ""))
    if len(cands) == 1 or cands[0].score - cands[1].score >= 1.5:
        return cands[0], ""
    return None, (f"{len(cands)} bank transactions of {fmt_money(info.total)} — match it by hand")


# ---------------------------------------------------------------------------
# Filing
# ---------------------------------------------------------------------------


@dataclass
class DumpEvent:
    file: str
    matched: bool
    new_name: str = ""
    transaction: str = ""
    reason: str = ""

    def describe(self) -> str:
        if self.matched and self.reason == "saved":
            return f"Invoice Dump: {self.file} recorded → Saved Invoices/{self.new_name}"
        if self.matched and self.reason == "paid":
            return f"Invoice Dump: future payable {self.file} paid by {self.transaction}"
        if self.matched:
            return f"Invoice Dump: {self.file} → {self.new_name} (matched {self.transaction})"
        return f"Invoice Dump: {self.file} — not matched yet: {self.reason}"


def _recognized_path(root: Path, tx: Transaction, info: DocInfo, original: str) -> Path:
    d = date.fromisoformat(tx.date)
    name = info.party or merchant_key(tx.description).title() or tx.description.title()
    stem = safe_filename(f"{tx.date} {name} {fmt_money(abs(tx.amount_cents))}")
    return root / RECOGNIZED_DIR / str(d.year) / f"{d:%Y-%m}" / f"{stem}{Path(original).suffix.lower()}"


def _saved_path(saved: Path, tx: Transaction, info: DocInfo, original: str) -> Path:
    """Saved Invoices/<year>/<year-month>/<transaction date> <invoice number> <vendor>.<ext>"""
    d = date.fromisoformat(tx.date)
    vendor = info.party or merchant_key(tx.description).title() or tx.description.title()
    if info.number:
        stem = f"{tx.date} {info.number} {vendor}"
    else:
        stem = f"{tx.date} {vendor} {fmt_money(abs(tx.amount_cents))}"
    return saved / str(d.year) / f"{d:%Y-%m}" / f"{safe_filename(stem)}{Path(original).suffix.lower()}"


def _set_attachment_note(books: Books, tx: Transaction, name: str) -> None:
    parts = [p for p in tx.memo.split(" · ") if p and not p.startswith("📎")] if tx.memo else []
    parts.append(f"📎 {name}")
    books.conn.execute("UPDATE transactions SET memo=? WHERE id=?", (" · ".join(parts), tx.id))


def _tx_label(tx: Transaction) -> str:
    return f"{tx.date} {tx.description} {fmt_money(tx.amount_cents, parens=False)}"


def link(books: Books, file_id: int, tx_id: int) -> Path:
    """Match a dump file to a bank transaction (automatically or by hand).

    If the transaction's expense is already recorded (it has a category) the file goes straight to
    Saved Invoices, renamed with the transaction date and invoice number; otherwise to Recognized/
    until it is recorded.
    """
    root = dump_root(books)
    row = books.conn.execute("SELECT * FROM dump_files WHERE id=?", (file_id,)).fetchone()
    tx = books.transaction(tx_id)
    if row["status"] == "payable" and row["document_id"] and tx.amount_cents < 0:
        from . import receivables as rx  # a future payable being paid: settle its bill in Accounts Payable

        try:
            if tx.id not in {t.id for t, _a in rx.payments_for(books, row["document_id"])}:
                rx.record_payment(books, row["document_id"], tx.id)
            tx = books.transaction(tx_id)
        except (ValueError, KeyError):
            pass
    info = DocInfo(row["amount_cents"], [], date.fromisoformat(row["doc_date"]) if row["doc_date"] else None,
                   row["number"], row["party"])
    src = _abs(books, row["rel_path"])
    target = (_saved_path(saved_root(books), tx, info, row["original_name"]) if is_recorded(books, tx)
              else _recognized_path(root, tx, info, row["original_name"]))
    dest = unique_path(target)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), dest)
    with books.conn:
        books.conn.execute("UPDATE dump_files SET status='matched', transaction_id=?, rel_path=?, reason='' "
                           "WHERE id=?", (tx_id, _rel(books, dest), file_id))
        _set_attachment_note(books, tx, dest.name)
    return dest


def save_recorded(books: Books) -> list[tuple[str, Path]]:
    """Move matched invoices whose expense is now recorded from Recognized/ to Saved Invoices/.

    Returns [(original name, new path)]. Runs after every scan, so categorizing a payment in the
    To Categorize panel is enough for its invoice to be saved.
    """
    if dump_root(books) is None:
        return []
    ensure_schema(books)
    moved = []
    for r in books.conn.execute("SELECT * FROM dump_files WHERE status='matched' AND transaction_id IS NOT NULL "
                                "AND rel_path NOT LIKE '@saved/%'").fetchall():
        try:
            tx = books.transaction(r["transaction_id"])
        except KeyError:
            continue
        if not is_recorded(books, tx):
            continue
        src = _abs(books, r["rel_path"])
        if not src.exists():
            continue
        info = DocInfo(r["amount_cents"], [], None, r["number"], r["party"])
        dest = unique_path(_saved_path(saved_root(books), tx, info, r["original_name"]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), dest)
        except OSError:
            continue  # open in another program: try again next time
        with books.conn:
            books.conn.execute("UPDATE dump_files SET rel_path=? WHERE id=?", (_rel(books, dest), r["id"]))
            _set_attachment_note(books, tx, dest.name)
        moved.append((r["original_name"], dest))
    root = dump_root(books)
    for d in sorted((p for p in (root / RECOGNIZED_DIR).rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    return moved


def unlink(books: Books, file_id: int) -> Path:
    """Undo a match, a Future Payable or an Unrelated choice: the file goes back to Not Yet Matched."""
    root = dump_root(books)
    row = books.conn.execute("SELECT * FROM dump_files WHERE id=?", (file_id,)).fetchone()
    if row["status"] == "payable" and row["document_id"]:
        _drop_bill(books, row["document_id"])
    src = _abs(books, row["rel_path"])
    dest = unique_path(root / PENDING_DIR / row["original_name"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), dest)
    with books.conn:
        if row["transaction_id"]:
            tx = books.transaction(row["transaction_id"])
            memo = " · ".join(p for p in tx.memo.split(" · ") if not p.startswith("📎"))
            books.conn.execute("UPDATE transactions SET memo=? WHERE id=?", (memo, tx.id))
        books.conn.execute("UPDATE dump_files SET status='unmatched', transaction_id=NULL, document_id=NULL, "
                           "rel_path=?, reason='Unmatched by hand' WHERE id=?",
                           (dest.relative_to(root).as_posix(), file_id))
    return dest


def _drop_bill(books: Books, doc_id: int) -> None:
    """Remove the Accounts Payable bill created for a Future Payable, unless it has payments already."""
    from . import receivables as rx

    try:
        if not rx.payments_for(books, doc_id):
            rx.delete_document(books, doc_id)
    except KeyError:
        pass


def _payable_path(root: Path, row, vendor: str, due: str) -> Path:
    """Future Payables/<invoice date> <invoice number> <vendor> <amount> (due <date>).<ext>"""
    parts = [row["doc_date"] or "", row["number"], vendor, fmt_money(row["amount_cents"]), f"(due {due})"]
    stem = safe_filename(" ".join(p for p in parts if p))
    return root / PAYABLES_DIR / f"{stem}{Path(row['original_name']).suffix.lower()}"


def mark_payable(books: Books, file_id: int, *, category_id: int | None = None, due_date: str | None = None,
                 due_days: int = 30, vendor: str | None = None, control_id: int | None = None) -> Path:
    """Not paid yet: record the invoice as a bill in Accounts Payable and move it to Future Payables.
    When its payment shows up in a bank statement it is matched and moved to Saved Invoices."""
    from . import chart_of_accounts as coa
    from . import receivables as rx
    from .categorizer import Categorizer

    root = dump_root(books)
    row = books.conn.execute("SELECT * FROM dump_files WHERE id=?", (file_id,)).fetchone()
    if row["status"] == "matched":
        raise ValueError("This invoice is already matched to a bank payment")
    if not row["amount_cents"]:
        raise ValueError("No total could be read from this invoice — use Correct Details first")
    vendor = (vendor or row["party"] or Path(row["original_name"]).stem).strip()
    issued = date.fromisoformat(row["doc_date"]) if row["doc_date"] else date.today()
    due = due_date or (issued + timedelta(days=due_days)).isoformat()
    doc_id = row["document_id"] if row["status"] == "payable" else None
    if doc_id is None and row["number"]:
        hit = books.conn.execute("SELECT id FROM documents WHERE kind='bill' AND number=? AND amount_cents=?",
                                 (row["number"], row["amount_cents"])).fetchone()
        doc_id = hit[0] if hit else None
    if doc_id is None:
        if category_id is None:
            category_id = suggest_category(books, Categorizer(books), vendor, vendor, -row["amount_cents"])
        if category_id is None or books.account(category_id).type not in ("expense", "asset", "liability"):
            category_id = books.role(coa.UNCATEGORIZED_EXPENSE).id
        doc_id = rx.add_document(books, "bill", vendor, issued.isoformat(), due, row["amount_cents"], category_id,
                                 number=row["number"], memo=f"From Invoice Dump: {row['original_name']}",
                                 control_id=control_id or rx.default_control(books, "bill", vendor))
    src = _abs(books, row["rel_path"])
    dest = unique_path(_payable_path(root, row, vendor, rx.get_document(books, doc_id).due_date))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists() and src != dest:
        shutil.move(str(src), dest)
    with books.conn:
        books.conn.execute("UPDATE dump_files SET status='payable', document_id=?, transaction_id=NULL, rel_path=?, "
                           "reason=? WHERE id=?",
                           (doc_id, dest.relative_to(root).as_posix(),
                            f"Bill in Accounts Payable, due {rx.get_document(books, doc_id).due_date} — "
                            "waiting for its payment", file_id))
    return dest


def mark_unrelated(books: Books, file_id: int) -> Path:
    """Not a bill of this company (wrong invoice, personal, sent by mistake): move it to Unrelated,
    keep it out of the books and stop trying to match it."""
    root = dump_root(books)
    row = books.conn.execute("SELECT * FROM dump_files WHERE id=?", (file_id,)).fetchone()
    if row["status"] == "matched":
        raise ValueError("This invoice is matched to a bank payment — unmatch it first")
    if row["status"] == "payable" and row["document_id"]:
        _drop_bill(books, row["document_id"])
    src = _abs(books, row["rel_path"])
    dest = unique_path(root / UNRELATED_DIR / row["original_name"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), dest)
    with books.conn:
        books.conn.execute("UPDATE dump_files SET status='unrelated', document_id=NULL, transaction_id=NULL, "
                           "rel_path=?, reason='Marked as not related' WHERE id=?",
                           (dest.relative_to(root).as_posix(), file_id))
    return dest


def settle_payables(books: Books, linked: set[int] | None = None) -> list[DumpEvent]:
    """Future Payables whose payment has arrived: link the file to that bank payment (→ Saved Invoices)."""
    from . import receivables as rx

    linked = linked if linked is not None else linked_transaction_ids(books)
    events = []
    for row in books.conn.execute("SELECT * FROM dump_files WHERE status='payable'").fetchall():
        tx = None
        if row["document_id"]:
            try:
                pays = rx.payments_for(books, row["document_id"])
            except KeyError:
                pays = []
            tx = next((t for t, _a in pays if t.id not in linked), None)
        if tx is None:
            match, _why = best_match(books, _row_info(row), linked)
            if match and row["document_id"]:
                try:
                    rx.record_payment(books, row["document_id"], match.tx.id)
                except (ValueError, KeyError):
                    match = None
            tx = match.tx if match else None
        if tx is None:
            continue
        dest = link(books, row["id"], tx.id)
        linked.add(tx.id)
        events.append(DumpEvent(row["original_name"], True, dest.name, _tx_label(tx), reason="paid"))
    return events


def _row_info(row) -> DocInfo:
    return DocInfo(row["amount_cents"],
                   [int(a) for a in row["alt_amounts"].split(",") if a],
                   date.fromisoformat(row["doc_date"]) if row["doc_date"] else None,
                   row["number"], row["party"], set(row["words"].split()))


def pending_files(books: Books) -> list[Path]:
    """New files sitting in the dump, not yet read."""
    root = dump_root(books)
    if root is None or not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS
                  and not p.name.startswith((".", "~$")))


def process_dump(books: Books, *, read_new: bool = True, min_age_seconds: float = 5.0,
                 progress=None) -> list[DumpEvent]:
    """Read new files in the dump, match them, and re-check files still waiting. Returns what happened."""
    import time

    root = dump_root(books)
    if root is None:
        return []
    ensure_schema(books)
    ensure_folders(root)
    events: list[DumpEvent] = []
    linked = linked_transaction_ids(books)

    if read_new:
        new_files = pending_files(books)
        for n, f in enumerate(new_files, start=1):
            if progress:
                progress(n, len(new_files), f.name)
            try:
                if min_age_seconds > 0 and time.time() - f.stat().st_mtime < min_age_seconds:
                    continue  # still being copied / synced
                data = f.read_bytes()
            except OSError:
                continue
            digest = hashlib.sha256(data).hexdigest()
            if books.conn.execute("SELECT 1 FROM dump_files WHERE file_hash=?", (digest,)).fetchone():
                f.unlink(missing_ok=True)  # the identical file is already filed
                continue
            try:
                _method, text = read_document(f)
            except Exception:  # noqa: BLE001 - an unreadable file just waits in Not Yet Matched
                text = ""
            info = extract_info(text)
            pending = unique_path(root / PENDING_DIR / f.name)
            shutil.move(str(f), pending)
            with books.conn:
                cur = books.conn.execute(
                    "INSERT INTO dump_files(rel_path, original_name, file_hash, status, doc_date, amount_cents, "
                    "alt_amounts, party, number, words, reason, added_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pending.relative_to(root).as_posix(), f.name, digest, "unmatched",
                     info.doc_date.isoformat() if info.doc_date else None, info.total,
                     ",".join(map(str, info.alternates)), info.party, info.number,
                     " ".join(sorted(info.words))[:4000], "", datetime.now().isoformat(timespec="seconds")))
            dup = None
            if info.number and info.total:
                dup = books.conn.execute(
                    "SELECT original_name, rel_path FROM dump_files WHERE number=? AND amount_cents=? AND id != ?",
                    (info.number, info.total, cur.lastrowid)).fetchone()
            if dup:
                reason = f"Duplicate of {Path(dup['rel_path']).name} (same invoice number and total)"
                with books.conn:
                    books.conn.execute("UPDATE dump_files SET reason=? WHERE id=?", (reason, cur.lastrowid))
                events.append(DumpEvent(f.name, False, reason=reason))
                continue
            match, reason = best_match(books, info, linked)
            if match:
                dest = link(books, cur.lastrowid, match.tx.id)
                linked.add(match.tx.id)
                events.append(DumpEvent(f.name, True, dest.name, _tx_label(match.tx)))
            else:
                with books.conn:
                    books.conn.execute("UPDATE dump_files SET reason=? WHERE id=?", (reason, cur.lastrowid))
                events.append(DumpEvent(f.name, False, reason=reason))

    # Files still waiting: maybe their bank transaction has been imported since
    for row in books.conn.execute("SELECT * FROM dump_files WHERE status='unmatched' AND reason != "
                                  "'Unmatched by hand' AND reason NOT LIKE 'Duplicate of%'").fetchall():
        match, reason = best_match(books, _row_info(row), linked)
        if match:
            dest = link(books, row["id"], match.tx.id)
            linked.add(match.tx.id)
            events.append(DumpEvent(row["original_name"], True, dest.name, _tx_label(match.tx)))
        elif reason != row["reason"]:
            with books.conn:
                books.conn.execute("UPDATE dump_files SET reason=? WHERE id=?", (reason, row["id"]))
    events += settle_payables(books, linked)
    for name, dest in save_recorded(books):
        events.append(DumpEvent(name, True, dest.name, reason="saved"))
    return events


@dataclass
class DumpItem:
    id: int
    path: Path
    original_name: str
    status: str
    transaction: Transaction | None
    doc_date: str | None
    amount_cents: int | None
    party: str
    number: str
    reason: str


def items(books: Books, status: str | None = None) -> list[DumpItem]:
    root = dump_root(books)
    if root is None:
        return []
    ensure_schema(books)
    sql = "SELECT * FROM dump_files"
    args: tuple = ()
    if status:
        sql += " WHERE status=?"
        args = (status,)
    sql += " ORDER BY COALESCE(doc_date, added_at) DESC"
    out = []
    for r in books.conn.execute(sql, args).fetchall():
        tx = None
        if r["transaction_id"]:
            try:
                tx = books.transaction(r["transaction_id"])
            except KeyError:
                tx = None
        out.append(DumpItem(r["id"], _abs(books, r["rel_path"]), r["original_name"], r["status"], tx, r["doc_date"],
                            r["amount_cents"], r["party"], r["number"], r["reason"]))
    return out


def files_for_transaction(books: Books, tx_id: int) -> list[Path]:
    root = dump_root(books)
    if root is None:
        return []
    ensure_schema(books)
    return [_abs(books, r[0]) for r in books.conn.execute(
        "SELECT rel_path FROM dump_files WHERE transaction_id=? AND status='matched'", (tx_id,))]


def set_details(books: Books, file_id: int, *, amount_cents: int | None, doc_date: str | None,
                party: str | None = None) -> None:
    """Correct what was read (e.g. a misread total) so matching can try again."""
    with books.conn:
        books.conn.execute("UPDATE dump_files SET amount_cents=?, doc_date=?, party=COALESCE(?, party), reason='' "
                           "WHERE id=?", (amount_cents, doc_date, party, file_id))


def archive(books: Books, cutoff: date) -> list[tuple[str, str]]:
    """Move matched items dated before ``cutoff`` into an Archive folder (keeping year/month):
    Recognized/… → Invoice Dump/Archive/…, Saved Invoices/… → Saved Invoices/Archive/…"""
    root = dump_root(books)
    if root is None:
        return []
    ensure_schema(books)
    moves = []
    for r in books.conn.execute("SELECT id, rel_path FROM dump_files WHERE status='matched'").fetchall():
        rel_s = r["rel_path"]
        saved = rel_s.startswith(SAVED_PREFIX)
        rel = Path(rel_s[len(SAVED_PREFIX):] if saved else rel_s)
        if saved:
            if rel.parts[0] == ARCHIVE_DIR:
                continue
            dest_rel = Path(ARCHIVE_DIR) / rel
        else:
            if rel.parts[0] != RECOGNIZED_DIR:
                continue
            dest_rel = Path(ARCHIVE_DIR) / Path(*rel.parts[1:])
        m = re.match(r"(\d{4}-\d{2}-\d{2})", rel.name)
        if not m or date.fromisoformat(m.group(1)) >= cutoff:
            continue
        src = _abs(books, rel_s)
        dest = unique_path((saved_root(books) if saved else root) / dest_rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), dest)
        with books.conn:
            books.conn.execute("UPDATE dump_files SET rel_path=? WHERE id=?", (_rel(books, dest), r["id"]))
        moves.append((str(src), str(dest)))
    for base in (root / RECOGNIZED_DIR, saved_root(books)):
        if base.is_dir():
            for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
                if not any(d.iterdir()):
                    d.rmdir()
    return moves


# ---------------------------------------------------------------------------
# "Process All Bills": read everything, record the expenses
# ---------------------------------------------------------------------------


@dataclass
class RunSummary:
    read: int = 0                 # new files read this run
    matched: int = 0              # matched to a bank payment this run
    categorized: int = 0          # bank transactions given an expense category from their bill
    still_uncategorized: int = 0  # matched payments that still need a category from you
    saved: int = 0                # moved to Saved Invoices (matched and recorded)
    bills: list[str] = field(default_factory=list)       # unpaid bills recorded in Accounts Payable
    attention: list[str] = field(default_factory=list)   # need a person (ambiguous, unreadable…)
    events: list = field(default_factory=list)

    def describe(self) -> str:
        parts = [f"{self.read} new file(s) read", f"{self.matched} matched to bank payments"]
        if self.matched:
            parts.append("all with an expense category" if not self.still_uncategorized else
                         f"{self.still_uncategorized} still need a category (To Categorize panel)")
        if self.categorized:
            parts.append(f"{self.categorized} categorized from their bill")
        parts.append(f"{len(self.bills)} unpaid bill(s) recorded")
        if self.saved:
            parts.append(f"{self.saved} saved to Saved Invoices")
        if self.attention:
            parts.append(f"{len(self.attention)} need your attention")
        return " · ".join(parts)


def suggest_category(books: Books, categorizer, party: str, description: str, amount_cents: int) -> int | None:
    """Expense account for a bill: your rules and past choices first, then built-in suggestions."""
    for text in (description, party, f"{party} {description}"):
        text = (text or "").strip()
        if not text:
            continue
        fake = Transaction(0, -1, date.today().isoformat(), text, amount_cents, None, None, False, "",
                           merchant_key(text))
        s = categorizer.suggest(fake)
        if s is not None:
            return s.account_id
    return None


def run_all(books: Books, *, record_unpaid: bool = True, due_days: int = 30, progress=None) -> RunSummary:
    """Read every file in the dump, match it to its bank payment, categorize the expense, and record
    bills that haven't been paid yet in Accounts Payable (their files move to Future Payables)."""
    from .categorizer import Categorizer

    summary = RunSummary()
    if dump_root(books) is None:
        summary.attention.append("Set up the company folder first (File → Company Folder & Auto-Save)")
        return summary
    summary.read = len(pending_files(books))
    ensure_schema(books)
    count_saved = lambda: books.conn.execute(  # noqa: E731
        "SELECT COUNT(*) FROM dump_files WHERE rel_path LIKE '@saved/%'").fetchone()[0]
    saved_before = count_saved()
    events = process_dump(books, min_age_seconds=0, progress=progress)
    summary.events = events
    summary.matched = sum(1 for e in events if e.matched and e.reason != "saved")

    cat = Categorizer(books)
    for it in items(books, "matched"):
        tx = it.transaction
        if tx is None or tx.reviewed or tx.category_source == "manual":
            continue
        if tx.category_id is not None and tx.category_source not in (None, "keyword"):
            continue
        acct = suggest_category(books, cat, it.party, tx.description, tx.amount_cents)
        if acct and acct != tx.category_id and acct != tx.bank_account_id:
            books.set_category(tx.id, acct, source="learned")
            summary.categorized += 1

    save_recorded(books)
    summary.saved = count_saved() - saved_before
    run_tx = {e.transaction for e in events if e.matched and e.reason != "saved"}
    summary.still_uncategorized = sum(
        1 for it in items(books, "matched")
        if it.transaction and _tx_label(it.transaction) in run_tx and it.transaction.category_id is None)

    for it in items(books, "unmatched"):
        why = it.reason or ""
        if not it.amount_cents:
            summary.attention.append(f"{it.original_name}: no total could be read — use Correct Details")
            continue
        if why.startswith("Duplicate of") or why == "Unmatched by hand":
            continue
        if "bank transactions of" in why:
            summary.attention.append(f"{it.original_name}: {why}")
            continue
        if not record_unpaid:
            continue
        vendor = it.party or Path(it.original_name).stem
        try:
            mark_payable(books, it.id, due_days=due_days)
            summary.bills.append(f"{vendor} {fmt_money(it.amount_cents)}")
        except Exception as e:  # noqa: BLE001
            summary.attention.append(f"{it.original_name}: couldn't record the bill ({e})")
    if summary.bills:
        settle_payables(books)  # a payment might already be in the books
    return summary
