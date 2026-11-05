"""Property operations for one company in the portfolio: rent roll, unit mix and a monthly operating statement.

* **Rent roll** — each unit: tenant, type, size, status (old lease / renovated / vacant / under construction),
  base rent, parking, locker, market rent, move-in, lease end (a date or MTM) and notes. Typed in, or imported
  from a workbook / CSV with a rent-roll table.
* **Unit mix** — units, average rent and market rent by unit type and status.
* **Monthly operating statement** — months across, categories down. Past months are the actual figures from the
  company's books, account by account. The coming months are projected: rent from the rent roll (each lease
  goes up by the annual increase on its move-in anniversary) less a vacancy allowance; other revenue and the
  expenses as in the same month a year earlier (so property tax, snow removal… stay seasonal), else the monthly
  average. Below: NOI, debt service (the company's loans), cash flow after debt service and DSCR.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    id           INTEGER PRIMARY KEY,
    holding_id   INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    unit         TEXT NOT NULL,
    tenant       TEXT NOT NULL DEFAULT '',
    unit_type    TEXT NOT NULL DEFAULT '',
    size_sqft    REAL,
    status       TEXT NOT NULL DEFAULT 'Occupied',
    rent_cents   INTEGER NOT NULL DEFAULT 0,
    parking_cents INTEGER NOT NULL DEFAULT 0,
    locker_cents INTEGER NOT NULL DEFAULT 0,
    market_cents INTEGER NOT NULL DEFAULT 0,
    move_in      TEXT,
    lease_end    TEXT NOT NULL DEFAULT '',       -- a date, 'MTM' (month to month) or ''
    notes        TEXT NOT NULL DEFAULT '',
    sort         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_units_h ON units(holding_id, sort);
CREATE TABLE IF NOT EXISTS ops_settings (
    holding_id       INTEGER PRIMARY KEY REFERENCES holdings(id) ON DELETE CASCADE,
    rent_increase_pct REAL NOT NULL DEFAULT 2.5,  -- a year, on each lease's move-in anniversary
    vacancy_pct      REAL NOT NULL DEFAULT 3.0,   -- allowance taken off projected rent
    as_of            TEXT                          -- the date the rent roll's rents are current at
);
"""
STATUSES = ["Old Lease", "Renovated", "New Lease", "Vacant", "Under Construction", "Notice Given"]
EMPTY = ("vacant", "under construction", "construction", "down")


@dataclass
class Unit:
    unit: str
    tenant: str = ""
    unit_type: str = ""
    size_sqft: float | None = None
    status: str = "Old Lease"
    rent_cents: int = 0
    parking_cents: int = 0
    locker_cents: int = 0
    market_cents: int = 0
    move_in: str | None = None
    lease_end: str = ""
    notes: str = ""
    id: int | None = None
    sort: int = 0

    @property
    def occupied(self) -> bool:
        s = self.status.strip().lower()
        if any(s.startswith(e) for e in EMPTY):
            return False
        return bool(self.tenant.strip()) and self.tenant.strip().lower() != "vacant"

    @property
    def total_rent(self) -> int:
        return (self.rent_cents + self.parking_cents + self.locker_cents) if self.occupied else 0

    @property
    def mtm(self) -> bool:
        return self.lease_end.strip().upper() in ("MTM", "M2M", "MONTH TO MONTH", "MONTH-TO-MONTH")

    def lease_end_date(self) -> date | None:
        try:
            return date.fromisoformat(self.lease_end[:10])
        except ValueError:
            return None


@dataclass
class Settings:
    rent_increase_pct: float = 2.5
    vacancy_pct: float = 3.0
    as_of: str | None = None


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


def units(pf, holding_id: int) -> list[Unit]:
    ready(pf)
    return [Unit(r["unit"], r["tenant"], r["unit_type"], r["size_sqft"], r["status"], r["rent_cents"],
                 r["parking_cents"], r["locker_cents"], r["market_cents"], r["move_in"], r["lease_end"], r["notes"],
                 r["id"], r["sort"])
            for r in pf.conn.execute("SELECT * FROM units WHERE holding_id=? ORDER BY sort, id", (holding_id,))]


