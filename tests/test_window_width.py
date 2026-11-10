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
    pages = [w.tabs.widget(i) for i in range(w.tabs.count())] + [v.tabs.widget(i) for i in range(v.tabs.count())]
    wide = {type(p).__name__: p.minimumSizeHint().width() for p in pages if p.minimumSizeHint().width() > 1100}
    assert wide == {}
    assert w.minimumSizeHint().width() < 1100
    assert w.tx_tab.findChildren(FlowLayout)                      # its toolbar wraps
    c = w.contacts_tab
    assert c.which.button(2) is None and c.new_btn.text() == "New Customer…"
    c.which.button(1).click()
    assert c.new_btn.text() == "New Vendor…"
