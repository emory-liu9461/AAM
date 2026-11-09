"""Cheques & bank drafts: read scans / photos / PDFs saved in a folder and match them to the statement.

Bank statements show cheques and drafts without the payee — ``CHQ#00126-9000000001`` or
``CAD DRAFT 05551234``. Save the cheque or draft (a scan, a phone photo, the bank's cheque
image PDF, or the draft receipt) in the company folder's ``Cheques & Drafts/`` folder, or add it
in **Bookkeeping → Cheques & Drafts…**. Each one is read (PDF text, or offline OCR) for:

* kind — cheque or bank draft / money order,
* number — "No. 126", the MICR line at the bottom, or the file name ("Cheque 126.pdf"),
* payee — "Pay to the order of …",
* amount, date and memo.

It is then matched to the bank line with the same number (``CHQ#00126`` → 126) and amount; without
a number, by amount and date (cheques can take weeks to be cashed). Matched files are renamed and
filed, and the transaction's memo shows ``✎ Cheque #126 to Jane Smith “April rent”``::

    Cheques & Drafts/
        Matched/2024/2024-04/2024-04-15 Cheque 126 Jane Smith $4,982.50.pdf
        Not Yet Matched/scan0007.jpg          ← no bank line yet; checked after every import

The category you used for that payee before is applied, and a cheque / draft for exactly the amount
of an open bill (or invoice) of that payee is recorded as its payment.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .books import Books, Transaction
from .companies import safe_filename
from .etransfer import AMOUNT_RE, _cents, _date_in
from .money import fmt_money
from .pdf_to_excel import unique_path

DIR_NAME = "Cheques & Drafts"
MATCHED_DIR = "Matched"
PENDING_DIR = "Not Yet Matched"
ARCHIVE_DIR = "Archive"
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS cheques (
    id             INTEGER PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('cheque','draft')),
    number         TEXT NOT NULL DEFAULT '',
    date           TEXT,
    payee          TEXT NOT NULL DEFAULT '',
    amount_cents   INTEGER,
    memo           TEXT NOT NULL DEFAULT '',
    rel_path       TEXT NOT NULL,
    original_name  TEXT NOT NULL,
    file_hash      TEXT NOT NULL UNIQUE,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    reason         TEXT NOT NULL DEFAULT '',
    added_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_cheques_tx ON cheques(transaction_id);
"""


def ensure_schema(books: Books) -> None:
    if books.first_time("schema:cheques"):
        books.conn.executescript(SCHEMA)


def folder(books: Books) -> Path | None:
    from . import workspace as ws

    cfg = ws.get_config(books)
    return cfg.dir("cheques") if cfg.enabled else None


def ensure_folders(root: Path) -> None:
    for sub in ("", MATCHED_DIR, PENDING_DIR):
        (root / sub).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Bank lines that are cheques / drafts
# ---------------------------------------------------------------------------

CHEQUE_LINE = re.compile(r"\bCHQ|\bCHEQUE|\bCHECK\b|\bCHK\b|\bCK\s*#", re.I)
DRAFT_LINE = re.compile(r"\bDRAFT\b|\bMONEY ORDER\b|\bBANK ORDER\b", re.I)


def line_kind(description: str) -> str | None:
    if re.search(r"\bFEE\b|IMAGE", description, re.I):
        return None
    if DRAFT_LINE.search(description):
        return "draft"
    if CHEQUE_LINE.search(description):
        return "cheque"
    return None


def line_number(description: str) -> str:
    """'CHQ#00126-9000000001' -> '126'; 'CAD DRAFT 05551234' -> '5551234'."""
    m = re.search(r"(?:CHQ|CHEQUE|CHECK|CHK|CK|DRAFT|ORDER)\s*(?:NO\.?|#)?\s*0*(\d{1,10})", description, re.I)
    return m.group(1) if m else ""


def _norm(number: str) -> str:
    digits = re.sub(r"\D", "", number)
    return digits.lstrip("0") or digits


# ---------------------------------------------------------------------------
# Reading a cheque / draft
# ---------------------------------------------------------------------------


