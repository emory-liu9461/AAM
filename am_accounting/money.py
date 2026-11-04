"""Money helpers. All amounts are stored as integer cents to avoid float error."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_CLEAN_RE = re.compile(r"[\s$€£¥,]|USD|CAD|EUR|GBP", re.IGNORECASE)


def parse_money(text: str | float | int | None) -> int | None:
    """Parse a money string into signed cents.

    Handles ``$1,234.56``, ``(12.00)``, ``12.00-``, ``-12``, ``12.00 CR`` / ``DR``.
    Returns ``None`` when the text is not a number.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return int((Decimal(str(text)) * 100).quantize(Decimal("1")))
    s = str(text).strip()
    if not s:
        return None
    negative = False
    upper = s.upper()
    if upper.endswith("DR"):
        negative = True
        s = s[:-2]
    elif upper.endswith("CR"):
        s = s[:-2]
    s = _CLEAN_RE.sub("", s)
    if s.startswith("(") and s.endswith(")"):
        negative = not negative
        s = s[1:-1]
    if s.endswith("-"):
        negative = not negative
        s = s[:-1]
    if s.startswith("+"):
        s = s[1:]
    if s.startswith("-"):
        negative = not negative
        s = s[1:]
    if not s or not re.fullmatch(r"\d+(\.\d+)?|\.\d+", s):
        return None
    try:
        cents = int((Decimal(s) * 100).quantize(Decimal("1")))
    except InvalidOperation:
        return None
    return -cents if negative else cents


def fmt_money(cents: int | None, *, parens: bool = True, symbol: str = "$") -> str:
    """Format cents for display. Negatives shown in parentheses by default."""
    if cents is None:
        return ""
    neg = cents < 0
    value = f"{symbol}{abs(cents) / 100:,.2f}"
    if neg:
        return f"({value})" if parens else f"-{value}"
    return value
