"""Interac e-Transfer history: put names and messages on "SEND E-TFR ***k9q" / "E-TRANSFER ***r2T" lines.

Bank statements show e-Transfers without the other person's name — only the
last characters of the transfer's reference number (``***k9q``). The online
banking e-Transfer history has the rest: date, who it was sent to or received
from, amount, reference number and message. Give the app that history as

* a **picture** (screenshot or phone photo) — read with offline OCR,
* a **PDF** (printed / saved history page),
* a **CSV / Excel** export, or
* **pasted text** (select the history in online banking and copy),

or drop the files into the company folder's ``E-Transfer History/`` folder.

Each record is matched to its bank transaction by the reference-number ending
(``k9q``), the amount, the direction (sent / received) and the date. A matched
transaction's memo shows ``e-Transfer to Jane Smith · April rent``, and if you
have categorized e-Transfers to that person before, the same category is used.
Records whose transaction isn't imported yet are matched after the next import.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .books import Books, Transaction
from .money import fmt_money

DIR_NAME = "E-Transfer History"
READ_DIR = "Read"
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".csv", ".txt", ".xlsx"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS etransfers (
    id             INTEGER PRIMARY KEY,
    direction      TEXT NOT NULL CHECK (direction IN ('sent','received','unknown')),
    date           TEXT,               -- ISO date, or '????-MM-DD' when the history didn't show the year
    name           TEXT NOT NULL DEFAULT '',
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    reference      TEXT NOT NULL DEFAULT '',
    message        TEXT NOT NULL DEFAULT '',
    source         TEXT NOT NULL DEFAULT '',
    fingerprint    TEXT NOT NULL UNIQUE,
    transaction_id INTEGER REFERENCES transactions(id) ON DELETE SET NULL,
    added_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_etransfer_tx ON etransfers(transaction_id);
"""


def ensure_schema(books: Books) -> None:
    if books.first_time("schema:etransfer"):
        books.conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Which bank transactions are e-Transfers
# ---------------------------------------------------------------------------

# SEND E-TFR, E-TFR, E-TRANSFER, ETFR, E TFR, SENDE-TFR, E‑TFR (other dashes), INTERAC, EMT, AUTODEPOSIT…
ETRANSFER_RE = re.compile(
    r"(?:^|[^A-Z]|SEND|RCV|REC|RECV)E\s*[-\u2010-\u2015\u2212]?\s*(?:TRANSFER|TFR|TRF|TRSF|XFER)\b"
    r"|\bINTERAC\b|\bEMT\b|\bAUTO\s*-?\s*DEPOSIT|\bE[-\s]?MONEY TRANSFER\b", re.I)
SUFFIX_RE = re.compile(r"\*{2,}\s*([A-Za-z0-9]{2,5})\b")


def is_etransfer(description: str) -> bool:
    return bool(ETRANSFER_RE.search(description)) and not re.search(r"\bFEE\b", description, re.I)


def ref_suffix(description: str) -> str:
    """'SEND E-TFR ***k9q' -> 'k9q'."""
    m = SUFFIX_RE.search(description)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Reading history
# ---------------------------------------------------------------------------


@dataclass
class Record:
    direction: str          # sent | received | unknown
    date: str | None        # ISO, or '????-MM-DD'
    name: str
    amount_cents: int
    reference: str = ""
    message: str = ""
    id: int | None = None
    transaction_id: int | None = None
    source: str = ""

    @property
    def label(self) -> str:
        who = {"sent": f"to {self.name}", "received": f"from {self.name}"}.get(self.direction, self.name)
        return f"e-Transfer {who}".strip() if self.name else "e-Transfer"


MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], start=1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_PATTERNS = [
    (re.compile(r"\b(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\b"), "ymd"),
    (re.compile(rf"\b{_MON}\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.I), "mdy_text"),
    (re.compile(rf"\b(\d{{1,2}})\s+{_MON},?\s+(\d{{4}})\b", re.I), "dmy_text"),
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "mdy"),
    (re.compile(rf"\b{_MON}\s+(\d{{1,2}})\b(?!,?\s*\d{{4}})", re.I), "md_text"),
    (re.compile(rf"\b(\d{{1,2}})\s+{_MON}\b(?!,?\s*\d{{4}})", re.I), "dm_text"),
]
AMOUNT_RE = re.compile(r"(?<![\w.])-?\$?\s?\d{1,3}(?:,\d{3})*\.\d{2}(?!\d)|(?<![\w.])-?\$\s?\d+(?:\.\d{2})?(?!\d)")
REF_RE = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,14}\b")
SENT_WORDS = re.compile(r"\b(sent|send|to|paid|transfer to|outgoing)\b", re.I)
RECEIVED_WORDS = re.compile(r"\b(received|receive|from|deposited by|incoming|autodeposit(?:ed)? from)\b", re.I)
SKIP_STATUS = re.compile(r"\b(cancell?ed|declined|expired|failed|reclaimed)\b", re.I)
NOISE = re.compile(
    r"\b(interac|e-?transfers?|e-?tfr|sent|send|received|receive|to|from|deposited|completed|complete|accepted|"
    r"status|amount|date|reference|ref|number|no|message|memo|pending|fee|autodeposit(?:ed)?|request(?:ed)?|"
    r"money|cad|history|details|recipient|sender|contact|transaction|type|paid|available|outgoing|incoming|"
    r"view|total|today|yesterday|e-?mail|mobile|phone|sms|text|method|via|notification|deposit(?:ed)?|"
    r"payment|direct)\b\.?#?:?", re.I)


def _date_in(text: str) -> tuple[str | None, tuple[int, int] | None]:
    """First date in a line -> (iso or '????-MM-DD', span)."""
    for rx, kind in DATE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        g = m.groups()
        try:
            if kind == "ymd":
                d = date(int(g[0]), int(g[1]), int(g[2]))
            elif kind == "mdy":
                d = date(int(g[2]), int(g[0]), int(g[1]))
            elif kind == "mdy_text":
                d = date(int(g[2]), MONTHS[g[0][:3].lower()], int(g[1]))
            elif kind == "dmy_text":
                d = date(int(g[2]), MONTHS[g[1][:3].lower()], int(g[0]))
            elif kind == "md_text":
                mo, dy = MONTHS[g[0][:3].lower()], int(g[1])
                date(2000, mo, dy)
                return f"????-{mo:02d}-{dy:02d}", m.span()
            else:
                mo, dy = MONTHS[g[1][:3].lower()], int(g[0])
                date(2000, mo, dy)
                return f"????-{mo:02d}-{dy:02d}", m.span()
            return d.isoformat(), m.span()
        except (ValueError, KeyError):
            continue
    return None, None


def _cents(token: str) -> int:
    t = token.replace("$", "").replace(",", "").replace(" ", "")
    return round(abs(float(t)) * 100)


