from datetime import date

import pytest

from am_accounting import capex as cx
from am_accounting import operations as op
from am_accounting import portfolio as pfm
from am_accounting.books import Books, ParsedTransaction

ROWS = [
    ["Maple Court Rent Roll"],
    [None, "Unit Number", "Tenant", "Unit Type", "Unit Size", "Status", "Base Rent", "Parking", "Market Rent",
     "Original Rent", "Move in Date", "Lease End Date", "Notes"],
    [None, "101", "A. Tenant", "Bachelor", 400, "Old Lease", 900, 0, 1500, 900, date(2016, 3, 1), "MTM", "quiet"],
    [None, "102", "B. Tenant", "2 Bedroom", 800, "Renovated", 2300, 50, 2300, 2300, date(2025, 7, 1),
     date(2026, 6, 30), ""],
    [None, "103", "", "2 Bedroom", 800, "Vacant", 0, 0, 2300, 0, None, "", "being renovated"],
    [None, "Total", None, None, None, None, 3200],
]


def test_parse_rent_roll_and_figures():
    rows = op.parse_rows(ROWS)
    assert [u.unit for u in rows] == ["101", "102", "103"]
    a, b, c = rows
    assert (a.rent_cents, a.market_cents, a.move_in, a.lease_end, a.mtm) == (900_00, 1500_00, "2016-03-01", "MTM", True)
    assert b.parking_cents == 50_00 and b.lease_end == "2026-06-30" and not c.occupied
    s = op.summary(rows, date(2026, 5, 1))
    assert (s.units, s.occupied, s.in_place, s.market, s.mtm, s.expiring_90) == (3, 2, 3250_00, 6100_00, 1, 1)
    assert s.loss_to_lease == 3800_00 - 3250_00 and round(s.occupancy, 3) == 0.667
    mix = {(m.unit_type, m.status): m for m in op.unit_mix(rows)}
    assert mix[("2 Bedroom", "Renovated")].avg_rent == 2350_00 and mix[("2 Bedroom", "Vacant")].occupied == 0
    with pytest.raises(ValueError):
        op.parse_rows([["no", "table", "here"]])


def test_scheduled_rent_goes_up_on_move_in_anniversaries():
    rows = op.parse_rows(ROWS)
    st = op.Settings(rent_increase_pct=2.5, vacancy_pct=0, as_of="2026-01-15")
    assert op.scheduled_rent(rows, date(2026, 2, 1), st) == 900_00 + 2350_00
    assert op.scheduled_rent(rows, date(2026, 3, 1), st) == 922_50 + 2350_00         # 101: March anniversary
    assert op.scheduled_rent(rows, date(2026, 7, 1), st) == 922_50 + 2357_50 + 50_00  # 102: July (parking flat)


def test_monthly_statement_from_books_and_rent_roll(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    rent = b.add_account("4010", "Rental Income", "income", "operating").id
    tax = b.add_account("6110", "Property Tax", "expense", "operating").id
    txs = [ParsedTransaction(date(2025, m, 3), "RENTS", 3200_00) for m in range(1, 13)]
    txs += [ParsedTransaction(date(2025, m, 20), "CITY TAX", -1000_00) for m in (3, 6, 9)]
    b.import_transactions(bank, txs)
    for t in b.transactions():
        b.set_category(t.id, rent if t.amount_cents > 0 else tax, source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    for u in op.parse_rows(ROWS):
        op.save_unit(pf, h.id, u)
    op.save_settings(pf, h.id, op.Settings(2.5, 10.0, "2025-12-31"))
    pfm.save_loan(pf, h.id, pfm.Loan("Bank", "Mortgage", 240_000_00, rate_pct=0, start_date="2025-01-01",
                                     term_months=240))
    perf = pfm.performance(pf, h, date(2025, 12, 31))
    st = op.monthly_statement(pf, perf, date(2025, 12, 31), back=12, forward=12)
    assert len(st.months) == 24 and st.projected == [False] * 12 + [True] * 12
    line = {x.label: x for x in st.lines}
    assert line["Rental revenue"].values[0] == 3200_00                        # actual, Jan 2025
    assert line["Rental revenue"].values[12] == round((900_00 + 2350_00) * 0.9)  # projected Jan 2026, less vacancy
    assert line["Property Tax"].values[2] == 1000_00 and line["Property Tax"].values[14] == 1000_00  # seasonal
    assert line["Property Tax"].values[13] == 0
    assert line["Debt service"].values[0] == 1000_00                           # 240,000 over 240 months
    assert line["Net operating income"].values[2] == 2200_00
    assert line["DSCR"].ratios[2] == 2.2
    assert op.t12(st, "Net operating income") == 12 * 3200_00 - 3000_00
    pf.close()


def test_capex_linked_to_the_books(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 2, 10), "ROOFER", -30_000_00),
                                 ParsedTransaction(date(2026, 4, 2), "ROOFER", -26_600_00)])
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    roof = cx.CapexLine("Roofing", "Hard Costs", 60_000_00)
    cx.save_line(pf, h.id, roof)
    cx.save_line(pf, h.id, cx.CapexLine("Permits", "Soft Costs", 10_000_00, manual_cents=2_500_00))
    acct = cx.create_account(pf, h, roof)                    # "CapEx – Roofing" in the books, linked
    b = Books.open(tmp_path / "op.amdb")
    assert b.account(acct).name == "CapEx – Roofing" and b.account(acct).subtype == "fixed_asset"
    for t in b.transactions():
        b.set_category(t.id, acct, source="manual")           # paying the roofer
    b.close()
    rows = cx.lines(pf, h, date(2026, 6, 30))
    r = {ln.item: ln for ln in rows}
    assert r["Roofing"].to_date == 56_600_00 and r["Roofing"].to_complete == 3_400_00
    assert r["Roofing"].monthly == {"2026-02": 30_000_00, "2026-04": 26_600_00}
    assert r["Permits"].to_date == 2_500_00 and r["Permits"].account_id is None
    tot = cx.totals(rows)
    assert (tot.budget, tot.to_date, tot.to_complete) == (70_000_00, 59_100_00, 10_900_00)
    assert tot.by_group["Hard Costs"] == [60_000_00, 56_600_00, 3_400_00]
    assert cx.monthly_outflows(rows, ["2026-02", "2026-03"]) == {"2026-02": 30_000_00, "2026-03": 0}
    assert cx.lines(pf, h, date(2026, 3, 1))[0].to_date == 30_000_00         # as of an earlier date
    assert cx.add_template(pf, h.id) > 10
    pf.close()


