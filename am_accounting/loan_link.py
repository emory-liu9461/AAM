"""Loans in the accounting: the loans entered in Asset Management (Financing & Cap Table) meet the company's books.

* Each loan gets a liability account in the books (created on request, with an opening balance when the loan is
  older than the books).
* A loan payment in the bank feed is split: **principal** to the loan account (the balance goes down) and
  **interest** to Interest Expense. Interest is the balance owed in the books before the payment × the rate for
  one payment period (the amortization table when the books don't have a balance yet).
* The money received when the loan was advanced goes to the loan account.
* Payments are found by amount (the loan's payment) or by the lender's name with a loan word in the description.

* **On the balance sheet:** a loan entered on Financing & Cap Table is put in the books — the loan account (a
  liability) against **Assets Held** (an asset), or another asset account chosen on the loan — by a journal entry
  for the amount borrowed on its start date (for a loan older than the books, what was owed the day before they
  start). The entry shows in Journal Entries; change or delete it there and it is left as you made it. Recording the
  loan's advance from the bank instead (the money came into the bank account) replaces it.

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


def ensure_account(pf: pfm.Portfolio, books: Books, cl: CompanyLoan, *, opening: bool = False) -> tuple[int, int]:
    """The loan's liability account in the books, created if needed (then put on the balance sheet against Assets
    Held — see book_loan). With ``opening``, a loan older than the books gets an opening balance instead. Returns
    (account id, opening balance set)."""
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
    first = books.conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
    if not opening:
        ln.account_id = acct
        ln.balance_cents = None
        pfm.save_loan(pf, cl.holding.id, ln)
        return acct, 0
    opening = 0
    if ln.start_date and first and ln.start_date < first:
        sb = ln.scheduled_balance(date.fromisoformat(first) - timedelta(days=1))
        opening = sb if sb is not None else ln.balance
        if opening:
            books.set_opening_balance(acct, (date.fromisoformat(first) - timedelta(days=1)).isoformat(), opening)
    ln.account_id = acct
    ln.balance_cents = None
    pfm.save_loan(pf, cl.holding.id, ln)
    return acct, opening


ASSETS_HELD = "Assets Held"
OPENING_NOTE = "; the opening balance in the books)"


def assets_held_account(books: Books) -> int:
    """The Assets Held account (a fixed asset) loans are matched to unless another is chosen; created if needed."""
    for a in books.accounts():
        if a.type == "asset" and a.name.lower() == ASSETS_HELD.lower():
            return a.id
    used = {a.code for a in books.accounts()}
    code = "1600"
    while code in used:
        code = str(int(code) + 1)
    return books.add_account(code, ASSETS_HELD, "asset", "fixed_asset").id


def _entry_exists(books: Books, entry_id: int | None) -> bool:
    return bool(entry_id and entry_id > 0 and
                books.conn.execute("SELECT 1 FROM journal_entries WHERE id=?", (entry_id,)).fetchone())


def loan_posting(books: Books, ln: pfm.Loan) -> tuple[str, int]:
    """(date, amount) the loan goes on the balance sheet: what was borrowed on its start date, or — for a loan older
    than the books — what was owed the day before they start."""
    first = books.conn.execute("SELECT MIN(date) FROM transactions").fetchone()[0]
    if ln.start_date and (not first or ln.start_date >= first):
        return ln.start_date, ln.principal_cents or ln.balance
    day = (date.fromisoformat(first) - timedelta(days=1)) if first else \
        (date.fromisoformat(ln.start_date) if ln.start_date else date.today())
    if ln.balance_cents is not None and not first:
        return day.isoformat(), ln.balance_cents
    sb = ln.scheduled_balance(day) if ln.start_date else None
    return day.isoformat(), sb if sb is not None else ln.balance


def _proceeds_recorded(books: Books, ln: pfm.Loan) -> bool:
    return bool(books.conn.execute(
        "SELECT 1 FROM tx_splits s JOIN accounts a ON a.id = s.account_id WHERE s.ref=? AND a.type='liability' "
        "AND s.amount_cents > 0 AND EXISTS (SELECT 1 FROM transactions t WHERE t.id = s.transaction_id AND "
        "t.amount_cents > 0)", (f"loan:{ln.id}",)).fetchone())


def book_loan(pf: pfm.Portfolio, books: Books, cl: CompanyLoan) -> str:
    """Put a loan on the company's balance sheet: its account (created if needed) against Assets Held (or the asset
    chosen on the loan). Kept up to date when the loan's amount, start or asset change; left alone once changed or
    deleted in the books, or when the account already had a balance of its own. Returns what happened:
    'posted', 'updated', 'unchanged' or 'skipped'."""
    ln = cl.loan
    if ln.id is None or ln.terms_missing or (ln.book_entry_id or 0) < 0:
        return "skipped"
    mine = ln.book_entry_id if _entry_exists(books, ln.book_entry_id) else None
    if ln.book_entry_id and mine is None:          # deleted or changed in Journal Entries: leave it as it is
        ln.book_entry_id = -1
        pfm.save_loan(pf, cl.holding.id, ln)
        return "skipped"
    if ln.account_id is not None and _proceeds_recorded(books, ln):   # the money came into the bank instead
        if mine:
            books.delete_journal_entry(mine)
            ln.book_entry_id = -1
            pfm.save_loan(pf, cl.holding.id, ln)
        return "skipped"
    acct, _ = ensure_account(pf, books, cl)
    day, amt = loan_posting(books, ln)
    if amt <= 0:
        return "skipped"
    # an account with a balance of its own already (an opening balance or other entries by then) is left as it is —
    # except an opening balance, which is moved onto Assets Held
    others = books.conn.execute(
        "SELECT e.id, e.kind, l.credit_cents - l.debit_cents, e.date FROM journal_lines l JOIN journal_entries e "
        "ON e.id = l.entry_id WHERE l.account_id=? AND e.date <= ? AND e.id IS NOT ?", (acct, day, mine)).fetchall()
    if any(k != "opening" for _i, k, _n, _d in others):
        return "skipped"
    old = next((e for e in books.journal_entries(kind="manual") if e["id"] == mine), None) if mine else None
    kept = bool(old and old["memo"].endswith(OPENING_NOTE))
    if others:                       # its opening balance in the books: the amount the books have
        amt = sum(n for _i, _k, n, _d in others)
        day = min(d for _i, _k, _n, d in others)
        if amt <= 0:
            return "skipped"
        for i, _k, _n, _d in others:
            books.conn.execute("DELETE FROM journal_entries WHERE id=?", (i,))
        books.conn.commit()
        kept = True
    elif kept:                       # an opening balance moved here earlier keeps its amount
        amt, day = sum(c for _a, _d, c in old["lines"]), old["date"]
    asset = ln.asset_account_id
    try:
        if asset is None or books.account(asset).type != "asset":
            raise KeyError
    except KeyError:
        asset = assets_held_account(books)
    lines = [(asset, amt, 0), (acct, 0, amt)]
    memo = f"{ln.kind} – {ln.lender} (matched to {books.account(asset).name}" + (OPENING_NOTE if kept else ")")
    if mine:
        if old and old["date"] == day and sorted(old["lines"]) == sorted(lines) and old["memo"] == memo:
            return "unchanged"
        books.delete_journal_entry(mine)
    ln.book_entry_id = books.add_journal_entry(day, memo, lines)
    pfm.save_loan(pf, cl.holding.id, ln)
    return "updated" if mine else "posted"


def book_loans(pf: pfm.Portfolio, holding: pfm.Holding) -> int:
    """Put every loan of a company on its balance sheet (see book_loan). Returns how many entries changed."""
    if not holding.company_path or not Path(holding.company_path).exists():
        return 0
    books = Books.open(holding.company_path, create=False)
    try:
        n = 0
        for ln in pfm.loans(pf, holding, with_books=False):
            n += book_loan(pf, books, CompanyLoan(holding, ln)) in ("posted", "updated")
        return n
    finally:
        books.close()


def unbook_loan(books: Books, ln: pfm.Loan) -> None:
    """Take a loan's balance-sheet entry out of the books (when the loan is deleted)."""
    if _entry_exists(books, ln.book_entry_id):
        books.delete_journal_entry(ln.book_entry_id)


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
    unbook_loan(books, ln)   # the money came into the bank: no longer matched to Assets Held
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
