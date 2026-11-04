"""Who a bank transaction was with — "names" for e-Transfers, cheques, drafts, transfers and wires.

A name comes from (first that applies):

1. **you** — set by hand on a transaction ("Who was this?"),
2. a matched **e-Transfer** history record (sent to / received from),
3. a matched **cheque or bank draft** scan (pay to the order of),
4. a **name rule** — "description contains TFR-FR 7340218 → Alex Morgan" — so future transactions get
   the name as soon as they're imported.

Names let the To Categorize panel and the Transactions list filter and group by person / company, apply
one category to everything from that name, and remember the category per name: once e-Transfers from
Alex are categorized as *Shareholder Loan*, the next one from Alex is too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .books import Books, Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS tx_names (
    transaction_id INTEGER PRIMARY KEY REFERENCES transactions(id) ON DELETE CASCADE,
    name           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS name_rules (
    id         INTEGER PRIMARY KEY,
    pattern    TEXT NOT NULL,
    name       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(pattern)
);
"""


def ensure_schema(books: Books) -> None:
    if books.first_time("schema:parties"):
        books.conn.executescript(SCHEMA)


@dataclass
class Name:
    name: str
    source: str    # manual | etransfer | cheque | rule


def _clean(name: str) -> str:
    return " ".join(name.split()).strip(" -·")


def names(books: Books, tx_ids: list[int] | None = None) -> dict[int, Name]:
    """{transaction id: Name} for every transaction that has one (or just ``tx_ids``). Kept until the file
    changes, so the many screens asking for it after one change don't each work it out again."""
    ensure_schema(books)
    key = books.change_key()
    cached = books.__dict__.get("_names_cache")
    if cached is None or cached[0] != key:
        cached = books.__dict__["_names_cache"] = (key, _names(books))
    if tx_ids is None:
        return dict(cached[1])
    return {i: cached[1][i] for i in tx_ids if i in cached[1]}


def _names(books: Books, tx_ids: list[int] | None = None) -> dict[int, Name]:
    from . import contacts

    amap = contacts.alias_map(books)  # other names → the contact's name
    out: dict[int, Name] = {}
    want = set(tx_ids) if tx_ids is not None else None

    def put(tx_id: int, name: str, source: str) -> None:
        if tx_id is None or (want is not None and tx_id not in want) or tx_id in out:
            return
        name = _clean(name or "")
        if name:
            out[tx_id] = Name(amap.get(name.lower(), name), source)

    for tx_id, name in books.conn.execute("SELECT transaction_id, name FROM tx_names"):
        put(tx_id, name, "manual")
    if books._has_table("etransfers"):
        from .etransfer import _clean_name

        for tx_id, name in books.conn.execute("SELECT transaction_id, name FROM etransfers "
                                              "WHERE transaction_id IS NOT NULL"):
            put(tx_id, _clean_name(name) or name, "etransfer")
    if books._has_table("cheques"):
        for tx_id, payee in books.conn.execute("SELECT transaction_id, payee FROM cheques "
                                               "WHERE transaction_id IS NOT NULL"):
            put(tx_id, payee, "cheque")
    rules = list(books.conn.execute("SELECT pattern, name FROM name_rules ORDER BY LENGTH(pattern) DESC"))
    if rules:
        sql = "SELECT id, description FROM transactions"
        for tx_id, desc in books.conn.execute(sql):
            if tx_id in out or (want is not None and tx_id not in want):
                continue
            low = desc.lower()
            for pattern, name in rules:
                if pattern.lower() in low:
                    put(tx_id, name, "rule")
                    break
    return out


def name_of(books: Books, tx_id: int) -> Name | None:
    return names(books, [tx_id]).get(tx_id)


def all_names(books: Books) -> list[str]:
    """Every name in use (for pick lists): your customers & vendors, then other names on transactions, A–Z."""
    from . import contacts

    seen: dict[str, str] = {c.name.lower(): c.name for c in contacts.all_contacts(books, active_only=True)}
    for n in names(books).values():
        seen.setdefault(n.name.lower(), n.name)
    ensure_schema(books)
    for (n,) in books.conn.execute("SELECT name FROM name_rules"):
        seen.setdefault(n.lower(), n)
    return sorted(seen.values(), key=str.lower)


def set_name(books: Books, tx_ids: list[int], name: str) -> None:
    """Name transactions by hand (blank: back to the automatic name)."""
    ensure_schema(books)
    with books.conn:
        for tx_id in tx_ids:
            if name.strip():
                books.conn.execute("INSERT INTO tx_names(transaction_id, name) VALUES (?,?) ON CONFLICT(transaction_id) "
                                   "DO UPDATE SET name=excluded.name", (tx_id, _clean(name)))
            else:
                books.conn.execute("DELETE FROM tx_names WHERE transaction_id=?", (tx_id,))


