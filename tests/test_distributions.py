from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import distributions as ds
from am_accounting import personal_finance as pfi
from am_accounting import portfolio as pfm
from am_accounting.books import Books, CompanyProfile, ParsedTransaction
from am_accounting.companies import CompanyRegistry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _setup(tmp_path):
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    op = reg.create(CompanyProfile("Maple Court Ltd"))
    b = Books.open(op)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date.today(), "RENT", 100_000_00)])
    b.set_category(b.transactions()[0].id, b.account_by_code("4100").id, source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", "Real estate", ownership_pct=50, company_path=str(op))
    pf.save_holding(h)
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Pat Lee", "", 50_000_00, link="me"))
    pfm.save_shareholder(pf, h.id, pfm.Shareholder("Sam Roe", "", 50_000_00))
    return pf, h


def test_cash_reserve_plan_and_paid(app, tmp_path):
    pf, h = _setup(tmp_path)
    today = date.today()
    p = pfm.performance(pf, h, today)
    pos = ds.position(pf, p, today)
    assert pos.has_books and pos.cash == 100_000_00 and pos.available == 100_000_00
    ds.set_reserve(pf, h.id, ds.Reserve(30_000_00))
    pos = ds.position(pf, p, today)
    assert pos.reserve == 30_000_00 and pos.available == 70_000_00
    ds.save_plan(pf, h.id, ds.Planned(today.isoformat(), 60_000_00, "Q1"))
    ds.save_plan(pf, h.id, ds.Planned(today.isoformat(), 20_000_00, "Q2"))
    pos = ds.position(pf, p, today)
    first, second = pos.rows
    assert first.cash_after == 40_000_00 and first.ok and first.ours == 30_000_00
    assert second.cash_after == 20_000_00 and not second.ok              # below the 30,000 reserve
    assert pos.planned_ahead == 80_000_00
    ds.mark_paid(pf, h, first.plan)                                      # recorded on the holding at our share
    flows = [f for f in pf.flows(h.id) if f.kind == "distribution"]
    assert [f.amount_cents for f in flows] == [30_000_00]
    row = next(x for x in pfi.from_portfolio(pf, today.year) if x.holding_id == h.id)
    assert row.distributions == 30_000_00 and row.source == "recorded"   # your personal income (all yours here)
    ds.mark_paid(pf, h, first.plan, False)                               # undone
    assert not [f for f in pf.flows(h.id) if f.kind == "distribution"]
    assert ds.owners_split(pf, h, 10_000_00) == [("Pat Lee", 5_000_00), ("Sam Roe", 5_000_00)]
    pf.close()


def test_page(app, tmp_path):
    from am_accounting.gui.distributions_page import DistributionsPage

    pf, h = _setup(tmp_path)

    class View:
        def __init__(self):
            self.pf = pf
            self.summary = pfm.summary(pf, date.today())
            self.tabs = None

    page = DistributionsPage(View())
    page.fill_pick(page.view.summary.performances)
    page.fill()
    assert page.cards["cash"].value.text() == "$100,000.00"

    def plan(dlg):
        dlg.amount.setValue(25_000)
        return True

    assert page.add(run=plan)
    assert page.table.rowCount() == 1 and page.table.item(0, 2).text() == "$12,500.00"     # our 50%
    page.table.selectRow(0)
    page.toggle_paid()
    assert page.table.item(0, 5).text() == "Paid"
    pf.close()