def save_unit(pf, holding_id: int, u: Unit) -> int:
    ready(pf)
    if not u.unit.strip():
        raise ValueError("Enter the unit number")
    if min(u.rent_cents, u.parking_cents, u.locker_cents, u.market_cents) < 0:
        raise ValueError("Rents can't be negative")
    if u.move_in:
        date.fromisoformat(u.move_in)
    vals = (holding_id, u.unit.strip(), u.tenant.strip(), u.unit_type.strip(), u.size_sqft, u.status.strip(),
            u.rent_cents, u.parking_cents, u.locker_cents, u.market_cents, u.move_in, u.lease_end.strip(),
            u.notes.strip(), u.sort)
    with pf.conn:
        if u.id is None:
            if not u.sort:
                u.sort = (pf.conn.execute("SELECT COALESCE(MAX(sort), 0) FROM units WHERE holding_id=?",
                                          (holding_id,)).fetchone()[0] or 0) + 1
                vals = vals[:-1] + (u.sort,)
            u.id = pf.conn.execute(
                "INSERT INTO units(holding_id, unit, tenant, unit_type, size_sqft, status, rent_cents, parking_cents, "
                "locker_cents, market_cents, move_in, lease_end, notes, sort) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                vals).lastrowid
        else:
            pf.conn.execute("UPDATE units SET holding_id=?, unit=?, tenant=?, unit_type=?, size_sqft=?, status=?, "
                            "rent_cents=?, parking_cents=?, locker_cents=?, market_cents=?, move_in=?, lease_end=?, "
                            "notes=?, sort=? WHERE id=?", vals + (u.id,))
    return u.id


def delete_unit(pf, unit_id: int) -> None:
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM units WHERE id=?", (unit_id,))


def replace_units(pf, holding_id: int, rows: list[Unit]) -> int:
    """Replace a company's whole rent roll (an import)."""
    ready(pf)
    with pf.conn:
        pf.conn.execute("DELETE FROM units WHERE holding_id=?", (holding_id,))
    for i, u in enumerate(rows, start=1):
        u.id, u.sort = None, i
        save_unit(pf, holding_id, u)
    return len(rows)


def settings(pf, holding_id: int) -> Settings:
    ready(pf)
    r = pf.conn.execute("SELECT * FROM ops_settings WHERE holding_id=?", (holding_id,)).fetchone()
    return Settings(r["rent_increase_pct"], r["vacancy_pct"], r["as_of"]) if r else Settings()


def save_settings(pf, holding_id: int, s: Settings) -> None:
    ready(pf)
    if not 0 <= s.rent_increase_pct <= 50 or not 0 <= s.vacancy_pct <= 100:
        raise ValueError("Check the rent increase and vacancy percentages")
    with pf.conn:
        pf.conn.execute("INSERT INTO ops_settings(holding_id, rent_increase_pct, vacancy_pct, as_of) VALUES (?,?,?,?) "
                        "ON CONFLICT(holding_id) DO UPDATE SET rent_increase_pct=excluded.rent_increase_pct, "
                        "vacancy_pct=excluded.vacancy_pct, as_of=excluded.as_of",
                        (holding_id, s.rent_increase_pct, s.vacancy_pct, s.as_of))


# ---------------------------------------------------------------------------
# Importing a rent roll
# ---------------------------------------------------------------------------

HEADERS = {  # field: words a column heading may contain
    "unit": ("unit number", "unit #", "unit no", "suite", "unit"),
    "tenant": ("tenant", "resident", "name"),
    "unit_type": ("unit type", "type", "bedrooms", "layout"),
    "size_sqft": ("unit size", "size", "sq ft", "sqft", "square"),
    "status": ("status",),
    "rent_cents": ("base rent", "current rent", "monthly rent", "rent"),
    "parking_cents": ("parking",),
    "locker_cents": ("locker", "storage"),
    "market_cents": ("market rent", "market"),
    "move_in": ("move in", "move-in", "lease start", "start date"),
    "lease_end": ("lease end", "lease expiry", "expiry", "end date"),
    "notes": ("notes", "comments", "remarks"),
}


def _match(heading: str) -> str | None:
    h = " ".join(str(heading or "").lower().replace("\n", " ").split())
    if not h:
        return None
    best, score = None, 0
    for key, words in HEADERS.items():
        for w in words:
            if w in h and len(w) > score:
                best, score = key, len(w)
    if best == "rent_cents" and ("original" in h or "market" in h or "increase" in h):
        return "market_cents" if "market" in h else None
    return best


def _money(v) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, (int, float)):
        return round(float(v) * 100)
    s = re.sub(r"[^0-9.\-]", "", str(v))
    try:
        return round(float(s) * 100) if s not in ("", "-", ".") else 0
    except ValueError:
        return 0


def _date(v) -> str | None:
    from datetime import datetime

    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:20], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def parse_rows(rows: list[list]) -> list[Unit]:
    """A rent-roll table from rows of cells: the first row with a unit column and a rent column is the heading;
    reading stops at a 'Total' row or two blank rows."""
    head_at, cols = None, {}
    for i, row in enumerate(rows[:60]):
        found = {}
        for j, cell in enumerate(row):
            k = _match(cell) if isinstance(cell, str) else None
            if k and k not in found:
                found[k] = j
        if "unit" in found and "rent_cents" in found and len(found) >= 3:
            head_at, cols = i, found
            break
    if head_at is None:
        raise ValueError("Couldn't find a rent-roll table (a heading row with Unit and Rent columns)")
    out, blanks = [], 0
    for row in rows[head_at + 1:]:
        get = lambda k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
        unit = str(get("unit") or "").strip()
        if unit.lower().startswith("total"):
            break
        if not unit:
            blanks += 1
            if blanks >= 2 and out:
                break
            continue
        blanks = 0
        end = get("lease_end")
        end_s = _date(end) or (str(end).strip() if end not in (None, "") else "")
        size = get("size_sqft")
        try:
            size = float(size) if size not in (None, "") else None
        except (TypeError, ValueError):
            size = None
        status = str(get("status") or "").strip()
        tenant = str(get("tenant") or "").strip()
        if not status:
            status = "Vacant" if not tenant or tenant.lower() == "vacant" else "Old Lease"
        out.append(Unit(unit, tenant, str(get("unit_type") or "").strip(), size, status, _money(get("rent_cents")),
                        _money(get("parking_cents")), _money(get("locker_cents")), _money(get("market_cents")),
                        _date(get("move_in")), end_s, str(get("notes") or "").strip()))
    if not out:
        raise ValueError("The rent-roll table has no units")
    return out


def read_rent_roll(path: str | Path) -> tuple[list[Unit], str]:
    """Units from an Excel workbook (the sheet named like 'Rent Roll' first) or a CSV. Returns (units, where)."""
    path = Path(path)
    if path.suffix.lower() == ".csv":
        import csv

        with open(path, newline="", encoding="utf-8-sig") as f:
            return parse_rows([r for r in csv.reader(f)]), path.name
    import openpyxl

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    try:
        sheets = sorted(wb.worksheets, key=lambda ws: (0 if "rent" in ws.title.lower() else 1))
        problem = None
        for ws in sheets:
            rows = [list(r) for r in ws.iter_rows(max_row=400, max_col=40, values_only=True)]
            try:
                return parse_rows(rows), ws.title
            except ValueError as e:
                problem = e
        raise problem or ValueError("No sheets")
    finally:
        wb.close()


# ---------------------------------------------------------------------------
# Rent roll figures
# ---------------------------------------------------------------------------

