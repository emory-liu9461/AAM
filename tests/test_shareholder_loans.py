"""Shareholder loans between you and your companies: in your net worth, and posted in the company's books."""

from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import personal as pers
from am_accounting import portfolio as pfm
from am_accounting import shareholder_loans as shl
from am_accounting.books import Books


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _setup(tmp_path):
    hold = tmp_path / "Maple Holdings.amdb"
    b = Books.open(hold, create=True)
    b.company_name = "Maple Holdings Inc."
    loan = b.add_account("2810", "Shareholder Loan – Pat Lee", "liability", "long_term").id
    bank = b.account_by_code("1000").id
    b.add_journal_entry("2026-01-10", "Pat lent the company", [(bank, 25_000_00, 0), (loan, 0, 25_000_00)])
    b.close()
    op = tmp_path / "Elm.amdb"
    b = Books.open(op, create=True)
    b.company_name = "Elm Street Ltd"
    b.add_account("1390", "Due from shareholder", "asset", "current")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    pf.holdco_path = str(hold)
    h = pfm.Holding("Elm Street", "Real estate", company_path=str(op), owner_path=str(hold))
    pf.save_holding(h)
    pers.save(pf, pers.Saved("Pat Lee", [pers.Entry("Cash and bank accounts", "", 10_000_00)]))
    return pf, hold, op, loan


def test_link_and_net_worth(tmp_path):
    pf, hold, op, loan = _setup(tmp_path)
    assert {p for _n, p in shl.companies(pf)} == {str(hold), str(op)}
    assert [a.name for a in shl.candidates(str(hold), "Pat Lee")][0] == "Shareholder Loan – Pat Lee"
    assert shl.auto_link(pf) == 2 and shl.auto_link(pf) == 0
    pos = {p.path: p for p in shl.positions(pf)}
    assert pos[str(hold)].owed_to_you == 25_000_00 and pos[str(op)].owed_to_you == 0
    st = pers.statement(pf, as_of=date(2026, 6, 30))
    assert st.loans_to_companies == 25_000_00 and st.total_assets == 35_000_00
    assert "Shareholder loan to" in pers.html(st)
    assert pers.snapshot(st).by_kind[pers.LOANS_TO] == 25_000_00
    pf.close()


def test_cross_entries(tmp_path):
    pf, hold, op, loan = _setup(tmp_path)
    shl.auto_link(pf)
    bank = shl.bank_accounts(str(hold))[0][0]
    shl.record(str(hold), loan, shl.PAID_YOU, 5_000_00, date(2026, 3, 1), other_account=bank, memo="partial")
    shl.adjust_cash(pf, shl.PAID_YOU, 5_000_00)
    assert {p.path: p for p in shl.positions(pf)}[str(hold)].owed_to_you == 20_000_00
    from am_accounting import personal_accounts as pacc

    assert [(b.name, b.balance) for b in pacc.load(pf).banks] == [("Cash", 5_000_00)]   # your cash account
    hist = shl.history(str(hold), loan)
    assert hist[0].amount == -5_000_00 and "partial" in hist[0].memo and hist[1].amount == 25_000_00
    exp = shl.expense_accounts(str(hold))[0][0]
    shl.record(str(hold), loan, shl.PAID_EXPENSE, 300_00, date(2026, 3, 2), other_account=exp)
    assert {p.path: p for p in shl.positions(pf)}[str(hold)].owed_to_you == 20_300_00
    b = Books.open(hold)
    assert b.account_balance(bank) == 20_000_00 and b.account_balance(exp) == 300_00
    b.close()
    # you owe a company: a liability on your net worth
    new = shl.create_account(str(op), "Pat Lee")
    shl.set_link(pf, str(op), new)
    opbank = shl.bank_accounts(str(op))[0][0]
    shl.record(str(op), new, shl.PAID_FOR_YOU, 1_000_00, date(2026, 3, 3), other_account=opbank)
    st = pers.statement(pf, as_of=date(2026, 6, 30))
    assert st.owed_to_companies == 1_000_00 and "Owed to" in pers.html(st)
    with pytest.raises(ValueError):
        shl.record(str(op), new, shl.LEND, 0, date(2026, 3, 3), other_account=opbank)
    pf.close()


def test_tab(app, tmp_path):
    from am_accounting.gui.personal_finance_page import PersonalFinancePage

    pf, hold, op, loan = _setup(tmp_path)
    page = PersonalFinancePage(pf)
    tab = page.loans
    assert page.tabs.tabText(5) == "Shareholder Loans"
    assert tab.cards["to_you"].value.text() == "$25,000.00"
    r = next(i for i, p in enumerate(tab.positions) if p.path == str(hold))
    tab.table.setCurrentCell(r, 0)
    assert tab.hist.rowCount() == 1 and tab.hist.item(0, 2).text() == "+$25,000.00"

    def run(dlg):
        dlg.direction.setCurrentIndex(dlg.direction.findData(shl.LEND))
        dlg.amount.setText("2,500")
        assert dlg.other_label.text() == "Company bank account:"
        return dlg.record() is not None

    assert tab.record(run=run)
    assert tab.cards["to_you"].value.text() == "$27,500.00"
    from am_accounting import personal_accounts as pacc

    assert pacc.load(pf).banks[0].balance == 7_500_00                  # your cash went down (moved to an account)
    assert not [e for e in pers.load(pf).assets if e.amount]           # (the typed cash line moved to Bank Accounts)
    page.tabs.setCurrentWidget(page.net_worth)
    assert page.net_worth.statement().loans_to_companies == 27_500_00
    pf.close()


