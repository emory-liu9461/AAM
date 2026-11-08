"""The company file ("books"): a single SQLite database.

Every bank transaction is posted to the general ledger as a balanced
double-entry journal entry:

    deposit    (amount > 0):  Dr bank account   / Cr category
    withdrawal (amount < 0):  Dr category       / Cr bank account

Transactions without a category post to Uncategorized Income/Expense, so the
ledger and every report always balance, even before categorization is done.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

from . import chart_of_accounts as coa

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS accounts (
    id       INTEGER PRIMARY KEY,
    code     TEXT NOT NULL UNIQUE,
    name     TEXT NOT NULL,
    type     TEXT NOT NULL CHECK (type IN ('asset','liability','equity','income','expense')),
    subtype  TEXT NOT NULL DEFAULT '',
    is_bank  INTEGER NOT NULL DEFAULT 0,
    active   INTEGER NOT NULL DEFAULT 1,
    parent_id INTEGER REFERENCES accounts(id),
    std_key  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS imports (
    id              INTEGER PRIMARY KEY,
    bank_account_id INTEGER NOT NULL REFERENCES accounts(id),
    filename        TEXT NOT NULL,
    file_hash       TEXT NOT NULL,
    imported_at     TEXT NOT NULL,
    row_count       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id              INTEGER PRIMARY KEY,
    bank_account_id INTEGER NOT NULL REFERENCES accounts(id),
    date            TEXT NOT NULL,
    description     TEXT NOT NULL,
    amount_cents    INTEGER NOT NULL,
    category_id     INTEGER REFERENCES accounts(id),
    category_source TEXT,
    reviewed        INTEGER NOT NULL DEFAULT 0,
    memo            TEXT NOT NULL DEFAULT '',
    merchant_key    TEXT NOT NULL DEFAULT '',
    fingerprint     TEXT NOT NULL,
    import_id       INTEGER REFERENCES imports(id) ON DELETE SET NULL,
    UNIQUE (bank_account_id, fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_tx_date ON transactions(date);
CREATE TABLE IF NOT EXISTS tx_splits (
    id             INTEGER PRIMARY KEY,
    transaction_id INTEGER NOT NULL REFERENCES transactions(id) ON DELETE CASCADE,
    account_id     INTEGER NOT NULL REFERENCES accounts(id),
    amount_cents   INTEGER NOT NULL CHECK (amount_cents > 0),
    memo           TEXT NOT NULL DEFAULT '',
    ref            TEXT NOT NULL DEFAULT ''      -- e.g. 'loan:3' for a loan payment
);
CREATE INDEX IF NOT EXISTS ix_split_tx ON tx_splits(transaction_id);
CREATE INDEX IF NOT EXISTS ix_tx_merchant ON transactions(merchant_key);

CREATE TABLE IF NOT EXISTS rules (
    id         INTEGER PRIMARY KEY,
    pattern    TEXT NOT NULL,
    match_type TEXT NOT NULL DEFAULT 'contains' CHECK (match_type IN ('contains','regex','exact')),
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    direction  TEXT NOT NULL DEFAULT 'any' CHECK (direction IN ('any','in','out')),
    priority   INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id             INTEGER PRIMARY KEY,
    date           TEXT NOT NULL,
    memo           TEXT NOT NULL DEFAULT '',
    kind           TEXT NOT NULL DEFAULT 'manual' CHECK (kind IN ('transaction','opening','manual')),
    transaction_id INTEGER UNIQUE REFERENCES transactions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_je_date ON journal_entries(date);

CREATE TABLE IF NOT EXISTS journal_lines (
    id           INTEGER PRIMARY KEY,
    entry_id     INTEGER NOT NULL REFERENCES journal_entries(id) ON DELETE CASCADE,
    account_id   INTEGER NOT NULL REFERENCES accounts(id),
    debit_cents  INTEGER NOT NULL DEFAULT 0,
    credit_cents INTEGER NOT NULL DEFAULT 0,
    party        TEXT NOT NULL DEFAULT ''        -- customer / vendor on a hand-made entry's line
);
CREATE INDEX IF NOT EXISTS ix_jl_entry ON journal_lines(entry_id);
CREATE INDEX IF NOT EXISTS ix_jl_account ON journal_lines(account_id);
"""


@dataclass
class Account:
    id: int
    code: str
    name: str
    type: str
    subtype: str
    is_bank: bool
    active: bool
    parent_id: int | None = None
    key: str = ""  # permanent internal key for built-in accounts ("" for accounts you create)

    @property
    def is_system(self) -> bool:
        return self.key in coa.ROLE_KEYS

    @property
    def label(self) -> str:
        return f"{self.code} · {self.name}"

    @property
    def debit_normal(self) -> bool:
        return self.type in ("asset", "expense")


@dataclass
class CompanyProfile:
    name: str
    legal_name: str = ""
    entity_type: str = ""  # e.g. Sole proprietor, LLC, S corporation
    tax_id: str = ""
    fiscal_year_start_month: int = 1
    role: str = "operating"  # operating | holding | asset_manager (see company_roles.py)


@dataclass
class Transaction:
    id: int
    bank_account_id: int
    date: str
    description: str
    amount_cents: int
    category_id: int | None
    category_source: str | None
    reviewed: bool
    memo: str
    merchant_key: str
    manual: bool = False      # entered by hand (not from a statement)


@dataclass
class Rule:
    id: int
    pattern: str
    match_type: str
    account_id: int
    direction: str
    priority: int


@dataclass
class ParsedTransaction:
    """A transaction read from a statement, before it is saved."""

    date: date
    description: str
    amount_cents: int  # positive = money in, negative = money out
    memo: str = ""


# ---------------------------------------------------------------------------
# Merchant normalisation (used for de-duplication and learned categorisation)
# ---------------------------------------------------------------------------

_NOISE_WORDS = {
    "POS", "PURCHASE", "DEBIT", "CREDIT", "CARD", "CHECKCARD", "CHECK", "ACH",
    "WITHDRAWAL", "DEPOSIT", "ONLINE", "PAYMENT", "RECURRING", "VISA", "MC",
    "MASTERCARD", "DBT", "PREAUTHORIZED", "PRE", "AUTH", "AUTHORIZED", "ON",
    "TRANSACTION", "ELECTRONIC", "EFT", "WEB", "PPD", "CCD", "ID", "REF", "INST",
    "XFER", "PMT", "SQ", "TST", "PAYPAL", "INC", "LLC", "CO", "THE", "FROM", "TO",
}


