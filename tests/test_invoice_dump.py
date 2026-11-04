from datetime import date

import pytest

from am_accounting import invoice_dump as dump
from am_accounting import workspace as ws
from am_accounting.books import Books, ParsedTransaction

pytest.importorskip("reportlab")


def invoice_pdf(path, lines):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=letter)
    y = 740
    for ln in lines:
        c.drawString(60, y, ln)
        y -= 18
    c.save()
    return path


HARBOR = ["Harbor Supply Co", "125 Dock Road, Halifax NS", "INVOICE", "Invoice #: HS-44871",
          "Invoice Date: October 3, 2026", "Bill to: Northwind Labs",
          "Packaging materials        1,950.00", "Subtotal                   1,950.00",
          "Tax                          175.00", "Total Due                  2,125.00"]


@pytest.fixture
def setup(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    root = tmp_path / "Company"
    ws.set_config(b, ws.WorkspaceConfig(str(root)))
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [
        ParsedTransaction(date(2026, 10, 25), "HARBOR SUPPLY CO PAYMENT", -212500),
        ParsedTransaction(date(2026, 10, 26), "MOBILE DEPOSIT", 50000),
        ParsedTransaction(date(2026, 10, 27), "WIRE OUT 1182", -30000),
        ParsedTransaction(date(2026, 10, 28), "WIRE OUT 1183", -30000)])
    yield b, root / dump.DUMP_DIR
    b.close()


def test_extract_info():
    info = dump.extract_info("\n".join(HARBOR))
    assert info.total == 212500 and info.number == "HS-44871" and info.doc_date == date(2026, 10, 3)
    assert info.party == "Harbor Supply Co" and {"HARBOR", "SUPPLY"} <= info.words
    assert 195000 in info.alternates


def test_text_pdf_is_matched_renamed_and_filed(setup, tmp_path):
    books, root = setup
    invoice_pdf(root / "scan_00042.pdf", HARBOR)
    events = dump.process_dump(books, min_age_seconds=0)
    assert len(events) == 1 and events[0].matched
    filed = root / dump.RECOGNIZED_DIR / "2026" / "2026-10" / "2026-10-25 Harbor Supply Co $2,125.00.pdf"
    assert filed.exists() and not (root / "scan_00042.pdf").exists()
    tx = next(t for t in books.transactions() if t.description.startswith("HARBOR"))
    assert dump.files_for_transaction(books, tx.id) == [filed]
    assert "📎" in books.transaction(tx.id).memo
    # dropping the very same file again: removed, nothing duplicated
    import shutil

    shutil.copy(filed, root / "copy.pdf")
    assert dump.process_dump(books, min_age_seconds=0) == []
    assert not (root / "copy.pdf").exists()
    # the same invoice saved again as a different file (e.g. emailed twice): set aside as a duplicate
    invoice_pdf(root / "again.pdf", HARBOR)
    ev = dump.process_dump(books, min_age_seconds=0)[0]
    assert not ev.matched and ev.reason.startswith("Duplicate of 2026-10-25 Harbor Supply Co")
    assert dump.process_dump(books, read_new=False) == []  # and left alone afterwards


def test_photo_receipt_is_read_with_ocr(setup, tmp_path):
    pytest.importorskip("rapidocr_onnxruntime")
    from PIL import Image, ImageDraw, ImageFont

    books, root = setup
    img = Image.new("RGB", (1400, 900), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=44)
    for i, ln in enumerate(["CORNER CAFE CATERING", "Receipt Date: 2026-10-26", "Catering for 12",
                            "TOTAL  500.00"]):
        d.text((80, 80 + i * 110), ln, fill="black", font=font)
    img.save(root / "IMG_2231.jpg", quality=90)
    events = dump.process_dump(books, min_age_seconds=0)
    assert events and events[0].matched, events
    assert "MOBILE DEPOSIT" not in events[0].new_name and events[0].new_name.endswith("$500.00.jpg")


