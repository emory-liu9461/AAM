import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import contacts as ct
from am_accounting import shared_vendors as sv
from am_accounting.books import Books


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _co(tmp_path, name, *vendors, expense=None):
    b = Books.open(tmp_path / f"{name}.amdb")
    try:
        acct = b.add_account("6420", expense, "expense", "operating").id if expense else None
        for v in vendors:
            v.expense_id = acct if v.expense_id == "x" else None
            ct.save(b, v)
    finally:
        b.close()
    return str(tmp_path / f"{name}.amdb")


def _vendors(path):
    b = Books.open(path, create=False)
    try:
        return {c.name: c for c in ct.all_contacts(b, "vendor")}
    finally:
        b.close()


def test_every_company_gets_the_vendors(tmp_path):
    a = _co(tmp_path, "A", ct.Contact("Enbridge Gas", "vendor", "Supplier", "billing@enbridge.example", "555-0100",
                                      aliases=["ENBRIDGE"], expense_id="x"),
            ct.Contact("Old Supplier", "vendor", active=False), expense="Gas")
    b = _co(tmp_path, "B", ct.Contact("Enbridge Gas", "vendor", "Supplier", "other@b.example"),
            ct.Contact("North Plow", "vendor", "Contractor"))
    c = _co(tmp_path, "C", expense="Gas")
    added = sv.sync([a, b, c])
    assert added == {a: 1, c: 2}                                    # B already had Enbridge: nothing added there
    assert set(_vendors(c)) == {"Enbridge Gas", "North Plow"}       # inactive ones aren't shared
    enb = _vendors(c)["Enbridge Gas"]
    assert (enb.email, enb.phone, enb.aliases) == ("billing@enbridge.example", "555-0100", ["ENBRIDGE"])
    bc = Books.open(c, create=False)
    try:
        assert bc.account(enb.expense_id).name == "Gas"            # matched by account name in C
    finally:
        bc.close()
    assert _vendors(b)["Enbridge Gas"].email == "other@b.example"   # B's own record left as it is
    assert _vendors(a)["North Plow"].type == "Contractor"
    assert sv.sync([a, b, c]) == {}                                 # nothing new
    # removed from one company only: not added back to it
    bb = Books.open(b, create=False)
    try:
        ct.delete(bb, ct.find(bb, "North Plow").id)
        sv.skip_here(bb, "North Plow")
    finally:
        bb.close()
    assert sv.sync([a, b, c]) == {} and "North Plow" not in _vendors(b)
    # removed everywhere
    assert sv.remove_everywhere([a, b, c], "Enbridge Gas") == 3
    assert all("Enbridge Gas" not in _vendors(p) for p in (a, b, c))


def test_contacts_tab_remove_choices(app, tmp_path, monkeypatch):
    from am_accounting.gui import contacts_tab as ctt

    a = _co(tmp_path, "A", ct.Contact("Shield Mutual", "vendor"), ct.Contact("Bright Power", "vendor"))
    b = _co(tmp_path, "B")
    sv.sync([a, b])
    books = Books.open(a, create=False)
    try:
        tab = ctt.ContactsTab(books)
        tab.companies = lambda: [a, b]
        tab.which.button(1).click()                                 # Vendors
        names = [r.contact.name for r in tab.rows]
        tab.table.selectRow(names.index("Shield Mutual"))
        tab.delete(where="all")
        assert "Shield Mutual" not in _vendors(b) and ct.find(books, "Shield Mutual") is None
        names = [r.contact.name for r in tab.rows]
        tab.table.selectRow(names.index("Bright Power"))
        tab.delete(where="here")
        assert ct.find(books, "Bright Power") is None and "Bright Power" in _vendors(b)
        assert sv.sync([a, b]) == {}                                # not brought back to A
    finally:
        books.close()
