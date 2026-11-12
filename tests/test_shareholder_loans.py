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
    assert pers.load(pf).assets[0].amount == 15_000_00
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
    assert page.tabs.tabText(3) == "Shareholder Loans"
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
    assert pers.load(pf).assets[0].amount == 7_500_00                  # your cash went down
    page.tabs.setCurrentWidget(page.net_worth)
    assert page.net_worth.statement().loans_to_companies == 27_500_00
    pf.close()
