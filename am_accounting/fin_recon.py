"""Financing & Cap Table against the company's balance sheet.

**Debt:** the loans on Financing & Cap Table (their balance — read from the books where linked to an account, as
entered where not) against the loans, mortgages and long-term liabilities on the balance sheet. The gap is loans
entered but not in the books, and debt in the books that isn't on Financing (no terms entered).

**Equity:** what the owners on the cap table have paid in against the owners' money on the balance sheet — share
capital / contributions (equity) and shareholder loans (owed to the owners). Retained earnings aren't owners' money
put in, so they're left out; Opening Balance Equity is shown, since unexplained amounts often sit there.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from . import portfolio as pfm

SHAREHOLDER_WORDS = ("shareholder", "due to owner", "due to shareholder", "owner loan", "partner loan")


def _is_shareholder(name: str) -> bool:
    return any(w in name.lower() for w in SHAREHOLDER_WORDS)


@dataclass
class Row:
    name: str
    financing: int | None        # on Financing & Cap Table (None = not there)
    books: int | None            # on the balance sheet (None = not in the books)
    note: str = ""

    @property
    def gap(self) -> int:
        return (self.financing or 0) - (self.books or 0)


@dataclass
class FinRecon:
    as_of: str
    debt: list[Row] = field(default_factory=list)
    equity: list[Row] = field(default_factory=list)
    opening_equity: int = 0          # Opening Balance Equity on the balance sheet
    retained: int = 0                # retained earnings + this year's profit (not owners' money put in)
    draws: int = 0                   # paid out to owners (draws / dividends)
    cap_table_entered: bool = True
    error: str = ""

    @property
    def debt_financing(self) -> int:
        return sum(r.financing or 0 for r in self.debt)

    @property
    def debt_books(self) -> int:
        return sum(r.books or 0 for r in self.debt)

    @property
    def debt_gap(self) -> int:
        return self.debt_financing - self.debt_books

    @property
    def equity_cap_table(self) -> int:
        return sum(r.financing or 0 for r in self.equity)

    @property
    def equity_books(self) -> int:
        return sum(r.books or 0 for r in self.equity)

    @property
    def equity_gap(self) -> int:
        return self.equity_cap_table - self.equity_books


def _balances(path: str, as_of: str) -> tuple[dict, dict]:
    """({id: (code, name, type, subtype, key)}, {id: debits − credits to as_of})."""
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}
        key = "std_key" if "std_key" in cols else "''"
        accts = {r[0]: r[1:] for r in conn.execute(f"SELECT id, code, name, type, subtype, {key} FROM accounts")}
        bal = dict(conn.execute(
            "SELECT l.account_id, SUM(l.debit_cents - l.credit_cents) FROM journal_lines l JOIN journal_entries e "
            "ON e.id = l.entry_id WHERE e.date <= ? GROUP BY l.account_id", (as_of,)).fetchall())
        return accts, bal
    finally:
        conn.close()


def reconcile(pf, h, as_of: date | None = None) -> FinRecon:
    as_of = as_of or date.today()
    r = FinRecon(as_of.isoformat())
    path = h.company_path
    loans = [ln for ln in pfm.loans(pf, h, as_of, with_books=False)]
    if not path or not Path(path).exists():
        r.error = "Not linked to its books"
        r.debt = [Row(f"{ln.kind} – {ln.lender}", ln.balance, None, "not in the books") for ln in loans]
        return r
    accts, bal = _balances(path, as_of.isoformat())
    owed = {i: -bal.get(i, 0) for i in accts}                      # credit balances, as owed
    debt_ids = [i for i, (_c, name, typ, sub, _k) in accts.items()
                if typ == "liability" and (sub == "long_term" or any(w in name.lower() for w in pfm.DEBT_WORDS))]
    linked = set()
    # the loans entered on Financing & Cap Table
    for ln in loans:
        label = f"{ln.kind} – {ln.lender}" if ln.lender else ln.kind
        if ln.account_id is not None and ln.account_id in accts:
            linked.add(ln.account_id)
            b = owed.get(ln.account_id, 0)
            r.debt.append(Row(label, b, b, f"read from “{accts[ln.account_id][1]}”"))
        else:
            r.debt.append(Row(label, ln.balance, None, "not in the books — Create Account in the Books"))
    # debt in the books that isn't on Financing (owners' loans are on the equity side)
    for i in debt_ids:
        name = accts[i][1]
        if i in linked or _is_shareholder(name) or not owed.get(i):
            continue
        r.debt.append(Row(name, None, owed[i], "in the books, not on Financing — add its terms"))
    # equity: the cap table's money paid in against share capital / contributions and shareholder loans
    holders = pfm.cap_table(pf, SimpleNamespace(holding=h, invested=0, value_100=None))
    r.cap_table_entered = not any(x.worked_out for x in holders)
    if r.cap_table_entered and holders:
        r.equity.append(Row("Owners on the cap table (paid in)", sum(x.funded for x in holders), None,
                            f"{len(holders)} owner(s)"))
    for i, (_c, name, typ, sub, key) in sorted(accts.items(), key=lambda kv: kv[1][0]):
        b = owed.get(i, 0)
        if not b:
            continue
        if typ == "equity" and (key == "opening_balance_equity" or "opening balance" in name.lower()):
            r.opening_equity += b
        elif typ == "equity" and sub == "contribution":
            r.equity.append(Row(name, None, b, "owners' money (equity)"))
        elif typ == "liability" and _is_shareholder(name):
            r.equity.append(Row(name, None, b, "owed to an owner (shareholder loan)"))
        elif typ == "equity" and sub == "draw":
            r.draws += -b
        elif typ == "equity":
            r.retained += b
    return r