@dataclass
class RentRollSummary:
    units: int = 0
    occupied: int = 0
    in_place: int = 0          # monthly rent of occupied units (incl. parking / locker)
    market: int = 0            # monthly market rent of all units
    market_occupied: int = 0
    mtm: int = 0
    expiring_90: int = 0
    sqft: float = 0.0

    @property
    def occupancy(self) -> float | None:
        return self.occupied / self.units if self.units else None

    @property
    def avg_rent(self) -> int | None:
        return round(self.in_place / self.occupied) if self.occupied else None

    @property
    def avg_market(self) -> int | None:
        return round(self.market / self.units) if self.units and self.market else None

    @property
    def loss_to_lease(self) -> int:
        """Monthly: market rent of the occupied units less what they pay."""
        return max(0, self.market_occupied - self.in_place) if self.market_occupied else 0


def summary(rows: list[Unit], as_of: date | None = None) -> RentRollSummary:
    as_of = as_of or date.today()
    s = RentRollSummary(units=len(rows))
    for u in rows:
        s.market += u.market_cents
        s.sqft += u.size_sqft or 0
        if u.occupied:
            s.occupied += 1
            s.in_place += u.total_rent
            s.market_occupied += u.market_cents
            if u.mtm:
                s.mtm += 1
            end = u.lease_end_date()
            if end and as_of <= end <= date.fromordinal(as_of.toordinal() + 90):
                s.expiring_90 += 1
    return s


@dataclass
class MixRow:
    unit_type: str
    status: str
    units: int = 0
    occupied: int = 0
    rent: int = 0
    market: int = 0

    @property
    def avg_rent(self) -> int | None:
        return round(self.rent / self.occupied) if self.occupied else None

    @property
    def avg_market(self) -> int | None:
        return round(self.market / self.units) if self.units and self.market else None


def unit_mix(rows: list[Unit]) -> list[MixRow]:
    groups: dict[tuple[str, str], MixRow] = {}
    for u in rows:
        k = (u.unit_type or "—", u.status or "—")
        g = groups.setdefault(k, MixRow(*k))
        g.units += 1
        g.market += u.market_cents
        if u.occupied:
            g.occupied += 1
            g.rent += u.total_rent
    return sorted(groups.values(), key=lambda g: (g.unit_type, g.status))


def scheduled_rent(rows: list[Unit], month: date, st: Settings) -> int:
    """The rent roll's rent for ``month`` (its first day): each occupied unit's rent, raised by the annual increase
    on every move-in anniversary between the rent roll's date and that month."""
    as_of = date.fromisoformat(st.as_of) if st.as_of else date.today()
    total = 0
    for u in rows:
        if not u.occupied:
            continue
        rent = u.rent_cents
        if u.move_in and st.rent_increase_pct:
            mi = date.fromisoformat(u.move_in)
            for y in range(as_of.year, month.year + 1):
                try:
                    anniv = mi.replace(year=y)
                except ValueError:  # 29 February
                    anniv = date(y, 3, 1)
                if as_of < anniv <= month and anniv.year > mi.year:
                    rent = round(rent * (1 + st.rent_increase_pct / 100))
        total += rent + u.parking_cents + u.locker_cents
    return total


# ---------------------------------------------------------------------------
# Monthly operating statement
# ---------------------------------------------------------------------------

NON_OPERATING = ("depreciation", "amortization", "amortisation", "interest", "income tax")
RENT_WORDS = ("rent",)


@dataclass
class Line:
    label: str
    values: list[int | None]
    kind: str = "line"         # line | total | head | gap | ratio
    ratios: list[float | None] = field(default_factory=list)


@dataclass
class Statement:
    months: list[date]
    projected: list[bool]
    lines: list[Line]
    note: str = ""


def _month_starts(end: date, back: int, forward: int) -> list[date]:
    first = date(end.year, end.month, 1)
    out = []
    y, m = first.year, first.month
    for _ in range(back - 1):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    for _ in range(back + forward):
        out.append(date(y, m, 1))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _book_months(company_path: str, start: date, end: date) -> tuple[dict, dict]:
    """{(account name, type): {'YYYY-MM': cents}} for income (credit−debit) and expense (debit−credit) accounts,
    and {account name: subtype}."""
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT a.name, a.type, a.subtype, substr(e.date, 1, 7), SUM(l.debit_cents - l.credit_cents) "
            "FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id "
            "WHERE a.type IN ('income', 'expense') AND e.kind != 'opening' AND e.date >= ? AND e.date <= ? "
            "GROUP BY a.id, substr(e.date, 1, 7)", (start.isoformat(), end.isoformat())).fetchall()
    finally:
        conn.close()
    out: dict = {}
    for name, typ, _sub, ym, net in rows:
        out.setdefault((name, typ), {})[ym] = -net if typ == "income" else net
    return out, {}


