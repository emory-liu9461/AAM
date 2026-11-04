"""Financial statements built from the general ledger.

* Income Statement (Profit & Loss) for a period
* Balance Sheet as of a date (net income to date rolls into Retained Earnings)
* Statement of Cash Flows for a period (direct method, from bank activity)
* Trial Balance as of a date
"""

from __future__ import annotations

import csv
import html
import io
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable
from datetime import date, timedelta

from . import chart_of_accounts as coa
from .books import Account, Books
from .money import fmt_money


@dataclass
class Line:
    label: str
    values: list[int | None] = field(default_factory=list)
    kind: str = "line"  # header | line | subtotal | total | spacer | note
    indent: int = 0
    account_id: int | None = None   # lines for one account can be expanded into their entries


@dataclass
class Report:
    title: str
    subtitle: str
    columns: list[str]
    lines: list[Line]
    company: str = ""
    checks: dict[str, bool] = field(default_factory=dict)
    start: str | None = None        # period the amounts cover (None: since the beginning)
    end: str | None = None
    spans: list = field(default_factory=list)  # [(start, end)] of each period column (statements by period)

    def add(self, label: str = "", *values: int | None, kind: str = "line", indent: int = 0,
            account: int | None = None) -> None:
        self.lines.append(Line(label, list(values), kind, indent, account))

    def value(self, label: str) -> int | None:
        for l in self.lines:
            if l.label == label:
                return l.values[0] if l.values else None
        raise KeyError(label)

    # -- rendering ----------------------------------------------------------

    def to_html(self, *, links: bool = False, expanded: set[int] | None = None,
                details: Callable[[int], list] | None = None) -> str:
        """links: account lines are clickable ('acct:<id>'); expanded / details: account ids whose entries are
        shown under their line, and a function giving them (see account_entries)."""
        e = html.escape
        expanded = expanded or set()
        rows = []
        ncols = len(self.columns)
        for l in self.lines:
            if l.kind == "spacer":
                rows.append(f'<tr><td colspan="{ncols + 1}">&nbsp;</td></tr>')
                continue
            pad = 6 + l.indent * 18
            cls = l.kind
            cells = "".join(
                f'<td class="num {cls}">{e(fmt_money(v)) if v is not None else ""}</td>' for v in l.values
            )
            cells += '<td class="num"></td>' * (ncols - len(l.values))
            label = e(l.label)
            is_open = l.account_id is not None and l.account_id in expanded
            if links and l.account_id is not None:
                label = (f'<a href="acct:{l.account_id}" style="color:#1c2430; text-decoration:none">'
                         f'<span style="color:#2f6fde">{"▾" if is_open else "▸"}</span> {label}</a>')
            rows.append(f'<tr class="{cls}"><td class="{cls}" style="padding-left:{pad}px">{label}</td>{cells}</tr>')
            if is_open and details is not None:
                entries = details(l.account_id)
                dpad = pad + 22
                if not entries:
                    rows.append(f'<tr class="detail"><td style="padding-left:{dpad}px" colspan="{ncols + 1}">'
                                f'No entries in this period</td></tr>')
                for en in entries[:500]:
                    col = 0
                    if self.spans:  # under the period it falls in (earlier entries: the first column)
                        col = next((i for i, (ps, pe) in enumerate(self.spans) if ps <= en.date <= pe),
                                   0 if en.date < self.spans[0][0] else len(self.spans) - 1)
                    text = e(f"{en.date}  ·  {en.description}")
                    if links and en.transaction_id:
                        text = f'<a href="tx:{en.transaction_id}" style="color:#2f4a6e; text-decoration:none">{text}</a>'
                    if en.note:
                        text += f' <span class="dnote">· {e(en.note)}</span>'
                    cells_d = ['<td class="num"></td>'] * ncols
                    cells_d[col] = f'<td class="num">{e(fmt_money(en.amount_cents))}</td>'
                    if self.spans and ncols > len(self.spans):  # the Total column
                        cells_d[-1] = f'<td class="num">{e(fmt_money(en.amount_cents))}</td>'
                    rows.append(f'<tr class="detail"><td style="padding-left:{dpad}px">{text}</td>'
                                + "".join(cells_d) + '</tr>')
                if len(entries) > 500:
                    rows.append(f'<tr class="detail"><td style="padding-left:{dpad}px" colspan="{ncols + 1}">'
                                f'… and {len(entries) - 500} more — see the Transactions tab</td></tr>')
        head = "".join(f'<th class="num">{e(c)}</th>' for c in self.columns)
        warn = ""
        failed = [k for k, ok in self.checks.items() if not ok]
        if failed:
            warn = '<p class="warn">Check failed: ' + e(", ".join(failed)) + "</p>"
        return f"""<html><head><style>
body {{ font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif; color:#1c2430; }}
h1 {{ font-size: 15pt; margin: 0; text-align:center; }}
h2 {{ font-size: 18pt; margin: 4px 0 0 0; text-align:center; }}
p.sub {{ text-align:center; color:#5b6573; margin: 2px 0 14px 0; }}
table {{ border-collapse: collapse; width: 100%; }}
th {{ border-bottom: 1.5px solid #1c2430; padding: 4px 6px; font-weight:600; }}
td {{ padding: 3px 6px; }}
td.num, th.num {{ text-align: right; white-space: nowrap; width: {140 if len(self.columns) <= 4 else 105}px; }}
tr.header td {{ font-weight: 700; padding-top: 10px; }}
td.subtotal {{ font-weight: 600; border-top: 1px solid #9aa3ad; }}
td.total {{ font-weight: 700; border-top: 1px solid #1c2430; border-bottom: 3px double #1c2430; }}
tr.note td {{ color: #5b6573; font-style: italic; }}
tr.group td {{ font-weight: 600; }}
td.groupsum {{ font-weight: 600; border-top: 1px solid #c9ced6; }}
p.warn {{ color: #b3261e; font-weight: 600; }}
tr.detail td {{ color: #4a5361; font-size: 8.5pt; padding-top: 1px; padding-bottom: 1px; }}
span.dnote {{ color: #7a8390; }}
</style></head><body>
<h1>{e(self.company)}</h1><h2>{e(self.title)}</h2><p class="sub">{e(self.subtitle)}</p>
<table width="{720 if len(self.columns) <= 2 else '100%'}" align="center" cellspacing="0"><tr><th style="text-align:left"></th>{head}</tr>{''.join(rows)}</table>{warn}
</body></html>"""

    def to_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([self.company])
        w.writerow([self.title])
        w.writerow([self.subtitle])
        w.writerow([""] + self.columns)
        for l in self.lines:
            if l.kind == "spacer":
                w.writerow([])
                continue
            w.writerow([("  " * l.indent) + l.label] + [f"{v / 100:.2f}" if v is not None else "" for v in l.values])
        return buf.getvalue()


