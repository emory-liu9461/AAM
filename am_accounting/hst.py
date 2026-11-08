"""GST/HST returns: what was collected and paid for each filing period, and the figures for the return.

The figures come from the company's HST accounts — any account named HST, GST or Sales Tax (or picked by hand):

* **Line 103 — GST/HST collected**: credits to those accounts (tax charged on invoices and sales).
* **Line 106 — input tax credits**: debits to them (tax paid on purchases, recorded on the HST account — e.g. by
  splitting a bill between the expense and HST).
* **Line 109 — net tax**: 103 − 106; positive is owing, negative a refund.
* **Payments to / refunds from the CRA** are left out of both: entries whose bank line or memo names the Receiver
  General / CRA / Canada Revenue, or entries recorded here as a return payment.

**Line 101 — revenue** is the period's income (operating and other), shown with the share that went through
accounts charging HST. These are a starting point for the return: check them before filing.

Filing periods follow the company's fiscal year: monthly, quarterly or annually. A return marked as filed is kept
with its figures and date.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

FREQUENCIES = {"monthly": "Monthly", "quarterly": "Quarterly", "annual": "Annually"}
CRA_WORDS = re.compile(r"receiver gen|rec\.? ?gen|canada revenue|\bcra\b|gst/hst (payment|remit)|hst (payment|remit)"
                       r"|gst (payment|remit)|gst34|tax centre", re.I)
NAME_WORDS = re.compile(r"\b(hst|gst|sales tax|qst|pst)\b", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS hst_returns (
    id          INTEGER PRIMARY KEY,
    p_start     TEXT NOT NULL,
    p_end       TEXT NOT NULL,
    line101     INTEGER NOT NULL DEFAULT 0,
    line103     INTEGER NOT NULL DEFAULT 0,
    line106     INTEGER NOT NULL DEFAULT 0,
    line109     INTEGER NOT NULL DEFAULT 0,
    filed_on    TEXT NOT NULL,
    notes       TEXT NOT NULL DEFAULT '',
    UNIQUE(p_start, p_end)
);
"""


@dataclass
class Settings:
    frequency: str = "quarterly"
    account_ids: list[int] = field(default_factory=list)     # [] = found by name
    number: str = ""                                          # the business number's RT account


def settings(books) -> Settings:
    raw = books._meta("hst_settings")
    if not raw:
        return Settings()
    d = json.loads(raw)
    return Settings(d.get("frequency", "quarterly"), list(d.get("account_ids", [])), d.get("number", ""))


def save_settings(books, s: Settings) -> None:
    if s.frequency not in FREQUENCIES:
        raise ValueError("Pick monthly, quarterly or annually")
    with books.conn:
        books._set_meta("hst_settings", json.dumps({"frequency": s.frequency, "account_ids": s.account_ids,
                                                    "number": s.number.strip()}))


def hst_accounts(books, s: Settings | None = None) -> list:
    s = s or settings(books)
    accts = books.accounts()
    if s.account_ids:
        return [a for a in accts if a.id in s.account_ids]
    return [a for a in accts if a.type in ("liability", "asset") and NAME_WORDS.search(a.name)]


def ensure_accounts(books) -> list[int]:
    """"HST Collected" and "HST Paid (Input Tax Credits)" (liabilities), when the books have no HST account yet."""
    have = hst_accounts(books, Settings())
    if have:
        return [a.id for a in have]
    used = {a.code for a in books.accounts()}

    def code(c: int) -> str:
        while str(c) in used:
            c += 1
        used.add(str(c))
        return str(c)

    return [books.add_account(code(2210), "HST Collected", "liability", "current").id,
            books.add_account(code(2215), "HST Paid (Input Tax Credits)", "liability", "current").id]


