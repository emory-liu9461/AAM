from datetime import date
from pathlib import Path

import pytest

from am_accounting import doc_folder as df
from am_accounting import receivables as rx
from am_accounting.books import ParsedTransaction


def tx(d, desc, cents):
    return ParsedTransaction(date.fromisoformat(d), desc, cents)


def _docs(books):
    svc = books.account_by_code("4100").id
    cogs = books.account_by_code("5000").id
    a = rx.add_document(books, "invoice", "Cedar & Pine Architects", "2026-01-05", "2026-02-04", 610000, svc,
                        number="INV-1903")
    b = rx.add_document(books, "invoice", "Lakeside Clinic", "2026-01-06", "2026-02-05", 375000, svc,
                        number="INV-1904")
    c = rx.add_document(books, "invoice", "Orchard Street Cafe", "2026-01-07", "2026-02-06", 198000, svc,
                        number="INV-1905")
    d = rx.add_document(books, "invoice", "Summit Logistics", "2026-01-08", "2026-02-07", 250000, svc,
                        number="INV-1906")
    e = rx.add_document(books, "invoice", "Riverbend School", "2026-01-08", "2026-02-07", 250000, svc,
                        number="INV-1907")
    f = rx.add_document(books, "bill", "Harbor Supply Co", "2026-01-03", "2026-02-02", 212500, cogs,
                        number="HS-4411")
    return a, b, c, d, e, f


def test_find_and_auto_match(books):
    a, b, c, d, e, f = _docs(books)
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [
        tx("2026-01-20", "ACH CREDIT INV1903 PAYMENT", 610000),            # number (no dash) -> high
        tx("2026-01-21", "DEPOSIT LAKESIDE CLINIC", 375000),               # name -> high
        tx("2026-01-22", "MOBILE DEPOSIT", 198000),                        # unique amount -> high
        tx("2026-01-23", "MOBILE DEPOSIT", 250000),                        # two invoices of 2,500 -> possible
        tx("2026-01-24", "HARBOR SUPPLY CO", -212500),                     # bill by name -> high
        tx("2025-12-01", "OLD DEPOSIT", 610000),                           # before invoice date: ignored
    ])
    props = {p.doc.number: p for p in rx.find_matches(books)}
    assert props["INV-1903"].confidence == "high" and "INV-1903 in description" in props["INV-1903"].why
    assert props["INV-1904"].confidence == "high"
    assert props["INV-1905"].confidence == "high" and "only payment" in props["INV-1905"].why
    two = [p for p in props.values() if p.amount_cents == 250000]
    assert len(two) == 1 and two[0].confidence == "possible"
    assert props["HS-4411"].confidence == "high" and props["HS-4411"].doc.kind == "bill"
    assert all(p.tx.description != "OLD DEPOSIT" for p in props.values())

    applied = rx.auto_match(books)
    assert {p.doc.number for p in applied} == {"INV-1903", "INV-1904", "INV-1905", "HS-4411"}
    assert rx.get_document(books, a).balance_cents == 0
    assert rx.get_document(books, f).balance_cents == 0
    assert rx.get_document(books, d).balance_cents == 250000  # left for you to confirm
    assert books.account_balance(books.account_by_code("1200").id) == 500000
    assert books.check_balanced()
    # nothing left that's confident
    assert all(p.confidence == "possible" for p in rx.find_matches(books))


def test_manual_category_downgrades_confidence(books):
    _docs(books)
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [tx("2026-01-20", "LAKESIDE CLINIC", 375000)])
    t = books.transactions()[0]
    books.set_category(t.id, books.account_by_code("4000").id, source="manual", reviewed=True)
    p = next(p for p in rx.find_matches(books) if p.doc.number == "INV-1904")
    assert p.confidence == "possible" and "you categorized it" in p.why
    assert rx.auto_match(books) == []


def _folder(books, tmp_path, **kw):
    cfg = df.FolderConfig(str(tmp_path / "OneDrive" / "Invoices & Bills"), **kw)
    df.set_config(books, cfg)
    return cfg


