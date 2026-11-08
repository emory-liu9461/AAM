from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import hst
from am_accounting import year_end as ye
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _books(tmp_path):
    b = Books.open(tmp_path / "co.amdb", create=True)
    cash = b.account_by_code("1000").id
    tax = b.account_by_code("2200").id                       # Sales Tax Payable (standard chart)
    rent = b.add_account("4010", "Commercial Rent", "income", "operating").id
    res = b.add_account("4020", "Residential Rent", "income", "operating").id
    rep = next(a.id for a in b.accounts() if a.name == "Repairs & Maintenance")
    # Q1: commercial rent $10,000 + HST $1,300; residential rent $5,000 (exempt); repairs $2,000 + HST $260
    b.add_journal_entry("2026-02-01", "Unit 1 rent", [(cash, 11_300_00, 0), (rent, 0, 10_000_00), (tax, 0, 1_300_00)])
    b.add_journal_entry("2026-02-01", "Residential", [(cash, 5_000_00, 0), (res, 0, 5_000_00)])
    b.add_journal_entry("2026-03-05", "Plumber", [(rep, 2_000_00, 0), (tax, 260_00, 0), (cash, 0, 2_260_00)])
    # paid the Q4 balance to the CRA in January — not part of the Q1 return
    b.import_transactions(cash, [ParsedTransaction(date(2026, 1, 20), "RECEIVER GENERAL GST/HST", -500_00)])
    t = b.transactions()[0]
    b.set_category(t.id, tax, source="manual")
    b.close()
    return str(tmp_path / "co.amdb")


def test_hst_return(tmp_path):
    b = Books.open(_books(tmp_path), create=False)
    try:
        assert [a.name for a in hst.hst_accounts(b)] == ["Sales Tax Payable"]
        ps = hst.periods("quarterly", 1, date(2026, 5, 10), back=3)
        assert ps[0] == (date(2026, 4, 1), date(2026, 6, 30)) and ps[1] == (date(2026, 1, 1), date(2026, 3, 31))
        assert hst.periods("annual", 7, date(2026, 5, 10), back=1) == [(date(2025, 7, 1), date(2026, 6, 30))]
        r = hst.compute(b, date(2026, 1, 1), date(2026, 3, 31))
        assert (r.line101, r.taxable_revenue, r.line103, r.line106, r.line109) == (
            15_000_00, 10_000_00, 1_300_00, 260_00, 1_040_00)
        assert r.paid == 500_00 and r.owing and r.balance_end == 1_040_00 - 500_00
        assert len(r.lines) == 3
        hst.mark_filed(b, r, date(2026, 4, 20))
        assert hst.compute(b, date(2026, 1, 1), date(2026, 3, 31)).filed_on == "2026-04-20"
        hst.unmark_filed(b, r)
        assert hst.filed(b) == {}
        s = hst.Settings("monthly", [], "123456789 RT0001")
        hst.save_settings(b, s)
        assert hst.settings(b).frequency == "monthly" and hst.settings(b).number == "123456789 RT0001"
        with pytest.raises(ValueError):
            hst.save_settings(b, hst.Settings("weekly"))
        hst.write_xlsx(b, tmp_path / "h.xlsx", [r])
        assert (tmp_path / "h.xlsx").exists()
    finally:
        b.close()


def test_hst_accounts_created(tmp_path):
    b = Books.open(tmp_path / "x.amdb", create=True)
    try:
        for a in hst.hst_accounts(b, hst.Settings()):
            b.conn.execute("UPDATE accounts SET name='Other Payable' WHERE id=?", (a.id,))
        b._invalidate()
        assert hst.hst_accounts(b, hst.Settings()) == []
        ids = hst.ensure_accounts(b)
        assert [b.account(i).name for i in ids] == ["HST Collected", "HST Paid (Input Tax Credits)"]
    finally:
        b.close()


