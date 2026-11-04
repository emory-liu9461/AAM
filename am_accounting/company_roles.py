"""What kind of company a company file is: operating company, holding company or asset manager.

Chosen when a company is created (and changeable in Company Settings). Each role adds the accounts it
typically needs, and holding companies / asset managers connect to the Asset Management mode:

* **Operating company** — a business that sells goods or services (the standard chart of accounts).
* **Holding company** — owns other businesses: *Investments in Subsidiaries*, *Due from / Due to Related
  Parties*, *Dividend Income*, *Management Fees from Subsidiaries*. Its books are where the money going to and
  coming from each holding is recorded, so it becomes the portfolio's holding-company books.
* **Asset manager** — manages assets / funds for others: *Management Fee Income*, *Performance Fees / Carried
  Interest*, *Fees Receivable*, *Fund Expenses Recoverable*, *Investments in Managed Funds*.
"""

from __future__ import annotations

from .books import Books

ROLES = {
    "operating": ("Operating company", "A business that sells goods or services"),
    "holding": ("Holding company", "Owns other businesses (subsidiaries, real estate companies, investments)"),
    "asset_manager": ("Asset manager", "Manages assets or funds for clients / investors and earns fees"),
}
SHORT = {"operating": "OpCo", "holding": "HoldCo", "asset_manager": "Asset Mgr"}
GROUPS = [("operating", "Operating companies"), ("holding", "Holding companies"), ("asset_manager", "Asset managers")]

# (name, type, subtype, preferred codes, parent: None | "ar" | "ap")
ROLE_ACCOUNTS = {
    "holding": [
        ("Investments in Subsidiaries", "asset", "other_asset", range(1700, 1800), None),
        ("Due from Related Parties", "asset", "current", None, "ar"),
        ("Due to Related Parties", "liability", "current", None, "ap"),
        ("Dividend Income", "income", "other", range(4810, 4900), None),
        ("Management Fees from Subsidiaries", "income", "operating", range(4200, 4300), None),
    ],
    "asset_manager": [
        ("Management Fee Income", "income", "operating", range(4200, 4300), None),
        ("Performance Fees / Carried Interest", "income", "operating", range(4300, 4400), None),
        ("Fees Receivable", "asset", "current", None, "ar"),
        ("Fund Expenses Recoverable", "asset", "current", range(1300, 1400), None),
        ("Investments in Managed Funds", "asset", "other_asset", range(1700, 1800), None),
    ],
}


def role_of(books: Books) -> str:
    r = books._meta("company_role") or "operating"
    return r if r in ROLES else "operating"


def label(role: str) -> str:
    return ROLES.get(role, ROLES["operating"])[0]


def apply(books: Books, role: str) -> list[str]:
    """Set the company's role and add the accounts it needs (skipping any it already has). Returns names added."""
    from . import receivables as rx

    if role not in ROLES:
        raise ValueError(f"Unknown company role {role!r}")
    with books.conn:
        books._set_meta("company_role", role)
    added = []
    have = {a.name.lower() for a in books.accounts()}
    for name, typ, sub, codes, parent in ROLE_ACCOUNTS.get(role, []):
        if name.lower() in have:
            continue
        if parent:
            rx.ensure_schema(books)
            rx.add_control_account(books, "invoice" if parent == "ar" else "bill", name)
        else:
            used = {a.code for a in books.accounts()}
            code = next((str(c) for c in codes if str(c) not in used), None)
            if code is None:
                continue
            books.add_account(code, name, typ, sub)
        added.append(name)
    return added


def read_role(path) -> str:
    """A company file's role without opening it for writing."""
    import sqlite3
    from pathlib import Path

    try:
        conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='company_role'").fetchone()
        finally:
            conn.close()
        return row[0] if row and row[0] in ROLES else "operating"
    except sqlite3.Error:
        return "operating"