@dataclass
class ChequeInfo:
    kind: str = "cheque"
    number: str = ""
    date: str | None = None
    payee: str = ""
    amount_cents: int | None = None
    memo: str = ""

    @property
    def label(self) -> str:
        noun = "Cheque" if self.kind == "cheque" else "Draft"
        bits = [f"{noun} #{self.number}" if self.number else noun]
        if self.payee:
            bits.append(f"to {self.payee}")
        return " ".join(bits)


PAYEE_RE = re.compile(r"pay\s*(?:to\s*the\s*order\s*of|to)?\s*[:\-]?\s*(.*)", re.I)
STOP = re.compile(r"\b(the sum of|dollars|cad|\$|order of|pay|amount|memo|date|signature|authori[sz]ed|"
                  r"per|void|non-negotiable)\b", re.I)


def _payee_from(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines()]
    for i, ln in enumerate(lines):
        m = re.search(r"pay\s*to\s*the\s*order\s*of|payee\s*:|^pay\b|\bpay\s*to\b", ln, re.I)
        if not m:
            continue
        rest = ln[m.end():]
        rest = AMOUNT_RE.sub(" ", rest)
        rest = re.split(r"\$|\*{2,}|\d", rest)[0]
        rest = re.sub(r"^[\s:\-–]+|[\s:\-–*]+$", "", rest)
        if not re.search(r"[A-Za-z]{2,}", rest) and i + 1 < len(lines):
            rest = re.split(r"\$|\*{2,}|\d", AMOUNT_RE.sub(" ", lines[i + 1]))[0].strip(" :-–*")
        rest = re.sub(r"\b(order of|the)\b", " ", rest, flags=re.I)
        rest = re.sub(r"\s+", " ", rest).strip()
        if re.search(r"[A-Za-z]{2,}", rest) and not re.search(r"\bdollars?\b|\bthousand\b|\bhundred\b", rest, re.I):
            return rest[:60]
    return ""


def _number_from(text: str, filename: str, kind: str) -> str:
    # "No. 000126", "Cheque # 126", "Draft No. 05551234", "Serial 0792 3345"
    label = r"(?:draft|cheque|check|chq|serial|money\s*order)\s*(?:no\.?|number|#)?|\bno\.|#"
    for m in re.finditer(rf"(?:{label})\s*:?\s*([0-9][0-9 ]{{1,12}}[0-9]|[0-9]{{1,10}})", text, re.I):
        n = _norm(m.group(1))
        if n and len(n) <= 10:
            return n
    # MICR line: cheque number in transit symbols, e.g. "⑈000126⑈ ⑆12345⑉004⑆ 1234567⑈" (OCR reads the symbols
    # as letters / punctuation); take the first 3–6 digit group on a line that's mostly digits
    for ln in text.splitlines()[::-1]:
        digits = re.findall(r"\d+", ln)
        if len(digits) >= 2 and sum(map(len, digits)) >= 12 and len(re.findall(r"[A-Za-z]{3,}", ln)) <= 1:
            first = digits[0]
            if 3 <= len(first) <= 8:
                return _norm(first)
    m = re.search(r"(?:chq|cheque|check|draft|ck)[\s_#-]*0*(\d{1,10})", Path(filename).stem, re.I)
    if m:
        return m.group(1)
    if kind == "cheque":  # a lone 3–6 digit number near the top right is the cheque number
        for ln in text.splitlines()[:4]:
            m = re.search(r"(?<![\d,.$])\b(\d{3,6})\b\s*$", ln.strip())
            if m and not _date_in(ln)[0]:
                return _norm(m.group(1))
    return ""


def _date_from(text: str) -> str | None:
    d, _ = _date_in(text)
    if d and not d.startswith("????"):
        return d
    # Cheque date boxes: 8 digits after "DATE", spaced or not — YYYYMMDD, DDMMYYYY or MMDDYYYY
    m = re.search(r"date[^\d]{0,12}((?:\d\s*){8})", text, re.I)
    if m:
        g = re.sub(r"\D", "", m.group(1))
        for y, mo, dy in ((g[:4], g[4:6], g[6:]), (g[4:], g[2:4], g[:2]), (g[4:], g[:2], g[2:4])):
            try:
                if 1990 <= int(y) <= 2100:
                    return date(int(y), int(mo), int(dy)).isoformat()
            except ValueError:
                continue
    return d