def test_unmatched_waits_and_is_matched_after_import(setup):
    books, root = setup
    invoice_pdf(root / "keystone.pdf", ["Keystone Printing", "Invoice No. KP-2207", "Date: 2026-10-20",
                                        "Amount Due 689.00"])
    ev = dump.process_dump(books, min_age_seconds=0)[0]
    assert not ev.matched and "No bank transaction of $689.00" in ev.reason
    assert (root / dump.PENDING_DIR / "keystone.pdf").exists()
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 11, 1), "KEYSTONE PRINTING KP-2207", -68900)])
    ev = dump.process_dump(books, read_new=False)[0]
    assert ev.matched and ev.new_name == "2026-11-01 Keystone Printing $689.00.pdf"
    assert not (root / dump.PENDING_DIR / "keystone.pdf").exists()


def test_ambiguous_waits_for_a_person_then_manual_match_and_undo(setup):
    books, root = setup
    invoice_pdf(root / "contractor.pdf", ["J Martinez", "Invoice 77", "Date: 2026-10-20", "Total 300.00"])
    ev = dump.process_dump(books, min_age_seconds=0)[0]
    assert not ev.matched and "2 bank transactions of $300.00" in ev.reason
    item = dump.items(books, "unmatched")[0]
    wire = next(t for t in books.transactions() if t.description == "WIRE OUT 1183")
    dest = dump.link(books, item.id, wire.id)
    assert dest.parent.name == "2026-10" and dump.items(books, "matched")[0].transaction.id == wire.id
    back = dump.unlink(books, item.id)
    assert back.parent.name == dump.PENDING_DIR and "📎" not in books.transaction(wire.id).memo
    # unmatched by hand: not re-matched automatically
    assert dump.process_dump(books, read_new=False) == []


def test_archive_recognized(setup):
    books, root = setup
    invoice_pdf(root / "h.pdf", HARBOR)
    dump.process_dump(books, min_age_seconds=0)
    moves = dump.archive(books, date(2027, 1, 1))
    assert len(moves) == 1
    assert (root / dump.ARCHIVE_DIR / "2026" / "2026-10" / "2026-10-25 Harbor Supply Co $2,125.00.pdf").exists()
    assert dump.items(books, "matched")[0].path.exists()


def test_inbox_scan_runs_the_dump(setup):
    from am_accounting.inbox import StatementInbox

    books, root = setup
    invoice_pdf(root / "h.pdf", HARBOR)
    events = StatementInbox().scan_company(books.path, require_stable=False)
    assert any("Harbor Supply Co $2,125.00" in e.describe() for e in events)


def test_process_all_bills(setup):
    from am_accounting import receivables as rx

    books, root = setup
    # teach it that Harbor Supply is cost of goods (a past choice)
    cogs = books.account_by_code("5000").id
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 9, 2), "HARBOR SUPPLY CO PAYMENT", -10000)])
    old = next(t for t in books.transactions() if t.date == "2026-09-02")
    books.set_category(old.id, cogs, source="manual")

    invoice_pdf(root / "harbor.pdf", HARBOR)                                          # paid → categorize
    invoice_pdf(root / "keystone.pdf", ["Keystone Printing", "Invoice No. KP-2207",  # unpaid → bill
                                        "Date: 2026-10-20", "Amount Due 689.00"])
    invoice_pdf(root / "contractor.pdf", ["J Martinez", "Invoice 77",                # ambiguous → attention
                                          "Date: 2026-10-20", "Total 300.00"])
    seen = []
    s = dump.run_all(books, progress=lambda n, total, name: seen.append((n, total)))
    assert seen[-1] == (3, 3)
    assert (s.read, s.matched, s.categorized) == (3, 1, 1)
    harbor_tx = next(t for t in books.transactions() if t.date == "2026-10-25")
    assert harbor_tx.category_id == cogs  # expense recorded from the bill, using the past choice
    assert s.bills == ["Keystone Printing $689.00"]
    bill = rx.documents(books, "bill")[0]
    assert (bill.party, bill.number, bill.amount_cents, bill.issue_date, bill.due_date) == \
        ("Keystone Printing", "KP-2207", 68900, "2026-10-20", "2026-11-19")
    payable = root / dump.PAYABLES_DIR / "2026-10-20 KP-2207 Keystone Printing $689.00 (due 2026-11-19).pdf"
    assert payable.exists() and not (root / dump.PENDING_DIR / "keystone.pdf").exists()
    assert len(s.attention) == 1 and "2 bank transactions" in s.attention[0]
    assert "1 unpaid bill(s) recorded" in s.describe() and "all with an expense category" in s.describe()
    # when the bill's payment arrives, it's matched and the bill is paid
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 11, 10), "KEYSTONE PRINTING KP-2207", -68900)])
    dump.process_dump(books, read_new=False)
    assert rx.get_document(books, bill.id).balance_cents == 0
    assert not payable.exists()
    assert (root.parent / dump.SAVED_DIR / "2026" / "2026-11" / "2026-11-10 KP-2207 Keystone Printing.pdf").exists()
    # running again: nothing new, nothing duplicated
    s2 = dump.run_all(books)
    assert (s2.read, s2.matched, len(s2.bills)) == (0, 0, 0)


