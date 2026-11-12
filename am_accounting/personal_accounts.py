"""Your bank accounts and investment accounts (Personal Finance → Bank Accounts / Investments). They flow into your
net worth by themselves — cash and investments as assets, credit cards and lines of credit as liabilities — instead
of being typed on the Net Worth page.

* **Bank accounts**: chequing, savings, cash, credit cards and lines of credit, each with its balance and the date of
  that balance (what you owe on a card or a line, for those).
* **Investment accounts**: RRSP / RRIF, TFSA, FHSA, RESP, non-registered … each with its holdings (symbol, quantity,
  price, book cost) and any cash in it; market value = quantity × price. Prices are typed in (nothing is looked up
  online).

Kept in the portfolio file. Lines you'd typed on the Net Worth page for these (cash, RRSP, TFSA, investments, credit
cards, lines of credit) are moved here once, so nothing is lost or counted twice.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

from . import personal as pers

BANK_TYPES = ["Chequing", "Savings", "Cash", "Credit card", "Line of credit"]
OWED_TYPES = {"Credit card", "Line of credit"}            # a balance you owe: a liability
INVEST_TYPES = ["RRSP / RRIF", "TFSA", "FHSA", "RESP", "Non-registered", "Other"]
# the Net Worth line each type becomes
BANK_KIND = {"Credit card": "Credit cards", "Line of credit": "Lines of credit"}
INVEST_KIND = {"RRSP / RRIF": "RRSP / RRIF", "TFSA": "TFSA", "Non-registered": "Investments (non-registered)"}
# typed Net Worth lines these replace (moved here once)
MOVED_ASSETS = {"Cash and bank accounts": None, "RRSP / RRIF": "RRSP / RRIF", "TFSA": "TFSA",
                "Investments (non-registered)": "Non-registered"}
MOVED_LIABILITIES = {"Credit cards": "Credit card", "Lines of credit": "Line of credit"}


@dataclass
class BankAccount:
    name: str
    type: str = "Chequing"
    institution: str = ""
    balance: int = 0                  # cents: what's in it; for a card or a line, what you owe
    as_of: str = ""
    note: str = ""
    id: int = 0

    @property
    def owed(self) -> bool:
        return self.type in OWED_TYPES


@dataclass
class Holding:
    symbol: str = ""
    name: str = ""
    quantity: float = 0.0
    price: int = 0                    # cents a unit
    book_cost: int = 0                # cents, all units

    @property
    def market_value(self) -> int:
        return round(self.quantity * self.price)


@dataclass
class InvestmentAccount:
    name: str
    type: str = "TFSA"
    institution: str = ""
    cash: int = 0                     # uninvested cash in the account
    holdings: list[Holding] = field(default_factory=list)
    as_of: str = ""
    id: int = 0

    @property
    def market_value(self) -> int:
        return self.cash + sum(h.market_value for h in self.holdings)

    @property
    def book_cost(self) -> int:
        return self.cash + sum(h.book_cost for h in self.holdings)

    @property
    def gain(self) -> int:
        return self.market_value - self.book_cost


@dataclass
class Accounts:
    banks: list[BankAccount] = field(default_factory=list)
    investments: list[InvestmentAccount] = field(default_factory=list)
    next_id: int = 1

    def new_id(self) -> int:
        self.next_id += 1
        return self.next_id - 1


def load(pf) -> Accounts:
    raw = pers._setting(pf, "personal_accounts")
    if not raw:
        return Accounts()
    d = json.loads(raw)
    return Accounts([BankAccount(**b) for b in d.get("banks", [])],
                    [InvestmentAccount(**{**i, "holdings": [Holding(**h) for h in i.get("holdings", [])]})
                     for i in d.get("investments", [])], d.get("next_id", 1))


def save(pf, a: Accounts) -> None:
    pers._set(pf, "personal_accounts", json.dumps({"banks": [asdict(b) for b in a.banks],
                                                   "investments": [asdict(i) for i in a.investments],
                                                   "next_id": a.next_id}))


def move_typed_lines(pf) -> int:
    """Once: the Net Worth lines typed for cash, RRSP, TFSA, investments, credit cards and lines of credit become
    accounts here (and leave the typed lines). Returns how many were moved."""
    if pers._setting(pf, "personal_accounts_moved"):
        return 0
    s = pers.load(pf)
    a = load(pf)
    today = date.today().isoformat()
    n = 0
    keep_a, keep_l = [], []
    for e in s.assets:
        if e.kind in MOVED_ASSETS and e.amount:
            name = e.description or e.kind
            if e.kind == "Cash and bank accounts":
                a.banks.append(BankAccount(name, "Chequing", balance=e.amount, as_of=today, id=a.new_id()))
            else:
                a.investments.append(InvestmentAccount(name, MOVED_ASSETS[e.kind], cash=e.amount, as_of=today,
                                                       id=a.new_id()))
            n += 1
        elif e.kind not in MOVED_ASSETS:
            keep_a.append(e)
    for e in s.liabilities:
        if e.kind in MOVED_LIABILITIES and e.amount:
            a.banks.append(BankAccount(e.description or e.kind, MOVED_LIABILITIES[e.kind], balance=e.amount,
                                       as_of=today, id=a.new_id()))
            n += 1
        elif e.kind not in MOVED_LIABILITIES:
            keep_l.append(e)
    s.assets, s.liabilities = keep_a, keep_l
    pers.save(pf, s)
    save(pf, a)
    pers._set(pf, "personal_accounts_moved", "1")
    return n


def entries(pf, a: Accounts | None = None) -> tuple[list[pers.Entry], list[pers.Entry]]:
    """The Net Worth lines these accounts make: (assets, liabilities)."""
    a = a or load(pf)
    assets, liabs = [], []
    for b in a.banks:
        if not b.balance:
            continue
        desc = " ".join(x for x in (b.institution, b.name) if x)
        if b.owed:
            liabs.append(pers.Entry(BANK_KIND[b.type], desc, b.balance))
        else:
            assets.append(pers.Entry("Cash and bank accounts", desc, b.balance))
    for i in a.investments:
        if i.market_value:
            assets.append(pers.Entry(INVEST_KIND.get(i.type, f"Investments ({i.type})"),
                                     " ".join(x for x in (i.institution, i.name) if x), i.market_value))
    return assets, liabs


def first_cash_account(pf) -> tuple[Accounts, BankAccount]:
    """Your first chequing / savings / cash account (made, as "Cash", if there's none)."""
    a = load(pf)
    acct = next((b for b in a.banks if not b.owed), None)
    if acct is None:
        acct = BankAccount("Cash", "Cash", id=a.new_id())
        a.banks.append(acct)
    return a, acct
