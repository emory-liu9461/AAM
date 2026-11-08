import sqlite3
from datetime import date

import pytest

from am_accounting import chart_of_accounts as coa
from am_accounting import reports
from am_accounting.books import Books, CompanyProfile, ParsedTransaction
from am_accounting.categorizer import auto_categorize


def tx(d, desc, cents):
    return ParsedTransaction(date.fromisoformat(d), desc, cents)


def test_templates():
    corp = Books.open(":memory:", template="corporation")
    names = {a.name for a in corp.accounts(types=("equity",))}
    assert {"Common Stock", "Additional Paid-in Capital", "Dividends Paid", "Treasury Stock"} <= names
    minimal = Books.open(":memory:", template="minimal")
    assert len(minimal.accounts()) == 5 and all(minimal.role(k) for k in coa.ROLE_KEYS)
    with pytest.raises(ValueError):
        Books.open(":memory:", template="nope")
    assert coa.template_for_entity("S corporation") == "corporation"
    assert coa.template_for_entity("Single-member LLC") == "llc"


def test_add_custom_accounts_of_every_type(books):
    for t in coa.ACCOUNT_TYPES:
        for sub in coa.SUBTYPES[t]:
            if sub == "bank":
                continue
            a = books.add_account(books.next_account_code(t), f"My {t} {sub}", t, sub)
            assert (a.type, a.subtype) == (t, sub)
    with pytest.raises(ValueError):
        books.add_account("6250", "Duplicate number", "expense")
    with pytest.raises(ValueError):
        books.add_account("7777", "Bad group", "expense", "current")
    with pytest.raises(ValueError):
        books.add_account("7778", "", "expense")


def test_sub_accounts_roll_up_on_reports(books):
    bank = books.account_by_code("1000").id
    rev = books.account_by_code("4000")
    online = books.add_account("4010", "Online Sales", "income", parent_id=rev.id)
    retail = books.add_account("4020", "In-Store Sales", "income", parent_id=rev.id)
    web = books.add_account("4011", "Website", "income", parent_id=online.id)
    assert web.subtype == rev.subtype  # inherits the parent's group
    books.import_transactions(bank, [tx("2026-01-05", "A", 10000), tx("2026-01-06", "B", 5000),
                                     tx("2026-01-07", "C", 2500), tx("2026-01-08", "D", 1000)])
    ts = books.transactions()
    for t, acct in zip(sorted(ts, key=lambda t: t.date), [online, retail, web, rev]):
        books.set_category(t.id, acct.id)
    inc = reports.income_statement(books, "2026-01-01", "2026-01-31")
    labels = [(l.label, l.values, l.indent) for l in inc.lines]
    assert ("Sales Revenue", [], 1) in labels
    assert ("Sales Revenue - Other", [1000], 2) in labels
    assert ("Online Sales", [], 2) in labels
    assert ("Online Sales - Other", [10000], 3) in labels
    assert ("Website", [2500], 3) in labels
    assert ("Total Online Sales", [12500], 2) in labels
    assert ("Total Sales Revenue", [18500], 1) in labels
    assert inc.value("Total Revenue") == 18500
    assert "groupsum" in inc.to_html()

    # cycles and cross-type parents are refused
    with pytest.raises(ValueError):
        books.update_account(rev.id, code="4000", name="Sales Revenue", type_="income", subtype="operating",
                             parent_id=web.id)
    with pytest.raises(ValueError):
        books.add_account("6011", "Bad", "expense", parent_id=rev.id)
    # moving the parent to another group moves the children
    books.update_account(rev.id, code="4000", name="Sales Revenue", type_="income", subtype="other")
    assert books.account(web.id).subtype == "other"


def test_system_accounts_can_be_renamed_and_renumbered(books):
    unc = books.role(coa.UNCATEGORIZED_EXPENSE)
    books.update_account(unc.id, code="9999", name="Ask My Accountant", type_="expense", subtype="other")
    obe = books.role(coa.OPENING_BALANCE_EQUITY)
    books.update_account(obe.id, code="3999", name="Opening Equity", type_="equity", subtype="opening")
    meals = books.account_by_code("6250")
    books.update_account(meals.id, code="7250", name="Client Entertainment", type_="expense",
                         subtype="operating")
    bank = books.account_by_code("1000").id
    books.set_opening_balance(bank, "2025-12-31", 100000)
    books.import_transactions(bank, [tx("2026-01-03", "STARBUCKS 123", -500), tx("2026-01-04", "ZZZ", -700)])
    auto_categorize(books)
    by_desc = {t.description: t for t in books.transactions()}
    assert books.account(by_desc["STARBUCKS 123"].category_id).name == "Client Entertainment"
    inc = reports.income_statement(books, "2026-01-01", "2026-01-31")
    assert ("Ask My Accountant", [700], 1) in [(l.label, l.values, l.indent) for l in inc.lines]
    assert all(reports.balance_sheet(books, "2026-01-31").checks.values())
    with pytest.raises(ValueError):
        books.update_account(unc.id, code="9999", name="X", type_="income", subtype="other")
    with pytest.raises(ValueError):
        books.delete_account(unc.id)


