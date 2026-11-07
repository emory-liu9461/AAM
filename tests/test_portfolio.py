from datetime import date

import pytest

from am_accounting import portfolio as pfm
from am_accounting.books import Books, CompanyProfile, ParsedTransaction


@pytest.fixture
def pf(tmp_path):
    p = pfm.Portfolio(tmp_path / "portfolio.ampf")
    yield p
    p.close()


def test_xirr():
    # 100 in, 121 back two years later → 10%
    r = pfm.xirr([(date(2024, 1, 1), -10000), (date(2026, 1, 1), 12100)])
    assert abs(r - 0.10) < 0.001
    assert pfm.xirr([(date(2024, 1, 1), -100)]) is None


def test_holding_returns_with_valuation(pf):
    h = pfm.Holding("Lakeshore Apartments LP", "Real estate", 60, "2023-01-01")
    pf.save_holding(h)
    pf.add_flow(h.id, pfm.Flow("2023-01-01", "acquisition", 600000_00))
    pf.add_flow(h.id, pfm.Flow("2024-06-30", "distribution", 30000_00))
    pf.add_flow(h.id, pfm.Flow("2025-06-30", "distribution", 30000_00))
    pf.add_valuation(h.id, pfm.Valuation("2025-12-31", 1_200_000_00, "Appraisal"))
    p = pfm.performance(pf, h, date(2026, 1, 1))
    assert (p.invested, p.returned) == (600000_00, 60000_00)
    assert p.nav == 720000_00                     # 60% of $1.2M
    assert p.gain == 180000_00 and abs(p.moic - 1.3) < 1e-9
    assert 0.08 < p.irr < 0.11
    with pytest.raises(ValueError, match="already"):
        pf.save_holding(pfm.Holding("lakeshore apartments lp"))
    with pytest.raises(ValueError, match="Ownership"):
        pf.save_holding(pfm.Holding("X", ownership_pct=150))


def test_linked_books_give_financials_and_book_value(pf, tmp_path):
    b = Books.open(tmp_path / "OpCo.amdb")
    b.set_profile(CompanyProfile("Northwind OpCo Inc"))
    bank = b.account_by_code("1000").id
    b.set_opening_balance(bank, "2025-12-31", 50000_00)
    b.import_transactions(bank, [ParsedTransaction(date(2026, 3, 1), "CLIENT", 120000_00),
                                 ParsedTransaction(date(2026, 3, 5), "RENT", -20000_00)])
    for t in b.transactions():
        b.set_category(t.id, b.account_by_code("4100" if t.amount_cents > 0 else "6400").id, source="manual")
    loan = b.add_account("2610", "Bank Loan", "liability", "long_term")
    b.set_opening_balance(loan.id, "2025-12-31", 30000_00)
    b.close()
    f = pfm.company_financials(tmp_path / "OpCo.amdb", date(2026, 6, 30))
    assert f.company == "Northwind OpCo Inc" and not f.error
    assert (f.revenue_ltm, f.net_income_ltm, f.cash) == (120000_00, 100000_00, 150000_00)
    assert f.debt == 30000_00 and f.equity == f.total_assets - f.total_liabilities == 120000_00
    h = pfm.Holding("Northwind OpCo", "Operating company", 80, company_path=str(tmp_path / "OpCo.amdb"))
    pf.save_holding(h)
    pf.add_flow(h.id, pfm.Flow("2025-12-31", "contribution", 50000_00))
    p = pfm.performance(pf, h, date(2026, 6, 30))
    assert p.value_method == "Book value (from books)" and p.nav == 96000_00   # 80% of book equity
    s = pfm.summary(pf, date(2026, 6, 30))
    assert s.consolidated.revenue_ltm == 96000_00 and s.by_type == {"Operating company": 96000_00}
    assert s.nav == 96000_00 and s.invested == 50000_00
    path = pfm.write_xlsx(pf, tmp_path / "pf.xlsx", date(2026, 6, 30))
    from openpyxl import load_workbook

    wb = load_workbook(path)
    assert wb.sheetnames == ["Holdings", "Capital flows"]
    assert wb["Holdings"]["A5"].value == "Northwind OpCo"