def _amount_from(text: str) -> int | None:
    best = None
    for ln in text.splitlines():
        if re.search(r"\bfee\b|charge", ln, re.I):
            continue
        for tok in AMOUNT_RE.findall(ln):
            c = _cents(tok)
            weight = 2 if ("$" in tok or "**" in ln or re.search(r"amount|sum|cad", ln, re.I)) else 1
            if c and (best is None or (weight, c) > best):
                best = (weight, c)
    return best[1] if best else None


def parse_text(text: str, filename: str = "") -> ChequeInfo:
    kind = "draft" if re.search(r"\bdraft\b|money\s*order|bank\s*order", text + " " + filename, re.I) else "cheque"
    memo = ""
    m = re.search(r"(?:memo|re|for)\s*[:\-]\s*(.+)", text, re.I)
    if m:
        memo = re.split(r"\s{2,}|\$", m.group(1))[0].strip()[:80]
    return ChequeInfo(kind, _number_from(text, filename, kind), _date_from(text), _payee_from(text),
                      _amount_from(text), memo)


def read_file(path: Path) -> ChequeInfo:
    from .invoice_dump import read_document

    try:
        _method, text = read_document(path)
    except Exception:  # noqa: BLE001 - unreadable: the user can correct the details
        text = ""
    return parse_text(text, path.name)


# ---------------------------------------------------------------------------
# Records, matching, filing
# ---------------------------------------------------------------------------


@dataclass
class Item:
    id: int
    info: ChequeInfo
    path: Path
    original_name: str
    transaction_id: int | None
    reason: str


def _abs(books: Books, rel: str) -> Path:
    return folder(books) / rel


def items(books: Books, status: str | None = None) -> list[Item]:
    root = folder(books)
    if root is None:
        return []
    ensure_schema(books)
    sql = "SELECT * FROM cheques"
    if status == "matched":
        sql += " WHERE transaction_id IS NOT NULL"
    elif status == "unmatched":
        sql += " WHERE transaction_id IS NULL"
    sql += " ORDER BY COALESCE(date, added_at) DESC, id DESC"
    return [Item(r["id"], ChequeInfo(r["kind"], r["number"], r["date"], r["payee"], r["amount_cents"], r["memo"]),
                 root / r["rel_path"], r["original_name"], r["transaction_id"], r["reason"])
            for r in books.conn.execute(sql)]


def _info(row) -> ChequeInfo:
    return ChequeInfo(row["kind"], row["number"], row["date"], row["payee"], row["amount_cents"], row["memo"])


def linked_ids(books: Books) -> set[int]:
    ensure_schema(books)
    return {r[0] for r in books.conn.execute("SELECT transaction_id FROM cheques WHERE transaction_id IS NOT NULL")}


@dataclass
class Candidate:
    tx: Transaction
    score: int
    why: str


