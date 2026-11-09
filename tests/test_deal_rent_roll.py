import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import deals as dl
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


ROLL = [{"unit": "101", "type": "1 Bedroom", "tenant": "A. Chen", "status": "Occupied", "rent": 1_500_00,
         "market": 1_900_00},
        {"unit": "102", "type": "1 Bedroom", "tenant": "", "status": "Vacant", "rent": 0, "market": 1_900_00},
        {"unit": "201", "type": "2 Bedroom", "tenant": "R. Patel", "status": "Occupied", "rent": 2_000_00,
         "market": 0}]
EXP = [{"item": "Property tax", "amount": 12_000_00}, {"item": "Insurance", "amount": 6_000_00}]


def test_rent_roll_and_expenses_drive_the_numbers():
    d = dl.Deal("Elm", price_cents=1_000_000_00, units=10)
    u = dl.Underwriting(rent_inplace=999_00, vacancy_pct=0, rent_roll=ROLL, expenses=EXP, reserve_per_unit=100_00)
    assert (u.roll_units, u.roll_vacant, u.roll_occupied_rent) == (3, 1, 3_500_00)
    assert u.roll_inplace == 1_500_00 + 1_900_00 + 2_000_00                # the vacant unit at market
    assert u.roll_market == 1_900_00 * 2 + 2_000_00                         # no market: its rent
    a = dl.analyze(d, u)
    assert a.gpr == 5_400_00 * 12 and a.opex == 18_000_00 and a.reserve == 300_00     # 3 units, not 10
    assert dl.analyze(d, u, market=True).gpr == 5_800_00 * 12
    back = dl.Underwriting.from_json(u.to_json())
    assert back.rent_roll == ROLL and back.expenses == EXP
    old = dl.Underwriting.from_json('{"rent_inplace": 100000}')               # saved before: still reads
    assert old.rent_roll == [] and old.rent_inplace == 1_000_00


def test_rent_roll_tab_and_closing(app, tmp_path):
    from am_accounting import closing as cl
    from am_accounting import op_costs as oc
    from am_accounting import operations as op
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui import deal_roll_tab as drt
    from am_accounting.gui import deals_tab as dt

    assert drt.expand_units("101-103, 201,PH1") == ["101", "102", "103", "201", "PH1"]
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("45 Elm St", stage="Under Contract", price_cents=1_000_000_00, equity_cents=300_000_00,
                closing_date="2026-01-15")
    dl.save(pf, d)
    dlg = dt.DealDialog(pf, dl.deal(pf, d.id))
    tab = dlg.roll_tab
    assert dlg.tabs.tabText(dlg.tabs.indexOf(tab)) == "Rent Roll && Expenses"
    for x in ROLL:
        tab._roll_row(x)
    tab._changed()
    assert dlg.units.value() == 3 and not dlg.u_rent.isEnabled()              # the rent roll sets the units
    tab.add_unit()
    assert tab.roll.item(3, 0).text() == "202"                               # the next number
    tab.roll.selectRow(3)
    tab.remove_unit()
    tab.usual()
    assert tab.exp.rowCount() == len(dl.EXPENSE_STARTERS)
    tab.exp.item(0, 1).setText("12,000")                                     # property tax
    tab.exp.item(1, 1).setText("6,000")
    assert tab.expenses()[:2] == EXP and not dlg.u_opex.isEnabled()
    assert "$18,000.00" in tab.exp_total.text() and tab.exp.item(0, 2).text() == "$4,000.00"
    page = dlg.summary.toPlainText()
    assert "· Property tax" in page and "Operating expenses" in page
    dlg.save()
    u = dl.underwriting(dl.deal(pf, d.id))
    assert len(u.rent_roll) == 3 and u.expenses_total == 18_000_00
    # closing: the property's rent roll and operating costs
    reg = CompanyRegistry(tmp_path / "c.json", tmp_path / "Companies")
    s = cl.default_setup(dl.deal(pf, d.id), pf)
    s.company_name = "1001234567 Ontario Inc."
    s.loans[0].lender = "First National"
    s.equity[0].owner = "Pat Lee"
    hid = cl.close_deal(pf, d.id, s, lambda name, **kw: str(reg.create(CompanyProfile(name, name, ""))))
    units = op.units(pf, hid)
    assert [(x.unit, x.tenant, x.status, x.rent_cents) for x in units] == [
        ("101", "A. Chen", "Old Lease", 1_500_00), ("102", "", "Vacant", 0), ("201", "R. Patel", "Old Lease", 2_000_00)]
    costs = {c.category: c.budget_cents for c in oc.lines(pf, hid)}
    assert costs["Property tax"] == 12_000_00 and costs["Insurance"] == 6_000_00
    pf.close()


def test_import_rent_roll(app, tmp_path):
    import csv

    from am_accounting.gui import deal_roll_tab as drt

    p = tmp_path / "roll.csv"
    with open(p, "w", newline="") as f:
        csv.writer(f).writerows([["Unit", "Tenant", "Unit Type", "Status", "Rent", "Market Rent"],
                                 ["1", "A. Tenant", "1 Bedroom", "Old Lease", "1,200.00", "1,800"],
                                 ["2", "", "1 Bedroom", "Vacant", "0", "1,800"]])
    tab = drt.RollExpensesTab(dl.Underwriting())
    assert tab.import_roll(str(p)) == 2
    assert [(x["unit"], x["tenant"], x["status"], x["rent"]) for x in tab.rent_roll()] == [
        ("1", "A. Tenant", "Occupied", 1_200_00), ("2", "", "Vacant", 0)]
