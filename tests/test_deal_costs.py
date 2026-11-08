from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import closing as cl
from am_accounting import deal_costs as dc
from am_accounting import deals as dl
from am_accounting import portfolio as pfm
from am_accounting.books import Books, CompanyProfile
from am_accounting.companies import CompanyRegistry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _setup(tmp_path):
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    hold = str(reg.create(CompanyProfile("Maple Holdings Inc.", "Maple Holdings Inc.", "Corporation", role="holding")))
    b = Books.open(hold, create=False)
    try:
        bank = b.bank_accounts()[0].id if b.bank_accounts() else b.add_account(
            "1000", "Chequing", "asset", is_bank=True).id
        feed = b.add_manual_transaction(bank, "2026-10-05", "CHQ 104 APPRAISAL", -4_500_00)   # already in the feed
        b.set_category(feed, None)
    finally:
        b.close()

    def create_company(name, **kw):
        return str(reg.create(CompanyProfile(name, name, kw.get("entity_type", ""), role="operating")))

    d = dl.Deal("45 Elm St", stage="Under Contract", price_cents=4_000_000_00, equity_cents=1_000_000_00,
                closing_date="2026-12-15")
    dl.save(pf, d)
    return pf, hold, bank, feed, d, create_company


def _bal(path, name):
    b = Books.open(path, create=False)
    try:
        a = next((a for a in b.accounts() if a.name == name), None)
        return (b.account_balance(a.id) if a else None), b.check_balanced()
    finally:
        b.close()


def test_record_close_and_settle(tmp_path):
    pf, hold, bank, feed, d, create_company = _setup(tmp_path)
    acct = "Deal Deposits & Costs – 45 Elm St"
    dep = dc.DealCost("Deposit", "deposit", 200_000_00, "2026-10-01", hold, "Maple Holdings Inc.", "Smith LLP in trust")
    appr = dc.DealCost("Appraisal", "cost", 4_500_00, "2026-10-04", hold, "Maple Holdings Inc.", "Acme Appraisals")
    legal = dc.DealCost("Legal", "cost", 12_000_00, "2026-10-20", hold, "Maple Holdings Inc.", "Smith LLP")
    for c in (dep, appr, legal):
        dc.save(pf, d.id, c)
    dc.record(pf, d.name, dep, "bank", bank_id=bank, deal_id=d.id)
    b = Books.open(hold, create=False)
    try:
        cands = dc.candidates(b, appr)
    finally:
        b.close()
    assert [t.id for t in cands] == [feed]
    dc.record(pf, d.name, appr, "matched", tx_id=feed, deal_id=d.id)
    dc.record(pf, d.name, legal, "owing", deal_id=d.id)
    with pytest.raises(ValueError):
        dc.record(pf, d.name, legal, "owing", deal_id=d.id)            # only once
    assert _bal(hold, acct) == (216_500_00, True)
    assert [c.status for c in dc.costs(pf, d.id)] == ["Paid from the bank", "Matched to a bank payment", "Owing (AP)"]
    # taking one out and putting it back
    dc.unrecord(pf, d.id, legal)
    assert _bal(hold, acct)[0] == 204_500_00 and not legal.recorded
    dc.record(pf, d.name, legal, "owing", deal_id=d.id)
    p = dc.prepaid(pf, d.id)
    assert len(p) == 1 and (p[0].deposit, p[0].costs, p[0].total) == (200_000_00, 16_500_00, 216_500_00)

    # closing: the deposit and costs count as paid, the new company owes them back
    s = cl.default_setup(dl.deal(pf, d.id), pf)
    assert s.prepaid_total == 216_500_00 and ("Appraisal", 4_500_00) in s.costs and ("Legal", 12_000_00) in s.costs
    s.company_name = "1001234567 Ontario Inc."
    s.loans[0].lender = "First National"
    s.equity[0].owner = "Pat Lee"
    assert s.cash_left == 0
    hid = cl.close_deal(pf, d.id, s, create_company)
    new = next(h for h in pf.holdings() if h.id == hid).company_path
    assert _bal(new, "Due to Maple Holdings Inc.") == (216_500_00, True)
    assert _bal(new, "Property – 45 Elm St – Appraisal")[0] == 4_500_00
    assert _bal(hold, acct) == (0, True)
    assert _bal(hold, "Due from 1001234567 Ontario Inc.")[0] == 216_500_00
    assert {c.settled for c in dc.costs(pf, d.id)} == {"closed"} and dc.prepaid(pf, d.id) == []
    assert pfm.entered_sources_uses(pf, hid)["Other sources"] == 216_500_00
    pf.close()