@dataclass
class Entry:
    date: str
    description: str
    note: str            # who / memo / category for context
    amount_cents: int    # in the account's normal direction (as on the statement)
    transaction_id: int | None = None


def account_entries(books: Books, account_id: int, start: str | None, end: str | None) -> list[Entry]:
    """The individual entries that make up an account's amount on a statement, oldest first."""
    from . import parties

    acct = books.account(account_id)
    sql = ("SELECT e.date, e.memo, e.kind, e.transaction_id, l.debit_cents - l.credit_cents AS net "
           "FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id = ?")
    args: list = [account_id]
    if start:
        sql += " AND e.date >= ?"
        args.append(start)
    if end:
        sql += " AND e.date <= ?"
        args.append(end)
    sql += " ORDER BY e.date, e.id"
    rows = books.conn.execute(sql, args).fetchall()
    names = parties.names(books, [r["transaction_id"] for r in rows if r["transaction_id"]]) if rows else {}
    out = []
    for r in rows:
        amount = _normal(acct, r["net"])
        if r["transaction_id"]:
            tx = books.transaction(r["transaction_id"])
            bits = []
            if r["transaction_id"] in names:
                bits.append(names[r["transaction_id"]].name)
            if tx.memo and not tx.memo.startswith(("↔", "✎", "📎")):
                bits.append(tx.memo)
            bank = books.account(tx.bank_account_id).name
            if acct.is_bank:
                if tx.category_id:
                    bits.append(books.account(tx.category_id).name)
            else:
                bits.append(bank)
            out.append(Entry(r["date"], tx.description, " · ".join(bits), amount, tx.id))
        else:
            kind = {"opening": "Opening balance", "manual": "Journal entry"}.get(r["kind"], "")
            out.append(Entry(r["date"], r["memo"] or kind, kind if r["memo"] else "", amount))
    return out


