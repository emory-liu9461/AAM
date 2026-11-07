from datetime import date

import pytest

from am_accounting import portfolio as pfm


def _company(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("45 Elm St", "Real estate", acquired="2024-01-01")
    pf.save_holding(h)
    # bought for $4.0M with $2.8M debt; worth $5.0M today with the same debt → equity value $2.2M
    pf.add_valuation(h.id, pfm.Valuation("2026-06-30", 2_200_000_00, "Appraisal", company_value_cents=5_000_000_00,
                                         debt_cents=2_800_000_00))
    for name, cls, amt in (("Pat Lee", "Class A", 900_000_00), ("Sam Roe", "Class A", 200_000_00),
                           ("Sponsor Co", "Class B", 100_000_00)):
        pfm.save_shareholder(pf, h.id, pfm.Shareholder(name, amount_cents=amt, share_class=cls, funded_cents=amt,
                                                       funded_date="2024-01-01"))
    return pf, h


def test_no_gp_splits_the_equity_value_pro_rata(tmp_path):
    pf, h = _company(tmp_path)
    w, owners, value = pfm.cap_table_waterfall(pf, h, date(2026, 6, 30), pfm.Terms(100, 8, 0, 0, ""))
    assert value == 2_200_000_00 and w.distributable == 2_200_000_00 and w.capital == 1_200_000_00
    by = {x.name: x for x in owners}
    assert by["Pat Lee"].value == pytest.approx(2_200_000_00 * 0.75, abs=2) and by["Pat Lee"].role == "LP"
    assert sum(x.value for x in owners) == pytest.approx(2_200_000_00, abs=3) and w.gp_total == 0
    assert by["Sponsor Co"].multiple == pytest.approx(2_200_000 / 1_200_000, rel=1e-4)


def test_gp_class_gets_the_promote(tmp_path):
    pf, h = _company(tmp_path)
    pfm.set_terms(pf, pfm.company_terms_key(h.id), pfm.Terms(100, 8, 100, 20, "Class B"))
    w, owners, _v = pfm.cap_table_waterfall(pf, h, date(2026, 6, 30))
    by = {x.name: x for x in owners}
    assert by["Sponsor Co"].role == "GP" and by["Pat Lee"].role == "LP"
    assert w.terms.lp_pct == pytest.approx(1_100_000 / 1_200_000 * 100)
    assert w.gp_promote > 0 and by["Sponsor Co"].promote == w.gp_promote
    # the GP gets more than its pro-rata share; the LPs split theirs by what they paid in
    assert by["Sponsor Co"].value > 2_200_000_00 * 100_000 / 1_200_000
    assert by["Pat Lee"].value / by["Sam Roe"].value == pytest.approx(900 / 200, rel=1e-4)
    assert sum(x.value for x in owners) == pytest.approx(w.distributable, abs=3)
    carry = next(t for t in w.tiers if t.name == "Carried interest")
    assert carry.gp >= round(0.20 * carry.total)                    # 20% carry, plus its pro-rata share of the rest
    pf.close()


def test_without_owners(tmp_path):
    pf = pfm.Portfolio(tmp_path / "p.ampf")
    h = pfm.Holding("Empty")
    pf.save_holding(h)
    w, owners, _v = pfm.cap_table_waterfall(pf, h)
    assert w is None and owners == []
    pf.close()
