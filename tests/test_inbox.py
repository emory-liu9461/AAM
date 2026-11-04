import shutil
import threading
from pathlib import Path

import pytest

from am_accounting.books import Books, CompanyProfile
from am_accounting.companies import CompanyRegistry
from am_accounting.inbox import (ARCHIVE_DIR, FAILED_DIR, PROCESSED_DIR, InboxConfig, InboxWatcher, StatementInbox,
                                 detect_cloud_folders, get_config, main, set_config, suggested_inbox)

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def company(tmp_path):
    reg = CompanyRegistry(tmp_path / "c.json", tmp_path / "Companies")
    path = reg.create(CompanyProfile("Alpha Bakery"))
    inbox = tmp_path / "OneDrive - Contoso" / "AM Accounting Inbox" / "Alpha Bakery"
    b = Books.open(path)
    set_config(b, InboxConfig(True, str(inbox), ""))
    b.close()
    return path, inbox


def _books(path):
    return Books.open(path, create=False)


def test_config_roundtrip_and_folders(company):
    path, inbox = company
    b = _books(path)
    assert get_config(b) == InboxConfig(True, str(inbox), "", "account_year", True, False)
    assert (inbox / "Business Checking").is_dir() and (inbox / "Business Savings").is_dir()
    with pytest.raises(ValueError):
        set_config(b, InboxConfig(True, "", ""))
    with pytest.raises(ValueError):
        set_config(b, InboxConfig(True, str(inbox), "6000"))  # not a bank account
    b.close()


def test_imports_from_account_folders_and_files_them(company):
    path, inbox = company
    shutil.copy(FIX / "chase_style.csv", inbox / "Business Checking" / "jan.csv")
    shutil.copy(FIX / "sample.ofx", inbox / "Business Savings" / "jan.qfx")
    (inbox / "Business Checking" / "~$jan.xlsx").write_text("office lock file")
    (inbox / "Business Checking" / "feb.csv.partial").write_text("still downloading")

    events = []
    box = StatementInbox(events.append)
    assert box.scan_company(path) == []          # first sighting: wait until files are stable
    box.scan_company(path)
    by_file = {e.file: e for e in events}
    assert by_file["jan.csv"].ok and by_file["jan.csv"].imported == 10 and by_file["jan.csv"].categorized == 9
    assert by_file["jan.qfx"].ok and by_file["jan.qfx"].account == "Business Savings"
    assert (inbox / ARCHIVE_DIR / "Business Checking" / "2026" / "2026-01 Business Checking.csv").exists()
    assert (inbox / ARCHIVE_DIR / "Business Savings" / "2026" / "2026-01 Business Savings.qfx").exists()
    assert (inbox / "Business Checking" / "~$jan.xlsx").exists()        # ignored
    assert (inbox / "Business Checking" / "feb.csv.partial").exists()   # ignored

    b = _books(path)
    assert len(b.transactions(bank_account_id=b.account_by_code("1000").id)) == 10
    assert len(b.transactions(bank_account_id=b.account_by_code("1010").id)) == 3
    assert b.check_balanced()
    b.close()

    # Same statement dropped again: nothing new, still filed as processed
    shutil.copy(FIX / "chase_style.csv", inbox / "Business Checking" / "jan.csv")
    events.clear()
    box.scan_company(path, require_stable=False)
    assert events[0].ok and events[0].imported == 0 and events[0].duplicates == 10
    # identical file already filed: no second copy kept
    assert not (inbox / "Business Checking" / "jan.csv").exists()
    assert not list((inbox / ARCHIVE_DIR).rglob("*(2)*"))


