from datetime import date

from am_accounting import receivables as rx
from am_accounting import review_queue as rq
from am_accounting.books import ParsedTransaction
from am_accounting.categorizer import auto_categorize


def tx(d, desc, cents):
    return ParsedTransaction(date.fromisoformat(d), desc, cents)


def _load(books):
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [
        tx("2026-01-02", "WIRE TRANSFER OUT REF 88123 NORTHSTAR FABRICATION", -250000),
        tx("2026-01-03", "ZELLE TO J MARTINEZ 4411", -35000),
        tx("2026-01-10", "ZELLE TO J MARTINEZ 9902", -60000),
        tx("2026-01-04", "CHECK 1042", -42500),
        tx("2026-01-05", "ONLINE TRANSFER TO SAV 4421", -100000),   # keyword guess: needs confirming
        tx("2026-01-06", "MOBILE DEPOSIT", 75000),
        tx("2026-01-07", "STARBUCKS 123", -500),                      # clear: not in the queue
        tx("2026-01-08", "MYSTERY CO", -1200),                        # uncategorized: in the queue
    ])
    auto_categorize(books)
    return bank


def test_kinds_and_patterns():
    assert rq.kind_of("WIRE TRANSFER OUT REF 1") == "Wire"
    assert rq.kind_of("INCOMING WIRE 4455") == "Wire"
    assert rq.kind_of("Venmo payment") == "Zelle / Venmo / PayPal"
    assert rq.kind_of("CHECK #1042") == "Check" and rq.check_number("CHECK #1042") == "1042"
    assert rq.kind_of("1042") == "Check"
    assert rq.kind_of("CHQ#00141-9000000002") == "Check" and rq.check_number("CHQ#00141-9000000002") == "00141"
    assert rq.kind_of("SEND E-TFR ***m4D") == "e-Transfer"
    assert rq.kind_of("ONLINE TRANSFER FROM CHK") == "Transfer"
    assert rq.kind_of("ATM WITHDRAWAL") == "ATM / cash"
    assert rq.kind_of("ACH WITHDRAWAL GUSTO") == "ACH / electronic"
    assert rq.kind_of("REMOTE DEPOSIT") == "Deposit"
    assert rq.kind_of("STARBUCKS") == "Other"
    assert rq.suggested_rule_pattern("ZELLE TO J MARTINEZ 4411") == "zelle to j martinez"
    assert rq.suggested_rule_pattern("WIRE TRANSFER OUT REF 88123 X") == "wire transfer out"


def test_queue_contents(books):
    _load(books)
    items = {i.tx.description: i for i in rq.queue(books)}
    assert "STARBUCKS 123" not in items
    assert items["ONLINE TRANSFER TO SAV 4421"].reason == "Guessed – please confirm"
    assert items["MYSTERY CO"].reason == "Uncategorized" and items["MYSTERY CO"].kind == "Other"
    assert items["CHECK 1042"].check_no == "1042"
    z = items["ZELLE TO J MARTINEZ 4411"]
    assert len(z.similar_ids) == 1
    assert rq.counts(books) == {"total": 7, "uncategorized": 6, "guessed": 1}
    assert {i.tx.description for i in rq.queue(books, "Wire")} == {"WIRE TRANSFER OUT REF 88123 NORTHSTAR FABRICATION"}
    assert {i.tx.description for i in rq.queue(books, "other")} == {"MYSTERY CO"}
    assert len(rq.queue(books, "uncategorized")) == 6
    labels = [l for l, _ in rq.quick_choices(books, z)]
    assert "Contractor" in labels and "Owner draw / distribution" in labels
    dep = rq.quick_choices(books, items["MOBILE DEPOSIT"])
    assert dep[0][0] == "Sales"


def test_categorize_similar_with_note_and_rule(books):
    bank = _load(books)
    z = next(i for i in rq.queue(books) if i.tx.description.startswith("ZELLE"))
    contractor = books.account_by_code("6100").id
    n = rq.categorize(books, [z.tx.id] + z.similar_ids, contractor, memo="Bookkeeping help",
                      rule_pattern="zelle to j martinez")
    assert n == 2
    for t in books.transactions():
        if t.description.startswith("ZELLE"):
            assert t.category_id == contractor and t.reviewed and t.memo == "Bookkeeping help"
    assert any(r.pattern == "zelle to j martinez" and r.direction == "out" for r in books.rules())
    # the rule handles the next one automatically
    books.import_transactions(bank, [tx("2026-02-01", "ZELLE TO J MARTINEZ 7777", -20000)])
    auto_categorize(books)
    new = next(t for t in books.transactions() if t.description.endswith("7777"))
    assert new.category_id == contractor and new.category_source == "rule"
    assert all(not i.tx.description.startswith("ZELLE") for i in rq.queue(books))


def test_confirm_guess(books):
    _load(books)
    item = next(i for i in rq.queue(books) if i.reason.startswith("Guessed"))
    rq.confirm_guess(books, item.tx.id)
    t = books.transaction(item.tx.id)
    assert t.reviewed and t.category_source == "manual"
    assert all(i.tx.id != t.id for i in rq.queue(books))


def test_matching_open_invoice(books):
    _load(books)
    rx.add_document(books, "invoice", "Bluebird Dental", "2026-01-01", "2026-01-31", 75000,
                    books.account_by_code("4100").id, number="INV-1")
    dep = next(i for i in rq.queue(books) if i.tx.description == "MOBILE DEPOSIT")
    assert dep.open_documents and dep.open_documents[0].number == "INV-1"
