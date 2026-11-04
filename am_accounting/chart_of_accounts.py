"""Chart of accounts: types, groups, templates and statement headings.

Every account has a **type** (asset, liability, equity, income, expense) and a
**group** (subtype) that decides where it appears on the financial statements
and how it's treated on the cash-flow statement. Accounts can also have a
**parent** to form sub-accounts, which roll up into the parent on reports.

All accounts, numbers, names, groups and statement headings can be changed by
the user. A handful of accounts have a *role* the app relies on (for example
"Uncategorized Expense"); those are found by a permanent internal key, not by
their number or name, so they can be renamed and renumbered freely too.

Template entry: (key, code, name, type, subtype, is_bank)
"""

from __future__ import annotations

ACCOUNT_TYPES = ("asset", "liability", "equity", "income", "expense")

TYPE_LABELS = {"asset": "Asset", "liability": "Liability", "equity": "Equity", "income": "Income",
               "expense": "Expense"}

# Groups per type, with the label shown in the app. Order = order on statements.
SUBTYPES: dict[str, dict[str, str]] = {
    "asset": {"bank": "Bank / cash", "current": "Current asset", "fixed_asset": "Fixed asset",
              "other_asset": "Other long-term asset"},
    "liability": {"current": "Current liability", "long_term": "Long-term liability"},
    "equity": {"contribution": "Capital / contributions / stock", "draw": "Draws / distributions / dividends",
               "retained": "Retained earnings adjustment", "other": "Other equity",
               "opening": "Opening balance"},
    "income": {"operating": "Operating revenue", "other": "Other income (non-operating)"},
    "expense": {"cogs": "Cost of goods sold", "operating": "Operating expense",
                "other": "Other expense (non-operating)"},
}


def subtype_label(type_: str, subtype: str) -> str:
    return SUBTYPES.get(type_, {}).get(subtype, subtype.replace("_", " ").title() or "—")


# Permanent keys for accounts the app relies on.
TRANSFER_CLEARING = "1090"
OPENING_BALANCE_EQUITY = "3900"
UNCATEGORIZED_INCOME = "4999"
UNCATEGORIZED_EXPENSE = "6999"
ACCOUNTS_RECEIVABLE = "1200"  # used for invoices (recreated if deleted, but not locked)
ACCOUNTS_PAYABLE = "2000"     # used for bills
ROLE_KEYS = {
    TRANSFER_CLEARING: "Transfers between your own accounts",
    OPENING_BALANCE_EQUITY: "Offset for opening balances",
    UNCATEGORIZED_INCOME: "Deposits not yet categorized",
    UNCATEGORIZED_EXPENSE: "Payments not yet categorized",
}

_REQUIRED = [
    ("1000", "1000", "Business Checking", "asset", "bank", True),
    (TRANSFER_CLEARING, "1090", "Transfers Between Accounts", "asset", "current", False),
    (OPENING_BALANCE_EQUITY, "3900", "Opening Balance Equity", "equity", "opening", False),
    (UNCATEGORIZED_INCOME, "4999", "Uncategorized Income", "income", "other", False),
    (UNCATEGORIZED_EXPENSE, "6999", "Uncategorized Expense", "expense", "other", False),
]

_GENERAL = _REQUIRED + [
    ("1010", "1010", "Business Savings", "asset", "bank", True),
    ("1200", "1200", "Accounts Receivable", "asset", "current", False),
    ("1300", "1300", "Prepaid Expenses", "asset", "current", False),
    ("1500", "1500", "Equipment", "asset", "fixed_asset", False),
    ("1590", "1590", "Accumulated Depreciation", "asset", "fixed_asset", False),
    ("2000", "2000", "Accounts Payable", "liability", "current", False),
    ("2100", "2100", "Credit Card Payable", "liability", "current", False),
    ("2200", "2200", "Sales Tax Payable", "liability", "current", False),
    ("2300", "2300", "Payroll Liabilities", "liability", "current", False),
    ("2500", "2500", "Loans Payable", "liability", "long_term", False),
    ("3000", "3000", "Owner Contributions", "equity", "contribution", False),
    ("3100", "3100", "Owner Draws", "equity", "draw", False),
    ("4000", "4000", "Sales Revenue", "income", "operating", False),
    ("4100", "4100", "Service Revenue", "income", "operating", False),
    ("4500", "4500", "Refunds & Returns", "income", "operating", False),
    ("4800", "4800", "Interest Income", "income", "other", False),
    ("4900", "4900", "Other Income", "income", "other", False),
    ("5000", "5000", "Cost of Goods Sold", "expense", "cogs", False),
    ("6000", "6000", "Advertising & Marketing", "expense", "operating", False),
    ("6050", "6050", "Bank & Merchant Fees", "expense", "operating", False),
    ("6100", "6100", "Contract Labor", "expense", "operating", False),
    ("6150", "6150", "Insurance", "expense", "operating", False),
    ("6250", "6250", "Meals", "expense", "operating", False),
    ("6300", "6300", "Office Supplies", "expense", "operating", False),
    ("6350", "6350", "Professional Fees", "expense", "operating", False),
    ("6400", "6400", "Rent & Lease", "expense", "operating", False),
    ("6450", "6450", "Repairs & Maintenance", "expense", "operating", False),
    ("6500", "6500", "Software & Subscriptions", "expense", "operating", False),
    ("6550", "6550", "Taxes & Licenses", "expense", "operating", False),
    ("6600", "6600", "Telephone & Internet", "expense", "operating", False),
    ("6650", "6650", "Travel", "expense", "operating", False),
    ("6700", "6700", "Utilities", "expense", "operating", False),
    ("6750", "6750", "Payroll - Wages", "expense", "operating", False),
    ("6760", "6760", "Payroll Taxes", "expense", "operating", False),
    ("6800", "6800", "Vehicle & Fuel", "expense", "operating", False),
    ("6850", "6850", "Postage & Shipping", "expense", "operating", False),
    ("6900", "6900", "Interest Expense", "expense", "other", False),
]