def test_cash_flow_reconciles_to_the_bank_and_projects_forward(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    rent = b.add_account("4010", "Rental Income", "income", "operating").id
    gas = b.add_account("6120", "Gas", "expense", "operating").id
    b.set_opening_balance(bank, "2025-12-31", 10_000_00)
    txs = [ParsedTransaction(date(2026, m, 3), "RENTS", 5_000_00) for m in range(1, 7)]
    txs += [ParsedTransaction(date(2026, m, 15), "ENBRIDGE", -1_000_00) for m in range(1, 7)]
    txs += [ParsedTransaction(date(2026, 4, 20), "CONTRACTOR", -4_000_00)]
    b.import_transactions(bank, txs)
    bldg = b.add_account("1660", "Building", "asset", "fixed_asset").id      # not on the capex schedule
    for t in b.transactions():
        b.set_category(t.id, rent if t.description == "RENTS" else gas if t.description == "ENBRIDGE" else bldg,
                       source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    cx.save_line(pf, h.id, cx.CapexLine("Roofing", "Hard Costs", 12_000_00))       # nothing spent yet
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Pat Lee", "", 50_000_00, share_class="Class A",
                                                   funded_cents=50_000_00, funded_date="2026-08-10"))
    perf = pfm.performance(pf, h, date(2026, 6, 30))
    st = op.cash_flow(pf, perf, date(2026, 6, 30), back=6, forward=6)
    line = {x.label: x for x in st.lines}
    assert line["Net operating income"].values[0] == 4_000_00
    # April: the contractor payment (to an account not on the schedule) shows as other, so the cash matches
    assert line["Ending cash"].values[5] == 10_000_00 + 6 * 4_000_00 - 4_000_00                # the bank balance
    assert line["Other / working capital (books)"].values[3] == -4_000_00
    assert line["CapEx"].values[6] == -2_000_00                                    # 12,000 to complete over 6 months
    assert line["Equity injections"].values[7] == 50_000_00                        # August
    assert line["Ending cash"].values[6] == line["Ending cash"].values[5] + line["Net cash flow"].values[6]
    pf.close()


def test_master_cash_flow_pro_forma_and_actual(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    rent = b.add_account("4010", "Rental Income", "income", "operating").id
    gas = b.add_account("6120", "Gas", "expense", "operating").id
    mort = b.add_account("2610", "Mortgage Payable", "liability", "long_term").id
    b.set_opening_balance(bank, "2025-12-31", 10_000_00)
    b.set_opening_balance(mort, "2025-12-31", 100_000_00)
    txs = [ParsedTransaction(date(2026, m, 3), "RENTS", 5_000_00) for m in range(1, 4)]
    txs += [ParsedTransaction(date(2026, m, 15), "ENBRIDGE", -1_000_00) for m in range(1, 4)]
    txs += [ParsedTransaction(date(2026, 2, 1), "MORTGAGE PMT", -2_000_00)]
    b.import_transactions(bank, txs)
    for t in b.transactions():
        cat = {"RENTS": rent, "ENBRIDGE": gas, "MORTGAGE PMT": mort}[t.description]
        b.set_category(t.id, cat, source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    perf = pfm.performance(pf, h, date(2026, 3, 31))
    f = op.master_cash_flow(pf, perf, date(2026, 3, 31), back=3, forward=2)
    assert [m.strftime("%b %y") for m in f.months] == ["Jan 26", "Feb 26", "Mar 26", "Apr 26", "May 26"]
    assert f.past == [True, True, True, False, False]
    r = {x.label: x for x in f.rows}
    assert r["Rental income"].actual[:3] == [5_000_00] * 3 and r["Rental income"].actual[3] is None
    assert r["Debt payment"].actual[1] == -2_000_00                 # principal paid to the mortgage
    assert r["Ending balance"].actual[2] == 10_000_00 + 3 * 4_000_00 - 2_000_00   # the bank balance
    assert r["Other / timing"].actual == [0, 0, 0, None, None]       # everything explained
    assert r["Starting cash"].pro_forma[3] == r["Ending balance"].actual[2]    # projection starts from the bank
    assert r["Ending balance"].pro_forma[4] == r["Ending balance"].pro_forma[3] + r["Net property cash flow"].pro_forma[4]
    pf.close()
