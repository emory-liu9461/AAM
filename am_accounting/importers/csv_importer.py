"""CSV / spreadsheet bank export importer with automatic column detection."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field

from ..books import ParsedTransaction
from ..dates import parse_date
from ..money import parse_money

# Header synonyms, most preferred first.
DATE_HEADERS = ["date", "transaction date", "trans date", "posting date", "post date", "posted date",
                "value date", "booking date", "effective date"]
DESC_HEADERS = ["description", "transaction description", "payee", "name", "merchant", "details",
                "narrative", "transaction details", "particulars", "memo"]
AMOUNT_HEADERS = ["amount", "transaction amount", "amt", "net amount", "amount (usd)", "value"]
DEBIT_HEADERS = ["debit", "debits", "withdrawal", "withdrawals", "money out", "paid out", "debit amount",
                 "amount debit", "outflow", "charges", "payments"]
CREDIT_HEADERS = ["credit", "credits", "deposit", "deposits", "money in", "paid in", "credit amount",
                  "amount credit", "inflow"]
TYPE_HEADERS = ["type", "transaction type", "credit/debit", "debit/credit", "cr/dr", "dr/cr", "details"]
MEMO_HEADERS = ["memo", "notes", "note", "additional info", "reference", "check number", "check or slip #"]


@dataclass
class ColumnMap:
    date: int | None = None
    description: int | None = None
    amount: int | None = None
    debit: int | None = None
    credit: int | None = None
    type: int | None = None
    memo: int | None = None
    header_row: int | None = None  # index of header row; None for headerless files
    headers: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (self.date is not None and self.description is not None
                and (self.amount is not None or self.debit is not None or self.credit is not None))


def _norm(h: str) -> str:
    return re.sub(r"\s+", " ", h.strip().lower().replace("_", " "))


def _find(headers: list[str], names: list[str], taken: set[int]) -> int | None:
    normed = [_norm(h) for h in headers]
    for name in names:
        for i, h in enumerate(normed):
            if i not in taken and h == name:
                return i
    for name in names:
        for i, h in enumerate(normed):
            if i not in taken and name in h and len(name) > 3:
                return i
    return None


def _map_headers(headers: list[str]) -> ColumnMap:
    taken: set[int] = set()
    m = ColumnMap(headers=headers)

    def grab(names):
        i = _find(headers, names, taken)
        if i is not None:
            taken.add(i)
        return i

    m.date = grab(DATE_HEADERS)
    m.amount = grab(AMOUNT_HEADERS)
    if m.amount is None:
        m.debit = grab(DEBIT_HEADERS)
        m.credit = grab(CREDIT_HEADERS)
    m.description = grab(DESC_HEADERS)
    m.type = grab(TYPE_HEADERS)
    m.memo = grab(MEMO_HEADERS)
    return m


def _infer_headerless(rows: list[list[str]]) -> ColumnMap:
    """Guess columns from content when the file has no header row."""
    sample = rows[:30]
    ncols = max(len(r) for r in sample)
    date_score = [0] * ncols
    num_score = [0] * ncols
    text_len = [0] * ncols
    for r in sample:
        for i, cell in enumerate(r):
            if parse_date(cell):
                date_score[i] += 1
            elif parse_money(cell) is not None:
                num_score[i] += 1
            else:
                text_len[i] += len(cell)
    m = ColumnMap()
    m.date = max(range(ncols), key=lambda i: date_score[i]) if any(date_score) else None
    num_cols = [i for i in range(ncols) if num_score[i] >= len(sample) * 0.5 and i != m.date]
    if num_cols:
        # First numeric column is the amount; a later one is usually the running balance.
        m.amount = num_cols[0]
    text_cols = [i for i in range(ncols) if i not in (m.date, m.amount)]
    if text_cols:
        m.description = max(text_cols, key=lambda i: text_len[i])
    return m


def detect_columns(rows: list[list[str]]) -> ColumnMap:
    for idx, row in enumerate(rows[:25]):
        cells = [c for c in row if c.strip()]
        if len(cells) < 2:
            continue
        m = _map_headers(row)
        if m.ok:
            m.header_row = idx
            return m
    return _infer_headerless([r for r in rows if len([c for c in r if c.strip()]) >= 2])


def read_rows(data: bytes | str) -> list[list[str]]:
    if isinstance(data, bytes):
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    else:
        text = data
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]


def read_xlsx_rows(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = []
    for row in ws.iter_rows(values_only=True):
        out = []
        for v in row:
            if v is None:
                out.append("")
            elif hasattr(v, "strftime"):
                out.append(v.strftime("%Y-%m-%d"))
            else:
                out.append(str(v).strip())
        rows.append(out)
    return rows


def parse_rows(rows: list[list[str]], *, dayfirst: bool = False, columns: ColumnMap | None = None
               ) -> tuple[list[ParsedTransaction], list[str]]:
    """Return (transactions, warnings)."""
    m = columns or detect_columns(rows)
    warnings: list[str] = []
    if not m.ok:
        raise ValueError("Could not find date, description and amount columns in this file.")
    start = (m.header_row + 1) if m.header_row is not None else 0
    out: list[ParsedTransaction] = []

    def cell(r, i):
        return r[i] if i is not None and i < len(r) else ""

    for n, r in enumerate(rows[start:], start=start + 1):
        if not any(c.strip() for c in r):
            continue
        d = parse_date(cell(r, m.date), dayfirst=dayfirst)
        if d is None:
            continue  # totals / footer lines
        if m.amount is not None:
            amt = parse_money(cell(r, m.amount))
            if amt is None:
                warnings.append(f"Row {n}: unreadable amount {cell(r, m.amount)!r}")
                continue
            t = cell(r, m.type).lower()
            if t in ("debit", "dr", "withdrawal", "d") and amt > 0:
                amt = -amt
            elif t in ("credit", "cr", "deposit", "c") and amt < 0:
                amt = -amt
        else:
            debit = parse_money(cell(r, m.debit)) or 0
            credit = parse_money(cell(r, m.credit)) or 0
            amt = abs(credit) - abs(debit)
        desc = cell(r, m.description) or "(no description)"
        memo = cell(r, m.memo) if m.memo is not None and m.memo != m.description else ""
        out.append(ParsedTransaction(d, desc, amt, memo))
    return out, warnings
