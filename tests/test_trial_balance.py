from datetime import date

from openpyxl import load_workbook

from am_accounting import trial_balance as tb
from am_accounting import workspace as ws
from test_workspace import company, fake_pdf  # noqa: F401 - fixture


def _sheet_rows(sheet, first):
    return [r for r in sheet.iter_rows(min_row=first, values_only=True)]


def test_rows_split_opening_and_period_activity(company):  # noqa: F811
    rows = {r.code: r for r in tb.rows(company, "2026-10-01", "2026-10-31")}
    bank = rows["1000"]
    assert (bank.opening, bank.debits, bank.credits, bank.closing) == (150000, 25000, 10000, 165000)
    assert sum(r.closing for r in rows.values()) == 0  # debits = credits
    types = [r.type for r in tb.rows(company, "2026-10-01", "2026-10-31")]
    assert types == sorted(types, key=tb.TYPE_ORDER.index)  # assets, liabilities, equity, income, expenses


def test_workbook_sheets_and_numbers(company, tmp_path):  # noqa: F811
    path = tb.write_xlsx(company, tmp_path / "tb.xlsx", date(2026, 10, 31), date(2026, 10, 1))
    wb = load_workbook(path)
    assert wb.sheetnames == ["Trial Balance", "Working TB", "Summary"]
    s = wb["Trial Balance"]
    assert s["A1"].value == "Northwind Labs" and s["A3"].value == "As of October 31, 2026"
    assert [c.value for c in s[5]] == ["Account #", "Account", "Type", "Debit", "Credit"]
    body = [r for r in _sheet_rows(s, 6) if r[0]]
    assert ("1000", "Business Checking", "Assets", 1650.0, None) in body
    assert sum(r[3] or 0 for r in body) == sum(r[4] or 0 for r in body)  # stored as numbers, balanced
    total = next(r for r in _sheet_rows(s, 6) if r[1] == "Total")
    assert total[3].startswith("=ROUND(SUM(D6:") and total[4].startswith("=ROUND(SUM(E6:")
    w = wb["Working TB"]
    assert [c.value for c in w[5]][:7] == ["Account #", "Account", "Type", "Opening", "Debits", "Credits", "Closing"]
    bank = next(r for r in _sheet_rows(w, 6) if r[0] == "1000")
    assert bank[3:6] == (1500.0, 250.0, 100.0) and bank[6].startswith("=ROUND(D")
    summary = wb["Summary"]
    assert [r[0] for r in _sheet_rows(summary, 6)] == ["Assets", "Liabilities", "Equity", "Income", "Expenses",
                                                       "Total"]


def test_saved_to_trial_balance_folder_on_schedule(company, tmp_path):  # noqa: F811
    root = tmp_path / "Company"
    ws.set_config(company, ws.WorkspaceConfig(str(root), "monthly"))
    assert (root / tb.TB_DIR).is_dir()
    assert ws.get_config(company).trial_balance  # on by default
    res = ws.run_schedule(company, pdf_writer=fake_pdf, today=date(2026, 11, 2))
    saved = root / tb.TB_DIR / "2026" / "Trial Balance - 31 Oct 2026.xlsx"
    assert saved.exists() and saved in res[0].files and not res[0].errors
    # manual save for another date, and switching it off
    assert tb.save_to_folder(company, date(2026, 9, 30)) == root / tb.TB_DIR / "2026" / "Trial Balance - 30 Sep 2026.xlsx"
    cfg = ws.get_config(company)
    cfg.trial_balance = False
    ws.set_config(company, cfg)
    assert not ws.get_config(company).trial_balance
    res = ws.run_schedule(company, pdf_writer=fake_pdf, today=date(2026, 12, 2))
    assert not (root / tb.TB_DIR / "2026" / "Trial Balance - 30 Nov 2026.xlsx").exists()


def test_old_trial_balances_are_archived(company, tmp_path):  # noqa: F811
    root = tmp_path / "Company"
    ws.set_config(company, ws.WorkspaceConfig(str(root), "monthly"))
    old = tb.save_to_folder(company, date(2025, 6, 30))
    ws.archive_old(company, today=date(2026, 11, 2))
    assert not old.exists()
    assert (root / tb.TB_DIR / ws.ARCHIVE / "2025" / old.name).exists()
