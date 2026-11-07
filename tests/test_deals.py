from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import deals as dl
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_deals_move_through_the_pipeline(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    soon = (date.today() + timedelta(days=20)).isoformat()
    a = dl.Deal("45 Elm St", "45 Elm St, Hamilton", price_cents=4_200_000_00, units=24, noi_cents=252_000_00,
                equity_cents=1_200_000_00, received="2026-10-01")
    dl.save(pf, a)
    b = dl.Deal("Dundas Plaza", asset_type="Retail", price_cents=7_500_000_00)
    dl.save(pf, b)
    assert a.cap_rate == pytest.approx(0.06) and a.per_unit == 175_000_00
    assert [d.name for d in dl.deals(pf, ["New"])] == ["45 Elm St", "Dundas Plaza"]      # received first
    # into due diligence: the usual checklist starts
    a.dd_deadline = soon
    dl.save(pf, a)
    dl.advance(pf, a.id)
    d = dl.deal(pf, a.id)
    assert d.stage == "Due Diligence" and d.key_date == ("DD deadline", soon) and d.days_to() == 20
    items = dl.checklist(pf, a.id)
    assert len(items) == len(dl.DD_ITEMS) and all(it.due == soon for it in items)
    items[0].done = items[1].done = True
    items.append(dl.DDItem("Rezoning letter"))
    dl.save_checklist(pf, a.id, items)
    got = next(x for x in dl.deals(pf) if x.id == a.id)
    assert (got.dd_done, got.dd_total) == (2, len(dl.DD_ITEMS) + 1)
    dl.advance(pf, a.id)
    assert dl.deal(pf, a.id).stage == "Under Contract"
    dl.back(pf, a.id)
    assert dl.deal(pf, a.id).stage == "Due Diligence" and len(dl.checklist(pf, a.id)) == len(dl.DD_ITEMS) + 1
    dl.move(pf, b.id, "Dead")
    t = dl.totals(dl.deals(pf))
    assert t["Due Diligence"].count == 1 and t["Due Diligence"].price == 4_200_000_00 and t["Dead"].count == 1
    # closed and added to the portfolio
    d = dl.deal(pf, a.id)
    d.closing_date = "2026-12-15"
    dl.save(pf, d)
    dl.advance(pf, a.id)
    dl.advance(pf, a.id)
    assert dl.deal(pf, a.id).stage == "Closed"
    hid = dl.add_to_portfolio(pf, a.id)
    h = next(x for x in pf.holdings() if x.id == hid)
    assert (h.name, h.type, h.acquired, h.location) == ("45 Elm St", "Real estate", "2026-12-15", "45 Elm St, Hamilton")
    v = pf.valuations(hid)[0]
    assert (v.company_value_cents, v.value_cents, v.debt_cents, v.method) == (
        4_200_000_00, 1_200_000_00, 3_000_000_00, "Purchase price")
    with pytest.raises(ValueError):
        dl.add_to_portfolio(pf, a.id)                                  # only once
    dl.delete(pf, b.id)
    assert [x.name for x in dl.deals(pf)] == ["45 Elm St"]
    with pytest.raises(ValueError):
        dl.save(pf, dl.Deal(" "))
    pf.close()


def test_deal_pipeline_page(app, tmp_path, monkeypatch):
    from am_accounting.gui import deals_tab as dt

    monkeypatch.setattr(dt, "confirm", lambda *a: True)
    monkeypatch.setattr(dt, "info", lambda *a: None)
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    added = []
    page = dt.DealPipelinePage(pf, on_added=added.append)
    dlg = dt.DealDialog(pf, parent=page)
    dlg.name.setText("45 Elm St")
    dlg.price.setValue(4_200_000)
    dlg.noi.setValue(252_000)
    dlg.units.setValue(24)
    assert "cap rate 6.00%" in dlg.metrics.text() and "$175,000.00 per unit" in dlg.metrics.text()
    dlg.save()
    assert dlg.result()
    dl.save(pf, dl.Deal("Dundas Plaza", stage="Under Contract", price_cents=7_500_000_00,
                        closing_date=(date.today() + timedelta(days=10)).isoformat()))
    page.refresh()
    assert page.tables["New"].rowCount() == 1 and page.tables["Under Contract"].rowCount() == 1
    assert page.cards["New"].value.text() == "1" and page.cards["next"].value.text() != "—"
    assert "NEW DEALS" in page.heads["New"].text()
    page.select(dlg.deal.id)
    page.advance()                                                      # → due diligence
    assert page.tables["Due Diligence"].rowCount() == 1 and page.tables["New"].rowCount() == 0
    assert page.tables["Due Diligence"].item(0, 3).text() == f"0 / {len(dl.DD_ITEMS)}"
    assert page.selected().name == "45 Elm St"
    page.advance()                                                      # → under contract
    page.advance()                                                      # → closed, added to the portfolio
    assert dl.deal(pf, dlg.deal.id).stage == "Closed" and added and page.show_closed.isChecked()
    assert any(h.name == "45 Elm St" for h in pf.holdings())
    page.select(next(d.id for d in dl.deals(pf) if d.name == "Dundas Plaza"))
    page.mark_dead()
    assert page.tables["Under Contract"].rowCount() == 0 and page.tables["Closed"].rowCount() == 2
    pf.close()
