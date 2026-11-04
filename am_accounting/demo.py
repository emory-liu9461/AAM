"""Generate a realistic sample company for trying the app and for screenshots."""

from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

from .books import Books, CompanyProfile, ParsedTransaction
from .categorizer import auto_categorize

SAMPLE_NAME = "Sample Co (demo)"


def sample_transactions(end: date, days: int = 150, seed: int = 7) -> tuple[list, list]:
    """Return (checking, savings) parsed transactions covering ``days`` up to ``end``."""
    rng = random.Random(seed)
    start = end - timedelta(days=days - 1)
    chk: list[ParsedTransaction] = []
    sav: list[ParsedTransaction] = []
    d = start
    while d <= end:
        dow, dom = d.weekday(), d.day
        if dow == 0:
            chk.append(ParsedTransaction(d, f"STRIPE TRANSFER ST-{rng.randint(10**5, 10**6)}",
                                         rng.randint(180_000, 420_000)))
        if dow == 3 and rng.random() < 0.6:
            chk.append(ParsedTransaction(d, f"CLIENT PAYMENT INV {rng.randint(1000, 1999)}",
                                         rng.choice([250_000, 375_000, 520_000, 610_000])))
        if dow == 4 and (d - start).days // 7 % 2 == 0:
            chk.append(ParsedTransaction(d, "GUSTO PAYROLL", -rng.randint(540_000, 580_000)))
            chk.append(ParsedTransaction(d, "GUSTO TAX", -rng.randint(95_000, 110_000)))
        if dom == 1:
            chk.append(ParsedTransaction(d, "OAKWOOD PROPERTIES RENT", -320_000))
            chk.append(ParsedTransaction(d, "MONTHLY SERVICE FEE", -1_500))
        if dom == 3:
            chk.append(ParsedTransaction(d, "ADOBE *CREATIVE CLD", -5_999))
            chk.append(ParsedTransaction(d, "GOOGLE *WORKSPACE", -3_600))
            chk.append(ParsedTransaction(d, "GITHUB INC", -2_100))
        if dom == 8:
            chk.append(ParsedTransaction(d, "COMCAST BUSINESS", -18_999))
        if dom == 12:
            chk.append(ParsedTransaction(d, "NEXT INSURANCE PREMIUM", -24_500))
        if dom == 15:
            chk.append(ParsedTransaction(d, "PG&E ELECTRIC", -rng.randint(21_000, 34_000)))
            chk.append(ParsedTransaction(d, "SBA LOAN PAYMENT", -150_000))
        if dom == 20:
            chk.append(ParsedTransaction(d, "ONLINE TRANSFER TO SAV 4421", -250_000))
            sav.append(ParsedTransaction(d, "ONLINE TRANSFER FROM CHK 1180", 250_000))
        if dom == 25:
            chk.append(ParsedTransaction(d, "HARBOR SUPPLY CO", -rng.randint(150_000, 260_000)))
        if rng.random() < 0.35:
            chk.append(ParsedTransaction(d, rng.choice(["STARBUCKS STORE 0412", "CHIPOTLE 2231", "DOORDASH*LUNCH"]),
                                         -rng.randint(800, 6_500)))
        if rng.random() < 0.15:
            chk.append(ParsedTransaction(d, rng.choice(["SHELL OIL 5544", "CHEVRON 0093", "CITY PARKING"]),
                                         -rng.randint(1_500, 7_500)))
        if rng.random() < 0.08:
            chk.append(ParsedTransaction(d, rng.choice(["STAPLES 0101", "USPS PO 0571", "FEDEX 7781"]),
                                         -rng.randint(1_200, 18_000)))
        if rng.random() < 0.04:
            chk.append(ParsedTransaction(d, rng.choice(["HARBOR SUPPLY CO", "J MARTINEZ", "VENDOR 2291"]),
                                         -rng.randint(10_000, 90_000)))
        if dom == 28:
            sav.append(ParsedTransaction(d, "INTEREST PAID", rng.randint(900, 1_600)))
        # Vague bank descriptions that need a person to say what they were
        if rng.random() < 0.035:
            chk.append(ParsedTransaction(d, f"WIRE TRANSFER OUT REF {rng.randint(10**5, 10**6)} "
                                            f"{rng.choice(['NORTHSTAR FABRICATION', 'K LEE DESIGN'])}",
                                         -rng.choice([250_000, 480_000, 125_000])))
        if rng.random() < 0.03:
            chk.append(ParsedTransaction(d, f"INCOMING WIRE {rng.randint(10**5, 10**6)} ORIG BLUEBIRD DENTAL",
                                         rng.choice([320_000, 450_000])))
        if rng.random() < 0.04:
            chk.append(ParsedTransaction(d, f"ZELLE TO {rng.choice(['J MARTINEZ', 'SAM OKAFOR'])} "
                                            f"{rng.randint(1000, 9999)}", -rng.choice([35_000, 60_000, 85_000])))
        if rng.random() < 0.03:
            chk.append(ParsedTransaction(d, f"CHECK {rng.randint(1040, 1099)}", -rng.choice([42_500, 18_000, 95_000])))
        if rng.random() < 0.03:
            chk.append(ParsedTransaction(d, "MOBILE DEPOSIT", rng.choice([75_000, 120_000, 33_500])))
        d += timedelta(days=1)
    return chk, sav


