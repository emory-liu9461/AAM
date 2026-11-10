"""Year-end package for the accountant: one folder with everything for the fiscal year.

::

    Year-End FY2026 - <company>/
        01 Trial Balance.xlsx            (trial_balance.py: opening, activity, closing; formulas)
        02 General Ledger.xlsx           every entry in every account, with running balances
        03 Income Statement.xlsx
        04 Balance Sheet.xlsx
        05 AR & AP at year end.xlsx      (arap.py: every open item and its age)
        06 Fixed Assets & CCA.xlsx       cost continuity by fixed-asset account, and a draft CCA schedule
        07 GST-HST.xlsx                  each filing period's figures (hst.py)
        Read Me.txt                      what's in it, and checks: balanced, uncategorized, unnamed …

**Draft CCA** (capital cost allowance): each fixed-asset account is given a CCA class (guessed from its name —
buildings Class 1 at 4%, furniture and appliances Class 8 at 20%, vehicles Class 10 at 30%, computers Class 50 at 55%,
paving Class 17 at 8%; land none — and can be changed). For each class: opening UCC (last year's closing UCC as worked
out here, or entered), additions and disposals from the books, the first-year rule (half-year rule, or the
Accelerated Investment Incentive's suspension of it for property available for use before 2028), CCA and closing
UCC. It's a draft for the accountant to confirm.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

CCA_CLASSES: dict[str, tuple[float, str]] = {
    "1": (0.04, "Buildings (Class 1)"),
    "1-NR": (0.06, "Non-residential buildings (Class 1, 6%)"),
    "3": (0.05, "Buildings before 1988 (Class 3)"),
    "6": (0.10, "Frame / wooden buildings (Class 6)"),
    "8": (0.20, "Furniture, appliances, equipment (Class 8)"),
    "10": (0.30, "Vehicles (Class 10)"),
    "13": (0.0, "Leasehold improvements (Class 13, straight line — enter by hand)"),
    "17": (0.08, "Paving, parking lots (Class 17)"),
    "43.1": (0.30, "Clean energy equipment (Class 43.1)"),
    "50": (0.55, "Computers (Class 50)"),
    "none": (0.0, "Not depreciable (land)"),
}
GUESS = [(r"\bland\b", "none"), (r"computer|laptop|server|software", "50"),
         (r"vehicle|truck|car\b|automobile|van\b", "10"), (r"paving|parking lot|asphalt", "17"),
         (r"leasehold", "13"), (r"furniture|appliance|equipment|fixture|tool|machinery|fridge|stove|washer|dryer", "8"),
         (r"solar|geothermal|heat pump", "43.1"),
         (r"building|property|renovation|roof|in-suite|suite|unit|hvac|boiler|elevator|window|brick|mechanical"
          r"|improvement|purchase price|land transfer|legal|closing|appraisal|consultant", "1")]


def guess_class(name: str) -> str:
    n = name.lower()
    for pat, cls in GUESS:
        if re.search(pat, n):
            return cls
    return "8"


def fiscal_year(books, year_end: date) -> tuple[date, date]:
    """The fiscal year ending in the month of ``year_end``."""
    m = books.fiscal_year_start_month
    start_year = year_end.year if year_end.month >= m else year_end.year - 1
    start = date(start_year, m, 1)
    end = date(start_year + 1, m, 1) - timedelta(days=1)
    return start, end


def last_fiscal_year(books, today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start, end = fiscal_year(books, today)
    if end >= today:
        start, end = fiscal_year(books, start - timedelta(days=1))
    return start, end


# ---- fixed assets & CCA ----

@dataclass
class AssetRow:
    account_id: int
    code: str
    name: str
    cca_class: str
    opening: int                 # cost at the start of the year
    additions: int
    disposals: int
    accumulated: bool = False    # an accumulated depreciation account (shown, not in CCA)

    @property
    def closing(self) -> int:
        return self.opening + self.additions - self.disposals


@dataclass
class CcaRow:
    cca_class: str
    rate: float
    opening_ucc: int
    additions: int
    disposals: int
    first_year: str              # "half-year" | "aiip" | "none"
    ucc_entered: bool = False    # opening UCC entered (or from last year) rather than assumed

    @property
    def net_additions(self) -> int:
        return self.additions - self.disposals

    @property
    def base(self) -> int:
        net = self.net_additions
        if net > 0 and self.first_year == "half-year":
            return self.opening_ucc + net - net // 2
        return self.opening_ucc + net

    @property
    def cca(self) -> int:
        return max(0, round(self.base * self.rate))

    @property
    def closing_ucc(self) -> int:
        return self.opening_ucc + self.net_additions - self.cca

    @property
    def label(self) -> str:
        return CCA_CLASSES.get(self.cca_class, (0.0, f"Class {self.cca_class}"))[1]


def class_map(books) -> dict[int, str]:
    raw = books._meta("cca_classes")
    return {int(k): v for k, v in json.loads(raw).items()} if raw else {}


def save_class_map(books, m: dict[int, str]) -> None:
    with books.conn:
        books._set_meta("cca_classes", json.dumps({str(k): v for k, v in m.items()}))


def saved_ucc(books, fy_end: date) -> dict[str, int]:
    """Closing UCC by class saved for the fiscal year ending ``fy_end``."""
    raw = books._meta(f"cca_ucc:{fy_end.isoformat()}")
    return json.loads(raw) if raw else {}


def save_ucc(books, fy_end: date, ucc: dict[str, int]) -> None:
    with books.conn:
        books._set_meta(f"cca_ucc:{fy_end.isoformat()}", json.dumps(ucc))


def asset_rows(books, start: date, end: date) -> list[AssetRow]:
    m = class_map(books)
    out = []
    for a in books.accounts():
        if a.type != "asset" or a.subtype != "fixed_asset":
            continue
        accum = bool(re.search(r"accumulated|depreciation|amortization", a.name, re.I))
        before = books.conn.execute(
            "SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l JOIN journal_entries e "
            "ON e.id = l.entry_id WHERE l.account_id=? AND e.date < ?", (a.id, start.isoformat())).fetchone()[0]
        d, c = books.conn.execute(
            "SELECT COALESCE(SUM(l.debit_cents), 0), COALESCE(SUM(l.credit_cents), 0) FROM journal_lines l "
            "JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id=? AND e.date >= ? AND e.date <= ?",
            (a.id, start.isoformat(), end.isoformat())).fetchone()
        if not (before or d or c):
            continue
        out.append(AssetRow(a.id, a.code, a.name, "" if accum else m.get(a.id, guess_class(a.name)), before, d, c,
                            accum))
    return out


def first_year_rule(fy_end: date) -> str:
    """The Accelerated Investment Incentive suspends the half-year rule for property available for use before 2028
    (and enhances it before 2024); otherwise the half-year rule."""
    return "aiip" if fy_end.year <= 2027 else "half-year"


def cca_schedule(books, start: date, end: date, *, rule: str | None = None,
                 opening_ucc: dict[str, int] | None = None) -> list[CcaRow]:
    rows = [r for r in asset_rows(books, start, end) if not r.accumulated and r.cca_class != "none"]
    prev = saved_ucc(books, start - timedelta(days=1))
    opening_ucc = opening_ucc or {}
    rule = rule or first_year_rule(end)
    out: dict[str, CcaRow] = {}
    for r in rows:
        rate = CCA_CLASSES.get(r.cca_class, (0.0, ""))[0]
        c = out.setdefault(r.cca_class, CcaRow(r.cca_class, rate, 0, 0, 0, rule))
        c.additions += r.additions
        c.disposals += r.disposals
        c.opening_ucc += r.opening          # assumed: no CCA claimed before (replaced below when known)
    for cls, c in out.items():
        if cls in opening_ucc:
            c.opening_ucc, c.ucc_entered = opening_ucc[cls], True
        elif cls in prev:
            c.opening_ucc, c.ucc_entered = prev[cls], True
    return sorted(out.values(), key=lambda c: (float(re.sub(r"[^0-9.]", "", c.cca_class) or 0), c.cca_class))


# ---- the workbooks ----

def _money(ws, min_row: int, cols: list[int]) -> None:
    fmt = '#,##0.00;[Red](#,##0.00);"-"'
    for row in ws.iter_rows(min_row=min_row):
        for c in row:
            if c.column in cols and isinstance(c.value, (int, float)):
                c.number_format = fmt


def _head(ws, books, title: str, sub: str, heads: list[str]) -> None:
    from openpyxl.styles import Font, PatternFill

    ws.append([books.company_name])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([title])
    ws.append([sub])
    ws.append([])
    ws.append(heads)
    for c in ws[5]:
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor="E8EEF6")


def write_general_ledger(books, path: Path, start: date, end: date) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    from . import parties

    wb = Workbook()
    ws = wb.active
    ws.title = "General Ledger"
    _head(ws, books, "General Ledger", f"{start.isoformat()} to {end.isoformat()}",
          ["Account", "Date", "Description", "Customer / vendor", "Debit", "Credit", "Balance"])
    tx_ids = [r[0] for r in books.conn.execute(
        "SELECT DISTINCT e.transaction_id FROM journal_entries e WHERE e.transaction_id IS NOT NULL AND e.date >= ? "
        "AND e.date <= ?", (start.isoformat(), end.isoformat()))]
    names = parties.names(books, tx_ids) if tx_ids else {}
    for a in books.accounts():
        opening = books.conn.execute(
            "SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l JOIN journal_entries e "
            "ON e.id = l.entry_id WHERE l.account_id=? AND e.date < ?", (a.id, start.isoformat())).fetchone()[0]
        rows = books.conn.execute(
            "SELECT e.date, e.memo, e.kind, e.transaction_id, l.debit_cents, l.credit_cents, l.party FROM journal_lines l "
            "JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id=? AND e.date >= ? AND e.date <= ? "
            "ORDER BY e.date, e.id", (a.id, start.isoformat(), end.isoformat())).fetchall()
        if not rows and not opening:
            continue
        ws.append([f"{a.code} · {a.name}", "", "Opening balance", "", None, None, opening / 100])
        ws.cell(ws.max_row, 1).font = Font(bold=True)
        bal = opening
        for r in rows:
            desc = r["memo"] or {"opening": "Opening balance", "manual": "Journal entry"}.get(r["kind"], "")
            if r["transaction_id"]:
                try:
                    desc = books.transaction(r["transaction_id"]).description
                except KeyError:
                    pass
            who = r["party"] or (names[r["transaction_id"]].name if r["transaction_id"] in names else "")
            bal += r["debit_cents"] - r["credit_cents"]
            ws.append(["", r["date"], desc, who, r["debit_cents"] / 100 or None, r["credit_cents"] / 100 or None,
                       bal / 100])
        ws.append(["", "", "Closing balance", "", None, None, bal / 100])
        ws.cell(ws.max_row, 3).font = Font(bold=True)
        ws.append([])
    _money(ws, 6, [5, 6, 7])
    for col, w in (("A", 34), ("B", 12), ("C", 44), ("D", 26), ("E", 14), ("F", 14), ("G", 16)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A6"
    wb.save(path)
    return path


def write_fixed_assets(books, path: Path, start: date, end: date, cca: list[CcaRow]) -> Path:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Fixed Assets"
    _head(ws, books, "Fixed assets – cost continuity", f"{start.isoformat()} to {end.isoformat()}",
          ["Account", "CCA class", "Cost at start", "Additions", "Disposals / credits", "Cost at end"])
    for r in asset_rows(books, start, end):
        ws.append([f"{r.code} · {r.name}", "—" if r.accumulated else CCA_CLASSES.get(r.cca_class, (0, r.cca_class))[1],
                   r.opening / 100, r.additions / 100, r.disposals / 100, r.closing / 100])
    _money(ws, 6, [3, 4, 5, 6])
    for col, w in (("A", 44), ("B", 40), ("C", 16), ("D", 16), ("E", 18), ("F", 16)):
        ws.column_dimensions[col].width = w
    ws2 = wb.create_sheet("CCA (draft)")
    _head(ws2, books, "Capital cost allowance – DRAFT for the accountant to confirm",
          f"Fiscal year {start.isoformat()} to {end.isoformat()}",
          ["Class", "Rate", "Opening UCC", "Additions", "Disposals", "First-year rule", "CCA", "Closing UCC",
           "Opening UCC from"])
    for c in cca:
        ws2.append([c.label, c.rate, c.opening_ucc / 100, c.additions / 100, c.disposals / 100,
                    {"half-year": "Half-year rule", "aiip": "AII (half-year rule suspended)"}.get(c.first_year,
                                                                                               c.first_year),
                    c.cca / 100, c.closing_ucc / 100,
                    "entered / last year" if c.ucc_entered else "assumed = cost (check)"])
        ws2.cell(ws2.max_row, 2).number_format = "0%"
    if cca:
        n = ws2.max_row
        ws2.append(["Total", None, f"=SUM(C6:C{n})", f"=SUM(D6:D{n})", f"=SUM(E6:E{n})", None, f"=SUM(G6:G{n})",
                    f"=SUM(H6:H{n})"])
    _money(ws2, 6, [3, 4, 5, 7, 8])
    for col, w in (("A", 46), ("B", 8), ("C", 16), ("D", 16), ("E", 14), ("F", 30), ("G", 14), ("H", 16), ("I", 26)):
        ws2.column_dimensions[col].width = w
    wb.save(path)
    return path


@dataclass
class Package:
    folder: Path
    files: list[Path] = field(default_factory=list)
    checks: list[str] = field(default_factory=list)


def checks(books, start: date, end: date) -> list[str]:
    out = []
    out.append("Trial balance: balanced" if books.check_balanced() else "⚠ Trial balance: debits and credits differ")
    n = books.conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE category_id IS NULL AND date >= ? AND date <= ?",
        (start.isoformat(), end.isoformat())).fetchone()[0]
    out.append("Every bank transaction has a category" if not n else
               f"⚠ {n} bank transaction(s) in the year have no category")
    for a in books.accounts():
        if a.name.lower().startswith("uncategorized"):
            bal = books.conn.execute(
                "SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l JOIN journal_entries e "
                "ON e.id = l.entry_id WHERE l.account_id=? AND e.date >= ? AND e.date <= ?",
                (a.id, start.isoformat(), end.isoformat())).fetchone()[0]
            if bal:
                from .money import fmt_money

                out.append(f"⚠ {fmt_money(abs(bal))} in {a.name}")
    return out


def build(books, folder: Path, end: date, *, rule: str | None = None, opening_ucc: dict[str, int] | None = None,
          save_closing_ucc: bool = True) -> Package:
    """Write the package for the fiscal year ending ``end`` into ``folder`` (a subfolder is made)."""
    from . import arap, hst, reports
    from . import trial_balance as tb
    from .companies import safe_filename
    from .workspace import report_to_xlsx

    start, end = fiscal_year(books, end)
    label = f"FY{end.year}" if start.year == end.year else f"FY{start.year}-{str(end.year)[2:]}"
    pkg = Package(Path(folder) / safe_filename(f"Year-End {label} - {books.company_name}"))
    pkg.folder.mkdir(parents=True, exist_ok=True)
    f = pkg.folder
    pkg.files.append(tb.write_xlsx(books, f / "01 Trial Balance.xlsx", end, start))
    pkg.files.append(write_general_ledger(books, f / "02 General Ledger.xlsx", start, end))
    for name, kind in (("03 Income Statement.xlsx", "income"), ("04 Balance Sheet.xlsx", "balance")):
        rep = (reports.income_statement(books, start.isoformat(), end.isoformat()) if kind == "income" else
               reports.balance_sheet(books, end.isoformat(), fiscal_year_start_month=books.fiscal_year_start_month))
        report_to_xlsx(rep, f / name)
        pkg.files.append(f / name)
    pkg.files.append(arap.write_xlsx(books, f / "05 AR & AP at year end.xlsx", end))
    cca = cca_schedule(books, start, end, rule=rule, opening_ucc=opening_ucc)
    pkg.files.append(write_fixed_assets(books, f / "06 Fixed Assets & CCA.xlsx", start, end, cca))
    if save_closing_ucc and cca:
        save_ucc(books, end, {c.cca_class: c.closing_ucc for c in cca})
    if hst.hst_accounts(books):
        hst.write_xlsx(books, f / "07 GST-HST.xlsx", hst.year_returns(books, start, end))
        pkg.files.append(f / "07 GST-HST.xlsx")
    pkg.checks = checks(books, start, end)
    readme = f / "Read Me.txt"
    lines = [f"{books.company_name} — year-end package", f"Fiscal year {start.isoformat()} to {end.isoformat()}",
             f"Made {date.today().isoformat()} with Aedalus Nexus", "", "Contents:"]
    lines += [f"  {p.name}" for p in pkg.files]
    lines += ["", "Checks:"] + [f"  {c}" for c in pkg.checks]
    lines += ["", "The CCA schedule is a draft: classes are guessed from account names (change them in the app) and "
              "opening UCC is last year's closing UCC worked out here, entered by hand, or assumed equal to cost."]
    readme.write_text("\n".join(lines), encoding="utf-8")
    pkg.files.append(readme)
    return pkg
