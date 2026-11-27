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
    assert [bt.table.horizontalHeaderItem(c).text() for c in range(6)] == [
        "Category", "Budget / month", "Budget to date", "Actual to date", "Variance", "Jan"]   # actual by the budget
    bt.table.item(r, bt.JAN).setText("$750")                     # January's actual
    r = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == "Groceries")
    assert bt.table.item(r, 1).text() == "$800.00" and bt.table.item(r, bt.JAN).text() == "$750.00"
    assert bt.table.item(r, 3).text() == "$750.00"                # actual to date, beside the budget
    assert bt.table.item(r, 2).text() == "$9,600.00"              # a past year: 12 × $800
    assert bt.table.item(r, 4).text() == "$8,850.00"              # under budget by
    biz = next(i for i, (k, x) in enumerate(bt._rows) if k == "line" and x.line.category == pfi.BUSINESS)
    assert bt.table.item(biz, bt.JAN).text() == "$12,000.00"
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
    assert bt.table.item(biz, 3).text() == "$14,500.00"
    page.resize(1300, 900)
    page.show()
    page.grab()
    pf.close()


def test_budget_table_and_chart_not_squeezed(app, tmp_path):
    from am_accounting.gui.personal_finance_page import BudgetTab

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    tab = BudgetTab(pf)
    tab.resize(1000, 500)                            # a small window
    tab.show()
    app.processEvents()
    t = tab.table
    assert t.height() >= sum(t.rowHeight(r) for r in range(t.rowCount()))   # every line, the page scrolls
    assert tab.chart.height() >= 280 and tab.scroll.verticalScrollBar().maximum() > 0
    pf.close()


def test_import_budget_from_a_previous_year(app, tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    lines = pfi.budget_lines(pf, 2025)
    for ln in lines:
        if ln.category == "Groceries":
            ln.budget = 800_00
    lines.append(pfi.Line(pfi.EXPENSE, "Boat"))
    pfi.save_budget_lines(pf, lines, 2025)
    for m in range(1, 13):
        pfi.set_actual(pf, 2025, "Groceries", m, 900_00)
    assert all(ln.budget == 0 for ln in pfi.budget_lines(pf, 2026))        # each year its own budget
    got = {ln.category: ln.budget for ln in pfi.import_budget(pf, 2025, 2026)}
    assert got["Groceries"] == 800_00 and "Boat" in got
    assert {ln.category: ln.budget for ln in pfi.budget_lines(pf, 2026)}["Groceries"] == 800_00
    pfi.import_budget(pf, 2025, 2027, "actual")                            # what was actually spent a month
    assert {ln.category: ln.budget for ln in pfi.budget_lines(pf, 2027)}["Groceries"] == 900_00
    assert {ln.category: ln.budget for ln in pfi.budget_lines(pf, 2025)}["Groceries"] == 800_00   # untouched

    from am_accounting.gui.personal_finance_page import BudgetTab

    tab = BudgetTab(pf)
    tab.year.setValue(2028)

    def pick(dlg):
        dlg.from_year.setValue(2025)
        dlg.use_actual.setChecked(True)
        return True

    assert tab.import_year(run=pick)
    assert {ln.category: ln.budget for ln in pfi.budget_lines(pf, 2028)}["Groceries"] == 900_00
    pf.close()


def test_budget_category_column_stays_put(app, tmp_path):
    from am_accounting.gui.personal_finance_page import BudgetTab

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    tab = BudgetTab(pf)
    tab.resize(700, 600)
    tab.show()
    app.processEvents()
    t, f = tab.table, tab.frozen
    assert f.isVisible() and f.width() == t.columnWidth(0)
    assert all(f.isColumnHidden(c) for c in range(1, t.columnCount())) and not f.isColumnHidden(0)
    t.horizontalScrollBar().setValue(t.horizontalScrollBar().maximum())        # scrolled all the way right
    app.processEvents()
    assert f.x() <= t.frameWidth() + 1 and f.model().index(2, 0).data() == t.item(2, 0).text()
    tab.year.setValue(2025)                                                     # refilled: still only Category
    app.processEvents()
    assert all(f.isColumnHidden(c) for c in range(1, t.columnCount()))
    pf.close()


def test_budget_view_toggle(app, tmp_path):
    from PySide6.QtCore import Qt

    from am_accounting.gui.common import NEGATIVE, POSITIVE
    from am_accounting.gui.personal_finance_page import BudgetTab

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    lines = pfi.budget_lines(pf, 2025)
    for ln in lines:
        if ln.category == "Groceries":
            ln.budget = 800_00
    pfi.save_budget_lines(pf, lines, 2025)
    pfi.set_actual(pf, 2025, "Groceries", 1, 900_00)
    pfi.set_actual(pf, 2025, "Groceries", 2, 700_00)
    tab = BudgetTab(pf)
    tab.year.setValue(2025)
    t = tab.table
    row = next(i for i, (k, x) in enumerate(tab._rows) if k == "line" and x.line.category == "Groceries")
    shown = lambda: [t.horizontalHeaderItem(c).text() for c in range(tab.JAN) if not t.isColumnHidden(c)]  # noqa: E731
    assert tab.mode == "vs" and shown() == ["Category", "Budget / month", "Budget to date", "Actual to date",
                                            "Variance"]
    jan, feb = t.item(row, tab.JAN), t.item(row, tab.JAN + 1)
    assert jan.text() == "$900.00" and jan.foreground().color() == NEGATIVE   # over budget
    assert feb.foreground().color() == POSITIVE                                # under
    tab.view_btns["budget"].click()
    assert shown() == ["Category", "Budget / month"] and t.item(row, tab.JAN + 5).text() == "$800.00"
    tab.view_btns["actual"].click()
    assert shown() == ["Category", "Actual to date"] and t.item(row, 3).text() == "$1,600.00"
    assert t.item(row, tab.JAN).text() == "$900.00" and t.item(row, tab.JAN).flags() & Qt.ItemFlag.ItemIsEditable
    pf.close()