def _long_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def _normal(acct: Account, net_debit: int) -> int:
    return net_debit if acct.debit_normal else -net_debit


def _tree_lines(report: Report, accounts: list[Account], totals: dict[int, int], indent: int) -> int:
    """Add account lines, nesting sub-accounts under their parent with a parent total. Returns the sum."""
    ids = {a.id for a in accounts}
    kids: dict[int | None, list[Account]] = defaultdict(list)
    roots: list[Account] = []
    for a in accounts:
        (kids[a.parent_id] if a.parent_id in ids else roots).append(a)

    def own(a: Account) -> int:
        return _normal(a, totals.get(a.id, 0))

    def subtotal(a: Account) -> int:
        return own(a) + sum(subtotal(c) for c in kids[a.id])

    def active(a: Account) -> bool:
        return totals.get(a.id, 0) != 0 or any(active(c) for c in kids[a.id])

    def emit(a: Account, ind: int) -> None:
        live = [c for c in kids[a.id] if active(c)]
        if not live:
            report.add(a.name, own(a), indent=ind, account=a.id)
            return
        report.add(a.name, kind="group", indent=ind)
        if own(a):
            report.add(f"{a.name} - Other", own(a), indent=ind + 1, account=a.id)
        for c in live:
            emit(c, ind + 1)
        report.add(f"Total {a.name}", subtotal(a), kind="groupsum", indent=ind)

    for a in roots:
        if active(a):
            emit(a, indent)
    return sum(subtotal(a) for a in roots)


def _section(report: Report, books: Books, totals: dict[int, int], accounts: list[Account],
             heading: str, total_label: str | None = None, *, indent: int = 1, hide_empty: bool = False) -> int:
    if hide_empty and not any(totals.get(a.id) for a in accounts):
        return 0
    report.add(heading, kind="header")
    total = _tree_lines(report, accounts, totals, indent)
    report.add(total_label or f"Total {heading}", total, kind="subtotal")
    return total


def _by(accts: list[Account], typ: str, subs: set[str]) -> list[Account]:
    order = list(coa.SUBTYPES[typ])
    picked = [a for a in accts if a.type == typ and (a.subtype in subs or (a.subtype not in order and "" in subs))]
    return sorted(picked, key=lambda a: (order.index(a.subtype) if a.subtype in order else 99, a.code))


# ---------------------------------------------------------------------------


def income_statement(books: Books, start: str, end: str) -> Report:
    L = books.labels()
    r = Report("Income Statement", f"{_long_date(start)} – {_long_date(end)}", ["Amount"], [],
               company=books.company_name, start=start, end=end)
    t = books.ledger_totals(start=start, end=end)
    accts = books.accounts()

    revenue = _section(r, books, t, _by(accts, "income", {"operating", ""}), L["revenue"])
    cogs = _section(r, books, t, _by(accts, "expense", {"cogs"}), L["cogs"], hide_empty=True)
    r.add(L["gross_profit"], revenue - cogs, kind="total")
    r.add(kind="spacer")
    opex = _section(r, books, t, _by(accts, "expense", {"operating", ""}), L["operating_expenses"])
    operating_income = revenue - cogs - opex
    r.add(L["operating_income"], operating_income, kind="total")
    r.add(kind="spacer")
    other_inc = _section(r, books, t, _by(accts, "income", {"other"}), L["other_income"], hide_empty=True)
    other_exp = _section(r, books, t, _by(accts, "expense", {"other"}), L["other_expenses"], hide_empty=True)
    net = operating_income + other_inc - other_exp
    r.add(kind="spacer")
    r.add(L["net_income"], net, kind="total")

    unc = (t.get(books.role(coa.UNCATEGORIZED_INCOME).id, 0)
           or t.get(books.role(coa.UNCATEGORIZED_EXPENSE).id, 0))
    if unc:
        r.add("Includes uncategorized transactions – categorize them for accurate results.", kind="note")
    return r


