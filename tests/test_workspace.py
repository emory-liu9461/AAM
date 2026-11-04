from datetime import date

import pytest

from am_accounting import inbox, doc_folder
from am_accounting import workspace as ws
from am_accounting.books import Books, CompanyProfile, ParsedTransaction


def fake_pdf(html, path):
    from pathlib import Path

    Path(path).write_text(html, encoding="utf-8")


def test_periods():
    today = date(2026, 11, 2)
    assert [p.key for p in ws.completed_periods("monthly", today, after_key="2026-08")] == ["2026-09", "2026-10"]
    m = ws.completed_periods("monthly", today)[-1]
    assert (m.start, m.end, m.label, m.folder) == (date(2026, 10, 1), date(2026, 10, 31), "October 2026",
                                                   "2026-10 October")
    w = ws.completed_periods("weekly", today)[-1]  # Mon 26 Oct – Sun 1 Nov
    assert (w.start, w.end) == (date(2026, 10, 26), date(2026, 11, 1))
    q = ws.completed_periods("quarterly", today)[-1]
    assert (q.start, q.end, q.label) == (date(2026, 7, 1), date(2026, 9, 30), "Q3 2026")
    y = ws.completed_periods("yearly", today)[-1]
    assert (y.start, y.end) == (date(2025, 1, 1), date(2025, 12, 31))
    d = ws.completed_periods("daily", today)[-1]
    assert d.start == d.end == date(2026, 11, 1)
    assert ws.completed_periods("off", today) == []
    assert len(ws.completed_periods("monthly", today)) == 12  # catch-up is capped


