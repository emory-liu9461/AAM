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


def test_project_contacts_deal_and_property(tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from am_accounting import deals as dl
    from am_accounting import directory as dr
    from am_accounting.gui.directory_page import PickContactsDialog, ProjectContacts

    QApplication.instance() or QApplication([])
    pf, _h = _pf(tmp_path)
    d = dl.Deal("Maple Court", address="12 Maple Ct, Town")
    dl.save(pf, d)
    primary, names = dr.project_names(pf, deal=d)
    assert primary == "Maple Court"
    broker = dr.Person("Pat Lee", "Lee Mortgages", "Mortgage broker", phone="555-0101")
    dr.save(pf, broker)
    dr.save(pf, dr.Person("Sam Roe", "Roe Reno", "Contractor"))
    w = ProjectContacts(pf, primary, names, sync=False)
    assert w.rows == []

    def pick(dlg: PickContactsDialog):
        for i in range(dlg.list.count()):
            if dlg.list.item(i).text().startswith("Pat Lee"):
                from PySide6.QtCore import Qt
                dlg.list.item(i).setCheckState(Qt.CheckState.Checked)
        return True

    w.add_existing(run=pick)
    assert [p.name for p in w.rows] == ["Pat Lee"]
    assert "Maple Court" in dr.find(pf, "Pat Lee").works_on
    w.table.selectRow(0)
    assert w.remove(ask=False)
    assert w.rows == [] and dr.find(pf, "Pat Lee") is not None
    assert dr.details(broker) == "Lee Mortgages · 555-0101"


def test_on_project_matches_any_name(tmp_path):
    from am_accounting import directory as dr

    pf, _h = _pf(tmp_path)
    dr.save(pf, dr.Person("A", kind="Lender", works_on=["45 Elm"]))
    dr.save(pf, dr.Person("B", kind="Contractor", works_on=["Elm Deal"]))
    dr.save(pf, dr.Person("C", works_on=["Other"]))
    assert [p.name for p in dr.on_project(pf, ["45 elm", "Elm Deal"])] == ["B", "A"]
    p = dr.find(pf, "a")
    dr.unlink(pf, p, ["45 Elm"])
    assert dr.find(pf, "A").works_on == []


def test_investor_on_cap_table_goes_to_contacts(tmp_path, app):
    from am_accounting.gui.asset_mgmt import ShareholderDialog

    pf, h = _pf(tmp_path)
    dlg = ShareholderDialog(pf, h)
    dlg.name.setText("Jo Park")
    dlg.amount.setValue(50_000)
    dlg.phone.setText("555-0199")
    dlg.email.setText("jo@example.com")
    dlg.save()
    p = dr.find(pf, "Jo Park")
    assert p.kind == "Investor" and p.phone == "555-0199" and p.email == "jo@example.com"
    where, names = dr.project_names(pf, holding=h)
    assert where in p.works_on and p in dr.on_project(pf, names)
    # editing them again shows what's on file
    again = ShareholderDialog(pf, h, next(x for x in pfm.entered_shareholders(pf, h.id) if x.name == "Jo Park"))
    assert again.email.text() == "jo@example.com"
    # you aren't added as a contact
    me = ShareholderDialog(pf, h)
    me.name.setText("Me Myself")
    me.amount.setValue(1_000)
    me.link.setCurrentIndex(me.link.findData("me"))
    me.save()
    assert dr.find(pf, "Me Myself") is None


def test_contact_assigned_as_vendor_in_company_books(tmp_path, app):
    from PySide6.QtCore import Qt

    from am_accounting import contacts as ct
    from am_accounting.books import Books
    from am_accounting.gui.contacts_tab import ContactsTab

    pf, h = _pf(tmp_path)
    sparky = dr.Person("Dana Volt", "Volt Electric Ltd", "Contractor", phone="555-0123", email="dana@volt.example")
    dr.save(pf, sparky)
    books = Books.open(tmp_path / "co.amdb")
    tab = ContactsTab(books)
    tab.directory = lambda: (pf, "286 Saguenay")
    tab.which.button(1).click()                 # Vendors

    def pick(dlg):
        for i in range(dlg.list.count()):
            if dlg.list.item(i).text().startswith("Dana Volt"):
                dlg.list.item(i).setCheckState(Qt.CheckState.Checked)
        return True

    out = tab.from_directory(run=pick)
    c = ct.find(books, "Volt Electric Ltd")
    assert out and c.is_vendor and c.type == "Contractor" and c.phone == "555-0123"
    assert ct.find(books, "Dana Volt").id == c.id                 # their own name is another name for them
    assert "286 Saguenay" in dr.find(pf, "Dana Volt").works_on
    # again as a customer: the same contact, now both
    dr.assign(books, dr.find(pf, "Dana Volt"), "customer")
    assert ct.find(books, "Volt Electric Ltd").role == "both"
    assert len([x for x in ct.all_contacts(books) if x.name == "Volt Electric Ltd"]) == 1
