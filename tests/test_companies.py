from datetime import date

import pytest

from am_accounting import reports
from am_accounting.books import Books, CompanyProfile, ParsedTransaction
from am_accounting.companies import CompanyRegistry, safe_filename


@pytest.fixture
def reg(tmp_path):
    return CompanyRegistry(tmp_path / "settings" / "companies.json", tmp_path / "Companies")


def test_create_and_list_companies(reg, tmp_path):
    a = reg.create(CompanyProfile("Alpha Bakery", entity_type="LLC", tax_id="12-3456789"))
    b = reg.create(CompanyProfile("Beta / Consulting: Inc?"))
    assert a.name == "Alpha Bakery.amdb" and a.parent == tmp_path / "Companies"
    assert b.name == "Beta Consulting Inc.amdb"
    assert {e.name for e in reg.companies()} == {"Alpha Bakery", "Beta / Consulting: Inc?"}

    # Same name again never overwrites an existing company
    a2 = reg.create(CompanyProfile("Alpha Bakery"))
    assert a2.name == "Alpha Bakery (2).amdb"

    books = Books.open(a, create=False)
    assert books.profile() == CompanyProfile("Alpha Bakery", "", "LLC", "12-3456789", 1)
    books.close()

    # Registry persists and remembers what was opened last
    reg.touch(b)
    reloaded = CompanyRegistry(reg.registry_file, reg.companies_dir)
    assert reloaded.most_recent().path == str(b)


def test_registry_discovers_files_and_handles_missing(reg, tmp_path):
    reg.companies_dir.mkdir(parents=True)
    Books.open(reg.companies_dir / "Dropped In.amdb").close()
    assert [e.name for e in reg.companies()] == ["My Company"]  # name read from the file

    elsewhere = tmp_path / "elsewhere" / "Remote.amdb"
    elsewhere.parent.mkdir()
    Books.open(elsewhere).close()
    reg.add(elsewhere)
    elsewhere.unlink()
    missing = reg.find(elsewhere)
    assert missing and not missing.exists
    reg.remove(elsewhere)
    assert reg.find(elsewhere) is None


def test_delete_company(reg):
    p = reg.create(CompanyProfile("Temp Co"))
    with pytest.raises(ValueError):
        reg.delete(p.with_suffix(".txt"))
    reg.delete(p)
    assert not p.exists() and reg.find(p) is None


def test_companies_are_isolated(reg):
    a = Books.open(reg.create(CompanyProfile("A")))
    b = Books.open(reg.create(CompanyProfile("B")))
    a.import_transactions(a.account_by_code("1000").id, [ParsedTransaction(date(2026, 1, 2), "SALE", 1000)])
    assert a.stats()["transactions"] == 1
    assert b.stats()["transactions"] == 0
    a.close()
    b.close()


def test_copy_setup_from_another_company(reg):
    src = Books.open(reg.create(CompanyProfile("Template")))
    src.add_account("6010", "Ingredients", "expense", "cogs")
    src.update_account(src.account_by_code("6250").id, code="6250", name="Client Meals", type_="expense",
                       subtype="operating")
    src.add_rule("gusto", src.account_by_code("6750").id, priority=5)
    src.add_rule("chase card", src.account_by_code("1000").id)  # points at a bank account: not copied
    src.import_transactions(src.account_by_code("1000").id, [ParsedTransaction(date(2026, 1, 2), "X", 100)])
    src.close()

    new = Books.open(reg.create(CompanyProfile("Client 2"), copy_setup_from=reg.find(
        reg.companies_dir / "Template.amdb").path))
    assert new.account_by_code("6010").name == "Ingredients"
    assert new.account_by_code("6250").name == "Client Meals"
    rules = new.rules()
    assert [(r.pattern, new.account(r.account_id).code, r.priority) for r in rules] == [("gusto", "6750", 5)]
    assert new.stats()["transactions"] == 0  # setup only, never data
    assert new.check_balanced()
    new.close()


def test_profile_validation(books):
    with pytest.raises(ValueError):
        books.set_profile(CompanyProfile(""))
    with pytest.raises(ValueError):
        books.set_profile(CompanyProfile("X", fiscal_year_start_month=13))


def test_fiscal_year_balance_sheet(books):
    """With a July fiscal year, income before July 1 is retained earnings, after is current net income."""
    books.set_profile(CompanyProfile("FY Co", fiscal_year_start_month=7))
    bank = books.account_by_code("1000").id
    books.import_transactions(bank, [ParsedTransaction(date(2026, 3, 1), "SALE", 10000),
                                     ParsedTransaction(date(2026, 8, 1), "SALE", 2500)])
    bs = reports.build(books, "Balance Sheet", "2026-07-01", "2026-09-30")
    assert bs.value("Retained Earnings") == 10000
    assert bs.value("Net Income") == 2500
    assert all(bs.checks.values())


def test_safe_filename():
    assert safe_filename('  A/B:C*?"<>|  ') == "ABC"
    assert safe_filename("...") == "Company"
