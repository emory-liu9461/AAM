from datetime import date

from am_accounting import contacts, parties, party_totals as pt, receivables as rx
from am_accounting.books import Books, ParsedTransaction


def test_revenue_by_customer_and_expenses_by_vendor(tmp_path):
    b = Books.open(tmp_path / "op.amdb", create=True)
    bank = b.account_by_code("1000").id
    sales = b.account_by_code("4100").id
    rent = b.add_account("6990", "Rent & Lease", "expense", "operating").id
    b.import_transactions(bank, [
        ParsedTransaction(date(2026, 3, 1), "E-TFR FROM ACME", 6000_00),
        ParsedTransaction(date(2026, 4, 1), "E-TFR FROM ACME", 4000_00),
        ParsedTransaction(date(2026, 4, 2), "DEPOSIT", 1000_00),
        ParsedTransaction(date(2026, 4, 3), "LANDLORD PAD", -2500_00),
        ParsedTransaction(date(2024, 1, 3), "OLD SALE", 9999_00),            # outside the period
    ])
    for t in b.transactions():
        b.set_category(t.id, rent if t.amount_cents < 0 else sales, source="manual")
    contacts.ensure_contact(b, "Acme Corp", "customer")
    c = contacts.find(b, "Acme Corp")
    c.aliases = ["ACME"]
    contacts.save(b, c)
    parties.set_name(b, [t.id for t in b.transactions() if "ACME" in t.description], "ACME")   # alias → Acme Corp
    parties.add_rule(b, "LANDLORD", "Bay Properties")
    rx.add_document(b, "invoice", "Globex", "2026-05-01", "2026-05-31", 3000_00, sales, number="INV-7")
    out = pt.breakdown(b, "2026-01-01", "2026-06-30")
    cust = {x.name: x for x in out.customers}
    assert out.revenue == 14000_00 and out.expenses == 2500_00
    assert cust["Acme Corp"].amount == 10000_00 and cust["Acme Corp"].count == 2 and cust["Acme Corp"].listed
    assert abs(cust["Acme Corp"].share - 10 / 14) < 1e-9
    assert cust["Globex"].amount == 3000_00 and cust["Globex"].outstanding == 3000_00   # invoice not paid yet
    assert cust[pt.UNASSIGNED].amount == 1000_00 and out.customers[-1].name == pt.UNASSIGNED
    assert [x.name for x in out.customers][:2] == ["Acme Corp", "Globex"]
    assert {x.name: x.amount for x in out.vendors} == {"Bay Properties": 2500_00}
    assert out.vendors[0].main_account == "Rent & Lease" and out.unassigned("customers") == 1000_00
    b.close()
    whole = pt.company_breakdown(tmp_path / "op.amdb", date(2026, 6, 30), start="0000-01-01")
    assert whole.revenue == 14000_00 + 9999_00 and not whole.error
