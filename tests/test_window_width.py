import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_pages_fit_a_scaled_laptop_screen(app, tmp_path):
    """Every page fits 1100 px (a 1920 screen at 150% is 1280): rows wrap, long hints wrap."""
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.flow import FlowLayout
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    path = str(reg.create(CompanyProfile("Maple Court Ltd")))
    w = MainWindow(path, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    from PySide6.QtWidgets import QTabWidget

    wide = {f"{type(tw).__name__}/{tw.tabText(i)}": tw.widget(i).minimumSizeHint().width()
            for tw in [w.tabs, v.tabs] + v.findChildren(QTabWidget) for i in range(tw.count())
            if tw.widget(i).minimumSizeHint().width() > 1100}
    assert wide == {}
    assert w.minimumSizeHint().width() < 1100
    assert w.tx_tab.findChildren(FlowLayout)                      # its toolbar wraps
    c = w.contacts_tab
    assert c.which.button(2) is None and c.new_btn.text() == "New Customer…"
    assert not c.table.isColumnHidden(5) and c.table.isColumnHidden(6)        # customers: owed to you
    c.which.button(1).click()
    assert c.new_btn.text() == "New Vendor…"
    assert c.table.isColumnHidden(5) and not c.table.isColumnHidden(6)        # vendors: owed to them
    assert c.table.horizontalHeaderItem(6).text() == "Owed to them" and "owed to them" in c.summary.text()


def test_company_pages_follow_the_company_at_the_top(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    a = str(reg.create(CompanyProfile("Alder Court Ltd")))
    b = str(reg.create(CompanyProfile("Birch Row Ltd")))
    w = MainWindow(a, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    pages = (v.dash_page, v.ops_page, v.cash_page, v.capex_page, v.folder_page)
    assert all(p.pick.isHidden() and p.pick_label.isHidden() for p in pages) and v.fin_pick.isHidden()
    assert {p.pick.currentText() for p in pages} | {v.fin_pick.currentText()} == {"Alder Court Ltd"}
    w.switch_books(b)                                          # the Company box at the very top
    assert {p.pick.currentText() for p in pages} | {v.fin_pick.currentText()} == {"Birch Row Ltd"}


def test_capex_in_suite_units_collapse(app, tmp_path):
    """The In-suite renovations line opens and closes its unit sub-lines (remembered)."""
    from am_accounting import capex as cx
    from am_accounting import operations as op
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    path = str(reg.create(CompanyProfile("Alder Court Ltd")))
    w = MainWindow(path, registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    cap = v.capex_page
    h = cap.perf().holding
    for u in ("101", "102", "103"):
        op.save_unit(v.pf, h.id, op.Unit(u, status="Vacant"))
    cx.add_template(v.pf, h.id)
    cap.fill()
    t = cap.table
    parent = next(r for r, x in enumerate(cap._rows) if x is not None and x.item == "In-suite renovations")
    pid = cap._rows[parent].id
    units = [r for r, x in enumerate(cap._rows) if x is not None and x.parent_id == pid]
    try:
        assert len(units) == 3 and t.item(parent, 0).text().strip().startswith("▾ ")
        assert not any(t.isRowHidden(r) for r in units)
        cap.toggle_units(pid)
        assert all(t.isRowHidden(r) for r in units) and t.item(parent, 0).text().strip().startswith("▸ ")
        cap.fill()                                                  # stays closed
        assert all(t.isRowHidden(r) for r in units)
        assert any("Total In-suite renovations" in (t.item(r, 0).text() if t.item(r, 0) else "")
                   and not t.isRowHidden(r) for r in range(t.rowCount()))   # its total still shows
        cap.toggle_units(pid)
        assert not any(t.isRowHidden(r) for r in units)
        # Hard Costs in two categories: in-suite renovations, and everything else (exterior & common area)
        names = [t.item(r, 0).text().strip() for r in range(t.rowCount())]
        ins, ext = names.index("In-suite renovations"), names.index("Exterior & common area")
        assert names.index("Hard Costs") < ins < names.index("▾ In-suite renovations") < ext < names.index("Roofing")
        assert names.index("Common area renovations") > ext and names.index("Contingency") > ext
        assert "Total In-suite renovations" in names and "Total exterior & common area" in names
        assert names.index("Total exterior & common area") < names.index("Total Hard Costs")
        lines = cx.lines(v.pf, h)
        assert {cx.category(x) for x in lines if x.parent_id == pid} == {cx.IN_SUITE}
        assert cx.category(next(x for x in lines if x.item == "Roofing")) == cx.EXTERIOR
        assert cx.category(next(x for x in lines if x.item == "GC fees")) == ""
    finally:
        cap._settings().remove("capex/collapsed")
        w.close()


def test_click_a_selected_line_again_to_unselect(app, tmp_path):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableWidget, QTableWidgetItem

    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    t = QTableWidget(3, 2)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    for r in range(3):
        t.setItem(r, 0, QTableWidgetItem(f"line {r}"))
    opened = []
    t.doubleClicked.connect(lambda i: opened.append(i.row()))
    t.show()
    QApplication.processEvents()
    at = t.visualItemRect(t.item(1, 0)).center()

    def wait():
        QTest.qWait(QApplication.doubleClickInterval() + 80)

    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=at)
    wait()
    assert [i.row() for i in t.selectionModel().selectedRows()] == [1]
    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=at)     # again: the highlight goes off
    wait()
    assert t.selectionModel().selectedRows() == [] and t.currentRow() == -1
    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=at)     # and on again
    wait()
    assert [i.row() for i in t.selectionModel().selectedRows()] == [1]
    QTest.mouseDClick(t.viewport(), Qt.MouseButton.LeftButton, pos=at)    # a double-click still opens it
    wait()
    assert opened == [1] and [i.row() for i in t.selectionModel().selectedRows()] == [1]
    other = t.visualItemRect(t.item(2, 0)).center()
    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=other)  # another line: just picks it
    wait()
    assert [i.row() for i in t.selectionModel().selectedRows()] == [2]
    t.close()
    w.close()


