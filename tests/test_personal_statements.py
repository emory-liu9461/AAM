"""Personal statements: read from a folder, sorted into the budget's categories, counted as its actuals."""

from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import personal_finance as pfi
from am_accounting import personal_statements as pst
from am_accounting import portfolio as pfm

YEAR = date.today().year - 1


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


CSV1 = f"""Date,Description,Amount
{YEAR}-03-02,LOBLAWS #1234 TORONTO,-182.40
{YEAR}-03-05,PAYROLL ACME CORP,3500.00
{YEAR}-03-09,ESSO 5521,-60.00
{YEAR}-03-12,MYSTERY SHOP 77,-25.00
{YEAR}-03-15,PAYMENT - THANK YOU,500.00
{YEAR}-03-20,MAPLE HOLDINGS E-TRANSFER,2000.00
{YEAR}-03-21,INTERAC E-TRANSFER TO J SMITH,-300.00
{YEAR}-03-22,TRANSFER TO SAVINGS,-1000.00
"""
CSV2 = f"""Date,Description,Amount
{YEAR}-03-12,MYSTERY SHOP 77,-25.00
{YEAR}-04-02,MYSTERY SHOP 12,-40.00
"""


def _pf(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    from am_accounting.books import Books

    hold = tmp_path / "Maple Holdings.amdb"
    b = Books.open(hold, create=True)
    b.company_name = "Maple Holdings Inc."
    b.close()
    pf.holdco_path = str(hold)
    pf.save_holding(pfm.Holding("Elm", "Real estate", owner_path=str(hold)))
    f = tmp_path / "Statements"
    (f / "TD Chequing").mkdir(parents=True)
    (f / "Archive").mkdir()
    (f / "TD Chequing" / "march.csv").write_text(CSV1)
    (f / "Archive" / "old.csv").write_text(CSV1)
    pst.set_folder(pf, f)
    return pf, f


def test_read_and_sort(tmp_path):
    pf, f = _pf(tmp_path)
    res = pst.scan(pf)
    assert (res.files, res.added) == (1, 8)                                # not the Archive
    assert pst.scan(pf).files == 0                                          # each file once
    cats = {t.description: t.category for t in pst.transactions(pf, YEAR)}
    assert cats["LOBLAWS #1234 TORONTO"] == "Groceries" and cats["ESSO 5521"] == "Transportation"
    assert cats["PAYROLL ACME CORP"] == "Salary / employment" and cats["PAYMENT - THANK YOU"] == pst.TRANSFER
    assert cats["MAPLE HOLDINGS E-TRANSFER"] == pst.TRANSFER                 # your own company: already counted
    assert cats["MYSTERY SHOP 77"] == "" and cats["INTERAC E-TRANSFER TO J SMITH"] == ""    # e-transfers: you sort
    assert cats["TRANSFER TO SAVINGS"] == pst.TRANSFER
    pst.set_category(pf, next(t.id for t in pst.transactions(pf, YEAR) if "J SMITH" in t.description), "Other")
    (f / "TD Chequing" / "april.csv").write_text(CSV2)                      # overlaps March 12
    res = pst.scan(pf)
    assert (res.added, res.duplicates) == (1, 1)
    shop = next(t for t in pst.transactions(pf, YEAR) if t.description == "MYSTERY SHOP 77")
    assert pst.set_category(pf, shop.id, "Entertainment") == 2               # the April one follows
    assert not pst.transactions(pf, YEAR, unsorted_only=True)
    (f / "TD Chequing" / "may.csv").write_text(f"Date,Description,Amount\n{YEAR}-05-01,MYSTERY SHOP 9,-10.00\n")
    pst.scan(pf)
    assert next(t for t in pst.transactions(pf, YEAR) if t.date == f"{YEAR}-05-01").category == "Entertainment"
    m = pst.monthly(pf, YEAR)
    assert m["Groceries"] == {3: 182_40} and m["Salary / employment"] == {3: 3_500_00}
    assert m["Entertainment"] == {3: 25_00, 4: 40_00, 5: 10_00} and pst.TRANSFER not in m
    pf.close()


def test_into_the_budget(tmp_path):
    pf, f = _pf(tmp_path)
    pst.scan(pf)
    pfi.set_actual(pf, YEAR, "Groceries", 3, 20_00)                          # cash you typed
    rows = {r.line.category: r for r in pfi.budget(pf, YEAR).rows}
    assert rows["Groceries"].months[3] == 202_40 and rows["Groceries"].statements == {3: 182_40}
    assert rows["Salary / employment"].months[3] == 3_500_00
    pf.close()


def test_tab(app, tmp_path):
    from am_accounting.gui.personal_finance_page import PersonalFinancePage

    pf, f = _pf(tmp_path)
    page = PersonalFinancePage(pf)
    st = page.statements
    st.year.setValue(YEAR)
    page.budget.year.setValue(YEAR)
    page.tabs.setCurrentWidget(st)                                          # read when opened
    assert st.table.rowCount() == 8 and st.cards["unsorted"].value.text() == "2"
    st.sort(next(x.id for x in st.txs if "J SMITH" in x.description), "Other")
    r = next(i for i, x in enumerate(st.txs) if x.description == "MYSTERY SHOP 77")
    st.sort(st.txs[r].id, "Dining out")
    assert st.cards["unsorted"].value.text() == "0"
    b = page.budget
    i = next(i for i, (k, x) in enumerate(b._rows) if k == "line" and x.line.category == "Dining out")
    assert b.table.item(i, 4).text() == "$25.00"                             # March, from the statement
    b.table.item(i, 4).setText("$40")                                       # typed total: $15 more in cash
    assert pfi.actuals(pf, YEAR)["Dining out"][3] == 15_00
    i = next(i for i, (k, x) in enumerate(b._rows) if k == "line" and x.line.category == "Dining out")
    assert b.table.item(i, 4).text() == "$40.00"
    new = tmp_path / "Elsewhere"
    st.choose(str(new))
    assert pst.folder(pf) == new and new.is_dir()
    pf.close()
