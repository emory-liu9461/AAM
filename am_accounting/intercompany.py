"""Intercompany balances: what the group's companies owe each other, checked from both sides.

An account in one company's books is **intercompany** when its name names another company in the group — "Due from
1001234567 Ontario Inc.", "Due to Maple Holdings Inc.", "Shareholder Loan – Maple Holdings", "Loan to Elm Street
Apartments Ltd." (an asset or a liability; an *investment* in a company is ownership, not a balance owed, and is left
out). Names are compared without "Inc.", "Ltd.", punctuation and case, by the company's name or its legal name.

For each pair of companies the two sides should agree: what A's books say B owes A should be what B's books say B owes
A. A pair that doesn't agree (a payment recorded in one company only, a different amount, a balance in one set of
books only) is flagged with the difference.

The same balances are taken out of the portfolio's combined figures (see :func:`eliminations`), so money moving
between the group's companies isn't counted as an asset and a liability of the group.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SUFFIXES = {"inc", "incorporated", "ltd", "limited", "corp", "corporation", "co", "company", "lp", "llc", "llp",
            "the", "ulc", "partnership"}
SKIP_WORDS = ("investment in", "investments in", "share capital", "shares of", "dividend")


def norm(name: str) -> str:
    words = re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower()).split()
    return " ".join(w for w in words if w not in SUFFIXES)


@dataclass
class ICAccount:
    company: str            # the company whose books it's in
    path: str
    account_id: int
    account: str            # the account's name
    other: str              # the company it's with
    other_path: str
    type: str               # asset | liability
    balance: int            # what the other company owes this one (+) or this one owes it (−)
    debt: bool = False      # counted as debt in the company's figures (a loan / long-term liability)


@dataclass
class ICPair:
    a: str
    a_path: str
    b: str
    b_path: str
    a_says: int             # A's books: what B owes A (− = A owes B)
    b_says: int             # B's books: what B owes A, from B's side (− of B's net position)
    a_accounts: list = field(default_factory=list)
    b_accounts: list = field(default_factory=list)

    @property
    def difference(self) -> int:
        return self.a_says - self.b_says

    @property
    def status(self) -> str:
        if not self.a_accounts:
            return f"Only in {self.b}'s books"
        if not self.b_accounts:
            return f"Only in {self.a}'s books"
        return "Matches" if self.difference == 0 else "Doesn't match"

    @property
    def ok(self) -> bool:
        return self.difference == 0


@dataclass
class ICResult:
    as_of: str
    pairs: list[ICPair]
    accounts: list[ICAccount]
    errors: list[str] = field(default_factory=list)

    @property
    def problems(self) -> list[ICPair]:
        return [p for p in self.pairs if not p.ok]


def _is_debt(a) -> bool:
    name = a.name.lower()
    return a.subtype == "long_term" or any(w in name for w in ("loan", "mortgage", "debt", "line of credit",
                                                             "note payable", "credit facility"))


def _names_of(path: str, name: str) -> list[str]:
    """The company's name and legal name, normalized."""
    from .books import Books

    out = {norm(name)}
    try:
        b = Books.open(path, create=False)
        try:
            p = b.profile()
            out |= {norm(p.name), norm(p.legal_name)}
        finally:
            b.close()
    except Exception:  # noqa: BLE001
        pass
    return [n for n in out if len(n) >= 3]


def _key(companies, as_of) -> tuple:
    from .party_totals import _file_key

    return ("ic", as_of, tuple((n, _file_key(p)) for n, p, *_ in companies))


_CACHE: dict = {}


def check(companies, as_of: date | None = None) -> ICResult:
    """``companies`` = [(name, path[, role])]. Reads every company's books (reused until a file changes)."""
    as_of = (as_of or date.today()).isoformat()
    comps = [(n, str(p)) for n, p, *_ in companies if p and Path(p).exists()]
    key = _key(comps, as_of)
    if key in _CACHE:
        return _CACHE[key]
    res = _check(comps, as_of)
    if len(_CACHE) > 16:
        _CACHE.clear()
    _CACHE[key] = res
    _CACHE[_key(comps, as_of)] = res          # opening the books can touch the files
    return res


def _check(comps: list[tuple[str, str]], as_of: str) -> ICResult:
    from .books import Books

    names = {p: _names_of(p, n) for n, p in comps}
    accounts: list[ICAccount] = []
    errors: list[str] = []
    for name, path in comps:
        try:
            books = Books.open(path, create=False)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{name}: couldn't open the books ({e})")
            continue
        try:
            totals = books.ledger_totals(end=as_of)
            for a in books.accounts():
                if a.type not in ("asset", "liability"):
                    continue
                an = norm(a.name)
                if any(w in a.name.lower() for w in SKIP_WORDS):
                    continue
                other = None
                for on, op in comps:
                    if op == path:
                        continue
                    if any(re.search(rf"(^| ){re.escape(n)}( |$)", an) for n in names[op]):
                        # the longest name wins ("Elm Street Holdings" over "Elm Street")
                        best = max(len(n) for n in names[op] if re.search(rf"(^| ){re.escape(n)}( |$)", an))
                        if other is None or best > other[2]:
                            other = (on, op, best)
                if other is None:
                    continue
                net = totals.get(a.id, 0)       # debit − credit
                if not net and not books.conn.execute("SELECT 1 FROM journal_lines WHERE account_id=? LIMIT 1",
                                                      (a.id,)).fetchone():
                    continue
                accounts.append(ICAccount(name, path, a.id, a.name, other[0], other[1], a.type, net,
                                          a.type == "liability" and _is_debt(a)))
        finally:
            books.close()
    pairs: dict[tuple[str, str], ICPair] = {}
    order = {p: i for i, (_n, p) in enumerate(comps)}
    for acc in accounts:
        a_path, b_path = sorted((acc.path, acc.other_path), key=lambda p: order[p])
        a_name = acc.company if acc.path == a_path else acc.other
        b_name = acc.other if acc.path == a_path else acc.company
        pair = pairs.setdefault((a_path, b_path), ICPair(a_name, a_path, b_name, b_path, 0, 0))
        if acc.path == a_path:
            pair.a_says += acc.balance          # A's net: + = B owes A
            pair.a_accounts.append(acc)
        else:
            pair.b_says -= acc.balance          # B's net position is − of what B owes A
            pair.b_accounts.append(acc)
    out = sorted(pairs.values(), key=lambda p: (p.ok, order[p.a_path], order[p.b_path]))
    return ICResult(as_of, out, accounts, errors)


@dataclass
class Elimination:
    assets: int = 0          # intercompany balances counted in the company's total assets
    liabilities: int = 0     # … and in its total liabilities
    debt: int = 0            # … of which counted as debt


def eliminations(res: ICResult) -> dict[str, Elimination]:
    """By company file: the intercompany balances in its figures (to take out of combined figures)."""
    out: dict[str, Elimination] = {}
    for acc in res.accounts:
        e = out.setdefault(str(Path(acc.path).resolve()), Elimination())
        if acc.type == "asset":
            e.assets += acc.balance
        else:
            e.liabilities += -acc.balance
            if acc.debt:
                e.debt += -acc.balance
    return out
