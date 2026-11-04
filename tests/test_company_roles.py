from am_accounting import company_roles
from am_accounting.books import Books, CompanyProfile
from am_accounting.companies import CompanyRegistry


def test_roles_add_their_accounts(tmp_path):
    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    hold = reg.create(CompanyProfile("Morgan Holdings Inc", role="holding"))
    mgr = reg.create(CompanyProfile("Northstar Capital", role="asset_manager"))
    op = reg.create(CompanyProfile("Northwind OpCo"))
    b = Books.open(hold, create=False)
    names = {a.name for a in b.accounts()}
    assert {"Investments in Subsidiaries", "Due from Related Parties", "Due to Related Parties", "Dividend Income",
            "Management Fees from Subsidiaries"} <= names
    due = next(a for a in b.accounts() if a.name == "Due from Related Parties")
    assert b.account(due.parent_id).name == "Accounts Receivable"
    assert b.profile().role == "holding" and company_roles.role_of(b) == "holding"
    assert company_roles.apply(b, "holding") == []  # nothing added twice
    b.close()
    m = Books.open(mgr, create=False)
    assert {"Management Fee Income", "Performance Fees / Carried Interest", "Fees Receivable",
            "Investments in Managed Funds"} <= {a.name for a in m.accounts()}
    m.close()
    assert company_roles.read_role(op) == "operating" and company_roles.read_role(hold) == "holding"
    # changing an operating company into a holding company later adds the accounts
    o = Books.open(op, create=False)
    assert "Dividend Income" in company_roles.apply(o, "holding")
    o.close()