def periods(freq: str, fy_month: int, around: date, back: int = 8) -> list[tuple[date, date]]:
    """Filing periods (newest first) ending on or before the end of the period holding ``around``, ``back`` of them."""
    step = {"monthly": 1, "quarterly": 3, "annual": 12}[freq]
    y = around.year if around.month >= fy_month else around.year - 1
    start = date(y, fy_month, 1)
    while True:                                   # the period holding ``around``
        nxt = _add_months(start, step)
        if nxt > around:
            break
        start = nxt
    out = []
    for _ in range(back):
        out.append((start, _add_months(start, step) - timedelta(days=1)))
        start = _add_months(start, -step)
    return out


def _add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


@dataclass
class Line:
    date: str
    what: str
    account: str
    collected: int = 0
    itc: int = 0
    payment: int = 0             # paid to the CRA (+) / refund received (−)


@dataclass
class Return:
    start: date
    end: date
    line101: int = 0             # revenue in the period
    taxable_revenue: int = 0     # of which in income accounts on invoices that charged HST (estimate)
    line103: int = 0
    line106: int = 0
    paid: int = 0                # paid to the CRA during the period (net of refunds)
    balance_end: int = 0         # owing on the HST accounts at the end (− = refund due)
    lines: list[Line] = field(default_factory=list)
    accounts: list[str] = field(default_factory=list)
    filed_on: str | None = None

    @property
    def line109(self) -> int:
        return self.line103 - self.line106

    @property
    def owing(self) -> bool:
        return self.line109 > 0


def _is_payment(books, entry) -> bool:
    if entry["kind"] == "hst_payment":
        return True
    text = entry["memo"] or ""
    if entry["transaction_id"]:
        try:
            tx = books.transaction(entry["transaction_id"])
            text += " " + tx.description + " " + (tx.memo or "")
        except KeyError:
            pass
    return bool(CRA_WORDS.search(text))


def compute(books, start: date, end: date, s: Settings | None = None) -> Return:
    s = s or settings(books)
    accts = hst_accounts(books, s)
    r = Return(start, end, accounts=[a.name for a in accts])
    ids = [a.id for a in accts]
    names = {a.id: a.name for a in accts}
    if ids:
        q = (f"SELECT e.id, e.date, e.memo, e.kind, e.transaction_id, l.account_id, l.debit_cents, l.credit_cents "
             f"FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id IN "
             f"({','.join('?' * len(ids))}) AND e.date >= ? AND e.date <= ? AND e.kind != 'opening' "
             f"ORDER BY e.date, e.id")
        for row in books.conn.execute(q, [*ids, start.isoformat(), end.isoformat()]):
            what = row["memo"] or ""
            if row["transaction_id"]:
                try:
                    what = books.transaction(row["transaction_id"]).description
                except KeyError:
                    pass
            ln = Line(row["date"], what, names[row["account_id"]])
            if _is_payment(books, row):
                ln.payment = row["debit_cents"] - row["credit_cents"]
                r.paid += ln.payment
            else:
                ln.collected, ln.itc = row["credit_cents"], row["debit_cents"]
                r.line103 += row["credit_cents"]
                r.line106 += row["debit_cents"]
            r.lines.append(ln)
        bal = books.conn.execute(
            f"SELECT COALESCE(SUM(l.credit_cents - l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e "
            f"ON e.id = l.entry_id WHERE l.account_id IN ({','.join('?' * len(ids))}) AND e.date <= ?",
            [*ids, end.isoformat()]).fetchone()[0]
        r.balance_end = bal
    inc = books.conn.execute(
        "SELECT COALESCE(SUM(l.credit_cents - l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id WHERE a.type = 'income' AND e.kind != 'opening' "
        "AND e.date >= ? AND e.date <= ?", (start.isoformat(), end.isoformat())).fetchone()[0]
    r.line101 = inc
    if ids:                       # revenue in entries that also credited an HST account (sales with tax on them)
        r.taxable_revenue = books.conn.execute(
            f"SELECT COALESCE(SUM(l.credit_cents - l.debit_cents), 0) FROM journal_lines l JOIN journal_entries e "
            f"ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id WHERE a.type = 'income' AND e.date >= ? "
            f"AND e.date <= ? AND e.id IN (SELECT entry_id FROM journal_lines WHERE credit_cents > 0 AND account_id IN "
            f"({','.join('?' * len(ids))}))", [start.isoformat(), end.isoformat(), *ids]).fetchone()[0]
    f = filed(books).get((start.isoformat(), end.isoformat()))
    r.filed_on = f["filed_on"] if f else None
    return r


