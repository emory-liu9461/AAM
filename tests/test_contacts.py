from datetime import date

import pytest

from am_accounting import arap, contacts as ct, parties
from am_accounting import etransfer as et
from am_accounting import invoicing as inv
from am_accounting import receivables as rx
from am_accounting import review_queue as rq
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture
def books(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    yield b
    b.close()


def test_seeded_from_existing_invoices_and_bills(tmp_path):
    b = Books.open(tmp_path / "Old.amdb")
    rx.ensure_schema(b)
    with b.conn:  # documents made before the list existed
        for kind, party in (("invoice", "Acme"), ("bill", "Harbor Supply"), ("bill", "Acme")):
            ctl = rx.control_account(b, kind).id
            b.conn.execute("INSERT INTO documents(kind, party, issue_date, due_date, amount_cents, category_id, "
                           "control_id) VALUES (?,?,?,?,?,?,?)", (kind, party, "2026-01-01", "2026-02-01", 100,
                                                                 b.account_by_code("4100").id, ctl))
    people = {c.name: c.role for c in ct.all_contacts(b)}
    assert people == {"Acme": "both", "Harbor Supply": "vendor"}
    b.close()


def test_related_entity_with_due_from_account_and_other_names(books):
    c = ct.Contact("Sister Co Ltd", "both", "Related entity", aliases=["PRIYA SHAH", "SISTER CO"])
    ct.save(books, c)
    acct = ct.related_account(books, c, "receivable")
    assert books.account(acct).name == "Due from Sister Co Ltd"
    assert books.account(acct).parent_id == rx.control_account(books, "invoice").id
    assert ct.get(books, c.id).receivable_id == acct
    assert ct.find(books, "priya shah").name == "Sister Co Ltd"
    # an e-Transfer from "Priya Shah" is grouped under the contact, and its account is a one-click choice
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 7), "E-TRANSFER ***aa1", 250000)])
    et.import_text(books, "Received from Priya Shah\n$2,500.00\nOct 7, 2026\n")
    tx = books.transactions()[0]
    assert parties.name_of(books, tx.id).name == "Sister Co Ltd"
    item = rq.queue(books, name_filter="Sister Co Ltd")[0]
    assert rq.quick_choices(books, item)[0] == ("Due from Sister Co Ltd", acct)
    # categorized to the due-from account → shows in AR & AP under the contact
    rq.categorize(books, [tx.id], acct)
    line = next(i for i in arap.outstanding(books, "invoice", date(2026, 12, 31)) if i.tx_id == tx.id)
    assert line.party == "Sister Co Ltd" and line.account == "Due from Sister Co Ltd"
    s = next(x for x in ct.summaries(books, date(2026, 12, 31)) if x.contact.name == "Sister Co Ltd")
    assert s.receivable_cents == -250000 and s.transactions == 1
    # new invoices / bills for the contact default to its account
    assert rx.default_control(books, "invoice", "sister co") == acct
    with pytest.raises(ValueError, match="already a name"):
        ct.save(books, ct.Contact("Someone", aliases=["Priya Shah"]))


def test_invoices_and_bills_add_contacts_and_rename_follows(books):
    rx.add_document(books, "bill", "Keystone Printing", "2026-10-01", "2026-10-31", 5000,
                    books.account_by_code("6000").id)
    i = inv.new_invoice(books, "Northwind Labs")
    i.customer_email = "ap@northwind.test"
    i.lines = [inv.Line("Work", 1, 10000, books.account_by_code("4100").id)]
    inv.save_invoice(books, i)
    people = {c.name: c for c in ct.all_contacts(books)}
    assert people["Keystone Printing"].role == "vendor"
    assert people["Northwind Labs"].role == "customer" and people["Northwind Labs"].email == "ap@northwind.test"
    nw = people["Northwind Labs"]
    nw.name = "Northwind Laboratories Inc"
    ct.save(books, nw)
    assert rx.documents(books, "invoice")[0].party == "Northwind Laboratories Inc"
    assert inv.customer(books, "Northwind Laboratories Inc").email == "ap@northwind.test"


def test_assign_ar_lines_to_customer_and_move_to_their_account(books):
    ar = rx.control_account(books, "invoice").id
    bank = books.account_by_code("1000").id
    sister = ct.Contact("Sister Co Ltd", "customer", "Related entity")
    ct.save(books, sister)
    due = ct.related_account(books, sister, "receivable")
    books.set_opening_balance(ar, "2025-12-31", 3000000)
    books.import_transactions(bank, [ParsedTransaction(date(2026, 4, 9), "AB123 TFR-FR 7340218", -2500000)])
    tx = books.transactions()[0]
    books.set_category(tx.id, ar, source="manual")
    doc = rx.add_document(books, "invoice", "SISTER CO", "2026-05-01", "2026-05-31", 50000,
                          books.account_by_code("4100").id)
    items = arap.outstanding(books, "invoice", date(2026, 12, 31))
    assert len(items) == 3
    n = arap.assign(books, items, "Sister Co Ltd", move_account=True)
    assert n == 3
    after = arap.outstanding(books, "invoice", date(2026, 12, 31))
    assert {i.party for i in after} == {"Sister Co Ltd"}
    by_src = {i.source: i.account for i in after}
    assert by_src["bank"] == "Due from Sister Co Ltd" and by_src["document"] == "Due from Sister Co Ltd"
    assert by_src["opening"] == "Accounts Receivable"     # opening balances stay where they were posted
    assert rx.get_document(books, doc).party == "Sister Co Ltd"
    assert books.transaction(tx.id).category_id == due
    assert parties.name_of(books, tx.id).name == "Sister Co Ltd"
    s = next(x for x in ct.summaries(books, date(2026, 12, 31)) if x.contact.name == "Sister Co Ltd")
    assert s.receivable_cents == 3000000 + 2500000 + 50000
    assert books.check_balanced()