def net_income(books: Books, start: str | None, end: str) -> int:
    t = books.ledger_totals(start=start, end=end)
    total = 0
    for a in books.accounts(types=("income", "expense")):
        total += -t.get(a.id, 0)  # credit-positive: income adds, expense subtracts
    return total


def balance_sheet(books: Books, as_of: str, *, fiscal_year_start_month: int = 1) -> Report:
    L = books.labels()
    r = Report("Balance Sheet", f"As of {_long_date(as_of)}", ["Amount"], [], company=books.company_name,
               end=as_of)
    t = books.ledger_totals(end=as_of)
    accts = books.accounts()

    r.add(L["assets"].upper(), kind="header")
    cur_assets = _section(r, books, t, _by(accts, "asset", {"bank", "current", ""}), L["current_assets"], indent=2)
    fixed_total = _section(r, books, t, _by(accts, "asset", {"fixed_asset"}), L["fixed_assets"], indent=2,
                           hide_empty=True)
    other_total = _section(r, books, t, _by(accts, "asset", {"other_asset"}), L["other_assets"], indent=2,
                           hide_empty=True)
    total_assets = cur_assets + fixed_total + other_total
    r.add(f"TOTAL {L['assets'].upper()}", total_assets, kind="total")
    r.add(kind="spacer")

    both = f"{L['liabilities']} and {L['equity']}"
    r.add(both.upper(), kind="header")
    cur_liab = _section(r, books, t, _by(accts, "liability", {"current", ""}), L["current_liabilities"], indent=2)
    lt_total = _section(r, books, t, _by(accts, "liability", {"long_term"}), L["long_term_liabilities"], indent=2,
                        hide_empty=True)
    total_liab = cur_liab + lt_total
    r.add(f"Total {L['liabilities']}", total_liab, kind="subtotal")

    # Equity: equity accounts + retained earnings (prior years) + net income (current year)
    d = date.fromisoformat(as_of)
    fy_start = date(d.year if d.month >= fiscal_year_start_month else d.year - 1, fiscal_year_start_month, 1)
    prior_end = (fy_start - timedelta(days=1)).isoformat()
    retained = net_income(books, None, prior_end)
    current_ni = net_income(books, fy_start.isoformat(), as_of)

    r.add(L["equity"], kind="header")
    equity_total = _tree_lines(r, _by(accts, "equity", set(coa.SUBTYPES["equity"]) | {""}), t, 2)
    r.add(L["retained_earnings"], retained, indent=2)
    r.add(L["net_income"], current_ni, indent=2)
    equity_total += retained + current_ni
    r.add(f"Total {L['equity']}", equity_total, kind="subtotal")
    r.add(f"TOTAL {both.upper()}", total_liab + equity_total, kind="total")
    r.checks["Assets = Liabilities + Equity"] = total_assets == total_liab + equity_total
    return r


def _cash_flow_section(acct: Account) -> str | None:
    if acct.is_bank or acct.key == coa.TRANSFER_CLEARING:
        return None  # movement between your own accounts is not a cash flow
    if acct.type in ("income", "expense"):
        return "operating"
    if acct.type == "asset":
        return "investing" if acct.subtype in ("fixed_asset", "other_asset") else "operating"
    if acct.type == "liability":
        return "financing" if acct.subtype == "long_term" else "operating"
    if acct.type == "equity":
        return "financing"
    return "operating"