def test_exited_holding_has_no_nav(pf):
    h = pfm.Holding("Old Co", status="exited")
    pf.save_holding(h)
    pf.add_flow(h.id, pfm.Flow("2020-01-01", "acquisition", 100_00))
    pf.add_flow(h.id, pfm.Flow("2024-01-01", "proceeds", 300_00))
    pf.add_valuation(h.id, pfm.Valuation("2023-01-01", 999_00))
    p = pfm.performance(pf, h, date(2026, 1, 1))
    assert p.nav == 0 and abs(p.moic - 3.0) < 1e-9 and p.irr > 0.3


def test_capital_flows_come_from_the_holding_company_books(pf, tmp_path):
    hold = Books.open(tmp_path / "HoldCo.amdb")
    hold.set_profile(CompanyProfile("Morgan Holdings Inc"))
    hold.close()
    pf.holdco_path = str(tmp_path / "HoldCo.amdb")
    inv = pfm.create_investment_account(pf.holdco_path, "Northwind OpCo")
    hold = Books.open(tmp_path / "HoldCo.amdb")
    assert hold.account(inv).name == "Investment in Northwind OpCo"
    div = hold.add_account("4810", "Dividend Income", "income", "other").id
    bank = hold.account_by_code("1000").id
    hold.set_opening_balance(inv, "2024-12-31", 250000_00)        # bought before the books started
    hold.import_transactions(bank, [ParsedTransaction(date(2025, 3, 1), "WIRE TO NORTHWIND", -50000_00),
                                    ParsedTransaction(date(2025, 9, 1), "NORTHWIND DIVIDEND", 20000_00),
                                    ParsedTransaction(date(2025, 12, 1), "NORTHWIND REPAYS", 10000_00)])
    tx = {t.description: t for t in hold.transactions()}
    hold.set_category(tx["WIRE TO NORTHWIND"].id, inv, source="manual")
    hold.set_category(tx["NORTHWIND DIVIDEND"].id, div, source="manual")
    hold.set_category(tx["NORTHWIND REPAYS"].id, inv, source="manual")
    hold.close()
    accts = {label: i for i, label, _t in pfm.holdco_accounts(pf.holdco_path)}
    assert "4810 · Dividend Income" in accts and not any("Business Checking" in k for k in accts)
    h = pfm.Holding("Northwind OpCo", invest_accounts=[inv], income_accounts=[div])
    pf.save_holding(h)
    assert pf.holdings()[0].invest_accounts == [inv]
    p = pfm.performance(pf, pf.holdings()[0], date(2026, 1, 1))
    assert [(f.kind, f.amount_cents) for f in p.flows] == [
        ("acquisition", 250000_00), ("contribution", 50000_00), ("distribution", 20000_00),
        ("return_of_capital", 10000_00)]
    assert all(f.from_books for f in p.flows)
    assert p.invested == 300000_00 and p.returned == 30000_00


def test_holding_companies_and_asset_managers(pf, tmp_path):
    from am_accounting import receivables as rx
    from am_accounting.companies import CompanyRegistry

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    h1 = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    h2 = str(reg.create(CompanyProfile("Shah Family Holdco", role="holding")))
    am = str(reg.create(CompanyProfile("Northstar Capital", role="asset_manager")))
    op = str(reg.create(CompanyProfile("Northwind OpCo Inc")))
    pf.holdco_path = h1
    a = pfm.Holding("Northwind OpCo", ownership_pct=60, company_path=op, manager_path=am, fee_pct=2.0,
                    fee_fixed_cents=500_00)
    b = pfm.Holding("Lakeshore Apartments LP", "Real estate", 100, owner_path=h2, manager_path=am, fee_pct=1.0)
    c = pfm.Holding("Side Project Co", owner_path=h2)
    for h in (a, b, c):
        pf.save_holding(h)
    pf.add_valuation(a.id, pfm.Valuation("2026-01-01", 1_000_000_00))
    pf.add_valuation(b.id, pfm.Valuation("2026-01-01", 3_650_000_00))
    assert [h.name for h in pfm.select(pf, owner=h1)] == ["Northwind OpCo"]       # default holding company
    assert {h.name for h in pfm.select(pf, owner=h2)} == {"Lakeshore Apartments LP", "Side Project Co"}
    assert {h.name for h in pfm.select(pf, manager=am)} == {"Northwind OpCo", "Lakeshore Apartments LP"}
    s = pfm.summary(pf, date(2026, 6, 30), owner=h2)
    assert s.nav == 3_650_000_00
    managed = {m.company: m for m in pfm.managed_companies(pf, am, date(2026, 1, 1), date(2026, 6, 30))}
    assert managed["Northwind OpCo Inc"].annual_fee == 20000_00 + 6000_00   # 2% of $1M + $500 × 12
    assert managed["Lakeshore Apartments LP"].annual_fee == 36500_00
    made = pfm.bill_management_fees(pf, am, date(2026, 1, 1), date(2026, 3, 31))
    amounts = {name: amt for name, amt, _ in made}
    assert amounts["Lakeshore Apartments LP"] == round(3_650_000_00 * 0.01 * 90 / 365)
    assert amounts["Northwind OpCo Inc"] == round(1_000_000_00 * 0.02 * 90 / 365) + 3 * 500_00
    assert pfm.bill_management_fees(pf, am, date(2026, 1, 1), date(2026, 3, 31)) == []   # not billed twice
    books = Books.open(am, create=False)
    inv = rx.documents(books, "invoice")
    assert {d.party for d in inv} == {"Northwind OpCo Inc", "Lakeshore Apartments LP"}
    assert all(books.account(d.category_id).name == "Management Fee Income" for d in inv)
    books.close()
    billed = {m.company: m.billed for m in pfm.managed_companies(pf, am, date(2026, 1, 1), date(2026, 6, 30))}
    assert billed["Northwind OpCo Inc"] == amounts["Northwind OpCo Inc"]


