from datetime import date

from am_accounting import closing as cl
from am_accounting import deals as dl
from am_accounting import fin_recon as fr
from am_accounting import portfolio as pfm
from am_accounting.books import Books, CompanyProfile
from am_accounting.companies import CompanyRegistry


def test_financing_and_cap_table_against_the_balance_sheet(tmp_path):
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")

    def create_company(name, **kw):
        return str(reg.create(CompanyProfile(name, name, kw.get("entity_type", ""), role="operating")))

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("45 Elm St", stage="Under Contract", price_cents=4_000_000_00, closing_date="2026-01-15")
    dl.save(pf, d)
    s = cl.ClosingSetup("1001234567 Ontario Inc.", "45 Elm St", "2026-01-15", [("Purchase price", 4_000_000_00)],
                        [pfm.Loan("First National", "Mortgage", 2_800_000_00, rate_pct=5)],
                        [cl.EquityLine("Pat Lee", 800_000_00), cl.EquityLine("Kim Day", 400_000_00,
                                                                               form="Shareholder loan")])
    hid = cl.close_deal(pf, d.id, s, create_company)
    h = next(x for x in pf.holdings() if x.id == hid)
    r = fr.reconcile(pf, h, date(2026, 6, 30))
    assert (r.debt_financing, r.debt_books, r.debt_gap) == (2_800_000_00, 2_800_000_00, 0)   # straight from closing
    assert r.cap_table_entered and (r.equity_cap_table, r.equity_books, r.equity_gap) == (1_200_000_00,) * 2 + (0,)
    assert {x.name for x in r.equity} == {"Owners on the cap table (paid in)", "Share Capital – Class A",
                                          "Shareholder Loan – Kim Day"}
    # gaps: a loan entered but not in the books, debt in the books with no terms, an owner not in the books
    pfm.save_loan(pf, hid, pfm.Loan("Private Lender", "Term loan", 150_000_00, 150_000_00, 9.0))
    b = Books.open(h.company_path, create=False)
    loc = b.add_account("2720", "Line of Credit – RBC", "liability", "long_term").id
    b.set_opening_balance(loc, "2026-01-14", 60_000_00)
    b.close()
    pfm.save_shareholder(pf, hid, pfm.Shareholder("Sam Roe", amount_cents=100_000_00))
    r = fr.reconcile(pf, h, date(2026, 6, 30))
    rows = {x.name: x for x in r.debt}
    pl = rows["Term loan – Private Lender"]
    assert pl.books is None and "not in the books" in pl.note
    assert rows["Line of Credit – RBC"].financing is None and rows["Line of Credit – RBC"].books == 60_000_00
    assert r.debt_gap == 150_000_00 - 60_000_00
    assert r.equity_gap == 100_000_00 and r.opening_equity == -60_000_00   # the line of credit set against it
    pf.close()


def test_no_books(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("No books")
    pf.save_holding(h)
    pfm.save_loan(pf, h.id, pfm.Loan("Bank", "Mortgage", 100_000_00))
    r = fr.reconcile(pf, h)
    assert r.error and r.debt[0].financing == 100_000_00 and r.debt_gap == 100_000_00
    pf.close()