@pytest.fixture
def company(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    b.set_profile(CompanyProfile("Northwind Labs"))
    bank = b.account_by_code("1000").id
    b.set_opening_balance(bank, "2026-08-31", 100000)
    b.import_transactions(bank, [ParsedTransaction(date(2026, 9, 5), "SALE", 50000),
                                 ParsedTransaction(date(2026, 10, 7), "SALE", 25000),
                                 ParsedTransaction(date(2026, 10, 9), "RENT", -10000)])
    yield b
    b.close()


def test_setup_creates_folders_and_connects_inbox_and_invoices(company, tmp_path):
    root = tmp_path / "OneDrive" / "AM Accounting" / "Northwind Labs"
    ws.set_config(company, ws.WorkspaceConfig(str(root)))
    for sub in (ws.BANK_DIR, ws.DOCS_DIR, ws.REPORTS_DIR, ws.BACKUP_DIR):
        assert (root / sub).is_dir()
    icfg = inbox.get_config(company)
    assert icfg.enabled and icfg.folder == str(root / ws.BANK_DIR)
    assert (root / ws.BANK_DIR / "Business Checking").is_dir()  # per-account drop folders
    assert doc_folder.get_config(company).folder == str(root / ws.DOCS_DIR)
    assert ws.get_config(company).frequency == "monthly"


def test_schedule_saves_each_completed_period_once(company, tmp_path):
    root = tmp_path / "Company"
    ws.set_config(company, ws.WorkspaceConfig(str(root), "monthly"))
    # first run: only the latest completed month (no flood of history)
    results = ws.run_schedule(company, pdf_writer=fake_pdf, today=date(2026, 11, 2))
    assert [r.period.key for r in results] == ["2026-10"] and not results[0].errors
    folder = root / ws.REPORTS_DIR / "2026" / "2026-10 October"
    names = sorted(p.name for p in folder.iterdir())
    assert "Income Statement - October 2026.pdf" in names
    assert "Income Statement - October 2026.xlsx" in names
    assert "Balance Sheet - 31 Oct 2026.xlsx" in names
    assert "Cash Flow - October 2026.pdf" in names
    assert "Income Statement - Year to Date 31 Oct 2026.xlsx" in names
    assert results[0].backup and results[0].backup.name == "Northwind Labs 2026-10.amdb"
    # backup is a working copy of the books
    copy = Books.open(results[0].backup, create=False)
    assert len(copy.transactions()) == 3
    copy.close()
    # running again the same day does nothing; next month catches up both missed months
    assert ws.run_schedule(company, pdf_writer=fake_pdf, today=date(2026, 11, 20)) == []
    later = ws.run_schedule(company, pdf_writer=fake_pdf, today=date(2027, 1, 3))
    assert [r.period.key for r in later] == ["2026-11", "2026-12"]
    assert ws.next_due(company, date(2027, 1, 3)) == date(2027, 2, 1)


def test_excel_report_has_numbers(company, tmp_path):
    from openpyxl import load_workbook

    from am_accounting import reports

    path = tmp_path / "is.xlsx"
    ws.report_to_xlsx(reports.income_statement(company, "2026-10-01", "2026-10-31"), path)
    sheet = load_workbook(path).active
    values = {r[0]: r[1] for r in sheet.iter_rows(min_row=6, values_only=True) if r and r[0]}
    assert values["Net Income"] == 150.0  # 250 deposit - 100 rent, stored as a number


def test_backups_are_pruned(company, tmp_path):
    cfg = ws.WorkspaceConfig(str(tmp_path / "C"), keep_backups=2)
    ws.set_config(company, cfg, connect_inbox=False, connect_documents=False)
    for stamp in ("a", "b", "c"):
        ws.backup_books(company, cfg, stamp=stamp)
    assert sorted(p.name for p in (tmp_path / "C" / ws.BACKUP_DIR).iterdir()) == \
        ["Northwind Labs b.amdb", "Northwind Labs c.amdb"]


def test_off_and_unset(company):
    assert ws.run_schedule(company, today=date(2026, 11, 2)) == []  # no folder chosen
    ws.set_config(company, ws.WorkspaceConfig("", "off"))
    assert ws.next_due(company) is None


def test_date_from_name():
    from am_accounting.workspace import date_from_name as d

    assert d("2026-01 Business Checking.pdf") == date(2026, 1, 31)
    assert d("2026-01-15 to 2026-02-14 Business Checking.pdf") == date(2026, 2, 14)
    assert d("Income Statement - October 2026.pdf") == date(2026, 10, 31)
    assert d("Balance Sheet - 31 Oct 2026.xlsx") == date(2026, 10, 31)
    assert d("Cash Flow - Q3 2026.pdf") == date(2026, 9, 30)
    assert d("Income Statement - Week of 26 Oct 2026.pdf") == date(2026, 11, 1)
    assert d("Income Statement - 2025.pdf") == date(2025, 12, 31)
    assert d("Northwind Labs 2026-10.amdb") == date(2026, 10, 31)
    assert d("Northwind Labs 2026-W44.amdb") == date(2026, 11, 1)
    assert d("Northwind Labs 2026-Q3.amdb") == date(2026, 9, 30)
    assert d("eStatement_88123.pdf") is None


def test_archive_moves_old_items_in_every_folder(company, tmp_path):
    import os
    import time

    from am_accounting import receivables as rx
    from am_accounting.pdf_to_excel import COMBINED_NAME

    root = tmp_path / "Co"
    ws.set_config(company, ws.WorkspaceConfig(str(root), "monthly", archive_months=12))
    today = date(2026, 11, 2)
    bank = root / ws.BANK_DIR / "Statements" / "Business Checking"
    for name, year in [("2025-09 Business Checking.pdf", "2025"), ("2025-11 Business Checking.pdf", "2025"),
                       ("2026-10 Business Checking.pdf", "2026")]:
        (bank / year).mkdir(parents=True, exist_ok=True)
        (bank / year / name).write_bytes(b"%PDF")
    (root / ws.BANK_DIR / "Statements" / COMBINED_NAME).write_bytes(b"xlsx")
    old_scan = bank / "2025" / "eStatement_old.pdf"
    old_scan.write_bytes(b"%PDF")
    t = time.mktime((2025, 3, 1, 0, 0, 0, 0, 0, -1))
    os.utime(old_scan, (t, t))  # no date in the name: its file date is used
    rep = root / ws.REPORTS_DIR
    for folder, name in [("2025/2025-06 June", "Income Statement - June 2025.pdf"),
                         ("2026/2026-10 October", "Income Statement - October 2026.pdf")]:
        (rep / folder).mkdir(parents=True, exist_ok=True)
        (rep / folder / name).write_bytes(b"%PDF")
    (root / ws.BACKUP_DIR / "Northwind Labs 2025-08.amdb").write_bytes(b"x")
    (root / ws.BACKUP_DIR / "Northwind Labs 2026-10.amdb").write_bytes(b"x")
    svc = company.account_by_code("4100").id
    paid_old = rx.add_document(company, "invoice", "Old Client", "2025-06-01", "2025-07-01", 50000, svc,
                               number="INV-1")
    open_old = rx.add_document(company, "invoice", "Slow Payer", "2025-05-01", "2025-06-01", 25000, svc,
                               number="INV-2")
    company.import_transactions(company.account_by_code("1000").id,
                                [ParsedTransaction(date(2025, 6, 20), "OLD CLIENT INV-1", 50000)])
    rx.auto_match(company)
    from am_accounting import doc_folder

    for d in (paid_old, open_old):
        f = tmp_path / f"{d}.pdf"
        f.write_bytes(b"%PDF")
        doc_folder.attach_file(company, d, f)

    ws.archive_old(company, today=today)

    arch = root / ws.BANK_DIR / "Archive"
    assert (arch / "Business Checking" / "2025" / "2025-09 Business Checking.pdf").exists()
    assert (arch / "Business Checking" / "2025" / "eStatement_old.pdf").exists()
    assert (bank / "2025" / "2025-11 Business Checking.pdf").exists()  # within 12 months: stays
    assert (bank / "2026" / "2026-10 Business Checking.pdf").exists()
    assert (root / ws.BANK_DIR / "Statements" / COMBINED_NAME).exists()  # the running workbook stays
    assert (rep / "Archive" / "2025" / "2025-06 June" / "Income Statement - June 2025.pdf").exists()
    assert not (rep / "2025").exists()  # emptied folders are tidied away
    assert (rep / "2026" / "2026-10 October" / "Income Statement - October 2026.pdf").exists()
    assert (root / ws.BACKUP_DIR / "Archive" / "Northwind Labs 2025-08.amdb").exists()
    assert (root / ws.BACKUP_DIR / "Northwind Labs 2026-10.amdb").exists()
    docs = root / ws.DOCS_DIR
    assert (docs / "Archive" / "Invoices" / "2025" / "2025-06-01 INV-1 Old Client $500.00.pdf").exists()
    assert (docs / "Invoices" / "Open" / "2025-05-01 INV-2 Slow Payer $250.00.pdf").exists()  # unpaid stays
    # the inbox never mistakes the Archive folder for a bank account
    from am_accounting.inbox import StatementInbox

    assert StatementInbox().scan_company(company.path, require_stable=False) == []
    # archiving is off when set to 0
    cfg = ws.get_config(company)
    cfg.archive_months = 0
    ws.set_config(company, cfg)
    assert ws.archive_old(company, today=today) == []