def test_company_value_debt_nav_and_operating_figures(pf, tmp_path):
    b = Books.open(tmp_path / "Re.amdb")
    bank = b.account_by_code("1000").id
    b.set_opening_balance(bank, "2025-06-30", 100000_00)
    mort = b.add_account("2610", "Mortgage Payable", "liability", "long_term").id
    b.set_opening_balance(mort, "2025-06-30", 1_800_000_00)
    dep = b.add_account("6890", "Depreciation", "expense", "operating").id
    bld = b.add_account("1610", "Building Improvements", "asset", "fixed_asset").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 1, 5), "RENTS", 420000_00),
                                 ParsedTransaction(date(2026, 2, 5), "PROPERTY COSTS", -150000_00),
                                 ParsedTransaction(date(2026, 3, 5), "MORTGAGE INTEREST", -40000_00)])
    t = {x.description: x for x in b.transactions()}
    b.set_category(t["RENTS"].id, b.account_by_code("4100").id, source="manual")
    b.set_category(t["PROPERTY COSTS"].id, b.account_by_code("6400").id, source="manual")
    other = next(a for a in b.accounts() if a.type == "expense" and a.subtype == "other")
    b.set_category(t["MORTGAGE INTEREST"].id, other.id, source="manual")
    b.add_journal_entry("2026-03-31", "Depreciation", [(dep, 50000_00, 0), (bld, 0, 50000_00)])
    b.add_journal_entry("2026-04-30", "Roof replacement", [(bld, 60000_00, 0), (bank, 0, 60000_00)])
    b.close()
    f = pfm.company_financials(tmp_path / "Re.amdb", date(2026, 6, 30))
    assert f.revenue_ltm == 420000_00 and f.opex_ltm == 150000_00 and f.ebitda_ltm == 270000_00  # D&A, interest out
    assert f.nonop_ltm == 40000_00 and f.capex_ltm == 60000_00 and f.debt == 1_800_000_00
    assert f.cash_flow_ltm == 420000_00 - 150000_00 - 40000_00 - 60000_00
    h = pfm.Holding("Lakeshore", "Real estate", 50, company_path=str(tmp_path / "Re.amdb"))
    pf.save_holding(h)
    pf.add_flow(h.id, pfm.Flow("2025-06-30", "acquisition", 600000_00))
    pf.add_valuation(h.id, pfm.Valuation("2026-06-30", 1_450_000_00, "Appraisal", company_value_cents=3_250_000_00,
                                         debt_cents=1_800_000_00))
    p = pfm.performance(pf, h, date(2026, 6, 30))
    assert (p.company_value, p.debt, p.value_100, p.nav) == (3_250_000_00, 1_800_000_00, 1_450_000_00, 725000_00)
    assert abs(p.ltv - 1_800_000_00 / 3_250_000_00) < 1e-9
    assert p.capex_req == 60000_00 and p.net_cash_flow == 270000_00 - 40000_00 - 60000_00
    h.capex_req_cents = 100000_00   # a set annual requirement replaces last year's actual
    pf.save_holding(h)
    assert pfm.performance(pf, pf.holdings()[0], date(2026, 6, 30)).net_cash_flow == 270000_00 - 40000_00 - 100000_00
    s = pfm.summary(pf, date(2026, 6, 30))
    assert s.net_cash_flow_share == (270000_00 - 40000_00 - 100000_00) // 2
    assert (s.company_value, s.debt, s.value_share, s.debt_share) == (3_250_000_00, 1_800_000_00, 1_625_000_00,
                                                                      900000_00)
    assert s.consolidated.ebitda_ltm == 135000_00
    months = pfm.monthly_results(tmp_path / "Re.amdb", date(2026, 6, 30))
    assert len(months) == 12 and months[6] == ("Jan 26", 420000_00, 420000_00)


