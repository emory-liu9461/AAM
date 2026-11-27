"""Contacts: contractors, investors, lenders … beside Personal Finance."""

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import directory as dr
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pf(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("286-286 Saguenay Investment Limited", "Real estate")
    pf.save_holding(h)
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Sam Roe", "", 100_000_00))
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Pat Lee", "", 100_000_00, link="me"))     # you: not a contact
    pfm.save_loan(pf, h.id, pfm.Loan("First National", "Mortgage", 2_000_000_00, 2_000_000_00, 5.0))
    return pf, h


def test_people_and_portfolio(tmp_path):
    pf, h = _pf(tmp_path)
    dr.save(pf, dr.Person("Ann Builder", "Builder Co", "Contractor", "905-555-0101", "ann@example.com",
                          works_on=["286-286 Saguenay"], notes="Did the roof"))
    assert dr.from_portfolio(pf) == 2 and dr.from_portfolio(pf) == 0          # nobody twice
    names = {p.name: p for p in dr.people(pf)}
    assert set(names) == {"Ann Builder", "Sam Roe", "First National"}
    assert names["Sam Roe"].kind == "Investor" and names["First National"].kind == "Lender"
    assert names["First National"].works_on == ["286-286 Saguenay"]
    assert [p.name for p in dr.people(pf, "Contractor")] == ["Ann Builder"]
    assert [p.name for p in dr.people(pf, search="roof saguenay")] == ["Ann Builder"]
    assert dr.counts(pf) == {"Contractor": 1, "Investor": 1, "Lender": 1}
    with pytest.raises(ValueError):
        dr.save(pf, dr.Person(" "))
    dr.delete(pf, names["Sam Roe"].id)
    assert len(dr.people(pf)) == 2
    pf.close()


def test_page_beside_personal_finance(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    w.show()
    w.set_mode("assets")
    v = w.asset_view
    assert v.contacts_btn.text() == "Contacts"
    btns = [b.text() for b in v.findChildren(type(v.contacts_btn)) if b.isCheckable()]
    assert btns.index("Contacts") == btns.index("Personal Finance") + 1      # beside Personal Finance
    v.show_contacts()
    page = v.contacts_page
    assert v.tabs.currentWidget() is page and v.contacts_btn.isChecked() and not v.net_worth_btn.isChecked()
    assert not w.switcher.isVisible() and not v.tabs.tabBar().isVisible()

    def add(dlg):
        dlg.name.setText("Ann Builder")
        dlg.company.setText("Builder Co")
        dlg.kind.setCurrentText("Contractor")
        dlg.email.setText("ann@example.com")
        return dlg.save()

    assert page.new(run=add)
    assert page.table.rowCount() == 1 and page.table.item(0, 2).text() == "Contractor"
    page.kind.setCurrentIndex(page.kind.findData("Lender"))
    assert page.table.rowCount() == 0
    page.kind.setCurrentIndex(0)
    page.table.selectRow(0)

    def change(dlg):
        dlg.phone.setText("905-555-0101")
        return dlg.save()

    assert page.edit(run=change)
    assert dr.people(v.pf)[0].phone == "905-555-0101"
    page.table.selectRow(0)
    assert page.delete(ask=False) and page.table.rowCount() == 0
    v.show_summary()
    assert not v.contacts_btn.isChecked()
    w.close()
