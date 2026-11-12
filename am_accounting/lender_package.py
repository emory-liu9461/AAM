"""A lender submission package: the documents a lender usually asks for when financing (or refinancing) a building,
copied out of the deal folder into one folder you can send.

The package goes in **Underwriting → Financing → Lender Submission Package**, a folder per submission (e.g.
"2026-11-11 Lender Submission"), with a numbered folder per document ("05 Property Tax Bill") and a checklist
(``00 Document Checklist.xlsx``) of what's in it and what's still missing. Your own documents (net worth
statement, T1s and notices of assessment …) come from your personal folder (see personal.py).

For each document on the checklist, :func:`candidates` finds the files that could go in it: the files in the deal
folder's own place for it (e.g. Consultant Reports → Appraisal for the appraisal) — picked to start with — and files
elsewhere in the deal folder whose names look like it (e.g. "Rent Roll Oct 2026.xlsx") — picked only when the
document's own folders have nothing. Archived versions and the earlier packages are left out. You choose the files;
:func:`generate` copies them (the originals stay where they are).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import longpath as lpth

PACKAGES = "Underwriting/Financing/Lender Submission Package"
CHECKLIST_FILE = "00 Document Checklist.xlsx"
SKIP_DIRS = {"archive", "archived", "previous files", "lender submission package"}


@dataclass(frozen=True)
class Doc:
    name: str
    note: str = ""
    folders: tuple[str, ...] = ()       # the deal folder's own places for it (relative paths)
    words: tuple[str, ...] = ()         # words in a file name that suggest it
    personal: tuple[str, ...] = ()      # places for it in your personal folder (see personal.py)


AM = "Asset Management"
UW = "Underwriting"

CHECKLIST: list[Doc] = [
    Doc("Purchase and Sale Agreement", "The signed APS and every amendment and waiver",
        (f"{UW}/Legal and Admin/Purchase and Sales Agreement", f"{AM}/Legal and Admin/APS"),
        ("aps", "purchase and sale", "agreement of purchase", "amendment", "waiver")),
    Doc("Letter of Intent", "", (f"{UW}/Legal and Admin/Purchase and Sales Agreement/Initial Letter of Intent",
                                 f"{AM}/Legal and Admin/APS/Latest LOI"), ("loi", "letter of intent")),
    Doc("Rent Roll", "Current, by unit: tenant, rent, lease start, deposits", (), ("rent roll", "rentroll")),
    Doc("Operating Statements", "Trailing 12 months and the last 2–3 years of income and expenses",
        (f"{AM}/Accounting/Financial Statements",),
        ("t12", "t-12", "trailing", "operating statement", "income statement", "financial statement", "p&l",
         "profit and loss", "income and expense")),
    Doc("Property Tax Bill", "The latest final tax bill (and assessment notice)", (f"{AM}/Accounting/Taxes",),
        ("tax bill", "property tax", "assessment", "mpac")),
    Doc("Utility Bills", "The last 12 months of hydro, gas and water", (), ("hydro", "enbridge", "utility",
                                                                       "utilities", "water bill", "gas bill")),
    Doc("Insurance", "Quote or binder, with the lender named as mortgagee",
        (f"{UW}/Contracts and Quotes/Insurance",), ("insurance", "binder")),
    Doc("Property Management Agreement", "", (f"{UW}/Contracts and Quotes/Property Management",),
        ("property management", "management agreement")),
    Doc("Appraisal", "", (f"{AM}/Property/Initial DD Documents/Consultant Reports/Appraisal",), ("appraisal",)),
    Doc("Building Condition Assessment", "", (f"{AM}/Property/Initial DD Documents/Consultant Reports/BCA",),
        ("bca", "building condition", "property condition", "pca")),
    Doc("Environmental (Phase I ESA)", "", (f"{AM}/Property/Initial DD Documents/Consultant Reports/ESA",),
        ("esa", "environmental", "phase i", "phase 1")),
    Doc("Capital Expenditure Plan", "Renovation budget and what's been spent so far",
        (f"{AM}/Property/Construction",), ("capex", "construction cost", "renovation budget", "reno budget")),
    Doc("Financial Model / Pro Forma", "", (f"{UW}/Financial Model", f"{AM}/Property/Financial Model"),
        ("pro forma", "proforma", "financial model", "underwriting model")),
    Doc("Property Photos", "", (f"{UW}/Pictures",), ("photo",)),
    Doc("Corporate Documents", "Articles and certificate of incorporation of the borrower",
        (f"{UW}/Legal and Admin/Corporate Documents", f"{AM}/Legal and Admin/Corporation Document"),
        ("articles of incorporation", "certificate of incorporation", "articles of amendment")),
    Doc("Ownership Structure", "Shareholder / LP agreement, org chart", (
        f"{UW}/Legal and Admin/Shareholder Documents", f"{AM}/Legal and Admin/Investor Documents"),
        ("shareholder", "org chart", "organization chart", "lp agreement", "partnership agreement")),
    Doc("Borrower and Guarantor Information", "Net worth statements, schedule of real estate owned, credit consent, "
                                             "ID, résumés", (),
        ("net worth", "pnws", "real estate owned", "schedule of real estate", "reo schedule", "credit consent",
         "guarantor", "resume", "résumé"),
        ("Net Worth Statements", "Identification", "Credit Reports", "Résumé", "Real Estate Owned")),
    Doc("Personal Tax Returns and NOAs", "T1 returns, notices of assessment and T4s — usually the last 2–3 years",
        (), ("t1 general", "notice of assessment", "noa", "t4"), ("Income Tax",)),
    Doc("Leases and Tenant Forms", "", (f"{AM}/Property/Leasing/Leases",), ("lease",)),
    Doc("Survey and Title", "", (f"{AM}/Legal and Admin/Closing Documents",), ("survey", "title", "parcel register")),
    Doc("Existing Financing", "Current mortgage commitment and statements", (f"{AM}/Accounting/Financing Documents",),
        ("mortgage statement", "commitment letter", "charge")),
    Doc("Lender Term Sheet and Requests", "", (f"{UW}/Financing/Term Sheet", f"{UW}/Financing/Commitment Letter",
                                               f"{UW}/Financing/Lender Document Request"), ("term sheet",)),
    Doc("Banking", "Void cheque / account details", (f"{AM}/Accounting/Banking/Banking Information",),
        ("void cheque", "void check", "direct deposit")),
]


@dataclass
class Candidate:
    path: Path
    rel: str                 # relative to the deal folder
    picked: bool             # picked to start with
    why: str                 # "in its folder" / "by its name"
    modified: datetime | None = None


@dataclass
class Pick:
    doc: Doc
    files: list[Candidate] = field(default_factory=list)


def _skipped(rel_parts: tuple[str, ...]) -> bool:
    return any(p.lower() in SKIP_DIRS for p in rel_parts)


def _matches(name: str, words: tuple[str, ...]) -> bool:
    low = " " + re.sub(r"[_\-.]+", " ", name.lower()) + " "
    for w in words:
        if " " in w:
            if w in low:
                return True
        elif re.search(rf"(?<![a-z0-9]){re.escape(w)}" + (r"(?![a-z0-9])" if len(w) <= 4 else ""), low):
            return True                           # a word at its start (photo → photos); short ones (esa) whole
    return False


def _modified(p: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(lpth.stat(p).st_mtime)
    except (OSError, AttributeError):
        return None


def _personal(personal: Path | None, d: Doc) -> list[Candidate]:
    """The document's files in your personal folder (not the archived ones): picked to start with."""
    if personal is None or not d.personal or not lpth.is_dir(personal):
        return []
    out = []
    for sub in d.personal:
        base = Path(personal) / sub
        if not lpth.is_dir(base):
            continue
        for p in sorted(lpth.walk_files(base)):
            rel = p.relative_to(personal)
            if _skipped(rel.parts[:-1]) or p.name.startswith(("~$", ".")) or p.name.lower() == "desktop.ini":
                continue
            out.append(Candidate(p, f"Personal/{rel.as_posix()}", True, "personal folder", _modified(p)))
    return out


