import os
import time
from datetime import date
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import deal_folders as df
from am_accounting import deals as dl
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _touch(p: Path, text="x", age=0):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def test_layout_follows_the_standard():
    ctx = df.Context("45 Elm St", ["Enbridge", "City of Toronto"], ["101", "102"], [2025, 2026], 2)
    rels = {it.rel for it in df.layout(ctx) if it.is_folder}
    for r in ("Underwriting/Presentation/Archive", "Underwriting/Legal and Admin/Purchase and Sales Agreement/Initial "
              "Letter of Intent", "Underwriting/Contracts and Quotes/Insurance/Quote",
              "Underwriting/Pictures/Original (Provided) Photos",
              "Asset Management/Accounting/Financing Documents/Second Mortgage/Commitment Letter and Term Sheet/Executed",
              "Asset Management/Accounting/Invoices/Operating Invoices/Enbridge Invoices/Invoices from the Last 12 Months",
              "Asset Management/Accounting/Invoices/Construction Invoices/AAM Invoices/2026/03 Mar",
              "Asset Management/Accounting/Invoices/Construction Invoices/Third-party Invoices/Unit 101/Labour",
              "Asset Management/Accounting/Invoices/Construction Invoices/Third-party Invoices/Common Area/Roof/Material",
              "Asset Management/Legal and Admin/Investor Documents/Term Sheet/Signed",
              "Asset Management/Property/Initial DD Documents/Consultant Reports/BCA/Reliance Letter",
              "Asset Management/Property/Contracts - Agreements/City of Toronto/Agreements",
              "Asset Management/Property/Leasing/Leases/Units/N-11, N-12 etc"):
        assert r in rels, r
    named = [it for it in df.layout(ctx) if it.node.kind == "file"]
    assert "45 Elm St AAM Invoices.xlsx" in [it.label for it in named]
    under = df.layout(ctx, asset_management=False)
    assert all(it.rel.startswith("Underwriting") for it in under)
    signed = next(it for it in df.layout(ctx) if it.rel.endswith("Term Sheet/Signed"))
    assert signed.naming == "Entity Signing Name - 45 Elm St, Term Sheet"
    assert df.street_of(dl.Deal("Elm", address="45 Elm St, Hamilton ON")) == "45 Elm St"


def test_set_up_check_archive_and_close(tmp_path):
    ctx = df.Context("45 Elm St", years=[2026])
    f = tmp_path / "45 Elm St"
    assert df.create(f, ctx) > 20 and df.create(f, ctx) == 0                 # only what's missing
    assert not (f / "Asset Management").exists()
    done, total = df.summary(df.check(f, ctx))
    assert done == 0 and total > 5
    model = f / "Underwriting/Financial Model"
    _touch(model / "Elm Model 2.0.xlsx", age=500)
    _touch(model / "Elm Model 3.0.xlsx", age=1000)                            # newer version, older file date
    _touch(model / "Elm Model 3.0 (1).pdf")
    _touch(f / "Underwriting/Legal and Admin/Purchase and Sales Agreement/APS signed.pdf")
    _touch(f / "Underwriting/Legal and Admin/Purchase and Sales Agreement/Initial Letter of Intent/LOI.pdf")
    _touch(f / "Underwriting/Legal and Admin/Corporate Documents/Articles of Incorporation/Articles.pdf")
    st = {s.item.rel + "|" + s.item.label: s for s in df.check(f, ctx)}
    assert st["Underwriting/Financial Model|Live file"].ok
    assert st["Underwriting/Presentation|Live file"].missing
    assert st["Underwriting/Legal and Admin/Purchase and Sales Agreement/Initial Letter of Intent|Initial Letter of "
              "Intent"].ok
    assert df.doc_key("Elm Model 2.0.xlsx") == df.doc_key("elm model v3 (1).xlsx") != df.doc_key("Elm Model.pdf")
    plan = df.archive_plan(f, ctx)
    assert [(p.name, d.parent.name) for p, d in plan] == [("Elm Model 2.0.xlsx", "Archive")]
    assert df.archive(plan) == 1 and (model / "Archive/Elm Model 2.0.xlsx").exists()
    assert (model / "Elm Model 3.0.xlsx").exists() and df.archive_plan(f, ctx) == []
    # closing: Asset Management set up, the underwriting's legal documents copied over
    assert df.create(f, ctx, asset_management=True) > 50
    assert df.transfer_on_closing(f) == 3
    assert (f / "Asset Management/Legal and Admin/APS/Initial Letter of Intent/LOI.pdf").exists()
    assert (f / "Asset Management/Legal and Admin/Corporation Document/Articles of Incorporation/Articles.pdf").exists()
    assert (f / "Underwriting/Legal and Admin/Purchase and Sales Agreement/APS signed.pdf").exists()   # copied
    assert df.transfer_on_closing(f) == 0
    it = next(i for i in df.layout(ctx) if i.rel.endswith("Common Area/Roof/Material"))
    src = _touch(tmp_path / "scan.pdf")
    name = df.suggested_name(it, src, vendor="Acme Roofing", on=date(2024, 12, 21))
    assert name == "45 Elm St, Acme Roofing, Dec 21, 2024.pdf"
    dest = df.file_into(f, it, src, name)
    assert dest.parent.name == "Material" and df.file_into(f, it, src, name).name.endswith("(2).pdf")


