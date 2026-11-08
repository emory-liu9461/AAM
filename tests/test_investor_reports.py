from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import investor_reports as ir
from am_accounting import portfolio as pfm
from am_accounting.books import Books


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _setup(tmp_path):
    b = Books.open(tmp_path / "elm.amdb", create=True)
    cash = b.account_by_code("1000").id
    rent = b.add_account("4010", "Rental Income", "income", "operating").id
    hydro = b.add_account("6410", "Hydro", "expense", "operating").id
    dist = b.add_account("3910", "Distributions to Owners", "equity", "draw").id
    b.add_journal_entry("2026-05-15", "rent", [(cash, 30_000_00, 0), (rent, 0, 30_000_00)])
    b.add_journal_entry("2026-05-20", "hydro", [(hydro, 5_000_00, 0), (cash, 0, 5_000_00)])
    b.add_journal_entry("2026-03-31", "Q1 distribution", [(dist, 10_000_00, 0), (cash, 0, 10_000_00)])
    b.add_journal_entry("2026-06-30", "Q2 distribution", [(dist, 20_000_00, 0), (cash, 0, 20_000_00)])
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Elm Street", acquired="2025-01-01", company_path=str(tmp_path / "elm.amdb"))
    pf.save_holding(h)
    pf.add_valuation(h.id, pfm.Valuation("2026-06-30", 1_600_000_00, "Appraisal"))
    for name, co, amt, when in (("Pat Lee", "", 600_000_00, "2025-01-01"), ("Sam Roy", "Roy Family Trust",
                                                                          200_000_00, "2025-01-01"),
                                ("Ana Diaz", "", 200_000_00, "2026-04-15")):
        pfm.save_shareholder(pf, h.id, pfm.Shareholder(name, co, amt, funded_cents=amt, funded_date=when))
    oak = pfm.Holding("Oak Plaza", acquired="2025-06-01")
    pf.save_holding(oak)
    pfm.save_shareholder(pf, oak.id, pfm.Shareholder("Pat Lee", "", 100_000_00, funded_cents=100_000_00,
                                                     funded_date="2025-06-01"))
    pf.add_valuation(oak.id, pfm.Valuation("2026-06-30", 150_000_00, "Appraisal"))
    return pf, h, oak


def test_quarterly_statements(tmp_path):
    assert ir.quarter_bounds(2026, 2) == (date(2026, 4, 1), date(2026, 6, 30))
    assert ir.quarter_bounds(2026, 4) == (date(2026, 10, 1), date(2026, 12, 31))
    assert ir.last_quarter(date(2026, 11, 8)) == (2026, 3) and ir.last_quarter(date(2026, 2, 1)) == (2025, 4)
    pf, h, oak = _setup(tmp_path)
    sts = {s.label: s for s in ir.statements(pf, 2026, 2)}
    assert set(sts) == {"Pat Lee", "Sam Roy (Roy Family Trust)", "Ana Diaz"}
    pat = sts["Pat Lee"]
    assert [p.company for p in pat.positions] == ["Elm Street", "Oak Plaza"]
    elm = pat.positions[0]
    # Q1 distribution $10,000 shared by Pat and Sam only (Ana paid in later): 75% to Pat
    # Q2 distribution $20,000 shared by all three: 60% to Pat
    assert (elm.opening, elm.contributed, elm.distributed) == (600_000_00 - 7_500_00, 0, 12_000_00)
    assert elm.closing == 600_000_00 - 19_500_00 and elm.total_distributed == 19_500_00
    assert elm.pct == pytest.approx(0.6) and elm.value == 960_000_00                 # 60% of $1.6M, no GP terms
    assert elm.moic == pytest.approx((19_500_00 + 960_000_00) / 600_000_00)
    ana = sts["Ana Diaz"].positions[0]
    assert (ana.opening, ana.contributed, ana.distributed, ana.closing) == (0, 200_000_00, 4_000_00, 196_000_00)
    assert ana.irr is None and sts["Ana Diaz"].irr is None                  # under a year: no annual rate
    cq = pat.companies[h.id]
    assert (cq.revenue, cq.opex, cq.noi, cq.equity_value) == (30_000_00, 5_000_00, 25_000_00, 1_600_000_00)
    assert pat.total("paid_in") == 700_000_00 and pat.irr is not None
    page = ir.statement_html(pat, "Maple Asset Management")
    for text in ("Investor statement – Q2 2026", "April 1, 2026 to June 30, 2026", "Pat Lee", "Elm Street",
                 "Oak Plaza", "$960,000.00", "Maple Asset Management"):
        assert text in page
    assert ir.file_name(pat) == "Investor Statement Q2 2026 - Pat Lee.pdf"
    only_oak = ir.statements(pf, 2026, 2, [oak.id])
    assert [s.label for s in only_oak] == ["Pat Lee"]
    pf.close()


def test_investor_reports_window(app, tmp_path, monkeypatch):
    from am_accounting.gui import investor_reports_dialog as ird

    pf, h, _oak = _setup(tmp_path)
    monkeypatch.setattr(ird, "info", lambda *a: None)
    dlg = ird.InvestorReportsDialog(pf, holding_id=h.id, sender="Maple AM")
    dlg.year.setValue(2026)
    dlg.q.setCurrentIndex(1)
    assert dlg.table.rowCount() == 3 and "Investor statement – Q2 2026" in dlg.view.toPlainText()
    out = dlg.save_all(str(tmp_path / "out"))
    assert len(out) == 3 and all(p.exists() and p.stat().st_size > 1000 for p in out)
    pf.close()