def cash_flow(books: Books, start: str, end: str) -> Report:
    r = Report("Statement of Cash Flows", f"{_long_date(start)} – {_long_date(end)} (direct method)",
               ["Amount"], [], company=books.company_name)
    banks = {a.id for a in books.accounts() if a.is_bank}
    accts = {a.id: a for a in books.accounts()}
    prior_end = (date.fromisoformat(start) - timedelta(days=1)).isoformat()
    beginning = sum(books.account_balance(b, as_of=prior_end) for b in banks)

    # For each entry touching a bank account, attribute the cash movement to the other accounts.
    rows = books.conn.execute(
        "SELECT e.id, e.kind, l.account_id, l.debit_cents - l.credit_cents AS net "
        "FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
        "WHERE e.date >= ? AND e.date <= ? ORDER BY e.id", (start, end)).fetchall()
    entries: dict[int, list] = {}
    for row in rows:
        entries.setdefault(row["id"], []).append(row)

    buckets: dict[str, dict[int, int]] = {"operating": {}, "investing": {}, "financing": {}}
    opening_in_period = 0
    transfers = 0
    for lines in entries.values():
        cash = sum(l["net"] for l in lines if l["account_id"] in banks)
        if cash == 0:
            continue
        if lines[0]["kind"] == "opening":
            opening_in_period += cash
            continue
        others = [l for l in lines if l["account_id"] not in banks]
        other_total = sum(l["net"] for l in others) or 1
        for l in others:
            share = round(cash * l["net"] / other_total)
            section = _cash_flow_section(accts[l["account_id"]])
            if section is None:
                transfers += share
                continue
            b = buckets[section]
            b[l["account_id"]] = b.get(l["account_id"], 0) + share

    L = books.labels()
    titles = {k: (L[f"{k}_activities"], f"Net Cash from {L[f'{k}_activities']}")
              for k in ("operating", "investing", "financing")}
    net_change = 0
    for key, (heading, total_label) in titles.items():
        r.add(heading, kind="header")
        total = 0
        items = sorted(buckets[key].items(), key=lambda kv: accts[kv[0]].code)
        receipts = [(a, v) for a, v in items if v > 0]
        payments = [(a, v) for a, v in items if v < 0]
        for a, v in receipts:
            r.add(("Receipts – " if key == "operating" else "") + accts[a].name, v, indent=1)
            total += v
        for a, v in payments:
            r.add(("Paid for " if key == "operating" else "") + accts[a].name, v, indent=1)
            total += v
        r.add(total_label, total, kind="subtotal")
        net_change += total
    if transfers:
        r.add(kind="spacer")
        r.add("Unmatched transfers between your accounts", transfers)
        net_change += transfers
    r.add(kind="spacer")
    r.add("Net Change in Cash", net_change, kind="total")
    r.add("Cash at Beginning of Period", beginning + opening_in_period)
    ending = sum(books.account_balance(b, as_of=end) for b in banks)
    r.add("Cash at End of Period", ending, kind="total")
    r.checks["Beginning + change = ending cash"] = beginning + opening_in_period + net_change == ending
    return r


def trial_balance(books: Books, as_of: str) -> Report:
    r = Report("Trial Balance", f"As of {_long_date(as_of)}", ["Debit", "Credit"], [], company=books.company_name,
               end=as_of)
    t = books.ledger_totals(end=as_of)
    dr = cr = 0
    for a in books.accounts():
        net = t.get(a.id, 0)
        if net == 0:
            continue
        if net > 0:
            r.add(a.label, net, None, account=a.id)
            dr += net
        else:
            r.add(a.label, None, -net, account=a.id)
            cr += -net
    r.add("Total", dr, cr, kind="total")
    r.checks["Debits = Credits"] = dr == cr
    return r


REPORTS = {
    "Income Statement (P&L)": "income_statement",
    "Balance Sheet": "balance_sheet",
    "Statement of Cash Flows": "cash_flow",
    "Trial Balance": "trial_balance",
}


def build(books: Books, name: str, start: str, end: str) -> Report:
    fn = REPORTS[name]
    if fn == "income_statement":
        return income_statement(books, start, end)
    if fn == "balance_sheet":
        return balance_sheet(books, end, fiscal_year_start_month=books.fiscal_year_start_month)
    if fn == "cash_flow":
        return cash_flow(books, start, end)
    return trial_balance(books, end)


# ---------------------------------------------------------------------------
# Periods across the top: one column per week / month / quarter / year
# ---------------------------------------------------------------------------

