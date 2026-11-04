from datetime import date

from am_accounting import loan_link as ll, portfolio as pfm
from am_accounting.books import Books, ParsedTransaction


def test_loan_payments_split_into_principal_and_interest(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [
        ParsedTransaction(date(2026, 1, 2), "RENT DEPOSIT", 20_000_00),
        ParsedTransaction(date(2026, 2, 1), "TD MORTGAGE PMT", -6_443_01),
        ParsedTransaction(date(2026, 3, 1), "TD MORTGAGE PMT", -6_443_01),
        ParsedTransaction(date(2026, 3, 5), "COFFEE", -6_443_01 + 1),     # same amount by chance, still found
        ParsedTransaction(date(2026, 3, 10), "BDC LOAN ADVANCE", 50_000_00),
    ])
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Op", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    pfm.save_loan(pf, h.id, pfm.Loan("TD Bank", "Mortgage", 1_000_000_00, rate_pct=6.0, start_date="2025-06-01",
                                     term_months=60, amort_months=300))
    pfm.save_loan(pf, h.id, pfm.Loan("BDC", "Term loan", 50_000_00, rate_pct=8.0, start_date="2026-03-10",
                                     term_months=36))
    ll.set_portfolio_path(tmp_path / "p.ampf")
    loans = ll.company_loans(b)
    assert [c.loan.lender for c in loans] == ["TD Bank", "BDC"]
    td, bdc = loans
    acct, opening = ll.ensure_account(pf, b, td)          # older than the books: opening balance
    assert b.account(acct).name == "Mortgage – TD Bank" and b.account(acct).type == "liability"
    sched = td.loan.scheduled_balance(date(2026, 1, 1))
    assert opening == sched and ll.book_balance(b, acct, "2026-01-02") == sched
    assert pfm.loans(pf, h, with_books=False)[0].account_id == acct      # linked in the portfolio
    ll.ensure_account(pf, b, bdc)
    sugg = ll.suggestions(b, ll.company_loans(b, pf))
    kinds = [(s.tx.description, s.kind, s.cl.loan.lender) for s in sugg]
    assert ("TD MORTGAGE PMT", "payment", "TD Bank") in kinds and ("BDC LOAN ADVANCE", "proceeds", "BDC") in kinds
    first = next(s for s in sugg if s.tx.date == "2026-02-01")
    assert first.interest == round(sched * 0.06 / 12) and first.principal + first.interest == 6_443_01
    ll.record_payment(b, first.tx.id, first.cl, first.principal, first.interest)
    assert ll.book_balance(b, acct, "2026-02-02") == sched - first.principal
    intr = ll.interest_account(b)
    assert b.ledger_totals()[intr] == first.interest and b.account(intr).name == "Interest Expense"
    tx = b.transaction(first.tx.id)
    assert tx.category_id == acct and tx.category_source == "loan" and tx.reviewed
    adv = next(s for s in sugg if s.kind == "proceeds")
    ll.record_proceeds(b, adv.tx.id, adv.cl)
    assert ll.book_balance(b, adv.cl.loan.account_id, "2026-03-11") == 50_000_00
    assert [(t.date, p, i) for t, p, i in ll.recorded(b, td.loan.id)] == [("2026-02-01", first.principal,
                                                                           first.interest)]
    left = ll.suggestions(b, ll.company_loans(b, pf))
    assert first.tx.id not in {s.tx.id for s in left} and adv.tx.id not in {s.tx.id for s in left}
    b.set_category(first.tx.id, None)                       # recategorizing undoes the split
    assert not b.splits(first.tx.id) and ll.book_balance(b, acct, "2026-02-02") == sched
    pf.close()
    b.close()
    ll.set_portfolio_path(None)


def test_consecutive_payments_use_the_falling_balance(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, m, 1), "MORTGAGE PMT", -6_443_01) for m in (2, 3, 4)])
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Op", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    pfm.save_loan(pf, h.id, pfm.Loan("TD Bank", "Mortgage", 1_000_000_00, rate_pct=6.0, start_date="2026-01-01",
                                     term_months=60, amort_months=300))
    cl = ll.company_loans(b, pf)[0]
    acct, _ = ll.ensure_account(pf, b, cl)
    b.set_opening_balance(acct, "2026-01-01", 1_000_000_00)
    sugg = ll.suggestions(b, ll.company_loans(b, pf))
    assert [s.interest for s in sugg] == [5_000_00, 4_992_78, 4_985_53]     # falls as principal is paid
    for s in sugg:
        p, i = ll.split_payment(b, s.cl.loan, s.tx.date, 6_443_01)
        ll.record_payment(b, s.tx.id, s.cl, p, i)
        assert (p, i) == (s.principal, s.interest)
    rows = pfm.amortization_schedule(cl.loan)
    assert ll.book_balance(b, acct, "2026-04-02") == rows[2].balance          # books match the table
    pf.close()
    b.close()
