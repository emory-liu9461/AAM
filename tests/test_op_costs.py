from datetime import date, timedelta

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
    b.import_transactions(bank, [ParsedTransaction(today - timedelta(days=1), "BRIGHT POWER UTIL", -2_400_00),
                                 ParsedTransaction(today - timedelta(days=2), "BRIGHT POWER UTIL", -2_100_00),
                                 ParsedTransaction(today - timedelta(days=3), "NORTH PLOW", -1_500_00),
                                 ParsedTransaction(today - timedelta(days=4), "SHIELD MUTUAL", -18_000_00),
                                 ParsedTransaction(today - timedelta(days=5), "LIFTCO", -900_00),
                                 ParsedTransaction(today - timedelta(days=6), "HANDY FIX", -300_00)])
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



def test_every_operating_expense_has_a_vendor(tmp_path):
    from PySide6.QtWidgets import QApplication

    path = _books(tmp_path)
    b = Books.open(path)
    bank = b.account_by_code("1000").id
    snow = next(a.id for a in b.accounts() if a.name == "Snow Removal")
    office = next(a.id for a in b.accounts() if a.name == "Office Supplies")
    intr = next(a.id for a in b.accounts() if a.name == "Interest Expense")
    today = date.today()
    b.import_transactions(bank, [ParsedTransaction(today - timedelta(days=7), "PLOW CASH", -500_00),
                                 ParsedTransaction(today - timedelta(days=8), "STAPLES 0101", -80_00),
                                 ParsedTransaction(today - timedelta(days=9), "LOAN INTEREST", -900_00)])
    for t in b.transactions():
        if t.description.startswith(("PLOW", "STAPLES", "LOAN")):
            b.set_category(t.id, {"PLOW": snow, "STAPLES": office, "LOAN": intr}[t.description.split()[0]],
                           source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    oc.start_once(pf, h.id)
    chk = oc.vendor_check(pf, h)
    by = {ln.category: ln for ln in chk.all_rows}
    assert by["Snow removal"].unassigned_cents == 500_00 and by["Snow removal"].actual_cents == 2_000_00
    assert [ln.category for ln in chk.other_accounts] == ["Office Supplies"]      # not on the list
    assert "Interest Expense" not in by                                           # non-operating: left out
    assert chk.unassigned == 580_00 and {ln.category for ln in chk.accounts_unassigned} == {"Snow removal",
                                                                                          "Office Supplies"}
    assert chk.total == 4_500_00 + 2_000_00 + 18_000_00 + 900_00 + 300_00 + 80_00
    # name them in the books: nothing left without a vendor
    b = Books.open(path)
    for t in b.transactions():
        if t.description.startswith("PLOW"):
            parties.set_name(b, [t.id], "North Plow Ltd")
        if t.description.startswith("STAPLES"):
            parties.set_name(b, [t.id], "Staples")
    # the Transactions tab can show just the ones with no customer / vendor
    QApplication.instance() or QApplication([])
    from am_accounting.gui.transactions import NO_NAME, TransactionsTab, show_unnamed

    tab = TransactionsTab(b)
    show_unnamed(tab)
    assert tab.name_filter.currentData() == NO_NAME
    assert [t.description for t in tab.model.rows] == ["LOAN INTEREST"]           # only the unnamed one
    b.close()
    chk = oc.vendor_check(pf, h)
    assert chk.unassigned == 0 and not chk.accounts_unassigned
    pf.close()


def test_naming_payments_after_the_provider(tmp_path):
    path = _books(tmp_path)
    b = Books.open(path)
    bank = b.account_by_code("1000").id
    snow = next(a.id for a in b.accounts() if a.name == "Snow Removal")
    b.import_transactions(bank, [ParsedTransaction(date.today() - timedelta(days=10), "E-TRANSFER 4471", -700_00)])
    t = next(t for t in b.transactions() if t.description.startswith("E-TRANSFER"))
    b.set_category(t.id, snow, source="manual")
    b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    oc.start_once(pf, h.id)
    assert oc.vendor_check(pf, h).unassigned == 700_00
    ln = next(x for x in oc.lines(pf, h.id) if x.category == "Snow removal")
    ln.provider = "North Plow Ltd"
    oc.save(pf, h.id, ln)
    found = oc.provider_matches(pf, h)
    assert [(x.category, len(ids)) for x, ids in found] == [("Snow removal", 1)]
    assert oc.name_with_providers(pf, h) == 1
    from am_accounting import contacts
    b = Books.open(path)
    assert any(c.name == "North Plow Ltd" and c.is_vendor for c in contacts.all_contacts(b))   # on the list
    b.close()
    chk = oc.vendor_check(pf, h)
    assert chk.unassigned == 0
    assert dict(next(x for x in chk.lines if x.category == "Snow removal").vendors)["North Plow Ltd"] == 2_200_00
    assert oc.name_with_providers(pf, h) == 0                      # nothing twice
    pf.close()


def test_assignment_check(tmp_path):
    from am_accounting import contacts, party_totals

    path = _books(tmp_path)
    b = Books.open(path)
    start, end = (date.today() - timedelta(days=364)).isoformat(), date.today().isoformat()
    chk = party_totals.assignment_check(b, start, end)
    # every payment is named, but none of the names are on the Customers & Vendors list yet
    assert chk.expenses_unassigned == 0 and set(chk.unlisted_vendors) == {
        "Bright Power", "North Plow Ltd", "Shield Mutual", "LiftCo", "Handy Fix"}
    for n in chk.unlisted_vendors:
        contacts.ensure_contact(b, n, "vendor")
    assert party_totals.assignment_check(b, start, end).ok
    # an unnamed deposit, an unnamed expense, and rent owing with no tenant named
    bank = b.account_by_code("1000").id
    inc = next(a.id for a in b.accounts() if a.type == "income")
    snow = next(a.id for a in b.accounts() if a.name == "Snow Removal")
    ar = next(a.id for a in b.accounts() if a.type == "asset" and "receivable" in a.name.lower())
    b.import_transactions(bank, [ParsedTransaction(date.today() - timedelta(days=4), "DEPOSIT", 900_00),
                                 ParsedTransaction(date.today() - timedelta(days=5), "CASH PLOW", -250_00)])
    for t in b.transactions():
        if t.description in ("DEPOSIT", "CASH PLOW"):
            b.set_category(t.id, inc if t.description == "DEPOSIT" else snow, source="manual")
    b.replace_journal_entry(None, (date.today() - timedelta(days=3)).isoformat(), "Rent charged",
                            [(ar, 1_500_00, 0), (inc, 0, 1_500_00)])
    chk = party_totals.assignment_check(b, start, end)
    assert chk.revenue_unassigned == 900_00 + 1_500_00 and chk.expenses_unassigned == 250_00
    assert chk.ar_unallocated == 1_500_00 and len(chk.ar_items) == 1 and not chk.ok
    assert any("no customer" in x for x in chk.lines()) and any("isn't allocated to a customer" in x
                                                               for x in chk.lines())
    b.close()


def test_breakdown_is_reused_until_the_books_change(tmp_path):
    from am_accounting import party_totals

    path = _books(tmp_path)
    a = party_totals.company_breakdown(path)
    assert party_totals.company_breakdown(path) is a                 # the same file: reused
    b = Books.open(path)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date.today() - timedelta(days=2), "NEW COST", -10_00)])
    b.close()
    c = party_totals.company_breakdown(path)
    assert c is not a and c.expenses == a.expenses + 10_00           # changed file: worked out again


