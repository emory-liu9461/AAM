from datetime import date

import pytest

from am_accounting import etransfer as et
from am_accounting import receivables as rx
from am_accounting.books import Books, ParsedTransaction

HISTORY = """Interac e-Transfer History
Date  Recipient  Amount  Status  Reference Number
Apr 4, 2024  Jane Smith  $3,000.00  Deposited  CA9XyZwk9q
Apr 15, 2024  Mike Chen  $3,000.00  Deposited  C1AxyZBaz
Apr 15, 2024  Harbor Supply Co  $395.50  Completed  CAqwe9yzq
Apr 20, 2024  Bob Lee  $50.00  Cancelled  CAzzz1abc
"""

PHONE = """Sent to Jane Smith
$3,000.00
Apr 4
Reference: CA9XyZwk9q
Message: April rent
Received from Acme Corp
$2,500.00
Apr 2
Reference number CA9Zx1uPZ
"""


@pytest.fixture
def books(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    b.import_transactions(b.account_by_code("1000").id, [
        ParsedTransaction(date(2024, 4, 2), "E-TRANSFER ***r2T", 250000),
        ParsedTransaction(date(2024, 4, 4), "SEND E-TFR ***k9q", -300000),
        ParsedTransaction(date(2024, 4, 4), "SEND E-TFR FEE", -150),
        ParsedTransaction(date(2024, 4, 15), "SEND E-TFR ***Baz", -300000),
        ParsedTransaction(date(2024, 4, 15), "SEND E-TFR ***yzq", -39550),
        ParsedTransaction(date(2024, 4, 15), "CD456 TFR-TO 7340218", -300000)])
    yield b
    b.close()


def tx(books, desc):
    return next(t for t in books.transactions() if t.description == desc)


def test_recognizes_bank_etransfers():
    assert et.is_etransfer("SEND E-TFR ***k9q") and et.ref_suffix("SEND E-TFR ***k9q") == "k9q"
    assert et.is_etransfer("E-TRANSFER ***r2T") and et.is_etransfer("INTERAC E-TRF")
    assert not et.is_etransfer("SEND E-TFR FEE") and not et.is_etransfer("CD456 TFR-TO 7340218")


def test_table_history_matched_by_reference_ending(books):
    added, known, res = et.import_text(books, HISTORY)
    assert (added, known) == (3, 0)  # the cancelled one is skipped
    assert res.matched == 3 and res.waiting == 0
    assert tx(books, "SEND E-TFR ***k9q").memo == "↔ e-Transfer Jane Smith"
    assert tx(books, "SEND E-TFR ***Baz").memo == "↔ e-Transfer Mike Chen"  # same amount, told apart by ref
    assert tx(books, "CD456 TFR-TO 7340218").memo == ""                   # not an e-Transfer
    # reading the same history again adds nothing
    assert et.import_text(books, HISTORY)[:2] == (0, 3)


def test_phone_screenshot_text_with_message_and_direction(books):
    et.import_text(books, PHONE)
    assert tx(books, "SEND E-TFR ***k9q").memo == "↔ e-Transfer to Jane Smith “April rent”"
    assert tx(books, "E-TRANSFER ***r2T").memo == "↔ e-Transfer from Acme Corp"


def test_category_learned_per_person_and_unlink(books):
    rent = books.account_by_code("6400").id
    et.import_text(books, PHONE)
    t = tx(books, "SEND E-TFR ***k9q")
    books.set_category(t.id, rent, source="manual", reviewed=True)
    # next month's rent to Jane is categorized the same way
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2024, 5, 3), "SEND E-TFR ***k2Q", -300000)])
    et.import_text(books, "Sent to Jane Smith\n$3,000.00\nMay 3, 2024\nReference: CA77abck2Q\n")
    may = tx(books, "SEND E-TFR ***k2Q")
    assert may.category_id == rent and "Jane Smith" in may.memo
    rec = et.record_for_transaction(books, may.id)
    et.unlink(books, rec.id)
    assert books.transaction(may.id).memo == "" and et.record_for_transaction(books, may.id) is None


def test_waits_for_import_and_pays_open_invoice(books):
    inv = rx.add_document(books, "invoice", "Northwind Labs", "2024-04-20", "2024-05-20", 120000,
                          books.account_by_code("4100").id, number="INV-1001")
    _a, _k, res = et.import_text(books, "Received from Northwind Labs\n$1,200.00\nApr 28, 2024\nReference: CA5551xR2\n")
    assert res.waiting == 1 and res.matched == 0
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2024, 4, 29), "E-TRANSFER ***xR2", 120000)])
    assert et.match_all(books).matched == 1
    assert rx.get_document(books, inv).balance_cents == 0  # the e-Transfer paid the invoice


def test_csv_and_folder(books, tmp_path):
    from am_accounting import workspace as ws

    ws.set_config(books, ws.WorkspaceConfig(str(tmp_path / "Company")), connect_inbox=False, connect_documents=False)
    drop = et.folder(books)
    drop.mkdir(parents=True, exist_ok=True)
    (drop / "history.csv").write_text("Date,Type,Contact,Amount,Reference #,Message\n"
                                      "2024-04-15,Sent,Harbor Supply Co,395.50,CAqwe9yzq,Invoice HS-1\n")
    msgs = et.process_folder(books, min_age_seconds=0)
    assert any("1 transfer(s) read, 1 new" in m for m in msgs)
    assert (drop / et.READ_DIR / "history.csv").exists()
    assert tx(books, "SEND E-TFR ***yzq").memo == "↔ e-Transfer to Harbor Supply Co “Invoice HS-1”"


