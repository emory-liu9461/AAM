"""Column-aware PDF statement reader.

Many statements lay transactions out in columns under a header row such as::

    DESCRIPTION   CHEQUE/DEBIT   DEPOSIT/CREDIT   DATE    BALANCE
    MOS MOS COFFEE        8.14                    MAY02     25.57
    PAYPROP AP                       1,233.09     MAY06  1,258.66

Reading the page as plain text loses which column an amount was in (and puts the
date wherever the bank chose). This reader finds the header row, then uses each
amount's horizontal position to decide whether it's a debit, a credit or the
running balance — the same way a person reads the statement. It works whether the
date comes first or last, and with dates like ``MAY02``, ``May 2``, ``05/02`` or
``02 May``.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date

from ..books import ParsedTransaction
from ..dates import parse_date
from ..money import parse_money

MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|SEPT|OCT|NOV|DEC"
AMOUNT_RE = re.compile(r"\(?-?\$?\d{1,3}(?:,\d{3})*\.\d{2}\)?(?:\s?(?:OD|CR|DR)|-)?", re.I)
DATE_RE = re.compile(
    rf"(?<![A-Za-z])(?:(?:{MONTHS})[a-z]*\.?\s?\d{{1,2}}(?:,?\s?\d{{2,4}})?|\d{{1,2}}\s?(?:{MONTHS})[a-z]*(?:\s?\d{{2,4}})?"
    rf"|\d{{1,2}}/\d{{1,2}}(?:/\d{{2,4}})?|\d{{4}}-\d{{2}}-\d{{2}})(?![\d,.])", re.I)

DEBIT_HEAD = re.compile(r"debit|withdraw|cheque|check|payment|paid\s*out|money\s*out|charges|subtractions", re.I)
CREDIT_HEAD = re.compile(r"credit|deposit|paid\s*in|money\s*in|additions", re.I)
BALANCE_HEAD = re.compile(r"balance", re.I)
DATE_HEAD = re.compile(r"^date$|^posted$|^posting$", re.I)
OPENING_RE = re.compile(r"balance\s*forward|opening\s*balance|previous\s*balance|beginning\s*balance|"
                        r"starting\s*balance|balance\s*brought\s*forward", re.I)
FOOTER_RE = re.compile(r"\b(total|totals|monthly|aver|average|min\.?\s*bal|chqs?\s+enclosed|items|"
                       r"closing\s*balance|ending\s*balance|statement\s*date)\b", re.I)


@dataclass
class _Tok:
    text: str
    x0: float
    x1: float
    kind: str  # amount | date | text

    @property
    def center(self) -> float:
        return (self.x0 + self.x1) / 2


@dataclass
class _Col:
    kind: str  # debit | credit | balance | date
    x0: float
    x1: float


def _tokens(word: dict) -> list[_Tok]:
    """Split a word into amount / date / text pieces, with x positions from its characters."""
    text, chars = word["text"], word.get("chars") or []
    if len(chars) != len(text):  # can't map positions reliably: treat as one piece
        return [_Tok(text, word["x0"], word["x1"], _kind(text))]
    spans = []
    for m in AMOUNT_RE.finditer(text):
        spans.append((m.start(), m.end(), "amount"))
    masked = list(text)
    for s_, e_, _k in spans:  # dates are looked for only outside amounts ("1,233.09MAY06")
        masked[s_:e_] = " " * (e_ - s_)
    for m in DATE_RE.finditer("".join(masked)):
        spans.append((m.start(), m.end(), "date"))
    spans.sort()
    out, pos = [], 0
    for s, e, kind in spans:
        if s > pos:
            out.append(_Tok(text[pos:s], chars[pos]["x0"], chars[s - 1]["x1"], "text"))
        out.append(_Tok(text[s:e], chars[s]["x0"], chars[e - 1]["x1"], kind))
        pos = e
    if pos < len(text):
        out.append(_Tok(text[pos:], chars[pos]["x0"], chars[-1]["x1"], "text"))
    return [t for t in out if t.text.strip()]


def _merge_dates(toks: list[_Tok]) -> list[_Tok]:
    """Join neighbouring words that together make a date ('Mar' '3' '2026' -> 'Mar 3 2026')."""
    out: list[_Tok] = []
    i = 0
    while i < len(toks):
        merged = None
        for n in (3, 2):
            group = toks[i:i + n]
            if len(group) < n or any(t.kind == "amount" for t in group):
                continue
            if any(b.x0 - a.x1 > 8 for a, b in zip(group, group[1:])):
                continue
            text = " ".join(t.text for t in group)
            if DATE_RE.fullmatch(text):
                merged = (_Tok(text, group[0].x0, group[-1].x1, "date"), n)
                break
        if merged:
            out.append(merged[0])
            i += merged[1]
        else:
            out.append(toks[i])
            i += 1
    return out


def _kind(text: str) -> str:
    if AMOUNT_RE.fullmatch(text):
        return "amount"
    if DATE_RE.fullmatch(text):
        return "date"
    return "text"


def _lines(words: list[dict], tol: float = 3.0) -> list[list[dict]]:
    rows: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if rows and abs(rows[-1][0]["top"] - w["top"]) <= tol:
            rows[-1].append(w)
        else:
            rows.append([w])
    return [sorted(r, key=lambda w: w["x0"]) for r in rows]


def _header(row: list[dict]) -> list[_Col] | None:
    cols = []
    for w in row:
        t = w["text"]
        if DATE_HEAD.search(t):
            cols.append(_Col("date", w["x0"], w["x1"]))
        elif BALANCE_HEAD.search(t):
            cols.append(_Col("balance", w["x0"], w["x1"]))
        elif CREDIT_HEAD.search(t) and not DEBIT_HEAD.search(t.split("/")[0]):
            cols.append(_Col("credit", w["x0"], w["x1"]))
        elif DEBIT_HEAD.search(t):
            cols.append(_Col("debit", w["x0"], w["x1"]))
    kinds = {c.kind for c in cols}
    if {"debit", "credit"} <= kinds:
        return cols
    return None


def _column_for(tok: _Tok, cols: list[_Col]) -> str | None:
    money_cols = [c for c in cols if c.kind in ("debit", "credit", "balance")]
    best, best_key = None, None
    for c in money_cols:
        gap = max(0.0, c.x0 - tok.x1, tok.x0 - c.x1)
        key = (gap, abs(tok.center - (c.x0 + c.x1) / 2))
        if best_key is None or key < best_key:
            best, best_key = c, key
    if best is None or best_key[0] > 60:  # far from every money column: not a table amount
        return None
    return best.kind


def _money(text: str) -> int | None:
    t = text.strip()
    od = t.upper().endswith("OD")
    v = parse_money(t[:-2] if od else t)
    if v is None:
        return None
    return -abs(v) if od else v


def _resolve(raw: str, period_end: date | None, dayfirst: bool) -> date | None:
    from .pdf_importer import _resolve_date

    raw = re.sub(rf"^({MONTHS})[a-z]*\.?(\d)", r"\1 \2", raw.strip(), flags=re.I)  # MAY02 -> MAY 02
    raw = re.sub(rf"^(\d{{1,2}})({MONTHS})", r"\1 \2", raw, flags=re.I)              # 02MAY -> 02 MAY
    return _resolve_date(raw, period_end, dayfirst)


def parse_columns(data: bytes, *, dayfirst: bool = False,
                  period_end: date | None = None) -> tuple[list[ParsedTransaction], list[str]]:
    """Read transactions from column-laid-out statements. Returns ([], []) if no table header is found."""
    import pdfplumber

    out: list[ParsedTransaction] = []
    warnings: list[str] = []
    cols: list[_Col] | None = None
    balance: int | None = None
    first_balance: int | None = None
    last_date: date | None = None
    last_stated: int | None = None
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            words = [w for w in page.extract_words(x_tolerance=1.5, y_tolerance=2, return_chars=True)
                     if w.get("upright", True)]  # skip sideways margin text (mail sorting codes etc.)
            in_table = False
            for row in _lines(words):
                head = _header(row)
                if head:
                    cols, in_table = head, True
                    continue
                if not cols or not in_table:
                    continue
                toks = _merge_dates([t for w in row for t in _tokens(w)])
                line_text = " ".join(t.text for t in toks)
                amounts = [(t, _column_for(t, cols)) for t in toks if t.kind == "amount"]
                amounts = [(t, k) for t, k in amounts if k]
                if OPENING_RE.search(line_text):
                    bal = next((_money(t.text) for t, k in amounts if k == "balance"), None)
                    if bal is not None:
                        balance = bal
                        if first_balance is None:
                            first_balance = bal
                    continue
                moves = [(t, k) for t, k in amounts if k in ("debit", "credit")]
                if not moves:
                    continue
                dates = [t for t in toks if t.kind == "date"]
                date_col = next((c for c in cols if c.kind == "date"), None)
                if date_col and dates:
                    dates.sort(key=lambda t: abs(t.center - (date_col.x0 + date_col.x1) / 2))
                d = _resolve(dates[0].text, period_end, dayfirst) if dates else None
                first_amount_x = min(t.x0 for t, _ in amounts)
                chosen = dates[0] if dates else None
                desc = " ".join(t.text for t in toks if t.x1 <= first_amount_x + 1 and t is not chosen
                                and t.kind != "amount").strip(" -")
                if d is None:
                    if last_date is None or not re.search(r"[A-Za-z]{2}", desc) or FOOTER_RE.search(line_text):
                        continue
                    d = last_date  # some banks print the date only on the first line of each day
                if FOOTER_RE.search(desc) and not re.search(r"fee|charge|interest", desc, re.I):
                    continue
                for t, k in moves:
                    cents = abs(_money(t.text) or 0)
                    if not cents:
                        continue
                    sign = -1 if k == "debit" else 1
                    out.append(ParsedTransaction(d, desc or "(no description)", sign * cents))
                    if balance is not None:
                        balance += sign * cents
                stated = next((_money(t.text) for t, k in amounts if k == "balance"), None)
                if stated is not None:
                    if balance is not None and stated != balance:
                        warnings.append(f"Running balance on {d:%b %d} is {stated / 100:,.2f} on the statement but "
                                        f"{balance / 100:,.2f} from the transactions read — check that day.")
                    balance = stated
                    last_stated = stated
                last_date = d
    if out and first_balance is not None and last_stated is not None:
        computed = first_balance + sum(t.amount_cents for t in out)
        if computed == last_stated and not warnings:
            warnings.append(f"Checked: opening balance {first_balance / 100:,.2f} plus these transactions equals "
                            f"the statement's closing balance {last_stated / 100:,.2f}.")
    return out, warnings
