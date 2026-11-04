"""PDF bank statement importer.

PDF statements have no standard layout, so this works from the text:

* A transaction line starts with a date and ends with one or more amounts.
  When there are two amounts, the last one is the running balance.
* Money in vs. money out is decided, in order, by: an explicit sign on the
  amount (``-``, parentheses, ``CR``/``DR``); the change in running balance;
  the section heading the line sits under ("Deposits", "Withdrawals", ...);
  and finally words in the description.
* Years missing from dates ("03/14") come from the statement period.

Results are always shown in a preview before import so signs can be flipped.
Scanned (image-only) PDFs have no text layer and need OCR first.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from datetime import date

from ..books import ParsedTransaction
from ..dates import parse_date
from ..money import parse_money

MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_DATE = (rf"(?:\d{{1,2}}[/-]\d{{1,2}}(?:[/-]\d{{2,4}})?|\d{{4}}-\d{{2}}-\d{{2}}|"
         rf"(?:{MONTHS})[a-z]*\.?\s+\d{{1,2}}(?:,?\s+\d{{4}})?|\d{{1,2}}\s+(?:{MONTHS})[a-z]*(?:\s+\d{{4}})?)")
_AMOUNT = r"[-+]?\(?\$?\s?\d{1,3}(?:,\d{3})*\.\d{2}\)?-?(?:\s?(?:CR|DR))?"

LINE_RE = re.compile(rf"^\s*(?P<date>{_DATE})\s+(?P<rest>.+)$", re.I)
TRAILING_AMOUNT_RE = re.compile(rf"\s+(?P<amt>{_AMOUNT})\s*$", re.I)
YEAR_RE = re.compile(r"\b(20\d{2}|19\d{2})\b")
PERIOD_RE = re.compile(rf"({_DATE}[,\s]+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}})\s*(?:-|–|to|through|thru)\s*"
                       rf"({_DATE}[,\s]+\d{{4}}|\d{{1,2}}/\d{{1,2}}/\d{{2,4}}|{_DATE})", re.I)

INFLOW_HEADINGS = re.compile(r"^\s*(deposits?|credits?|additions|deposits and (other )?(credits|additions)|"
                             r"money in|incoming|electronic deposits|other credits)\b", re.I)
OUTFLOW_HEADINGS = re.compile(r"^\s*(withdrawals?|debits?|subtractions|checks( paid)?|fees|service fees|"
                              r"atm (and|&) debit card|card (purchases|transactions)|electronic (withdrawals|payments)|"
                              r"other (debits|withdrawals)|withdrawals and (other )?(debits|subtractions)|money out|"
                              r"payments|purchases)\b", re.I)
NEUTRAL_HEADINGS = re.compile(r"^\s*(daily (ending )?balance|balance summary|account summary)\b", re.I)
OPENING_RE = re.compile(r"(beginning|opening|previous|starting) balance", re.I)
SKIP_RE = re.compile(r"(ending|closing|new) balance|total|subtotal|balance forward|page \d", re.I)
INFLOW_WORDS = re.compile(r"deposit|credit|refund|interest (paid|earned)|transfer from|payment received|"
                          r"\bfrom\b|reversal|payroll|direct dep", re.I)


def _split_amounts(rest: str) -> tuple[str, list[str]]:
    amounts: list[str] = []
    while True:
        m = TRAILING_AMOUNT_RE.search(rest)
        if not m:
            break
        amounts.insert(0, m.group("amt"))
        rest = rest[: m.start()]
    return rest.strip(), amounts


def _explicit_sign(token: str) -> int:
    t = token.strip().upper()
    if t.startswith("-") or t.endswith("-") or t.startswith("(") or t.endswith("DR"):
        return -1
    if t.endswith("CR") or t.startswith("+"):
        return 1
    return 0


PERIOD_SHORT_RE = re.compile(rf"({_DATE})\s*(?:-|–|to|through|thru)\s*({_DATE})[,\s]+(\d{{4}})", re.I)


def _statement_period(text: str) -> tuple[date | None, date | None]:
    m2 = PERIOD_SHORT_RE.search(text)
    if m2:
        end = parse_date(f"{m2.group(2)} {m2.group(3)}") or parse_date(f"{m2.group(2)}/{m2.group(3)}")
        if end:
            start = parse_date(f"{m2.group(1)} {end.year}") or parse_date(f"{m2.group(1)}/{end.year}")
            return start, end
    m = PERIOD_RE.search(text)
    if m:
        end = parse_date(m.group(2).replace(",", ", ").replace("  ", " "))
        start = parse_date(m.group(1).replace(",", ", ").replace("  ", " "))
        if end and not start:
            start = parse_date(f"{m.group(1)} {end.year}")
        if end:
            return start, end
    # "APR 30/24 - MAY 31/24" (TD and other Canadian banks)
    short = [parse_date(f"{m.group(1)} {m.group(2)}/{m.group(3)}") for m in
             re.finditer(rf"\b({MONTHS})[a-z]*\.?\s?(\d{{1,2}})/(\d{{2,4}})\b", text, re.I)]
    short = [d for d in short if d]
    if short:
        return min(short), max(short)
    years = [int(y) for y in YEAR_RE.findall(text)]
    if years:
        y = Counter(years).most_common(1)[0][0]
        return None, date(y, 12, 31)
    return None, None


def _resolve_date(raw: str, period_end: date | None, dayfirst: bool) -> date | None:
    d = parse_date(raw, dayfirst=dayfirst)
    if d:
        return d
    if period_end is None:
        return None
    raw = raw.strip().rstrip(",")
    d = parse_date(f"{raw}/{period_end.year}", dayfirst=dayfirst) or parse_date(f"{raw} {period_end.year}")
    if d and d > period_end and (d - period_end).days > 31:
        d = d.replace(year=d.year - 1)  # e.g. Dec transactions on a January statement
    return d


def parse_statement_text(text: str, *, dayfirst: bool = False,
                         fallback_year_end: date | None = None) -> tuple[list[ParsedTransaction], list[str]]:
    _, period_end = _statement_period(text)
    warnings: list[str] = []
    if period_end is None:
        # No year anywhere on the statement: assume the most recent 12 months.
        period_end = fallback_year_end or date.today()
        warnings.append(f"Could not find the statement year; dates without a year were assumed to be in the "
                        f"12 months up to {period_end:%b %Y}. Check them in the preview.")
    out: list[ParsedTransaction] = []
    section = 0
    balance: int | None = None

    for line in text.splitlines():
        if not line.strip():
            continue
        if INFLOW_HEADINGS.search(line) and not LINE_RE.match(line):
            section = 1
            continue
        if OUTFLOW_HEADINGS.search(line) and not LINE_RE.match(line):
            section = -1
            continue
        if NEUTRAL_HEADINGS.search(line) and not LINE_RE.match(line):
            section = 0
            continue

        if OPENING_RE.search(line):
            _, amts = _split_amounts(line)
            if amts:
                balance = parse_money(amts[-1])
            continue

        m = LINE_RE.match(line)
        if not m:
            continue
        desc, amounts = _split_amounts(m.group("rest"))
        if not amounts or not desc or SKIP_RE.search(desc):
            if amounts and re.search(r"(ending|closing|new) balance", desc or "", re.I):
                balance = parse_money(amounts[-1])
            continue
        if section == 0 and len(amounts) == 1 and balance is None and NEUTRAL_HEADINGS.search(desc):
            continue
        d = _resolve_date(m.group("date"), period_end, dayfirst)
        if d is None:
            continue

        amt_token = amounts[-2] if len(amounts) >= 2 else amounts[0]
        new_balance = parse_money(amounts[-1]) if len(amounts) >= 2 else None
        cents = abs(parse_money(amt_token) or 0)
        if cents == 0:
            continue

        sign = _explicit_sign(amt_token)
        if sign == 0 and balance is not None and new_balance is not None:
            diff = new_balance - balance
            if abs(diff) == cents:
                sign = 1 if diff > 0 else -1
        if sign == 0:
            sign = section
        if sign == 0:
            sign = 1 if INFLOW_WORDS.search(desc) else -1
        if new_balance is not None:
            balance = new_balance
        elif balance is not None:
            balance += sign * cents
        out.append(ParsedTransaction(d, " ".join(desc.split()), sign * cents))

    if not out:
        warnings.append("No transactions found. If this is a scanned PDF, it needs OCR first; "
                        "otherwise try downloading a CSV or OFX/QFX export from your bank.")
    return out, warnings


CID_RE = re.compile(r"\(cid:\d+\)")


class PdfReadError(ValueError):
    pass


def _open_pdf(data: bytes):
    import pdfplumber

    try:
        return pdfplumber.open(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "password" in msg or "encrypt" in msg:
            raise PdfReadError("This PDF is password-protected. Open it, print/save it as a new PDF without a "
                               "password, and import that copy.") from e
        raise PdfReadError(f"This file couldn't be opened as a PDF ({e}).") from e


def extract_text(data: bytes, *, layout: bool = False) -> str:
    pages = []
    with _open_pdf(data) as pdf:
        for page in pdf.pages:
            if layout:
                text = page.extract_text(layout=True, x_density=6, y_density=13) or ""
            else:
                text = page.extract_text(layout=False, x_tolerance=1.5) or ""
            pages.append(CID_RE.sub("", text))
    return "\n".join(pages)


def text_is_usable(text: str) -> bool:
    """False for scanned pages (no text layer) or text that came out as gibberish."""
    letters = re.findall(r"[A-Za-z]{3,}", text)
    return len(letters) >= 15 and bool(re.search(r"\d", text))


def parse_pdf(data: bytes, *, dayfirst: bool = False, allow_ocr: bool = True,
              progress=None) -> tuple[list[ParsedTransaction], list[str]]:
    """Read a PDF statement, trying progressively stronger methods until transactions are found:

    1. the PDF's text in reading order,
    2. the text laid out as on the page (keeps columns apart),
    3. OCR of the page images, for scanned statements or unreadable fonts.
    """
    attempts: list[tuple[str, list, list, str]] = []
    # 1. Column-aware reading (statements with DEBIT / CREDIT / BALANCE columns)
    try:
        plain = extract_text(data)
    except PdfReadError:
        raise
    except Exception:  # noqa: BLE001
        plain = ""
    _, period_end = _statement_period(plain)
    try:
        from .pdf_columns import parse_columns

        col_txs, col_warns = parse_columns(data, dayfirst=dayfirst, period_end=period_end or date.today())
    except Exception as e:  # noqa: BLE001 - fall back to the text readers
        col_txs, col_warns = [], [f"Column reader failed: {e}"]
    if col_txs:
        if period_end is None:
            col_warns.insert(0, "Could not find the statement year; dates were assumed to be in the last 12 "
                                "months. Check them in the preview.")
        attempts.append(("columns", col_txs, col_warns, plain))
    for name, layout in (("text", False), ("layout", True)):
        try:
            text = extract_text(data, layout=layout)
        except PdfReadError:
            raise
        except Exception as e:  # noqa: BLE001 - try the next method
            attempts.append((name, [], [f"Text extraction failed: {e}"], ""))
            continue
        txs, warns = parse_statement_text(text, dayfirst=dayfirst)
        attempts.append((name, txs, warns, text))
        if txs and name == "text":
            break
    best = max(attempts, key=lambda a: len(a[1]))
    if best[1]:
        return best[1], best[2]

    text_ok = any(text_is_usable(a[3]) for a in attempts)
    from .ocr import ocr_available, ocr_pdf_text

    if allow_ocr and ocr_available():
        text = ocr_pdf_text(data, progress=progress)
        txs, warns = parse_statement_text(text, dayfirst=dayfirst)
        if txs:
            return txs, ["Read with OCR (this PDF is a scanned image or has unreadable text) — check dates "
                         "and amounts in the preview against the statement."] + warns
        if not text_is_usable(text) and not text_ok:
            return [], ["OCR couldn't read text on this PDF. Try a clearer scan (300 dpi, straight pages) or "
                        "download a CSV / OFX / QFX export from your bank."]
        return [], ["The text was read, but no transaction lines were recognized in this statement layout. "
                    "Use “Show extracted text” and send it to support, or download a CSV / OFX / QFX export "
                    "from your bank."] + [w for w in warns if "No transactions" not in w]
    if not text_ok:
        return [], ["This PDF has no readable text (it's probably a scanned image) and OCR isn't installed. "
                    "Download a CSV / OFX / QFX export from your bank instead."]
    return [], ["The text was read, but no transaction lines were recognized in this statement layout. Use "
                "“Show extracted text” to see what was read, or download a CSV / OFX / QFX export from your bank."]


def best_text(data: bytes, *, allow_ocr: bool = True) -> tuple[str, str]:
    """(method, text) actually read from a PDF — for the 'Show extracted text' view."""
    text = ""
    try:
        text = extract_text(data)
        if text_is_usable(text):
            return "PDF text", text
    except PdfReadError as e:
        return "error", str(e)
    from .ocr import ocr_available, ocr_pdf_text

    if allow_ocr and ocr_available():
        return "OCR", ocr_pdf_text(data)
    return "PDF text (little or none)", text