def test_builtin_suggestions_toggle(books):
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [tx("2026-01-03", "STARBUCKS 123", -500)])
    books.builtin_suggestions = False
    assert auto_categorize(books) == {"unmatched": 1}
    books.builtin_suggestions = True
    assert auto_categorize(books)["keyword"] == 1


def test_delete_and_merge(books):
    bank = books.account_by_code("1000").id
    spare = books.add_account("6123", "Unused", "expense")
    books.add_rule("unused", spare.id)
    books.delete_account(spare.id)
    assert all(r.account_id != spare.id for r in books.rules())

    old = books.account_by_code("6300")
    new = books.account_by_code("6450")
    books.import_transactions(bank, [tx("2026-01-03", "STAPLES", -1500)])
    t = books.transactions()[0]
    books.set_category(t.id, old.id)
    books.add_rule("staples", old.id)
    with pytest.raises(ValueError):
        books.delete_account(old.id)  # has activity
    books.merge_account(old.id, new.id)
    assert books.transaction(t.id).category_id == new.id
    assert books.rules()[-1].account_id == new.id
    assert books.account_balance(new.id) == 1500
    assert books.check_balanced()
    with pytest.raises(ValueError):
        books.merge_account(new.id, bank)  # category into bank


def test_custom_statement_headings(books):
    books.set_profile(CompanyProfile("Acme Inc", entity_type="C corporation"))
    assert books.labels()["equity"] == "Shareholders' Equity"
    books.set_labels({"revenue": "Net Sales", "operating_expenses": "Selling, General & Administrative",
                      "equity": ""})
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [tx("2026-01-03", "SALE", 1000)])
    books.set_category(books.transactions()[0].id, books.account_by_code("4000").id)
    inc = reports.income_statement(books, "2026-01-01", "2026-01-31")
    assert inc.value("Total Net Sales") == 1000
    assert any(l.label == "Selling, General & Administrative" for l in inc.lines)
    bs = reports.balance_sheet(books, "2026-01-31")
    assert any(l.label == "LIABILITIES AND SHAREHOLDERS' EQUITY" for l in bs.lines)
    assert bs.value("Total Shareholders' Equity") == 1000
    books.set_labels({"revenue": ""})
    assert books.labels()["revenue"] == "Revenue"
    with pytest.raises(KeyError):
        books.set_labels({"bogus": "x"})
    assert coa.default_labels("Nonprofit")["equity"] == "Net Assets"


def test_chart_of_accounts_csv_roundtrip(books):
    rows = books.export_accounts_rows()
    assert rows[0] == Books.COA_COLUMNS
    other = Books.open(":memory:", template="minimal")
    added, updated = other.import_accounts_rows(rows)
    assert added == len(rows) - 1 - 5 and updated == 5
    assert {a.code for a in other.accounts()} == {a.code for a in books.accounts()}

    edits = [Books.COA_COLUMNS,
             ["7000", "Marketing", "Expense", "Operating expense", "", "", "yes"],
             ["7010", "Paid Social", "expense", "", "7000", "", ""],
             ["4000", "Product Sales", "income", "operating", "", "", "yes"]]
    assert books.import_accounts_rows(edits) == (2, 1)
    assert books.account_by_code("7010").parent_id == books.account_by_code("7000").id
    assert books.account_by_code("4000").name == "Product Sales"

    before = [(a.code, a.name) for a in books.accounts()]
    bad = [Books.COA_COLUMNS, ["7100", "Fine", "expense", "", "", "", ""],
           ["7200", "Broken", "expense", "", "9999", "", ""]]
    with pytest.raises(ValueError, match="parent"):
        books.import_accounts_rows(bad)
    assert [(a.code, a.name) for a in books.accounts()] == before  # all-or-nothing
    with pytest.raises(ValueError, match="group"):
        books.import_accounts_rows([Books.COA_COLUMNS, ["7300", "X", "expense", "current", "", "", ""]])


def test_copy_setup_copies_hierarchy_and_headings(books, tmp_path):
    rev = books.account_by_code("4000")
    books.add_account("4010", "Online", "income", parent_id=rev.id)
    books.set_labels({"revenue": "Turnover"})
    other = Books.open(":memory:", template="minimal")
    other.copy_setup_from(books)
    assert other.account_by_code("4010").parent_id == other.account_by_code("4000").id
    assert other.labels()["revenue"] == "Turnover"


def test_upgrade_old_company_file(tmp_path):
    """A company file from before sub-accounts/keys existed is upgraded on open."""
    path = tmp_path / "old.amdb"
    b = Books.open(path)
    b.import_transactions(b.account_by_code("1000").id, [tx("2026-01-03", "ZZZ", -700)])
    b.close()
    conn = sqlite3.connect(path)
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall():
        conn.execute(f"DROP TRIGGER {name}")      # an old file has no change history
    conn.executescript("""
        CREATE TABLE accounts_old AS SELECT id, code, name, type, subtype, is_bank, active FROM accounts;
        PRAGMA foreign_keys=OFF; DROP TABLE accounts; ALTER TABLE accounts_old RENAME TO accounts;""")
    conn.close()
    b = Books.open(path)
    assert b.role(coa.UNCATEGORIZED_EXPENSE).code == "6999"
    assert b.account_by_code("6250").key == "6250"
    assert all(reports.trial_balance(b, "2026-12-31").checks.values())