def test_year_end_package(tmp_path):
    import openpyxl

    path = _books(tmp_path)
    b = Books.open(path, create=False)
    try:
        cash = b.account_by_code("1000").id
        bldg = b.add_account("1610", "Building", "asset", "fixed_asset").id
        land = b.add_account("1620", "Land", "asset", "fixed_asset").id
        appl = b.add_account("1630", "Appliances", "asset", "fixed_asset").id
        b.add_journal_entry("2025-01-01", "Purchase", [(bldg, 1_000_000_00, 0), (land, 400_000_00, 0),
                                                       (cash, 0, 1_400_000_00)], kind="opening")
        b.add_journal_entry("2026-06-01", "Fridges", [(appl, 10_000_00, 0), (cash, 0, 10_000_00)])
        assert ye.guess_class("Building") == "1" and ye.guess_class("Land") == "none"
        assert ye.fiscal_year(b, date(2026, 12, 31)) == (date(2026, 1, 1), date(2026, 12, 31))
        assert ye.last_fiscal_year(b, date(2026, 11, 8)) == (date(2025, 1, 1), date(2025, 12, 31))
        rows = {r.name: r for r in ye.asset_rows(b, date(2026, 1, 1), date(2026, 12, 31))}
        assert rows["Building"].opening == 1_000_000_00 and rows["Appliances"].additions == 10_000_00
        cca = {c.cca_class: c for c in ye.cca_schedule(b, date(2026, 1, 1), date(2026, 12, 31), rule="half-year")}
        assert set(cca) == {"1", "8"}                       # land isn't depreciable
        assert cca["1"].cca == 40_000_00 and not cca["1"].ucc_entered
        assert cca["8"].cca == 1_000_00                     # half-year rule: 20% of half the addition
        assert ye.cca_schedule(b, date(2026, 1, 1), date(2026, 12, 31), rule="aiip")[1].cca == 2_000_00
        pkg = ye.build(b, tmp_path / "out", date(2026, 12, 31), rule="half-year",
                       opening_ucc={"1": 960_000_00})
        names = sorted(p.name for p in pkg.files)
        assert names == ["01 Trial Balance.xlsx", "02 General Ledger.xlsx", "03 Income Statement.xlsx",
                         "04 Balance Sheet.xlsx", "05 AR & AP at year end.xlsx", "06 Fixed Assets & CCA.xlsx",
                         "07 GST-HST.xlsx", "Read Me.txt"]
        assert pkg.folder.name.startswith("Year-End FY2026")
        assert ye.saved_ucc(b, date(2026, 12, 31))["1"] == 960_000_00 - 38_400_00
        nxt = {c.cca_class: c for c in ye.cca_schedule(b, date(2027, 1, 1), date(2027, 12, 31))}
        assert nxt["1"].ucc_entered and nxt["1"].opening_ucc == 921_600_00     # carried from last year
        gl = openpyxl.load_workbook(pkg.folder / "02 General Ledger.xlsx").active
        assert any(r[0].value and "Building" in str(r[0].value) for r in gl.iter_rows())
        readme = (pkg.folder / "Read Me.txt").read_text()
        assert "Trial balance: balanced" in readme and "06 Fixed Assets & CCA.xlsx" in readme
    finally:
        b.close()


def test_year_end_and_hst_windows(app, tmp_path, monkeypatch):
    from am_accounting.gui import hst_dialog as hd
    from am_accounting.gui import year_end_dialog as yd

    b = Books.open(_books(tmp_path), create=False)
    try:
        monkeypatch.setattr(hd, "info", lambda *a: None)
        monkeypatch.setattr(yd, "info", lambda *a: None)
        h = hd.HstDialog(b, today=date(2026, 5, 10))
        assert "Q" not in h.period.currentText() and h.period.currentData()[0] == date(2026, 1, 1)
        assert "$1,040.00" in h.figures.text() and h.table.rowCount() == 3
        h.toggle_filed()
        assert "filed" in h.period.currentText() and h.filed_btn.text() == "Not Filed After All"
        y = yd.YearEndDialog(b, today=date(2027, 2, 1))
        assert y.year.currentData() == date(2026, 12, 31)
        pkg = y.make(str(tmp_path / "pkg"))
        assert pkg is not None and (pkg.folder / "Read Me.txt").exists()
    finally:
        b.close()
