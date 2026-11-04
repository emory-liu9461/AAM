"""Loans in the accounting: the loans entered in Asset Management (Financing & Cap Table) meet the company's books.

* Each loan gets a liability account in the books (created on request, with an opening balance when the loan is
  older than the books).
* A loan payment in the bank feed is split: **principal** to the loan account (the balance goes down) and
  **interest** to Interest Expense. Interest is the balance owed in the books before the payment × the rate for
  one payment period (the amortization table when the books don't have a balance yet).
* The money received when the loan was advanced goes to the loan account.
* Payments are found by amount (the loan's payment) or by the lender's name with a loan word in the description.

Because the split is posted in the books, the loan's balance on Financing & Cap Table (read from the books), the
balance sheet, interest in Non-Op. Ex and the cash flow all follow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from . import portfolio as pfm
from .books import Books, Transaction

_PORTFOLIO: Path | None = None
LOAN_WORDS = ("MORTGAGE", "MTG", "LOAN", "LN PMT", "LOAN PMT", "LINE OF CREDIT", "LOC ", "HELOC", "FINANCING")


def set_portfolio_path(path: str | Path | None) -> None:
    """Where the portfolio (with the loans) is kept; set by the main window."""
    global _PORTFOLIO
    _PORTFOLIO = Path(path) if path else None


def portfolio_path() -> Path | None:
    return _PORTFOLIO if _PORTFOLIO and _PORTFOLIO.exists() else None


@dataclass
class CompanyLoan:
    holding: pfm.Holding
    loan: pfm.Loan


def company_loans(books: Books, pf: pfm.Portfolio | None = None) -> list[CompanyLoan]:
    """The loans entered in Asset Management for this company's books."""
    own = pf is None
    if own:
        path = portfolio_path()
        if not path:
            return []
        pf = pfm.Portfolio(path)
    try:
        out = []
        for h in pf.holdings():
            if h.company_path and pfm._same_file(h.company_path, str(books.path)):
                out += [CompanyLoan(h, ln) for ln in pfm.loans(pf, h, with_books=False)]
        return out
    finally:
        if own:
            pf.close()


def interest_account(books: Books) -> int:
    """The Interest Expense account (created if there isn't one)."""
    for a in books.accounts():
        if a.type == "expense" and "interest" in a.name.lower() and a.active:
            return a.id
    code = "6900"
    used = {a.code for a in books.accounts()}
    while code in used:
        code = str(int(code) + 1)
    return books.add_account(code, "Interest Expense", "expense", "other").id


def ensure_account(pf: pfm.Portfolio, books: Books, cl: CompanyLoan) -> tuple[int, int]:
    """The loan's liability account in the books, created if needed. A loan older than the books gets an opening
    balance (its scheduled balance on the books' first day). Returns (account id, opening balance set)."""
    ln = cl.loan
    if ln.account_id is not None:
        try:
            books.account(ln.account_id)
            return ln.account_id, 0
        except KeyError:
            pass
    used = {a.code for a in books.accounts()}
    code = "2700"
    while code in used:
        code = str(int(code) + 1)
    name = f"{ln.kind} – {ln.lender}" if ln.lender else ln.kind
    base, n = name, 2
    while any(a.name.lower() == name.lower() for a in books.accounts()):
        name, n = f"{base} ({n})", n + 1
    acct = books.add_account(code, name, "liability", "long_term").id
    opening = 0
    first = books.conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
    if ln.start_date and first and ln.start_date < first:
        sb = ln.scheduled_balance(date.fromisoformat(first) - timedelta(days=1))
        opening = sb if sb is not None else ln.balance
        if opening:
            books.set_opening_balance(acct, (date.fromisoformat(first) - timedelta(days=1)).isoformat(), opening)
    ln.account_id = acct
    ln.balance_cents = None
    pfm.save_loan(pf, cl.holding.id, ln)
    return acct, opening


def book_balance(books: Books, account_id: int, before: str) -> int:
    """What's owed on the loan account at the end of the day before ``before``."""
    end = (date.fromisoformat(before) - timedelta(days=1)).isoformat()
    return -books.ledger_totals(end=end).get(account_id, 0)


def split_payment(books: Books, ln: pfm.Loan, tx_date: str, amount: int, *, less: int = 0) -> tuple[int, int]:
    """(principal, interest) of a payment of ``amount`` cents on ``tx_date`` (``less`` = principal of earlier
    payments about to be recorded too)."""
    n = pfm.PER_YEAR.get(ln.payment_freq, 12) or 12
    bal = (book_balance(books, ln.account_id, tx_date) - less) if ln.account_id is not None else 0
    if bal > 0:
        interest = round(bal * (ln.rate_pct or 0) / 100 / n)
    else:
        rows = pfm.amortization_schedule(ln)
        near = min(rows, key=lambda r: abs((date.fromisoformat(r.date) - date.fromisoformat(tx_date)).days),
                   default=None)
        interest = near.interest if near and near.number else round(ln.balance * (ln.rate_pct or 0) / 100 / n)
    interest = max(0, min(interest, amount))
    return amount - interest, interest