def test_waterfall_tiers():
    t = pfm.Terms(lp_pct=100, pref_pct=8, catchup_pct=100, carry_pct=20)
    flows = [pfm.Flow("2025-01-01", "contribution", 1_000_000_00)]
    # exactly one year later: capital 1M, pref 80K; distribute 2M total
    w = pfm.waterfall(flows, 2_000_000_00, date(2026, 1, 1), t)
    tiers = {x.name: x for x in w.tiers}
    assert tiers["Return of capital"].total == 1_000_000_00
    assert tiers["Preferred return"].total == 80000_00 and w.pref == 80000_00
    assert tiers["GP catch-up"].total == 20000_00 and tiers["GP catch-up"].gp == 20000_00   # 20% of 100K profit
    assert tiers["Carried interest"].total == 900000_00 and tiers["Carried interest"].gp == 180000_00
    assert w.gp_total == 200000_00 and w.lp_total == 1_800_000_00            # GP has 20% of the 1M profit
    assert w.lp_total + w.gp_total == w.distributable
    assert abs(w.lp_moic - 1.8) < 1e-9 and w.gp_promote == 200000_00
    # below the hurdle: no carry
    low = pfm.waterfall(flows, 1_050_000_00, date(2026, 1, 1), t)
    assert low.gp_total == 0 and low.lp_total == 1_050_000_00
    # LP / GP capital split 98 / 2: pro-rata tiers shared, promote to the GP
    t2 = pfm.Terms(lp_pct=98)
    w2 = pfm.waterfall(flows, 2_000_000_00, date(2026, 1, 1), t2)
    assert w2.lp_capital == 980000_00 and w2.gp_capital == 20000_00
    assert w2.gp_total == round(1_080_000_00 * 0.02) + 20000_00 + 180000_00 + round(720000_00 * 0.02)
    assert w2.lp_total + w2.gp_total == 2_000_000_00 and w2.lp_irr is not None


def test_holding_company_waterfall(pf, tmp_path):
    from am_accounting.companies import CompanyRegistry

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    hold = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    mgr = str(reg.create(CompanyProfile("Northstar Capital", role="asset_manager")))
    a = pfm.Holding("A Co", owner_path=hold, manager_path=mgr)
    b = pfm.Holding("B Co", owner_path=hold)
    pf.save_holding(a)
    pf.save_holding(b)
    for h, v in ((a, 900000_00), (b, 600000_00)):
        pf.add_flow(h.id, pfm.Flow("2024-01-01", "acquisition", 500000_00))
        pf.add_valuation(h.id, pfm.Valuation("2026-01-01", v))
    pfm.set_terms(pf, hold, pfm.Terms(lp_pct=95, carry_pct=20))
    w = pfm.holding_waterfall(pf, hold, date(2026, 1, 1))
    assert w.capital == 1_000_000_00 and w.distributable == 1_500_000_00
    assert w.terms.gp_name == "Northstar Capital" and w.terms.lp_pct == 95
    one = pfm.holding_waterfall(pf, hold, date(2026, 1, 1), holding_id=b.id)
    assert one.capital == 500000_00 and one.distributable == 600000_00
    with pytest.raises(ValueError, match="catch-up"):
        pfm.set_terms(pf, hold, pfm.Terms(catchup_pct=10, carry_pct=20))