def candidates(folder: Path, personal: Path | None = None) -> list[Pick]:
    """For each document on the checklist, the files in the deal folder (and, for your own documents, your
    personal folder) that could go in it."""
    folder = Path(folder)
    files = []
    if lpth.is_dir(folder):
        for p in lpth.walk_files(folder):
            rel = p.relative_to(folder)
            if _skipped(rel.parts[:-1]) or p.name.startswith(("~$", ".")) or p.name.lower() == "desktop.ini":
                continue
            files.append((p, rel))
    own_place: dict[Path, tuple[int, str]] = {}  # file -> (depth, the document whose folder it's in): the deepest
    for d in CHECKLIST:
        for f in d.folders:
            pre = tuple(Path(f).parts)
            for p, rel in files:
                if rel.parts[:len(pre)] == pre and len(pre) > own_place.get(p, (0, ""))[0]:
                    own_place[p] = (len(pre), d.name)
    own_place = {p: name for p, (_n, name) in own_place.items()}
    out = []
    for d in CHECKLIST:
        mine = [Candidate(p, rel.as_posix(), True, "in its folder", _modified(p))
                for p, rel in files if own_place.get(p) == d.name]
        named = [Candidate(p, rel.as_posix(), not mine, "by its name", _modified(p))
                 for p, rel in files if p not in own_place and _matches(p.stem, d.words)]
        out.append(Pick(d, mine + _personal(personal, d) + named))
    return out