def monthly_statement(pf, perf, end: date | None = None, back: int = 12, forward: int = 12) -> Statement:
    """Months across (``back`` actual months to ``end``'s month, then ``forward`` projected), categories down."""
    from . import portfolio as pfm

    end = end or date.today()
    h = perf.holding
    months = _month_starts(end, back, forward)
    this_month = date(end.year, end.month, 1)
    projected = [m > this_month for m in months]
    keys = [m.strftime("%Y-%m") for m in months]
    hist_start = date(months[0].year - 1, months[0].month, 1)  # a year more, for "same month last year"
    books: dict = {}
    if h.company_path and Path(h.company_path).exists():
        last_actual = date(end.year, end.month, calendar.monthrange(end.year, end.month)[1])
        books, _ = _book_months(h.company_path, hist_start, last_actual)
    rows_ = units(pf, h.id) if h.id else []
    st = settings(pf, h.id) if h.id else Settings()

    def is_rent(name: str) -> bool:
        return any(w in name.lower() for w in RENT_WORDS) and "parking" not in name.lower()

    revenue_accts = sorted(k for k in books if k[1] == "income" and not any(w in k[0].lower()
                                                                          for w in NON_OPERATING))
    expense_accts = sorted(k for k in books if k[1] == "expense" and not any(w in k[0].lower()
                                                                           for w in NON_OPERATING))
    rent_accts = [k for k in revenue_accts if is_rent(k[0])]
    other_rev = [k for k in revenue_accts if k not in rent_accts]

    books_start = min((ym for v in books.values() for ym in v), default=None)  # when the books' history begins

    def actual(acct_keys, ym) -> int:
        return sum(books.get(k, {}).get(ym, 0) for k in acct_keys)

    def project(acct_keys, m: date) -> int:
        """The same month a year earlier when the books go back that far (a month with no bill stays at zero, so
        seasonal costs stay seasonal), else the average of the actual months."""
        ly = f"{m.year - 1:04d}-{m.month:02d}"
        if books_start is not None and books_start <= ly:
            return actual(acct_keys, ly)
        past = [ym for ym, p in zip(keys, projected) if not p]
        return round(sum(actual(acct_keys, ym) for ym in past) / len(past)) if past else 0

    def row(acct_keys, label) -> Line:
        vals = [project(acct_keys, m) if p else actual(acct_keys, ym) for m, ym, p in zip(months, keys, projected)]
        return Line(label, vals)

    lines: list[Line] = [Line("REVENUE", [None] * len(months), "head")]
    if rows_ or rent_accts:
        rent_vals = []
        for m, ym, p in zip(months, keys, projected):
            if p and rows_:
                gross = scheduled_rent(rows_, m, st)
                rent_vals.append(round(gross * (1 - st.vacancy_pct / 100)))
            elif p:
                rent_vals.append(project(rent_accts, m))
            else:
                rent_vals.append(actual(rent_accts, ym))
        lines.append(Line("Rental revenue", rent_vals))
    for k in other_rev:
        lines.append(row([k], k[0]))
    rev_lines = [x for x in lines if x.kind == "line"]
    total_rev = [sum(x.values[i] or 0 for x in rev_lines) for i in range(len(months))]
    lines.append(Line("Total revenue", total_rev, "total"))
    lines.append(Line("", [None] * len(months), "gap"))
    lines.append(Line("OPERATING EXPENSES", [None] * len(months), "head"))
    exp_lines = [row([k], k[0]) for k in expense_accts]
    lines += exp_lines
    total_exp = [sum(x.values[i] or 0 for x in exp_lines) for i in range(len(months))]
    lines.append(Line("Total operating expenses", total_exp, "total"))
    lines.append(Line("", [None] * len(months), "gap"))
    noi = [r - e for r, e in zip(total_rev, total_exp)]
    lines.append(Line("Net operating income", noi, "total"))
    # debt service from the company's loans
    loans = [ln for ln in pfm.loans(pf, h, end, with_books=False)] if h.id else []
    ds = []
    for m in months:
        mo = 0
        for ln in loans:
            if ln.start_date and m < date.fromisoformat(ln.start_date[:7] + "-01"):
                continue
            mat = ln.maturity_date
            if mat and m > date.fromisoformat(mat):
                continue
            mo += round(ln.annual_debt_service / 12)
        ds.append(mo)
    lines.append(Line("Debt service", ds))
    lines.append(Line("Cash flow after debt service", [n - d for n, d in zip(noi, ds)], "total"))
    lines.append(Line("DSCR", [None] * len(months), "ratio", [n / d if d else None for n, d in zip(noi, ds)]))
    if rows_:
        lines.append(Line("", [None] * len(months), "gap"))
        lines.append(Line("Scheduled rent (rent roll)", [scheduled_rent(rows_, m, st) if p or m == this_month
                                                         else None for m, p in zip(months, projected)]))
    note = ("Actual months from the books; projected months: rent from the rent roll"
            + (f" (+{st.rent_increase_pct:g}% a year on move-in anniversaries, less {st.vacancy_pct:g}% vacancy)"
               if rows_ else "") + ", other lines as the same month last year, else the average.")
    return Statement(months, projected, lines, note)


def t12(stmt: Statement, label: str) -> int:
    """The last twelve actual months of a statement line."""
    line = next((x for x in stmt.lines if x.label == label), None)
    if line is None:
        return 0
    vals = [v for v, p in zip(line.values, stmt.projected) if not p and v is not None]
    return sum(vals[-12:])


# ---------------------------------------------------------------------------
# Cash flow
# ---------------------------------------------------------------------------


def _bank_balances(company_path: str, month_ends: list[date]) -> list[int | None]:
    """Cash in the company's bank accounts at each month end (from its books)."""
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        out = []
        for d in month_ends:
            r = conn.execute("SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l "
                             "JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id "
                             "WHERE a.is_bank = 1 AND e.date <= ?", (d.isoformat(),)).fetchone()
            out.append(r[0])
        return out
    finally:
        conn.close()


def _distributions(company_path: str, start: date, end: date) -> dict[str, int]:
    """Money paid out to owners per month: debits to draw / distribution / dividend equity accounts."""
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT substr(e.date, 1, 7), SUM(l.debit_cents - l.credit_cents) FROM journal_lines l "
            "JOIN journal_entries e ON e.id = l.entry_id JOIN accounts a ON a.id = l.account_id "
            "WHERE a.type = 'equity' AND (a.subtype = 'draw' OR lower(a.name) LIKE '%distribution%' "
            "OR lower(a.name) LIKE '%dividend%') AND e.date >= ? AND e.date <= ? GROUP BY substr(e.date, 1, 7)",
            (start.isoformat(), end.isoformat())).fetchall()
        return {ym: v for ym, v in rows}
    finally:
        conn.close()


