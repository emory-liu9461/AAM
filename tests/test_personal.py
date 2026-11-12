"""Personal folder and net worth statement."""

from datetime import date
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import lender_package as lpk
from am_accounting import personal as pers
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pf(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("45 Elm St", "Real estate", ownership_pct=50, acquired="2024-01-01", location="Oshawa, ON")
    pf.save_holding(h)
    pf.add_valuation(h.id, pfm.Valuation("2026-06-30", 2_200_000_00, "Appraisal", company_value_cents=5_000_000_00,
                                         debt_cents=2_800_000_00))
    pfm.save_loan(pf, h.id, pfm.Loan("First National", "Mortgage", 2_800_000_00, 2_750_000_00, 5.0))
    gone = pfm.Holding("Old Mill", "Real estate", status="exited")
    pf.save_holding(gone)
    return pf, h


def test_folder_layout(tmp_path):
    f = tmp_path / "Personal"
    assert pers.create(f) > 0 and pers.create(f) == 0
    for p in ("Net Worth Statements/Archive", "Income Tax/T1 Returns", "Income Tax/T4 Slips",
              "Income Tax/Notices of Assessment (NOA)", "Identification", "Credit Reports"):
        assert (f / p).is_dir()
    for gone in ("Résumé", "Personal Guarantees"):
        assert not (f / gone).exists()


def test_net_worth(tmp_path):
    pf, h = _pf(tmp_path)
    s = pers.Saved("Pat Lee", [pers.Entry("Cash and bank accounts", "TD chequing", 50_000_00),
                               pers.Entry("TFSA", "", 0)],
                   [pers.Entry("Credit cards", "Visa", 5_000_00)])
    pers.save(pf, s)
    assert pers.load(pf) == s                                           # kept for next time
    props = pers.properties(pf, as_of=date(2026, 11, 1))
    assert [p.name for p in props] == ["45 Elm St"]                     # exited holdings aren't owned now
    p = props[0]
    assert (p.value, p.debt, p.share_pct, p.lenders) == (5_000_000_00, 2_750_000_00, 50, "First National")
    assert p.my_equity == 1_125_000_00
    st = pers.statement(pf, as_of=date(2026, 11, 1))
    assert [e.kind for e in st.assets] == ["Cash and bank accounts"]   # empty lines are left out
    assert st.total_assets == 2_550_000_00 and st.total_liabilities == 1_380_000_00
    assert st.net_worth == 1_170_000_00
    s.shares = {h.id: 25.0}                                             # you own half the holding company
    st = pers.statement(pf, s, date(2026, 11, 1))
    assert st.properties[0].my_equity == 562_500_00
    page = pers.html(st)
    assert "Pat Lee" in page and "Schedule of Real Estate Owned" in page and "guarantee" not in page.lower()
    out = tmp_path / "nw.xlsx"
    pers.write_xlsx(st, out)
    from openpyxl import load_workbook

    wb = load_workbook(out)
    vals = {r[0]: r[1] for r in wb["Net Worth"].iter_rows(values_only=True) if r and r[0]}
    assert vals["NET WORTH"] == st.net_worth / 100 and "Real Estate Owned" in wb.sheetnames
    pf.close()


def test_dialog_generates_into_the_personal_folder(app, tmp_path):
    from am_accounting.gui.net_worth_page import NetWorthPage

    pf, h = _pf(tmp_path)
    f = tmp_path / "Me"
    dlg = NetWorthPage(pf, as_of=date(2026, 11, 1))
    assert dlg.re.rowCount() == 1 and dlg.re.item(0, 3).text() == "50"
    dlg.choose_folder(str(f))
    assert pers.folder(pf) == f and (f / "Income Tax/T1 Returns").is_dir()
    dlg.name.setText("Pat Lee")
    dlg.assets.table.item(0, 2).setText("50000")                       # cash
    assert dlg.assets.table.item(0, 2).text() == "$50,000.00"
    dlg.re.item(0, 3).setText("25")
    assert dlg.re.item(0, 4).text() == "$562,500.00"
    old = f / "Net Worth Statements/2025-01-01 Net Worth Statement - Pat Lee.pdf"
    old.write_text("x")
    made = dlg.generate(quiet=True)
    assert [p.name for p in made] == ["2026-11-01 Net Worth Statement - Pat Lee.pdf",
                                      "2026-11-01 Net Worth Statement - Pat Lee.xlsx"]
    assert all(p.exists() and p.stat().st_size > 0 for p in made)
    assert not old.exists() and (f / "Net Worth Statements/Archive" / old.name).exists()   # the earlier one archived
    saved = pers.load(pf)
    assert saved.name == "Pat Lee" and saved.shares == {h.id: 25.0}
    assert saved.assets[0].amount == 50_000_00
    # lender packages take your own documents from the personal folder
    (f / "Income Tax/Notices of Assessment (NOA)/2025 NOA.pdf").write_text("x")
    deal = tmp_path / "Deal"
    (deal / "Underwriting").mkdir(parents=True)
    picks = {pk.doc.name: pk for pk in lpk.candidates(deal, f, pers.statements_folder(pf))}
    assert [c.path.name for c in picks["Personal Tax Returns and NOAs"].files] == ["2025 NOA.pdf"]
    bg = [c.path.name for c in picks["Borrower and Guarantor Information"].files]
    assert made[0].name in bg and old.name not in bg                    # not the archived one
    assert all(c.picked for c in picks["Borrower and Guarantor Information"].files)
    # statements saved to a folder of your choosing
    own = tmp_path / "Bank Stuff" / "NW"
    assert dlg.out_label.text().endswith("Net Worth Statements</b>")
    dlg.choose_output(str(own))
    assert pers.statements_folder(pf) == own and str(own) in dlg.out_label.text()
    dlg.name.setText("Pat Lee")
    made2 = dlg.generate(quiet=True)
    assert [p.parent for p in made2] == [own, own] and all(p.exists() for p in made2)
    picks = {pk.doc.name: pk for pk in lpk.candidates(deal, f, pers.statements_folder(pf))}
    assert made2[0].name in [c.path.name for c in picks["Borrower and Guarantor Information"].files]
    assert any(str(own) in str(c.path) for c in picks["Borrower and Guarantor Information"].files)
    dlg.reset_output()
    assert pers.statements_folder(pf) == f / pers.NET_WORTH
    pf.close()


def test_button_beside_portfolio_summary(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    w.set_mode("assets")
    v = w.asset_view
    assert v.net_worth_btn.text() == "Net Worth"
    v.show_summary()
    assert v.portfolio_bar.isVisibleTo(v)                       # As of, Viewing … with the portfolio
    v.show_net_worth()
    assert v.tabs.currentWidget() is v.net_worth_page and v.net_worth_btn.isChecked()
    assert not v.summary_btn.isChecked() and not v.company_btn.isChecked()
    assert not v.tabs.tabBar().isVisibleTo(v) and not v.portfolio_bar.isVisibleTo(v)
    assert v._company_shown() is None
    v.show_company_pages()                                      # a company's pages: no portfolio controls either
    assert not v.portfolio_bar.isVisibleTo(v) and v.tabs.tabBar().isVisibleTo(v)
    v.show_deals()
    assert not v.portfolio_bar.isVisibleTo(v)
    v.show_summary()
    assert v.portfolio_bar.isVisibleTo(v)
    v.show_net_worth()                                          # opened again: the same page, refreshed
    assert v.tabs.currentWidget() is v.net_worth_page
    w.close()


def test_history_and_charts(app, tmp_path):
    from am_accounting.gui.net_worth_page import NetWorthPage

    pf, h = _pf(tmp_path)
    old = pers.statement(pf, pers.Saved("Pat", [pers.Entry("Cash and bank accounts", "", 10_000_00)]),
                         date(2026, 1, 31))
    pers.record(pf, old)
    page = NetWorthPage(pf)                                       # today: recorded as you type
    page.assets.table.item(0, 2).setText("25000")                 # cash
    hist = pers.history(pf)
    assert [x.date for x in hist] == ["2026-01-31", date.today().isoformat()]
    assert hist[0].cash == 10_000_00 and hist[-1].cash == 25_000_00
    assert hist[-1].by_kind[pers.REAL_ESTATE] == 2_500_000_00 and hist[-1].net_worth == page.statement().net_worth
    page.assets.table.item(0, 2).setText("30000")                 # same day: replaced, not added
    assert len(pers.history(pf)) == 2 and pers.history(pf)[-1].cash == 30_000_00
    assert page.chart_cash.dates == [x.date for x in pers.history(pf)]
    assert page.chart_cash.series[0][1] == [10_000_00, 30_000_00]
    assert [n for n, _v in page.chart_worth.series] == ["Net worth", "Assets", "Liabilities"]
    assert {n for n, _v in page.chart_assets.series} >= {pers.REAL_ESTATE, "Cash and bank accounts"}
    page.right.setCurrentIndex(1)
    page.resize(1200, 900)
    page.show()
    page.grab()                                                   # draws without errors
    past = NetWorthPage(pf, as_of=date(2025, 6, 30))              # a past date isn't recorded
    past.assets.table.item(0, 2).setText("1")
    assert "2025-06-30" not in [x.date for x in pers.history(pf)]
    pf.close()