def candidates(books: Books, info: ChequeInfo, exclude: set[int] | None = None,
               max_days: int = 90) -> list[Candidate]:
    """Bank cheque / draft lines this could be, best first."""
    if not info.amount_cents and not info.number:
        return []
    exclude = exclude or set()
    if info.amount_cents:
        rows = books.conn.execute("SELECT id FROM transactions WHERE ABS(amount_cents)=?",
                                  (info.amount_cents,)).fetchall()
    else:
        rows = books.conn.execute("SELECT id FROM transactions").fetchall()
    out = []
    for (tx_id,) in rows:
        if tx_id in exclude:
            continue
        tx = books.transaction(tx_id)
        kind = line_kind(tx.description)
        if kind is None:
            continue
        score, why = 0, []
        num = line_number(tx.description)
        if info.number and num:
            if _norm(info.number) == _norm(num):
                score += 100
                why.append(f"{'cheque' if kind == 'cheque' else 'draft'} #{num}")
            elif not info.amount_cents or len(_norm(num)) >= 3:
                continue  # different number: a different cheque
        if not info.amount_cents and score < 100:
            continue
        if info.amount_cents:
            score += 20
            why.append("same amount")
        if kind == info.kind:
            score += 5
        if tx.amount_cents < 0:
            score += 3  # usually cheques you wrote / drafts you bought
        if info.date:
            gap = (date.fromisoformat(tx.date) - date.fromisoformat(info.date)).days
            if score < 100 and not (-5 <= gap <= max_days):
                continue
            score += max(0, 10 - abs(gap) // 7)
            why.append("same day" if gap == 0 else f"cashed {gap} day(s) later" if gap > 0 else f"{-gap} day(s) "
                                                                                             f"earlier")
        out.append(Candidate(tx, score, ", ".join(why)))
    out.sort(key=lambda c: (-c.score, c.tx.date))
    return out


def best_match(books: Books, info: ChequeInfo, exclude: set[int]) -> tuple[Candidate | None, str]:
    cands = candidates(books, info, exclude)
    if not info.amount_cents:
        return (cands[0], "") if cands and cands[0].score >= 100 else (None, "No amount could be read — use "
                                                                             "Correct Details")
    if not cands:
        what = f"{fmt_money(info.amount_cents)}" + (f", #{info.number}" if info.number else "")
        return None, f"No cheque / draft line of {what} in the statements yet"
    if len(cands) > 1 and cands[0].score < 100 and cands[1].score >= cands[0].score - 3:
        return None, f"{len(cands)} cheque / draft lines of {fmt_money(info.amount_cents)} — match by hand"
    return cands[0], ""


def _matched_path(root: Path, tx: Transaction, info: ChequeInfo, original: str) -> Path:
    d = date.fromisoformat(tx.date)
    noun = "Cheque" if info.kind == "cheque" else "Draft"
    num = info.number or line_number(tx.description)
    parts = [tx.date, f"{noun} {num}".strip(), info.payee, fmt_money(abs(tx.amount_cents))]
    stem = safe_filename(" ".join(p for p in parts if p))
    return root / MATCHED_DIR / str(d.year) / f"{d:%Y-%m}" / f"{stem}{Path(original).suffix.lower()}"


def note_for(info: ChequeInfo) -> str:
    return f"✎ {info.label}" + (f" “{info.memo}”" if info.memo else "")


def _set_memo(books: Books, tx: Transaction, note: str | None) -> None:
    parts = [p for p in tx.memo.split(" · ") if p and not p.startswith("✎")]
    if note:
        parts.append(note)
    books.conn.execute("UPDATE transactions SET memo=? WHERE id=?", (" · ".join(parts), tx.id))


def link(books: Books, item_id: int, tx_id: int) -> Path:
    """Match a cheque / draft to its bank line: rename and file it, note the payee, apply the category."""
    root = folder(books)
    row = books.conn.execute("SELECT * FROM cheques WHERE id=?", (item_id,)).fetchone()
    info = _info(row)
    tx = books.transaction(tx_id)
    if not info.number:
        info.number = line_number(tx.description)
    if not info.amount_cents:
        info.amount_cents = abs(tx.amount_cents)
    src = root / row["rel_path"]
    dest = unique_path(_matched_path(root, tx, info, row["original_name"]))
    if src.exists() and src != dest:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)
    with books.conn:
        books.conn.execute("UPDATE cheques SET transaction_id=NULL WHERE transaction_id=? AND id != ?",
                           (tx_id, item_id))
        books.conn.execute("UPDATE cheques SET transaction_id=?, rel_path=?, number=?, amount_cents=?, reason='' "
                           "WHERE id=?", (tx_id, dest.relative_to(root).as_posix(), info.number,
                                          info.amount_cents, item_id))
        _set_memo(books, tx, note_for(info))
    _apply_category(books, info, books.transaction(tx_id))
    return dest


