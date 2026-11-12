"""Your own information, apart from any deal: a personal folder (net worth statements, T1 returns, T4 slips, notices
of assessment, ID, credit reports …) and your personal net worth statement.

**The personal folder** is one folder you choose (kept in the portfolio file). :data:`LAYOUT` is what goes in it;
:func:`create` makes the folders that aren't there yet. Lender packages pick their borrower / guarantor documents
from it.

**The net worth statement** lists:

* real estate from the portfolio — each active holding at its market value (equity value + debt, 100%) with its
  mortgages and other loans (100%), times **your share** (the holding's ownership by default; change it when you
  own only part of the holding company);
* your other assets (cash, investments, RRSP, TFSA, your home, vehicles …) and liabilities (your home's mortgage,
  lines of credit, credit cards …), entered by you and kept for next time;

:func:`statement` works it out; the dialog saves it as a PDF and an Excel file in the folder you pick for statements
(the personal folder's Net Worth Statements to start with); the earlier ones move to its Archive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import longpath as lpth

NET_WORTH = "Net Worth Statements"
ARCHIVE = "Archive"

# (folder, subfolders, what goes in it)
LAYOUT: list[tuple[str, list[str], str]] = [
    (NET_WORTH, [ARCHIVE], "The latest statement here, older ones in Archive (made by Net Worth Statement)"),
    ("Income Tax", ["T1 Returns", "T4 Slips", "Notices of Assessment (NOA)", "T5 and Other Slips"],
     "Name them by year, e.g. “2025 T1 General”, “2025 NOA”"),
    ("Identification", [], "Driver's licence, passport"),
    ("Credit Reports", [], ""),
    ("Bank and Investment Statements", [], "Personal accounts, RRSP, TFSA"),
    ("Real Estate Owned", [], "Your own home and anything not in the portfolio"),
]

ASSET_KINDS = ["Cash and bank accounts", "Investments (non-registered)", "RRSP / RRIF", "TFSA", "Principal residence",
               "Other real estate (not in the portfolio)", "Business interests (not in the portfolio)", "Vehicles",
               "Other assets"]
LIABILITY_KINDS = ["Mortgage on principal residence", "Other mortgages", "Lines of credit", "Credit cards",
                   "Car loans and leases", "Other loans", "Taxes owing"]


# ---- the personal folder ----

def _setting(pf, key: str, default: str = "") -> str:
    r = pf.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r[0] if r and r[0] is not None else default


def _set(pf, key: str, value: str) -> None:
    with pf.conn:
        pf.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, value))


def folder(pf) -> Path | None:
    v = _setting(pf, "personal_folder")
    return Path(v) if v else None


def set_folder(pf, path: str | Path) -> None:
    _set(pf, "personal_folder", str(path))


def statements_folder(pf) -> Path | None:
    """Where net worth statements are saved: the folder you picked for them, else the personal folder's Net Worth
    Statements."""
    v = _setting(pf, "net_worth_folder")
    if v:
        return Path(v)
    f = folder(pf)
    return f / NET_WORTH if f else None


def set_statements_folder(pf, path: str | Path | None) -> None:
    """The folder for net worth statements (None / '' = the personal folder's Net Worth Statements)."""
    _set(pf, "net_worth_folder", str(path) if path else "")


def create(path: Path) -> int:
    """Make the personal folder's folders that aren't there yet (nothing is moved or removed). Returns how many."""
    n = 0
    for name, subs, _note in LAYOUT:
        for p in [path / name] + [path / name / s for s in subs]:
            if not lpth.exists(p):
                lpth.mkdir(p)
                n += 1
    return n


# ---- the net worth statement ----

@dataclass
class Entry:
    kind: str
    description: str = ""
    amount: int = 0           # cents


@dataclass
class Property:
    holding_id: int
    name: str
    location: str = ""
    type: str = ""
    value: int = 0            # market value, 100% (cents)
    debt: int = 0             # mortgages and other loans, 100%
    share_pct: float = 100.0  # your share
    lenders: str = ""

    @property
    def my_value(self) -> int:
        return round(self.value * self.share_pct / 100)

    @property
    def my_debt(self) -> int:
        return round(self.debt * self.share_pct / 100)

    @property
    def my_equity(self) -> int:
        return self.my_value - self.my_debt


@dataclass
class Saved:
    name: str = ""
    assets: list[Entry] = field(default_factory=list)
    liabilities: list[Entry] = field(default_factory=list)
    shares: dict[int, float] = field(default_factory=dict)   # holding id -> your share % (when changed)
    guarantees: str = ""


def load(pf) -> Saved:
    raw = _setting(pf, "net_worth")
    if not raw:
        return Saved()
    d = json.loads(raw)
    return Saved(d.get("name", ""), [Entry(**e) for e in d.get("assets", [])],
                 [Entry(**e) for e in d.get("liabilities", [])],
                 {int(k): float(v) for k, v in d.get("shares", {}).items()}, d.get("guarantees", ""))


def save(pf, s: Saved) -> None:
    _set(pf, "net_worth", json.dumps({
        "name": s.name, "assets": [e.__dict__ for e in s.assets], "liabilities": [e.__dict__ for e in s.liabilities],
        "shares": {str(k): v for k, v in s.shares.items()}, "guarantees": s.guarantees}))


def properties(pf, saved: Saved | None = None, as_of: date | None = None) -> list[Property]:
    """The portfolio's active holdings as real estate owned: market value and debt (100%), and your share."""
    from . import portfolio as pfm

    saved = saved or load(pf)
    as_of = as_of or date.today()
    out = []
    for h in pf.holdings():
        if h.status != "active":
            continue
        p = pfm.performance(pf, h, as_of, with_financials=True)
        loans = [ln for ln in pfm.loans(pf, h, as_of) if not (ln.start_date and ln.start_date > as_of.isoformat())]
        debt = sum(ln.balance or 0 for ln in loans) if loans else (p.debt or 0)
        value = p.company_value if p.company_value is not None else (p.value_100 or 0) + debt
        out.append(Property(h.id, h.name, h.location, h.sector or h.type, value or 0, debt,
                            saved.shares.get(h.id, h.ownership_pct),
                            ", ".join(dict.fromkeys(ln.lender for ln in loans if ln.lender))))
    return out


@dataclass
class Statement:
    name: str
    as_of: date
    properties: list[Property]
    assets: list[Entry]
    liabilities: list[Entry]
    guarantees: str = ""
    loans: list = field(default_factory=list)   # shareholder loans with your companies (shareholder_loans.Position)

    @property
    def loans_to_companies(self) -> int:
        """Your companies owe you (from their books)."""
        return sum(p.owed_to_you for p in self.loans if p.owed_to_you > 0)

    @property
    def owed_to_companies(self) -> int:
        """You owe your companies (from their books)."""
        return -sum(p.owed_to_you for p in self.loans if p.owed_to_you < 0)

    @property
    def real_estate(self) -> int:
        return sum(p.my_value for p in self.properties)

    @property
    def real_estate_debt(self) -> int:
        return sum(p.my_debt for p in self.properties)

    @property
    def total_assets(self) -> int:
        return self.real_estate + self.loans_to_companies + sum(e.amount for e in self.assets)

    @property
    def total_liabilities(self) -> int:
        return self.real_estate_debt + self.owed_to_companies + sum(e.amount for e in self.liabilities)

    @property
    def net_worth(self) -> int:
        return self.total_assets - self.total_liabilities


def statement(pf, saved: Saved | None = None, as_of: date | None = None,
              props: list[Property] | None = None, loans: list | None = None) -> Statement:
    saved = saved or load(pf)
    as_of = as_of or date.today()
    if loans is None:
        from . import shareholder_loans as shl

        try:
            loans = [p for p in shl.positions(pf, as_of) if p.owed_to_you]
        except Exception:  # noqa: BLE001 — a company file that can't be read: left out
            loans = []
    return Statement(saved.name, as_of, props if props is not None else properties(pf, saved, as_of),
                     [e for e in saved.assets if e.amount], [e for e in saved.liabilities if e.amount],
                     saved.guarantees, loans)


# ---- history: a snapshot per day you update it ----

CASH_KINDS = {"Cash and bank accounts"}
REAL_ESTATE = "Real estate (your share)"
LOANS_TO = "Shareholder loans to your companies"


@dataclass
class Snapshot:
    date: str
    assets: int
    liabilities: int
    net_worth: int
    cash: int
    by_kind: dict[str, int] = field(default_factory=dict)   # assets by type


def snapshot(st: Statement) -> Snapshot:
    by_kind: dict[str, int] = {}
    if st.properties:
        by_kind[REAL_ESTATE] = st.real_estate
    if st.loans_to_companies:
        by_kind[LOANS_TO] = st.loans_to_companies
    for e in st.assets:
        by_kind[e.kind] = by_kind.get(e.kind, 0) + e.amount
    return Snapshot(st.as_of.isoformat(), st.total_assets, st.total_liabilities, st.net_worth,
                    sum(e.amount for e in st.assets if e.kind in CASH_KINDS), by_kind)


def history(pf) -> list[Snapshot]:
    raw = _setting(pf, "net_worth_history")
    return sorted((Snapshot(**d) for d in json.loads(raw)), key=lambda x: x.date) if raw else []


def record(pf, st: Statement) -> Snapshot:
    """Keep today's figures (one snapshot a day: the latest replaces the day's earlier one)."""
    snap = snapshot(st)
    hist = {h.date: h for h in history(pf)}
    hist[snap.date] = snap
    _set(pf, "net_worth_history", json.dumps([h.__dict__ for h in sorted(hist.values(), key=lambda x: x.date)]))
    return snap


def _m(cents: int) -> str:
    from .money import fmt_money

    return fmt_money(cents)


def html(st: Statement) -> str:
    """The statement as a printable page."""
    import html as h

    esc = h.escape
    td, tr_ = "padding:3px 8px", "border-top:1px solid #999"

    def row(label, amount, *, bold=False, indent=0):
        w = "font-weight:bold;" if bold else ""
        return (f"<tr><td style='{td};{w}padding-left:{8 + indent}px'>{esc(label)}</td>"
                f"<td align='right' width='22%' style='{td};{w}white-space:nowrap'>{_m(amount)}</td></tr>")

    out = [f"<h2 style='margin-bottom:0'>Personal Net Worth Statement</h2>",
           f"<p style='margin-top:4px'><b>{esc(st.name or '')}</b><br>As of {st.as_of:%B} {st.as_of.day}, {st.as_of.year}</p>",
           "<table cellspacing='0' width='100%'>",
           "<tr><td colspan='2' style='padding-top:10px'><b>ASSETS</b></td></tr>"]
    for e in st.assets:
        out.append(row(e.kind + (f" — {e.description}" if e.description else ""), e.amount, indent=8))
    for p in st.loans:
        if p.owed_to_you > 0:
            out.append(row(f"Shareholder loan to {p.company}", p.owed_to_you, indent=8))
    if st.properties:
        out.append(row("Real estate owned (your share of market value — see schedule)", st.real_estate, indent=8))
    out.append(f"<tr><td style='{td};{tr_}'><b>Total assets</b></td><td align='right' style='{td};{tr_}'>"
               f"<b style='white-space:nowrap'>{_m(st.total_assets)}</b></td></tr>")
    out.append("<tr><td colspan='2' style='padding-top:14px'><b>LIABILITIES</b></td></tr>")
    for e in st.liabilities:
        out.append(row(e.kind + (f" — {e.description}" if e.description else ""), e.amount, indent=8))
    for p in st.loans:
        if p.owed_to_you < 0:
            out.append(row(f"Owed to {p.company} (shareholder loan)", -p.owed_to_you, indent=8))
    if st.properties:
        out.append(row("Mortgages on real estate owned (your share — see schedule)", st.real_estate_debt, indent=8))
    out.append(f"<tr><td style='{td};{tr_}'><b>Total liabilities</b></td><td align='right' style='{td};{tr_}'>"
               f"<b style='white-space:nowrap'>{_m(st.total_liabilities)}</b></td></tr>")
    out.append(f"<tr><td style='{td};{tr_};padding-top:10px'><b>NET WORTH</b></td><td align='right' "
               f"style='{td};{tr_};padding-top:10px;white-space:nowrap'><b>{_m(st.net_worth)}</b></td></tr></table>")
    if st.properties:
        cols = ["Property", "Location", "Value (100%)", "Debt (100%)", "Lender(s)", "Share", "Your equity"]
        out.append("<h3 style='margin-top:18px'>Schedule of Real Estate Owned</h3><table cellspacing='0' "
                   "width='100%'><tr>" + "".join(f"<td style='{td};border-bottom:1px solid #999'><b>{c}</b></td>"
                                                 for c in cols) + "</tr>")
        for p in st.properties:
            out.append("<tr>" + "".join(f"<td style='{td}{';white-space:nowrap' if k in (2, 3, 5, 6) else ''}'"
                                        f"{' align=right' if k in (2, 3, 5, 6) else ''}>{v}</td>"
                                        for k, v in enumerate([esc(p.name), esc(p.location),
                                                               _m(p.value), _m(p.debt), esc(p.lenders),
                                                               f"{p.share_pct:g}%", _m(p.my_equity)])) + "</tr>")
        out.append(f"<tr><td colspan='6' style='{td};{tr_}'><b>Total</b></td><td align='right' style='{td};{tr_};white-space:nowrap'>"
                   f"<b>{_m(st.real_estate - st.real_estate_debt)}</b></td></tr></table>")
    out.append("<p style='margin-top:24px'>I certify that the information above is true and complete as of the date "
               "shown.</p><p style='margin-top:30px'>Signature: ______________________________ &nbsp;&nbsp; "
               "Date: ________________</p>")
    return "".join(out)


def write_xlsx(st: Statement, path: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    bold = Font(bold=True)
    money = '#,##0.00;(#,##0.00)'
    wb = Workbook()
    ws = wb.active
    ws.title = "Net Worth"
    ws.append(["Personal Net Worth Statement"])
    ws["A1"].font = Font(bold=True, size=14)
    ws.append([st.name])
    ws.append([f"As of {st.as_of.isoformat()}"])
    ws.append([])

    def put(label, cents=None, *, b=False):
        ws.append([label, None if cents is None else cents / 100])
        r = ws.max_row
        if b:
            ws.cell(r, 1).font = bold
            ws.cell(r, 2).font = bold
        ws.cell(r, 2).number_format = money

    put("ASSETS", b=True)
    for e in st.assets:
        put(e.kind + (f" — {e.description}" if e.description else ""), e.amount)
    for p in st.loans:
        if p.owed_to_you > 0:
            put(f"Shareholder loan to {p.company}", p.owed_to_you)
    if st.properties:
        put("Real estate owned (your share of market value)", st.real_estate)
    put("Total assets", st.total_assets, b=True)
    ws.append([])
    put("LIABILITIES", b=True)
    for e in st.liabilities:
        put(e.kind + (f" — {e.description}" if e.description else ""), e.amount)
    for p in st.loans:
        if p.owed_to_you < 0:
            put(f"Owed to {p.company} (shareholder loan)", -p.owed_to_you)
    if st.properties:
        put("Mortgages on real estate owned (your share)", st.real_estate_debt)
    put("Total liabilities", st.total_liabilities, b=True)
    ws.append([])
    put("NET WORTH", st.net_worth, b=True)
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 18
    if st.properties:
        s = wb.create_sheet("Real Estate Owned")
        s.append(["Property", "Location", "Type", "Market value (100%)", "Mortgages (100%)", "Lender(s)",
                  "Your share %", "Your market value", "Your mortgages", "Your equity"])
        for c in s[1]:
            c.font = bold
        for p in st.properties:
            s.append([p.name, p.location, p.type, p.value / 100, p.debt / 100, p.lenders, p.share_pct,
                      p.my_value / 100, p.my_debt / 100, p.my_equity / 100])
        for row in s.iter_rows(min_row=2):
            for c in (row[3], row[4], row[7], row[8], row[9]):
                c.number_format = money
        for col, w in zip("ABCDEFGHIJ", (28, 22, 14, 18, 18, 22, 12, 18, 18, 18)):
            s.column_dimensions[col].width = w
    wb.save(lpth.lp(path))


def file_name(st: Statement, ext: str) -> str:
    who = f" - {st.name.strip()}" if st.name.strip() else ""
    return f"{st.as_of.isoformat()} Net Worth Statement{who}.{ext}"


def archive_older(nw: Path, keep: list[Path]) -> int:
    """Move every earlier statement in the statements folder into its Archive (the new ones stay). Returns how
    many."""
    arch = nw / ARCHIVE
    keep_names = {p.name for p in keep}
    n = 0
    for p in lpth.listdir(nw) if lpth.exists(nw) else []:
        if lpth.is_file(p) and p.name not in keep_names and not p.name.startswith("~$"):
            lpth.mkdir(arch)
            dst = arch / p.name
            k = 2
            while lpth.exists(dst):
                dst = arch / f"{p.stem} ({k}){p.suffix}"
                k += 1
            lpth.move(p, dst)
            n += 1
    return n