def merchant_key(description: str) -> str:
    """Reduce a bank description to a stable merchant key.

    ``"POS PURCHASE 03/14 STARBUCKS #1234 SEATTLE WA"`` -> ``"STARBUCKS SEATTLE WA"``
    """
    s = description.upper()
    s = re.sub(r"[*#]\S*", " ", s)               # card refs like *1234, #556
    s = re.sub(r"\S*\d\S*", " ", s)               # any token containing a digit
    s = re.sub(r"[^A-Z&' ]", " ", s)
    words = [w for w in s.split() if w not in _NOISE_WORDS and len(w) > 1]
    return " ".join(words[:3])


MANUAL_PREFIX = "manual:"


class _Connection(sqlite3.Connection):
    """A connection whose ``with conn:`` blocks don't commit while a ``Books.batch()`` is open, so a bulk change
    (hundreds of categories) is written to disk once instead of once per transaction."""

    batch_depth = 0

    def __exit__(self, exc_type, exc, tb):
        if self.batch_depth:
            return False  # the batch commits (or rolls back) at its end
        return super().__exit__(exc_type, exc, tb)


def fingerprint(tx: ParsedTransaction, occurrence: int) -> str:
    raw = f"{tx.date.isoformat()}|{tx.amount_cents}|{' '.join(tx.description.upper().split())}|{occurrence}"
    return hashlib.sha1(raw.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Books
# ---------------------------------------------------------------------------


class Books:
    """A company file. Open with ``Books.open(path)`` or ``Books.create(path)``."""

    def __init__(self, conn: sqlite3.Connection, path: str):
        self.conn = conn
        self.path = path
        self._accounts_cache: dict[int, Account] | None = None

    # -- lifecycle ----------------------------------------------------------

    @classmethod
    def open(cls, path: str | Path, *, create: bool = True, template: str = "general") -> "Books":
        path = str(path)
        is_new = path == ":memory:" or not Path(path).exists()
        if is_new and not create:
            raise FileNotFoundError(path)
        conn = sqlite3.connect(path, factory=_Connection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
        if cols and "parent_id" not in cols:  # company file from an earlier version
            conn.execute("ALTER TABLE accounts ADD COLUMN parent_id INTEGER REFERENCES accounts(id)")
            conn.execute("ALTER TABLE accounts ADD COLUMN std_key TEXT NOT NULL DEFAULT ''")
            conn.executemany("UPDATE accounts SET std_key=? WHERE code=?",
                             [(c, c) for c, *_ in coa.DEFAULT_ACCOUNTS])
            conn.commit()
        conn.executescript(SCHEMA)
        if "party" not in {r[1] for r in conn.execute("PRAGMA table_info(journal_lines)")}:
            conn.execute("ALTER TABLE journal_lines ADD COLUMN party TEXT NOT NULL DEFAULT ''")
            conn.commit()
        books = cls(conn, path)
        if books._meta("schema_version") is None:
            books._initialise(template)
        from . import history

        try:          # the change history (see history.py)
            history.install(conn, "books")
        except sqlite3.DatabaseError:
            pass       # a read-only file: no history written
        return books

    create = open

    def close(self) -> None:
        self.conn.close()

    def _initialise(self, template: str = "general") -> None:
        if template not in coa.TEMPLATES:
            raise ValueError(f"Unknown chart of accounts template {template!r}")
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO accounts(std_key, code, name, type, subtype, is_bank) VALUES (?,?,?,?,?,?)",
                [(k, c, n, t, s, int(b)) for k, c, n, t, s, b in coa.TEMPLATES[template][1]],
            )
            self._set_meta("coa_template", template)
            self._set_meta("schema_version", str(SCHEMA_VERSION))
            self._set_meta("company_name", "My Company")
            self._set_meta("created_at", datetime.now().isoformat(timespec="seconds"))

    def _meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))

    @property
    def company_name(self) -> str:
        return self._meta("company_name") or "My Company"

    @company_name.setter
    def company_name(self, value: str) -> None:
        with self.conn:
            self._set_meta("company_name", value)

    @property
    def fiscal_year_start_month(self) -> int:
        try:
            m = int(self._meta("fiscal_year_start_month") or 1)
        except ValueError:
            return 1
        return m if 1 <= m <= 12 else 1

    def profile(self) -> CompanyProfile:
        return CompanyProfile(self.company_name, self._meta("legal_name") or "", self._meta("entity_type") or "",
                              self._meta("tax_id") or "", self.fiscal_year_start_month,
                              self._meta("company_role") or "operating")

    def set_profile(self, profile: CompanyProfile) -> None:
        if not profile.name.strip():
            raise ValueError("Company name is required")
        if not 1 <= profile.fiscal_year_start_month <= 12:
            raise ValueError("Fiscal year start month must be 1-12")
        with self.conn:
            self._set_meta("company_name", profile.name.strip())
            self._set_meta("legal_name", profile.legal_name.strip())
            self._set_meta("entity_type", profile.entity_type.strip())
            self._set_meta("tax_id", profile.tax_id.strip())
            self._set_meta("fiscal_year_start_month", str(profile.fiscal_year_start_month))

    def stats(self) -> dict:
        row = self.conn.execute(
            "SELECT COUNT(*), SUM(category_id IS NULL), MIN(date), MAX(date) FROM transactions").fetchone()
        return {"transactions": row[0], "uncategorized": row[1] or 0, "first": row[2], "last": row[3]}

    def copy_setup_from(self, source: "Books") -> None:
        """Copy the chart of accounts (with sub-accounts), statement headings and rules from another company.

        Accounts are matched by number; bank accounts are not copied (each company
        has its own), and no transactions or balances are copied.
        """
        with self.conn:
            for a in source.accounts():
                if a.is_bank:
                    continue
                existing = self.conn.execute("SELECT id FROM accounts WHERE code=? OR (std_key!='' AND std_key=?)",
                                             (a.code, a.key)).fetchone()
                if existing:
                    self.conn.execute("UPDATE accounts SET code=?, name=?, type=?, subtype=?, active=?, std_key=? "
                                      "WHERE id=?", (a.code, a.name, a.type, a.subtype, int(a.active), a.key,
                                                     existing[0]))
                else:
                    self.conn.execute("INSERT INTO accounts(code, name, type, subtype, is_bank, active, std_key) "
                                      "VALUES (?,?,?,?,0,?,?)", (a.code, a.name, a.type, a.subtype, int(a.active),
                                                                 a.key))
            self._invalidate()
            codes = {a.code: a.id for a in self.accounts()}
            for a in source.accounts():
                if not a.is_bank and a.parent_id is not None:
                    parent = source.account(a.parent_id)
                    if parent.code in codes:
                        self.conn.execute("UPDATE accounts SET parent_id=? WHERE id=?",
                                          (codes[parent.code], codes[a.code]))
            for r in source.rules():
                src = source.account(r.account_id)
                if src.code in codes and not src.is_bank:
                    self.conn.execute(
                        "INSERT INTO rules(pattern, match_type, account_id, direction, priority) VALUES (?,?,?,?,?)",
                        (r.pattern, r.match_type, codes[src.code], r.direction, r.priority))
            for key, value in source.conn.execute("SELECT key, value FROM meta WHERE key LIKE 'label:%' "
                                                  "OR key='builtin_suggestions'"):
                self._set_meta(key, value)
        self._invalidate()

    # -- statement headings & preferences -----------------------------------

    def labels(self) -> dict[str, str]:
        """Statement headings: defaults for the business type, overridden by any you've customized."""
        out = coa.default_labels(self.profile().entity_type)
        for key, value in self.conn.execute("SELECT key, value FROM meta WHERE key LIKE 'label:%'"):
            if value:
                out[key[6:]] = value
        return out

    def set_labels(self, labels: dict[str, str]) -> None:
        """Save custom headings. Blank or default values revert to the default."""
        defaults = coa.default_labels(self.profile().entity_type)
        with self.conn:
            for k, v in labels.items():
                if k not in coa.LABELS:
                    raise KeyError(k)
                v = (v or "").strip()
                if not v or v == defaults[k]:
                    self.conn.execute("DELETE FROM meta WHERE key=?", (f"label:{k}",))
                else:
                    self._set_meta(f"label:{k}", v)

    @property
    def builtin_suggestions(self) -> bool:
        return self._meta("builtin_suggestions") != "0"

    @builtin_suggestions.setter
    def builtin_suggestions(self, on: bool) -> None:
        with self.conn:
            self._set_meta("builtin_suggestions", "1" if on else "0")

    # -- accounts -----------------------------------------------------------

    def _invalidate(self) -> None:
        self._accounts_cache = None

    def batch(self):
        """``with books.batch():`` — many changes, one write to disk (all or nothing)."""
        from contextlib import contextmanager

        @contextmanager
        def _batch():
            conn = self.conn
            conn.batch_depth += 1
            try:
                yield self
            except BaseException:
                conn.batch_depth -= 1
                if not conn.batch_depth:
                    conn.rollback()
                raise
            conn.batch_depth -= 1
            if not conn.batch_depth:
                conn.commit()

        return _batch()

    def first_time(self, key: str) -> bool:
        """True the first time ``key`` is asked about on this open file (e.g. to create a module's tables once)."""
        done = self.__dict__.setdefault("_done_once", set())
        if key in done:
            return False
        done.add(key)
        return True

    def change_key(self) -> tuple[int, int]:
        """Changes whenever the file is written (by this connection or another): for caching what's read from it."""
        return self.conn.total_changes, self.conn.execute("PRAGMA data_version").fetchone()[0]

    def _accounts(self) -> dict[int, Account]:
        # another connection (e.g. Asset Management adding a loan account) changed the file: read again
        version = self.conn.execute("PRAGMA data_version").fetchone()[0]
        if version != getattr(self, "_accounts_version", None):
            self._accounts_cache, self._accounts_version = None, version
        if self._accounts_cache is None:
            rows = self.conn.execute("SELECT * FROM accounts ORDER BY code").fetchall()
            self._accounts_cache = {
                r["id"]: Account(r["id"], r["code"], r["name"], r["type"], r["subtype"],
                                 bool(r["is_bank"]), bool(r["active"]), r["parent_id"], r["std_key"] or "")
                for r in rows
            }
        return self._accounts_cache

    def accounts(self, *, active_only: bool = False, types: Iterable[str] | None = None) -> list[Account]:
        out = list(self._accounts().values())
        if active_only:
            out = [a for a in out if a.active]
        if types is not None:
            ts = set(types)
            out = [a for a in out if a.type in ts]
        return out

    def bank_accounts(self) -> list[Account]:
        return [a for a in self.accounts(active_only=True) if a.is_bank]

    def account(self, account_id: int) -> Account:
        return self._accounts()[account_id]

    def account_by_code(self, code: str) -> Account:
        for a in self._accounts().values():
            if a.code == code:
                return a
        raise KeyError(code)

    def account_by_key(self, key: str) -> Account:
        """Built-in account by its permanent key (unaffected by renaming or renumbering)."""
        for a in self._accounts().values():
            if a.key == key:
                return a
        raise KeyError(key)

    def role(self, key: str) -> Account:
        """An account the app relies on (see ``coa.ROLE_KEYS``); recreated if it's somehow missing."""
        try:
            return self.account_by_key(key)
        except KeyError:
            entry = next(e for e in coa.TEMPLATES["minimal"][1] + coa.TEMPLATES["general"][1] if e[0] == key)
            _k, code, name, type_, subtype, _b = entry
            while any(a.code == code for a in self.accounts()):
                code += "-1"
            with self.conn:
                self.conn.execute("INSERT INTO accounts(std_key, code, name, type, subtype) VALUES (?,?,?,?,?)",
                                  (key, code, name, type_, subtype))
            self._invalidate()
            return self.account_by_key(key)

    def children(self, account_id: int) -> list[Account]:
        return [a for a in self.accounts() if a.parent_id == account_id]

    def _check_parent(self, account_id: int | None, parent_id: int | None, type_: str) -> Account | None:
        if parent_id is None:
            return None
        parent = self.account(parent_id)
        if parent.type != type_:
            raise ValueError("A sub-account must be the same type as its parent account")
        if parent.is_bank:
            raise ValueError("Bank accounts can't have sub-accounts")
        p: Account | None = parent
        while p is not None:
            if p.id == account_id:
                raise ValueError("An account can't be a sub-account of itself or of its own sub-accounts")
            p = self.account(p.parent_id) if p.parent_id else None
        return parent

    def _validate_names(self, code: str, name: str, type_: str, subtype: str, account_id: int | None) -> None:
        if not code.strip() or not name.strip():
            raise ValueError("Account number and name are required")
        if type_ not in coa.ACCOUNT_TYPES:
            raise ValueError(f"Unknown account type {type_!r}")
        if subtype and subtype not in coa.SUBTYPES[type_]:
            raise ValueError(f"{subtype!r} isn't a group for {type_} accounts")
        for a in self.accounts():
            if a.id != account_id and a.code == code.strip():
                raise ValueError(f"Account number {code.strip()} is already used by “{a.name}”")

    def add_account(self, code: str, name: str, type_: str, subtype: str = "", *, is_bank: bool = False,
                    parent_id: int | None = None, active: bool = True) -> Account:
        if is_bank:
            type_, subtype, parent_id = "asset", "bank", None
        subtype = subtype or next(iter(coa.SUBTYPES.get(type_, {"": ""})))
        if not is_bank and subtype == "bank":
            raise ValueError("Use “bank account” to create a bank account")
        self._validate_names(code, name, type_, subtype, None)
        parent = self._check_parent(None, parent_id, type_)
        if parent:
            subtype = parent.subtype
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO accounts(code, name, type, subtype, is_bank, parent_id, active) VALUES (?,?,?,?,?,?,?)",
                (code.strip(), name.strip(), type_, subtype, int(is_bank), parent_id, int(active)),
            )
        self._invalidate()
        return self.account(cur.lastrowid)

    def update_account(self, account_id: int, *, code: str, name: str, type_: str, subtype: str,
                       active: bool = True, parent_id: int | None | type(...) = ...) -> None:
        """Change any detail of an account. Sub-accounts follow their parent's type and group."""
        acct = self.account(account_id)
        if parent_id is ...:
            parent_id = acct.parent_id
        if acct.is_bank:
            type_, subtype, parent_id = "asset", "bank", None
        elif subtype == "bank":
            raise ValueError("Only bank accounts can be in the Bank / cash group")
        if acct.is_system:
            if type_ != acct.type:
                raise ValueError(f"“{acct.name}” is used by the app ({coa.ROLE_KEYS[acct.key].lower()}); "
                                 "its type can't change, but you can rename or renumber it")
            if not active:
                raise ValueError(f"“{acct.name}” is used by the app and can't be made inactive")
        self._validate_names(code, name, type_, subtype, account_id)
        parent = self._check_parent(account_id, parent_id, type_)
        if parent:
            subtype = parent.subtype
        if type_ != acct.type and self.conn.execute(
                "SELECT 1 FROM journal_lines WHERE account_id=? LIMIT 1", (account_id,)).fetchone():
            pass  # allowed: reclassifying an account is a legitimate correction; reports follow the new type
        with self.conn:
            self.conn.execute(
                "UPDATE accounts SET code=?, name=?, type=?, subtype=?, active=?, parent_id=? WHERE id=?",
                (code.strip(), name.strip(), type_, subtype, int(active), parent_id, account_id),
            )
            # Sub-accounts move with their parent.
            stack = [account_id]
            while stack:
                pid = stack.pop()
                for (cid,) in self.conn.execute("SELECT id FROM accounts WHERE parent_id=?", (pid,)).fetchall():
                    self.conn.execute("UPDATE accounts SET type=?, subtype=? WHERE id=?", (type_, subtype, cid))
                    stack.append(cid)
            if acct.code != code.strip() and self._meta("inbox_default_account") == acct.code:
                self._set_meta("inbox_default_account", code.strip())
        self._invalidate()

    def _has_table(self, name: str) -> bool:
        known = self.__dict__.setdefault("_tables_seen", set())  # tables are never dropped, so a yes stays a yes
        if name in known:
            return True
        found = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() \
            is not None
        if found:
            known.add(name)
        return found

    def account_usage(self, account_id: int) -> dict[str, int]:
        q = lambda sql: self.conn.execute(sql, (account_id,) * sql.count("?")).fetchone()[0]  # noqa: E731
        docs = q("SELECT COUNT(*) FROM documents WHERE category_id=? OR control_id=?") \
            if self._has_table("documents") else 0
        return {
            "documents": docs,
            "transactions": q("SELECT COUNT(*) FROM transactions WHERE category_id=? OR bank_account_id=?"),
            "journal_lines": q("SELECT COUNT(*) FROM journal_lines WHERE account_id=?"),
            "rules": q("SELECT COUNT(*) FROM rules WHERE account_id=?"),
            "sub_accounts": q("SELECT COUNT(*) FROM accounts WHERE parent_id=?"),
        }

    def delete_account(self, account_id: int) -> None:
        """Delete an account that has never been used. Use ``merge_account`` for one that has."""
        acct = self.account(account_id)
        if acct.is_system:
            raise ValueError(f"“{acct.name}” is used by the app and can't be deleted (you can rename it)")
        use = self.account_usage(account_id)
        if use["transactions"] or use["journal_lines"] or use["documents"]:
            raise ValueError(f"“{acct.name}” has activity. Merge it into another account instead, "
                             "or make it inactive.")
        if use["sub_accounts"]:
            raise ValueError(f"“{acct.name}” has sub-accounts. Move or delete them first.")
        with self.conn:
            self.conn.execute("DELETE FROM rules WHERE account_id=?", (account_id,))
            self.conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        self._invalidate()

    def merge_account(self, source_id: int, target_id: int) -> None:
        """Move everything recorded in ``source`` to ``target`` and delete ``source``."""
        src, dst = self.account(source_id), self.account(target_id)
        if source_id == target_id:
            raise ValueError("Choose a different account to merge into")
        if src.is_system:
            raise ValueError(f"“{src.name}” is used by the app and can't be merged away")
        if src.is_bank != dst.is_bank:
            raise ValueError("Bank accounts can only be merged into other bank accounts")
        with self.conn:
            if self.conn.execute("SELECT 1 FROM transactions WHERE (bank_account_id=? AND category_id=?) "
                                 "OR (bank_account_id=? AND category_id=?) LIMIT 1",
                                 (source_id, target_id, target_id, source_id)).fetchone():
                raise ValueError("Some transactions move money between these two accounts; "
                                 "recategorize them before merging")
            self.conn.execute("UPDATE transactions SET category_id=? WHERE category_id=?", (target_id, source_id))
            self.conn.execute("UPDATE transactions SET bank_account_id=? WHERE bank_account_id=?",
                              (target_id, source_id))
            self.conn.execute("UPDATE journal_lines SET account_id=? WHERE account_id=?", (target_id, source_id))
            self.conn.execute("UPDATE rules SET account_id=? WHERE account_id=?", (target_id, source_id))
            if self._has_table("documents"):
                self.conn.execute("UPDATE documents SET category_id=? WHERE category_id=?", (target_id, source_id))
                self.conn.execute("UPDATE documents SET control_id=? WHERE control_id=?", (target_id, source_id))
            self.conn.execute("UPDATE accounts SET parent_id=? WHERE parent_id=?",
                              (None if dst.is_bank else target_id, source_id))
            self.conn.execute("DELETE FROM accounts WHERE id=?", (source_id,))
        self._invalidate()

    # -- chart of accounts as a spreadsheet ---------------------------------

    COA_COLUMNS = ["Number", "Name", "Type", "Group", "Parent Number", "Bank Account", "Active"]

    def export_accounts_rows(self) -> list[list[str]]:
        rows = [list(self.COA_COLUMNS)]
        for a in self.accounts():
            parent = self.account(a.parent_id).code if a.parent_id else ""
            rows.append([a.code, a.name, a.type, a.subtype, parent, "yes" if a.is_bank else "",
                         "yes" if a.active else "no"])
        return rows

    def import_accounts_rows(self, rows: list[list[str]]) -> tuple[int, int]:
        """Add or update accounts from rows shaped like ``export_accounts_rows``. Returns (added, updated).

        Accounts are matched by number. Type and group accept either the internal
        names (``expense``, ``operating``) or the labels shown in the app.
        Nothing is deleted. All rows are validated before anything is saved.
        """
        if not rows:
            return 0, 0
        header = [h.strip().lower() for h in rows[0]]
        col = {name: header.index(name.lower()) for name in self.COA_COLUMNS if name.lower() in header}
        for required in ("Number", "Name", "Type"):
            if required not in col:
                raise ValueError(f"Missing column “{required}”. Expected: {', '.join(self.COA_COLUMNS)}")

        def get(r, name):
            i = col.get(name)
            return r[i].strip() if i is not None and i < len(r) else ""

        type_lookup = {t: t for t in coa.ACCOUNT_TYPES} | {v.lower(): k for k, v in coa.TYPE_LABELS.items()}
        type_lookup |= {"liabilities": "liability", "assets": "asset", "expenses": "expense", "revenue": "income",
                        "shareholders' equity": "equity", "owner's equity": "equity"}
        parsed = []
        for n, r in enumerate(rows[1:], start=2):
            if not any(c.strip() for c in r):
                continue
            code, name = get(r, "Number"), get(r, "Name")
            type_ = type_lookup.get(get(r, "Type").lower())
            if not code or not name or not type_:
                raise ValueError(f"Row {n}: needs a number, a name and a type (asset, liability, equity, "
                                 f"income or expense)")
            g = get(r, "Group").lower()
            groups = coa.SUBTYPES[type_]
            subtype = g if g in groups else next((k for k, v in groups.items() if v.lower() == g), "")
            if g and not subtype:
                raise ValueError(f"Row {n}: “{get(r, 'Group')}” isn't a group for {type_} accounts. "
                                 f"Use one of: {', '.join(groups)}")
            is_bank = get(r, "Bank Account").lower() in ("yes", "y", "true", "1", "x")
            active = get(r, "Active").lower() not in ("no", "n", "false", "0")
            parsed.append((code, name, type_, subtype, is_bank, active, get(r, "Parent Number")))

        added = updated = 0
        snapshot = sqlite3.connect(":memory:")
        self.conn.backup(snapshot)  # all-or-nothing: restored if any row fails
        try:
            for code, name, type_, subtype, is_bank, active, _parent in parsed:
                existing = next((a for a in self.accounts() if a.code == code), None)
                if existing:
                    self.update_account(existing.id, code=code, name=name, type_=type_,
                                        subtype=subtype or existing.subtype, active=active)
                    updated += 1
                else:
                    self.add_account(code, name, type_, subtype, is_bank=is_bank, active=active)
                    added += 1
            for code, _n, _t, _s, _b, _a, parent in parsed:
                acct = self.account_by_code(code)
                try:
                    pid = self.account_by_code(parent).id if parent else None
                except KeyError:
                    raise ValueError(f"Account {code}: parent number {parent} doesn't exist") from None
                if pid != acct.parent_id:
                    self.update_account(acct.id, code=acct.code, name=acct.name, type_=acct.type,
                                        subtype=acct.subtype, active=acct.active, parent_id=pid)
        except Exception:
            snapshot.backup(self.conn)
            self._invalidate()
            raise
        finally:
            snapshot.close()
        self._invalidate()
        return added, updated

    def next_account_code(self, type_: str, *, is_bank: bool = False) -> str:
        base = {"asset": 1000, "liability": 2000, "equity": 3000, "income": 4000, "expense": 6000}[type_]
        if is_bank:
            base = 1000
        used = {a.code for a in self.accounts()}
        code = base
        while str(code) in used:
            code += 10 if is_bank else 1
        return str(code)

    def account_balance(self, account_id: int, *, as_of: str | None = None, start: str | None = None) -> int:
        """Signed balance in the account's normal direction (debit-normal for assets/expenses)."""
        sql = ("SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l "
               "JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id = ?")
        args: list = [account_id]
        if start:
            sql += " AND e.date >= ?"
            args.append(start)
        if as_of:
            sql += " AND e.date <= ?"
            args.append(as_of)
        net = self.conn.execute(sql, args).fetchone()[0]
        return net if self.account(account_id).debit_normal else -net

    # -- transactions -------------------------------------------------------

    def _row_to_tx(self, r: sqlite3.Row) -> Transaction:
        return Transaction(r["id"], r["bank_account_id"], r["date"], r["description"], r["amount_cents"],
                           r["category_id"], r["category_source"], bool(r["reviewed"]), r["memo"],
                           r["merchant_key"], str(r["fingerprint"]).startswith(MANUAL_PREFIX))

    def transaction(self, tx_id: int) -> Transaction:
        r = self.conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
        if r is None:
            raise KeyError(tx_id)
        return self._row_to_tx(r)

    def transactions(self, *, bank_account_id: int | None = None, status: str = "all",
                     search: str = "", start: str | None = None, end: str | None = None) -> list[Transaction]:
        """status: all | uncategorized | unreviewed | reviewed"""
        sql = "SELECT * FROM transactions WHERE 1=1"
        args: list = []
        if bank_account_id is not None:
            sql += " AND bank_account_id=?"
            args.append(bank_account_id)
        if status == "uncategorized":
            sql += " AND category_id IS NULL"
        elif status == "unreviewed":
            sql += " AND reviewed=0"
        elif status == "reviewed":
            sql += " AND reviewed=1"
        if search:  # description, memo, or category (name or number)
            sql += (" AND (description LIKE ? OR memo LIKE ? OR category_id IN "
                    "(SELECT id FROM accounts WHERE REPLACE(name, '&', 'and') LIKE ? OR code LIKE ?)"
                    + (" OR category_id IS NULL" if "uncategorized".startswith(search.lower()) and len(search) >= 4
                       else "") + ")")
            plain = search.replace("&", "and")  # "Bank & Merchant" and "bank and merchant" both match
            args += [f"%{search}%", f"%{search}%", f"%{plain}%", f"%{search}%"]
        if start:
            sql += " AND date >= ?"
            args.append(start)
        if end:
            sql += " AND date <= ?"
            args.append(end)
        sql += " ORDER BY date DESC, id DESC"
        return [self._row_to_tx(r) for r in self.conn.execute(sql, args)]

    def existing_fingerprints(self, bank_account_id: int) -> set[str]:
        rows = self.conn.execute("SELECT fingerprint FROM transactions WHERE bank_account_id=?", (bank_account_id,))
        return {r[0] for r in rows}

    @staticmethod
    def fingerprints_for(parsed: Sequence[ParsedTransaction]) -> list[str]:
        seen: Counter = Counter()
        out = []
        for tx in parsed:
            k = (tx.date, tx.amount_cents, " ".join(tx.description.upper().split()))
            out.append(fingerprint(tx, seen[k]))
            seen[k] += 1
        return out

    def import_transactions(self, bank_account_id: int, parsed: Sequence[ParsedTransaction], *,
                            filename: str = "", file_bytes: bytes = b"") -> tuple[int, int]:
        """Save parsed transactions. Returns ``(imported, skipped_duplicates)``.

        Re-importing the same statement (or overlapping statements) is safe:
        duplicates are detected by a fingerprint of date, amount, description
        and occurrence number.
        """
        if not self.account(bank_account_id).is_bank:
            raise ValueError("Transactions can only be imported into a bank account")
        existing = self.existing_fingerprints(bank_account_id)
        fps = self.fingerprints_for(parsed)
        imported = skipped = 0
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO imports(bank_account_id, filename, file_hash, imported_at) VALUES (?,?,?,?)",
                (bank_account_id, filename, hashlib.sha256(file_bytes).hexdigest(),
                 datetime.now().isoformat(timespec="seconds")),
            )
            import_id = cur.lastrowid
            for tx, fp in zip(parsed, fps):
                if fp in existing:
                    skipped += 1
                    continue
                cur = self.conn.execute(
                    "INSERT INTO transactions(bank_account_id, date, description, amount_cents, memo, "
                    "merchant_key, fingerprint, import_id) VALUES (?,?,?,?,?,?,?,?)",
                    (bank_account_id, tx.date.isoformat(), tx.description.strip(), tx.amount_cents,
                     tx.memo, merchant_key(tx.description), fp, import_id),
                )
                self._post_transaction(cur.lastrowid)
                existing.add(fp)
                imported += 1
            self.conn.execute("UPDATE imports SET row_count=? WHERE id=?", (imported, import_id))
        return imported, skipped

    def set_category(self, tx_id: int, category_id: int | None, *, source: str = "manual",
                     reviewed: bool | None = None) -> None:
        tx = self.transaction(tx_id)
        if category_id is not None and category_id == tx.bank_account_id:
            raise ValueError("A transaction cannot be categorized to its own bank account")
        with self.conn:
            if self._has_table("document_payments"):
                # Recategorizing a payment away from A/R or A/P unlinks it from its invoices/bills.
                self.conn.execute(
                    "DELETE FROM document_payments WHERE transaction_id=? AND (? IS NULL OR document_id IN "
                    "(SELECT id FROM documents WHERE control_id != ?))", (tx_id, category_id, category_id))
            self.conn.execute("DELETE FROM tx_splits WHERE transaction_id=?", (tx_id,))  # one category again
            self.conn.execute(
                "UPDATE transactions SET category_id=?, category_source=? WHERE id=?",
                (category_id, source if category_id is not None else None, tx_id),
            )
            if reviewed is not None:
                self.conn.execute("UPDATE transactions SET reviewed=? WHERE id=?", (int(reviewed), tx_id))
            self._post_transaction(tx_id)

    def splits(self, tx_id: int) -> list[tuple[int, int, str, str]]:
        """[(account id, cents, memo, ref)] when a transaction is split across accounts (else [])."""
        return [tuple(r) for r in self.conn.execute(
            "SELECT account_id, amount_cents, memo, ref FROM tx_splits WHERE transaction_id=? ORDER BY id", (tx_id,))]

    def set_splits(self, tx_id: int, parts: Sequence[tuple[int, int, str, str]], *, source: str = "manual",
                   reviewed: bool = True) -> None:
        """Split a bank transaction across accounts: parts = [(account id, cents, memo, ref)], adding up to the
        transaction's amount. The first part's account becomes its category."""
        tx = self.transaction(tx_id)
        parts = [p for p in parts if p[1]]
        if not parts:
            raise ValueError("Nothing to split")
        if any(p[1] < 0 for p in parts):
            raise ValueError("Split amounts can't be negative")
        if sum(p[1] for p in parts) != abs(tx.amount_cents):
            raise ValueError(f"The parts add up to {sum(p[1] for p in parts) / 100:,.2f}, not "
                             f"{abs(tx.amount_cents) / 100:,.2f}")
        if any(p[0] == tx.bank_account_id for p in parts):
            raise ValueError("A transaction cannot be split to its own bank account")
        with self.conn:
            self.conn.execute("DELETE FROM tx_splits WHERE transaction_id=?", (tx_id,))
            self.conn.executemany("INSERT INTO tx_splits(transaction_id, account_id, amount_cents, memo, ref) "
                                  "VALUES (?,?,?,?,?)", [(tx_id, a, c, m, r) for a, c, m, r in parts])
            self.conn.execute("UPDATE transactions SET category_id=?, category_source=?, reviewed=? WHERE id=?",
                              (parts[0][0], source, int(reviewed), tx_id))
            self._post_transaction(tx_id)

    def set_reviewed(self, tx_ids: Iterable[int], reviewed: bool = True) -> None:
        with self.conn:
            self.conn.executemany("UPDATE transactions SET reviewed=? WHERE id=?",
                                  [(int(reviewed), i) for i in tx_ids])

    def set_memo(self, tx_id: int, memo: str) -> None:
        with self.conn:
            self.conn.execute("UPDATE transactions SET memo=? WHERE id=?", (memo, tx_id))

    def add_manual_transaction(self, bank_account_id: int, tx_date: str, description: str, amount_cents: int, *,
                               category_id: int | None = None, memo: str = "") -> int:
        """A bank transaction entered by hand (cash, a cheque not on a statement yet…). Returns its id."""
        import uuid

        self._check_manual(bank_account_id, tx_date, description, amount_cents, category_id)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO transactions(bank_account_id, date, description, amount_cents, memo, merchant_key, "
                "fingerprint, category_id, category_source, reviewed) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (bank_account_id, tx_date, description.strip(), amount_cents, memo.strip(),
                 merchant_key(description), MANUAL_PREFIX + uuid.uuid4().hex, category_id,
                 "manual" if category_id is not None else None, int(category_id is not None)))
            self._post_transaction(cur.lastrowid)
        return cur.lastrowid

    def update_manual_transaction(self, tx_id: int, bank_account_id: int, tx_date: str, description: str,
                                  amount_cents: int) -> None:
        """Change the date, account, description or amount of a transaction entered by hand."""
        tx = self.transaction(tx_id)
        if not tx.manual:
            raise ValueError("Only transactions entered by hand can be changed; imported ones come from the statement")
        self._check_manual(bank_account_id, tx_date, description, amount_cents, tx.category_id)
        with self.conn:
            self.conn.execute("UPDATE transactions SET bank_account_id=?, date=?, description=?, amount_cents=?, "
                              "merchant_key=? WHERE id=?", (bank_account_id, tx_date, description.strip(),
                                                            amount_cents, merchant_key(description), tx_id))
            self._post_transaction(tx_id)

    def _check_manual(self, bank_account_id: int, tx_date: str, description: str, amount_cents: int,
                      category_id: int | None) -> None:
        if not self.account(bank_account_id).is_bank:
            raise ValueError("Pick a bank account")
        date.fromisoformat(tx_date)
        if not description.strip():
            raise ValueError("Enter a description")
        if amount_cents == 0:
            raise ValueError("Enter an amount")
        if category_id is not None and category_id == bank_account_id:
            raise ValueError("A transaction cannot be categorized to its own bank account")

    def similar_transactions(self, bank_account_id: int, tx_date: str, amount_cents: int, days: int = 5,
                             exclude: int | None = None) -> list[Transaction]:
        """Transactions in the same account for the same amount within a few days (possible duplicates)."""
        d = date.fromisoformat(tx_date)
        rows = self.conn.execute(
            "SELECT * FROM transactions WHERE bank_account_id=? AND amount_cents=? AND date BETWEEN ? AND ? AND id != ?",
            (bank_account_id, amount_cents, (d - timedelta(days=days)).isoformat(),
             (d + timedelta(days=days)).isoformat(), exclude or 0)).fetchall()
        return [self._row_to_tx(r) for r in rows]

    def manual_entries(self) -> list[dict]:
        """Journal entries made by hand (not from bank transactions, invoices / bills or opening balances)."""
        docs = set()
        if self._has_table("documents"):
            docs = {r[0] for r in self.conn.execute("SELECT entry_id FROM documents WHERE entry_id IS NOT NULL")}
        return [e for e in self.journal_entries(kind="manual") if e["id"] not in docs]

    def entry_parties(self) -> dict[int, tuple[str, str]]:
        """{journal entry: (customer, vendor)} named on its lines — the customer from its revenue / asset lines
        (e.g. Rental income · Unit 200), the vendor from its expense / liability lines (Hydro · Toronto Hydro), each
        only when those lines carry one name. A line left blank (the receivable, the payable) takes it."""
        if "party" not in {r[1] for r in self.conn.execute("PRAGMA table_info(journal_lines)")}:
            return {}
        sides: dict[int, tuple[set, set]] = {}
        for entry_id, party, typ in self.conn.execute(
                "SELECT l.entry_id, l.party, a.type FROM journal_lines l JOIN accounts a ON a.id = l.account_id "
                "WHERE l.party != ''"):
            c, v = sides.setdefault(entry_id, (set(), set()))
            (c if typ in ("income", "asset") else v).add(" ".join(party.split()))
        return {e: (next(iter(c)) if len(c) == 1 else "", next(iter(v)) if len(v) == 1 else "")
                for e, (c, v) in sides.items()}

    def replace_journal_entry(self, entry_id: int | None, entry_date: str, memo: str,
                              lines: Sequence[tuple]) -> int:
        """Save a hand-made journal entry (a new one, or replacing ``entry_id``). Each line is
        (account, debit, credit) or (account, debit, credit, customer / vendor)."""
        date.fromisoformat(entry_date)
        lines = [(ln[0], ln[1], ln[2], (ln[3] if len(ln) > 3 else "").strip()) for ln in lines if ln[1] or ln[2]]
        if any(d < 0 or c < 0 or (d and c) for _a, d, c, _p in lines):
            raise ValueError("Each line is a debit or a credit, not both, and not negative")
        if len(lines) < 2 or len({a for a, _d, _c, _p in lines}) < 2:
            raise ValueError("A journal entry needs at least two lines on different accounts")
        if not memo.strip():
            raise ValueError("Enter a description for the entry")
        new = self.add_journal_entry(entry_date, memo.strip(), lines)
        if entry_id is not None:
            self.delete_journal_entry(entry_id)
        return new

    def delete_transactions(self, tx_ids: Iterable[int]) -> None:
        with self.conn:
            self.conn.executemany("DELETE FROM transactions WHERE id=?", [(i,) for i in tx_ids])

    # -- ledger -------------------------------------------------------------

    def _post_transaction(self, tx_id: int) -> None:
        """(Re)create the journal entry for a bank transaction. Caller holds the transaction."""
        tx = self.transaction(tx_id)
        self.conn.execute("DELETE FROM journal_entries WHERE transaction_id=?", (tx_id,))
        if tx.amount_cents == 0:
            return
        category = tx.category_id
        if category is None:
            key = coa.UNCATEGORIZED_INCOME if tx.amount_cents > 0 else coa.UNCATEGORIZED_EXPENSE
            category = self.role(key).id
        cur = self.conn.execute(
            "INSERT INTO journal_entries(date, memo, kind, transaction_id) VALUES (?,?, 'transaction', ?)",
            (tx.date, tx.description, tx_id),
        )
        entry_id = cur.lastrowid
        amt = abs(tx.amount_cents)
        splits = self.splits(tx_id)
        if splits and sum(x[1] for x in splits) == amt:
            lines = [(tx.bank_account_id, amt, 0) if tx.amount_cents > 0 else (tx.bank_account_id, 0, amt)]
            for acct, cents, _memo, _ref in splits:
                lines.append((acct, 0, cents) if tx.amount_cents > 0 else (acct, cents, 0))
            self.conn.executemany(
                "INSERT INTO journal_lines(entry_id, account_id, debit_cents, credit_cents) VALUES (?,?,?,?)",
                [(entry_id, a, d, c) for a, d, c in lines])
            return
        debit_acct, credit_acct = (tx.bank_account_id, category) if tx.amount_cents > 0 else (category, tx.bank_account_id)
        self.conn.executemany(
            "INSERT INTO journal_lines(entry_id, account_id, debit_cents, credit_cents) VALUES (?,?,?,?)",
            [(entry_id, debit_acct, amt, 0), (entry_id, credit_acct, 0, amt)],
        )

    def add_journal_entry(self, entry_date: str, memo: str, lines: Sequence[tuple],
                          *, kind: str = "manual") -> int:
        """Add a manual entry. ``lines`` = [(account_id, debit_cents, credit_cents[, customer / vendor]), ...]"""
        debits = sum(l[1] for l in lines)
        credits = sum(l[2] for l in lines)
        if debits != credits:
            raise ValueError(f"Journal entry does not balance: debits {debits} != credits {credits}")
        if debits == 0:
            raise ValueError("Journal entry has no amounts")
        with self.conn:
            cur = self.conn.execute("INSERT INTO journal_entries(date, memo, kind) VALUES (?,?,?)",
                                    (entry_date, memo, kind))
            self.conn.executemany(
                "INSERT INTO journal_lines(entry_id, account_id, debit_cents, credit_cents, party) VALUES (?,?,?,?,?)",
                [(cur.lastrowid, ln[0], ln[1], ln[2], (ln[3] if len(ln) > 3 else "") or "") for ln in lines],
            )
        return cur.lastrowid

    def journal_entries(self, *, kind: str | None = None) -> list[dict]:
        sql = "SELECT * FROM journal_entries"
        args: list = []
        if kind:
            sql += " WHERE kind=?"
            args.append(kind)
        sql += " ORDER BY date, id"
        entries = []
        for e in self.conn.execute(sql, args).fetchall():
            lines = self.conn.execute(
                "SELECT account_id, debit_cents, credit_cents, party FROM journal_lines WHERE entry_id=? ORDER BY id",
                (e["id"],)).fetchall()
            entries.append({"id": e["id"], "date": e["date"], "memo": e["memo"], "kind": e["kind"],
                            "lines": [tuple(l)[:3] for l in lines], "parties": [l[3] for l in lines]})
        return entries

    def delete_journal_entry(self, entry_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM journal_entries WHERE id=? AND transaction_id IS NULL", (entry_id,))

    def set_opening_balance(self, account_id: int, as_of: str, cents: int) -> None:
        """Set an account's opening balance (in its normal direction) against Opening Balance Equity."""
        acct = self.account(account_id)
        obe = self.role(coa.OPENING_BALANCE_EQUITY).id
        with self.conn:
            for e in self.conn.execute(
                "SELECT e.id FROM journal_entries e JOIN journal_lines l ON l.entry_id=e.id "
                "WHERE e.kind='opening' AND l.account_id=?", (account_id,)
            ).fetchall():
                self.conn.execute("DELETE FROM journal_entries WHERE id=?", (e["id"],))
        if cents == 0:
            return
        debit_side = (cents > 0) == acct.debit_normal
        amt = abs(cents)
        lines = [(account_id, amt, 0), (obe, 0, amt)] if debit_side else [(account_id, 0, amt), (obe, amt, 0)]
        self.add_journal_entry(as_of, f"Opening balance - {acct.name}", lines, kind="opening")

    def opening_balance(self, account_id: int) -> tuple[str | None, int]:
        row = self.conn.execute(
            "SELECT e.date, l.debit_cents - l.credit_cents FROM journal_entries e "
            "JOIN journal_lines l ON l.entry_id=e.id WHERE e.kind='opening' AND l.account_id=?",
            (account_id,),
        ).fetchone()
        if not row:
            return None, 0
        net = row[1]
        return row[0], net if self.account(account_id).debit_normal else -net

    def ledger_totals(self, *, start: str | None = None, end: str | None = None,
                      kinds: Iterable[str] | None = None) -> dict[int, int]:
        """Net debit-minus-credit per account for the period."""
        sql = ("SELECT l.account_id, SUM(l.debit_cents - l.credit_cents) FROM journal_lines l "
               "JOIN journal_entries e ON e.id=l.entry_id WHERE 1=1")
        args: list = []
        if start:
            sql += " AND e.date >= ?"
            args.append(start)
        if end:
            sql += " AND e.date <= ?"
            args.append(end)
        if kinds is not None:
            ks = list(kinds)
            sql += f" AND e.kind IN ({','.join('?' * len(ks))})"
            args += ks
        sql += " GROUP BY l.account_id"
        return {r[0]: r[1] for r in self.conn.execute(sql, args)}

    def date_range(self) -> tuple[str | None, str | None]:
        row = self.conn.execute("SELECT MIN(date), MAX(date) FROM journal_entries").fetchone()
        return row[0], row[1]

    # -- rules --------------------------------------------------------------

    def rules(self) -> list[Rule]:
        rows = self.conn.execute("SELECT * FROM rules ORDER BY priority, id").fetchall()
        return [Rule(r["id"], r["pattern"], r["match_type"], r["account_id"], r["direction"], r["priority"])
                for r in rows]

    def add_rule(self, pattern: str, account_id: int, *, match_type: str = "contains",
                 direction: str = "any", priority: int = 100) -> int:
        if match_type == "regex":
            re.compile(pattern)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO rules(pattern, match_type, account_id, direction, priority) VALUES (?,?,?,?,?)",
                (pattern, match_type, account_id, direction, priority),
            )
        return cur.lastrowid

    def update_rule(self, rule_id: int, pattern: str, account_id: int, *, match_type: str,
                    direction: str, priority: int) -> None:
        if match_type == "regex":
            re.compile(pattern)
        with self.conn:
            self.conn.execute(
                "UPDATE rules SET pattern=?, match_type=?, account_id=?, direction=?, priority=? WHERE id=?",
                (pattern, match_type, account_id, direction, priority, rule_id),
            )

    def delete_rule(self, rule_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))

    # -- integrity ----------------------------------------------------------

    def check_balanced(self) -> bool:
        row = self.conn.execute("SELECT COALESCE(SUM(debit_cents),0), COALESCE(SUM(credit_cents),0) FROM journal_lines").fetchone()
        return row[0] == row[1]
