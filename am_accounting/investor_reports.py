"""Quarterly investor reports: one statement per investor covering every company they own part of.

For each company the investor is on the cap table of (Financing & Cap Table), for the quarter:

* **Capital account** — balance at the start of the quarter (paid in − distributions to them before it), money paid
  in during the quarter, distributions during the quarter, balance at the end.
* **Since the start** — total paid in, total distributed, the value of their interest today (the company's equity
  value split through its waterfall — Waterfall (LP / GP) — or pro rata when it has no GP terms), MOIC and IRR.
* **The company's quarter** — revenue, operating expenses and NOI from its books, occupancy from the rent roll,
  debt and equity value.

**Distributions** are read from the company's books: debits to its distribution / dividend accounts (equity accounts
in the "Draws / distributions / dividends" group, or named distribution / dividend), shared among the owners in
proportion to what each had paid in at the time.
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from . import portfolio as pfm
from .money import fmt_money


def quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start = date(year, 3 * (q - 1) + 1, 1)
    end = (date(year + (q == 4), 1 if q == 4 else 3 * q + 1, 1)) - timedelta(days=1)
    return start, end


def last_quarter(today: date | None = None) -> tuple[int, int]:
    """The most recent quarter that has ended."""
    today = today or date.today()
    q = (today.month - 1) // 3
    return (today.year - 1, 4) if q == 0 else (today.year, q)


def book_distributions(company_path: str | Path, end: date) -> list[tuple[str, int]]:
    """[(date, cents)] paid out to the owners to ``end``, from the company's books."""
    if not company_path or not Path(company_path).exists():
        return []
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT e.date, SUM(l.debit_cents - l.credit_cents) FROM journal_lines l "
            "JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id "
            "WHERE a.type = 'equity' AND (a.subtype = 'draw' OR lower(a.name) LIKE '%distribution%' "
            "OR lower(a.name) LIKE '%dividend%') AND e.date <= ? GROUP BY e.date ORDER BY e.date",
            (end.isoformat(),)).fetchall()
    finally:
        conn.close()
    return [(d, c) for d, c in rows if c]


def operating_quarter(company_path: str | Path, start: date, end: date) -> tuple[int, int]:
    """(revenue, operating expenses) in the period from the books (non-operating accounts left out)."""
    from .budget import actuals

    rev = opx = 0
    for year in range(start.year, end.year + 1):
        act, types = actuals(company_path, year)
        for name, months in act.items():
            amt = sum(v for m, v in enumerate(months, 1) if start <= date(year, m, 1) <= end)
            if types[name] == "income":
                rev += amt
            else:
                opx += amt
    return rev, opx


@dataclass
class Position:
    """One investor's interest in one company."""
    company: str
    holding_id: int
    share_class: str
    paid_in: int                 # to the end of the quarter
    pct: float                   # of the capital paid in
    opening: int
    contributed: int             # in the quarter
    distributed: int             # in the quarter
    closing: int
    total_distributed: int
    value: int | None            # their interest today
    irr: float | None = None
    flows: list = field(default_factory=list)      # [(date, signed cents)] from the investor's side

    @property
    def moic(self) -> float | None:
        if not self.paid_in or self.value is None:
            return None
        return (self.total_distributed + self.value) / self.paid_in


@dataclass
class CompanyQuarter:
    name: str
    revenue: int = 0
    opex: int = 0
    occupancy: float | None = None
    units: int = 0
    debt: int = 0
    equity_value: int | None = None

    @property
    def noi(self) -> int:
        return self.revenue - self.opex


@dataclass
class Statement:
    investor: str                # the person
    company: str                 # the company they invest through (may be blank)
    year: int
    quarter: int
    positions: list[Position]
    companies: dict[int, CompanyQuarter]

    @property
    def label(self) -> str:
        return f"{self.investor} ({self.company})" if self.company and self.investor else (self.investor or
                                                                                           self.company)

    @property
    def period(self) -> tuple[date, date]:
        return quarter_bounds(self.year, self.quarter)

    def total(self, attr: str) -> int:
        return sum(getattr(p, attr) or 0 for p in self.positions)

    @property
    def irr(self) -> float | None:
        flows = [f for p in self.positions for f in p.flows]
        if not flows or (self.period[1] - min(d for d, _c in flows)).days < 365:
            return None                        # under a year: an annual rate would mislead
        value = sum(p.value or 0 for p in self.positions)
        if value:
            flows.append((self.period[1], value))
        return pfm.xirr(flows) if flows else None

    @property
    def moic(self) -> float | None:
        paid = self.total("paid_in")
        return (self.total("total_distributed") + self.total("value")) / paid if paid else None