def test_deal_settings_and_closing_hook(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("45 Elm St", address="45 Elm St, Hamilton")
    dl.save(pf, d)
    assert df.folder_of(pf, d) is None
    df.set_root(pf, str(tmp_path / "Deals"))
    assert df.folder_of(pf, d) == tmp_path / "Deals" / "45 Elm St"
    assert df.on_closing(pf, d) == (0, 0)                                    # no folder yet: nothing done
    df.create(df.folder_of(pf, d), df.context(pf, d))
    d.stage, d.closing_date = "Closed", "2026-01-15"
    made, copied = df.on_closing(pf, d)
    assert made > 50 and copied == 0
    df.set_folder_of(pf, d, tmp_path / "elsewhere")
    assert df.folder_of(pf, d) == tmp_path / "elsewhere"
    pf.close()


def test_deal_folder_tab(app, tmp_path, monkeypatch):
    from am_accounting.gui import deal_folder_tab as dft
    from am_accounting.gui import deals_tab as dt

    monkeypatch.setattr(dft, "confirm", lambda *a: True)
    monkeypatch.setattr(dft, "info", lambda *a: None)
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    df.set_root(pf, str(tmp_path / "Deals"))
    asked = []
    real = dft.ask_where
    monkeypatch.setattr(dft, "ask_where", lambda *a, **k: asked.append(1) or real(*a, choose=lambda: "here"))
    dlg = dt.DealDialog(pf, companies=lambda: [])
    dlg.name.setText("Oak Plaza")
    dlg.save()
    f = tmp_path / "Deals" / "Oak Plaza"
    assert asked == [1]                                                         # asked where its folder goes
    assert (f / "Underwriting/Presentation/Archive").is_dir()                   # made when the deal was added
    dlg = dt.DealDialog(pf, dl.deal(pf, dlg.deal.id))
    tab = dlg.folder_tab
    assert dlg.tabs.tabText(dlg.tabs.indexOf(tab)) == "Deal Folder"
    assert "required items still needed" in tab.where.text()
    assert tab.tree.topLevelItemCount() == 1 and tab.tree.topLevelItem(0).text(0) == "Underwriting"
    pres = tab.tree.topLevelItem(0).child(0)
    tab.tree.setCurrentItem(pres)
    src = _touch(tmp_path / "Deck v2.pptx", age=100)
    _touch(tmp_path / "Deck v3.pptx")
    assert tab.add_files([str(src), str(tmp_path / "Deck v3.pptx")]) == 2
    assert tab.archive(ask=False) == 1 and (f / "Underwriting/Presentation/Archive/Deck v2.pptx").exists()
    pres = tab.tree.currentItem()
    assert pres.text(0) == "Presentation"                                       # still selected after the refresh
    live = next(pres.child(i) for i in range(pres.childCount()) if "Live file" in pres.child(i).text(0))
    assert live.text(1) == "✓"
    tab.asset_management()
    assert (f / "Asset Management/Legal and Admin/APS").is_dir()
    assert tab.tree.topLevelItemCount() == 2
    pf.close()


def test_closed_deal_links_the_accounting(tmp_path):
    from am_accounting import closing as cl
    from am_accounting import doc_folder
    from am_accounting import inbox
    from am_accounting import receivables as rx
    from am_accounting import workspace as ws
    from am_accounting.books import Books, CompanyProfile
    from am_accounting.companies import CompanyRegistry

    reg = CompanyRegistry(tmp_path / "c.json", tmp_path / "Companies")
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    df.set_root(pf, str(tmp_path / "Deals"))
    d = dl.Deal("45 Elm St", address="45 Elm St, Hamilton", stage="Under Contract", price_cents=1_000_000_00,
                equity_cents=300_000_00, closing_date="2026-01-15")
    dl.save(pf, d)
    f = df.folder_of(pf, d)
    df.create(f, df.context(pf, d))
    s = cl.default_setup(dl.deal(pf, d.id), pf)
    s.company_name = "1001234567 Ontario Inc."
    s.loans[0].lender = "First National"
    s.equity[0].owner = "Pat Lee"

    def create_company(name, **kw):
        return str(reg.create(CompanyProfile(name, name, kw.get("entity_type", ""), role="operating")))

    cl.close_deal(pf, d.id, s, create_company)
    d = dl.deal(pf, d.id)
    acct = f / "Asset Management" / "Accounting"
    assert df.linked(pf, d)
    b = Books.open(df.company_path(pf, d), create=False)
    try:
        cfg = ws.get_config(b)
        assert cfg.deal and cfg.path == acct
        assert Path(inbox.get_config(b).folder) == acct / "Banking/Monthly Statements"
        live = acct / "QuickBooks File" / "1001234567 Ontario Inc.amdb"
        assert live.exists()                                                 # the latest copy of the books
        # financial statements: the latest outside, the ones before in Archive
        p1, p2 = ws._period_of("monthly", date(2026, 1, 10)), ws._period_of("monthly", date(2026, 2, 10))
        cfg.formats = ["xlsx"]
        r1 = ws.save_period(b, p1, cfg=cfg)
        assert not r1.errors and (acct / "Financial Statements/Income Statement/Income Statement - January 2026.xlsx"
                                  ).exists()
        assert (acct / "Financial Statements/Balance Sheet").is_dir()
        assert any((acct / "Financial Statements/Trial Balance").glob("*.xlsx"))
        ws.save_period(b, p2, cfg=cfg)
        inc = acct / "Financial Statements/Income Statement"
        assert {x.name for x in inc.glob("*.xlsx")} == {"Income Statement - February 2026.xlsx",
                                                        "Income Statement - Year to Date 28 Feb 2026.xlsx"}
        assert (inc / "Archive/2026/Income Statement - January 2026.xlsx").exists()
        # backups in QuickBooks File/Archive
        assert ws.backup_books(b, cfg, stamp="2026-02").parent == acct / "QuickBooks File/Archive"
        # a bill filed by vendor, named "## Street Name, Vendor, Month, Invoice"
        exp = next(a.id for a in b.accounts() if a.type == "expense")
        ap = rx.control_account(b, "bill").id
        doc = rx.add_document(b, "bill", "Enbridge", "2026-02-03", "2026-03-03", 412_00, exp, number="GAS-77", control_id=ap)
        src = tmp_path / "scan.pdf"
        src.write_bytes(b"%PDF bill")
        filed = doc_folder.attach_file(b, doc, src)
        assert filed == (acct / "Invoices/Operating Invoices/Enbridge Invoices/Invoices from the Last 12 Months"
                         / "45 Elm St, Enbridge, February 2026, Invoice GAS-77.pdf")
        assert doc_folder.drop_dir(doc_folder.get_config(b)) == acct / "Invoices/Drop files here"
        assert ws.SUBDIRS["deal"]["year_end"] == "Taxes"
    finally:
        b.close()
    rows = {s_.item.rel: s_ for s_ in df.check(f, df.context(pf, d), asset_management=True)}
    assert rows["Asset Management/Accounting/QuickBooks File"].files >= 1
    pf.close()


def test_asked_where_a_new_deal_goes(app, tmp_path):
    from am_accounting.gui import deal_folder_tab as dft

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    d = dl.Deal("Birch Court")
    dl.save(pf, d)
    assert dft.ask_where(None, pf, d, choose=lambda: "later") is None and df.root(pf) == ""
    # no Deals folder yet: picking it sets it for every deal from now on
    f = dft.ask_where(None, pf, d, choose=lambda: "here", pick_dir=lambda *a: str(tmp_path / "Deals"))
    assert f == tmp_path / "Deals" / "Birch Court" and df.root(pf) == str(tmp_path / "Deals")
    assert (f / "Underwriting/Financing/Term Sheet").is_dir()
    # a folder of its own (e.g. one that already exists)
    e = dl.Deal("Cedar Lane")
    dl.save(pf, e)
    own = tmp_path / "OneDrive" / "Cedar"
    g = dft.ask_where(None, pf, e, choose=lambda: "other", pick_dir=lambda *a: str(own))
    assert g == own and df.folder_of(pf, e) == own and (own / "Underwriting/Pictures").is_dir()
    tab = dft.DealFolderTab(pf, e)
    tab.pick_own(str(tmp_path / "Elsewhere"))
    assert df.folder_of(pf, e) == tmp_path / "Elsewhere"
    pf.close()