def ready(books) -> None:
    books.conn.executescript(SCHEMA)


def filed(books) -> dict[tuple[str, str], dict]:
    ready(books)
    return {(r["p_start"], r["p_end"]): dict(r) for r in books.conn.execute("SELECT * FROM hst_returns")}


def mark_filed(books, r: Return, on: date | None = None, notes: str = "") -> None:
    ready(books)
    on_iso = (on or date.today()).isoformat()
    with books.conn:
        books.conn.execute("INSERT INTO hst_returns(p_start, p_end, line101, line103, line106, line109, filed_on, notes) "
                           "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(p_start, p_end) DO UPDATE SET line101=excluded.line101, "
                           "line103=excluded.line103, line106=excluded.line106, line109=excluded.line109, "
                           "filed_on=excluded.filed_on, notes=excluded.notes",
                           (r.start.isoformat(), r.end.isoformat(), r.line101, r.line103, r.line106, r.line109,
                            on_iso, notes))
    r.filed_on = on_iso


def unmark_filed(books, r: Return) -> None:
    ready(books)
    with books.conn:
        books.conn.execute("DELETE FROM hst_returns WHERE p_start=? AND p_end=?", (r.start.isoformat(), r.end.isoformat()))
    r.filed_on = None


def year_returns(books, fy_start: date, fy_end: date, s: Settings | None = None) -> list[Return]:
    """Every filing period in a fiscal year."""
    s = s or settings(books)
    ps = [p for p in periods(s.frequency, books.fiscal_year_start_month, fy_end, back=12) if p[0] >= fy_start]
    return [compute(books, a, b, s) for a, b in sorted(ps)]


def write_xlsx(books, path, returns: list[Return]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "GST-HST Returns"
    money = '#,##0.00;[Red](#,##0.00);"-"'
    ws.append([books.company_name])
    ws.append(["GST/HST returns" + (f" · {settings(books).number}" if settings(books).number else "")])
    ws.append([])
    ws.append(["Period start", "Period end", "Line 101 revenue", "Line 103 collected", "Line 106 ITCs",
               "Line 109 net tax", "Paid to CRA in period", "Owing at period end", "Filed on"])
    for c in ws[4]:
        c.font = Font(bold=True)
    for r in returns:
        ws.append([r.start.isoformat(), r.end.isoformat(), r.line101 / 100, r.line103 / 100, r.line106 / 100,
                   r.line109 / 100, r.paid / 100, r.balance_end / 100, r.filed_on or ""])
    for row in ws.iter_rows(min_row=5, min_col=3, max_col=8):
        for c in row:
            c.number_format = money
    ws2 = wb.create_sheet("Detail")
    ws2.append(["Date", "What", "Account", "Collected", "ITC", "Paid to CRA"])
    for c in ws2[1]:
        c.font = Font(bold=True)
    for r in returns:
        for ln in r.lines:
            ws2.append([ln.date, ln.what, ln.account, ln.collected / 100, ln.itc / 100, ln.payment / 100])
    for row in ws2.iter_rows(min_row=2, min_col=4, max_col=6):
        for c in row:
            c.number_format = money
    for w, col in ((14, "A"), (14, "B"), (18, "C"), (18, "D"), (16, "E"), (16, "F"), (20, "G"), (20, "H"), (12, "I")):
        ws.column_dimensions[col].width = w
    ws2.column_dimensions["B"].width = 44
    ws2.column_dimensions["C"].width = 28
    wb.save(path)
