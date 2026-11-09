"""Documents attached to records: leases, invoices, appraisals, closing documents … kept as local files.

A file attached to a record is **copied** into the app's attachments folder (so it stays even if the original is
moved or deleted) and listed with the record:

* in a company file — bank transactions and journal entries: ``<company folder>/Attachments/`` when a company folder
  is set up, otherwise ``<company file name> Attachments/`` next to the file;
* in the portfolio file — deals, units, holdings: ``Portfolio Attachments/`` next to the portfolio file.

Files are sorted into a subfolder per kind of record and named after the original (with a number added when a name is
already taken). The list keeps the path relative to the attachments folder, so the whole folder can be moved with the
file.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS attachments (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL,          -- transaction | entry | deal | unit | holding
    ref      TEXT NOT NULL,          -- the record's id (a unit: "<holding id>:<unit>")
    name     TEXT NOT NULL,          -- shown name (the original file name)
    stored   TEXT NOT NULL,          -- path relative to the attachments folder
    added    TEXT NOT NULL,
    note     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_attachments ON attachments(kind, ref);
"""
FOLDERS = {"transaction": "Transactions", "entry": "Journal Entries", "deal": "Deals", "unit": "Units",
           "holding": "Holdings"}


@dataclass
class Attachment:
    id: int
    kind: str
    ref: str
    name: str
    stored: str
    added: str
    note: str
    base: Path

    @property
    def path(self) -> Path:
        return self.base / self.stored

    @property
    def exists(self) -> bool:
        return self.path.exists()


def books_folder(books) -> Path:
    try:
        from . import workspace as ws

        cfg = ws.get_config(books)
        if cfg.enabled:
            return cfg.dir("attachments")
    except Exception:  # noqa: BLE001
        pass
    p = Path(books.path)
    return p.parent / f"{p.stem} Attachments"


def portfolio_folder(pf) -> Path:
    return Path(pf.path).parent / "Portfolio Attachments"


def ready(conn) -> None:
    conn.executescript(SCHEMA)


def listed(conn, base: Path, kind: str, ref) -> list[Attachment]:
    ready(conn)
    return [Attachment(r[0], r[1], r[2], r[3], r[4], r[5], r[6], Path(base))
            for r in conn.execute("SELECT id, kind, ref, name, stored, added, note FROM attachments WHERE kind=? AND "
                                  "ref=? ORDER BY id", (kind, str(ref)))]


def counts(conn, kind: str) -> dict[str, int]:
    ready(conn)
    return {r[0]: r[1] for r in conn.execute("SELECT ref, COUNT(*) FROM attachments WHERE kind=? GROUP BY ref",
                                             (kind,))}


def _safe(name: str) -> str:
    from .companies import safe_filename

    stem, suffix = Path(name).stem, Path(name).suffix
    return (safe_filename(stem) or "file") + suffix.lower()


def add(conn, base: Path, kind: str, ref, source: str | Path, *, sub: str = "", note: str = "") -> Attachment:
    """Copy ``source`` into the attachments folder and list it with the record."""
    source = Path(source)
    if not source.is_file():
        raise ValueError(f"{source.name} isn't a file")
    ready(conn)
    folder_rel = Path(FOLDERS.get(kind, kind.title()))
    if sub:
        folder_rel = folder_rel / _safe(sub).rstrip(".")
    folder = Path(base) / folder_rel
    folder.mkdir(parents=True, exist_ok=True)
    name = _safe(source.name)
    dest, n = folder / name, 2
    while dest.exists():
        dest = folder / f"{Path(name).stem} ({n}){Path(name).suffix}"
        n += 1
    shutil.copy2(source, dest)
    stored = dest.relative_to(Path(base)).as_posix()
    added = datetime.now().isoformat(timespec="seconds")
    with conn:
        aid = conn.execute("INSERT INTO attachments(kind, ref, name, stored, added, note) VALUES (?,?,?,?,?,?)",
                           (kind, str(ref), source.name, stored, added, note)).lastrowid
    return Attachment(aid, kind, str(ref), source.name, stored, added, note, Path(base))


def remove(conn, a: Attachment, *, delete_file: bool = True) -> None:
    ready(conn)
    with conn:
        conn.execute("DELETE FROM attachments WHERE id=?", (a.id,))
    if delete_file:
        try:
            a.path.unlink(missing_ok=True)
        except OSError:
            pass


def move_ref(conn, kind: str, old, new) -> None:
    """A record's key changed (e.g. a unit renamed): its attachments follow."""
    ready(conn)
    with conn:
        conn.execute("UPDATE attachments SET ref=? WHERE kind=? AND ref=?", (str(new), kind, str(old)))