def record_payment(books: Books, tx_id: int, cl: CompanyLoan, principal: int, interest: int) -> None:
    ln = cl.loan
    if ln.account_id is None:
        raise ValueError("Create the loan's account in the books first")
    parts = [(ln.account_id, principal, f"Principal – {ln.lender}", f"loan:{ln.id}"),
             (interest_account(books), interest, f"Interest – {ln.lender}", f"loan:{ln.id}")]
    books.set_splits(tx_id, parts, source="loan")


def record_proceeds(books: Books, tx_id: int, cl: CompanyLoan) -> None:
    ln = cl.loan
    if ln.account_id is None:
        raise ValueError("Create the loan's account in the books first")
    tx = books.transaction(tx_id)
    books.set_splits(tx_id, [(ln.account_id, abs(tx.amount_cents), f"Advance – {ln.lender}", f"loan:{ln.id}")],
                     source="loan")


def recorded(books: Books, loan_id: int) -> list[tuple[Transaction, int, int]]:
    """[(transaction, principal, interest)] recorded against a loan, oldest first."""
    rows = books.conn.execute(
        "SELECT s.transaction_id, a.type, s.amount_cents FROM tx_splits s JOIN accounts a ON a.id = s.account_id "
        "WHERE s.ref=?", (f"loan:{loan_id}",)).fetchall()
    by_tx: dict[int, list[int]] = {}
    for tx_id, typ, cents in rows:
        p = by_tx.setdefault(tx_id, [0, 0])
        p[0 if typ == "liability" else 1] += cents
    out = [(books.transaction(t), p, i) for t, (p, i) in by_tx.items()]
    return sorted(out, key=lambda x: x[0].date)


def is_loan_tx(books: Books, tx_id: int) -> bool:
    return bool(books.conn.execute("SELECT 1 FROM tx_splits WHERE transaction_id=? AND ref LIKE 'loan:%'",
                                   (tx_id,)).fetchone())


@dataclass
class Suggestion:
    tx: Transaction
    cl: CompanyLoan
    kind: str            # payment | proceeds
    principal: int
    interest: int
    why: str


def _lender_word(lender: str) -> str:
    w = re.sub(r"[^A-Z0-9 ]", " ", lender.upper()).split()
    return w[0] if w else ""


def suggestions(books: Books, loans: list[CompanyLoan]) -> list[Suggestion]:
    """Bank lines that look like loan payments or advances and haven't been recorded against a loan yet."""
    out: list[Suggestion] = []
    taken: set[int] = set()
    open_txs = [t for t in books.transactions()
                if not is_loan_tx(books, t.id) and (t.category_id is None or not t.reviewed
                                                     or t.category_source in (None, "keyword", "learned"))]
    for cl in loans:
        ln = cl.loan
        if ln.terms_missing:
            continue
        start = ln.start_date or "0000-01-01"
        end = ln.maturity_date
        last = (date.fromisoformat(end) + timedelta(days=31)).isoformat() if end else "9999-12-31"
        pay = ln.payment
        word = _lender_word(ln.lender)
        pending = 0  # principal of the payments suggested so far (they lower the balance for the next ones)
        for t in sorted(open_txs, key=lambda x: x.date):
            if t.id in taken or not start <= t.date <= last:
                continue
            desc = " ".join(t.description.upper().split())
            if t.amount_cents < 0:
                amt = -t.amount_cents
                by_amount = pay and abs(amt - pay) <= max(100, pay // 100)
                by_name = word and len(word) >= 2 and word in desc.split() and any(x in desc for x in LOAN_WORDS)
                if by_amount or by_name:
                    p, i = split_payment(books, ln, t.date, amt, less=pending)
                    pending += p
                    out.append(Suggestion(t, cl, "payment", p, i, "same amount as the payment" if by_amount
                                          else f"“{word}” and a loan word in the description"))
                    taken.add(t.id)
            elif ln.principal_cents and ln.start_date:
                near_start = abs((date.fromisoformat(t.date) - date.fromisoformat(ln.start_date)).days) <= 45
                if near_start and abs(t.amount_cents - ln.principal_cents) <= max(100, ln.principal_cents // 200):
                    out.append(Suggestion(t, cl, "proceeds", t.amount_cents, 0, "the loan amount, near its start"))
                    taken.add(t.id)
    return sorted(out, key=lambda s: s.tx.date)