PERIODS = {"total": "Total only", "week": "By week", "month": "By month", "quarter": "By quarter",
           "year": "By year"}
MAX_PERIOD_COLUMNS = 60


def periods(start: str, end: str, kind: str, fy_month: int = 1) -> list[tuple[str, str, str]]:
    """[(label, start, end)] covering start..end, cut by week (Mon–Sun), month, quarter or (fiscal) year;
    the first and last periods are trimmed to the range."""
    import calendar

    s, e = date.fromisoformat(start), date.fromisoformat(end)
    out = []
    cur = s
    while cur <= e and len(out) < MAX_PERIOD_COLUMNS:
        if kind == "week":
            p_end = cur + timedelta(days=6 - cur.weekday())
            label = f"Wk {cur:%b} {cur.day}"
        elif kind == "month":
            p_end = date(cur.year, cur.month, calendar.monthrange(cur.year, cur.month)[1])
            label = f"{cur:%b %Y}"
        elif kind == "quarter":
            q_last = ((cur.month - 1) // 3) * 3 + 3
            p_end = date(cur.year, q_last, calendar.monthrange(cur.year, q_last)[1])
            label = f"Q{(cur.month - 1) // 3 + 1} {cur.year}"
        elif kind == "year":
            fy_end_year = cur.year if cur.month < fy_month or fy_month == 1 else cur.year + 1
            if fy_month == 1:
                p_end = date(cur.year, 12, 31)
                label = str(cur.year)
            else:
                end_month = fy_month - 1
                p_end = date(fy_end_year, end_month, calendar.monthrange(fy_end_year, end_month)[1])
                label = f"FY {fy_end_year}"
        else:
            raise ValueError(kind)
        p_end = min(p_end, e)
        out.append((label, cur.isoformat(), p_end.isoformat()))
        cur = p_end + timedelta(days=1)
    return out


def _line_key(line: "Line", seen: dict) -> tuple:
    base = ("acct", line.account_id, line.label) if line.account_id is not None else ("label", line.kind, line.label)
    n = seen.get(base, 0)
    seen[base] = n + 1
    return base + (n,)


def build_by_period(books: Books, name: str, start: str, end: str, kind: str) -> Report:
    """A statement with the periods across the top and the accounts down the side.

    Income statement and cash flow: one column per period plus a Total. Balance sheet: one column per period end.
    Trial balance has no period columns (it's a single date)."""
    fn = REPORTS[name]
    if kind == "total" or fn == "trial_balance":
        return build(books, name, start, end)
    spans = periods(start, end, kind, books.fiscal_year_start_month)
    whole = build(books, name, start, end)            # the structure: every line over the whole range
    parts = [build(books, name, s, e) for _l, s, e in spans]
    keyed = []
    for rep in parts:
        seen: dict = {}
        keyed.append({_line_key(l, seen): l for l in rep.lines})
    out = Report(whole.title, whole.subtitle + f" · {PERIODS[kind].lower()}", [], [], company=whole.company,
                 start=whole.start, end=whole.end)
    labels = [lbl for lbl, _s, _e in spans]
    point_in_time = fn == "balance_sheet"
    if point_in_time:
        out.subtitle = f"{_long_date(spans[0][2])} – {_long_date(spans[-1][2])} · at each {kind} end"
    out.columns = labels + ([] if point_in_time else ["Total"])
    out.spans = [(s_, e_) for _l, s_, e_ in spans]
    seen = {}
    for line in whole.lines:
        key = _line_key(line, seen)
        if line.kind == "spacer" or not line.values:
            out.lines.append(Line(line.label, [], line.kind, line.indent, line.account_id))
            continue
        vals = []
        for kd in keyed:
            other = kd.get(key)
            vals.append(other.values[0] if other and other.values else (0 if line.kind != "header" else None))
        if not point_in_time:
            vals.append(line.values[0])
        out.lines.append(Line(line.label, vals, line.kind, line.indent, line.account_id))
    out.checks = whole.checks
    for rep in parts:
        for k, ok in rep.checks.items():
            out.checks[k] = out.checks.get(k, True) and ok
    return out