def test_recorded_invoices_go_to_saved_invoices_named_by_date_and_number(setup):
    books, root = setup
    saved = root.parent / dump.SAVED_DIR
    harbor_tx = next(t for t in books.transactions() if t.description.startswith("HARBOR"))
    # 1. matched but expense not recorded yet → waits in Recognized
    invoice_pdf(root / "harbor.pdf", HARBOR)
    dump.process_dump(books, min_age_seconds=0)
    recognized = root / dump.RECOGNIZED_DIR / "2026" / "2026-10" / "2026-10-25 Harbor Supply Co $2,125.00.pdf"
    assert recognized.exists()
    # 2. you categorize the payment (e.g. in the To Categorize panel) → next check saves it, renamed
    books.set_category(harbor_tx.id, books.account_by_code("5000").id, source="manual", reviewed=True)
    events = dump.process_dump(books, read_new=False)
    final = saved / "2026" / "2026-10" / "2026-10-25 HS-44871 Harbor Supply Co.pdf"
    assert final.exists() and not recognized.exists()
    assert events[0].reason == "saved" and "Saved Invoices" in events[0].describe()
    assert dump.files_for_transaction(books, harbor_tx.id) == [final]
    assert books.transaction(harbor_tx.id).memo == "📎 2026-10-25 HS-44871 Harbor Supply Co.pdf"
    assert not (root / dump.RECOGNIZED_DIR / "2026").exists()  # emptied folders tidied
    # 3. a payment that's already categorized: its invoice is saved straight away
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 11, 3), "KEYSTONE PRINTING", -68900)])
    k = next(t for t in books.transactions() if t.description == "KEYSTONE PRINTING")
    books.set_category(k.id, books.account_by_code("6000").id)
    invoice_pdf(root / "k.pdf", ["Keystone Printing", "Invoice No. KP-2207", "Date: 2026-10-30",
                                 "Amount Due 689.00"])
    dump.process_dump(books, min_age_seconds=0)
    assert (saved / "2026" / "2026-11" / "2026-11-03 KP-2207 Keystone Printing.pdf").exists()
    # 4. undo still works from Saved Invoices
    item = next(i for i in dump.items(books, "matched") if i.number == "KP-2207")
    back = dump.unlink(books, item.id)
    assert back.exists() and back.parent.name == dump.PENDING_DIR
    # 5. archive: Saved Invoices/Archive/<year>/<month>
    dump.archive(books, date(2027, 1, 1))
    assert (saved / dump.ARCHIVE_DIR / "2026" / "2026-10" / "2026-10-25 HS-44871 Harbor Supply Co.pdf").exists()
    assert dump.files_for_transaction(books, harbor_tx.id)[0].exists()


def test_saved_name_without_invoice_number(setup):
    books, root = setup
    dep = next(t for t in books.transactions() if t.description == "MOBILE DEPOSIT")
    books.set_category(dep.id, books.account_by_code("4000").id)
    invoice_pdf(root / "r.pdf", ["Corner Cafe Catering", "Receipt", "Date: 2026-10-26", "TOTAL 500.00"])
    dump.process_dump(books, min_age_seconds=0)
    assert (root.parent / dump.SAVED_DIR / "2026" / "2026-10" /
            "2026-10-26 Corner Cafe Catering $500.00.pdf").exists()


