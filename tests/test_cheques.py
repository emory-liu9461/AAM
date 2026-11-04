from datetime import date

import pytest

from am_accounting import cheques as cq
from am_accounting import receivables as rx
from am_accounting import workspace as ws
from am_accounting.books import Books, ParsedTransaction

CHEQUE = """NORTHWIND STUDIO INC.                                   No. 000126
DATE 2 0 2 4 0 4 1 0
PAY TO THE ORDER OF  Jane Smith                         $ **4,982.50
Four thousand nine hundred eighty-two -------- 50/100 DOLLARS
MEMO: April rent
C000126C A12345D004A 7340299C"""

DRAFT = """THE TORONTO-DOMINION BANK
BANK DRAFT                      Draft No. 05551234
Date: April 9, 2024
Pay to the order of: HARBOUR CONSTRUCTION LTD
CAD $12,134.95
"""


def test_reading():
    c = cq.parse_text(CHEQUE, "scan.jpg")
    assert (c.kind, c.number, c.date, c.payee, c.amount_cents, c.memo) == \
        ("cheque", "126", "2024-04-10", "Jane Smith", 498250, "April rent")
    d = cq.parse_text(DRAFT, "x.pdf")
    assert (d.kind, d.number, d.date, d.payee, d.amount_cents) == \
        ("draft", "5551234", "2024-04-09", "HARBOUR CONSTRUCTION LTD", 1213495)
    assert cq.parse_text("Cheque 0042\nPay to Bob $10.00", "").number == "42"
    assert cq.parse_text("", "CHQ 00130.pdf").number == "130"
    assert cq.line_kind("CHQ#00126-9000000001") == "cheque" and cq.line_number("CHQ#00126-9000000001") == "126"
    assert cq.line_kind("CAD DRAFT 05551234") == "draft" and cq.line_number("CAD DRAFT 05551234") == "5551234"
    assert cq.line_kind("CHQ-Image Fee") is None and cq.line_kind("SEND E-TFR ***k9q") is None


@pytest.fixture
def setup(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    ws.set_config(b, ws.WorkspaceConfig(str(tmp_path / "Company")), connect_inbox=False, connect_documents=False)
    b.import_transactions(b.account_by_code("1000").id, [
        ParsedTransaction(date(2024, 4, 9), "CAD DRAFT 05551234", -1213495),
        ParsedTransaction(date(2024, 4, 15), "CHQ#00126-9000000001", -498250),
        ParsedTransaction(date(2024, 4, 18), "CHQ#00127-4143091953", -500000),
        ParsedTransaction(date(2024, 4, 18), "CHQ-Image Fee", -150)])
    yield b, cq.folder(b)
    b.close()


def tx(books, desc):
    return next(t for t in books.transactions() if t.description == desc)


def _drop(root, name, text, monkeypatch):
    p = root / name
    p.write_bytes(text.encode())
    return p


def test_folder_reads_matches_renames_and_notes(setup, monkeypatch):
    books, root = setup
    assert root.is_dir() and (root / cq.MATCHED_DIR).is_dir()  # created with the company folder
    texts = {"scan0007.jpg": CHEQUE, "draft.pdf": DRAFT, "mystery.jpg": "Pay to Bob\n$77.00\n"}
    monkeypatch.setattr(cq, "read_file", lambda p: cq.parse_text(texts[p.name], p.name))
    for name in texts:
        (root / name).write_bytes(name.encode())
    events = cq.process_folder(books, min_age_seconds=0)
    assert sum(e.matched for e in events) == 2
    chq = root / cq.MATCHED_DIR / "2024" / "2024-04" / "2024-04-15 Cheque 126 Jane Smith $4,982.50.jpg"
    drf = root / cq.MATCHED_DIR / "2024" / "2024-04" / "2024-04-09 Draft 5551234 HARBOUR CONSTRUCTION LTD $12,134.95.pdf"
    assert chq.exists() and drf.exists()
    assert tx(books, "CHQ#00126-9000000001").memo == "✎ Cheque #126 to Jane Smith “April rent”"
    assert tx(books, "CAD DRAFT 05551234").memo == "✎ Draft #5551234 to HARBOUR CONSTRUCTION LTD"
    assert cq.files_for_transaction(books, tx(books, "CHQ#00126-9000000001").id) == [chq]
    assert (root / cq.PENDING_DIR / "mystery.jpg").exists()
    waiting = cq.items(books, "unmatched")[0]
    assert "No cheque / draft line of $77.00" in waiting.reason
    # undo and re-match by hand
    cq.unlink(books, cq.items(books, "matched")[0].id)
    assert len(cq.items(books, "unmatched")) == 2


def test_number_tells_same_amounts_apart_and_category_learned(setup, monkeypatch):
    books, root = setup
    rent = books.account_by_code("6400").id
    books.import_transactions(books.account_by_code("1000").id,
                              [ParsedTransaction(date(2024, 4, 20), "CHQ#00128-555", -500000)])
    texts = {"a.jpg": "No. 128\nPay to the order of Lakeshore Property Mgmt $5,000.00\nDate 2024-04-12",
             "b.jpg": "No. 127\nPay to the order of Lakeshore Property Mgmt $5,000.00\nDate 2024-04-10"}
    monkeypatch.setattr(cq, "read_file", lambda p: cq.parse_text(texts[p.name], p.name))
    (root / "a.jpg").write_bytes(b"a")
    cq.process_folder(books, min_age_seconds=0)
    t128 = tx(books, "CHQ#00128-555")
    assert "Cheque #128" in t128.memo and tx(books, "CHQ#00127-4143091953").memo == ""
    books.set_category(t128.id, rent, source="manual", reviewed=True)
    (root / "b.jpg").write_bytes(b"b")
    cq.process_folder(books, min_age_seconds=0)
    assert tx(books, "CHQ#00127-4143091953").category_id == rent  # same payee → same category


def test_pays_open_bill(setup, monkeypatch):
    books, root = setup
    bill = rx.add_document(books, "bill", "Harbour Construction Ltd", "2024-03-20", "2024-04-19", 1213495,
                           books.account_by_code("6000").id)
    monkeypatch.setattr(cq, "read_file", lambda p: cq.parse_text(DRAFT, p.name))
    (root / "d.pdf").write_bytes(b"d")
    cq.process_folder(books, min_age_seconds=0)
    assert rx.get_document(books, bill).balance_cents == 0