def cash_flow(pf, perf, end: date | None = None, back: int = 12, forward: int = 12) -> Statement:
    """Months across: NOI (from the operating statement), capex, debt service, loan advances, equity injections,
    distributions, net cash flow and the cash balance. Actual months use the books (the cash balance is the bank
    balance, with timing differences shown as "Other / working capital"); projected months: capex is the cost to
    complete spread evenly, and the balance carries forward."""
    from . import capex as cx
    from . import portfolio as pfm

    end = end or date.today()
    h = perf.holding
    ops = monthly_statement(pf, perf, end, back, forward)
    months, projected = ops.months, ops.projected
    keys = [m.strftime("%Y-%m") for m in months]
    n = len(months)
    by = {x.label: x for x in ops.lines}
    zero = [0] * n
    rev = by["Total revenue"].values
    opx = by["Total operating expenses"].values
    noi = by["Net operating income"].values
    ds = by["Debt service"].values
    # capex: actual from the books, projected = cost to complete over the projected months
    lines_ = cx.lines(pf, h, end) if h.id else []
    actual_capex = cx.monthly_outflows(lines_, keys)
    ctc = max(0, cx.totals(lines_).to_complete) if lines_ else 0
    nproj = sum(projected)
    capex = [round(ctc / nproj) if p and nproj else (0 if p else actual_capex[k]) for k, p in zip(keys, projected)]
    # loan advances and equity injections in their month
    funding, equity = list(zero), list(zero)
    for ln in (pfm.loans(pf, h, end, with_books=False) if h.id else []):
        if ln.start_date and ln.principal_cents and ln.start_date[:7] in keys:
            funding[keys.index(ln.start_date[:7])] += ln.principal_cents
    for x in (pfm.cap_table(pf, perf) if h.id else []):
        if x.funded_date and x.funded and x.funded_date[:7] in keys:
            equity[keys.index(x.funded_date[:7])] += x.funded
    has_books = bool(h.company_path and Path(h.company_path).exists())
    dist = list(zero)
    if has_books:
        d = _distributions(h.company_path, months[0], end)
        dist = [d.get(k, 0) if not p else 0 for k, p in zip(keys, projected)]
    net = [noi[i] - capex[i] - ds[i] + funding[i] + equity[i] - dist[i] for i in range(n)]
    # cash balance
    opening, closing, other = [None] * n, [None] * n, [None] * n
    if has_books:
        ends = [date(m.year, m.month, calendar.monthrange(m.year, m.month)[1]) for m in months]
        before = _bank_balances(h.company_path, [date.fromordinal(months[0].toordinal() - 1)])[0]
        actual_bal = _bank_balances(h.company_path, [e for e, p in zip(ends, projected) if not p])
        prev = before
        k = 0
        for i in range(n):
            opening[i] = prev
            if not projected[i]:
                closing[i] = actual_bal[k]
                k += 1
                other[i] = closing[i] - prev - net[i]   # receivables, payables, timing, non-operating items
            else:
                closing[i] = prev + net[i]
                other[i] = 0
            prev = closing[i]
    gap = Line("", [None] * n, "gap")
    lines = [Line("Total revenue", rev), Line("Total operating expenses", opx),
             Line("Net operating income", noi, "total"), gap,
             Line("CapEx", [-c for c in capex]), Line("Debt service", [-x for x in ds]),
             Line("Loan advances", funding), Line("Equity injections", equity),
             Line("Distributions", [-x for x in dist]), gap,
             Line("Net cash flow", net, "total")]
    if has_books:
        lines += [Line("Other / working capital (books)", other), gap,
                  Line("Opening cash", opening), Line("Ending cash", closing, "total")]
    note = ("Actual months from the books (ending cash = the bank balance; the difference from the items above "
            "is receivables, payables and other timing). Projected months: operating figures as on Operations, "
            f"capex = the {('cost to complete ' + format(ctc / 100, ',.0f')) if ctc else 'cost to complete (none)'} "
            "spread evenly, loan advances and equity on their dates.")
    return Statement(months, projected, lines, note)


# ---------------------------------------------------------------------------
# Dashboard: timeline milestones and weekly notes (typed in)
# ---------------------------------------------------------------------------

DASH_SCHEMA = """
CREATE TABLE IF NOT EXISTS dash_info (
    holding_id  INTEGER PRIMARY KEY REFERENCES holdings(id) ON DELETE CASCADE,
    address     TEXT NOT NULL DEFAULT '',
    exit_date   TEXT,
    notes       TEXT NOT NULL DEFAULT '',
    notes_date  TEXT
);
CREATE TABLE IF NOT EXISTS milestones (
    id          INTEGER PRIMARY KEY,
    holding_id  INTEGER NOT NULL REFERENCES holdings(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    original    TEXT,
    current     TEXT,
    sort        INTEGER NOT NULL DEFAULT 0
);
"""


@dataclass
class DashInfo:
    address: str = ""
    exit_date: str | None = None
    notes: str = ""
    notes_date: str | None = None


@dataclass
class Milestone:
    name: str
    original: str | None = None
    current: str | None = None
    id: int | None = None
    auto: bool = False          # from the holding / loans, not typed in


def dash_info(pf, holding_id: int) -> DashInfo:
    pf.conn.executescript(DASH_SCHEMA)
    r = pf.conn.execute("SELECT * FROM dash_info WHERE holding_id=?", (holding_id,)).fetchone()
    return DashInfo(r["address"], r["exit_date"], r["notes"], r["notes_date"]) if r else DashInfo()


def save_dash_info(pf, holding_id: int, d: DashInfo) -> None:
    pf.conn.executescript(DASH_SCHEMA)
    for x in (d.exit_date, d.notes_date):
        if x:
            date.fromisoformat(x)
    with pf.conn:
        pf.conn.execute("INSERT INTO dash_info(holding_id, address, exit_date, notes, notes_date) VALUES (?,?,?,?,?) "
                        "ON CONFLICT(holding_id) DO UPDATE SET address=excluded.address, exit_date=excluded.exit_date, "
                        "notes=excluded.notes, notes_date=excluded.notes_date",
                        (holding_id, d.address.strip(), d.exit_date, d.notes, d.notes_date))


