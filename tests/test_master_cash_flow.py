from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import master_cash_flow as mcf
from am_accounting import personal_accounts as pacc
from am_accounting import personal_finance as pfi
from am_accounting import portfolio as pfm


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_next_twelve_months_everything_together(app, tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", "Real estate", ownership_pct=50)
    pf.save_holding(h)
    pfm.save_projections(pf, h.id, [pfm.Projection(2027, revenue=1_200_000_00, opex=600_000_00, nonop=120_000_00,
                                                   capex=120_000_00, fin_out=120_000_00)])
    lines = pfi.budget_lines(pf, 2026)
    for ln in lines:
        ln.budget = {"Salary / employment": 6_000_00, "Groceries": 1_000_00, "Housing (mortgage / rent)": 3_000_00,
                     pfi.BUSINESS: 2_000_00}.get(ln.category, 0)
    pfi.save_budget_lines(pf, lines, 2026)
    a = pacc.load(pf)
    a.banks.append(pacc.BankAccount("Chequing", balance=5_000_00, id=a.new_id()))
    pacc.save(pf, a)
    d = mcf.build(pf, today=date(2026, 11, 15))
    assert d.months[0] == (2026, 12) and d.months[-1] == (2027, 11) and len(d.months) == 12
    biz = d.businesses[0]
    assert biz.by_month[0] == 0 and biz.basis == "projections"       # 2026: no projections or books
    assert biz.by_month[1] == round(240_000_00 * 0.5 / 12)            # 2027 projections, at our 50%
    assert d.personal_income[0] == 6_000_00 and d.personal_spend[0] == 4_000_00   # business income left out
    assert d.distributions[0] == 2_000_00                             # memo only
    assert d.net[0] == 2_000_00 and d.cash_start == 5_000_00 and d.cash_end[0] == 7_000_00

    from am_accounting.gui.master_cash_flow_page import MasterCashFlowPage

    page = MasterCashFlowPage(pf)
    page.fill()
    t = page.table
    labels = [t.item(r, 0).text() for r in range(t.rowCount())]
    assert "Cash flow from businesses" in labels and "Personal cash flow" in labels and "Net cash flow" in labels
    r = labels.index("Cash at the end of the month")
    assert t.item(r, 1).text() == "$7,000.00" and t.horizontalHeaderItem(1).text() == "Dec 2026"
    pf.close()
