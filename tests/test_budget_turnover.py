from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import budget as bg
from am_accounting import operations as op
from am_accounting import portfolio as pfm
from am_accounting import turnover as tv
from am_accounting.books import Books


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _books(tmp_path, year):
    b = Books.open(tmp_path / "op.amdb", create=True)
    rent = b.add_account("4010", "Rental Income", "income", "operating").id
    hydro = b.add_account("6410", "Hydro", "expense", "operating").id
    snow = b.add_account("6420", "Snow Removal", "expense", "operating").id
    interest = b.add_account("6810", "Mortgage Interest", "expense", "other").id
    cash = b.account_by_code("1000").id
    for m in (1, 2, 3):
        d = date(year, m, 15).isoformat()
        b.add_journal_entry(d, "rent", [(cash, 10_000_00, 0), (rent, 0, 10_000_00)])
        b.add_journal_entry(d, "hydro", [(hydro, 1_500_00, 0), (cash, 0, 1_500_00)])
        b.add_journal_entry(d, "interest", [(interest, 900_00, 0), (cash, 0, 900_00)])
    b.add_journal_entry(date(year, 2, 1).isoformat(), "snow", [(snow, 2_000_00, 0), (cash, 0, 2_000_00)])
    b.add_journal_entry(date(year - 1, 6, 1).isoformat(), "rent", [(cash, 9_000_00, 0), (rent, 0, 9_000_00)])
    b.close()
    return str(tmp_path / "op.amdb")


def test_budget_vs_actual(tmp_path):
    y = 2026
    path = _books(tmp_path, y)
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    pfm.save_projection_plan(pf, h.id, [y, y + 1], [
        pfm.ProjectionLine("revenue", "Rental income", {y: 120_000_00, y + 1: 125_000_00}),
        pfm.ProjectionLine("opex", "Hydro", {y: 12_000_00}),
        pfm.ProjectionLine("opex", "Insurance", {y: 6_000_00}),
        pfm.ProjectionLine("capex", "Roof", {y: 50_000_00})])
    rows = bg.from_projections(pf, h.id, y)
    assert [(r.kind, r.item, r.total) for r in rows] == [("revenue", "Rental income", 120_000_00),
                                                          ("opex", "Hydro", 12_000_00),
                                                          ("opex", "Insurance", 6_000_00)]
    assert rows[1].months[0] == 1_000_00 and sum(bg.split(100)) == 100
    bg.save(pf, h.id, y, rows)
    assert bg.years(pf, h.id) == [y]
    c = bg.compare(pf, h, y, 3)
    by = {r.item: r for r in c.rows}
    rent = by["Rental income"]                          # matched to "Rental Income" by name
    assert (rent.budget_ytd, rent.actual_ytd, rent.var_ytd, rent.favourable) == (30_000_00, 30_000_00, 0, True)
    hyd = by["Hydro"]
    assert (hyd.budget_ytd, hyd.actual_ytd, hyd.var_ytd) == (3_000_00, 4_500_00, 1_500_00)
    assert not hyd.favourable and hyd.flagged() and round(hyd.var_pct) == 50
    assert by["Insurance"].actual_ytd == 0 and by["Insurance"].favourable
    snow = by["Snow Removal"]
    assert snow.unbudgeted and snow.actual_ytd == 2_000_00 and snow.flagged()
    assert "Mortgage Interest" not in by                # not operating
    assert by["Net operating income"].actual_ytd == 30_000_00 - 4_500_00 - 2_000_00
    assert {r.item for r in c.flagged()} == {"Hydro", "Snow Removal"}
    # pick accounts by hand: snow removal under Insurance's line (odd, but picked)
    rows[2].accounts = ["Snow Removal"]
    bg.save(pf, h.id, y, rows)
    by = {r.item: r for r in bg.compare(pf, h, y, 3).rows}
    assert by["Insurance"].actual_ytd == 2_000_00 and "Snow Removal" not in by
    last = bg.from_last_year(path, y + 1, 10)
    assert [(r.item, r.total) for r in last if r.item == "Rental Income"] == [("Rental Income", 33_000_00)]
    with pytest.raises(ValueError):
        bg.save(pf, h.id, y, [bg.BudgetLine("capex", "Roof")])
    pf.close()


