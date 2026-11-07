from datetime import date

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import op_costs as oc
from am_accounting import parties
from am_accounting import portfolio as pfm
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _books(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    hydro = b.add_account("6410", "Hydro", "expense", "operating").id
    snow = b.add_account("6420", "Snow Removal", "expense", "operating").id
    ins = next(a.id for a in b.accounts() if a.name == "Insurance")             # in the standard chart
    elev = b.add_account("6440", "Elevator Maintenance", "expense", "operating").id
    rep = next(a.id for a in b.accounts() if a.name == "Repairs & Maintenance")
    today = date.today()
    b.import_transactions(bank, [ParsedTransaction(today.replace(day=1), "BRIGHT POWER UTIL", -2_400_00),
                                 ParsedTransaction(today.replace(day=2), "BRIGHT POWER UTIL", -2_100_00),
                                 ParsedTransaction(today.replace(day=3), "NORTH PLOW", -1_500_00),
                                 ParsedTransaction(today.replace(day=4), "SHIELD MUTUAL", -18_000_00),
                                 ParsedTransaction(today.replace(day=5), "LIFTCO", -900_00),
                                 ParsedTransaction(today.replace(day=6), "HANDY FIX", -300_00)])
    for t in b.transactions():
        acct = {"BRIGHT": hydro, "NORTH": snow, "SHIELD": ins, "LIFTCO": elev, "HANDY": rep}[t.description.split()[0]]
        b.set_category(t.id, acct, source="manual")
        name = {"BRIGHT": "Bright Power", "NORTH": "North Plow Ltd", "SHIELD": "Shield Mutual",
                "LIFTCO": "LiftCo", "HANDY": "Handy Fix"}[t.description.split()[0]]
        parties.set_name(b, [t.id], name)
    b.close()
    return str(tmp_path / "op.amdb")


def test_operating_costs_with_providers_from_the_books(tmp_path):
    path = _books(tmp_path)
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    assert oc.start_once(pf, h.id) and len(oc.lines(pf, h.id)) == len(oc.CATEGORIES)
    assert not oc.start_once(pf, h.id)                               # only the first time
    rows = oc.lines(pf, h.id)
    oc.with_books(rows, path)
    by = {ln.category: ln for ln in rows}
    assert by["Electricity (hydro)"].matched_account == "Hydro" and by["Electricity (hydro)"].actual_cents == 4_500_00
    assert by["Electricity (hydro)"].top_vendor == "Bright Power"
    assert by["Snow removal"].vendors == [("North Plow Ltd", 1_500_00)]
    assert by["Insurance"].matched_account == "Insurance"
    assert by["Elevator"].matched_account == "Elevator Maintenance"         # not taken by repairs & maintenance
    assert by["Repairs & maintenance"].matched_account == "Repairs & Maintenance"
    assert by["Legal & accounting"].matched_account == "Professional Fees"
    assert by["Internet & telecom"].matched_account == "Telephone & Internet"
    assert by["Pest control"].matched_account == "" and by["Pest control"].actual_cents is None
    assert oc.fill_providers(pf, h.id, rows) == 5
    by = {ln.category: ln for ln in oc.lines(pf, h.id)}
    assert by["Insurance"].provider == "Shield Mutual"
    ins = by["Insurance"]
    ins.account_no, ins.contact, ins.contract_end, ins.budget_cents = "POL-123", "J. Agent 555-0100", "2027-03-31", 20_000_00
    oc.save(pf, h.id, ins)
    assert next(x for x in oc.lines(pf, h.id) if x.category == "Insurance").account_no == "POL-123"
    oc.delete(pf, ins.id)
    assert oc.add_standard(pf, h.id) == 1                            # brings back what's missing
    with pytest.raises(ValueError):
        oc.save(pf, h.id, oc.CostLine(" "))
    pf.close()

