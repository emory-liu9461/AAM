"""Change history: who changed what and when, in a company file and in the portfolio file.

The file itself keeps the record: database triggers write a line to ``change_log`` whenever a tracked row is added,
changed or deleted — so changes are recorded whichever window (or program) makes them. Each line has the time, the
person (the Windows user name and computer, set when the file is opened), what kind of record, what happened and the
details (for a change: each field's old → new value).

Tracked in a company file: accounts, bank transactions (date, description, amount, category, note), journal entries
entered by hand, invoices and bills, customers and vendors, rules. In the portfolio file: holdings, valuations, capital
flows, loans, owners, deals, deposits & costs, units, capex lines, budgets, unit turnovers.

The triggers are put in once (and again when this list changes); opening a file doesn't rewrite them, so it doesn't
change the file each time.
"""

from __future__ import annotations

import getpass
import socket
from dataclasses import dataclass

VERSION = "4"

LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS change_log (
    id       INTEGER PRIMARY KEY,
    at       TEXT NOT NULL,
    who      TEXT NOT NULL DEFAULT '',
    tbl      TEXT NOT NULL,
    row_id   INTEGER,
    action   TEXT NOT NULL,        -- added | changed | deleted
    summary  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_change_log_at ON change_log(at);
CREATE TABLE IF NOT EXISTS am_session (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# table: (what it is, [(column, label)] shown / compared, condition for rows to track (or ""))
BOOKS = {
    "accounts": ("Account", [("code", "Number"), ("name", "Name"), ("type", "Type"), ("subtype", "Group"),
                             ("active", "Active"), ("parent_id", "Sub-account of")], ""),
    "transactions": ("Bank transaction", [("date", "Date"), ("description", "Description"),
                                          ("amount_cents", "Amount"), ("category_id", "Category"),
                                          ("memo", "Note")], ""),
    "journal_entries": ("Journal entry", [("date", "Date"), ("memo", "Memo"), ("kind", "Kind")],
                        "{r}.transaction_id IS NULL"),
    "documents": ("Invoice / bill", [("kind", "Kind"), ("party", "Customer / vendor"), ("number", "Number"),
                                     ("issue_date", "Date"), ("due_date", "Due"), ("amount_cents", "Amount"),
                                     ("category_id", "Account")], ""),
    "contacts": ("Customer / vendor", [("name", "Name"), ("role", "Role"), ("email", "Email"), ("phone", "Phone"),
                                       ("active", "Active")], ""),
    "rules": ("Rule", [("pattern", "Text"), ("match_type", "Match"), ("account_id", "Category"),
                       ("direction", "Direction")], ""),
}
PORTFOLIO = {
    "holdings": ("Holding", [("name", "Name"), ("type", "Type"), ("ownership_pct", "Own %"), ("acquired", "Acquired"),
                             ("status", "Status"), ("company_path", "Books")], ""),
    "valuations": ("Valuation", [("date", "Date"), ("value_cents", "Equity value"),
                                 ("company_value_cents", "Company value"), ("method", "Method")], ""),
    "flows": ("Capital flow", [("date", "Date"), ("kind", "Kind"), ("amount_cents", "Amount"), ("note", "Note")], ""),
    "loans": ("Loan", [("lender", "Lender"), ("kind", "Kind"), ("principal_cents", "Principal"),
                       ("rate_pct", "Rate %"), ("start_date", "Start"), ("maturity", "Maturity")], ""),
    "shareholders": ("Owner", [("name", "Name"), ("company", "Company"), ("share_class", "Class"),
                               ("invested_cents", "Equity"), ("funded_cents", "Funded"),
                               ("funded_date", "Funded on")], ""),
    "deals": ("Deal", [("name", "Name"), ("stage", "Stage"), ("price_cents", "Price"),
                       ("closing_date", "Closing")], ""),
    "deal_costs": ("Deal deposit / cost", [("item", "For"), ("vendor", "Vendor"), ("amount_cents", "Amount"),
                                           ("payer_name", "Paid by"), ("how", "In the books"),
                                           ("settled", "Settled")], ""),
    "units": ("Unit", [("unit", "Unit"), ("tenant", "Tenant"), ("status", "Status"), ("rent_cents", "Rent"),
                       ("market_cents", "Market rent"), ("lease_end", "Lease end")], ""),
    "capex_lines": ("CapEx line", [("item", "Item"), ("budget_cents", "Budget"), ("manual_cents", "Cost (by hand)")],
                    ""),
    "budget_lines": ("Budget line", [("year", "Year"), ("item", "Line"), ("months", "Months")], ""),
    "unit_turns": ("Unit turnover", [("unit", "Unit"), ("move_out", "Moved out"), ("move_in", "Moved in"),
                                     ("new_rent", "New rent")], ""),
}
ACCOUNT_COLS = ("category_id", "account_id", "parent_id")


def current_user() -> str:
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "unknown"
    try:
        host = socket.gethostname()
    except Exception:  # noqa: BLE001
        host = ""
    return f"{user} on {host}" if host else user


def _val(col: str, ref: str, books: bool) -> str:
    if col.endswith("_cents"):
        return f"COALESCE(printf('%.2f', {ref}.{col} / 100.0), '')"
    if books and col in ACCOUNT_COLS:
        return f"COALESCE((SELECT name FROM accounts WHERE id = {ref}.{col}), '')"
    return f"COALESCE({ref}.{col}, '')"


def _title(table: str, cols, ref: str, books: bool, have_holding: bool) -> str:
    parts = [_val(c, ref, books) for c, _l in cols[:3]]
    expr = " || ' · ' || ".join(parts) if parts else "''"
    if have_holding:
        expr = f"COALESCE((SELECT name FROM holdings WHERE id = {ref}.holding_id) || ': ', '') || {expr}"
    return expr


def _triggers(table: str, label: str, cols, cond: str, books: bool, have_holding: bool) -> list[str]:
    who = "(SELECT value FROM am_session WHERE key = 'user')"
    at = "strftime('%Y-%m-%d %H:%M:%S', 'now', 'localtime')"

    def when(ref: str) -> str:
        return f"WHEN {cond.format(r=ref)}" if cond else ""

    out = []
    ins = _title(table, cols, "NEW", books, have_holding)
    out.append(f"CREATE TRIGGER am_log_{table}_ins AFTER INSERT ON {table} {when('NEW')} BEGIN "
               f"INSERT INTO change_log(at, who, tbl, row_id, action, summary) VALUES ({at}, COALESCE({who}, ''), "
               f"'{label}', NEW.id, 'added', {ins}); END")
    def shown(c: str, ref: str) -> str:
        return f"COALESCE(NULLIF({_val(c, ref, books)}, ''), '(none)')"

    diffs = "rtrim(" + " || ".join(
        f"CASE WHEN OLD.{c} IS NOT NEW.{c} THEN '{lab}: ' || {shown(c, 'OLD')} || ' → ' || "
        f"{shown(c, 'NEW')} || '; ' ELSE '' END" for c, lab in cols) + ", '; ')"
    changed = " OR ".join(f"OLD.{c} IS NOT NEW.{c}" for c, _l in cols)
    cond_upd = f"({changed})" + (f" AND {cond.format(r='NEW')}" if cond else "")
    out.append(f"CREATE TRIGGER am_log_{table}_upd AFTER UPDATE ON {table} WHEN {cond_upd} BEGIN "
               f"INSERT INTO change_log(at, who, tbl, row_id, action, summary) VALUES ({at}, COALESCE({who}, ''), "
               f"'{label}', NEW.id, 'changed', {_title(table, cols, 'NEW', books, have_holding)} || ' — ' || {diffs});"
               f" END")
    out.append(f"CREATE TRIGGER am_log_{table}_del BEFORE DELETE ON {table} {when('OLD')} BEGIN "
               f"INSERT INTO change_log(at, who, tbl, row_id, action, summary) VALUES ({at}, COALESCE({who}, ''), "
               f"'{label}', OLD.id, 'deleted', {_title(table, cols, 'OLD', books, have_holding)}); END")
    return out


def install(conn, which: str = "books", *, user: str | None = None) -> None:
    """Put the change log and its triggers in the file (once per version, and for tables added since), and record
    who has it open (only written when it changes, so opening a file doesn't change it)."""
    spec = BOOKS if which == "books" else PORTFOLIO
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    trig = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    tracked = sorted(t for t in spec if t in have)
    sig = VERSION + ":" + ",".join(tracked)
    cur = None
    if "am_session" in have:
        r = conn.execute("SELECT value FROM am_session WHERE key='log_version'").fetchone()
        cur = r[0] if r else None
    user = user or current_user()
    if cur != sig or not all(f"am_log_{t}_ins" in trig for t in tracked):
        conn.executescript(LOG_SCHEMA)
        with conn:
            for t in spec:
                for kind in ("ins", "upd", "del"):
                    conn.execute(f"DROP TRIGGER IF EXISTS am_log_{t}_{kind}")
            for t in tracked:
                cols_in = {r[1] for r in conn.execute(f"PRAGMA table_info({t})")}
                label, cols, cond = spec[t]
                cols = [(c, lab) for c, lab in cols if c in cols_in]
                if not cols or "id" not in cols_in:
                    continue
                for sql in _triggers(t, label, cols, cond, which == "books", "holding_id" in cols_in):
                    conn.execute(sql)
            conn.execute("INSERT OR REPLACE INTO am_session(key, value) VALUES ('log_version', ?)", (sig,))
    r = conn.execute("SELECT value FROM am_session WHERE key='user'").fetchone()
    if not r or r[0] != user:
        with conn:
            conn.execute("INSERT OR REPLACE INTO am_session(key, value) VALUES ('user', ?)", (user,))


@dataclass
class Change:
    at: str
    who: str
    what: str
    action: str
    summary: str
    row_id: int | None = None


def changes(conn, *, search: str = "", what: str = "", limit: int = 2000) -> list[Change]:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "change_log" not in have:
        return []
    sql = "SELECT * FROM change_log WHERE 1=1"
    args: list = []
    if what:
        sql += " AND tbl = ?"
        args.append(what)
    if search.strip():
        sql += " AND (summary LIKE ? OR who LIKE ? OR tbl LIKE ?)"
        args += [f"%{search.strip()}%"] * 3
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    return [Change(r[1], r[2], r[3], r[5], r[6], r[4]) for r in conn.execute(sql, args)]


def kinds(conn) -> list[str]:
    try:
        return [r[0] for r in conn.execute("SELECT DISTINCT tbl FROM change_log ORDER BY tbl")]
    except Exception:  # noqa: BLE001
        return []
