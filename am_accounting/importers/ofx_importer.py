"""OFX / QFX importer (the "Quicken / QuickBooks download" most banks offer)."""

from __future__ import annotations

import re
from datetime import datetime

from ..books import ParsedTransaction
from ..money import parse_money

_TXN_RE = re.compile(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|(?=</BANKTRANLIST>))", re.S | re.I)


def _field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>([^<\r\n]*)", block, re.I)
    return m.group(1).strip() if m else ""


def parse_ofx(data: bytes | str) -> tuple[list[ParsedTransaction], list[str]]:
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    out: list[ParsedTransaction] = []
    warnings: list[str] = []
    for block in _TXN_RE.findall(text):
        raw_date = _field(block, "DTPOSTED")[:8]
        try:
            d = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            warnings.append(f"Skipped transaction with bad date {raw_date!r}")
            continue
        amt = parse_money(_field(block, "TRNAMT"))
        if amt is None:
            warnings.append(f"Skipped transaction with bad amount on {d}")
            continue
        name = _field(block, "NAME")
        memo = _field(block, "MEMO")
        desc = name or memo or _field(block, "TRNTYPE") or "(no description)"
        if name and memo and memo.lower() not in name.lower():
            desc = f"{name} {memo}" if len(name) < 20 else name
        check = _field(block, "CHECKNUM")
        if check and "check" not in desc.lower():
            desc = f"CHECK {check} {desc}".strip()
        out.append(ParsedTransaction(d, desc, amt, memo if memo != desc else ""))
    if not out and "<OFX>" not in text.upper():
        raise ValueError("This does not look like an OFX/QFX file.")
    return out, warnings