def test_received_transfers_match_even_when_reference_differs(books):
    """Received e-Transfers: the history's reference often doesn't end with the bank line's ***xxx."""
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [
        ParsedTransaction(date(2026, 10, 7), "E-TRANSFER ***w7B", 250000),
        ParsedTransaction(date(2026, 10, 8), "E-TRANSFER ***h5K", 500000),
        ParsedTransaction(date(2026, 8, 19), "MOBILE DEPOSIT", 710000),       # not labelled e-Transfer
        ParsedTransaction(date(2026, 7, 31), "E-TFR ***TrQ EPAY", 35000)])
    hist = ("Date  From  Amount  Reference\n"
            "Oct 7, 2026  Priya Shah Email/Mobile  $2,500.00  CA1zzzzAB1\n"
            "Oct 7, 2026  Priya Shah Email/Mobile  $5,000.00  CA1zzzzAB2\n"
            "Aug 19, 2026  Alex Morgan Email/Mobile  $7,100.00  CA1zzzzAB3\n"
            "Jul 31, 2026  Alex Morgan Email/Mobile Method  $350.00  CA1zzzzAB4\n"
            "Jun 11, 2026  Roger Qi Email/Mobile  $3,000.00  CA1zzzzAB5\n")
    hist = hist.replace("  From  ", "  Sender  ").replace("2026  ", "2026  Received from ")
    _a, _k, res = et.import_text(books, hist)
    assert res.matched == 4
    memo = {t.description: t.memo for t in books.transactions()}
    assert memo["E-TRANSFER ***w7B"] == "↔ e-Transfer from Priya Shah"
    assert memo["E-TRANSFER ***h5K"] == "↔ e-Transfer from Priya Shah"
    assert memo["MOBILE DEPOSIT"] == "↔ e-Transfer from Alex Morgan"
    assert memo["E-TFR ***TrQ EPAY"] == "↔ e-Transfer from Alex Morgan"
    roger = next(r for r in et.records(books, status="unmatched"))
    assert roger.name == "Roger Qi"
    assert et.why_waiting(books, roger) == "No statement imported around 2026-06-11 yet — it will match after you " \
                                         "import it"
    books.import_transactions(bank, [ParsedTransaction(date(2026, 6, 2), "RENT", -100000)])
    assert et.why_waiting(books, roger) == "No deposit of $3,000.00 near 2026-06-11 in the imported statements"


def test_match_by_hand_lists_same_amount_lines_around_the_date(books):
    et.import_text(books, HISTORY)
    jane = next(r for r in et.records(books) if r.name == "Jane Smith")
    lines = et.nearby_lines(books, jane, days=14)
    descs = [(ln.tx.description, ln.matched_to) for ln in lines]
    # Jane's own line first, then the other $3,000 lines (Mike's e-Transfer is shown as already matched)
    assert descs[0] == ("SEND E-TFR ***k9q", "")
    assert ("SEND E-TFR ***Baz", "e-Transfer Mike Chen") in descs
    assert ("CD456 TFR-TO 7340218", "") in descs and len(lines) == 3
    assert len(et.nearby_lines(books, jane, days=3)) == 1
    any_amt = et.nearby_lines(books, jane, days=14, any_amount=True)
    assert {ln.tx.description for ln in any_amt} >= {"SEND E-TFR ***yzq", "E-TRANSFER ***r2T"}
    assert all(et.is_etransfer(ln.tx.description) for ln in any_amt)
    # moving Mike's record to Jane's line clears Jane's old note
    mike = next(r for r in et.records(books) if r.name == "Mike Chen")
    k9q = tx(books, "SEND E-TFR ***k9q")
    et.link(books, mike.id, k9q.id)
    assert "Mike Chen" in tx(books, "SEND E-TFR ***k9q").memo
    assert tx(books, "SEND E-TFR ***Baz").memo == ""


def test_every_bank_etransfer_variant_is_recognized_and_accounted_for(books):
    from am_accounting import review_queue as rq

    for d in ["SEND E-TFR ***k9q", "E-TFR ***TrQ EPAY", "E-TRANSFER ***r2T", "SEND E-TFR", "E-TFR", "ETFR RECEIVED",
              "E TFR 12345", "SENDE-TFR ***A1", "E‑TFR ***x1", "INTERAC E-TRANSFER", "AUTODEPOSIT E-TRF"]:
        assert et.is_etransfer(d) and rq.kind_of(d) == "e-Transfer", d
    for d in ["SEND E-TFR FEE", "ONLINE TRANSFER", "PHONE TRANSFER", "AB123 TFR-FR 7340218"]:
        assert not et.is_etransfer(d) and rq.kind_of(d) != "e-Transfer", d
    assert rq.kind_of("CAD DRAFT 05551234") == "Bank draft" and rq.kind_of("CHQ#00126-31") == "Check"
    # every e-Transfer line in the statements is accounted for: matched, or listed as having no history
    lines = et.bank_lines(books)
    assert {t.description for t in lines} == {"E-TRANSFER ***r2T", "SEND E-TFR ***k9q", "SEND E-TFR ***Baz",
                                              "SEND E-TFR ***yzq"}
    et.import_text(books, HISTORY)
    assert {t.description for t in et.bank_lines_without_history(books)} == {"E-TRANSFER ***r2T"}
    assert {q.tx.description for q in rq.queue(books, "e-Transfer")} == {t.description for t in lines}