def test_root_files_use_default_account_or_fail(company):
    path, inbox = company
    shutil.copy(FIX / "split_columns.csv", inbox / "statement.csv")
    events = []
    StatementInbox(events.append).scan_company(path, require_stable=False)
    assert not events[0].ok and "default bank account" in events[0].error
    assert (inbox / FAILED_DIR / "statement.csv").exists()
    assert (inbox / FAILED_DIR / "statement.csv.error.txt").exists()

    b = _books(path)
    set_config(b, InboxConfig(True, str(inbox), "1000"))
    b.close()
    shutil.move(inbox / FAILED_DIR / "statement.csv", inbox / "statement.csv")
    events.clear()
    StatementInbox(events.append).scan_company(path, require_stable=False)
    assert events[0].ok and events[0].imported == 3 and events[0].account == "Business Checking"


def test_bad_files_and_unknown_folders(company):
    path, inbox = company
    (inbox / "Business Checking" / "junk.csv").write_text("hello,world\n")
    (inbox / "Old Bank").mkdir()
    shutil.copy(FIX / "sample.ofx", inbox / "Old Bank" / "x.ofx")
    events = []
    StatementInbox(events.append).scan_company(path, require_stable=False)
    errs = {e.file: e.error for e in events if not e.ok}
    assert "junk.csv" in errs and "x.ofx" in errs and "Old Bank" in errs["x.ofx"]
    assert (inbox / FAILED_DIR / "Old Bank" / "x.ofx").exists()


def test_disabled_inbox_is_ignored(company):
    path, inbox = company
    b = _books(path)
    set_config(b, InboxConfig(False, str(inbox), ""))
    b.close()
    shutil.copy(FIX / "chase_style.csv", inbox / "Business Checking" / "jan.csv")
    assert StatementInbox().scan_company(path, require_stable=False) == []
    assert (inbox / "Business Checking" / "jan.csv").exists()


def test_new_bank_account_gets_folder(company):
    path, inbox = company
    b = _books(path)
    b.add_account("1020", "Chase Card 4421", "asset", is_bank=True)
    b.close()
    StatementInbox().scan_company(path)
    assert (inbox / "Chase Card 4421").is_dir()


def test_background_watcher(company):
    path, inbox = company
    done = threading.Event()
    w = InboxWatcher(lambda: [str(path)], lambda e: done.set(), interval=0.05)
    w.start()
    try:
        shutil.copy(FIX / "sample.ofx", inbox / "Business Checking" / "a.ofx")
        assert done.wait(10)
    finally:
        w.stop()
    assert not w.running


def test_cli_once(company, capsys):
    path, inbox = company
    shutil.copy(FIX / "sample.ofx", inbox / "Business Checking" / "a.ofx")
    assert main([str(path), "--once"]) == 0
    assert "3 new transaction" in capsys.readouterr().out


def test_detect_onedrive(tmp_path):
    home = tmp_path / "home"
    (home / "OneDrive - Contoso").mkdir(parents=True)
    (home / "Library" / "CloudStorage" / "OneDrive-Personal").mkdir(parents=True)
    (home / "Dropbox").mkdir()
    win_business = tmp_path / "win" / "OneDrive - Fabrikam"
    win_business.mkdir(parents=True)
    found = detect_cloud_folders(home, {"OneDriveCommercial": str(win_business), "OneDrive": str(win_business)})
    labels = [l for l, _ in found]
    assert labels[0] == "OneDrive (work or school)"
    assert "OneDrive (Personal)" in labels and "OneDrive (Contoso)" in labels and "Dropbox" in labels
    assert len([p for _, p in found if p == win_business]) == 1     # no duplicates
    assert suggested_inbox(Path("/od"), "Alpha/Bakery") == Path("/od/AM Accounting Inbox/AlphaBakery")