def test_overall_waterfall_adds_up_holding_companies(pf, tmp_path):
    from am_accounting.companies import CompanyRegistry

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    h1 = str(reg.create(CompanyProfile("Morgan Holdings Inc", role="holding")))
    h2 = str(reg.create(CompanyProfile("Shah Holdco", role="holding")))
    for owner, name, nav in ((h1, "A Co", 2_000_000_00), (h2, "B Co", 1_200_000_00)):
        h = pfm.Holding(name, owner_path=owner)
        pf.save_holding(h)
        pf.add_flow(h.id, pfm.Flow("2025-01-01", "acquisition", 1_000_000_00))
        pf.add_valuation(h.id, pfm.Valuation("2026-01-01", nav))
    pfm.set_terms(pf, h2, pfm.Terms(lp_pct=100, carry_pct=10, catchup_pct=0))
    total, parts = pfm.overall_waterfall(pf, [h1, h2], date(2026, 1, 1))
    assert [o for o, _w in parts] == [h1, h2]
    assert total.capital == 2_000_000_00 and total.distributable == 3_200_000_00
    assert total.lp_total == sum(w.lp_total for _o, w in parts) and total.gp_total == sum(w.gp_total for _o, w in parts)
    assert total.lp_total + total.gp_total == total.distributable
    shah = parts[1][1]
    assert next(t for t in shah.tiers if t.name == "Carried interest").gp == round((1_200_000_00 - 1_080_000_00) * 0.1)


def test_projections(pf):
    h = pfm.Holding("Lakeshore", "Real estate", 50)
    pf.save_holding(h)
    f = pfm.Financials("2026-10-31", revenue_ltm=400000_00, opex_ltm=180000_00, nonop_ltm=50000_00, capex_ltm=30000_00)
    rows = pfm.project_from_actuals(f, 2027, 3, revenue_growth=5, opex_growth=3)
    assert [r.year for r in rows] == [2027, 2028, 2029]
    assert rows[0].revenue == 420000_00 and rows[1].revenue == 441000_00 and rows[0].opex == 185400_00
    assert rows[0].capex == 30000_00 and rows[0].ebitda == 420000_00 - 185400_00
    rows[1].capex = 250000_00   # a roof in 2028
    pfm.save_projections(pf, h.id, rows)
    back = pfm.projections(pf, h.id)
    assert back[1].capex == 250000_00 and back[1].net_cash_flow == back[1].ebitda - 50000_00 - 250000_00
    total = pfm.portfolio_projections(pf)
    assert total[2028].capex == 125000_00 and total[2027].revenue == 210000_00   # our 50%
    # next year's projected capex becomes the capex requirement (no requirement set on the holding)
    p = pfm.performance(pf, pf.holdings()[0], date(2027, 6, 30), with_financials=False)
    assert p.capex_req == 250000_00
    with pytest.raises(ValueError, match="once"):
        pfm.save_projections(pf, h.id, [pfm.Projection(2027), pfm.Projection(2027)])


def test_monthly_figures(tmp_path):
    b = Books.open(tmp_path / "Op.amdb")
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 3, 5), "SALES", 10000_00),
                                 ParsedTransaction(date(2026, 3, 9), "RENT", -4000_00),
                                 ParsedTransaction(date(2026, 5, 5), "SALES", 12000_00)])
    for t in b.transactions():
        b.set_category(t.id, b.account_by_code("4100" if t.amount_cents > 0 else "6400").id, source="manual")
    b.close()
    ms = pfm.monthly_figures(tmp_path / "Op.amdb", date(2026, 6, 30), 4)
    assert [m["label"] for m in ms] == ["Mar 26", "Apr 26", "May 26", "Jun 26"]
    assert (ms[0]["revenue"], ms[0]["opex"], ms[0]["ebitda"], ms[0]["net_cash_flow"]) == (10000_00, 4000_00, 6000_00,
                                                                                          6000_00)
    assert ms[2]["revenue"] == 12000_00 and ms[1]["revenue"] == 0