def _apply_category(books: Books, info: ChequeInfo, tx: Transaction) -> None:
    from . import receivables as rx

    if not info.payee or tx.reviewed:
        return
    if books._has_table("documents"):  # pays an open bill / invoice of this payee for exactly this amount
        kind = "bill" if tx.amount_cents < 0 else "invoice"
        name = info.payee.lower()
        for doc in rx.documents(books, kind, as_of=date(9999, 12, 31)):
            p = doc.party.lower()
            if doc.balance_cents == abs(tx.amount_cents) and (p in name or name in p):
                try:
                    rx.record_payment(books, doc.id, tx.id)
                    return
                except ValueError:
                    continue
    if tx.category_id is not None and tx.category_source not in (None, "keyword"):
        return
    acct = category_for_payee(books, info.payee, exclude_tx=tx.id)
    if acct and acct != tx.bank_account_id:
        books.set_category(tx.id, acct, source="learned")


def category_for_payee(books: Books, payee: str, exclude_tx: int | None = None) -> int | None:
    from . import chart_of_accounts as coa

    rows = books.conn.execute(
        "SELECT t.category_id, COUNT(*) n FROM cheques c JOIN transactions t ON t.id=c.transaction_id "
        "WHERE LOWER(c.payee)=LOWER(?) AND t.category_id IS NOT NULL AND t.id != ? "
        "AND (t.category_source='manual' OR t.reviewed=1) GROUP BY t.category_id ORDER BY n DESC",
        (payee, exclude_tx or 0)).fetchall()
    for acct_id, _n in rows:
        try:
            if books.account(acct_id).key not in (coa.UNCATEGORIZED_EXPENSE, coa.UNCATEGORIZED_INCOME):
                return acct_id
        except KeyError:
            continue
    return None


def unlink(books: Books, item_id: int) -> Path:
    """Undo a match: back to Not Yet Matched under its original name."""
    root = folder(books)
    row = books.conn.execute("SELECT * FROM cheques WHERE id=?", (item_id,)).fetchone()
    src = root / row["rel_path"]
    dest = unique_path(root / PENDING_DIR / row["original_name"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), dest)
    with books.conn:
        if row["transaction_id"]:
            try:
                _set_memo(books, books.transaction(row["transaction_id"]), None)
            except KeyError:
                pass
        books.conn.execute("UPDATE cheques SET transaction_id=NULL, rel_path=?, reason='Unmatched by hand' "
                           "WHERE id=?", (dest.relative_to(root).as_posix(), item_id))
    return dest


def set_details(books: Books, item_id: int, *, kind: str, number: str, date_: str | None, payee: str,
                amount_cents: int | None, memo: str = "") -> None:
    with books.conn:
        books.conn.execute("UPDATE cheques SET kind=?, number=?, date=?, payee=?, amount_cents=?, memo=?, reason='' "
                           "WHERE id=?", (kind, _norm(number) if number else "", date_, payee.strip(),
                                         amount_cents, memo.strip(), item_id))
    row = books.conn.execute("SELECT * FROM cheques WHERE id=?", (item_id,)).fetchone()
    if row and row["transaction_id"]:
        link(books, item_id, row["transaction_id"])  # refresh the name and memo
    elif row:
        match, reason = best_match(books, _info(row), linked_ids(books))
        if match:
            link(books, item_id, match.tx.id)
        else:
            with books.conn:
                books.conn.execute("UPDATE cheques SET reason=? WHERE id=?", (reason, item_id))


def files_for_transaction(books: Books, tx_id: int) -> list[Path]:
    root = folder(books)
    if root is None or not books._has_table("cheques"):
        return []
    return [root / r[0] for r in books.conn.execute("SELECT rel_path FROM cheques WHERE transaction_id=?", (tx_id,))]


@dataclass
class Event:
    file: str
    matched: bool
    new_name: str = ""
    transaction: str = ""
    reason: str = ""

    def describe(self) -> str:
        if self.matched:
            return f"Cheques & Drafts: {self.file} → {self.new_name} (matched {self.transaction})"
        return f"Cheques & Drafts: {self.file} — not matched yet: {self.reason}"


