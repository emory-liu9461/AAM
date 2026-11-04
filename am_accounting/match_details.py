"""What an e-Transfer history record or a cheque / draft scan says about a bank transaction (for display)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .books import Books
from .money import fmt_money


@dataclass
class MatchDetail:
    kind: str                 # "e-Transfer" | "Cheque" | "Draft"
    headline: str             # "e-Transfer from Priya Shah" / "Cheque #126 to Jane Smith"
    short: str                # one line for lists: "↔ from Priya Shah · “rent”"
    facts: list[tuple[str, str]] = field(default_factory=list)   # (label, value)
    file: Path | None = None  # the cheque / draft scan, or the history file if still around


def for_transaction(books: Books, tx_id: int) -> MatchDetail | None:
    """The e-Transfer record or cheque / draft matched to this transaction, if any."""
    if books._has_table("etransfers"):
        from . import etransfer as et

        rec = et.record_for_transaction(books, tx_id)
        if rec:
            who = {"sent": "to", "received": "from"}.get(rec.direction, "with")
            name = rec.name or "(name not shown)"
            facts = [("Sent to" if rec.direction == "sent" else "From" if rec.direction == "received" else "With",
                      name), ("Amount", fmt_money(rec.amount_cents))]
            if rec.date:
                facts.append(("Date", rec.date.replace("????-", "")))
            if rec.message:
                facts.append(("Message", rec.message))
            if rec.reference:
                facts.append(("Reference", rec.reference))
            if rec.source:
                facts.append(("Read from", rec.source))
            short = f"↔ {who} {name}" + (f" · “{rec.message}”" if rec.message else "")
            return MatchDetail("e-Transfer", f"e-Transfer {who} {name}", short, facts)
    if books._has_table("cheques"):
        from . import cheques as cq

        root = cq.folder(books)
        row = books.conn.execute("SELECT * FROM cheques WHERE transaction_id=?", (tx_id,)).fetchone()
        if row and root is not None:
            info = cq._info(row)
            noun = "Cheque" if info.kind == "cheque" else "Draft"
            facts = [("Pay to", info.payee or "(not read)")]
            if info.number:
                facts.insert(0, (f"{noun} #", info.number))
            if info.amount_cents:
                facts.append(("Amount", fmt_money(info.amount_cents)))
            if info.date:
                facts.append(("Dated", info.date))
            if info.memo:
                facts.append(("Memo", info.memo))
            path = root / row["rel_path"]
            facts.append(("File", path.name))
            short = f"✎ {noun}" + (f" #{info.number}" if info.number else "") + \
                    (f" to {info.payee}" if info.payee else "") + (f" · “{info.memo}”" if info.memo else "")
            return MatchDetail(noun, info.label, short, facts, path)
    return None
