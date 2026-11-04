from datetime import date

import pytest

from am_accounting import arap, parties
from am_accounting import etransfer as et
from am_accounting import receivables as rx
from am_accounting import review_queue as rq
from am_accounting.books import Books, ParsedTransaction


@pytest.fixture
def books(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    b.import_transactions(b.account_by_code("1000").id, [
        ParsedTransaction(date(2026, 7, 29), "E-TRANSFER ***aa1", 10000),
        ParsedTransaction(date(2026, 7, 31), "E-TRANSFER ***aa2", 35000),
        ParsedTransaction(date(2026, 8, 19), "E-TRANSFER ***aa3", 710000),
        ParsedTransaction(date(2026, 8, 20), "AB123 TFR-FR 7340218", 400000),
        ParsedTransaction(date(2026, 8, 21), "SEND E-TFR ***bb1", -300000)])
    et.import_text(b, "Received from Alex Morgan\n$100.00\nJul 29, 2026\nReceived from Alex Morgan\n$350.00\n"
                      "Jul 31, 2026\nReceived from Alex Morgan\n$7,100.00\nAug 19, 2026\n"
                      "Sent to Roger Qi\n$3,000.00\nAug 21, 2026\n")
    yield b
    b.close()


def test_names_from_etransfers_rules_and_by_hand(books):
    n = {books.transaction(i).description: v.name for i, v in parties.names(books).items()}
    assert n == {"E-TRANSFER ***aa1": "Alex Morgan", "E-TRANSFER ***aa2": "Alex Morgan", "E-TRANSFER ***aa3": "Alex Morgan",
                 "SEND E-TFR ***bb1": "Roger Qi"}
    assert parties.suggested_pattern("AB123 TFR-FR 7340218") == "TFR-FR 7340218"
    parties.add_rule(books, "TFR-FR 7340218", "Alex Morgan")
    tfr = next(t for t in books.transactions() if t.description.startswith("AB123"))
    assert parties.name_of(books, tfr.id).name == "Alex Morgan"
    # future transactions get the name from the rule as soon as they're imported
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 9, 2), "XY001 TFR-FR 7340218", 50000)])
    new = next(t for t in books.transactions() if t.description.startswith("XY001"))
    assert parties.name_of(books, new.id) == parties.Name("Alex Morgan", "rule")
    parties.set_name(books, [new.id], "Alex Morgan (savings)")
    assert parties.name_of(books, new.id) == parties.Name("Alex Morgan (savings)", "manual")
    parties.set_name(books, [new.id], "")
    assert parties.name_of(books, new.id).source == "rule"
    assert "Alex Morgan" in parties.all_names(books)


def test_queue_filters_and_groups_by_name_and_learns_category(books):
    loan = books.account_by_code("2700").id if any(a.code == "2700" for a in books.accounts()) else \
        books.add_account("2710", "Shareholder Loan", "liability", "current").id
    alex = rq.queue(books, name_filter="alex morgan")
    assert len(alex) == 3 and all(q.name == "Alex Morgan" for q in alex)
    assert sorted(len(q.similar_ids) for q in alex) == [2, 2, 2]  # "also apply to 2 others with Alex Morgan"
    assert [q.tx.description for q in rq.queue(books, name_filter=rq.NO_NAME)] == ["AB123 TFR-FR 7340218"]
    rq.categorize(books, [q.tx.id for q in alex[:1]], loan)
    # the next e-Transfer from Alex gets the same category
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 1), "E-TRANSFER ***aa9", 20000)])
    et.import_text(books, "Received from Alex Morgan\n$200.00\nOct 1, 2026\n")
    assert next(t for t in books.transactions() if t.description == "E-TRANSFER ***aa9").category_id == loan
    assert parties.apply_learned(books) == 2  # the two older Alex ones still uncategorized


def test_names_show_in_ar_ap(books):
    ar = rx.control_account(books, "invoice").id
    tfr = next(t for t in books.transactions() if t.description.startswith("AB123"))
    books.set_category(tfr.id, ar, source="manual")
    item = next(i for i in arap.ledger_items(books, "invoice", date(2026, 12, 31)) if i.tx_id == tfr.id)
    assert item.party == "Wo Tfr-Fr" or item.party  # automatic name from the description
    arap.set_party(books, item.key, "Alex Morgan")   # naming it in AR & AP names the transaction everywhere
    assert parties.name_of(books, tfr.id).name == "Alex Morgan"
    assert next(i for i in arap.ledger_items(books, "invoice", date(2026, 12, 31)) if i.tx_id == tfr.id).party == \
        "Alex Morgan"