def _replace(base: list, replacements: dict[str, tuple], extra: list = ()) -> list:
    out = [replacements.get(e[0], e) for e in base if replacements.get(e[0], e) is not None]
    return out + list(extra)


_CORPORATION = _replace(_GENERAL, {
    "3000": ("3000", "3000", "Common Stock", "equity", "contribution", False),
    "3100": ("3100", "3200", "Dividends Paid", "equity", "draw", False),
}, [
    ("apic", "3100", "Additional Paid-in Capital", "equity", "contribution", False),
    ("treasury", "3300", "Treasury Stock", "equity", "other", False),
    ("income_tax", "6950", "Income Tax Expense", "expense", "other", False),
    ("income_tax_payable", "2400", "Income Taxes Payable", "liability", "current", False),
])

_LLC = _replace(_GENERAL, {
    "3000": ("3000", "3000", "Member Contributions", "equity", "contribution", False),
    "3100": ("3100", "3100", "Member Distributions", "equity", "draw", False),
})

_RETAIL = _GENERAL + [
    ("inventory", "1250", "Inventory", "asset", "current", False),
    ("freight_in", "5100", "Freight & Shipping In", "expense", "cogs", False),
    ("merchant_fees", "5200", "Payment Processing Fees", "expense", "cogs", False),
    ("platform_fees", "6020", "Marketplace & Platform Fees", "expense", "operating", False),
]

_MINIMAL = _REQUIRED

TEMPLATES: dict[str, tuple[str, list]] = {
    "general": ("Standard small business", _GENERAL),
    "corporation": ("Corporation (shareholders' equity: common stock, APIC, dividends)", _CORPORATION),
    "llc": ("LLC (members' equity: contributions, distributions)", _LLC),
    "retail": ("Retail / e-commerce (inventory, cost of goods sold detail)", _RETAIL),
    "minimal": ("Minimal — just the essentials; build your own", _MINIMAL),
}

# Kept for backwards compatibility: (code, name, type, subtype, is_bank)
DEFAULT_ACCOUNTS = [(c, n, t, s, b) for _k, c, n, t, s, b in _GENERAL]


def template_for_entity(entity_type: str) -> str:
    e = (entity_type or "").lower()
    if "corporation" in e:
        return "corporation"
    if "llc" in e:
        return "llc"
    return "general"


# ---------------------------------------------------------------------------
# Statement headings (customizable per company)
# ---------------------------------------------------------------------------

LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "cogs": "Cost of Goods Sold",
    "gross_profit": "Gross Profit",
    "operating_expenses": "Operating Expenses",
    "operating_income": "Operating Income",
    "other_income": "Other Income",
    "other_expenses": "Other Expenses",
    "net_income": "Net Income",
    "assets": "Assets",
    "current_assets": "Current Assets",
    "fixed_assets": "Fixed Assets",
    "other_assets": "Other Assets",
    "liabilities": "Liabilities",
    "current_liabilities": "Current Liabilities",
    "long_term_liabilities": "Long-Term Liabilities",
    "equity": "Equity",
    "retained_earnings": "Retained Earnings",
    "operating_activities": "Operating Activities",
    "investing_activities": "Investing Activities",
    "financing_activities": "Financing Activities",
}

LABEL_GROUPS = [
    ("Income statement", ["revenue", "cogs", "gross_profit", "operating_expenses", "operating_income",
                          "other_income", "other_expenses", "net_income"]),
    ("Balance sheet", ["assets", "current_assets", "fixed_assets", "other_assets", "liabilities",
                       "current_liabilities", "long_term_liabilities", "equity", "retained_earnings"]),
    ("Cash flow statement", ["operating_activities", "investing_activities", "financing_activities"]),
]


def default_labels(entity_type: str = "") -> dict[str, str]:
    """Statement headings, with the equity wording that fits the type of business."""
    labels = dict(LABELS)
    e = (entity_type or "").lower()
    if "corporation" in e:
        labels["equity"] = "Shareholders' Equity"
    elif "llc" in e:
        labels["equity"] = "Members' Equity"
    elif "partnership" in e:
        labels["equity"] = "Partners' Capital"
    elif "sole" in e or "personal" in e:
        labels["equity"] = "Owner's Equity"
    elif "nonprofit" in e:
        labels.update(equity="Net Assets", net_income="Change in Net Assets", revenue="Revenue & Support",
                      retained_earnings="Net Assets, Beginning of Year")
    return labels
