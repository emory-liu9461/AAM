import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import intercompany as ic
from am_accounting.books import Books, CompanyProfile
from am_accounting.companies import CompanyRegistry


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _co(reg, name, role="operating", legal=""):
    return str(reg.create(CompanyProfile(name, legal, "Corporation", role=role)))


def _entry(path, lines, on="2026-06-30"):
    b = Books.open(path, create=False)
    try:
        ids = {}
        for name, type_, sub, dr, cr in lines:
            a = next((a for a in b.accounts() if a.name == name), None)
            if a is None:
                code = str(1900 + len(b.accounts()))
                a = b.add_account(code, name, type_, sub)
            ids[name] = a.id
        b.add_journal_entry(on, "test", [(ids[n], dr, cr) for n, _t, _s, dr, cr in lines])
    finally:
        b.close()


def test_pairs_match_and_mismatch(tmp_path):
    reg = CompanyRegistry(tmp_path / "c.json", tmp_path / "Companies")
    hold = _co(reg, "Maple Holdings Inc.", "holding")
    elm = _co(reg, "Elm Street Apartments", legal="1001234567 Ontario Inc.")
    oak = _co(reg, "Oak Plaza Ltd.")
    # Maple lent Elm $50,000 (both sides agree; Elm's account uses Maple's name without "Inc.")
    _entry(hold, [("Due from 1001234567 Ontario Inc.", "asset", "current", 50_000_00, 0),
                  ("Cash on hand", "asset", "current", 0, 50_000_00)])
    _entry(elm, [("Cash on hand", "asset", "current", 50_000_00, 0),
                 ("Shareholder Loan – Maple Holdings", "liability", "long_term", 0, 50_000_00)])
    # Maple says Oak owes it $8,000; Oak recorded $7,500
    _entry(hold, [("Due from Oak Plaza", "asset", "current", 8_000_00, 0),
                  ("Cash on hand", "asset", "current", 0, 8_000_00)])
    _entry(oak, [("Cash on hand", "asset", "current", 7_500_00, 0),
                 ("Due to Maple Holdings Inc.", "liability", "current", 0, 7_500_00)])
    # an investment is ownership, not a balance owed
    _entry(hold, [("Investment in Oak Plaza", "asset", "other_asset", 1_000_00, 0),
                  ("Cash on hand", "asset", "current", 0, 1_000_00)])
    comps = [("Maple Holdings Inc.", hold, "holding"), ("Elm Street Apartments", elm, "operating"),
             ("Oak Plaza Ltd.", oak, "operating")]
    res = ic.check(comps)
    by = {(p.a, p.b): p for p in res.pairs}
    elm_p, oak_p = by[("Maple Holdings Inc.", "Elm Street Apartments")], by[("Maple Holdings Inc.", "Oak Plaza Ltd.")]
    assert (elm_p.a_says, elm_p.b_says, elm_p.ok, elm_p.status) == (50_000_00, 50_000_00, True, "Matches")
    assert (oak_p.a_says, oak_p.b_says, oak_p.difference) == (8_000_00, 7_500_00, 50_000)
    assert res.problems == [oak_p] and res.pairs[0] is oak_p                 # problems first
    assert not any("Investment" in a.account for a in res.accounts)
    el = ic.eliminations(res)
    from pathlib import Path
    assert el[str(Path(hold).resolve())].assets == 58_000_00
    e = el[str(Path(elm).resolve())]
    assert (e.liabilities, e.debt) == (50_000_00, 50_000_00)
    # a balance in one set of books only
    _entry(oak, [("Due from Elm Street Apartments", "asset", "current", 1_200_00, 0),
                 ("Cash on hand", "asset", "current", 0, 1_200_00)])
    res = ic.check(comps)
    one = next(p for p in res.pairs if {p.a, p.b} == {"Elm Street Apartments", "Oak Plaza Ltd."})
    assert one.status == "Only in Oak Plaza Ltd.'s books" and not one.ok


def test_intercompany_page(app, tmp_path):
    from datetime import date

    from am_accounting.gui.intercompany_tab import IntercompanyPage

    reg = CompanyRegistry(tmp_path / "c.json", tmp_path / "Companies")
    hold = _co(reg, "Maple Holdings Inc.", "holding")
    elm = _co(reg, "Elm Street Apartments")
    _entry(hold, [("Due from Elm Street Apartments", "asset", "current", 5_000_00, 0),
                  ("Cash on hand", "asset", "current", 0, 5_000_00)])
    opened = []
    page = IntercompanyPage(lambda: [("Maple Holdings Inc.", hold, "holding"), ("Elm Street Apartments", elm, "")],
                            lambda: date.today(), open_books=opened.append)
    page.fill()
    assert page.pairs.rowCount() == 1 and "Only in Maple Holdings Inc.'s books" in page.pairs.item(0, 5).text()
    assert "1 don't match" in page.head.text()
    assert page.accounts.rowCount() == 1 and "Elm Street Apartments owes" in page.accounts.item(0, 4).text()
    page._open(page.accounts.model().index(0, 0))
    assert opened == [hold]