def test_click_a_selected_cell_again_in_a_typed_table(app, tmp_path):
    """Net Worth's Assets: cells you type into (double-click) unselect on a second click too."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from am_accounting import personal as pers
    from am_accounting import portfolio as pfm
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow
    from am_accounting.gui.net_worth_page import NetWorthPage

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)    # (installs the click filter)
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    pers.save(pf, pers.Saved("Pat", [pers.Entry("Vehicles", "Car", 20_000_00)]))
    page = NetWorthPage(pf)
    page.resize(1300, 800)
    page.show()
    QApplication.processEvents()
    t = page.assets.table
    at = t.visualItemRect(t.item(page.assets.n_auto, 1)).center()

    def wait():
        QTest.qWait(QApplication.doubleClickInterval() + 80)

    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=at)
    wait()
    assert len(t.selectionModel().selectedIndexes()) == 1
    QTest.mouseClick(t.viewport(), Qt.MouseButton.LeftButton, pos=at)     # again: the blue goes
    wait()
    assert t.selectionModel().selectedIndexes() == []
    page.close()
    pf.close()
    w.close()


def test_top_bar_never_scrolls_off(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    w.resize(1000, 420)                                          # shorter than the pages
    w.show()
    QApplication.processEvents()
    bar = w.outer_scroll.verticalScrollBar()
    bar.setValue(50)                                             # e.g. a field lower down getting the focus
    QApplication.processEvents()
    assert bar.value() == 0
    w.close()


def test_journal_entries_in_transactions(app, tmp_path):
    """The Transactions tab shows the journal entries too (not only bank lines), and they can be changed there."""
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow
    from am_accounting.gui.transactions import JournalRow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("High Park Ltd"))), registry=reg)
    b = w.books
    held = b.add_account("1650", "Assets Held", "asset", "fixed_asset").id
    kingsett = b.add_account("2810", "Term loan – Kingsett Capital", "liability", "long_term").id
    golden = b.add_account("2820", "Mortgage – Goldenbell Capital", "liability", "long_term").id
    eid = b.add_journal_entry("2025-09-01", "Mortgage – Goldenbell Capital (matched to Assets Held)",
                              [(held, 2_600_000_00, 0), (kingsett, 0, 2_600_000_00)])
    tab = w.tx_tab
    tab.refresh()
    assert tab.status_filter.currentData() == "all"                     # the default: everything
    assert any(isinstance(r, JournalRow) for r in tab.model.rows)
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("journal"))
    assert [type(r) for r in tab.model.rows] == [JournalRow]
    j = tab.model.rows[0]
    assert tab.model.data(tab.model.index(0, 1)) == "Journal entry"
    assert "Term loan – Kingsett Capital Cr" in tab.model.data(tab.model.index(0, 5))
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("all"))
    assert any(isinstance(r, JournalRow) for r in tab.model.rows)           # with everything else
    tab.search.setText("goldenbell")
    assert [r.id for r in tab.model.rows] == [-eid]
    tab.search.setText("")
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("unreviewed"))
    assert not any(isinstance(r, JournalRow) for r in tab.model.rows)

    def fix(dlg):                                                            # credit the right lender
        for r in range(dlg.table.rowCount()):
            box = dlg.table.cellWidget(r, 1)
            if box.currentData() == kingsett:
                box.setCurrentIndex(box.findData(golden))
        dlg.save()
        return dlg.result()

    assert tab.edit_journal_entry(j.entry, run=fix)
    assert b.account_balance(golden) == 2_600_000_00 and b.account_balance(kingsett) == 0
    tab.status_filter.setCurrentIndex(tab.status_filter.findData("journal"))
    tab.table.selectRow(0)
    assert tab.selected_ids() == [] and len(tab.selected_entries()) == 1
    w.close()


def test_files_kept_line_stays_on_one_line(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.common import OneLineLabel
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    labels = [x for x in w.findChildren(OneLineLabel)]
    assert labels                                                   # the Invoices & Bills folder line
    lbl = labels[0]
    long = "📁 Files are kept in …/Accounting/AM Accounting/Invoices & Bills — sorted by status (open / paid), paid " \
           "ones by year"
    lbl.setText(long)
    lbl.resize(300, 30)
    inner = lbl._label
    assert not inner.wordWrap() and "…" in inner.text() and long in inner.toolTip()
    lbl.resize(2000, 30)
    lbl.setText(long)                                               # (a hidden widget gets its resize when shown)
    assert inner.text() == long
    assert lbl.minimumSizeHint().width() < 200                      # never makes the page wider
    w.close()


def test_company_box_only_with_company_pages(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    w.show()
    w.set_mode("assets")
    v = w.asset_view
    v.show_company_pages()
    assert w.switcher.isVisible() and w.type_btn.isVisible()
    for show in (v.show_summary, v.show_deals, v.show_personal):
        show()
        assert not w.switcher.isVisible() and not w.company_label.isVisible() and not w.type_btn.isVisible()
        assert w.mode_tabs.isVisible()                               # Asset Management / Accounting stay
    v.show_company_pages()
    assert w.switcher.isVisible()
    v.show_summary()
    w.set_mode("accounting")                                         # always with Accounting
    assert w.switcher.isVisible()
    w.set_mode("assets")                                             # back: the company's pages (as before)
    assert w.switcher.isVisible() == (v.company_btn.isChecked())
    v.show_personal()
    assert not w.switcher.isVisible()
    w.close()