def _key(x: pfm.Shareholder) -> tuple[str, str]:
    return (x.name.strip().lower(), x.company.strip().lower())


def statements(pf, year: int, quarter: int, holding_ids: list[int] | None = None) -> list[Statement]:
    """A statement for every investor on the cap tables of these holdings (all active ones when None)."""
    from . import operations as op

    start, end = quarter_bounds(year, quarter)
    hs = [h for h in pf.holdings() if (holding_ids is None and h.status == "active")
          or (holding_ids is not None and h.id in holding_ids)]
    out: dict[tuple[str, str], Statement] = {}
    for h in hs:
        p = pfm.performance(pf, h, end, with_financials=True)
        holders = [x for x in pfm.cap_table(pf, p) if not x.worked_out]
        if not holders:
            continue
        cq = CompanyQuarter(h.name, debt=p.debt or 0, equity_value=p.value_100)
        if h.company_path:
            cq.revenue, cq.opex = operating_quarter(h.company_path, start, end)
        units = op.units(pf, h.id)
        if units:
            s = op.summary(units, end)
            cq.occupancy, cq.units = s.occupancy, s.units
        try:
            _w, shares, _v = pfm.cap_table_waterfall(pf, h, end)
        except Exception:  # noqa: BLE001
            shares = []
        funded = [x for x in holders if x.funded]           # cap_table_waterfall's owners, in the same order
        by_owner = {x.id: o for x, o in zip(funded, shares)} if len(funded) == len(shares) else {}
        dists = book_distributions(h.company_path or "", end)
        start_date = h.acquired or start.isoformat()

        def paid_at(x, d: str) -> int:
            return x.funded if (x.funded_date or start_date) <= d else 0

        for x in holders:
            when = x.funded_date or start_date
            mine = []
            for d, cents in dists:            # their share of each distribution, by what was paid in then
                total = sum(paid_at(y, d) for y in holders)
                if total and paid_at(x, d):
                    mine.append((d, round(cents * paid_at(x, d) / total)))
            before = [c for d, c in mine if d < start.isoformat()]
            during = [c for d, c in mine if start.isoformat() <= d <= end.isoformat()]
            paid = x.funded if when <= end.isoformat() else 0
            paid_before = x.funded if when < start.isoformat() else 0
            o = by_owner.get(x.id)
            total_paid = sum(paid_at(y, end.isoformat()) for y in holders)
            value = o.value if o is not None else (round(p.value_100 * paid / total_paid) if p.value_100 is not None
                                                     and total_paid else None)
            flows = ([(date.fromisoformat(when), -paid)] if paid else []) + [(date.fromisoformat(d), c)
                                                                            for d, c in mine]
            pos = Position(h.name, h.id, x.share_class or "Common", paid, paid / total_paid if total_paid else 0.0,
                           paid_before - sum(before), paid - paid_before, sum(during),
                           paid - sum(before) - sum(during), sum(before) + sum(during), value, flows=flows)
            if value is not None and flows and (end - min(d for d, _c in flows)).days >= 365:
                pos.irr = pfm.xirr(flows + [(end, value)])        # (not annualized under a year: misleading)
            st = out.setdefault(_key(x), Statement(x.name.strip(), x.company.strip(), year, quarter, [], {}))
            st.positions.append(pos)
            st.companies[h.id] = cq
    return sorted(out.values(), key=lambda s: s.label.lower())


def _pct(v: float | None, dec: int = 1) -> str:
    return "—" if v is None else f"{v * 100:.{dec}f}%"


def _x(v: float | None) -> str:
    return "—" if v is None else f"{v:.2f}x"