def test_sync_operating_companies(tmp_path):
    from am_accounting import company_roles
    from am_accounting.books import Books

    paths = {}
    for name, role in [("Opco A", "operating"), ("Opco B", "operating"), ("HoldCo", "holding"),
                       ("Manager", "asset_manager")]:
        b = Books.open(tmp_path / f"{name}.amdb", create=True)
        company_roles.apply(b, role)
        b.close()
        paths[name] = str(tmp_path / f"{name}.amdb")
    companies = [(n, p, company_roles.read_role(p)) for n, p in paths.items()]
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    pf.save_holding(pfm.Holding("Opco B", ownership_pct=60))       # typed in by hand, not linked yet

    added = pfm.sync_operating_companies(pf, companies)
    assert sorted(added) == ["Opco A", "Opco B"]
    hs = {h.name: h for h in pf.holdings()}
    assert set(hs) == {"Opco A", "Opco B"}                         # holding / manager companies not added
    assert hs["Opco B"].ownership_pct == 60 and hs["Opco B"].company_path == paths["Opco B"]
    assert pfm.sync_operating_companies(pf, companies) == []       # nothing twice

    pf.delete_holding(hs["Opco A"].id)                             # removed by hand stays removed
    assert pfm.sync_operating_companies(pf, companies) == []
    assert {h.name for h in pf.holdings()} == {"Opco B"}
    h = pf.holding(hs["Opco B"].id)
    h.company_path = ""                                            # unlinked by hand: not re-added either
    pf.save_holding(h)
    assert pfm.sync_operating_companies(pf, companies) == []

    pf.auto_sync = False
    pf._set_skipped(set())
    assert pfm.sync_operating_companies(pf, companies) == []       # switched off
    pf.auto_sync = True
    assert sorted(pfm.sync_operating_companies(pf, companies)) == ["Opco A", "Opco B"]
    pf.close()


def test_loans_and_financing(tmp_path):
    books = Books.open(tmp_path / "op.amdb", create=True)
    mort = books.add_account("2610", "Mortgage Payable", "liability", "long_term").id
    books.set_opening_balance(mort, "2025-01-01", 1_800_000_00)
    loc = books.add_account("2620", "Line of Credit", "liability", "long_term").id
    books.set_opening_balance(loc, "2025-01-01", 50_000_00)
    books.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Lakeshore", ownership_pct=50, company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    rows = pfm.loans(pf, h, date(2026, 6, 30))
    assert {(ln.lender, ln.balance, ln.terms_missing) for ln in rows} == {
        ("Mortgage Payable", 1_800_000_00, True), ("Line of Credit", 50_000_00, True)}   # found in the books
    pfm.save_loan(pf, h.id, pfm.Loan("TD Bank", "Mortgage", 2_000_000_00, rate_pct=5.0, maturity="2029-04-30",
                                     account_id=mort, payment_cents=11_000_00))
    pfm.save_loan(pf, h.id, pfm.Loan("Seller", "Vendor take-back", 100_000_00, 80_000_00, rate_pct=7.0,
                                     maturity="2027-01-31"))
    rows = pfm.loans(pf, h, date(2026, 6, 30))
    td = next(ln for ln in rows if ln.lender == "TD Bank")
    assert td.balance == 1_800_000_00 and td.account_name == "Mortgage Payable" and td.annual_interest == 90_000_00
    assert round(td.years_left(date(2026, 6, 30)), 1) == 2.8
    assert [ln.lender for ln in rows if ln.terms_missing] == ["Line of Credit"]   # mortgage now has its terms
    f = pfm.financing_totals(rows)
    assert f.balance == 1_930_000_00 and f.principal == 2_100_000_00 and f.next_maturity == "2027-01-31"
    assert f.annual_interest == 90_000_00 + 5_600_00
    assert abs(f.rate - (5 * 1_800_000 + 7 * 80_000) / 1_880_000) < 1e-9   # weighted, line of credit has no rate
    assert pfm.financing_totals(rows, 0.5).balance == 965_000_00
    with pytest.raises(ValueError):
        pfm.save_loan(pf, h.id, pfm.Loan(""))
    pfm.delete_loan(pf, td.id)
    assert len(pfm.loans(pf, h, date(2026, 6, 30))) == 3
    pf.close()


