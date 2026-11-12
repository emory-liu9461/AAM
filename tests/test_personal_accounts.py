"""Bank accounts and investments: their own tabs, flowing into your net worth."""

from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import personal as pers
from am_accounting import personal_accounts as pacc
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pf(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    pers.save(pf, pers.Saved("Pat", [pers.Entry("Cash and bank accounts", "TD chequing", 12_000_00),
                                     pers.Entry("TFSA", "Questrade", 40_000_00),
                                     pers.Entry("Vehicles", "Car", 20_000_00)],
                             [pers.Entry("Credit cards", "Visa", 1_500_00),
                              pers.Entry("Car loans and leases", "", 8_000_00)]))
    return pf


def test_typed_lines_move_once(tmp_path):
    pf = _pf(tmp_path)
    assert pacc.move_typed_lines(pf) == 3 and pacc.move_typed_lines(pf) == 0
    a = pacc.load(pf)
    assert [(b.name, b.type, b.balance) for b in a.banks] == [("TD chequing", "Chequing", 12_000_00),
                                                             ("Visa", "Credit card", 1_500_00)]
    assert [(i.name, i.type, i.market_value) for i in a.investments] == [("Questrade", "TFSA", 40_000_00)]
    s = pers.load(pf)
    assert [e.kind for e in s.assets] == ["Vehicles"] and [e.kind for e in s.liabilities] == ["Car loans and leases"]
    st = pers.statement(pf, as_of=date(2026, 6, 30))                    # the same totals as before the move
    assert st.total_assets == 72_000_00 and st.total_liabilities == 9_500_00
    assert pers.snapshot(st).cash == 12_000_00
    pf.close()


def test_holdings_and_net_worth(tmp_path):
    pf = _pf(tmp_path)
    pacc.move_typed_lines(pf)
    a = pacc.load(pf)
    tfsa = a.investments[0]
    tfsa.cash = 500_00
    tfsa.holdings = [pacc.Holding("XEQT", "iShares All-Equity", 1000, 32_50, 28_000_00),
                     pacc.Holding("VFV", "", 50.5, 140_00, 6_000_00)]
    assert tfsa.market_value == 500_00 + 32_500_00 + 7_070_00 and tfsa.gain == 40_070_00 - 34_500_00
    a.investments.append(pacc.InvestmentAccount("RBC", "RRSP / RRIF", cash=10_000_00, id=a.new_id()))
    pacc.save(pf, a)
    assets, liabs = pacc.entries(pf)
    assert ("RRSP / RRIF", "RBC", 10_000_00) in [(e.kind, e.description, e.amount) for e in assets]
    assert [(e.kind, e.amount) for e in liabs] == [("Credit cards", 1_500_00)]
    st = pers.statement(pf, as_of=date(2026, 6, 30))
    assert st.total_assets == 12_000_00 + 40_070_00 + 10_000_00 + 20_000_00
    assert "TFSA — Questrade" in pers.html(st)
    pf.close()


def test_tabs(app, tmp_path):
    from am_accounting.gui.personal_finance_page import PersonalFinancePage

    pf = _pf(tmp_path)
    page = PersonalFinancePage(pf)                                       # moves the typed lines
    assert [page.tabs.tabText(i) for i in (1, 2)] == ["Bank Accounts", "Investments"]
    nw = page.net_worth
    assert ("Cash and bank accounts", "TD chequing", "$12,000.00") in nw.assets.auto_rows()
    assert ("Credit cards", "Visa", "$1,500.00") in nw.liabs.auto_rows()
    kinds = [nw.assets.table.cellWidget(r, 0).itemText(i) for r in range(nw.assets.n_auto, nw.assets.table.rowCount())
             for i in range(nw.assets.table.cellWidget(r, 0).count())]
    assert "Cash and bank accounts" not in kinds and "TFSA" not in kinds      # not typed on this page anymore
    bt = page.banks
    assert bt.cards["cash"].value.text() == "$12,000.00" and bt.cards["owed"].value.text() == "$1,500.00"
    bt.add("Savings", "Savings")
    r = bt.table.rowCount() - 1
    bt.table.item(r, 3).setText("5000")
    assert bt.table.item(r, 3).text() == "$5,000.00" and bt.table.item(r, 4).text() == date.today().isoformat()
    assert pacc.load(pf).banks[-1].balance == 5_000_00
    assert ("Cash and bank accounts", "Savings", "$5,000.00") in nw.assets.auto_rows()      # into net worth
    inv = page.investments
    inv.accounts.setCurrentCell(0, 0)
    inv.add_holding("xeqt", 100, 32_50, 3_000_00)
    assert pacc.load(pf).investments[0].holdings[0].symbol == "XEQT"
    assert inv.holdings.item(0, 4).text() == "$3,250.00" and inv.holdings.item(0, 6).text() == "$250.00"
    inv.holdings.item(0, 2).setText("200")                              # quantity
    assert pacc.load(pf).investments[0].holdings[0].quantity == 200
    assert inv.cards["value"].value.text() == fmt(40_000_00 + 6_500_00)
    assert ("TFSA", "Questrade", fmt(46_500_00)) in nw.assets.auto_rows()
    inv.add_account("Wealthsimple", "FHSA")
    assert len(pacc.load(pf).investments) == 2
    pf.close()


def fmt(c):
    from am_accounting.money import fmt_money

    return fmt_money(c)


def test_cash_and_month_on_net_worth(app, tmp_path):
    from am_accounting import personal_finance as pfi
    from am_accounting.gui.personal_finance_page import PersonalFinancePage

    pf = _pf(tmp_path)
    today = date.today()
    lines = pfi.budget_lines(pf)
    for ln in lines:
        ln.budget = {"Groceries": 800_00, "Salary / employment": 6_000_00}.get(ln.category, 0)
    pfi.save_budget_lines(pf, lines)
    pfi.set_actual(pf, today.year, "Groceries", today.month, 900_00)
    pfi.set_actual(pf, today.year, "Salary / employment", today.month, 6_000_00)
    page = PersonalFinancePage(pf)
    nw = page.net_worth
    assert nw.cards["cash"].value.text() == "$12,000.00"
    assert nw.cards["income"].value.text() == "$6,000.00" and "budget $6,000.00" in nw.cards["income"].sub.text()
    assert nw.cards["spent"].value.text() == "$900.00" and "budget $800.00" in nw.cards["spent"].sub.text()
    page.budget.fill()
    pfi.set_actual(pf, today.year, "Groceries", today.month, 1_000_00)
    nw.refresh()
    assert nw.cards["spent"].value.text() == "$1,000.00"
    pf.close()
