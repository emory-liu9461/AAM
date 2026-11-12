"""Personal finances: income from your businesses and a monthly budget."""

from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import personal as pers
from am_accounting import personal_finance as pfi
from am_accounting import portfolio as pfm

YEAR = date.today().year - 1          # a whole year that has passed


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pf(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("45 Elm St", "Real estate", ownership_pct=50, acquired="2024-01-01")
    pf.save_holding(h)
    pf.add_flow(h.id, pfm.Flow(f"{YEAR}-01-15", "distribution", 10_000_00))
    pf.add_flow(h.id, pfm.Flow(f"{YEAR}-01-20", "distribution", 2_000_00))
    pf.add_flow(h.id, pfm.Flow(f"{YEAR}-02-15", "return_of_capital", 5_000_00))
    pf.add_flow(h.id, pfm.Flow(f"{YEAR - 1}-12-15", "distribution", 99_000_00))       # last year
    return pf, h


def test_business_income(tmp_path):
    pf, h = _pf(tmp_path)
    bi = pfi.business_income(pf, YEAR)
    row = bi.holdings[0]
    assert (row.distributions, row.capital, row.factor) == (12_000_00, 5_000_00, 1.0)   # you own the holding co.
    assert row.by_month == {1: 12_000_00}
    s = pers.load(pf)
    s.shares = {h.id: 25.0}                                     # you own half of the holding company
    pers.save(pf, s)
    assert pfi.business_income(pf, YEAR).distributions == 6_000_00
    pfi.save_received(pf, [pfi.Received(f"{YEAR}-03-01", "45 Elm St", "Management fee", 2_500_00),
                           pfi.Received(f"{YEAR - 1}-03-01", "45 Elm St", "Salary", 1_00)])
    bi = pfi.business_income(pf, YEAR)
    assert bi.other == 2_500_00 and bi.total == 8_500_00 and bi.capital == 2_500_00
    assert bi.by_month()[1] == 6_000_00 and bi.by_month()[3] == 2_500_00
    assert bi.by_company() == {"45 Elm St": 8_500_00}
    pf.close()


def test_budget(tmp_path):
    pf, h = _pf(tmp_path)
    lines = pfi.budget_lines(pf)
    assert pfi.BUSINESS in [ln.category for ln in lines] and "Groceries" in [ln.category for ln in lines]
    for ln in lines:
        ln.budget = {"Groceries": 800_00, "Salary / employment": 5_000_00, pfi.BUSINESS: 4_000_00}.get(ln.category, 0)
    pfi.save_budget_lines(pf, lines)
    pfi.set_actual(pf, YEAR, "Groceries", 1, 750_00)
    pfi.set_actual(pf, YEAR, "Groceries", 2, 900_00)
    pfi.set_actual(pf, YEAR, "Salary / employment", 1, 5_000_00)
    pfi.set_actual(pf, YEAR, "Groceries", 2, 0)                  # cleared
    b = pfi.budget(pf, YEAR)
    rows = {r.line.category: r for r in b.rows}
    assert rows["Groceries"].months == {1: 750_00} and rows["Groceries"].actual == 750_00
    assert rows[pfi.BUSINESS].auto and rows[pfi.BUSINESS].months == {1: 12_000_00}     # from the businesses
    assert b.total(pfi.INCOME, 1) == 17_000_00 and b.total(pfi.EXPENSE, 1) == 750_00
    assert b.net() == 16_250_00 and b.budget_total(pfi.INCOME) == 9_000_00
    assert pfi.budget(pf, YEAR - 1).rows[0].months == {}          # each year apart
    pf.close()


def test_page(app, tmp_path):
    from am_accounting.gui.personal_finance_page import PersonalFinancePage

    pf, h = _pf(tmp_path)
    page = PersonalFinancePage(pf)
    bt = page.budget
    bt.year.setValue(YEAR)
    page.income.year.setValue(YEAR)
    r = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == "Groceries")
    bt.table.item(r, 1).setText("800")                           # budget a month
    r = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == "Groceries")
    bt.table.item(r, 2).setText("$750")                          # January's actual
    r = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == "Groceries")
    assert bt.table.item(r, 1).text() == "$800.00" and bt.table.item(r, 2).text() == "$750.00"
    assert bt.table.item(r, 14).text() == "$750.00"
    biz = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == pfi.BUSINESS)
    assert bt.table.item(biz, 2).text() == "$12,000.00"
    bt.add_category("Pet care", pfi.EXPENSE)
    assert any(k == "line" and x.line.category == "Pet care" for k, x in bt._rows)
    bt.remove_category("Pet care")
    assert not any(k == "line" and x.line.category == "Pet care" for k, x in bt._rows)
    assert bt.cards["spent"].value.text() == "$750.00"
    inc = page.income
    page.tabs.setCurrentWidget(inc)
    assert inc.port.item(0, 2).text() == "$12,000.00" and inc.port.item(0, 3).text() == "$5,000.00"
    inc.add()
    assert inc.mine.item(0, 0).text() == f"{YEAR}-12-31"         # a past year: dated its last day
    inc.mine.item(0, 3).setText("2500")
    inc.mine.cellWidget(0, 2).setCurrentText("Management fee")
    saved = pfi.received(pf)
    assert len(saved) == 1 and saved[0].amount == 2_500_00 and saved[0].kind == "Management fee"
    assert inc.cards["total"].value.text() == "$14,500.00"
    page.tabs.setCurrentWidget(bt)                               # the budget picks it up
    biz = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == pfi.BUSINESS)
    assert bt.table.item(biz, 14).text() == "$14,500.00"
    page.resize(1300, 900)
    page.show()
    page.grab()
    pf.close()
