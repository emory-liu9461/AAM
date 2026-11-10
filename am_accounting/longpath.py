"""File operations that work past Windows' 260-character path limit.

Windows refuses ordinary paths longer than 260 characters (MAX_PATH) unless long paths are switched on for the whole
computer. Deal folders get deep (a OneDrive folder, then Deals / Existing Deals / <deal> / Asset Management /
Accounting / Invoices / …), so these helpers use Windows' extended-length form (``\\\\?\\C:\\…``) for each call that
touches the disk. Paths going in and coming out are ordinary paths; elsewhere these are plain pathlib / shutil calls.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
LIMIT = 260                 # what Explorer, Excel and most programs can open
_SAFE = 240                 # switch to the extended form a little before the limit


def lp(p: str | Path) -> str:
    """The path as the operating system call should get it (extended-length on Windows when long)."""
    s = os.path.abspath(str(p))
    if not WINDOWS or s.startswith("\\\\?\\") or len(s) < _SAFE:
        return s
    if s.startswith("\\\\"):                       # \\server\share\…
        return "\\\\?\\UNC\\" + s[2:]
    return "\\\\?\\" + s


def exists(p) -> bool:
    return os.path.exists(lp(p))


def is_dir(p) -> bool:
    return os.path.isdir(lp(p))


def is_file(p) -> bool:
    return os.path.isfile(lp(p))


def mkdir(p) -> None:
    os.makedirs(lp(p), exist_ok=True)


def listdir(p) -> list[Path]:
    """The entries of a folder (as ordinary paths); [] when it can't be read."""
    try:
        return [Path(p) / n for n in os.listdir(lp(p))]
    except OSError:
        return []


def walk_files(p) -> list[Path]:
    """Every file under a folder (as ordinary paths)."""
    base, out = Path(p), []
    root = lp(base)
    try:
        for dirpath, _dirs, files in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            for f in files:
                out.append(base / f if rel == "." else base / rel / f)
    except OSError:
        pass
    return out


def stat(p) -> os.stat_result:
    return os.stat(lp(p))


def move(src, dst) -> None:
    shutil.move(lp(src), lp(dst))


def copy2(src, dst) -> None:
    shutil.copy2(lp(src), lp(dst))


def too_long(p) -> bool:
    """Longer than most programs (Explorer, Excel) can open."""
    return len(os.path.abspath(str(p))) >= LIMIT