def milestones(pf, holding) -> list[Milestone]:
    """The timeline: closing (date acquired), each loan's funding and maturity, the milestones typed in, and the
    planned exit — by date."""
    from . import portfolio as pfm

    pf.conn.executescript(DASH_SCHEMA)
    out = []
    if holding.acquired:
        out.append(Milestone("Closing", holding.acquired, holding.acquired, auto=True))
    for ln in pfm.loans(pf, holding, with_books=False):
        if ln.start_date:
            out.append(Milestone(f"{ln.lender} funding", ln.start_date, ln.start_date, auto=True))
        if ln.maturity_date:
            out.append(Milestone(f"{ln.lender} maturity", ln.maturity_date, ln.maturity_date, auto=True))
    out += [Milestone(r["name"], r["original"], r["current"], r["id"])
            for r in pf.conn.execute("SELECT * FROM milestones WHERE holding_id=? ORDER BY sort, id", (holding.id,))]
    info = dash_info(pf, holding.id)
    if info.exit_date:
        out.append(Milestone("Exit", info.exit_date, info.exit_date, auto=True))
    return sorted(out, key=lambda m: (m.current or m.original or "9999"))


def save_milestone(pf, holding_id: int, m: Milestone) -> int:
    pf.conn.executescript(DASH_SCHEMA)
    if not m.name.strip():
        raise ValueError("Enter the milestone")
    for x in (m.original, m.current):
        if x:
            date.fromisoformat(x)
    with pf.conn:
        if m.id is None:
            m.id = pf.conn.execute("INSERT INTO milestones(holding_id, name, original, current) VALUES (?,?,?,?)",
                                   (holding_id, m.name.strip(), m.original, m.current)).lastrowid
        else:
            pf.conn.execute("UPDATE milestones SET name=?, original=?, current=? WHERE id=?",
                            (m.name.strip(), m.original, m.current, m.id))
    return m.id


def delete_milestone(pf, milestone_id: int) -> None:
    pf.conn.executescript(DASH_SCHEMA)
    with pf.conn:
        pf.conn.execute("DELETE FROM milestones WHERE id=?", (milestone_id,))


@dataclass
class TypeStatus:
    unit_type: str
    existing: int = 0           # occupied on an old lease
    existing_rent: int = 0      # their average rent
    renovated: int = 0          # occupied, renovated / new lease
    renovated_rent: int = 0
    vacant: int = 0
    market: int = 0             # average market rent


def by_unit_type(rows: list[Unit]) -> list[TypeStatus]:
    """Rental status by unit type: old-lease vs renovated units and their average rents, vacant units, market."""
    groups: dict[str, dict] = {}
    for u in rows:
        g = groups.setdefault(u.unit_type or "—", {"old": [], "new": [], "vacant": 0, "market": []})
        if u.market_cents:
            g["market"].append(u.market_cents)
        if not u.occupied:
            g["vacant"] += 1
        elif any(w in u.status.lower() for w in ("renovated", "new")):
            g["new"].append(u.rent_cents)
        else:
            g["old"].append(u.rent_cents)
    avg = lambda xs: round(sum(xs) / len(xs)) if xs else 0  # noqa: E731
    return [TypeStatus(k, len(g["old"]), avg(g["old"]), len(g["new"]), avg(g["new"]), g["vacant"], avg(g["market"]))
            for k, g in sorted(groups.items())]


# ---------------------------------------------------------------------------
# Master cash flow: Pro-Forma and Actual side by side, month by month
# ---------------------------------------------------------------------------


@dataclass
class CFRow:
    label: str
    level: int = 1                    # 0 section / total, 1 line, 2 sub-line
    kind: str = "line"                # line | total | head | gap | balance
    pro_forma: list = field(default_factory=list)   # cents or None, per month
    actual: list = field(default_factory=list)


@dataclass
class MasterCashFlow:
    months: list[date]
    past: list[bool]                  # months with actual figures (up to the as-of month)
    rows: list[CFRow]


def _balances_by_month(company_path: str, account_ids: list[int], month_ends: list[date]) -> list[int]:
    """Debit-minus-credit balance of a set of accounts at each month end."""
    if not account_ids:
        return [0] * len(month_ends)
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(account_ids))
        return [conn.execute(f"SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l "
                             f"JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id IN ({q}) AND e.date <= ?",
                             [*account_ids, d.isoformat()]).fetchone()[0] for d in month_ends]
    finally:
        conn.close()


def _account_ids(company_path: str) -> dict[str, list[int]]:
    """Receivable, payable, debt and interest-expense accounts in a company's books."""
    from . import receivables as rx
    from .books import Books
    from .portfolio import DEBT_WORDS

    books = Books.open(company_path, create=False)
    try:
        ar = sorted(rx.control_ids(books, "invoice")) if books._has_table("documents") else []
        ap = sorted(rx.control_ids(books, "bill")) if books._has_table("documents") else []
        if not ar:
            ar = [a.id for a in books.accounts() if a.type == "asset" and "receivable" in a.name.lower()]
        if not ap:
            ap = [a.id for a in books.accounts() if a.type == "liability" and "payable" in a.name.lower()
                  and not any(w in a.name.lower() for w in DEBT_WORDS)]
        debt = [a.id for a in books.accounts() if a.type == "liability" and a.id not in ap
                and (a.subtype == "long_term" or any(w in a.name.lower() for w in DEBT_WORDS))]
        interest = [a.id for a in books.accounts() if a.type == "expense" and "interest" in a.name.lower()]
        return {"ar": ar, "ap": ap, "debt": debt, "interest": interest}
    finally:
        books.close()


def _monthly_moves(company_path: str, account_ids: list[int], start: date, end: date) -> dict[str, tuple[int, int]]:
    """{'YYYY-MM': (debits, credits)} posted to these accounts (opening balances left out)."""
    if not account_ids:
        return {}
    conn = sqlite3.connect(f"file:{Path(company_path)}?mode=ro", uri=True)
    try:
        q = ",".join("?" * len(account_ids))
        return {ym: (d, c) for ym, d, c in conn.execute(
            f"SELECT substr(e.date, 1, 7), SUM(l.debit_cents), SUM(l.credit_cents) FROM journal_lines l "
            f"JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id IN ({q}) AND e.kind != 'opening' "
            f"AND e.date >= ? AND e.date <= ? GROUP BY substr(e.date, 1, 7)",
            [*account_ids, start.isoformat(), end.isoformat()])}
    finally:
        conn.close()


