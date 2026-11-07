from datetime import date

from am_accounting import capex as cx
from am_accounting import portfolio as pfm
from am_accounting.books import Books, ParsedTransaction


def test_capex_schedule_reconciles_to_the_balance_sheet(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    prop = b.add_account("1610", "Property – 45 Elm St", "asset", "fixed_asset").id
    mort = b.add_account("2710", "Mortgage – First National", "liability", "long_term").id
    eq = b.add_account("3010", "Share Capital – Class A", "equity", "contribution").id
    b.add_journal_entry("2026-01-01", "Closing – purchase of 45 Elm St",
                        [(prop, 4_000_000_00, 0), (bank, 100_000_00, 0), (mort, 0, 2_900_000_00),
                         (eq, 0, 1_200_000_00)], kind="opening")
    roof = b.add_account("1650", "CapEx – Roofing", "asset", "fixed_asset").id
    equip = next(a.id for a in b.accounts() if a.name == "Equipment")
    paint = b.add_account("6460", "Painting", "expense", "operating").id
    accum = next(a.id for a in b.accounts() if a.name == "Accumulated Depreciation")
    dep = b.add_account("6990", "Depreciation", "expense", "operating").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 3, 1), "ROOFER", -50_000_00),
                                 ParsedTransaction(date(2026, 4, 1), "BOILER CO", -18_000_00),
                                 ParsedTransaction(date(2026, 5, 1), "PAINTER", -4_000_00)])
    for t in b.transactions():
        b.set_category(t.id, {"ROOFER": roof, "BOILER": equip, "PAINTER": paint}[t.description.split()[0]],
                       source="manual")
    b.add_journal_entry("2026-06-30", "Depreciation", [(dep, 5_000_00, 0), (accum, 0, 5_000_00)])
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("45 Elm St", company_path=str(tmp_path / "op.amdb"))
    pf.save_holding(h)
    cx.save_line(pf, h.id, cx.CapexLine("Roofing", budget_cents=60_000_00, account_id=roof))
    cx.save_line(pf, h.id, cx.CapexLine("Painting", budget_cents=5_000_00, account_id=paint))
    cx.save_line(pf, h.id, cx.CapexLine("Permits", "Soft Costs", 3_000_00, manual_cents=2_500_00))
    r = cx.reconcile(pf, h, date(2026, 12, 31))
    assert r.schedule_total == 50_000_00 + 4_000_00 + 2_500_00
    assert (r.by_hand, r.expensed, r.schedule_fixed) == (2_500_00, 4_000_00, 50_000_00)
    names = {a.name: a for a in r.not_on_schedule}
    assert set(names) == {"Property – 45 Elm St", "Equipment"}
    assert names["Property – 45 Elm St"].acquisition == 4_000_000_00 and names["Property – 45 Elm St"].since == 0
    assert names["Equipment"].since == 18_000_00 and r.unscheduled_since == 18_000_00   # capex missed
    assert r.balance_sheet == 4_000_000_00 + 50_000_00 + 18_000_00 and r.accumulated == -5_000_00
    assert r.gap == r.balance_sheet - r.schedule_total and r.difference == 0
    # closing the gap: the boiler onto a line, the property as its own line
    boiler = cx.save_line(pf, h.id, cx.CapexLine("Boiler replacement", budget_cents=20_000_00))
    cx.link_account(pf, h, boiler, equip)
    cx.add_account_line(pf, h, names["Property – 45 Elm St"])
    r = cx.reconcile(pf, h, date(2026, 12, 31))
    assert not r.not_on_schedule and r.difference == 0
    assert r.gap == -(2_500_00 + 4_000_00)               # only what's by hand or expensed is left
    acq = next(ln for ln in cx.lines(pf, h) if ln.account_id == prop)
    assert acq.grp == "Acquisition Cost" and acq.budget_cents == 4_000_000_00
    pf.close()


def test_reconcile_without_books(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("No books")
    pf.save_holding(h)
    r = cx.reconcile(pf, h)
    assert r.error and r.gap == 0
    pf.close()
