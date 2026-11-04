from datetime import date

import pytest
from openpyxl import load_workbook

from am_accounting import arap
from am_accounting import receivables as rx
from am_accounting import workspace as ws
from am_accounting.books import Books


@pytest.fixture
def books(tmp_path):
    b = Books.open(tmp_path / "Co.amdb")
    ws.set_config(b, ws.WorkspaceConfig(str(tmp_path / "Company")))
    inc, exp = b.account_by_code("4100").id, b.account_by_code("6400").id
    related = rx.add_control_account(b, "invoice", "AR – Related Party")
    rx.add_document(b, "invoice", "Zeta Corp", "2026-08-01", "2026-08-31", 100000, inc, number="INV-1001")
    rx.add_document(b, "invoice", "Acme", "2026-10-15", "2026-11-14", 50000, inc, number="INV-1002")
    rx.add_document(b, "invoice", "Acme", "2026-09-01", "2026-10-01", 20000, inc, number="INV-1003")
    rx.add_document(b, "invoice", "Sister Co", "2026-10-20", "2026-11-20", 30000, inc, number="INV-1004",
                    control_id=related.id)
    rx.add_document(b, "bill", "Landlord", "2026-10-01", "2026-10-15", 250000, exp, number="R-10")
    yield b, tmp_path
    b.close()


def test_outstanding_sorted_by_customer_with_aging(books):
    b, _ = books
    items = arap.outstanding(b, "invoice", date(2026, 11, 2))
    assert [(i.party, i.number) for i in items] == [("Acme", "INV-1003"), ("Acme", "INV-1002"),
                                                    ("Sister Co", "INV-1004"), ("Zeta Corp", "INV-1001")]
    assert [i.aging for i in items] == ["31–60", "Current", "Current", "61–90"]
    assert items[2].account == "AR – Related Party"
    t = arap.totals(b, "invoice", items, date(2026, 11, 2))
    assert (t.count, t.balance_cents, t.overdue_cents) == (4, 200000, 120000)
    assert t.not_itemized == 0
    assert [i.party for i in arap.outstanding(b, "invoice", date(2026, 11, 2), overdue_only=True)] == \
        ["Acme", "Zeta Corp"]


def test_invoice_on_file_and_attach(books):
    b, tmp = books
    items = arap.outstanding(b, "bill", date(2026, 11, 2))
    assert items[0].document_status == "Missing" and not items[0].has_document
    pdf = tmp / "landlord rent october.pdf"
    pdf.write_bytes(b"%PDF-1.4 rent")
    dest = arap.attach(b, items[0].doc_id, pdf)
    assert dest.exists() and pdf.exists()  # copied, original kept
    again = arap.outstanding(b, "bill", date(2026, 11, 2))[0]
    assert again.has_document and again.document_status == "Attached"


def test_excel_export(books):
    b, tmp = books
    path = arap.write_xlsx(b, tmp / "arap.xlsx", date(2026, 11, 2))
    wb = load_workbook(path)
    assert wb.sheetnames == ["Receivable (AR)", "Payable (AP)"]
    s = wb["Receivable (AR)"]
    head = [c.value for c in s[5]]
    assert head[:2] == ["Account", "Customer"] and head[-1] == "Invoice on file"
    rows = [r for r in s.iter_rows(min_row=6, values_only=True) if r[2]]
    assert [r[1] for r in rows] == ["Acme", "Acme", "Sister Co", "Zeta Corp"]
    assert rows[0][9] == 200.0 and rows[0][11] == "Missing"


def test_ar_posted_without_invoices_is_listed_line_by_line(tmp_path):
    from am_accounting.books import ParsedTransaction

    b = Books.open(tmp_path / "Co2.amdb")
    ws.set_config(b, ws.WorkspaceConfig(str(tmp_path / "Company2")))
    ar = rx.control_account(b, "invoice").id
    bank = b.account_by_code("1000").id
    b.set_opening_balance(ar, "2023-12-31", 5000000)                     # $50,000 opening AR
    b.import_transactions(bank, [
        ParsedTransaction(date(2024, 4, 9), "AB123 TFR-FR 7340218", -2500000),  # money lent / paid out → AR
        ParsedTransaction(date(2024, 4, 12), "E-TRANSFER ***aHN", 300000),      # customer paid part → reduces AR
        ParsedTransaction(date(2024, 4, 20), "DEPOSIT", 120000)])               # pays an invoice (excluded)
    txs = {t.description: t for t in b.transactions()}
    b.set_category(txs["AB123 TFR-FR 7340218"].id, ar, source="manual")
    b.set_category(txs["E-TRANSFER ***aHN"].id, ar, source="manual")
    b.set_memo(txs["E-TRANSFER ***aHN"].id, "↔ e-Transfer from Northwind Labs")
    b.add_journal_entry("2024-04-30", "Accrued management fee – Sister Co",
                        [(ar, 800000, 0), (b.account_by_code("4100").id, 0, 800000)])
    inv = rx.add_document(b, "invoice", "Acme", "2024-04-01", "2024-05-01", 120000, b.account_by_code("4100").id)
    rx.record_payment(b, inv, txs["DEPOSIT"].id)
    items = arap.outstanding(b, "invoice", date(2024, 5, 31))
    assert sorted((i.source, i.amount_cents) for i in items) == [
        ("bank", -300000), ("bank", 2500000), ("journal", 800000), ("opening", 5000000)]
    t = arap.totals(b, "invoice", items, date(2024, 5, 31))
    assert t.balance_cents == t.ledger_cents == 8000000 and t.not_itemized == 0  # the $80,000 is all listed
    nw = next(i for i in items if i.source == "bank" and i.amount_cents < 0)
    assert nw.party == "Northwind Labs" and nw.document_status == "No invoice (bank entry)"
    wo = next(i for i in items if i.amount_cents == 2500000)
    arap.set_party(b, wo.key, "Shareholder loan – J. Morgan")
    pdf = tmp_path / "loan agreement.pdf"
    pdf.write_bytes(b"%PDF")
    arap.attach_to_item(b, wo, pdf)
    wo2 = next(i for i in arap.outstanding(b, "invoice", date(2024, 5, 31)) if i.key == wo.key)
    assert wo2.party == "Shareholder loan – J. Morgan" and wo2.has_document
    path = arap.write_xlsx(b, tmp_path / "x.xlsx", date(2024, 5, 31))
    rows = [r for r in load_workbook(path)["Receivable (AR)"].iter_rows(min_row=6, values_only=True) if r[1]]
    assert len([r for r in rows if r[0] == "Accounts Receivable"]) == 4
    b.close()


def test_own_receivable_accounts_count(tmp_path):
    b = Books.open(tmp_path / "Co3.amdb")
    mine = b.add_account("1250", "Loans Receivable – Shareholder", "asset", "current")
    b.set_opening_balance(mine.id, "2023-12-31", 8000000)
    items = arap.outstanding(b, "invoice", date(2024, 1, 31))
    assert [(i.account, i.amount_cents) for i in items] == [("Loans Receivable – Shareholder", 8000000)]
    assert rx.aging(b, "invoice", date(2024, 1, 31)).ledger_balance == 8000000
    tax = b.account_by_code("2200")  # Sales Tax Payable is not an accounts-payable line
    assert tax.id not in rx.control_ids(b, "bill")
    b.close()