def _clean_name(text: str) -> str:
    t = NOISE.sub(" ", text)
    t = re.sub(r"[|:•·*#$/\\]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip(" -,.;")
    words = [w for w in t.split() if re.search(r"[A-Za-z]", w)]
    return " ".join(words[:5])[:60]


def parse_text(text: str, source: str = "") -> list[Record]:
    """Records from e-Transfer history text (copied from online banking, OCR of a screenshot, a printed PDF).

    Works line by line: a record is the lines around one amount — a date, a name, a reference number and a
    message nearby — whether the history is a table (one row per transfer) or cards (several lines each).
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    records: list[Record] = []
    cur: dict = {}

    def flush() -> None:
        nonlocal cur
        if cur.get("amount") and not cur.get("skip"):
            direction = cur.get("direction") or "unknown"
            name = cur.get("name") or _clean_name(" ".join(cur.get("name_bits", [])))
            records.append(Record(direction, cur.get("date"), name, cur["amount"], cur.get("ref", ""),
                                  cur.get("message", ""), source=source))
        cur = {}

    for raw in lines:
        line = raw
        hw = r"(date|amount|status|reference(\s*(number|no\.?|#))?|recipient|sender|name|message|details|contact|to|from|type)"
        if re.fullmatch(rf"{hw}(\s*[|,\t ]\s*{hw})*", line, re.I):
            continue  # a table header
        low = line.lower()
        amounts = [] if re.search(r"\bfee\b", low) else AMOUNT_RE.findall(line)
        d, span = _date_in(line)
        starts_card = re.match(r"(sent to|send to|received from|deposited by|money (sent|received))\b", line, re.I)
        if starts_card and cur.get("amount"):
            flush()
        elif amounts and cur.get("amount"):
            flush()
        elif d and cur.get("date") and cur.get("amount"):
            flush()
        cur.setdefault("text", []).append(line)
        if SKIP_STATUS.search(line):
            cur["skip"] = True
        msg = re.match(r"(?:message|memo)\s*[:\-]\s*(.+)", line, re.I)
        if msg:
            cur["message"] = msg.group(1).strip()[:120]
            continue
        if d and not cur.get("date"):
            cur["date"] = d
        if amounts and not cur.get("amount"):
            cur["amount"] = _cents(amounts[0])
            if amounts[0].strip().startswith("-"):
                cur.setdefault("direction", "sent")
        ref = None
        for m in REF_RE.finditer(line):
            tok = m.group(0)
            if not re.fullmatch(r"\d+", tok) and not _date_in(tok)[0]:
                ref = tok
        if ref and not cur.get("ref"):
            cur["ref"] = ref
        m_to = re.search(r"\b(?:sent to|send to|to)\s*:?\s+([A-Z][\w'.&-]*(?:\s+[A-Z][\w'.&-]*){0,4})", line)
        m_from = re.search(r"\b(?:received from|from|deposited by)\s*:?\s+([A-Z][\w'.&-]*(?:\s+[A-Z][\w'.&-]*){0,4})",
                           line)
        if m_to and not cur.get("direction"):
            cur["direction"], cur["name"] = "sent", _clean_name(m_to.group(1))
        elif m_from and not cur.get("direction"):
            cur["direction"], cur["name"] = "received", _clean_name(m_from.group(1))
        elif not cur.get("direction"):
            if re.search(r"\b(sent|send|outgoing)\b", line, re.I):
                cur["direction"] = "sent"
            elif re.search(r"\b(received|incoming|autodeposit)", line, re.I):
                cur["direction"] = "received"
        # what's left of the line (no date, amounts, reference) may be the name
        rest = line
        if span:
            rest = rest[:span[0]] + " " + rest[span[1]:]
        for a in amounts:
            rest = rest.replace(a, " ")
        if ref:
            rest = rest.replace(ref, " ")
        if _clean_name(rest):
            cur.setdefault("name_bits", []).append(rest)
    flush()
    return [r for r in records if r.amount_cents > 0]


def _header_index(header: list[str], *names: str) -> int | None:
    for i, h in enumerate(header):
        h = h.strip().lower()
        if any(n in h for n in names):
            return i
    return None


def parse_table(rows: list[list[str]], source: str = "") -> list[Record]:
    """Records from a CSV / Excel export with a header row; falls back to the text reader."""
    rows = [[str(c or "").strip() for c in r] for r in rows if any(str(c or "").strip() for c in r)]
    if not rows:
        return []
    header = rows[0]
    ia = _header_index(header, "amount")
    idate = _header_index(header, "date")
    if ia is None or idate is None:
        return parse_text("\n".join("  ".join(r) for r in rows), source)
    iname = _header_index(header, "recipient", "sender", "name", "contact", "to/from", "payee")
    iref = _header_index(header, "reference", "ref")
    imsg = _header_index(header, "message", "memo", "note")
    istatus = _header_index(header, "status")
    itype = _header_index(header, "type", "direction")
    out = []
    for r in rows[1:]:
        get = lambda i: r[i] if i is not None and i < len(r) else ""  # noqa: E731
        amounts = AMOUNT_RE.findall(get(ia)) or re.findall(r"-?\d+(?:\.\d+)?", get(ia))
        if not amounts or SKIP_STATUS.search(get(istatus)):
            continue
        cents = _cents(amounts[0])
        if not cents:
            continue
        kind = (get(itype) + " " + get(istatus)).lower()
        direction = ("sent" if re.search(r"sent|send|out|debit", kind) or get(ia).strip().startswith("-") else
                     "received" if re.search(r"receiv|in\b|deposit|credit", kind) else "unknown")
        d, _ = _date_in(get(idate))
        out.append(Record(direction, d, _clean_name(get(iname)) if iname is not None else "", cents,
                          get(iref), get(imsg)[:120], source=source))
    return out


def read_file(path: Path) -> list[Record]:
    ext = path.suffix.lower()
    if ext == ".csv":
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        return parse_table(list(csv.reader(io.StringIO(text))), path.name)
    if ext == ".xlsx":
        from openpyxl import load_workbook

        ws = load_workbook(path, read_only=True, data_only=True).active
        return parse_table([list(r) for r in ws.iter_rows(values_only=True)], path.name)
    if ext == ".txt":
        return parse_text(path.read_text(encoding="utf-8", errors="replace"), path.name)
    from .invoice_dump import read_document

    _method, text = read_document(path)
    return parse_text(text, path.name)


# ---------------------------------------------------------------------------
# Storing and matching
# ---------------------------------------------------------------------------


def _fingerprint(r: Record) -> str:
    key = f"{r.direction}|{r.date}|{r.amount_cents}|{r.reference or r.name.lower()}"
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def add_records(books: Books, records: list[Record]) -> tuple[int, int]:
    """Save new records (the same transfer read twice is kept once). Returns (added, already_known)."""
    ensure_schema(books)
    added = known = 0
    now = datetime.now().isoformat(timespec="seconds")
    with books.conn:
        for r in records:
            cur = books.conn.execute(
                "INSERT OR IGNORE INTO etransfers(direction, date, name, amount_cents, reference, message, source, "
                "fingerprint, added_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (r.direction, r.date, r.name, r.amount_cents, r.reference, r.message, r.source, _fingerprint(r), now))
            if cur.rowcount:
                added += 1
            else:
                known += 1
    return added, known


def records(books: Books, *, status: str | None = None) -> list[Record]:
    ensure_schema(books)
    sql = "SELECT * FROM etransfers"
    if status == "matched":
        sql += " WHERE transaction_id IS NOT NULL"
    elif status == "unmatched":
        sql += " WHERE transaction_id IS NULL"
    sql += " ORDER BY COALESCE(REPLACE(date,'????','9999'), added_at) DESC, id"
    return [Record(r["direction"], r["date"], _clean_name(r["name"]) or r["name"], r["amount_cents"], r["reference"],
                   r["message"], r["id"], r["transaction_id"], r["source"]) for r in books.conn.execute(sql)]


def _day_gap(rec_date: str | None, tx_date: str) -> int | None:
    if not rec_date:
        return None
    t = date.fromisoformat(tx_date)
    if rec_date.startswith("????"):
        mo, dy = int(rec_date[5:7]), int(rec_date[8:10])
        gaps = []
        for y in (t.year - 1, t.year, t.year + 1):
            try:
                gaps.append(abs((date(y, mo, dy) - t).days))
            except ValueError:
                continue
        return min(gaps) if gaps else None
    return abs((date.fromisoformat(rec_date) - t).days)


@dataclass
class Candidate:
    tx: Transaction
    score: int
    why: str


def candidates(books: Books, rec: Record, exclude: set[int] | None = None, max_days: int = 5) -> list[Candidate]:
    """Bank e-Transfers this record could be, best first."""
    exclude = exclude or set()
    sign = {"sent": "< 0", "received": "> 0"}.get(rec.direction, "!= 0")
    rows = books.conn.execute(f"SELECT id FROM transactions WHERE ABS(amount_cents)=? AND amount_cents {sign}",
                              (rec.amount_cents,)).fetchall()
    out = []
    for (tx_id,) in rows:
        if tx_id in exclude:
            continue
        tx = books.transaction(tx_id)
        etf = is_etransfer(tx.description)
        if not etf and re.search(r"\bFEE\b", tx.description, re.I):
            continue
        suffix = ref_suffix(tx.description)
        gap = _day_gap(rec.date, tx.date)
        score, why = 0, []
        if suffix and rec.reference:
            if rec.reference.endswith(suffix):
                score += 100
                why.append(f"reference …{suffix}")
            elif rec.reference.lower().endswith(suffix.lower()):
                score += 80
                why.append(f"reference …{suffix}")
            else:
                # Banks don't always show the same reference as the e-Transfer history (received transfers
                # often carry the sender's number), so a different ending only lowers the score.
                score -= 5
        if not etf:
            score -= 20  # a deposit / withdrawal not labelled as an e-Transfer: only if nothing better
            why.append("not labelled e-Transfer")
        if gap is not None:
            if gap > max_days and score < 80:
                continue
            if not etf and gap > 3:
                continue
            score += max(0, 10 - 2 * gap)
            why.insert(0, "same day" if gap == 0 else f"{gap} day(s) apart")
        elif score < 80:
            continue  # no date and no reference: can't tell
        out.append(Candidate(tx, score, ", ".join(why) or "same amount"))
    out.sort(key=lambda c: (-c.score, c.tx.date))
    return out


@dataclass
class Line:
    """A bank line offered in Match by Hand."""
    tx: Transaction
    gap: int | None          # days from the e-Transfer's date
    is_etransfer: bool
    same_amount: bool
    right_direction: bool
    matched_to: str          # label of the e-Transfer already matched to it, '' if free
    why: str


def nearby_lines(books: Books, rec: Record, days: int = 7, any_amount: bool = False) -> list[Line]:
    """Bank lines around the e-Transfer's date for matching by hand: the same amount (or any amount), e-Transfers
    first, including lines already matched to another e-Transfer (so a wrong match can be moved)."""
    ensure_schema(books)
    owners = {r["transaction_id"]: r for r in books.conn.execute(
        "SELECT * FROM etransfers WHERE transaction_id IS NOT NULL AND id != ?", (rec.id or 0,))}
    if rec.date and not rec.date.startswith("?"):
        d = date.fromisoformat(rec.date)
        lo, hi = (d - timedelta(days=days)).isoformat(), (d + timedelta(days=days)).isoformat()
        sql, args = "SELECT id FROM transactions WHERE date BETWEEN ? AND ?", [lo, hi]
    else:
        sql, args = "SELECT id FROM transactions WHERE 1=1", []
    if not any_amount:
        sql += " AND ABS(amount_cents)=?"
        args.append(rec.amount_cents)
    out = []
    for (tx_id,) in books.conn.execute(sql, args).fetchall():
        tx = books.transaction(tx_id)
        etf = is_etransfer(tx.description)
        if any_amount and not etf:
            continue  # "any amount" lists the e-Transfers around that date
        same = abs(tx.amount_cents) == rec.amount_cents
        right = rec.direction == "unknown" or (rec.direction == "sent") == (tx.amount_cents < 0)
        gap = _day_gap(rec.date, tx.date)
        owner = owners.get(tx_id)
        matched = ""
        if owner is not None:
            who = {"sent": "to", "received": "from"}.get(owner["direction"], "")
            matched = f"e-Transfer {who} {_clean_name(owner['name']) or owner['name']}".replace("  ", " ")
        why = []
        suffix = ref_suffix(tx.description)
        if suffix and rec.reference and rec.reference.lower().endswith(suffix.lower()):
            why.append(f"reference …{suffix}")
        if not same:
            why.append(f"different amount ({fmt_money(abs(tx.amount_cents))})")
        if not right:
            why.append("money in" if tx.amount_cents > 0 else "money out")
        if not etf:
            why.append("not labelled e-Transfer")
        out.append(Line(tx, gap, etf, same, right, matched, ", ".join(why)))
    out.sort(key=lambda l: (bool(l.matched_to), not l.same_amount, not l.right_direction, not l.is_etransfer,
                            l.gap if l.gap is not None else 999, l.tx.date))
    return out


def bank_lines(books: Books) -> list[Transaction]:
    """Every e-Transfer line in the imported statements (SEND E-TFR …, E-TFR …, E-TRANSFER …), newest first."""
    return [t for t in books.transactions() if is_etransfer(t.description)]


def bank_lines_without_history(books: Books) -> list[Transaction]:
    used = linked_ids(books)
    return [t for t in bank_lines(books) if t.id not in used]


def linked_ids(books: Books) -> set[int]:
    ensure_schema(books)
    return {r[0] for r in books.conn.execute("SELECT transaction_id FROM etransfers WHERE transaction_id IS NOT NULL")}


def _memo_with(tx: Transaction, note: str) -> str:
    parts = [p for p in tx.memo.split(" · ") if p and not p.startswith("↔")]
    return " · ".join(parts + [note]) if parts else note


def note_for(rec: Record) -> str:
    bits = [f"↔ {rec.label}"]
    if rec.message:
        bits.append(f"“{rec.message}”")
    return " ".join(bits)


def link(books: Books, rec_id: int, tx_id: int) -> None:
    """Match a record to its bank transaction: memo gets the name and message; category from past choices."""
    row = books.conn.execute("SELECT * FROM etransfers WHERE id=?", (rec_id,)).fetchone()
    rec = Record(row["direction"], row["date"], _clean_name(row["name"]) or row["name"], row["amount_cents"],
                 row["reference"], row["message"], row["id"])
    if row["transaction_id"] and row["transaction_id"] != tx_id:
        unlink(books, rec_id)  # moving it: clear the note on the line it was on
    tx = books.transaction(tx_id)
    with books.conn:
        books.conn.execute("UPDATE etransfers SET transaction_id=NULL WHERE transaction_id=?", (tx_id,))
        books.conn.execute("UPDATE etransfers SET transaction_id=? WHERE id=?", (tx_id, rec_id))
        books.conn.execute("UPDATE transactions SET memo=? WHERE id=?", (_memo_with(tx, note_for(rec)), tx_id))
    if rec.name and _pay_open_document(books, rec, tx):
        return
    if rec.name and (tx.category_id is None or tx.category_source in (None, "keyword")) and not tx.reviewed:
        acct = category_for_contact(books, rec.name, rec.direction, exclude_tx=tx_id)
        if acct and acct != tx.bank_account_id:
            books.set_category(tx_id, acct, source="learned")


def _pay_open_document(books: Books, rec: Record, tx: Transaction) -> bool:
    """An e-Transfer from a customer / to a vendor with an open invoice or bill of exactly that amount pays it."""
    from . import receivables as rx

    if not books._has_table("documents") or tx.reviewed:
        return False
    kind = "invoice" if tx.amount_cents > 0 else "bill"
    name = rec.name.lower()
    for doc in rx.documents(books, kind, as_of=date(9999, 12, 31)):
        party = doc.party.lower()
        if doc.balance_cents == abs(tx.amount_cents) and (party in name or name in party):
            try:
                rx.record_payment(books, doc.id, tx.id)
                return True
            except ValueError:
                continue
    return False


def unlink(books: Books, rec_id: int) -> None:
    row = books.conn.execute("SELECT transaction_id FROM etransfers WHERE id=?", (rec_id,)).fetchone()
    with books.conn:
        if row and row[0]:
            try:
                tx = books.transaction(row[0])
                memo = " · ".join(p for p in tx.memo.split(" · ") if p and not p.startswith("↔"))
                books.conn.execute("UPDATE transactions SET memo=? WHERE id=?", (memo, tx.id))
            except KeyError:
                pass
        books.conn.execute("UPDATE etransfers SET transaction_id=NULL WHERE id=?", (rec_id,))


def delete(books: Books, rec_id: int) -> None:
    unlink(books, rec_id)
    with books.conn:
        books.conn.execute("DELETE FROM etransfers WHERE id=?", (rec_id,))


def category_for_contact(books: Books, name: str, direction: str, exclude_tx: int | None = None) -> int | None:
    """The category you chose most often for e-Transfers with this person (same direction)."""
    from . import chart_of_accounts as coa

    rows = books.conn.execute(
        "SELECT t.category_id, COUNT(*) n FROM etransfers e JOIN transactions t ON t.id=e.transaction_id "
        "WHERE LOWER(e.name)=LOWER(?) AND e.direction=? AND t.category_id IS NOT NULL AND t.id != ? "
        "AND (t.category_source='manual' OR t.reviewed=1) GROUP BY t.category_id ORDER BY n DESC",
        (name, direction, exclude_tx or 0)).fetchall()
    for acct_id, _n in rows:
        try:
            if books.account(acct_id).key not in (coa.UNCATEGORIZED_EXPENSE, coa.UNCATEGORIZED_INCOME):
                return acct_id
        except KeyError:
            continue
    return None


@dataclass
class MatchResult:
    matched: int = 0
    waiting: int = 0
    ambiguous: list[str] | None = None

    def describe(self) -> str:
        s = f"{self.matched} e-Transfer(s) matched · {self.waiting} waiting for their bank transaction"
        if self.ambiguous:
            s += f" · {len(self.ambiguous)} need you (two possible transactions)"
        return s


def match_all(books: Books) -> MatchResult:
    """Match every unmatched record whose bank transaction is clear. Safe to run after each import."""
    ensure_schema(books)
    used = linked_ids(books)
    res = MatchResult(ambiguous=[])
    for rec in records(books, status="unmatched"):
        cands = candidates(books, rec, used)
        if not cands:
            res.waiting += 1
            continue
        best = cands[0]
        if len(cands) > 1 and cands[1].score >= best.score - 2 and best.score < 80 or \
                (best.score < 0 and len(cands) > 1):
            res.ambiguous.append(f"{rec.label} {fmt_money(rec.amount_cents)}: "
                                 f"{len(cands)} transactions of that amount")
            res.waiting += 1
            continue
        link(books, rec.id, best.tx.id)
        used.add(best.tx.id)
        res.matched += 1
    return res


def why_waiting(books: Books, rec: Record, used: set[int] | None = None) -> str:
    """Plain-language reason an e-Transfer isn't matched yet."""
    used = linked_ids(books) if used is None else used
    cands = candidates(books, rec, used)
    money = fmt_money(rec.amount_cents)
    if len(cands) > 1:
        return f"{len(cands)} bank lines of {money} around that date — use Match by Hand"
    if cands:
        return "Ready to match — click Match Now"
    side = "deposit" if rec.direction == "received" else "withdrawal" if rec.direction == "sent" else "line"
    when = (rec.date or "").replace("????-", "")
    if not rec.date or rec.date.startswith("?"):
        return f"No {side} of {money} found — add the year, or use Match by Hand"
    d = date.fromisoformat(rec.date)
    lo, hi = (d - timedelta(days=15)).isoformat(), (d + timedelta(days=15)).isoformat()
    if not books.conn.execute("SELECT 1 FROM transactions WHERE date BETWEEN ? AND ? LIMIT 1", (lo, hi)).fetchone():
        return f"No statement imported around {when} yet — it will match after you import it"
    near = books.conn.execute("SELECT date FROM transactions WHERE ABS(amount_cents)=? AND date BETWEEN ? AND ? "
                              "ORDER BY date", (rec.amount_cents, (d - timedelta(days=60)).isoformat(),
                                                (d + timedelta(days=60)).isoformat())).fetchall()
    if near:
        return (f"A {money} line on {', '.join(x for (x,) in near[:3])} isn't a {side} within 5 days of {when} "
                f"— use Match by Hand if it's this one")
    return f"No {side} of {money} near {when} in the imported statements"


def import_file(books: Books, path: Path) -> tuple[int, int, MatchResult]:
    recs = read_file(path)
    added, known = add_records(books, recs)
    return added, known, match_all(books)


def import_text(books: Books, text: str) -> tuple[int, int, MatchResult]:
    added, known = add_records(books, parse_text(text, "pasted text"))
    return added, known, match_all(books)


def record_for_transaction(books: Books, tx_id: int) -> Record | None:
    ensure_schema(books)
    return next((r for r in records(books, status="matched") if r.transaction_id == tx_id), None)


# ---------------------------------------------------------------------------
# Drop folder in the company folder
# ---------------------------------------------------------------------------


def folder(books: Books) -> Path | None:
    from . import workspace as ws

    cfg = ws.get_config(books)
    return cfg.dir("etransfer") if cfg.enabled else None


def process_folder(books: Books, *, min_age_seconds: float = 5.0) -> list[str]:
    """Read history files dropped in E-Transfer History/ (they move to Read/), then match. Returns messages."""
    import time

    root = folder(books)
    if root is None or not root.is_dir():
        return []
    ensure_schema(books)
    msgs = []
    for f in sorted(p for p in root.iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS
                    and not p.name.startswith((".", "~$"))):
        try:
            if min_age_seconds > 0 and time.time() - f.stat().st_mtime < min_age_seconds:
                continue
            recs = read_file(f)
        except Exception as e:  # noqa: BLE001
            msgs.append(f"e-Transfer history {f.name}: couldn't read ({e})")
            continue
        added, _known = add_records(books, recs)
        dest = root / READ_DIR / f.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        from .pdf_to_excel import unique_path

        try:
            shutil.move(str(f), unique_path(dest))
        except OSError:
            pass
        msgs.append(f"e-Transfer history {f.name}: {len(recs)} transfer(s) read, {added} new")
    res = match_all(books)
    if res.matched:
        msgs.append(f"e-Transfer history: {res.describe()}")
    return msgs