def master_cash_flow(pf, perf, end: date | None = None, back: int = 12, forward: int = 12) -> MasterCashFlow:
    """Each month's Pro-Forma (the projection: rent from the rent roll ahead, other lines as the same month a year
    earlier, scheduled debt payments, capex to complete spread over the coming months) next to its Actual (the
    books: revenue and expenses by account, capex, changes in receivables and payables, loan payments and advances,
    equity, and the bank balance)."""
    from . import capex as cx
    from . import portfolio as pfm

    end = end or date.today()
    h = perf.holding
    months = _month_starts(end, back, forward)
    this_month = date(end.year, end.month, 1)
    past = [m <= this_month for m in months]
    keys = [m.strftime("%Y-%m") for m in months]
    n = len(months)
    ends = [date(m.year, m.month, calendar.monthrange(m.year, m.month)[1]) for m in months]
    has_books = bool(h.company_path and Path(h.company_path).exists())
    books: dict = {}
    if has_books:
        books, _ = _book_months(h.company_path, date(months[0].year - 1, months[0].month, 1), ends[-1])
    books_start = min((ym for v in books.values() for ym in v), default=None)
    rows_ = units(pf, h.id) if h.id else []
    st = settings(pf, h.id) if h.id else Settings()
    nonop = lambda name: any(w in name.lower() for w in NON_OPERATING)  # noqa: E731
    rev_accts = sorted(k for k in books if k[1] == "income" and not nonop(k[0]))
    exp_accts = sorted(k for k in books if k[1] == "expense" and not nonop(k[0]))
    rent_accts = [k for k in rev_accts if "rent" in k[0].lower() and "parking" not in k[0].lower()]
    past_keys = [k for k, p in zip(keys, past) if p and k <= end.strftime("%Y-%m")]

    def act(acct_keys, ym):
        return sum(books.get(k, {}).get(ym, 0) for k in acct_keys)

    def pro(acct_keys, m: date) -> int:
        ly = f"{m.year - 1:04d}-{m.month:02d}"
        if books_start is not None and books_start <= ly:
            return act(acct_keys, ly)
        return round(sum(act(acct_keys, k) for k in past_keys) / len(past_keys)) if past_keys else 0

    def account_row(label, acct_keys, level=1) -> CFRow:
        return CFRow(label, level, "line", [pro(acct_keys, m) for m in months],
                     [act(acct_keys, k) if p else None for k, p in zip(keys, past)])

    rows: list[CFRow] = []
    gap = lambda: CFRow("", 0, "gap", [None] * n, [None] * n)  # noqa: E731
    # revenue
    rows.append(CFRow("Revenue", 0, "head", [None] * n, [None] * n))
    rent = CFRow("Rental income", 1, "line", [], [])
    for m, k, p in zip(months, keys, past):
        if rows_ and m >= this_month:
            rent.pro_forma.append(round(scheduled_rent(rows_, m, st) * (1 - st.vacancy_pct / 100)))
        else:
            rent.pro_forma.append(pro(rent_accts, m))
        rent.actual.append(act(rent_accts, k) if p else None)
    rev_rows = [rent] + [account_row(k[0], [k]) for k in rev_accts if k not in rent_accts]
    rows += rev_rows

    def total(label, parts, kind="total", level=0) -> CFRow:
        return CFRow(label, level, kind,
                     [sum(r.pro_forma[i] or 0 for r in parts) for i in range(n)],
                     [sum(r.actual[i] or 0 for r in parts) if past[i] else None for i in range(n)])

    t_rev = total("Total revenue", rev_rows)
    rows += [t_rev, gap(), CFRow("Operating expenses", 0, "head", [None] * n, [None] * n)]
    exp_rows = [account_row(k[0], [k]) for k in exp_accts]
    rows += exp_rows
    t_exp = total("Total operating expenses", exp_rows)
    noi = CFRow("Net operating income", 0, "total", [r - e for r, e in zip(t_rev.pro_forma, t_exp.pro_forma)],
                [(r - e) if past[i] else None for i, (r, e) in enumerate(zip(t_rev.actual, t_exp.actual))])
    rows += [t_exp, gap(), noi, gap()]
    # capex
    lines_ = cx.lines(pf, h, end) if h.id else []
    spent = cx.monthly_outflows(lines_, keys)
    ctc = max(0, cx.totals(lines_).to_complete) if lines_ else 0
    future = [not p for p in past]
    nf = sum(future)
    capex = CFRow("Capital expenditure", 1, "line",
                  [round(ctc / nf) if f and nf else 0 for f in future],
                  [spent[k] if p else None for k, p in zip(keys, past)])
    rows += [CFRow("Capex", 0, "head", [None] * n, [None] * n), capex, total("Total capex", [capex]), gap()]
    # cash adjustments, financing, equity (actuals from the books)
    ids = _account_ids(h.company_path) if has_books else {"ar": [], "ap": [], "debt": [], "interest": []}
    prev_end = date.fromordinal(months[0].toordinal() - 1)
    ar_bal = _balances_by_month(h.company_path, ids["ar"], [prev_end] + ends) if has_books else [0] * (n + 1)
    ap_bal = _balances_by_month(h.company_path, ids["ap"], [prev_end] + ends) if has_books else [0] * (n + 1)
    ar = CFRow("Accounts receivable", 1, "line", [0] * n,
               [-(ar_bal[i + 1] - ar_bal[i]) if past[i] else None for i in range(n)])
    ap = CFRow("Accounts payable", 1, "line", [0] * n,
               [-(ap_bal[i + 1] - ap_bal[i]) if past[i] else None for i in range(n)])   # liability: credit = cash kept
    debt_moves = _monthly_moves(h.company_path, ids["debt"], months[0], ends[-1]) if has_books else {}
    int_moves = _monthly_moves(h.company_path, ids["interest"], months[0], ends[-1]) if has_books else {}
    loans = pfm.loans(pf, h, end, with_books=False) if h.id else []
    sched_pay, sched_fund = [0] * n, [0] * n
    for ln in loans:
        for i, m in enumerate(months):
            if ln.start_date and ln.start_date[:7] == keys[i] and ln.principal_cents:
                sched_fund[i] += ln.principal_cents
            if ln.start_date and m < date.fromisoformat(ln.start_date[:7] + "-01"):
                continue
            if ln.maturity_date and m > date.fromisoformat(ln.maturity_date):
                continue
            sched_pay[i] += round(ln.annual_debt_service / 12)
    pay = CFRow("Debt payment", 1, "line", [-x for x in sched_pay],
                [-(debt_moves.get(k, (0, 0))[0] + int_moves.get(k, (0, 0))[0] - int_moves.get(k, (0, 0))[1])
                 if p else None for k, p in zip(keys, past)])
    fund = CFRow("Debt funding", 1, "line", sched_fund,
                 [debt_moves.get(k, (0, 0))[1] if p else None for k, p in zip(keys, past)])
    equity = [0] * n
    for x in (pfm.cap_table(pf, perf) if h.id else []):
        if x.funded_date and x.funded and x.funded_date[:7] in keys:
            equity[keys.index(x.funded_date[:7])] += x.funded
    eq = CFRow("Equity injection", 0, "line", equity, [e if p else None for e, p in zip(equity, past)])
    dist = _distributions(h.company_path, months[0], ends[-1]) if has_books else {}
    dis = CFRow("Distributions", 0, "line", [0] * n, [-dist.get(k, 0) if p else None for k, p in zip(keys, past)])
    # bank balance and what the lines above don't explain
    bank = _bank_balances(h.company_path, [prev_end] + ends) if has_books else None
    known = [noi.actual[i] - (capex.actual[i] or 0) + (ar.actual[i] or 0) + (ap.actual[i] or 0) +
             (pay.actual[i] or 0) + (fund.actual[i] or 0) + (eq.actual[i] or 0) + (dis.actual[i] or 0)
             if past[i] else None for i in range(n)]
    other = CFRow("Other / timing", 1, "line", [0] * n,
                  [(bank[i + 1] - bank[i] - known[i]) if (bank and past[i]) else (0 if past[i] else None)
                   for i in range(n)])
    adj = total("Total cash adjustments", [ar, ap, other])
    fin = total("Total financing", [pay, fund])
    rows += [CFRow("Cash adjustments", 0, "head", [None] * n, [None] * n), ar, ap, other, adj, gap(),
             CFRow("Financing", 0, "head", [None] * n, [None] * n), pay, fund, fin, eq, dis, gap()]
    net = CFRow("Net property cash flow", 0, "total",
                [noi.pro_forma[i] - capex.pro_forma[i] + fin.pro_forma[i] + eq.pro_forma[i] for i in range(n)],
                [(noi.actual[i] - (capex.actual[i] or 0) + adj.actual[i] + fin.actual[i] + eq.actual[i]
                  + dis.actual[i]) if past[i] else None for i in range(n)])
    start_a = [bank[i] if (bank and past[i]) else None for i in range(n)]
    end_a = [bank[i + 1] if (bank and past[i]) else None for i in range(n)]
    start_p, end_p = [], []
    prev = bank[0] if bank else 0
    for i in range(n):
        if i and past[i - 1] and bank:
            prev = bank[i]          # each pro-forma month starts from the actual cash where there is one
        start_p.append(prev)
        prev = prev + net.pro_forma[i]
        end_p.append(prev)
    rows.insert(0, CFRow("Starting cash", 0, "balance", start_p, start_a))
    rows += [net, CFRow("Starting balance", 0, "balance", start_p, start_a),
             CFRow("Ending balance", 0, "total", end_p, end_a)]
    return MasterCashFlow(months, past, rows)


