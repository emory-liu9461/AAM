from datetime import date

from am_accounting import personal_accounts as pacc
from am_accounting import personal_commentary as pc
from am_accounting import personal_finance as pfi
from am_accounting import portfolio as pfm


def _budget(pf, year, amounts):
    lines = pfi.budget_lines(pf, year)
    for ln in lines:
        ln.budget = amounts.get(ln.category, 0)
    pfi.save_budget_lines(pf, lines, year)


def test_this_month_and_ahead(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    today = date(2026, 5, 20)
    amounts = {"Salary / employment": 8_000_00, "Groceries": 800_00, "Dining out": 400_00, "Utilities": 250_00}
    _budget(pf, 2026, amounts)
    pfi.set_actual(pf, 2026, "Salary / employment", 5, 6_000_00)
    pfi.set_actual(pf, 2026, "Groceries", 5, 1_100_00)
    pfi.set_actual(pf, 2026, "Dining out", 5, 50_00)
    pfi.set_actual(pf, 2025, "Home insurance", 7, 2_400_00)          # last July: a big one
    a = pacc.load(pf)
    a.banks.append(pacc.BankAccount("Chequing", balance=10_000_00, id=a.new_id()))
    pacc.save(pf, a)
    c = pc.commentary(pf, today)
    assert c.now[0].startswith("Groceries is high this month") and "$300.00 over" in c.now[0]
    assert c.now[1].startswith("Income hasn't reached budget") and "$2,000.00 short" in c.now[1]
    assert c.now[2].startswith("Dining out is low this month")
    assert c.ahead[0].startswith("Cash looks fine for the next three months") and "August" in c.ahead[0]
    assert "Home insurance" in c.ahead[1] and "July" in c.ahead[1]
    assert c.ahead[2] == "Budgeted income covers expenses in each of the next three months."
    # spending more than you earn: cash runs short, income doesn't cover
    _budget(pf, 2026, {"Salary / employment": 1_000_00, "Housing (mortgage / rent)": 6_000_00})
    c = pc.commentary(pf, today)
    assert c.ahead[0].startswith("Cash could run short by August 2026: about $5,000.00 below zero") and "short on the budget" in c.ahead[2]
    assert "This month" in pc.html(c)
    pf.close()


def test_no_budget_yet(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    c = pc.commentary(pf, date(2026, 5, 20))
    assert c.now[0].startswith("Set a monthly budget")
    pf.close()
