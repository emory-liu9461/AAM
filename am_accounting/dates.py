"""Lenient date parsing for bank exports."""

from __future__ import annotations

from datetime import date, datetime

_US_FORMATS = [
    "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y", "%m.%d.%Y",
]
_DAYFIRST_FORMATS = [
    "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d-%m-%y", "%d.%m.%Y",
]
_UNAMBIGUOUS = [
    "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S",
    "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y",
    "%d %b %Y", "%d %B %Y", "%d-%b-%Y", "%d-%b-%y", "%b. %d, %Y",
    "%b %d/%y", "%b %d/%Y", "%b%d/%y",
]


def parse_date(text: str, *, dayfirst: bool = False) -> date | None:
    """Parse a date string. Returns ``None`` if no format matches."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    # Drop a trailing time component like "01/02/2024 13:45:00".
    if " " in s and ":" in s.split(" ")[-1]:
        s = s.rsplit(" ", 1)[0]
    formats = (_DAYFIRST_FORMATS + _US_FORMATS) if dayfirst else (_US_FORMATS + _DAYFIRST_FORMATS)
    for fmt in _UNAMBIGUOUS + formats:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def looks_like_date(text: str) -> bool:
    return parse_date(text) is not None
