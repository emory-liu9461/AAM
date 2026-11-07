from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import capex as cx
from am_accounting import health
from am_accounting import operations as op
from am_accounting import portfolio as pfm
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _company(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    rent = b.add_account("4150", "Rental Income", "income", "operating").id
    roof = b.add_account("1650", "CapEx – Roofing", "asset", "fixed_asset").id
    equip = next(a.id for a in b.accounts() if a.name == "Equipment")
    d = date.today() - timedelta(days=20)
    b.import_transactions(bank, [ParsedTransaction(d, "RENTS", 2_000_00), ParsedTransaction(d, "ROOFER", -70_000_00),
                                 ParsedTransaction(d, "BOILER", -9_000_00), ParsedTransaction(d, "MYSTERY", -50_00)])
    for t in b.transactions():
        acct = {"RENTS": rent, "ROOFER": roof, "BOILER": equip}.get(t.description)
        if acct:
            b.set_category(t.id, acct, source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", "Real estate", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    for u in (op.Unit("101", "A. Tenant", rent_cents=1_500_00, market_cents=1_600_00, lease_end="2020-06-30"),
              op.Unit("102", "B. Tenant", rent_cents=0, market_cents=1_600_00),
              op.Unit("103", status="Vacant", rent_cents=1_400_00),
              op.Unit("101", "C. Tenant", rent_cents=1_550_00, market_cents=1_600_00)):
        op.save_unit(pf, h.id, u)
    cx.save_line(pf, h.id, cx.CapexLine("Roofing", budget_cents=60_000_00, account_id=roof))
    return pf, h


def test_company_issues(tmp_path):
    pf, h = _company(tmp_path)
    issues = health.company_issues(pf, h)
    text = " | ".join(f"{i.area}: {i.text}" for i in issues)
    assert {i.area for i in issues} == {"Rent roll", "Expenses", "CapEx"}
    assert "occupied unit(s) with no rent (102)" in text and "vacant unit(s) still showing rent (103)" in text
    assert "no market rent" in text and "listed twice: 101" in text and "lease(s) ended" in text
    assert "rent roll" in text and "in the books" in text                          # roll vs the books' rent
    assert "1 uncategorized transaction(s)" in text
    assert "over budget (Roofing)" in text and "$9,000.00 spent on fixed assets" in text
    assert {i.where for i in issues} >= {"rent_roll", "transactions", "capex"}
    pf.close()


def test_no_rent_roll_and_no_books(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Empty", "Real estate")
    pf.save_holding(h)
    assert [i.text for i in health.company_issues(pf, h)] == ["no rent roll yet — Import Rent Roll…"]
    pf.close()


def test_attention_banner(app, tmp_path):
    from am_accounting.gui.asset_mgmt import AssetManagementView

    pf, h = _company(tmp_path)
    pf.close()
    v = AssetManagementView(tmp_path / "p.ampf", lambda: [])
    v.refresh()
    assert v.attention.isHidden()                            # the portfolio summary: no company shown
    v.tabs.setCurrentWidget(v.dash_page)
    assert not v.attention.isHidden() and "Needs attention — Maple Court" in v.attention_text.text()
    assert "<b>Rent roll:</b>" in v.attention_text.text() and "href='capex'" in v.attention_text.text()
    v._attention_go("capex")
    assert v.tabs.currentWidget() is v.capex_page and v.capex_page.lower.currentIndex() == 1
    v._attention_go("rent_roll")
    assert v.tabs.currentWidget() is v.ops_page and v.ops_page.sub.tabText(v.ops_page.sub.currentIndex()) == "Rent Roll"
    got = []
    v.open_review.connect(got.append)
    v._attention_go("transactions")
    assert got == [h.company_path]
    v.show_deals()
    assert v.attention.isHidden()
    v.close_portfolio()