def test_holding_company_in_net_worth(app, tmp_path):
    from am_accounting.gui.net_worth_page import NetWorthPage

    pf, hold, op, loan = _setup(tmp_path)
    b = Books.open(hold)
    bank = b.account_by_code("1000").id
    inv = b.add_account("1610", "Investment in Elm Street", "asset", "other_asset").id
    b.add_journal_entry("2026-02-01", "bought Elm", [(inv, 15_000_00, 0), (bank, 0, 15_000_00)])
    b.close()
    h = pf.holdings()[0]
    h.invest_accounts = [inv]
    pf.save_holding(h)
    shl.auto_link(pf)
    s = pers.load(pf)
    s.holdco, s.holdco_pct = str(hold), 50.0
    pers.save(pf, s)
    hc = pers.holdco_part(pf, as_of=date(2026, 6, 30))
    # bank 25,000 lent − 15,000 invested = 10,000 cash; the investment is left out (it's in the real estate)
    assert (hc.cash, hc.other_assets, hc.left_out, hc.liabilities) == (10_000_00, 0, 15_000_00, 25_000_00)
    assert (hc.my_assets, hc.my_liabilities) == (5_000_00, 12_500_00)
    props = pers.properties(pf, as_of=date(2026, 6, 30))
    assert props[0].share_pct == 50.0 and props[0].default_pct == 50.0      # 100% held × your 50% of the holdco
    st = pers.statement(pf, as_of=date(2026, 6, 30))
    assert st.holdco.name == "Maple Holdings Inc."
    # your 10,000 cash + the loan it owes you 25,000 + your half of its cash 5,000; less your half of its debts
    assert st.total_assets == 10_000_00 + 25_000_00 + 5_000_00 + st.real_estate
    assert st.total_liabilities == 12_500_00 + st.real_estate_debt
    assert "Maple Holdings Inc. — cash and other assets (your 50%)" in pers.html(st)
    assert pers.snapshot(st).by_kind[pers.HOLDCO] == 5_000_00
    # on the page
    s.holdco = ""
    pers.save(pf, s)
    page = NetWorthPage(pf, as_of=date(2026, 6, 30))
    assert not hasattr(page, "holdco_box") and page.statement().holdco is None   # picked in Personal Settings

    def pick(sd):
        assert sd.holdco_box.currentData() == ""
        sd.pick_holdco(str(hold))
        assert sd.holdco_box.currentData() == str(hold) and sd.holdco_pct.isEnabled()
        sd.holdco_pct.setValue(100)
        assert "your 100%" in sd.holdco_label.text() and "$15,000.00 invested" in sd.holdco_label.text()
        return sd.save()

    page.settings(run=pick)
    assert pers.load(pf).holdco == str(hold) and pers.load(pf).holdco_pct == 100
    assert page.props[0].share_pct == 100 and page.statement().holdco.my_assets == 10_000_00
    assert any(r[0] == "Your holding company" for r in page.assets.auto_rows())
    assert pers.load(pf).shares == {}                                      # not changed by hand: not kept
    pf.close()


def test_my_ownership_from_cap_tables(app, tmp_path):
    pf, hold, op, loan = _setup(tmp_path)
    h = pf.holdings()[0]
    s = pers.load(pf)
    s.holdco, s.holdco_pct = str(hold), 50.0
    pers.save(pf, s)
    # no owners entered: the holding's ownership × your share of your holding company (it holds it)
    o = pers.my_ownership(pf, h)
    assert o.pct == 50.0 and not o.from_cap_table
    # the cap table: your holding company 40%, you 10% directly, someone else 50%
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Pat", "Maple Holdings Inc", 400_000_00))       # found by name
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Pat Lee", "", 100_000_00, link="me"))
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Sam Roe", "", 500_000_00))
    o = pers.my_ownership(pf, h)
    assert o.from_cap_table and o.pct == 40 * 0.5 + 10                  # 30%
    assert "× your 50%" in o.how
    assert pers.properties(pf)[0].default_pct == 30.0                 # your net worth follows
    rows = {x.name: x for x in pfm.entered_shareholders(pf, h.id)}
    assert rows["Pat Lee"].link == "me" and rows["Pat"].link == ""
    assert pers.guess_link(rows["Pat"], pers.load(pf)) == "holdco"
    sam = rows["Sam Roe"]
    sam.link = "me"                                                   # linked by hand
    pfm.save_shareholder(pf, h.id, sam)
    assert pers.my_ownership(pf, h).pct == 80.0
    pf.close()


def test_ownership_shown_in_the_app(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui import asset_mgmt as am
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    h = v.pf.holdings()[0]
    s = pers.load(v.pf)
    s.name = "Pat Lee"
    pers.save(v.pf, s)
    dlg = am.ShareholderDialog(v.pf, h)
    dlg.name.setText("Pat Lee")
    dlg.amount.setValue(60_000)
    dlg.link.setCurrentIndex(dlg.link.findData("me"))
    dlg.save()
    pfm.save_shareholder(v.pf, h.id, pfm.Shareholder("Sam Roe", "", 40_000_00))
    v.refresh()
    assert am.HOLD_COLS[3] == "Yours %"
    r = next(i for i in range(v.table.rowCount()) if v.table.item(i, 0).text().startswith(h.name))
    assert v.table.item(r, 3).text() == "60%"
    v._fill_financing()
    assert "Your ownership: 60.00%" in v.fin_mine.text()
    assert any(v.fin_holders.item(i, 0).text() == "★ Pat Lee" for i in range(v.fin_holders.rowCount()))
    v.dash_page.fill()
    assert ["Your ownership", "60%"] in v.dash_page.data["property"]
    w.close()