def add_file(books: Books, source: Path, *, move: bool = False) -> int | None:
    """Add a cheque / draft file from anywhere (copied into the folder). Returns its id, or None if known."""
    root = folder(books)
    if root is None:
        raise ValueError("Set up the company folder first (File → Company Folder & Auto-Save)")
    ensure_folders(root)
    dest = unique_path(root / source.name)
    (shutil.move if move else shutil.copy2)(str(source), dest)
    events = process_folder(books, min_age_seconds=0)
    return len(events)


def process_folder(books: Books, *, min_age_seconds: float = 5.0, progress=None) -> list[Event]:
    """Read new files dropped in Cheques & Drafts/, match them, and re-check ones still waiting."""
    import time

    root = folder(books)
    if root is None:
        return []
    ensure_schema(books)
    ensure_folders(root)
    events: list[Event] = []
    used = linked_ids(books)
    new = sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS
                 and not p.name.startswith((".", "~$")))
    for n, f in enumerate(new, start=1):
        if progress:
            progress(n, len(new), f.name)
        try:
            if min_age_seconds > 0 and time.time() - f.stat().st_mtime < min_age_seconds:
                continue
            data = f.read_bytes()
        except OSError:
            continue
        digest = hashlib.sha256(data).hexdigest()
        if books.conn.execute("SELECT 1 FROM cheques WHERE file_hash=?", (digest,)).fetchone():
            f.unlink(missing_ok=True)  # the identical file is already filed
            continue
        info = read_file(f)
        pending = unique_path(root / PENDING_DIR / f.name)
        shutil.move(str(f), pending)
        with books.conn:
            cur = books.conn.execute(
                "INSERT INTO cheques(kind, number, date, payee, amount_cents, memo, rel_path, original_name, "
                "file_hash, added_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (info.kind, info.number, info.date, info.payee, info.amount_cents, info.memo,
                 pending.relative_to(root).as_posix(), f.name, digest, datetime.now().isoformat(timespec="seconds")))
        match, reason = best_match(books, info, used)
        if match:
            dest = link(books, cur.lastrowid, match.tx.id)
            used.add(match.tx.id)
            events.append(Event(f.name, True, dest.name, f"{match.tx.date} {match.tx.description}"))
        else:
            with books.conn:
                books.conn.execute("UPDATE cheques SET reason=? WHERE id=?", (reason, cur.lastrowid))
            events.append(Event(f.name, False, reason=reason))
    for row in books.conn.execute("SELECT * FROM cheques WHERE transaction_id IS NULL AND reason != "
                                  "'Unmatched by hand'").fetchall():
        match, reason = best_match(books, _info(row), used)
        if match:
            dest = link(books, row["id"], match.tx.id)
            used.add(match.tx.id)
            events.append(Event(row["original_name"], True, dest.name, f"{match.tx.date} {match.tx.description}"))
        elif reason != row["reason"]:
            with books.conn:
                books.conn.execute("UPDATE cheques SET reason=? WHERE id=?", (reason, row["id"]))
    return events


def archive(books: Books, cutoff: date) -> list[tuple[str, str]]:
    """Matched cheques dated before ``cutoff`` move to Cheques & Drafts/Archive/<year>/<month>/."""
    root = folder(books)
    if root is None or not books._has_table("cheques"):
        return []
    moves = []
    for r in books.conn.execute("SELECT id, rel_path FROM cheques WHERE transaction_id IS NOT NULL").fetchall():
        rel = Path(r["rel_path"])
        if rel.parts[0] != MATCHED_DIR:
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})", rel.name)
        if not m or date.fromisoformat(m.group(1)) >= cutoff:
            continue
        src = root / rel
        dest = unique_path(root / ARCHIVE_DIR / Path(*rel.parts[1:]))
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.exists():
            shutil.move(str(src), dest)
        with books.conn:
            books.conn.execute("UPDATE cheques SET rel_path=? WHERE id=?", (dest.relative_to(root).as_posix(), r["id"]))
        moves.append((str(src), str(dest)))
    return moves
