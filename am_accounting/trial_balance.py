"""Trial balance workbooks, saved to the company folder's Trial Balance/ folder.

::

    <Company folder>/Trial Balance/
        2026/
            Trial Balance - 31 Oct 2026.xlsx
            Trial Balance - 30 Nov 2026.xlsx
        Archive/                      (older than the archive age)

Each workbook has three sheets, with live Excel formulas so it can be worked on:

* **Trial Balance** — every account with a balance: number, name, type, debit,
  credit; totals and a difference check (should be 0.00).
* **Working TB** — opening balance, debits and credits for the period, closing
  balance (formula), and the closing balance split into debit / credit.
* **Summary** — totals by account type (assets, liabilities, equity, income,
  expenses) using SUMIF over the Trial Balance sheet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .books import Books
from .companies import safe_filename

TB_DIR = "Trial Balance"
TYPE_ORDER = ["asset", "liability", "equity", "income", "expense"]
TYPE_LABELS = {"asset": "Assets", "liability": "Liabilities", "equity": "Equity", "income": "Income",
               "expense": "Expenses"}


@dataclass
class TBRow:
    code: str
    name: str
    type: str
    opening: int    # net debit (+) / credit (−) before the period
    debits: int     # period activity
    credits: int

    @property
    def closing(self) -> int:
        return self.opening + self.debits - self.credits


def rows(books: Books, start: str | None, end: str) -> list[TBRow]:
    """Every account with an opening balance or activity, in chart order (type, then number)."""
    def sums(sql_where: str, args: list) -> dict[int, tuple[int, int]]:
        q = ("SELECT l.account_id, COALESCE(SUM(l.debit_cents),0), COALESCE(SUM(l.credit_cents),0) "
             "FROM journal_lines l JOIN journal_entries e ON e.id=l.entry_id WHERE " + sql_where +
             " GROUP BY l.account_id")
        return {a: (d, c) for a, d, c in books.conn.execute(q, args)}

    before = sums("e.date < ?", [start]) if start else {}
    during = sums("e.date >= ? AND e.date <= ?", [start, end]) if start else sums("e.date <= ?", [end])
    out = []
    for a in books.accounts():
        bd, bc = before.get(a.id, (0, 0))
        dd, dc = during.get(a.id, (0, 0))
        r = TBRow(a.code, a.name, a.type, bd - bc, dd, dc)
        if r.opening or r.debits or r.credits:
            out.append(r)
    rank = {t: i for i, t in enumerate(TYPE_ORDER)}
    out.sort(key=lambda r: (rank.get(r.type, 9), r.code))
    return out


def default_start(books: Books, end: date) -> date:
    """Start of the fiscal year that ``end`` falls in (period activity on the Working TB sheet)."""
    fy = books.fiscal_year_start_month
    return date(end.year if end.month >= fy else end.year - 1, fy, 1)


def file_name(end: date) -> str:
    return safe_filename(f"Trial Balance - {end.day} {end:%b %Y}") + ".xlsx"


def folder_path(root: Path, end: date) -> Path:
    return root / TB_DIR / str(end.year)


def write_xlsx(books: Books, path: Path, end: date, start: date | None = None) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    start = start or default_start(books, end)
    data = rows(books, start.isoformat(), end.isoformat())
    money = '#,##0.00;[Red](#,##0.00);"-"'
    bold = Font(bold=True)
    head_fill = PatternFill("solid", fgColor="E8EEF6")
    thin, double = Side(style="thin"), Side(style="double")
    as_of = f"As of {end:%B} {end.day}, {end.year}"

    def title(ws, subtitle: str, ncols: int) -> None:
        ws.append([books.company_name])
        ws.append(["Trial Balance"])
        ws.append([subtitle])
        ws.append([])
        for r in (1, 2):
            ws.cell(row=r, column=1).font = Font(bold=True, size=13)
        ws.cell(row=3, column=1).font = Font(italic=True, color="5B6573")

    def header(ws, labels: list[str]) -> int:
        ws.append(labels)
        r = ws.max_row
        for c in range(1, len(labels) + 1):
            cell = ws.cell(row=r, column=c)
            cell.font = bold
            cell.fill = head_fill
            cell.border = Border(bottom=thin)
            if c > 3:
                cell.alignment = Alignment(horizontal="right")
        return r

    wb = Workbook()

    # 1. Trial Balance
    ws = wb.active
    ws.title = "Trial Balance"
    title(ws, as_of, 5)
    hr = header(ws, ["Account #", "Account", "Type", "Debit", "Credit"])
    first = hr + 1
    for r in data:
        bal = r.closing
        if bal == 0:
            continue
        ws.append([r.code, r.name, TYPE_LABELS.get(r.type, r.type.title()),
                   bal / 100 if bal > 0 else None, -bal / 100 if bal < 0 else None])
    last = ws.max_row
    ws.append(["", "Total", "", f"=ROUND(SUM(D{first}:D{last}),2)", f"=ROUND(SUM(E{first}:E{last}),2)"])
    tot = ws.max_row
    ws.append(["", "Difference (should be 0.00)", "", f"=ROUND(D{tot}-E{tot},2)", None])
    diff = ws.max_row
    for row in ws.iter_rows(min_row=first, max_row=diff, min_col=4, max_col=5):
        for cell in row:
            cell.number_format = money
    for c in range(1, 6):
        ws.cell(row=tot, column=c).font = bold
        if c >= 4:
            ws.cell(row=tot, column=c).border = Border(top=thin, bottom=double)
    ws.cell(row=diff, column=2).font = Font(italic=True, color="5B6573")
    ws.freeze_panes = ws.cell(row=first, column=1)
    ws.auto_filter.ref = f"A{hr}:E{last}"
    for col, w in zip("ABCDE", (11, 42, 12, 16, 16)):
        ws.column_dimensions[col].width = w

    # 2. Working TB (opening, activity, closing)
    wt = wb.create_sheet("Working TB")
    title(wt, f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}", 9)
    hr2 = header(wt, ["Account #", "Account", "Type", "Opening", "Debits", "Credits", "Closing",
                      "Closing Dr", "Closing Cr"])
    f2 = hr2 + 1
    for r in data:
        n = wt.max_row + 1
        wt.append([r.code, r.name, TYPE_LABELS.get(r.type, r.type.title()), r.opening / 100, r.debits / 100,
                   r.credits / 100, f"=ROUND(D{n}+E{n}-F{n},2)", f"=MAX(G{n},0)", f"=MAX(-G{n},0)"])
    l2 = wt.max_row
    wt.append(["", "Total", ""] + [f"=ROUND(SUM({c}{f2}:{c}{l2}),2)" for c in "DEFGHI"])
    t2 = wt.max_row
    for row in wt.iter_rows(min_row=f2, max_row=t2, min_col=4, max_col=9):
        for cell in row:
            cell.number_format = money
    for c in range(1, 10):
        wt.cell(row=t2, column=c).font = bold
        if c >= 4:
            wt.cell(row=t2, column=c).border = Border(top=thin, bottom=double)
    wt.append([])
    wt.append(["", "Opening and closing are net balances: debit positive, credit negative."])
    wt.cell(row=wt.max_row, column=2).font = Font(italic=True, color="5B6573")
    wt.freeze_panes = wt.cell(row=f2, column=1)
    for col, w in zip("ABCDEFGHI", (11, 42, 12, 15, 15, 15, 15, 15, 15)):
        wt.column_dimensions[col].width = w

    # 3. Summary by type
    sm = wb.create_sheet("Summary")
    title(sm, as_of, 3)
    hr3 = header(sm, ["Type", "", "", "Debit", "Credit"])
    rng = f"'Trial Balance'!$C${first}:$C${last}"
    for t in TYPE_ORDER:
        label = TYPE_LABELS[t]
        n = sm.max_row + 1
        sm.append([label, "", "", f"=SUMIF({rng},A{n},'Trial Balance'!$D${first}:$D${last})",
                   f"=SUMIF({rng},A{n},'Trial Balance'!$E${first}:$E${last})"])
    s_last = sm.max_row
    sm.append(["Total", "", "", f"=ROUND(SUM(D{hr3 + 1}:D{s_last}),2)", f"=ROUND(SUM(E{hr3 + 1}:E{s_last}),2)"])
    for row in sm.iter_rows(min_row=hr3 + 1, max_row=sm.max_row, min_col=4, max_col=5):
        for cell in row:
            cell.number_format = money
    for c in range(1, 6):
        sm.cell(row=sm.max_row, column=c).font = bold
    sm.column_dimensions["A"].width = 16
    for col in "DE":
        sm.column_dimensions[col].width = 16

    for sheet in (ws, wt, sm):  # print one page wide
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
    wt.page_setup.orientation = "landscape"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def save_to_folder(books: Books, end: date, start: date | None = None) -> Path | None:
    """Save the trial balance as of ``end`` into <Company folder>/Trial Balance/<year>/. None if no folder."""
    from . import workspace as ws

    cfg = ws.get_config(books)
    if not cfg.enabled:
        return None
    if cfg.deal:                    # the latest outside, earlier ones in Archive/
        ws.archive_live(cfg.dir("tb"))
        return write_xlsx(books, cfg.dir("tb") / file_name(end), end, start)
    return write_xlsx(books, folder_path(cfg.path, end) / file_name(end), end, start)


def last_month_end(today: date | None = None) -> date:
    today = today or date.today()
    return today.replace(day=1) - timedelta(days=1)