def test_write_off_a_dead_deal(tmp_path):
    pf, hold, bank, _feed, d, _cc = _setup(tmp_path)
    dep = dc.DealCost("Deposit", "deposit", 50_000_00, "2026-10-01", hold, "Maple Holdings Inc.")
    esa = dc.DealCost("Phase I ESA", "cost", 3_000_00, "2026-10-02", hold, "Maple Holdings Inc.", "GeoTest")
    for c in (dep, esa):
        dc.record(pf, d.name, c, "bank", bank_id=bank, deal_id=d.id)
    assert dc.write_off(pf, d.id, d.name, "2026-11-01") == 3_000_00          # the deposit is coming back
    assert _bal(hold, "Abandoned Deal Costs") == (3_000_00, True)
    assert _bal(hold, "Deal Deposits & Costs – 45 Elm St")[0] == 50_000_00
    assert dc.write_off(pf, d.id, d.name, "2026-11-02", include_deposits=True) == 50_000_00
    assert _bal(hold, "Abandoned Deal Costs")[0] == 53_000_00
    assert [c.status for c in dc.costs(pf, d.id)] == ["Written off", "Written off"]
    with pytest.raises(ValueError):
        dc.unrecord(pf, d.id, dc.costs(pf, d.id)[0])
    pf.close()


def test_deposits_and_costs_tab(app, tmp_path, monkeypatch):
    from am_accounting.gui import deal_costs_tab as dct
    from am_accounting.gui import deals_tab as dt

    pf, hold, bank, feed, d, _cc = _setup(tmp_path)
    monkeypatch.setattr(dct, "error", lambda *a: (_ for _ in ()).throw(AssertionError(a[1:])))
    monkeypatch.setattr(dct, "confirm", lambda *a: True)
    monkeypatch.setattr(dct, "info", lambda *a: None)
    comps = lambda: [("Maple Holdings Inc.", hold, "holding")]                # noqa: E731
    dlg = dt.DealDialog(pf, dl.deal(pf, d.id), companies=comps)
    tab = dlg.costs
    assert dlg.tabs.tabText(3) == "Deposits && Costs"
    tab._usual()
    assert tab.table.rowCount() == len(dc.STARTERS)
    assert tab.table.cellWidget(0, 5).currentData() == hold                    # the only company, picked
    tab.table.cellWidget(1, 3).setValue(4_500)                                # the appraisal
    tab.table.item(1, 2).setText("Acme Appraisals")
    tab.table.cellWidget(1, 4).setDate(dt.QDate(2026, 10, 4))
    tab.table.selectRow(1)

    def pick_match(self):
        assert self.r_match.isChecked() and self.match.rowCount() == 1        # the feed payment, offered first
        self._ok()
        return dct.QDialog.DialogCode.Accepted

    monkeypatch.setattr(dct.RecordCostDialog, "exec", pick_match)
    tab._record()
    assert tab.table.item(1, 6).text() == "Matched to a bank payment" and not tab.table.cellWidget(1, 3).isEnabled()
    assert "In the books, waiting for closing <b>$4,500.00" in tab.totals.text()
    tab.table.selectRow(5)
    tab._remove()                                                             # lender fee: not needed
    dlg.save()
    saved = dc.costs(pf, d.id)
    assert len(saved) == len(dc.STARTERS) - 1 and saved[1].how == "matched" and saved[1].vendor == "Acme Appraisals"
    assert _bal(hold, "Deal Deposits & Costs – 45 Elm St")[0] == 4_500_00
    # the close window counts it as paid before closing
    s = cl.default_setup(dl.deal(pf, d.id), pf)
    assert s.prepaid_total == 4_500_00
    pf.close()
    assert date.today()
