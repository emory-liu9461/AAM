"""Shareholder loans between you and your companies: the same money on both sides — your net worth and the
company's books.

**Linking.** Each company you own (the portfolio's holdings and the holding companies that own them) can have one
account in its books for money between it and you — "Shareholder Loan – Pat Lee", "Due to shareholder", "Due from
owner" … — found by its name (or your name), or created for you ("Shareholder Loan – <your name>", a long-term
liability). The link is kept in the portfolio file.

**Into your net worth.** The linked account's balance is read from the company's books: a credit balance (the company
owes you) is an asset of yours — "Shareholder loans to your companies"; a debit balance (you owe the company) is a
liability — "Owed to your companies". Nothing to type: it moves when the books do.

**Cross entries.** Money between you and a company recorded from Personal Finance is posted in the company's books
against the linked account:

* you put money in (a loan to the company): bank ← you — debit the company's bank, credit the loan account;
* the company pays you (repaying you, or lending to you): debit the loan account, credit the company's bank;
* you paid a company expense yourself: debit the expense, credit the loan account (the company owes you for it);
* the company paid a personal expense of yours: debit the loan account, credit the company's bank.

If the company's bank statement will also bring the deposit or payment in, categorize that line to the loan account
instead of recording it here (or it's counted twice).
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import personal as pers

WORDS = ("shareholder", "due to owner", "due from owner", "owner loan", "owner's loan", "loan from owner",
         "due to director", "due from director", "advances from owner", "advances to owner")

# directions: (label, which way the money goes for you)
LEND, PAID_YOU, PAID_EXPENSE, PAID_FOR_YOU = "lend", "paid_you", "you_paid_expense", "paid_for_you"
DIRECTIONS = {
    LEND: "You put money into the company (a loan to it)",
    PAID_YOU: "The company paid you (repaying you, or a loan to you)",
    PAID_EXPENSE: "You paid a company expense yourself",
    PAID_FOR_YOU: "The company paid a personal expense of yours",
}


# ---- your companies ----

def companies(pf) -> list[tuple[str, str]]:
    """(name, company file) of the companies you own through the portfolio: the holding companies and the holdings
    with books."""
    from .companies import _read_name

    seen: dict[str, str] = {}
    paths = [pf.holdco_path] if getattr(pf, "holdco_path", "") else []
    for h in pf.holdings():
        if h.status != "active":
            continue
        paths += [h.owner_path, h.company_path]
    for p in paths:
        if p and Path(p).exists() and str(Path(p).resolve()) not in {str(Path(x).resolve()) for x in seen}:
            seen[p] = _read_name(p) or Path(p).stem
    return [(n, p) for p, n in seen.items()]


# ---- the linked accounts ----

def links(pf) -> dict[str, int]:
    raw = pers._setting(pf, "shareholder_links")
    return {k: int(v) for k, v in json.loads(raw).items()} if raw else {}


def set_link(pf, path: str, account_id: int | None) -> None:
    d = links(pf)
    if account_id is None:
        d.pop(str(path), None)
    else:
        d[str(path)] = int(account_id)
    pers._set(pf, "shareholder_links", json.dumps(d))


@dataclass
class Account:
    id: int
    code: str
    name: str
    type: str
    balance: int              # in the account's normal direction


def _ro(path):
    return sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)


def _matches(name: str, person: str) -> bool:
    low = name.lower()
    if any(w in low for w in WORDS):
        return True
    parts = [w for w in re.split(r"\W+", person.lower()) if len(w) > 2]
    return bool(parts) and all(w in low for w in parts) and ("loan" in low or "due" in low or "advance" in low)


def candidates(path: str, person: str = "", as_of: date | None = None) -> list[Account]:
    """The company's accounts that look like a shareholder loan with you (by their names), best first."""
    as_of = (as_of or date.today()).isoformat()
    conn = _ro(path)
    try:
        rows = conn.execute("SELECT id, code, name, type FROM accounts WHERE type IN ('asset','liability') AND "
                            "is_bank=0 AND active=1 ORDER BY code").fetchall()
        out = []
        for i, code, name, typ in rows:
            if not _matches(name, person):
                continue
            net = conn.execute("SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l JOIN "
                               "journal_entries e ON e.id = l.entry_id WHERE l.account_id=? AND e.date <= ?",
                               (i, as_of)).fetchone()[0]
            out.append(Account(i, code, name, typ, net if typ == "asset" else -net))
    finally:
        conn.close()
    name_words = [w for w in re.split(r"\W+", person.lower()) if len(w) > 2]
    out.sort(key=lambda a: (not (name_words and all(w in a.name.lower() for w in name_words)), a.type != "liability",
                            a.code))
    return out


def all_accounts(path: str) -> list[tuple[int, str, str]]:
    """Every asset / liability account (not banks) a loan could be kept in: (id, "code name", type)."""
    conn = _ro(path)
    try:
        return [(i, f"{c} {n}", t) for i, c, n, t in conn.execute(
            "SELECT id, code, name, type FROM accounts WHERE type IN ('asset','liability') AND is_bank=0 AND active=1 "
            "ORDER BY type DESC, code")]
    finally:
        conn.close()


def auto_link(pf, person: str = "") -> int:
    """Link each company that has no link yet to its one clear shareholder-loan account. Returns how many."""
    person = person or pers.load(pf).name
    have = links(pf)
    n = 0
    for _name, path in companies(pf):
        if str(path) in have:
            continue
        try:
            c = candidates(path, person)
        except sqlite3.Error:
            continue
        mine = [a for a in c if person and all(w in a.name.lower() for w in re.split(r"\W+", person.lower())
                                                if len(w) > 2)]
        pick = mine[0] if len(mine) == 1 else (c[0] if len(c) == 1 else None)
        if pick is not None:
            set_link(pf, path, pick.id)
            n += 1
    return n