def operating_rows(f: MasterCashFlow, rent_roll: list[Unit] | None = None, st: Settings | None = None) -> list[CFRow]:
    """The operating statement from the master cash flow: revenue, operating expenses, NOI, then debt service, cash
    flow after debt service and DSCR (each with Pro-Forma and Actual), and the rent roll's scheduled rent."""
    n = len(f.months)
    labels = [r.label for r in f.rows]
    a, b = labels.index("Revenue"), labels.index("Net operating income")
    rows = [r for r in f.rows[a:b + 1]]
    by = {r.label: r for r in f.rows}
    noi, pay = by["Net operating income"], by["Debt payment"]
    after = CFRow("Cash flow after debt service", 0, "total",
                  [noi.pro_forma[i] + pay.pro_forma[i] for i in range(n)],
                  [(noi.actual[i] + pay.actual[i]) if f.past[i] else None for i in range(n)])

    def ratio(x, d):
        return (x / -d) if (x is not None and d) else None

    dscr = CFRow("DSCR", 0, "ratio", [ratio(noi.pro_forma[i], pay.pro_forma[i]) for i in range(n)],
                 [ratio(noi.actual[i], pay.actual[i]) if f.past[i] else None for i in range(n)])
    rows += [CFRow("", 0, "gap", [None] * n, [None] * n),
             CFRow("Debt service", 1, "line", pay.pro_forma, pay.actual), after, dscr]
    if rent_roll:
        st = st or Settings()
        rows += [CFRow("", 0, "gap", [None] * n, [None] * n),
                 CFRow("Scheduled rent (rent roll)", 1, "line",
                       # today's rent roll looks ahead: from the current month on
                       [scheduled_rent(rent_roll, m, st) if (not f.past[i] or i + 1 == n or not f.past[i + 1])
                        else None for i, m in enumerate(f.months)], [None] * n)]
    return rows
