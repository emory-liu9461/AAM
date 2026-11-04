"""Convert a PDF bank statement into a formatted Excel workbook.

The workbook has three sheets:

* **Transactions** – Date, Description, Money In, Money Out, Amount, with a
  totals row (live formulas), filters and frozen headers.
* **Summary** – source file, statement period, counts, totals, and any
  warnings from the reader.
* **Extracted Text** – the raw text read from the PDF, line by line, so any
  row can be checked against the original.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .books import ParsedTransaction
from .importers.pdf_importer import extract_text, parse_statement_text

MONEY_FMT = '#,##0.00;[Red]-#,##0.00'


@dataclass
class ConversionResult:
    pdf_path: Path
    xlsx_path: Path
    transactions: list[ParsedTransaction] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.transactions)


def unique_path(path: Path) -> Path:
    """``report.xlsx`` -> ``report (2).xlsx`` if the name is taken."""
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def write_workbook(transactions: list[ParsedTransaction], out_path: Path, *, source_name: str = "",
                   warnings: list[str] | None = None, raw_text: str = "") -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    warnings = warnings or []
    wb = Workbook()
    ws = wb.active
    ws.title = "Transactions"
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    header_font = Font(bold=True, color="FFFFFF")
    headers = ["Date", "Description", "Money In", "Money Out", "Amount"]
    ws.append(headers)
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = header_fill, header_font
        cell.alignment = Alignment(horizontal="center" if c != 2 else "left")

    for tx in transactions:
        amt = tx.amount_cents / 100
        ws.append([tx.date, tx.description, amt if amt > 0 else None, -amt if amt < 0 else None, amt])
    last = ws.max_row
    for r in range(2, last + 1):
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        for c in (3, 4, 5):
            ws.cell(row=r, column=c).number_format = MONEY_FMT

    total_row = last + 1
    ws.cell(row=total_row, column=2, value="Total").font = Font(bold=True)
    top = Border(top=Side(style="thin"), bottom=Side(style="double"))
    for c in (3, 4, 5):
        col = get_column_letter(c)
        cell = ws.cell(row=total_row, column=c, value=f"=SUM({col}2:{col}{last})" if last >= 2 else 0)
        cell.number_format = MONEY_FMT
        cell.font = Font(bold=True)
        cell.border = top
    for col, width in zip("ABCDE", (12, 60, 15, 15, 15)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    if last >= 2:
        ws.auto_filter.ref = f"A1:E{last}"

    # Summary
    s = wb.create_sheet("Summary")
    money_in = sum(t.amount_cents for t in transactions if t.amount_cents > 0) / 100
    money_out = -sum(t.amount_cents for t in transactions if t.amount_cents < 0) / 100
    rows = [
        ("Source file", source_name),
        ("Converted", datetime.now().strftime("%Y-%m-%d %H:%M")),
        ("First transaction", transactions[0].date if transactions else None),
        ("Last transaction", transactions[-1].date if transactions else None),
        ("Transactions", len(transactions)),
        ("Money in", money_in),
        ("Money out", money_out),
        ("Net change", money_in - money_out),
    ]
    for label, value in rows:
        s.append([label, value])
    for r in range(1, s.max_row + 1):
        s.cell(row=r, column=1).font = Font(bold=True)
    for r in (3, 4):
        s.cell(row=r, column=2).number_format = "yyyy-mm-dd"
    for r in (6, 7, 8):
        s.cell(row=r, column=2).number_format = MONEY_FMT
    s.append([])
    s.append(["Please review", "Signs (in/out) and years are inferred from the PDF layout. "
              "Compare totals with the statement before relying on them."])
    for w in warnings:
        s.append(["Warning", w])
    s.column_dimensions["A"].width = 20
    s.column_dimensions["B"].width = 80

    # Raw text for auditing
    t = wb.create_sheet("Extracted Text")
    t.append(["Line", "Text"])
    t["A1"].font = t["B1"].font = Font(bold=True)
    for i, line in enumerate(raw_text.splitlines(), start=1):
        t.append([i, line])
    t.column_dimensions["B"].width = 110

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)


def convert_pdf_to_excel(pdf_path: str | Path, out_path: str | Path | None = None, *,
                         dayfirst: bool = False, overwrite: bool = False) -> ConversionResult:
    """Convert one PDF statement. Output defaults to the same folder and name with ``.xlsx``."""
    pdf_path = Path(pdf_path)
    out = Path(out_path) if out_path else pdf_path.with_suffix(".xlsx")
    if out.is_dir():
        out = out / f"{pdf_path.stem}.xlsx"
    if not overwrite:
        out = unique_path(out)
    text = extract_text(pdf_path.read_bytes())
    txs, warnings = parse_statement_text(text, dayfirst=dayfirst)
    txs.sort(key=lambda t: t.date)
    write_workbook(txs, out, source_name=pdf_path.name, warnings=warnings, raw_text=text)
    return ConversionResult(pdf_path, out, txs, warnings)


# ---------------------------------------------------------------------------
# One combined workbook for many PDF statements
# ---------------------------------------------------------------------------

COMBINED_NAME = "Statements (converted from PDF).xlsx"
ALL_SHEET = "All Transactions"
SUMMARY_SHEET = "Summary"
SUMMARY_COLUMNS = ["Sheet", "Source file", "Bank account", "First date", "Last date", "Transactions",
                   "Money in", "Money out", "Net change", "Added"]
_RESERVED_SHEETS = {ALL_SHEET.lower(), SUMMARY_SHEET.lower()}


def _sheet_name(stem: str, taken: set[str]) -> str:
    import re

    base = re.sub(r"[\[\]:*?/\\]", " ", stem).strip().strip("'")[:28] or "Statement"
    name, n = base, 2
    while name.lower() in taken or name.lower() in _RESERVED_SHEETS:
        name = f"{base[:25]} ({n})"
        n += 1
    return name


def _style_header(ws, ncols: int) -> None:
    from openpyxl.styles import Alignment, Font, PatternFill

    fill, font = PatternFill("solid", fgColor="1F3A5F"), Font(bold=True, color="FFFFFF")
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.fill, cell.font = fill, font
        cell.alignment = Alignment(horizontal="left" if c == 2 else "center")


def _totals_row(ws, last_row: int, money_cols: tuple[int, ...], label_col: int = 2) -> None:
    from openpyxl.styles import Border, Font, Side
    from openpyxl.utils import get_column_letter

    row = last_row + 1
    ws.cell(row=row, column=label_col, value="Total").font = Font(bold=True)
    border = Border(top=Side(style="thin"), bottom=Side(style="double"))
    for c in money_cols:
        col = get_column_letter(c)
        cell = ws.cell(row=row, column=c, value=f"=SUM({col}2:{col}{last_row})" if last_row >= 2 else 0)
        cell.number_format, cell.font, cell.border = MONEY_FMT, Font(bold=True), border


def _write_statement_sheet(ws, transactions: list[ParsedTransaction]) -> None:
    headers = ["Date", "Description", "Money In", "Money Out", "Amount"]
    ws.append(headers)
    _style_header(ws, len(headers))
    for tx in sorted(transactions, key=lambda t: t.date):
        amt = tx.amount_cents / 100
        ws.append([tx.date, tx.description, amt if amt > 0 else None, -amt if amt < 0 else None, amt])
    last = ws.max_row
    for r in range(2, last + 1):
        ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        for c in (3, 4, 5):
            ws.cell(row=r, column=c).number_format = MONEY_FMT
    _totals_row(ws, last, (3, 4, 5))
    for col, width in zip("ABCDE", (12, 60, 15, 15, 15)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    if last >= 2:
        ws.auto_filter.ref = f"A1:E{last}"


def _read_statement_sheet(ws) -> list[tuple]:
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r[1] == "Total" or r[0] is None:
            continue
        d = r[0].date() if isinstance(r[0], datetime) else r[0]  # Excel hands dates back as datetimes
        rows.append((d, *r[1:5]))
    return rows


def add_to_combined_workbook(workbook: str | Path, source_name: str, transactions: list[ParsedTransaction], *,
                             account: str = "") -> Path:
    """Add (or replace) one statement in a combined workbook and rebuild its overview sheets.

    Returns the path written. If the workbook is open in Excel (locked), a copy
    named ``... (2).xlsx`` is written instead so nothing is lost.
    """
    from openpyxl import Workbook, load_workbook

    path = Path(workbook)
    wb = load_workbook(path) if path.exists() else Workbook()
    if not path.exists():
        wb.remove(wb.active)
    entries = _entries(wb)

    # Statements saved to "… (2).xlsx" copies while the workbook was open in Excel are merged back in,
    # so the main workbook always ends up with everything.
    pending = sorted(p for p in path.parent.glob(f"{path.stem} (*){path.suffix}") if p != path)
    for copy in pending:
        try:
            other = load_workbook(copy)
        except Exception:  # noqa: BLE001 - unreadable copy: leave it alone
            continue
        for key, e in _entries(other).items():
            if key not in entries or str(e[9] or "") > str(entries[key][9] or ""):
                rows = _read_statement_sheet(other[e[0]])
                txs = [ParsedTransaction(r[0], r[1] or "", round((r[4] or 0) * 100)) for r in rows]
                _put_statement(wb, entries, key[0], key[1], txs, added=str(e[9] or ""))

    _put_statement(wb, entries, source_name, account, transactions)

    ordered = sorted(entries.values(), key=lambda e: (str(e[2]), str(e[3] or "")))

    # Rebuild "All Transactions" from every statement sheet
    if ALL_SHEET in wb.sheetnames:
        del wb[ALL_SHEET]
    all_ws = wb.create_sheet(ALL_SHEET, 0)
    headers = ["Date", "Description", "Money In", "Money Out", "Amount", "Bank Account", "Statement"]
    all_ws.append(headers)
    _style_header(all_ws, len(headers))
    combined = []
    for e in ordered:
        for row in _read_statement_sheet(wb[e[0]]):
            combined.append((*row, e[2], e[1]))
    combined.sort(key=lambda r: str(r[0]))
    for row in combined:
        all_ws.append(list(row))
    last = all_ws.max_row
    for r in range(2, last + 1):
        all_ws.cell(row=r, column=1).number_format = "yyyy-mm-dd"
        for c in (3, 4, 5):
            all_ws.cell(row=r, column=c).number_format = MONEY_FMT
    _totals_row(all_ws, last, (3, 4, 5))
    for col, width in zip("ABCDEFG", (12, 55, 15, 15, 15, 24, 34)):
        all_ws.column_dimensions[col].width = width
    all_ws.freeze_panes = "A2"
    if last >= 2:
        all_ws.auto_filter.ref = f"A1:G{last}"

    # Rebuild "Summary"
    if SUMMARY_SHEET in wb.sheetnames:
        del wb[SUMMARY_SHEET]
    sm = wb.create_sheet(SUMMARY_SHEET, 1)
    sm.append(SUMMARY_COLUMNS)
    _style_header(sm, len(SUMMARY_COLUMNS))
    for e in ordered:
        sm.append(e)
    for r in range(2, sm.max_row + 1):
        for c in (4, 5):
            sm.cell(row=r, column=c).number_format = "yyyy-mm-dd"
        for c in (7, 8, 9):
            sm.cell(row=r, column=c).number_format = MONEY_FMT
    for col, width in zip("ABCDEFGHIJ", (28, 38, 24, 12, 12, 13, 15, 15, 15, 17)):
        sm.column_dimensions[col].width = width
    sm.freeze_panes = "A2"

    # Statement sheets after the overview, oldest first
    order = [ALL_SHEET, SUMMARY_SHEET] + [e[0] for e in ordered]
    wb._sheets = [wb[n] for n in order]
    wb.active = 0

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:  # open in Excel: save a copy now, merged back on the next import
        alt = unique_path(path)
        wb.save(alt)
        return alt
    for copy in pending:
        copy.unlink(missing_ok=True)
    return path


def _entries(wb) -> dict[tuple[str, str], list]:
    """Statements listed on the Summary sheet: (source file, account) -> summary row."""
    out: dict[tuple[str, str], list] = {}
    if SUMMARY_SHEET in wb.sheetnames:
        for r in wb[SUMMARY_SHEET].iter_rows(min_row=2, values_only=True):
            if r and r[0] and r[0] in wb.sheetnames:
                out[(str(r[1]), str(r[2] or ""))] = list(r) + [None] * (len(SUMMARY_COLUMNS) - len(r))
    return out


def _put_statement(wb, entries: dict, source_name: str, account: str, transactions: list[ParsedTransaction],
                   *, added: str | None = None) -> None:
    key = (source_name, account)
    if key in entries and entries[key][0] in wb.sheetnames:  # re-imported: replace that statement's sheet
        name = entries[key][0]
        del wb[name]
    else:
        name = _sheet_name(Path(source_name).stem, {s.lower() for s in wb.sheetnames})
    _write_statement_sheet(wb.create_sheet(name), transactions)
    dates = [t.date for t in transactions]
    money_in = sum(t.amount_cents for t in transactions if t.amount_cents > 0) / 100
    money_out = -sum(t.amount_cents for t in transactions if t.amount_cents < 0) / 100
    entries[key] = [name, source_name, account, min(dates) if dates else None, max(dates) if dates else None,
                    len(transactions), money_in, money_out, money_in - money_out,
                    added or datetime.now().strftime("%Y-%m-%d %H:%M")]
