"""Multiple companies.

Each company is its own self-contained company file (``.amdb``), so books for
different businesses never mix and each one can be backed up or handed to an
accountant on its own. The registry is a small JSON list of the company files
the user works with, kept in the app's settings folder; new companies are
created in a ``Companies`` folder by default but can live anywhere.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .books import Books, CompanyProfile

EXTENSION = ".amdb"


@dataclass
class CompanyEntry:
    path: str
    name: str
    last_opened: str = ""

    @property
    def exists(self) -> bool:
        return Path(self.path).is_file()


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().strip(".")
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:80] or "Company"


def _same(a: str | Path, b: str | Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a) == str(b)


class CompanyRegistry:
    def __init__(self, registry_file: str | Path, companies_dir: str | Path):
        self.registry_file = Path(registry_file)
        self.companies_dir = Path(companies_dir)
        self._entries: list[CompanyEntry] = self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> list[CompanyEntry]:
        try:
            data = json.loads(self.registry_file.read_text(encoding="utf-8"))
            return [CompanyEntry(**{k: e.get(k, "") for k in ("path", "name", "last_opened")})
                    for e in data.get("companies", []) if e.get("path")]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self) -> None:
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.registry_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"companies": [asdict(e) for e in self._entries]}, indent=2), encoding="utf-8")
        tmp.replace(self.registry_file)

    # -- queries ------------------------------------------------------------

    def companies(self) -> list[CompanyEntry]:
        """Known companies, most recently opened first. Also picks up files in the companies folder."""
        self._discover()
        return sorted(self._entries, key=lambda e: (e.last_opened or "", e.name.lower()), reverse=True)

    def find(self, path: str | Path) -> CompanyEntry | None:
        return next((e for e in self._entries if _same(e.path, path)), None)

    def most_recent(self) -> CompanyEntry | None:
        return next((e for e in self.companies() if e.exists), None)

    def _discover(self) -> None:
        if not self.companies_dir.is_dir():
            return
        changed = False
        for f in self.companies_dir.glob(f"*{EXTENSION}"):
            if self.find(f) is None:
                self._entries.append(CompanyEntry(str(f), _read_name(f) or f.stem))
                changed = True
        if changed:
            self._save()

    # -- changes ------------------------------------------------------------

    def add(self, path: str | Path, name: str | None = None) -> CompanyEntry:
        entry = self.find(path)
        if entry is None:
            entry = CompanyEntry(str(Path(path)), name or _read_name(path) or Path(path).stem)
            self._entries.append(entry)
        elif name:
            entry.name = name
        self._save()
        return entry

    def touch(self, path: str | Path, name: str | None = None) -> None:
        entry = self.add(path, name)
        entry.last_opened = datetime.now().isoformat(timespec="seconds")
        self._save()

    def rename(self, path: str | Path, name: str) -> None:
        entry = self.find(path)
        if entry:
            entry.name = name
            self._save()

    def remove(self, path: str | Path) -> None:
        """Forget a company (its file is left untouched)."""
        self._entries = [e for e in self._entries if not _same(e.path, path)]
        self._save()

    def delete(self, path: str | Path) -> None:
        """Permanently delete a company file and forget it."""
        p = Path(path)
        if p.suffix != EXTENSION:
            raise ValueError("Refusing to delete a file that isn't a company file")
        for f in (p, p.with_name(p.name + "-journal"), p.with_name(p.name + "-wal"), p.with_name(p.name + "-shm")):
            if f.exists():
                f.unlink()
        self.remove(p)

    def new_company_path(self, name: str, folder: str | Path | None = None) -> Path:
        folder = Path(folder) if folder else self.companies_dir
        base = folder / f"{safe_filename(name)}{EXTENSION}"
        path, n = base, 2
        while path.exists() or self.find(path):
            path = base.with_name(f"{base.stem} ({n}){EXTENSION}")
            n += 1
        return path

    def create(self, profile: CompanyProfile, *, folder: str | Path | None = None,
               copy_setup_from: str | Path | None = None, template: str | None = None) -> Path:
        """Create a new company file and register it. Returns its path."""
        if not profile.name.strip():
            raise ValueError("Company name is required")
        path = self.new_company_path(profile.name, folder)
        path.parent.mkdir(parents=True, exist_ok=True)
        from . import chart_of_accounts as coa

        books = Books.open(path, template=template or coa.template_for_entity(profile.entity_type))
        try:
            books.set_profile(profile)
            if copy_setup_from:
                source = Books.open(copy_setup_from, create=False)
                try:
                    books.copy_setup_from(source)
                finally:
                    source.close()
            from . import company_roles

            company_roles.apply(books, profile.role or "operating")  # role and the accounts it needs
        except Exception:
            books.close()
            path.unlink(missing_ok=True)
            raise
        books.close()
        self.add(path, profile.name.strip())
        return path


def _read_name(path: str | Path) -> str:
    """Company name from a file, without creating or modifying anything."""
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key='company_name'").fetchone()
        finally:
            conn.close()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""
