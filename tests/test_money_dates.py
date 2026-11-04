from datetime import date

import pytest

from am_accounting.dates import parse_date
from am_accounting.money import fmt_money, parse_money


@pytest.mark.parametrize("text,cents", [
    ("1,234.56", 123456), ("$1,234.56", 123456), ("(12.00)", -1200), ("12.00-", -1200),
    ("-12", -1200), ("12.00 CR", 1200), ("12.00 DR", -1200), ("+5.5", 550), ("", None), ("abc", None),
    (" -$3.10 ", -310), (".99", 99),
])
def test_parse_money(text, cents):
    assert parse_money(text) == cents


def test_fmt_money():
    assert fmt_money(123456) == "$1,234.56"
    assert fmt_money(-500) == "($5.00)"


@pytest.mark.parametrize("text,expected", [
    ("01/02/2026", date(2026, 1, 2)), ("2026-01-02", date(2026, 1, 2)), ("1/2/26", date(2026, 1, 2)),
    ("Jan 2, 2026", date(2026, 1, 2)), ("02 Jan 2026", date(2026, 1, 2)), ("20260102", date(2026, 1, 2)),
    ("01/02/2026 13:45:00", date(2026, 1, 2)), ("Total", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


def test_parse_date_dayfirst():
    assert parse_date("01/02/2026", dayfirst=True) == date(2026, 2, 1)