def test_provider_suggests_vendors(app, tmp_path):
    from am_accounting import contacts as ct
    from am_accounting.gui import operations_tab as ot

    path = _books(tmp_path)
    b = Books.open(path, create=False)
    try:
        ct.save(b, ct.Contact("Enbridge Gas", "vendor", "Supplier", "billing@enbridge.example", "555-0100"))
    finally:
        b.close()
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    dlg = ot.CostDialog(pf, h, oc.CostLine("Gas", vendors=[("North Plow Ltd", 100)]))
    items = [dlg.provider.itemText(i) for i in range(dlg.provider.count())]
    assert items[1] == "North Plow Ltd" and "Enbridge Gas" in items            # paid first, then the vendor list
    assert items.count("North Plow Ltd") == 1
    dlg.provider.setCurrentText("Enbridge Gas")
    assert dlg.contact.text() == "billing@enbridge.example · 555-0100"           # filled from the vendor list
    dlg.contact.setText("J. Agent")
    dlg.provider.setCurrentText("North Plow Ltd")
    dlg.provider.setCurrentText("Enbridge Gas")
    assert dlg.contact.text() == "J. Agent"                                       # not overwritten
    pf.close()


def test_new_provider_goes_on_the_vendor_list(app, tmp_path):
    from am_accounting import contacts as ct
    from am_accounting.gui import operations_tab as ot

    path = _books(tmp_path)
    assert oc.add_vendor(path, "Waste Away Inc.", "Sam Lee, sam@wasteaway.example, 905-555-0142")
    b = Books.open(path, create=False)
    try:
        v = ct.find(b, "Waste Away Inc.")
        assert (v.role, v.email, v.phone, v.notes) == ("vendor", "sam@wasteaway.example", "905-555-0142", "Sam Lee")
    finally:
        b.close()
    assert not oc.add_vendor(path, "waste away inc.")                       # already there: left as it is
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Maple Court", company_path=path)
    pf.save_holding(h)
    dlg = ot.CostDialog(pf, h, oc.CostLine("Pest control"))
    dlg.provider.setCurrentText("Bug Busters")
    dlg.contact.setText("416-555-0199")
    dlg.save()
    assert dlg.vendor_added
    b = Books.open(path, create=False)
    try:
        assert ct.find(b, "Bug Busters").phone == "416-555-0199"
    finally:
        b.close()
    dlg = ot.CostDialog(pf, h, oc.lines(pf, h.id)[0])
    dlg.save()
    assert not dlg.vendor_added                                              # nothing new
    pf.close()