def test_process_all_reports_saved(setup):
    books, root = setup
    cogs = books.account_by_code("5000").id
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 9, 2), "HARBOR SUPPLY CO PAYMENT", -10000)])
    old = next(t for t in books.transactions() if t.date == "2026-09-02")
    books.set_category(old.id, cogs, source="manual")
    invoice_pdf(root / "harbor.pdf", HARBOR)
    s = dump.run_all(books)
    assert s.saved == 1 and "1 saved to Saved Invoices" in s.describe()
    assert (root.parent / dump.SAVED_DIR / "2026" / "2026-10" / "2026-10-25 HS-44871 Harbor Supply Co.pdf").exists()


def test_user_marks_unmatched_invoice_as_future_payable(setup):
    from am_accounting import receivables as rx

    books, root = setup
    invoice_pdf(root / "keystone.pdf", ["Keystone Printing", "Invoice No. KP-2207",
                                        "Date: 2026-10-20", "Amount Due 689.00"])
    dump.process_dump(books, min_age_seconds=0)
    it = dump.items(books, "unmatched")[0]
    dest = dump.mark_payable(books, it.id, due_date="2026-12-01")
    assert dest == root / dump.PAYABLES_DIR / "2026-10-20 KP-2207 Keystone Printing $689.00 (due 2026-12-01).pdf"
    assert dest.exists() and not it.path.exists()
    bill = rx.documents(books, "bill")[0]
    assert (bill.number, bill.amount_cents, bill.due_date) == ("KP-2207", 68900, "2026-12-01")
    assert dump.items(books, "payable")[0].path == dest
    assert not dump.items(books, "unmatched")  # no longer waiting / counted
    # changed your mind: back to Not Yet Matched, and the bill is removed from Accounts Payable
    back = dump.unlink(books, it.id)
    assert back.parent.name == dump.PENDING_DIR and not rx.documents(books, "bill")
    # payment arrives later → matched, bill paid, file saved
    dump.mark_payable(books, it.id)
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 30), "KEYSTONE PRINTING", -68900)])
    ev = dump.process_dump(books, read_new=False)
    assert any(e.reason == "paid" for e in ev)
    assert rx.documents(books, "bill", status="open") == []
    saved = root.parent / dump.SAVED_DIR / "2026" / "2026-10" / "2026-10-30 KP-2207 Keystone Printing.pdf"
    assert saved.exists() and not list((root / dump.PAYABLES_DIR).iterdir())


def test_user_marks_invoice_unrelated(setup):
    books, root = setup
    invoice_pdf(root / "wrong.pdf", ["Someone Else Ltd", "Invoice 9911", "Date: 2026-10-20", "Total 300.00"])
    dump.process_dump(books, min_age_seconds=0)
    it = dump.items(books, "unmatched")[0]
    dest = dump.mark_unrelated(books, it.id)
    assert dest == root / dump.UNRELATED_DIR / "wrong.pdf" and dest.exists()
    # never matched again, even with a matching amount around
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2026, 10, 21), "SOMEONE ELSE LTD", -30000)])
    dump.process_dump(books, read_new=False)
    assert dump.items(books, "unrelated")[0].transaction is None
    assert dump.unlink(books, it.id).parent.name == dump.PENDING_DIR


def test_old_dump_table_is_upgraded(tmp_path):
    b = Books.open(tmp_path / "Old.amdb")
    b.conn.executescript(dump.SCHEMA.replace(",'payable','unrelated'", "").replace(
        ",\n    document_id    INTEGER", ""))
    b.conn.execute("INSERT INTO dump_files(rel_path, original_name, file_hash, status, added_at) "
                   "VALUES ('Not Yet Matched/a.pdf','a.pdf','h','unmatched','2026-10-01')")
    dump.ensure_schema(b)
    assert "'payable'" in b.conn.execute("SELECT sql FROM sqlite_master WHERE name='dump_files'").fetchone()[0]
    assert b.conn.execute("SELECT original_name, document_id FROM dump_files").fetchone()[:] == ("a.pdf", None)
    b.close()