def test_sources_and_uses(tmp_path):
    books = Books.open(tmp_path / "op.amdb", create=True)
    bank = books.account_by_code("1000").id
    bldg = books.add_account("1610", "Building Improvements", "asset", "fixed_asset").id
    books.import_transactions(bank, [ParsedTransaction(date(2026, 3, 1), "ROOF CO", -65_000_00)])
    books.set_category(books.transactions()[0].id, bldg, source="manual")
    books.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Lakeshore", ownership_pct=80, acquired="2025-01-01", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    pfm.save_loan(pf, h.id, pfm.Loan("TD Bank", "Mortgage", 2_000_000_00, rate_pct=5.0, maturity="2030-01-01"))
    pf.add_flow(h.id, pfm.Flow("2025-01-01", "acquisition", 800_000_00))
    pf.add_valuation(h.id, pfm.Valuation("2025-01-01", 900_000_00, "Purchase price", company_value_cents=2_900_000_00))
    p = pfm.performance(pf, h, date(2026, 6, 30))
    assert p.debt == 2_000_000_00                     # the loan (not in the books) counts as debt
    su = pfm.sources_uses(pf, p, date(2026, 6, 30))
    assert not su.entered
    assert su.sources == {"Debt financing": 2_000_000_00, "Equity – us": 800_000_00,
                          "Equity – other owners": 200_000_00, "Other sources": 0}
    assert su.uses["Purchase price"] == 2_900_000_00 and su.uses["CapEx / improvements"] == 65_000_00
    assert su.uses["Cash reserve / other"] == 35_000_00 and su.total_sources == su.total_uses == 3_000_000_00
    assert su.scaled(0.8).total_uses == 2_400_000_00
    pfm.save_sources_uses(pf, h.id, {"Debt financing": 100, "Purchase price": 100})
    su = pfm.sources_uses(pf, p)
    assert su.entered and su.total_sources == 100 and su.uses["CapEx / improvements"] == 0
    pfm.save_sources_uses(pf, h.id, None)
    assert not pfm.sources_uses(pf, p).entered
    with pytest.raises(ValueError):
        pfm.save_sources_uses(pf, h.id, {"Nonsense": 1})
    pf.close()


def test_loan_terms_and_amortization_schedule():
    ln = pfm.Loan("TD Bank", "Mortgage", 1_000_000_00, rate_pct=6.0, start_date="2026-01-01", term_months=60,
                  amort_months=300)
    assert ln.payment == 6_443_01 and ln.maturity_date == "2031-01-01" and ln.annual_debt_service == 77_316_12
    rows = pfm.amortization_schedule(ln)
    assert len(rows) == 61 and rows[0].interest == 5_000_00 and rows[0].principal == 1_443_01
    assert rows[-1].number == 0 and rows[-1].balance == 0                    # balloon at maturity
    assert sum(r.principal for r in rows) == 1_000_000_00
    assert abs(rows[-1].payment - ln.balloon()) < 100
    assert [y.number for y in pfm.schedule_by_year(rows)] == [2026, 2027, 2028, 2029, 2030, 2031]
    io = pfm.Loan("X", principal_cents=100_000_00, rate_pct=5, start_date="2026-01-01", term_months=24,
                  repayment="Interest only", payment_freq="Quarterly")
    assert io.payment == 1_250_00 and io.annual_debt_service == 5_000_00 and io.balloon() == 100_000_00
    rows = pfm.amortization_schedule(io)
    assert len(rows) == 9 and rows[0].date == "2026-04-01" and rows[-1].principal == 100_000_00
    full = pfm.Loan("F", principal_cents=120_000_00, rate_pct=0, start_date="2026-01-01", term_months=12)
    rows = pfm.amortization_schedule(full)
    assert full.payment == 10_000_00 and len(rows) == 12 and rows[-1].balance == 0 and not full.balloon()
    assert full.scheduled_balance(date(2026, 7, 15)) == 60_000_00


