"""Statement importers. ``load_statement`` picks the right one by file type."""

from __future__ import annotations

from pathlib import Path

from ..books import ParsedTransaction
from . import csv_importer, ofx_importer, pdf_importer

SUPPORTED = "Bank statements (*.csv *.txt *.tsv *.xlsx *.ofx *.qfx *.qbo *.pdf);;All files (*)"


def load_statement(path: str | Path, *, dayfirst: bool = False) -> tuple[list[ParsedTransaction], list[str]]:
    """Read a statement file and return ``(transactions, warnings)``."""
    path = Path(path)
    data = path.read_bytes()
    return parse_statement_bytes(data, path.name, dayfirst=dayfirst)


def parse_statement_bytes(data: bytes, filename: str, *, dayfirst: bool = False
                          ) -> tuple[list[ParsedTransaction], list[str]]:
    ext = Path(filename).suffix.lower()
    head = data[:2048].upper()
    if ext == ".pdf" or data[:5] == b"%PDF-":
        txs, warnings = pdf_importer.parse_pdf(data, dayfirst=dayfirst)
    elif ext in (".ofx", ".qfx", ".qbo") or b"<OFX>" in head or b"OFXHEADER" in head:
        txs, warnings = ofx_importer.parse_ofx(data)
    elif ext in (".xlsx", ".xlsm"):
        txs, warnings = csv_importer.parse_rows(csv_importer.read_xlsx_rows(data), dayfirst=dayfirst)
    else:
        txs, warnings = csv_importer.parse_rows(csv_importer.read_rows(data), dayfirst=dayfirst)
    txs.sort(key=lambda t: t.date)
    return txs, warnings


__all__ = ["load_statement", "parse_statement_bytes", "SUPPORTED"]