def test_turnover_recorded_from_rent_roll_edits(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court")
    pf.save_holding(h)
    u = op.Unit("101", "A. Tenant", rent_cents=1_200_00, parking_cents=50_00, move_in="2020-01-01")
    op.save_unit(pf, h.id, u)
    import dataclasses
    old = dataclasses.replace(u)
    u.tenant, u.status = "", "Vacant"
    tv.record_change(pf, h.id, old, u, date(2026, 3, 31))
    (t,) = tv.turns(pf, h.id)
    assert (t.move_out, t.prev_tenant, t.prev_rent, t.open) == ("2026-03-31", "A. Tenant", 1_250_00, True)
    assert t.days_vacant(date(2026, 4, 30)) == 30 and t.lost_rent(date(2026, 4, 30)) == round(1_250_00 * 12 / 365 * 30)
    old = dataclasses.replace(u)
    u.tenant, u.status, u.rent_cents, u.move_in = "B. Tenant", "Renovated", 1_600_00, "2026-05-15"
    tv.record_change(pf, h.id, old, u, date(2026, 5, 10))
    (t,) = tv.turns(pf, h.id)
    assert (t.move_in, t.new_tenant, t.new_rent, t.days_vacant()) == ("2026-05-15", "B. Tenant", 1_650_00, 45)
    assert t.rent_change == 400_00 and round(t.rent_change_pct) == 32
    # a straight swap: no days empty
    old = dataclasses.replace(u)
    u.tenant, u.move_in = "C. Tenant", "2026-09-01"
    tv.record_change(pf, h.id, old, u)
    swap = tv.turns(pf, h.id)[0]
    assert (swap.move_out, swap.move_in, swap.days_vacant()) == ("2026-09-01", "2026-09-01", 0)
    assert tv.record_change(pf, h.id, dataclasses.replace(u), u) is None        # nothing changed
    t.extra_cost = 800_00
    tv.save(pf, h.id, t)
    s = tv.summary(tv.turns(pf, h.id), date(2026, 10, 1))
    assert (s.turns_12, s.vacant_now, s.avg_days, s.avg_cost) == (2, 0, 22.5, 400_00)
    with pytest.raises(ValueError):
        tv.save(pf, h.id, tv.Turn("101", "2026-05-01", move_in="2026-04-01"))
    pf.close()


def test_turnover_cost_from_capex(tmp_path):
    from am_accounting import capex as cx

    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court")
    pf.save_holding(h)
    op.save_unit(pf, h.id, op.Unit("101", "A. Tenant", rent_cents=1_000_00))
    cx.add_template(pf, h.id)
    t = tv.Turn("101", "2026-03-01", "A. Tenant", 1_000_00, "2026-04-15", "B", 1_200_00)
    tv.save(pf, h.id, t)
    rows = tv.with_costs(pf, h, tv.turns(pf, h.id))
    assert rows[0].capex_cost == 0                       # no books: nothing from capex
    pf.close()


def test_budget_and_turnover_tabs(app, tmp_path, monkeypatch):
    import types

    from am_accounting.gui import ops_extra as ox

    y = date.today().year
    path = _books(tmp_path, y)
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    op.save_unit(pf, h.id, op.Unit("101", "A. Tenant", rent_cents=1_200_00))
    perf = types.SimpleNamespace(holding=h)
    page = types.SimpleNamespace(pf=pf, perf=lambda: perf, _units=op.units(pf, h.id),
                                 view=types.SimpleNamespace(_as_of_date=date.today))
    monkeypatch.setattr(ox, "confirm", lambda *a: True)
    tab = ox.BudgetTab(page)
    tab.month.setCurrentIndex(2)
    assert "No budget" in tab.head.text()
    bg.save(pf, h.id, y, [bg.BudgetLine("revenue", "Rental income", bg.split(120_000_00)),
                          bg.BudgetLine("opex", "Hydro", bg.split(12_000_00))])
    tab.fill()
    names = [tab.table.item(r, 0).text().strip() for r in range(tab.table.rowCount())]
    assert names[0] == "REVENUE" and "Snow Removal (not in the budget)" in names and names[-1] == "Net operating income"
    hyd = names.index("Hydro")
    assert tab.table.item(hyd, 9).text() == "⚠ Off budget" and "2 line(s) off budget" in tab.head.text()
    dlg = ox.BudgetDialog(pf, h, y, tab)
    assert dlg.table.rowCount() == 2 and dlg.table.item(1, 15).text() == "12,000.00"
    dlg.table.item(1, 15).setText("24,000")
    dlg.table.selectRow(1)
    dlg._spread()
    assert dlg.table.item(1, 3).text() == "2,000.00"
    dlg.save()
    assert bg.lines(pf, h.id, y)[1].total == 24_000_00
    tt = ox.TurnoverTab(page)
    tv.save(pf, h.id, tv.Turn("101", "2026-01-01", "A", 1_000_00, "2026-02-01", "B", 1_100_00))
    tt.fill()
    assert tt.table.rowCount() == 1 and tt.table.item(0, 8).text() == "31" and "+10.0%" in tt.table.item(0, 7).text()
    pf.close()
