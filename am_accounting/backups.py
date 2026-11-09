"""Automatic backups and restoring one.

* **Every day the app is used**, the first time a company file (or the portfolio file) is opened it's copied into
  ``Backups/Daily/`` — in the company folder when one is set up (File → Company Folder & Auto-Save), otherwise next to
  the file. The last 30 daily copies are kept. (The company folder's scheduled backups, in ``Backups/``, carry on as
  before.)
* **Restoring** replaces the file with a backup; the file as it was is first saved in ``Backups/Before Restore/``,
  so a restore can itself be undone.

Copies are made with SQLite's backup, so they're consistent even while the file is open.
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

DAILY_DIR = "Daily"
BEFORE_DIR = "Before Restore"
KEEP_DAILY = 30


def backup_root(file_path: str | Path, books=None) -> Path:
    """Backups/ in the company folder when there is one, else next to the file."""
    if books is not None:
        try:
            from . import workspace as ws

            cfg = ws.get_config(books)
            if cfg.enabled:
                return cfg.dir("backups")
        except Exception:  # noqa: BLE001
            pass
    return Path(file_path).parent / "Backups"


def _copy(conn: sqlite3.Connection | None, src: Path, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n, base = 2, dest
    while dest.exists():              # never overwrite a copy made the same minute
        dest = base.with_name(f"{base.stem} ({n}){base.suffix}")
        n += 1
    target = sqlite3.connect(dest)
    try:
        if conn is not None:
            conn.backup(target)
        else:
            s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            try:
                s.backup(target)
            finally:
                s.close()
    finally:
        target.close()
    return dest


def _stem(name: str) -> str:
    from .companies import safe_filename

    return safe_filename(name) or "Company"


def daily(conn: sqlite3.Connection, file_path: str | Path, name: str, root: Path | None = None, *,
          today: date | None = None, keep: int = KEEP_DAILY) -> Path | None:
    """Today's daily copy, if there isn't one yet. Returns the copy made (None when already done today)."""
    if str(file_path) == ":memory:" or not Path(file_path).exists():
        return None
    today = today or date.today()
    folder = (root or backup_root(file_path)) / DAILY_DIR
    ext = Path(file_path).suffix or ".amdb"
    stem = _stem(name)
    if any(folder.glob(f"{glob_escape(stem)} {today.isoformat()}*{ext}")):
        return None
    dest = _copy(conn, Path(file_path), folder / f"{stem} {today.isoformat()} {datetime.now():%H%M}{ext}")
    old = sorted(folder.glob(f"{glob_escape(stem)} ????-??-??*{ext}"))
    for p in old[:-keep] if keep > 0 else []:
        p.unlink(missing_ok=True)
    return dest


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)


@dataclass
class Backup:
    path: Path
    when: datetime
    kind: str             # Daily | Scheduled | Before restore
    size: int


_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2})(?:[ _](\d{2})(\d{2}))?")


def _when(p: Path) -> datetime:
    m = _STAMP.search(p.stem)
    if m:
        try:
            d = date.fromisoformat(m.group(1))
            return datetime(d.year, d.month, d.day, int(m.group(2) or 0), int(m.group(3) or 0))
        except ValueError:
            pass
    return datetime.fromtimestamp(p.stat().st_mtime)


def backups(file_path: str | Path, name: str, root: Path | None = None) -> list[Backup]:
    """Every backup of this file (newest first): daily, the company folder's scheduled ones, before-restore copies."""
    root = root or backup_root(file_path)
    ext = Path(file_path).suffix or ".amdb"
    stem = glob_escape(_stem(name))
    out = []
    for sub, kind in ((DAILY_DIR, "Daily"), ("", "Scheduled"), (BEFORE_DIR, "Before restore")):
        folder = root / sub if sub else root
        for p in folder.glob(f"{stem} *{ext}"):
            out.append(Backup(p, _when(p), kind, p.stat().st_size))
    return sorted(out, key=lambda b: b.when, reverse=True)


def check(path: Path) -> str:
    """'' when the backup opens and passes SQLite's check; otherwise what's wrong."""
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            r = c.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            c.close()
        return "" if r == "ok" else r
    except sqlite3.DatabaseError as e:
        return str(e)


def restore(file_path: str | Path, backup: str | Path, name: str, root: Path | None = None) -> Path:
    """Replace the file with the backup (the file must be closed). Returns the before-restore copy of the file."""
    file_path, backup = Path(file_path), Path(backup)
    problem = check(backup)
    if problem:
        raise ValueError(f"That backup can't be read ({problem})")
    root = root or backup_root(file_path)
    ext = file_path.suffix or ".amdb"
    before = _copy(None, file_path, root / BEFORE_DIR / f"{_stem(name)} {datetime.now():%Y-%m-%d %H%M%S}{ext}") \
        if file_path.exists() else None
    tmp = file_path.with_suffix(file_path.suffix + ".restoring")
    shutil.copy2(backup, tmp)
    for side in ("-wal", "-shm", "-journal"):
        Path(str(file_path) + side).unlink(missing_ok=True)
    tmp.replace(file_path)
    return before


def restore_into(conn: sqlite3.Connection, file_path: str | Path, backup: str | Path, name: str,
                 root: Path | None = None) -> Path:
    """Restore through the open connection (the file stays open; everything using it sees the backup's contents).
    The file as it was is saved in Before Restore/ first. Returns that copy."""
    file_path, backup = Path(file_path), Path(backup)
    problem = check(backup)
    if problem:
        raise ValueError(f"That backup can't be read ({problem})")
    root = root or backup_root(file_path)
    ext = file_path.suffix or ".amdb"
    before = _copy(conn, file_path, root / BEFORE_DIR / f"{_stem(name)} {datetime.now():%Y-%m-%d %H%M%S}{ext}")
    src = sqlite3.connect(f"file:{backup}?mode=ro", uri=True)
    try:
        conn.commit()
        src.backup(conn)
    finally:
        src.close()
    return before