def create_account(path: str, person: str) -> int:
    """A "Shareholder Loan – <you>" long-term liability in the company's books. Returns its id."""
    from .books import Books

    b = Books.open(path, create=False)
    try:
        used = {a.code for a in b.accounts()}
        code = "2800"
        while code in used:
            code = str(int(code) + 1)
        base = f"Shareholder Loan – {person.strip()}" if person.strip() else "Shareholder Loan"
        name, k = base, 2
        while any(a.name.lower() == name.lower() for a in b.accounts()):
            name, k = f"{base} ({k})", k + 1
        return b.add_account(code, name, "liability", "long_term").id
    finally:
        b.close()


# ---- the balances, into your net worth ----

@dataclass
class Position:
    company: str
    path: str
    account_id: int | None
    account: str = ""
    owed_to_you: int = 0        # + the company owes you; − you owe the company
    error: str = ""


def positions(pf, as_of: date | None = None) -> list[Position]:
    """Each of your companies with its linked account's balance (from its books)."""
    as_of = as_of or date.today()
    ln = links(pf)
    out = []
    for name, path in companies(pf):
        acct = ln.get(str(path))
        pos = Position(name, path, acct)
        if acct is not None:
            try:
                conn = _ro(path)
                try:
                    r = conn.execute("SELECT name FROM accounts WHERE id=?", (acct,)).fetchone()
                    net = conn.execute("SELECT COALESCE(SUM(l.debit_cents - l.credit_cents), 0) FROM journal_lines l "
                                       "JOIN journal_entries e ON e.id = l.entry_id WHERE l.account_id=? AND "
                                       "e.date <= ?", (acct, as_of.isoformat())).fetchone()[0]
                finally:
                    conn.close()
                if r is None:
                    pos.error, pos.account_id = "the linked account is gone", None
                else:
                    pos.account, pos.owed_to_you = r[0], -net        # a credit balance: the company owes you
            except sqlite3.Error as e:
                pos.error = str(e)
        out.append(pos)
    return out


# ---- cross entries: posted in the company's books ----

@dataclass
class Line:
    date: str
    memo: str
    amount: int                 # + toward you being owed more; − toward the company being owed more
    entry_id: int


def history(path: str, account_id: int) -> list[Line]:
    """Every entry on the linked account, newest first: + the company owes you more, − less."""
    conn = _ro(path)
    try:
        rows = conn.execute("SELECT e.date, e.memo, l.credit_cents - l.debit_cents, e.id FROM journal_lines l JOIN "
                            "journal_entries e ON e.id = l.entry_id WHERE l.account_id=? ORDER BY e.date DESC, e.id "
                            "DESC", (account_id,)).fetchall()
    finally:
        conn.close()
    return [Line(d, m or "", a, i) for d, m, a, i in rows]


def bank_accounts(path: str) -> list[tuple[int, str]]:
    conn = _ro(path)
    try:
        return [(i, n) for i, n in conn.execute("SELECT id, name FROM accounts WHERE is_bank=1 AND active=1 ORDER BY "
                                                "code")]
    finally:
        conn.close()


def expense_accounts(path: str) -> list[tuple[int, str]]:
    conn = _ro(path)
    try:
        return [(i, f"{c} {n}") for i, c, n in conn.execute(
            "SELECT id, code, name FROM accounts WHERE type='expense' AND active=1 ORDER BY code")]
    finally:
        conn.close()


def record(path: str, loan_account: int, direction: str, amount: int, on: date, *, other_account: int,
           memo: str = "") -> int:
    """Post it in the company's books (``other_account``: its bank, or the expense you paid). Returns the entry."""
    from .books import Books

    if amount <= 0:
        raise ValueError("Enter the amount")
    if direction not in DIRECTIONS:
        raise ValueError(f"Unknown kind {direction!r}")
    b = Books.open(path, create=False)
    try:
        if direction in (LEND, PAID_EXPENSE):           # the company owes you more
            lines = [(other_account, amount, 0), (loan_account, 0, amount)]
        else:                                           # the company owes you less
            lines = [(loan_account, amount, 0), (other_account, 0, amount)]
        label = {LEND: "Shareholder loan from you", PAID_YOU: "Paid to shareholder",
                 PAID_EXPENSE: "Company expense paid by shareholder", PAID_FOR_YOU: "Personal expense of shareholder "
                                                                                    "paid by the company"}[direction]
        return b.add_journal_entry(on.isoformat(), f"{label}{' – ' + memo if memo else ''}", lines,
                                   kind="manual")
    finally:
        b.close()


def adjust_cash(pf, direction: str, amount: int) -> None:
    """Your cash on the Net Worth page moves the other way: less when you lend or pay, more when the company pays
    you (the first "Cash and bank accounts" line, made if there isn't one)."""
    s = pers.load(pf)
    change = -amount if direction in (LEND, PAID_EXPENSE) else (amount if direction == PAID_YOU else 0)
    if not change:
        return
    cash = next((e for e in s.assets if e.kind in pers.CASH_KINDS), None)
    if cash is None:
        cash = pers.Entry("Cash and bank accounts")
        s.assets.insert(0, cash)
    cash.amount += change
    pers.save(pf, s)
