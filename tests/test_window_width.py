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
        assert len(units) == 3 and t.item(parent, 0).text().startswith("  ▾ ")
        assert not any(t.isRowHidden(r) for r in units)
        cap.toggle_units(pid)
        assert all(t.isRowHidden(r) for r in units) and t.item(parent, 0).text().startswith("  ▸ ")
        cap.fill()                                                  # stays closed
        assert all(t.isRowHidden(r) for r in units)
        assert any("Total In-suite renovations" in (t.item(r, 0).text() if t.item(r, 0) else "")
                   and not t.isRowHidden(r) for r in range(t.rowCount()))   # its total still shows
        cap.toggle_units(pid)
        assert not any(t.isRowHidden(r) for r in units)
    finally:
        cap._settings().remove("capex/collapsed")
        w.close()
