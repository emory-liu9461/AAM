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
    assert len(items) == len(dl.CHECKLIST) and all(it.due == soon for it in items)
    assert (items[0].section, items[0].grp, items[0].item) == ("Deliverables", "APS", "LOI")
    assert {it.section for it in items} == {"Deliverables", "Appraisal", "BCA", "ESA", "Financing"}
    items[0].status = "N/A"
    items[1].status = items[2].status = "Done"
    items[3].status = "In Progress"
    items.append(dl.DDItem("Rezoning letter", section="Deliverables", grp="Legals / Financials"))
    dl.save_checklist(pf, a.id, items)
    got = next(x for x in dl.deals(pf) if x.id == a.id)
    assert (got.dd_done, got.dd_total, got.dd_progress, got.dd_na) == (2, len(dl.CHECKLIST) + 1, 1, 1)
    p = dl.progress(dl.checklist(pf, a.id))
    assert p.needed == len(dl.CHECKLIST) and p.text() == f"2 / {len(dl.CHECKLIST)} done · 1 in progress"
    with pytest.raises(ValueError):
        dl.save_checklist(pf, a.id, [dl.DDItem("x", status="Maybe")])
    xl = tmp_path / "checklist.xlsx"
    dl.export_checklist(str(xl), "45 Elm St", dl.checklist(pf, a.id))
    import openpyxl
    ws = openpyxl.load_workbook(xl).active
    rows = [[c.value for c in r] for r in ws.iter_rows()]
    assert rows[0][0] == "Deal Checklist – 45 Elm St" and ["Deliverables"] == [x for x in rows[3] if x]
    assert any(r[2] == "Signed APS" and r[3] == "Done" for r in rows)
    dl.advance(pf, a.id)
    assert dl.deal(pf, a.id).stage == "Under Contract"
    dl.back(pf, a.id)
    assert dl.deal(pf, a.id).stage == "Due Diligence" and len(dl.checklist(pf, a.id)) == len(dl.CHECKLIST) + 1
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

    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry

    monkeypatch.setattr(dt, "confirm", lambda *a: True)
    monkeypatch.setattr(dt, "info", lambda *a: None)
    monkeypatch.setattr(dt, "error", lambda *a: (_ for _ in ()).throw(AssertionError(a[1:])))
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    added = []

    def create_company(name, **kw):
        return str(reg.create(CompanyProfile(name, name, kw.get("entity_type", ""), role="operating")))

    def fill_and_close(dlg):                         # the Close Deal window, filled in and saved
        dlg.company.setText("1001234567 Ontario Inc.")
        dlg.loans.setItem(0, 0, dt.QTableWidgetItem("First National"))
        dlg.loans.cellWidget(0, 2).setValue(3_000_000)
        dlg.eq.setItem(0, 0, dt.QTableWidgetItem("Pat Lee"))
        dlg.eq.setCurrentCell(0, 0)
        dlg._equity_rest()
        assert dlg.setup().cash_left == 0 and "cash left for the bank <b>$0.00" in dlg.totals.text()
        dlg.tabs.setCurrentIndex(4)
        assert "opening balance sheet" in dlg.review.text() and "Mortgage – First National" in dlg.review.text()
        dlg.save()
        return 1

    monkeypatch.setattr(dt.CloseDealDialog, "exec", fill_and_close)
    page = dt.DealPipelinePage(pf, on_added=added.append, create_company=create_company)
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
    assert page.tables["Due Diligence"].item(0, 3).text() == f"0 / {len(dl.CHECKLIST)}"
    # the deal checklist: sections → groups → items with a status each
    dd = dt.DealDialog(pf, dl.deal(pf, dlg.deal.id))
    assert dd.dd.topLevelItemCount() == 5 and dd.dd.topLevelItem(0).text(0) == "Deliverables"
    aps = dd.dd.topLevelItem(0).child(0)
    assert aps.text(0) == "APS" and aps.child(1).text(0) == "Signed APS"
    dd.dd.itemWidget(aps.child(0), 1).setCurrentIndex(dl.STATUSES.index("N/A"))
    dd.dd.itemWidget(aps.child(1), 1).setCurrentIndex(dl.STATUSES.index("Done"))
    aps.child(1).setText(3, "signed Nov 3")
    assert "background" in dd.dd.itemWidget(aps.child(1), 1).styleSheet()
    assert dd.dd.topLevelItem(0).text(1) == "1 / 12" and dd.bar.value() == round(100 / (len(dl.CHECKLIST) - 1))
    dd.dd.setCurrentItem(aps)
    dd._add_item_here()                                                # a deliverable of the user's own
    dd.dd.currentItem().setText(0, "Deposit receipt")
    dd.save()
    items = dl.checklist(pf, dlg.deal.id)
    assert any(it.item == "Deposit receipt" and it.grp == "APS" for it in items)
    assert next(it for it in items if it.item == "Signed APS").note == "signed Nov 3"
    page.refresh()
    assert page.tables["Due Diligence"].item(0, 3).text() == f"1 / {len(dl.CHECKLIST)}"
    assert page.selected().name == "45 Elm St"
    page.advance()                                                      # → under contract
    page.advance()                              # → Close Deal: its company, opening balance sheet, the portfolio
    assert dl.deal(pf, dlg.deal.id).stage == "Closed" and added and page.show_closed.isChecked()
    h = next(h for h in pf.holdings() if h.name == "45 Elm St")
    assert h.company_path and reg.find(h.company_path)
    page.select(next(d.id for d in dl.deals(pf) if d.name == "Dundas Plaza"))
    page.mark_dead()
    assert page.tables["Under Contract"].rowCount() == 0 and page.tables["Closed"].rowCount() == 2
    pf.close()


