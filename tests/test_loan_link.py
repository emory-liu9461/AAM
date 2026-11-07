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
    acct, opening = ll.ensure_account(pf, b, td, opening=True)   # older than the books: opening balance
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


def test_loans_go_on_the_balance_sheet_against_assets_held(tmp_path):
    from am_accounting import reports

    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 1, 2), "RENT DEPOSIT", 20_000_00),
                                 ParsedTransaction(date(2026, 5, 1), "EQUITABLE MTG PMT", -12_729_17)])
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("1001124102 Ontario Inc.", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    pfm.save_loan(pf, h.id, pfm.Loan("Equitable Bank", "Mortgage", 2_350_000_00, rate_pct=6.5,
                                     start_date="2026-04-01", term_months=12, repayment="Interest only"))
    pfm.save_loan(pf, h.id, pfm.Loan("Old Bank", "Term loan", 100_000_00, rate_pct=5.0, start_date="2024-01-01",
                                     term_months=60))
    assert ll.book_loans(pf, h) == 2
    assert ll.book_loans(pf, h) == 0                                   # nothing twice
    b = Books.open(tmp_path / "op.amdb")
    by = {x.lender: x for x in pfm.loans(pf, h, with_books=False)}
    eq, old = by["Equitable Bank"], by["Old Bank"]
    held = next(a for a in b.accounts() if a.name == "Assets Held")
    assert held.type == "asset" and held.subtype == "fixed_asset"
    assert ll.book_balance(b, eq.account_id, "2026-04-02") == 2_350_000_00     # borrowed on its start date
    assert ll.book_balance(b, eq.account_id, "2026-03-31") == 0
    sched = old.scheduled_balance(date(2026, 1, 1))
    assert ll.book_balance(b, old.account_id, "2026-01-02") == sched            # owed when the books start
    bs = reports.balance_sheet(b, "2026-06-30")
    assert all(bs.checks.values()) and bs.value("Assets Held") == 2_350_000_00 + sched
    assert pfm.loans(pf, h, date(2026, 6, 30))[0].balance in (2_350_000_00, sched)     # balance read from the books
    f = pfm.company_financials(str(tmp_path / "op.amdb"), date(2026, 6, 30))
    assert f.capex_ltm == 0 and f.debt == 2_350_000_00 + sched                 # financing, not capital spending
    assert pfm.capex_since(str(tmp_path / "op.amdb"), None, date(2026, 6, 30)) == 0
    b.close()
    # matched to another asset instead
    b = Books.open(tmp_path / "op.amdb")
    bldg = b.add_account("1610", "Building", "asset", "fixed_asset").id
    b.close()
    eq.asset_account_id = bldg
    pfm.save_loan(pf, h.id, eq)
    assert ll.book_loans(pf, h) == 1
    b = Books.open(tmp_path / "op.amdb")
    assert b.ledger_totals()[bldg] == 2_350_000_00
    # deleted in Journal Entries: left as it is
    b.delete_journal_entry({x.lender: x for x in pfm.loans(pf, h, with_books=False)}["Equitable Bank"].book_entry_id)
    b.close()
    assert ll.book_loans(pf, h) == 0
    eq2 = next(x for x in pfm.loans(pf, h, with_books=False) if x.lender == "Equitable Bank")
    assert eq2.book_entry_id == -1
    pf.close()
