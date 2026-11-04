"""The "To Categorize" queue: transactions that need a person to say what they were.

Bank descriptions like "WIRE TRANSFER OUT REF 88123", "ONLINE TRANSFER",
"ZELLE TO J SMITH", "CHECK 1042" or "DEPOSIT" don't say what the money was
for, so the app can't categorize them reliably. The queue lists:

* every transaction with no category, and
* vague ones (transfers, wires, ACH, P2P, checks, cash, plain deposits) that
  were only *guessed* by the built-in suggestions and haven't been reviewed.

For each one it offers quick choices that fit the kind of transaction, open
invoices/bills of the same amount, and similar transactions to answer at once.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import chart_of_accounts as coa
from .books import Books, Transaction

KINDS = [  # (kind, pattern) — first match wins
    ("e-Transfer", r"\be[-\s]?transfer\b|\be[-\s]?tfr\b|\be[-\s]?trf\b|\binterac\b|\bemt\b"),
    ("Wire", r"\bwire\b|\bwir\b|\bfedwire\b|\bswift\b"),
    ("Zelle / Venmo / PayPal", r"\bzelle\b|\bvenmo\b|\bcash ?app\b|\bpaypal\b|\bsquare cash\b"),
    ("Bank draft", r"\bdraft\b|\bmoney order\b|\bbank order\b"),
    ("Check", r"\bcheck\b|^\s*chk\s*#?\d|\bcheque\b|\bchq\b|^\s*#?\d{3,6}\s*$"),
    ("Transfer", r"\btransfer\b|\bxfer\b|\btrnsfr\b|\btfr\b|\bbook transfer\b|\bsweep\b"),
    ("ATM / cash", r"\batm\b|\bcash (withdrawal|deposit)\b|\bcounter withdrawal\b"),
    ("ACH / electronic", r"\bach\b|\beft\b|\belectronic\b|\bppd\b|\bccd\b|\bweb\b"),
    ("Deposit", r"\bdeposit\b|\bdep\b|\bmobile dep|\bremote dep|\bcounter credit\b"),
]
_KIND_RX = [(k, re.compile(p, re.I)) for k, p in KINDS]
VAGUE_KINDS = {k for k, _ in KINDS}

FILTERS = {
    "all": "Everything to categorize",
    "uncategorized": "Uncategorized only",
    "e-Transfer": "e-Transfers (SEND E-TFR, E-TRANSFER…)",
    "Transfer": "Transfers",
    "Wire": "Wires",
    "Zelle / Venmo / PayPal": "Zelle / Venmo / PayPal",
    "Check": "Cheques",
    "Bank draft": "Bank drafts",
    "Deposit": "Deposits",
    "other": "Everything else",
}


def kind_of(description: str) -> str:
    from .etransfer import is_etransfer

    if is_etransfer(description):  # the same test the e-Transfer history matching uses
        return "e-Transfer"
    for kind, rx in _KIND_RX:
        if kind == "e-Transfer":
            continue  # handled above ("SEND E-TFR FEE" is the bank's charge: not an e-Transfer)
        if rx.search(description):
            return kind
    return "Other"


def check_number(description: str) -> str:
    m = re.search(r"(?:check|chk|chq|cheque)\s*#?\s*(\d{2,7})", description, re.I) or \
        re.fullmatch(r"\s*#?(\d{3,6})\s*", description)
    return m.group(1) if m else ""


# Quick choices per kind: (label, account key). Keys refer to the standard accounts;
# choices whose account doesn't exist in this company are left out.
QUICK_CHOICES_OUT = {
    "e-Transfer": [("Contractor", "6100"), ("Paid a vendor / supplier", "5000"), ("Rent", "6400"),
                   ("Owner draw / distribution", "3100"), ("To another of my accounts", coa.TRANSFER_CLEARING)],
    "Bank draft": [("Paid a vendor / supplier", "5000"), ("Rent", "6400"), ("Taxes & licenses", "6550"),
                   ("Loan payment", "2500"), ("Owner draw / distribution", "3100")],
    "Transfer": [("To another of my accounts", coa.TRANSFER_CLEARING), ("Owner draw / distribution", "3100"),
                 ("Credit card payment", "2100"), ("Loan payment", "2500")],
    "Wire": [("Paid a vendor / supplier", "5000"), ("Contractor", "6100"), ("Owner draw / distribution", "3100"),
             ("To another of my accounts", coa.TRANSFER_CLEARING), ("Loan payment", "2500")],
    "Zelle / Venmo / PayPal": [("Contractor", "6100"), ("Owner draw / distribution", "3100"),
                               ("Meals", "6250"), ("Office supplies", "6300")],
    "Check": [("Rent", "6400"), ("Contractor", "6100"), ("Paid a vendor / supplier", "5000"),
              ("Taxes & licenses", "6550")],
    "ATM / cash": [("Owner draw / distribution", "3100"), ("Office supplies", "6300"), ("Meals", "6250")],
    "ACH / electronic": [("Software & subscriptions", "6500"), ("Utilities", "6700"), ("Insurance", "6150"),
                         ("Loan payment", "2500")],
    "Deposit": [("Sales", "4000"), ("Services", "4100")],
    "Other": [("Office supplies", "6300"), ("Meals", "6250"), ("Travel", "6650")],
}
QUICK_CHOICES_IN = {
    "e-Transfer": [("Customer payment – sales", "4000"), ("Customer payment – services", "4100"),
                   ("Owner contribution", "3000"), ("From another of my accounts", coa.TRANSFER_CLEARING),
                   ("Refund", "4500")],
    "Bank draft": [("Customer payment – sales", "4000"), ("Customer payment – services", "4100"),
                   ("Loan received", "2500"), ("Owner contribution", "3000")],
    "Transfer": [("From another of my accounts", coa.TRANSFER_CLEARING), ("Owner contribution", "3000"),
                 ("Loan received", "2500")],
    "Wire": [("Customer payment – sales", "4000"), ("Customer payment – services", "4100"),
             ("Owner contribution", "3000"), ("From another of my accounts", coa.TRANSFER_CLEARING),
             ("Loan received", "2500")],
    "Zelle / Venmo / PayPal": [("Sales", "4000"), ("Services", "4100"), ("Owner contribution", "3000")],
    "Check": [("Sales", "4000"), ("Services", "4100"), ("Refund", "4500")],
    "ATM / cash": [("Cash sales", "4000"), ("Owner contribution", "3000")],
    "ACH / electronic": [("Sales", "4000"), ("Services", "4100"), ("Refund", "4500"), ("Interest", "4800")],
    "Deposit": [("Sales", "4000"), ("Services", "4100"), ("Owner contribution", "3000"),
                ("From another of my accounts", coa.TRANSFER_CLEARING), ("Refund", "4500")],
    "Other": [("Sales", "4000"), ("Services", "4100"), ("Other income", "4900")],
}


@dataclass
class QueueItem:
    tx: Transaction
    kind: str
    reason: str                          # "Uncategorized" or "Guessed – please confirm"
    similar_ids: list[int] = field(default_factory=list)   # other queued transactions from the same source
    open_documents: list = field(default_factory=list)     # invoices/bills with the same amount
    name: str = ""                       # who it was with (e-Transfer / cheque / your name / name rule)
    name_source: str = ""

    @property
    def check_no(self) -> str:
        return check_number(self.tx.description) if self.kind == "Check" else ""


# Words that say how money moved but not who it was with or what it was for.
GENERIC_WORDS = {
    "TRANSFER", "XFER", "TRNSFR", "TFR", "WIRE", "FEDWIRE", "SWIFT", "ONLINE", "INTERNET", "MOBILE", "REMOTE",
    "BRANCH", "INCOMING", "OUTGOING", "OUT", "IN", "ORIG", "BENE", "BNF", "SAV", "SAVINGS", "CHK", "CHECKING",
    "ACCOUNT", "ACCT", "ATM", "CASH", "ZELLE", "VENMO", "ACH", "EFT", "BOOK", "INTERNAL", "EXTERNAL", "FUNDS",
    "DEP", "DEPOSIT", "WITHDRAWAL", "WITHDRAW", "DEBIT", "CREDIT", "PAYMENT", "SENT", "RECEIVED", "BANKING",
    "COUNTER", "TELLER", "SHARE", "DRAFT", "MONEY", "FROM", "TO", "VIA", "BY", "ITEM",
}


def is_generic(tx: Transaction) -> bool:
    """True when the description names no merchant, only how the money moved."""
    words = re.sub(r"[^A-Z ]", " ", tx.description.upper()).split()
    meaningful = [w for w in words if len(w) > 1 and w not in GENERIC_WORDS]
    return not meaningful


def needs_attention(tx: Transaction) -> str | None:
    """Why a transaction belongs in the queue, or None."""
    if tx.category_id is None:
        return "Uncategorized"
    if (not tx.reviewed and tx.category_source == "keyword" and kind_of(tx.description) in VAGUE_KINDS
            and is_generic(tx)):
        return "Guessed – please confirm"
    return None


NO_NAME = "\x00no name"


def queue(books: Books, filter_key: str = "all", name_filter: str | None = None) -> list[QueueItem]:
    """Transactions to categorize. ``name_filter``: a name (case-insensitive), NO_NAME, or None for everyone."""
    from . import parties
    from . import receivables as rx

    txs = [t for t in books.transactions() if needs_attention(t)]
    named = parties.names(books, [t.id for t in txs])
    by_key: dict[tuple[str, bool], list[int]] = {}
    by_name: dict[tuple[str, bool], list[int]] = {}
    for t in txs:
        if t.id in named:
            by_name.setdefault((named[t.id].name.lower(), t.amount_cents > 0), []).append(t.id)
        elif t.merchant_key:
            by_key.setdefault((t.merchant_key, t.amount_cents > 0), []).append(t.id)
    by_amount: dict[tuple[bool, int], list] = {}  # open invoices (True) / bills (False) by balance
    for kind, money_in in (("invoice", True), ("bill", False)):
        for d in rx.documents(books, kind, status="open", as_of=_far_future()):
            by_amount.setdefault((money_in, d.balance_cents), []).append(d)
    items = []
    for t in txs:
        kind = kind_of(t.description)
        if filter_key == "uncategorized" and t.category_id is not None:
            continue
        if filter_key == "other" and kind in VAGUE_KINDS:
            continue
        if filter_key in VAGUE_KINDS and kind != filter_key:
            continue
        n = named.get(t.id)
        if name_filter is not None:
            if name_filter == NO_NAME and n is not None:
                continue
            if name_filter != NO_NAME and (n is None or n.name.lower() != name_filter.lower()):
                continue
        if n is not None:  # "similar" = everything else with this name, same direction
            similar = [i for i in by_name.get((n.name.lower(), t.amount_cents > 0), []) if i != t.id]
        else:
            similar = [i for i in by_key.get((t.merchant_key, t.amount_cents > 0), []) if i != t.id]
        matches = list(by_amount.get((t.amount_cents > 0, abs(t.amount_cents)), []))
        items.append(QueueItem(t, kind, needs_attention(t), similar, matches, n.name if n else "",
                               n.source if n else ""))
    return items


def counts(books: Books) -> dict[str, int]:
    out = {"total": 0, "uncategorized": 0, "guessed": 0}
    for t in books.transactions():
        why = needs_attention(t)
        if why:
            out["total"] += 1
            out["uncategorized" if t.category_id is None else "guessed"] += 1
    return out


def _far_future():
    from datetime import date

    return date(9999, 12, 31)


def quick_choices(books: Books, item: QueueItem) -> list[tuple[str, int]]:
    """[(label, account id)] suited to this kind of transaction, for one-click answers."""
    table = QUICK_CHOICES_IN if item.tx.amount_cents > 0 else QUICK_CHOICES_OUT
    out, seen = [], set()
    if item.name:  # the contact's own accounts first, e.g. "Due from Sister Co"
        from . import contacts

        c = contacts.find(books, item.name)
        if c:
            money_in = item.tx.amount_cents > 0
            ids = [c.receivable_id, c.payable_id, c.income_id if money_in else c.expense_id]
            for acct_id in ids:
                if not acct_id or acct_id in seen or acct_id == item.tx.bank_account_id:
                    continue
                try:
                    acct = books.account(acct_id)
                except KeyError:
                    continue
                out.append((acct.name, acct.id))
                seen.add(acct.id)
    for label, key in table.get(item.kind, table["Other"]):
        try:
            acct = books.account_by_key(key)
        except KeyError:
            continue
        if acct.active and acct.id != item.tx.bank_account_id and acct.id not in seen:
            out.append((label, acct.id))
            seen.add(acct.id)
    return out


def suggested_rule_pattern(description: str) -> str:
    """A 'description contains' pattern for future look-alikes: the words before any reference number.

    "ZELLE TO JOHN SMITH 8812"            -> "zelle to john smith"
    "WIRE TRANSFER OUT REF 88123 ACME"    -> "wire transfer out"
    """
    words = []
    for w in description.split():
        if re.search(r"\d|[#*]", w) or w.upper() in {"REF", "REF:", "ID", "ID:", "CONF", "CONF#", "TRN"}:
            if words:
                break
            continue
        words.append(w)
    return " ".join(words).lower().strip(" -:") or description.lower()


def categorize(books: Books, tx_ids: list[int], category_id: int, *, memo: str | None = None,
               rule_pattern: str | None = None) -> int:
    """Categorize transactions (as your decision) and optionally remember it as a rule. Returns how many."""
    done = 0
    with books.batch():
        for tx_id in tx_ids:
            tx = books.transaction(tx_id)
            if category_id == tx.bank_account_id:
                continue
            books.set_category(tx_id, category_id, source="manual", reviewed=True)
            if memo is not None and memo.strip():
                books.set_memo(tx_id, memo.strip())
            done += 1
    if rule_pattern and rule_pattern.strip():
        sample = books.transaction(tx_ids[0])
        direction = "in" if sample.amount_cents > 0 else "out"
        if not any(r.pattern.lower() == rule_pattern.strip().lower() and r.direction == direction
                   for r in books.rules()):
            books.add_rule(rule_pattern.strip(), category_id, direction=direction, priority=50)
    return done


def confirm_guess(books: Books, tx_id: int) -> None:
    """Accept the suggested category as correct."""
    books.set_category(tx_id, books.transaction(tx_id).category_id, source="manual", reviewed=True)