def test_archive_layouts(tmp_path):
    from datetime import date

    from am_accounting.inbox import archive_path, statement_period

    root = Path("/inbox")
    jan = [date(2026, 1, 2), date(2026, 1, 30)]
    span = [date(2026, 1, 15), date(2026, 2, 14)]
    cfg = lambda org, rename=True: InboxConfig(True, "/inbox", "", org, rename)  # noqa: E731
    assert statement_period(jan) == "2026-01"
    assert statement_period(span) == "2026-01-15 to 2026-02-14"
    assert archive_path(root, cfg("account_year"), "Business Checking", jan, "Stmt.PDF") == \
        root / "Statements/Business Checking/2026/2026-01 Business Checking.pdf"
    assert archive_path(root, cfg("year_month"), "Business Checking", span, "s.pdf") == \
        root / "Statements/2026/02 - February/2026-01-15 to 2026-02-14 Business Checking.pdf"
    assert archive_path(root, cfg("account"), "Chase: Card", jan, "s.csv") == \
        root / "Statements/Chase Card/2026-01 Chase Card.csv"
    assert archive_path(root, cfg("account_year", rename=False), "Savings", jan, "orig.pdf") == \
        root / "Statements/Savings/2026/orig.pdf"
    assert archive_path(root, cfg("as_received"), "Savings", jan, "orig.pdf", "Business Savings") == \
        root / "Processed/Business Savings/orig.pdf"


def test_pdf_filed_with_excel_copy(company):
    pytest.importorskip("reportlab")
    from test_pdf_to_excel import make_pdf

    path, inbox = company
    b = _books(path)
    set_config(b, InboxConfig(True, str(inbox), "", "year_month", True, True))
    b.close()
    make_pdf(inbox / "Business Checking" / "eStatement_88123.pdf")
    events = []
    StatementInbox(events.append).scan_company(path, require_stable=False)
    assert events[0].ok and events[0].imported == 5
    filed = inbox / ARCHIVE_DIR / "2026" / "01 - January" / "2026-01 Business Checking.pdf"
    assert filed.exists() and filed.with_suffix(".xlsx").exists()
    assert events[0].filed_as == str(filed) and "filed as 2026-01 Business Checking.pdf" in events[0].describe()
    # a second, different statement for the same month is kept alongside, not overwritten
    shutil.copy(FIX / "chase_style.csv", inbox / "Business Checking" / "more.csv")
    StatementInbox().scan_company(path, require_stable=False)
    assert (filed.parent / "2026-01 Business Checking.csv").exists()


def test_inbox_auto_matches_invoices(company):
    from am_accounting import receivables as rx

    path, inbox = company
    b = _books(path)
    rx.add_document(b, "bill", "Comcast", "2026-01-01", "2026-01-15", 12999, b.account_by_code("6600").id)
    b.close()
    shutil.copy(FIX / "sample.ofx", inbox / "Business Checking" / "jan.ofx")
    events = []
    StatementInbox(events.append).scan_company(path, require_stable=False)
    assert events[0].matched == 1 and "1 matched" in events[0].describe()
    b = _books(path)
    assert rx.documents(b, "bill", status="open") == []
    b.close()


def test_inbox_pdfs_go_into_one_workbook(company):
    pytest.importorskip("reportlab")
    from openpyxl import load_workbook

    from am_accounting.pdf_to_excel import COMBINED_NAME
    from test_importers import STATEMENT_TEXT
    from test_pdf_to_excel import make_pdf

    path, inbox = company
    make_pdf(inbox / "Business Checking" / "jan.pdf")
    make_pdf(inbox / "Business Savings" / "feb.pdf", STATEMENT_TEXT.replace("January", "February").replace("01/", "02/"))
    StatementInbox().scan_company(path, require_stable=False)
    wb = load_workbook(inbox / ARCHIVE_DIR / COMBINED_NAME)
    assert wb.sheetnames[:2] == ["All Transactions", "Summary"] and len(wb.sheetnames) == 4
    accounts = {r[2] for r in wb["Summary"].iter_rows(min_row=2, values_only=True)}
    assert accounts == {"Business Checking", "Business Savings"}
    # the workbook isn't picked up as a statement on the next scan
    assert StatementInbox().scan_company(path, require_stable=False) == []