def add_rule(books: Books, pattern: str, name: str) -> None:
    """Future (and past) transactions whose description contains ``pattern`` get ``name``."""
    pattern, name = pattern.strip(), _clean(name)
    if len(pattern) < 3:
        raise ValueError("Use at least 3 characters of the description")
    if not name:
        raise ValueError("Enter a name")
    ensure_schema(books)
    with books.conn:
        books.conn.execute("INSERT INTO name_rules(pattern, name, created_at) VALUES (?,?,?) ON CONFLICT(pattern) "
                           "DO UPDATE SET name=excluded.name", (pattern, name,
                                                                datetime.now().isoformat(timespec="seconds")))


def rules(books: Books) -> list[tuple[int, str, str]]:
    ensure_schema(books)
    return [tuple(r) for r in books.conn.execute("SELECT id, pattern, name FROM name_rules ORDER BY name, pattern")]


def delete_rule(books: Books, rule_id: int) -> None:
    ensure_schema(books)
    with books.conn:
        books.conn.execute("DELETE FROM name_rules WHERE id=?", (rule_id,))


def suggested_pattern(description: str) -> str:
    """The stable part of a description for a name rule: 'AB123 TFR-FR 7340218' -> 'TFR-FR 7340218'."""
    import re

    d = description.strip()
    m = re.search(r"(TFR-(?:FR|TO)\s*\d+|WIRE\s+(?:IN|OUT|FROM|TO)\s+\S+(?:\s+\S+)?|FROM\s+\S+(?:\s+\S+)?)", d, re.I)
    if m:
        return m.group(1)
    if re.search(r"\*{2,}|CHQ#|DRAFT\s+\d", d):
        return ""  # e-Transfer / cheque / draft numbers differ every time: no useful pattern
    return re.sub(r"^\S*\d\S*\s+", "", d)[:40]


def _category_counts(books: Books) -> tuple[dict, dict]:
    """How often you chose each category per (name, money in?) — from transactions you categorized or reviewed —
    and what each of those transactions contributed. Worked out once per change to the file."""
    from . import chart_of_accounts as coa

    key = books.change_key()
    cached = books.__dict__.get("_name_cat_cache")
    if cached and cached[0] == key:
        return cached[1], cached[2]
    skip = set()
    for k in (coa.UNCATEGORIZED_EXPENSE, coa.UNCATEGORIZED_INCOME):
        try:
            skip.add(books.role(k).id)
        except Exception:  # noqa: BLE001
            pass
    named = names(books)
    counts: dict[tuple[str, bool], dict[int, int]] = {}
    contrib: dict[int, tuple[tuple[str, bool], int]] = {}
    if named:
        known = set(books._accounts())
        for r in books.conn.execute("SELECT id, amount_cents, category_id, category_source, reviewed FROM transactions "
                                    "WHERE category_id IS NOT NULL AND (category_source='manual' OR reviewed=1)"):
            n = named.get(r[0])
            if n is None or r[2] in skip or r[2] not in known:
                continue
            k = (n.name.lower(), r[1] > 0)
            c = counts.setdefault(k, {})
            c[r[2]] = c.get(r[2], 0) + 1
            contrib[r[0]] = (k, r[2])
    books.__dict__["_name_cat_cache"] = (key, counts, contrib)
    return counts, contrib


def category_for_name(books: Books, name: str, money_in: bool, exclude_tx: int | None = None) -> int | None:
    """The category you chose most often for transactions with this name (same direction)."""
    counts, contrib = _category_counts(books)
    c = dict(counts.get((name.lower(), money_in), {}))
    if exclude_tx in contrib and contrib[exclude_tx][0] == (name.lower(), money_in):
        acct = contrib[exclude_tx][1]
        c[acct] -= 1
        if c[acct] <= 0:
            del c[acct]
    return max(c, key=c.get) if c else None


def apply_learned(books: Books, txs: list[Transaction] | None = None) -> int:
    """Give uncategorized transactions that have a name the category you used for that name. Returns how many."""
    named = names(books, [t.id for t in txs] if txs is not None else None)
    if not named:
        return 0
    counts, _contrib = _category_counts(books)
    todo = []
    for r in books.conn.execute("SELECT id, amount_cents, category_id, category_source, reviewed, bank_account_id "
                                "FROM transactions WHERE reviewed=0 AND (category_id IS NULL OR category_source IS NULL "
                                "OR category_source='keyword')"):
        n = named.get(r[0])
        if n is None:
            continue
        c = counts.get((n.name.lower(), r[1] > 0))
        acct = max(c, key=c.get) if c else None
        if acct and acct != r[5] and acct != r[2]:
            todo.append((r[0], acct))
    if todo:
        with books.batch():
            for tx_id, acct in todo:
                books.set_category(tx_id, acct, source="learned")
    return len(todo)
