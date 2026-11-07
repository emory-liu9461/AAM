from datetime import date

import pytest

from am_accounting import exit_returns as er
from am_accounting import operations as op
from am_accounting import portfolio as pfm


def _setup(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", "Real estate", acquired="2026-01-01")
    pf.save_holding(h)
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Pat Lee", amount_cents=1_000_000_00, funded_cents=1_000_000_00,
                                                   funded_date="2026-01-01"))
    pfm.save_loan(pf, h.id, pfm.Loan("Bank", "Mortgage", 2_000_000_00, rate_pct=5.0, start_date="2026-01-01",
                                     term_months=120, repayment="Interest only"))
    years = [2027, 2028, 2029, 2030, 2031]
    lines = [pfm.ProjectionLine("revenue", "Rent", {y: 400_000_00 for y in years}),
             pfm.ProjectionLine("opex", "Opex", {y: 200_000_00 for y in years}),
             pfm.ProjectionLine("fin_out", "Interest – Bank", {y: 100_000_00 for y in years}),
             pfm.ProjectionLine("fin_out", "Distributions to owners", {y: 100_000_00 for y in years})]
    return pf, h, years, lines


def test_returns_to_the_exit(tmp_path):
    pf, h, years, lines = _setup(tmp_path)
    a = er.ExitAssumptions("2030-12-31", exit_cap_pct=5.0, selling_pct=2.0)
    r = er.projected_returns(pf, h, years, lines, {}, a, date(2026, 11, 7))
    # the exit year's NOI (2030) $200K at a 5% cap → $4.0M; less 2% and the $2.0M loan (interest only)
    assert r.noi_year == 2030 and r.exit_noi == 200_000_00 and r.exit_value == 4_000_000_00 and r.selling_costs == 80_000_00
    assert r.debt_repaid == 2_000_000_00 and r.net_proceeds == 1_920_000_00
    yearly = [v for d, v, w in r.flows if "cash to owners" in w]
    assert yearly == [100_000_00] * 4                      # NOI − interest, 2027–2030 (distributions are theirs)
    assert r.money_in == 1_000_000_00 and r.money_back == 400_000_00 + 1_920_000_00
    assert r.moic == pytest.approx(2.32) and 0.15 < r.irr < 0.25
    # entered exit value and debt
    a2 = er.ExitAssumptions("2029-06-30", exit_value=3_500_000_00, selling_pct=0, debt_at_exit=1_900_000_00)
    r2 = er.projected_returns(pf, h, years, lines, {}, a2, date(2026, 11, 7))
    assert r2.net_proceeds == 1_600_000_00 and r2.flows[-1][0] == date(2029, 6, 30)
    assert any("2029 cash to owners (to the exit)" == w for _d, _v, w in r2.flows)   # half of 2029
    # a multiple instead of a cap rate, on the exit year's NOI or the year after's
    lines[0].amounts[2031] = 460_000_00                    # NOI 2031 = $260K
    m = er.projected_returns(pf, h, years, lines, {}, er.ExitAssumptions("2030-12-31", method="multiple",
                                                                       multiple=12, selling_pct=0), date(2026, 11, 7))
    assert m.noi_year == 2030 and m.exit_value == 200_000_00 * 12
    fwd = er.projected_returns(pf, h, years, lines, {}, er.ExitAssumptions("2030-12-31", 5.0, selling_pct=0,
                                                                         basis="next_year"), date(2026, 11, 7))
    assert fwd.noi_year == 2031 and fwd.exit_value == 5_200_000_00
    pf.close()


def test_exit_date_is_the_dashboards(tmp_path):
    pf, h, _y, _l = _setup(tmp_path)
    er.save(pf, h, er.ExitAssumptions("2031-03-31", 4.5, 0, 1.5, None, "multiple", 11.5, "next_year"))
    assert op.dash_info(pf, h.id).exit_date == "2031-03-31"                   # the Dashboard's planned exit
    a = er.get(pf, h)
    assert (a.exit_date, a.exit_cap_pct, a.selling_pct, a.debt_at_exit) == ("2031-03-31", 4.5, 1.5, None)
    assert (a.method, a.multiple, a.basis) == ("multiple", 11.5, "next_year")
    with pytest.raises(ValueError):
        er.save(pf, h, er.ExitAssumptions("2031-03-31", 0, 0))
    pf.close()


def test_the_company_is_wound_up_at_the_exit(tmp_path):
    from am_accounting.books import Books

    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    ar = next(a.id for a in b.accounts() if a.type == "asset" and "receivable" in a.name.lower())
    ap = next(a.id for a in b.accounts() if a.type == "liability" and "payable" in a.name.lower())
    loc = b.add_account("2720", "Line of Credit – RBC", "liability", "long_term").id
    sh = b.add_account("2800", "Shareholder Loan – Pat Lee", "liability", "long_term").id
    for acct, cents in ((bank, 120_000_00), (ar, 15_000_00), (ap, 9_000_00), (loc, 40_000_00), (sh, 50_000_00)):
        b.set_opening_balance(acct, "2026-01-01", cents)
    b.close()
    pf, h, years, lines = _setup(tmp_path)
    h.company_path = str(tmp_path / "op.amdb")
    pf.save_holding(h)
    a = er.ExitAssumptions("2030-12-31", exit_cap_pct=5.0, selling_pct=2.0)
    r = er.projected_returns(pf, h, years, lines, {}, a, date(2026, 11, 7))
    assert (r.cash, r.receivables, r.payables) == (120_000_00, 15_000_00, 9_000_00)
    assert r.other_debt == 40_000_00                        # the line of credit; the shareholder loan is the owners'
    assert r.net_proceeds == 4_000_000_00 - 80_000_00 - 2_000_000_00 - 40_000_00 + 15_000_00 - 9_000_00 + 120_000_00
    assert r.flows[-1] == (date(2030, 12, 31), r.net_proceeds, "sale, debts and payables paid, cash paid out")
    pf.close()