def test_folder_files_are_named_sorted_and_move_when_paid(books, tmp_path):
    a, *_rest, f = _docs(books)
    cfg = _folder(books, tmp_path)
    scan = tmp_path / "scan_0042.pdf"
    scan.write_bytes(b"%PDF-1.4 bill")
    dest = df.attach_file(books, f, scan)
    assert dest == cfg.root / "Bills/Open/2026-01-03 HS-4411 Harbor Supply Co $2,125.00.pdf"
    assert not scan.exists()
    gen = tmp_path / "gen.pdf"
    gen.write_bytes(b"%PDF-1.4 invoice")
    inv_pdf = df.replace_generated(books, a, gen)
    assert inv_pdf == cfg.root / "Invoices/Open/2026-01-05 INV-1903 Cedar & Pine Architects $6,100.00.pdf"

    # pay the invoice: its file moves to Paid/<year>
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [tx("2026-01-20", "INV-1903", 610000)])
    rx.auto_match(books)
    moves = df.refile(books)
    assert len(moves) == 1
    paid = cfg.root / "Invoices/Paid/2026/2026-01-05 INV-1903 Cedar & Pine Architects $6,100.00.pdf"
    assert paid.exists() and not inv_pdf.exists()
    assert not (cfg.root / "Invoices/Open").exists() or not any((cfg.root / "Invoices/Open").iterdir())

    # rename the customer: the file is renamed
    rx.update_document(books, a, party="Cedar and Pine LLP", issue_date="2026-01-05", due_date="2026-02-04",
                       amount_cents=610000, category_id=books.account_by_code("4100").id, number="INV-1903")
    df.refile(books)
    assert (cfg.root / "Invoices/Paid/2026/2026-01-05 INV-1903 Cedar and Pine LLP $6,100.00.pdf").exists()


def test_layouts_and_reorganize(books, tmp_path):
    a, *_ = _docs(books)
    cfg = _folder(books, tmp_path)
    for i in range(2):
        p = tmp_path / f"page{i}.PDF"
        p.write_bytes(b"x")
        df.attach_file(books, a, p)
    names = sorted(x.name for x in (cfg.root / "Invoices/Open").iterdir())
    assert names == ["2026-01-05 INV-1903 Cedar & Pine Architects $6,100.00 (2).pdf",
                     "2026-01-05 INV-1903 Cedar & Pine Architects $6,100.00.pdf"]
    df.set_config(books, df.FolderConfig(cfg.folder, "party_year", "party_number_date"))
    files = sorted(p.relative_to(cfg.root).as_posix() for p in cfg.root.rglob("*.pdf"))
    assert files == ["Invoices/Cedar & Pine Architects/2026/Cedar & Pine Architects INV-1903 2026-01-05 (2).pdf",
                     "Invoices/Cedar & Pine Architects/2026/Cedar & Pine Architects INV-1903 2026-01-05.pdf"]
    df.set_config(books, df.FolderConfig(cfg.folder, "year_month", "original"))
    files = sorted(p.relative_to(cfg.root).as_posix() for p in cfg.root.rglob("*.PDF"))
    assert files == ["Invoices/2026/01 - January/page0.PDF", "Invoices/2026/01 - January/page1.PDF"]
    # moving the whole folder (e.g. into OneDrive) carries the files along
    new_root = tmp_path / "Elsewhere"
    df.set_config(books, df.FolderConfig(str(new_root), "status"))
    assert len(list(new_root.rglob("*.pdf"))) == 2 and not [p for p in cfg.root.rglob("*") if p.is_file()]
    assert df.describe_layout(df.get_config(books)) == \
        "Invoices/Open/2026-10-07 INV-1903 Cedar & Pine Architects $6,100.00.pdf"


def test_drop_folder_detach_and_delete(books, tmp_path):
    a, *_rest, f = _docs(books)
    cfg = _folder(books, tmp_path)
    dropped = cfg.root / df.DROP_DIR / "Harbor invoice 4411.pdf"
    dropped.write_bytes(b"x")
    assert df.unfiled(books) == [dropped]
    df.attach_file(books, f, dropped)
    assert df.unfiled(books) == []
    fid = df.files_for(books, f)[0].id
    back = df.detach_file(books, fid)
    assert back == cfg.root / df.DROP_DIR / "Harbor invoice 4411.pdf" and back.exists()
    df.attach_file(books, f, back)
    df.release_files(books, f)
    rx.delete_document(books, f)
    assert (cfg.root / df.REMOVED_DIR / "2026-01-03 HS-4411 Harbor Supply Co $2,125.00.pdf").exists()


def test_requires_folder(books, tmp_path):
    a, *_ = _docs(books)
    p = tmp_path / "x.pdf"
    p.write_bytes(b"x")
    with pytest.raises(ValueError, match="folder"):
        df.attach_file(books, a, p)
    assert df.refile(books) == [] and df.unfiled(books) == []


def test_invoice_html(books):
    a, *_ = _docs(books)
    html = df.invoice_html(books, rx.get_document(books, a))
    assert "INV-1903" in html and "Cedar &amp; Pine Architects" in html and "$6,100.00" in html
