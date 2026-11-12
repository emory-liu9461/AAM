"""Your personal bank and credit card statements: drop them in a folder you pick and they're read, sorted into your
budget's categories, and counted as the Budget's actuals.

* **The folder** — chosen on Personal Finance → Statements (the personal folder's *Bank and Investment Statements* to
  start with), subfolders included (not Archive). Statements are read the same way as the companies' (CSV, Excel,
  OFX/QFX/QBO, PDF — scanned ones too). Each file is read once; a transaction already read from another statement (an
  overlapping period) isn't counted twice.
* **Sorting** — by what you sorted the same merchant as before, else by common words (groceries, gas, hydro …).
  Transfers between your own accounts and credit card payments are *Transfer (not counted)*, and so is money from
  your own companies (it's already in Business income). Anything not sorted is listed for you to sort; once you sort
  a merchant, the others from it follow.
* **Into the budget** — money out in an expense category and money in in an income category, by month. A month's
  actual on the Budget is the statements plus what you type there.

Kept in the portfolio file, on this computer.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import longpath as lpth
from . import personal as pers
from . import personal_finance as pfi

TRANSFER = "Transfer (not counted)"
EXTS = {".csv", ".txt", ".tsv", ".xlsx", ".xlsm", ".ofx", ".qfx", ".qbo", ".pdf"}
DEFAULT_SUB = "Bank and Investment Statements"

SCHEMA = """
CREATE TABLE IF NOT EXISTS personal_tx (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    description TEXT NOT NULL,
    amount      INTEGER NOT NULL,          -- cents: + money in, − money out
    category    TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT '',  -- the statement file
    dupkey      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_ptx_date ON personal_tx(date);
CREATE INDEX IF NOT EXISTS ix_ptx_key ON personal_tx(dupkey);
CREATE TABLE IF NOT EXISTS personal_files (
    hash TEXT PRIMARY KEY, name TEXT NOT NULL, imported TEXT NOT NULL, count INTEGER NOT NULL DEFAULT 0
);
"""

# common words → category (checked in order; money out unless noted)
RULES: list[tuple[str, tuple[str, ...]]] = [
    (TRANSFER, ("transfer to", "transfer from", "account transfer", "internal transfer", "tfr", "payment - thank you", "payment thank you", "paiement merci", "online payment",
                "visa payment", "mastercard payment", "amex payment", "credit card payment", "e-transfer to self")),
    ("Groceries", ("loblaw", "no frills", "metro", "sobeys", "food basics", "freshco", "farm boy", "costco",
                   "walmart", "real canadian superstore", "superstore", "longo", "whole foods", "t&t", "fortinos",
                   "zehrs", "foodland", "iga", "grocery")),
    ("Dining out", ("restaurant", "starbucks", "tim hortons", "mcdonald", "uber eats", "ubereats", "doordash",
                    "skip the dishes", "skipthedishes", "pizza", "cafe", "coffee", "a&w", "subway", "chipotle",
                    "harvey", "wendy")),
    ("Transportation", ("esso", "petro-canada", "petro canada", "shell", "pioneer", "ultramar", "husky", "canadian tire "
                        "gas", "gas bar", "presto", "go transit", "ttc", "uber", "lyft", "parking", "407 etr",
                        "impark", "green p")),
    ("Utilities", ("hydro", "enbridge", "toronto hydro", "alectra", "elexicon", "water", "union gas", "reliance",
                   "utilit")),
    ("Phone and internet", ("rogers", "bell canada", "bell mobility", "telus", "fido", "koodo", "virgin", "freedom "
                            "mobile", "teksavvy", "shaw")),
    ("Home insurance", ("home insurance", "tenant insurance")),
    ("Car insurance", ("auto insurance", "car insurance")),
    ("Housing (mortgage / rent)", ("mortgage", "rent payment")),
    ("Property tax", ("property tax", "tax payment city", "city of toronto tax", "taxes city")),
    ("Health and fitness", ("pharmacy", "shoppers drug", "rexall", "dental", "dentist", "physio", "goodlife", "gym",
                            "fitness", "optometr")),
    ("Entertainment", ("netflix", "spotify", "disney", "crave", "apple.com/bill", "cineplex", "ticketmaster",
                       "steam", "playstation", "xbox", "prime video")),
    ("Travel", ("air canada", "westjet", "porter", "expedia", "airbnb", "booking.com", "hotel", "marriott", "hilton")),
    ("Clothing", ("winners", "h&m", "zara", "uniqlo", "gap ", "old navy", "lululemon", "sport chek", "simons")),
    ("Childcare and education", ("daycare", "tuition", "school", "university", "college")),
    ("Gifts and donations", ("donation", "charity", "canadahelps")),
    ("Income tax", ("cra", "canada revenue", "receiver general")),
]
INCOME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Salary / employment", ("payroll", "salary", "pay deposit", "direct deposit", "dir dep", "paie")),
    ("Investment income", ("interest", "dividend")),
]


def ready(pf) -> None:
    pf.conn.executescript(SCHEMA)


# ---- the folder ----

def folder(pf) -> Path | None:
    v = pers._setting(pf, "personal_statements_folder")
    if v:
        return Path(v)
    f = pers.folder(pf)
    return f / DEFAULT_SUB if f else None


def set_folder(pf, path: str | Path | None) -> None:
    pers._set(pf, "personal_statements_folder", str(path) if path else "")


# ---- sorting ----

def categories() -> list[str]:
    """What a transaction can be sorted into (Business income comes from the businesses, not statements)."""
    return [c for c in pfi.INCOME_CATS if c != pfi.BUSINESS] + pfi.EXPENSE_CATS + [TRANSFER]


def _learned(pf) -> dict[str, str]:
    raw = pers._setting(pf, "personal_rules")
    return json.loads(raw) if raw else {}


def _key(description: str) -> str:
    from .books import merchant_key

    return merchant_key(description) or description.strip().upper()


def _company_words(pf) -> list[str]:
    from . import intercompany as ic
    from . import shareholder_loans as shl

    try:
        return [w for w in (ic.norm(n) for n, _p in shl.companies(pf)) if len(w) > 3]
    except Exception:  # noqa: BLE001
        return []


def guess(pf, description: str, amount: int, *, learned=None, companies=None) -> str:
    learned = _learned(pf) if learned is None else learned
    k = _key(description)
    if k in learned:
        return learned[k]
    low = " " + description.lower() + " "
    from . import intercompany as ic

    norm = " " + ic.norm(description) + " "
    for w in (companies if companies is not None else _company_words(pf)):
        if f" {w} " in norm:
            return TRANSFER                  # from / to your own company: already counted there
    interac = any(w in low for w in ("e-transfer", "etransfer", "e transfer", "interac", "virement"))
    if amount > 0:
        for cat, words in INCOME_RULES:
            if any(w in low for w in words):
                return cat
        if not interac and any(w in low for w in RULES[0][1]):
            return TRANSFER
        return ""
    for cat, words in RULES:
        if cat == TRANSFER and interac:      # an e-transfer to someone is usually spending: yours to sort
            continue
        if any(w in low for w in words):
            return cat
    return ""


# ---- reading the folder ----

@dataclass
class ImportResult:
    files: int = 0
    added: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)


def _hash(p: Path) -> str:
    return hashlib.sha1(Path(lpth.lp(p)).read_bytes()).hexdigest()


def statement_files(base: Path) -> list[Path]:
    if not lpth.is_dir(base):
        return []
    out = []
    for p in lpth.walk_files(base):
        rel = p.relative_to(base)
        if any(x.lower() in ("archive", "archived") for x in rel.parts[:-1]) or p.name.startswith(("~$", ".")):
            continue
        if p.suffix.lower() in EXTS:
            out.append(p)
    return sorted(out)


def scan(pf, base: Path | None = None) -> ImportResult:
    """Read every statement in the folder not read yet."""
    from .importers import parse_statement_bytes

    ready(pf)
    base = Path(base) if base else folder(pf)
    res = ImportResult()
    if base is None:
        return res
    done = {r[0] for r in pf.conn.execute("SELECT hash FROM personal_files")}
    learned, comps = _learned(pf), _company_words(pf)
    for p in statement_files(base):
        try:
            h = _hash(p)
        except OSError as e:
            res.errors.append(f"{p.name}: {e}")
            continue
        if h in done:
            continue
        try:
            txs, _warn = parse_statement_bytes(Path(lpth.lp(p)).read_bytes(), p.name)
        except Exception as e:  # noqa: BLE001 — not a statement we can read: say so, keep going
            res.errors.append(f"{p.name}: {e}")
            continue
        rel = str(p.relative_to(base))
        n = 0
        with pf.conn:
            for t in txs:
                dup = f"{t.date.isoformat()}|{t.amount_cents}|{_key(t.description)}"
                if pf.conn.execute("SELECT 1 FROM personal_tx WHERE dupkey=? AND source != ?",
                                   (dup, rel)).fetchone():
                    res.duplicates += 1
                    continue
                pf.conn.execute("INSERT INTO personal_tx(date, description, amount, category, source, dupkey) "
                                "VALUES (?,?,?,?,?,?)", (t.date.isoformat(), t.description, t.amount_cents,
                                                         guess(pf, t.description, t.amount_cents, learned=learned,
                                                               companies=comps), rel, dup))
                n += 1
            pf.conn.execute("INSERT OR REPLACE INTO personal_files(hash, name, imported, count) VALUES (?,?,?,?)",
                            (h, rel, date.today().isoformat(), n))
        res.files += 1
        res.added += n
    return res


# ---- the transactions ----

@dataclass
class Tx:
    id: int
    date: str
    description: str
    amount: int
    category: str
    source: str


def transactions(pf, year: int | None = None, *, unsorted_only: bool = False) -> list[Tx]:
    ready(pf)
    sql, args = "SELECT id, date, description, amount, category, source FROM personal_tx WHERE 1=1", []
    if year:
        sql += " AND date LIKE ?"
        args.append(f"{year}-%")
    if unsorted_only:
        sql += " AND category = ''"
    sql += " ORDER BY date DESC, id DESC"
    return [Tx(*r) for r in pf.conn.execute(sql, args)]


def set_category(pf, tx_id: int, category: str, *, learn: bool = True) -> int:
    """Sort one transaction; with ``learn``, every unsorted one from the same merchant follows, and new ones will.
    Returns how many changed."""
    ready(pf)
    r = pf.conn.execute("SELECT description FROM personal_tx WHERE id=?", (tx_id,)).fetchone()
    if r is None:
        return 0
    n = 0
    with pf.conn:
        pf.conn.execute("UPDATE personal_tx SET category=? WHERE id=?", (category, tx_id))
        n += 1
        if learn:
            k = _key(r[0])
            rules = _learned(pf)
            if category:
                rules[k] = category
            else:
                rules.pop(k, None)
            pers._set(pf, "personal_rules", json.dumps(rules))
            if category:
                for i, d in pf.conn.execute("SELECT id, description FROM personal_tx WHERE category='' AND id != ?",
                                            (tx_id,)).fetchall():
                    if _key(d) == k:
                        pf.conn.execute("UPDATE personal_tx SET category=? WHERE id=?", (category, i))
                        n += 1
    return n


def monthly(pf, year: int) -> dict[str, dict[int, int]]:
    """{category: {month: cents}} from the statements: money out for an expense category, money in for income
    (transfers and unsorted ones left out)."""
    ready(pf)
    income = set(pfi.INCOME_CATS)
    out: dict[str, dict[int, int]] = {}
    for d, amt, cat in pf.conn.execute("SELECT date, amount, category FROM personal_tx WHERE date LIKE ? AND "
                                       "category NOT IN ('', ?)", (f"{year}-%", TRANSFER)):
        v = amt if cat in income else -amt
        m = int(d[5:7])
        out.setdefault(cat, {})[m] = out.setdefault(cat, {}).get(m, 0) + v
    return out


def files(pf) -> list[tuple[str, str, int]]:
    ready(pf)
    return [tuple(r) for r in pf.conn.execute("SELECT name, imported, count FROM personal_files ORDER BY imported "
                                              "DESC, name")]