def test_ontario_land_transfer_tax():
    from am_accounting.closing import ontario_ltt

    assert ontario_ltt(500_000_00) == 6_475_00                      # the usual Ontario figure
    assert ontario_ltt(1_000_000_00) == 16_475_00
    assert ontario_ltt(4_200_000_00) == 80_475_00                   # multi-residential: 2% above $400K
    assert ontario_ltt(4_200_000_00, single_family=True) == 91_475_00   # 2.5% above $2M for 1–2 homes


def test_closing_a_deal_sets_up_its_company(tmp_path):
    from am_accounting import closing as cl
    from am_accounting import reports
    from am_accounting.books import Books, CompanyProfile
    from am_accounting.companies import CompanyRegistry

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")

    def create_company(name, **kw):
        return str(reg.create(CompanyProfile(name, name, kw.get("entity_type", ""), "",
                                             kw.get("fiscal_year_start_month", 1), role="operating")))

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("45 Elm St", "45 Elm St, Hamilton", stage="Under Contract", price_cents=4_200_000_00, units=24,
                equity_cents=1_300_000_00, closing_date="2026-12-15")
    dl.save(pf, d)
    s = cl.default_setup(dl.deal(pf, d.id))
    assert s.price == 4_200_000_00 and dict(s.costs)["Land transfer tax"] == 80_475_00
    assert s.debt == 2_900_000_00 and s.cash_left == 0
    s.company_name = "1001234567 Ontario Inc."
    s.costs = [("Purchase price", 4_200_000_00), ("Land transfer tax", 80_475_00), ("Legal fees", 35_000_00),
               ("Consultant reports (BCA, ESA, appraisal)", 22_500_00)]
    s.land_pct = 20
    s.loans[0].lender, s.loans[0].rate_pct = "First National", 4.9
    s.equity = [cl.EquityLine("Pat Lee", 1_000_000_00, "Class A"),
                cl.EquityLine("Kim Day", 400_000_00, "Class B", "Day Capital Ltd", "Shareholder loan")]
    assert s.uses == 4_337_975_00 and s.cash_left == -37_975_00
    with pytest.raises(ValueError, match="short"):
        s.check()                                                   # not enough money in to close
    s.equity[0].amount_cents = 1_100_000_00                         # enough to close, 62,025 left for the bank
    assert s.cash_left == 62_025_00
    hid = cl.close_deal(pf, d.id, s, create_company)
    h = next(x for x in pf.holdings() if x.id == hid)
    assert h.name == "45 Elm St" and h.type == "Real estate" and h.acquired == "2026-12-15" and h.company_path
    assert dl.deal(pf, d.id).stage == "Closed" and dl.deal(pf, d.id).holding_id == hid
    b = Books.open(h.company_path, create=False)
    assert b.company_name == "1001234567 Ontario Inc." and b.profile().entity_type == "Corporation"
    bs = reports.balance_sheet(b, "2026-12-31")
    assert all(bs.checks.values())
    names = {a.name: a for a in b.accounts()}
    prop = names["Property – 45 Elm St"]
    assert {a.name for a in b.children(prop.id)} == {
        "Property – 45 Elm St – Land", "Property – 45 Elm St – Building", "Property – 45 Elm St – Land transfer tax",
        "Property – 45 Elm St – Legal fees", "Property – 45 Elm St – Consultant reports (BCA, ESA, appraisal)"}
    t = b.ledger_totals(end="2026-12-31")
    assert t[names["Property – 45 Elm St – Land"].id] == 840_000_00
    assert t[names["Mortgage – First National"].id] == -2_900_000_00
    assert t[names["Share Capital – Class A"].id] == -1_100_000_00
    assert t[names["Shareholder Loan – Kim Day"].id] == -400_000_00
    bank = next(a for a in b.accounts() if a.is_bank)
    assert t[bank.id] == 62_025_00
    b.close()
    ln = pfm.loans(pf, h, date(2026, 12, 31))[0]
    assert ln.lender == "First National" and ln.balance == 2_900_000_00 and ln.book_entry_id == -1
    from am_accounting import loan_link as ll
    assert ll.book_loans(pf, h) == 0                                # not matched to Assets Held as well
    owners = {x.name: x for x in pfm.cap_table(pf, pfm.performance(pf, h, date(2026, 12, 31)))}
    assert owners["Pat Lee"].share_class == "Class A" and owners["Kim Day"].funded == 400_000_00
    v = pf.valuations(hid)[0]
    assert (v.company_value_cents, v.debt_cents, v.value_cents) == (4_200_000_00, 2_900_000_00, 1_300_000_00)
    su = pfm.entered_sources_uses(pf, hid)
    assert su["Debt financing"] == 2_900_000_00 and su["Closing & financing costs"] == 137_975_00
    assert su["Cash reserve / other"] == 62_025_00
    f = pfm.company_financials(h.company_path, date(2026, 12, 31))
    assert f.capex_ltm == 0                                          # the purchase isn't the year's capex
    with pytest.raises(ValueError):
        cl.close_deal(pf, d.id, s, create_company)                  # only once
    pf.close()