def test_cap_table_and_capital_stack(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Co", ownership_pct=75)
    pf.save_holding(h)
    pf.add_flow(h.id, pfm.Flow("2025-01-01", "acquisition", 750_000_00))
    pf.add_valuation(h.id, pfm.Valuation("2025-06-30", 2_000_000_00, company_value_cents=5_000_000_00,
                                         debt_cents=3_000_000_00))
    pfm.save_loan(pf, h.id, pfm.Loan("Seller", "Vendor take-back", 500_000_00, rate_pct=8))
    pfm.save_loan(pf, h.id, pfm.Loan("RBC", "Mortgage", 2_500_000_00, rate_pct=5))
    p = pfm.performance(pf, h, date(2026, 1, 1))
    cap = pfm.cap_table(pf, p, "Morgan Holdings Inc")
    assert [(x.name, x.company, round(x.pct, 2)) for x in cap] == [("Us", "Morgan Holdings Inc", 0.75),
                                                                  ("Other owners", "", 0.25)]
    assert cap[0].worked_out and cap[0].value == 1_500_000_00 and cap[1].amount_cents == 250_000_00
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Alex Morgan", "Morgan Holdings Inc", 900_000_00))
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Jane Shah", "", 300_000_00))
    cap = pfm.cap_table(pf, p)
    assert [(x.label, x.pct) for x in cap] == [("Alex Morgan (Morgan Holdings Inc)", 0.75), ("Jane Shah", 0.25)]
    with pytest.raises(ValueError):
        pfm.save_shareholder(pf, h.id, pfm.Shareholder("No amount"))
    stack = pfm.capital_stack(pf, p)
    assert [s.name for s in stack] == ["RBC – Mortgage", "Seller – Vendor take-back", "Equity"]   # senior first
    assert stack[0].pct == 0.5 and stack[1].cumulative == 0.6 and stack[2].amount == 2_000_000_00
    pf.close()


def test_holding_companies_and_asset_managers_left_out_of_the_portfolio(tmp_path):
    from am_accounting import company_roles

    paths = {}
    for name, role in [("Opco", "operating"), ("HoldCo", "holding"), ("Manager", "asset_manager")]:
        b = Books.open(tmp_path / f"{name}.amdb", create=True)
        company_roles.apply(b, role)
        b.close()
        paths[name] = str(tmp_path / f"{name}.amdb")
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    for name, path in paths.items():   # e.g. pulled in before the company's type was set
        pf.save_holding(pfm.Holding(name, company_path=path))
    pf.save_holding(pfm.Holding("Unlinked JV"))
    assert {h.name for h in pfm.select(pf)} == {"Opco", "Unlinked JV"}
    s = pfm.summary(pf, date(2026, 1, 1))
    assert {p.holding.name for p in s.performances} == {"Opco", "Unlinked JV"}
    pf.close()




def test_select_groups(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    pf.save_holding(pfm.Holding("A", company_path="", owner_path="/h1", manager_path="/m1"))
    pf.save_holding(pfm.Holding("B", owner_path="", manager_path=""))
    pf.save_holding(pfm.Holding("C", owner_path="/h2"))
    assert {h.name for h in pfm.select(pf, any_owner=True)} == {"A", "C"}
    assert {h.name for h in pfm.select(pf, any_manager=True)} == {"A"}
    assert {h.name for h in pfm.select(pf, owner="/h2")} == {"C"}
    pf.close()


def test_valuation_without_debt_takes_the_financing_loans(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("1001124102 Ontario Inc.")
    pf.save_holding(h)
    pfm.save_loan(pf, h.id, pfm.Loan("Equitable Bank", "Mortgage", 2_350_000_00, rate_pct=6.5,
                                     start_date="2026-04-01", maturity="2027-04-01"))
    assert pfm.loan_debt(pf, h, date(2026, 11, 6)) == 2_350_000_00
    assert pfm.loan_debt(pf, h, date(2026, 1, 1)) is None or pfm.loan_debt(pf, h, date(2026, 1, 1)) == 0
    # saved with the debt box left at $0
    pf.add_valuation(h.id, pfm.Valuation("2026-11-06", 3_250_000_00, "Appraisal", "",
                                         company_value_cents=3_250_000_00, debt_cents=0))
    p = pfm.performance(pf, h, date(2026, 11, 6))
    assert p.debt == 2_350_000_00 and p.company_value == 3_250_000_00 and p.value_100 == 900_000_00
    assert abs(p.ltv - 2_350_000_00 / 3_250_000_00) < 1e-9
    cv, debt, eq, from_loans = pfm.valuation_figures(pf, h, pf.valuations(h.id)[-1])
    assert (cv, debt, eq, from_loans) == (3_250_000_00, 2_350_000_00, 900_000_00, True)
    # debt entered on the valuation is kept
    pf.add_valuation(h.id, pfm.Valuation("2026-11-07", 1_250_000_00, "Appraisal", "",
                                         company_value_cents=3_250_000_00, debt_cents=2_000_000_00))
    p = pfm.performance(pf, h, date(2026, 11, 7))
    assert p.debt == 2_000_000_00 and p.value_100 == 1_250_000_00