def populate_sample(books: Books, end: date | None = None) -> None:
    end = end or date.today()
    chk, sav = sample_transactions(end)
    first = min(t.date for t in chk)
    checking = books.account_by_code("1000").id
    savings = books.account_by_code("1010").id
    opening = (first - timedelta(days=1)).isoformat()
    books.set_opening_balance(checking, opening, 3_450_000)
    books.set_opening_balance(savings, opening, 4_000_000)
    books.set_opening_balance(books.account_by_code("1500").id, opening, 1_800_000)
    books.set_opening_balance(books.account_by_code("2500").id, opening, 3_600_000)
    books.import_transactions(checking, chk, filename="sample-checking.csv")
    books.import_transactions(savings, sav, filename="sample-savings.csv")
    auto_categorize(books)
    cutoff = (end - timedelta(days=21)).isoformat()
    books.set_reviewed([t.id for t in books.transactions(end=cutoff) if t.category_id is not None])
    add_sample_invoices_and_bills(books, end, first)


CUSTOMERS = ["Bluebird Dental Group", "Cedar & Pine Architects", "Lakeside Clinic", "Orchard Street Cafe",
             "Summit Logistics", "Riverbend School District"]
VENDORS = ["Harbor Supply Co", "Keystone Printing", "Brightline Electric", "Atlas Office Furniture"]


def add_sample_invoices_and_bills(books: Books, end: date, first: date, seed: int = 11) -> None:
    """Invoices paid by the sample client deposits, bills paid by supplier payments, plus open ones."""
    from . import receivables as rx

    rng = random.Random(seed)
    service = books.account_by_code("4100").id
    cogs = books.account_by_code("5000").id
    printing = books.account_by_code("6000").id
    repairs = books.account_by_code("6450").id
    for tx in books.transactions():
        if tx.description.startswith("CLIENT PAYMENT INV"):
            issued = date.fromisoformat(tx.date) - timedelta(days=rng.randint(18, 40))
            if issued < first:
                continue
            number = "INV-" + tx.description.split()[-1]
            doc = rx.add_document(books, "invoice", rng.choice(CUSTOMERS), issued.isoformat(),
                                  (issued + timedelta(days=30)).isoformat(), tx.amount_cents, service, number=number)
            rx.record_payment(books, doc, tx.id)
        elif tx.description == "HARBOR SUPPLY CO" and abs(tx.amount_cents) > 100_000:
            issued = date.fromisoformat(tx.date) - timedelta(days=rng.randint(20, 28))
            if issued < first:
                continue
            doc = rx.add_document(books, "bill", "Harbor Supply Co", issued.isoformat(),
                                  (issued + timedelta(days=30)).isoformat(), -tx.amount_cents, cogs,
                                  number=f"HS-{rng.randint(40000, 49999)}")
            rx.record_payment(books, doc, tx.id)

    # Open invoices: some current, some overdue
    n = 1900
    for days_ago, amount in [(4, 480_000), (12, 265_000), (26, 610_000), (41, 375_000), (58, 198_000),
                             (77, 520_000), (128, 145_000)]:
        issued = end - timedelta(days=days_ago)
        n += 1
        rx.add_document(books, "invoice", CUSTOMERS[n % len(CUSTOMERS)], issued.isoformat(),
                        (issued + timedelta(days=30)).isoformat(), amount, service, number=f"INV-{n}")
    # A larger invoice coming due
    rx.add_document(books, "invoice", "Summit Logistics", (end - timedelta(days=20)).isoformat(),
                    (end + timedelta(days=10)).isoformat(), 900_000, service, number=f"INV-{n + 1}")

    # Open bills
    for days_ago, terms, vendor, amount, acct in [(3, 30, "Harbor Supply Co", 212_500, cogs),
                                                  (18, 15, "Keystone Printing", 68_900, printing),
                                                  (36, 30, "Brightline Electric", 145_000, repairs),
                                                  (9, 10, "Atlas Office Furniture", 289_900, repairs)]:
        issued = end - timedelta(days=days_ago)
        rx.add_document(books, "bill", vendor, issued.isoformat(), (issued + timedelta(days=terms)).isoformat(),
                        amount, acct, number=f"B-{rng.randint(1000, 9999)}")


def create_sample_company(registry, end: date | None = None) -> Path:
    existing = next((e for e in registry.companies() if e.name == SAMPLE_NAME and e.exists), None)
    if existing:
        registry.delete(existing.path)
    path = registry.create(CompanyProfile(SAMPLE_NAME, entity_type="Single-member LLC"))
    books = Books.open(path)
    try:
        populate_sample(books, end)
    finally:
        books.close()
    return path