def statement_html(st: Statement, sender: str = "") -> str:
    """The statement as one page (for the preview and the PDF)."""
    esc = html.escape
    start, end = st.period
    m = fmt_money
    css = ("body{font-family:sans-serif;font-size:9pt} h1{font-size:15pt;margin:0} h2{font-size:11pt;margin:14px 0 4px} "
           "td{padding:2px 8px} .r{text-align:right} .muted{color:#6b7380} .tot td{font-weight:bold;"
           "border-top:1px solid #999}")
    out = [f"<html><head><style>{css}</style></head><body>",
           f"<h1>Investor statement – Q{st.quarter} {st.year}</h1>",
           f"<p class='muted'>{start:%B} {start.day}, {start.year} to {end:%B} {end.day}, {end.year}" + (f" · from {esc(sender)}" if sender else "")
           + "</p>", f"<p><b>{esc(st.label)}</b></p>"]
    out.append("<h2>Summary</h2><table>")
    for label, v in (("Paid in", m(st.total("paid_in"))), ("Distributions received", m(st.total("total_distributed"))),
                     ("Value of your interest", m(st.total("value"))), ("Multiple (MOIC)", _x(st.moic)),
                     ("IRR", _pct(st.irr))):
        out.append(f"<tr><td>{label}</td><td class='r'><b>{v}</b></td></tr>")
    out.append("</table>")
    out.append("<h2>Capital account this quarter</h2><table cellspacing='0'><tr class='muted'><td>Company</td>"
               "<td>Class</td><td class='r'>Start of quarter</td><td class='r'>Paid in</td>"
               "<td class='r'>Distributions</td><td class='r'>End of quarter</td></tr>")
    for p in st.positions:
        out.append(f"<tr><td>{esc(p.company)}</td><td>{esc(p.share_class)}</td><td class='r'>{m(p.opening)}</td>"
                   f"<td class='r'>{m(p.contributed)}</td><td class='r'>{m(p.distributed)}</td>"
                   f"<td class='r'>{m(p.closing)}</td></tr>")
    if len(st.positions) > 1:
        out.append(f"<tr class='tot'><td>Total</td><td></td><td class='r'>{m(st.total('opening'))}</td>"
                   f"<td class='r'>{m(st.total('contributed'))}</td><td class='r'>{m(st.total('distributed'))}</td>"
                   f"<td class='r'>{m(st.total('closing'))}</td></tr>")
    out.append("</table>")
    out.append("<h2>Since you invested</h2><table cellspacing='0'><tr class='muted'><td>Company</td>"
               "<td class='r'>Your share</td><td class='r'>Paid in</td><td class='r'>Distributions</td>"
               "<td class='r'>Value today</td><td class='r'>MOIC</td><td class='r'>IRR</td></tr>")
    for p in st.positions:
        out.append(f"<tr><td>{esc(p.company)}</td><td class='r'>{_pct(p.pct)}</td><td class='r'>{m(p.paid_in)}</td>"
                   f"<td class='r'>{m(p.total_distributed)}</td>"
                   f"<td class='r'>{m(p.value) if p.value is not None else '—'}</td>"
                   f"<td class='r'>{_x(p.moic)}</td><td class='r'>{_pct(p.irr)}</td></tr>")
    out.append("</table>")
    out.append("<h2>The companies this quarter</h2><table cellspacing='0'><tr class='muted'><td>Company</td>"
               "<td class='r'>Revenue</td><td class='r'>Operating expenses</td><td class='r'>NOI</td>"
               "<td class='r'>Occupancy</td><td class='r'>Debt</td><td class='r'>Equity value</td></tr>")
    for cq in st.companies.values():
        out.append(f"<tr><td>{esc(cq.name)}</td><td class='r'>{m(cq.revenue)}</td><td class='r'>{m(cq.opex)}</td>"
                   f"<td class='r'>{m(cq.noi)}</td>"
                   f"<td class='r'>{_pct(cq.occupancy) + f' of {cq.units}' if cq.occupancy is not None else '—'}</td>"
                   f"<td class='r'>{m(cq.debt)}</td>"
                   f"<td class='r'>{m(cq.equity_value) if cq.equity_value is not None else '—'}</td></tr>")
    out.append("</table>")
    out.append("<p class='muted' style='margin-top:14px'>Value of your interest is today's equity value of each company "
               "(its latest valuation, or book value) shared through its waterfall terms. Distributions are shared in "
               "proportion to what each owner had paid in at the time. IRR is shown once an investment is a year old. "
               "Unaudited.</p></body></html>")
    return "".join(out)


def file_name(st: Statement) -> str:
    from .companies import safe_filename

    return safe_filename(f"Investor Statement Q{st.quarter} {st.year} - {st.label}") + ".pdf"