def package_name(on: date | None = None, lender: str = "") -> str:
    on = on or date.today()
    return f"{on.isoformat()} {lender.strip() or 'Lender'} Submission"


def _safe(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*]+', "-", name).strip(" .") or "Package"


def _free(path: Path) -> Path:
    if not lpth.exists(path):
        return path
    n = 2
    while True:
        p = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not lpth.exists(p):
            return p
        n += 1


def generate(folder: Path, picks: list[Pick], name: str | None = None, *, extra: list[Path] | None = None) -> Path:
    """Copy the picked files into a new package folder (a numbered folder per document, a checklist of what's in it
    and what's missing). ``picks`` hold only the files to copy. Returns the package folder."""
    base = Path(folder) / PACKAGES
    lpth.mkdir(base)
    pkg = _free(base / _safe(name or package_name()))
    lpth.mkdir(pkg)
    rows = []
    for i, pk in enumerate(picks, 1):
        sub = pkg / f"{i:02d} {_safe(pk.doc.name)}"
        copied = []
        for c in pk.files:
            lpth.mkdir(sub)
            dst = _free(sub / c.path.name)
            lpth.copy2(c.path, dst)
            copied.append(dst.name)
        rows.append((i, pk.doc.name, pk.doc.note, copied))
    if extra:
        sub = pkg / f"{len(picks) + 1:02d} Other Documents"
        copied = []
        for p in extra:
            lpth.mkdir(sub)
            dst = _free(sub / Path(p).name)
            lpth.copy2(p, dst)
            copied.append(dst.name)
        rows.append((len(picks) + 1, "Other Documents", "", copied))
    write_checklist(pkg / CHECKLIST_FILE, rows)
    return pkg


def write_checklist(path: Path, rows: list[tuple[int, str, str, list[str]]]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Checklist"
    ws.append(["#", "Document", "Status", "Files", "Notes"])
    for c in ws[1]:
        c.font = Font(bold=True)
    missing = PatternFill("solid", fgColor="FDECEA")
    for i, doc, note, files in rows:
        ws.append([i, doc, "Included" if files else "Missing", "\n".join(files), note])
        if not files:
            for c in ws[ws.max_row]:
                c.fill = missing
    for col, w in zip("ABCDE", (5, 34, 11, 60, 60)):
        ws.column_dimensions[col].width = w
    wb.save(lpth.lp(path))
