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
    assert next(b for b in pacc.load(pf).banks if b.name == "Savings").balance == 5_000_00
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


def test_single_add_for_assets_and_liabilities(app, tmp_path):
    from am_accounting.gui.net_worth_page import NetWorthPage

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    page = NetWorthPage(pf)
    assert page.assets.table.rowCount() == page.assets.n_auto           # nothing pre-filled
    assert page.liabs.table.rowCount() == page.liabs.n_auto

    def home(dlg):
        assert dlg.kind.currentText() == "Principal residence"
        dlg.desc.setText("12 Oak St")
        dlg.amount.setText("900,000")
        dlg._ok()
        return dlg.result()

    def mortgage(dlg):
        dlg.group.setCurrentIndex(dlg.group.findData("liability"))
        assert dlg.kind.currentText() == "Mortgage on principal residence"
        dlg.desc.setText("RBC")
        dlg.amount.setText("450000")
        dlg._ok()
        return dlg.result()

    assert page.add_item(run=home) and page.add_item(run=mortgage)
    s = pers.load(pf)
    assert [(e.kind, e.description, e.amount) for e in s.assets] == [("Principal residence", "12 Oak St", 900_000_00)]
    assert [(e.kind, e.amount) for e in s.liabilities] == [("Mortgage on principal residence", 450_000_00)]
    assert page.statement().net_worth == 450_000_00
    page.liabs.table.setCurrentCell(page.liabs.n_auto, 1)
    page.liabs.table.setFocus()
    assert page.remove_item()
    assert pers.load(pf).liabilities == [] and len(pers.load(pf).assets) == 1
    pf.close()


def test_projected_vs_actual(app, tmp_path):
    from datetime import timedelta

    from am_accounting import personal_finance as pfi
    from am_accounting import personal_statements as pst
    from am_accounting.gui.net_worth_page import NetWorthPage

    pf = _pf(tmp_path)
    pacc.move_typed_lines(pf)
    today = date(2026, 11, 12)                                   # a Thursday
    lines = pfi.budget_lines(pf)
    for ln in lines:
        ln.budget = {"Groceries": 520_00, "Salary / employment": 5_200_00}.get(ln.category, 0)
    pfi.save_budget_lines(pf, lines)
    pfi.set_actual(pf, 2026, "Groceries", 11, 300_00)
    pfi.set_actual(pf, 2026, "Groceries", 3, 100_00)
    pst.ready(pf)
    with pf.conn:                                                # a statement line this week
        pf.conn.execute("INSERT INTO personal_tx(date, description, amount, category, source, dupkey) VALUES "
                        "('2026-11-10', 'LOBLAWS', -8000, 'Groceries', 'x.csv', 'k')")
    pers.record(pf, pers.Statement("Pat", date(2026, 11, 1), [], [pers.Entry("Cash and bank accounts", "", 9_000_00)],
                                   []))
    week, month, year = pfi.periods(pf, today)
    assert week.start == today - timedelta(days=3) and week.spend == 80_00 and week.spend_proj == 120_00
    assert month.income_proj == 5_200_00 and month.spend == 380_00 and month.spend_proj == 520_00
    assert year.spend_proj == 520_00 * 12 and year.spend == 480_00
    assert month.cash_start == 9_000_00 and month.cash_now == 12_000_00
    assert month.cash_proj == 9_000_00 + 5_200_00 - 520_00
    assert week.cash_start == 9_000_00 and year.cash_start is None
    page = NetWorthPage(pf)
    assert list(page.cards)[0] == "cash"                          # cash first
    assert page.plan.item(0, 0).text() == "Cash" and page.plan.horizontalHeaderItem(3).text() == "Month — projected"
    html = pers.html(page.statement())
    assert html.index("Cash and bank accounts") < html.index("TFSA")
    pf.close()


def test_plan_headings_right_aligned_in_a_box(app, tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QGroupBox

    from am_accounting.gui.net_worth_page import NetWorthPage

    pf = _pf(tmp_path)
    page = NetWorthPage(pf)
    for c in range(1, 7):
        assert page.plan.horizontalHeaderItem(c).textAlignment() & Qt.AlignmentFlag.AlignRight
    box = page.plan.parent()
    assert isinstance(box, QGroupBox) and "Projected" in box.title()
    pf.close()


def test_statement_side_by_side(tmp_path):
    pf = _pf(tmp_path)
    pacc.move_typed_lines(pf)
    s = pers.load(pf)
    s.assets.append(pers.Entry("Principal residence", "12 Oak St", 900_000_00))
    s.liabilities.append(pers.Entry("Mortgage on principal residence", "RBC", 450_000_00))
    pers.save(pf, s)
    st = pers.statement(pf, as_of=date(2026, 6, 30))
    sec = dict(pers.statement_sections(st))
    assert list(sec)[0] == "Cash" and sec["Cash"] == [("Cash and bank accounts — TD chequing", 12_000_00, "", None)]
    assert sec["Investments"][0][:2] == ("TFSA — Questrade", 40_000_00)
    assert sec["Real estate"] == [("Principal residence — 12 Oak St", 900_000_00,
                                   "Mortgage on principal residence — RBC", 450_000_00)]
    assert sec["Vehicles and other assets"] == [("Vehicles — Car", 20_000_00, "Car loans and leases", 8_000_00)]
    assert sec["Credit cards and other debt"] == [("", None, "Credit cards — Visa", 1_500_00)]
    page = pers.html(st)
    assert page.index("Cash") < page.index("Investments") < page.index("Real estate") < page.index("Credit cards")
    pf.close()


def test_debt_box_on_bank_accounts(app, tmp_path):
    from am_accounting.gui.personal_finance_page import PersonalFinancePage

    pf = _pf(tmp_path)
    page = PersonalFinancePage(pf)
    bt = page.banks
    assert bt.table.rowCount() == 1 and bt.debt_table.rowCount() == 1        # chequing / the Visa
    assert bt.debt_table.item(0, 0).text() == "Visa"
    bt.add(kind="Line of credit")
    r = bt.debt_table.rowCount() - 1
    bt.debt_table.item(r, 3).setText("15000")
    bt.add("Honda", "Car loan")
    r = bt.debt_table.rowCount() - 1
    bt.debt_table.item(r, 3).setText("9000")
    assert bt.cards["owed"].value.text() == "$25,500.00"
    st = page.net_worth.statement()
    sec = dict(pers.statement_sections(st))
    assert ("Vehicles — Car", 20_000_00, "Car loans and leases — Honda", 9_000_00) in sec["Vehicles and other assets"]
    debts = [x[2] for x in sec["Credit cards and other debt"]]
    assert "Credit cards — Visa" in debts and "Lines of credit — New line of credit" in debts
    bt.debt_table.setCurrentCell(0, 0)
    bt.remove()
    assert [b.name for b in pacc.load(pf).banks if b.owed] == ["New line of credit", "Honda"]
    pf.close()
