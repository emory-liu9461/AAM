"""Deal folders: every deal's files organised the same way, from underwriting through asset management.

::

    <Deals folder>/<deal>/
        Underwriting/              set up when the deal is added
            Presentation/  Financial Model/  Legal and Admin/  Due Diligence Files/  Contracts and Quotes/
            Pictures/  Financing/
        Asset Management/          set up when the deal closes
            Accounting/  Legal and Admin/  Property/

The rules:

* **The live file is the only file kept in a folder; anything older goes in its Archive.** (Model 2.0 and Model 3.0:
  only Model 3.0 stays outside.) *Archive Old Versions* finds files that are versions of the same document (v2 / v3,
  2.0 / 3.0, dates, "draft", "final", "(1)" …) and moves all but the newest into the folder's Archive.
* **Required items** (yellow in the standard) must not be empty — a required folder needs a file in it, a live file
  needs a file directly in its folder. The deal's Deal Folder tab shows what's missing.
* **Folders that repeat** are made from the deal's own records: a folder per vendor (operating costs' providers), per
  unit (the rent roll), per year and month (since closing), per mortgage (Financing & Cap Table).
* **Names**: where the standard sets a naming convention (e.g. "## Street Name, Vendor, Month, Invoice"), adding a file
  to that folder suggests the name, with the street and date filled in.
* **On closing**, the underwriting's purchase-and-sale and corporate documents are copied into Asset Management (Legal
  and Admin → APS and Corporation Document), as the standard says.

Nothing is ever deleted: setting up again only adds what's missing.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ORDINALS = ["First", "Second", "Third", "Fourth", "Fifth", "Sixth"]


@dataclass
class Node:
    name: str
    kind: str = "folder"          # folder | live (a file kept directly in the parent) | file (a named file)
    required: bool = False
    children: list["Node"] = field(default_factory=list)
    note: str = ""                # what goes here
    naming: str = ""              # the naming convention for files here
    repeat: str = ""              # vendors | units | years | months | loans: one copy per …
    ext: str = ""                 # a live file's expected type (".docx", ".pdf")


def F(name, *children, required=False, note="", naming="", repeat="") -> Node:  # noqa: N802
    return Node(name, "folder", required, list(children), note, naming, repeat)


def LIVE(name, ext="", required=True, note="") -> Node:  # noqa: N802
    return Node(name, "live", required, [], note, ext=ext)


def FILE(name, required=False, note="") -> Node:  # noqa: N802
    return Node(name, "file", required, [], note)


ARCHIVE = "Archive"


def A() -> Node:  # noqa: N802
    return F(ARCHIVE, note="Older versions — only the live file stays outside")


INVOICE_NAME = "## Street Name, Vendor, Month, Invoice"
SUPPLIER_NAME = "## Street Name, Name of Supplier, Date"
SIGNED_NAME = "Entity Signing Name - ## Street Name, {doc}"


def _investor_doc(name: str) -> Node:
    return F(name, LIVE("Latest clean (Word)", ".docx"), LIVE("Latest clean (PDF)", ".pdf"),
             F("Signed", required=True, naming=SIGNED_NAME.format(doc=name)), A())


def _mortgage() -> Node:
    return F("Mortgage", F("Commitment Letter and Term Sheet", F("Executed", required=True), A(),
                           F("Mortgage Documents"), required=True), repeat="loans")


def _material_labour() -> list[Node]:
    return [F("Material", required=True, naming=SUPPLIER_NAME), F("Labour", required=True, naming=SUPPLIER_NAME)]


def _report(name: str) -> Node:
    return F(name, F("Final Report", required=True), F("Reliance Letter", required=True), A())


UNDERWRITING = F(
    "Underwriting",
    F("Presentation", A(), LIVE("Live file")),
    F("Financial Model", A(), LIVE("Live file")),
    F("Legal and Admin",
      F("Purchase and Sales Agreement", A(), LIVE("Latest P&S agreement and any amendments"),
        F("Initial Letter of Intent", required=True)),
      F("Corporate Documents", F("Articles of Incorporation", required=True), F("Minute Book"), F("HST Registration")),
      F("Shareholder Documents", F("Investor Term Sheet"), F("Shareholder Agreement - LP Agreement", required=True),
        F("Subscription Agreement", required=True))),
    F("Due Diligence Files", note="Files received from the broker"),
    F("Contracts and Quotes",
      F("Insurance", F("Quote", required=True), F("Contract", required=True), A()),
      F("Property Management", F("Quote", required=True), F("Contract", required=True), A()),
      note="Any other contract: a folder with Quote, Contract and Archive, like these"),
    F("Pictures", F("Original (Provided) Photos"), F("Updated (Taken Internally) Photos")),
    F("Financing", F("Term Sheet"), F("Commitment Letter"), F("Lender Document Request")),
)

ASSET_MANAGEMENT = F(
    "Asset Management",
    F("Accounting",
      F("QuickBooks File", LIVE("Latest file"), A(), note="The latest accounting file"),
      F("Banking", F("Banking Information", F("Void Cheque", required=True), F("Bank Setup Documents")),
        F("Monthly Statements", required=True)),
      F("Financial Statements", F("Income Statement", LIVE("Latest"), A()), F("Balance Sheet", LIVE("Latest"), A())),
      F("Taxes"),
      F("Financing Documents", _mortgage(), required=True),
      F("Income", F("Owner Statements", required=True), F("Other Revenues")),
      F("Invoices",
        F("Invoice Requests"),
        F("Operating Invoices",
          F("Vendor Invoices", F("Invoices from the Last 12 Months", required=True, naming=INVOICE_NAME),
            F("Archived"), repeat="vendors")),
        F("Construction Invoices",
          F("AAM Invoices", FILE("## Street Name AAM Invoices.xlsx", required=True),
            F("Year", F("Month", required=True, naming="Invoice Number . ## Street Name AAM Invoice (Date)",
                        repeat="months"), repeat="years")),
          F("Third-party Invoices",
            F("Unit", *_material_labour(), repeat="units"),
            F("Common Area", F("Roof", *_material_labour()), F("Hallways", *_material_labour()),
              F("Stairwell", *_material_labour()))))),
      F("Closing Balances", F("Statement of Advance from Any Mortgage", required=True),
        F("Statement of Adjustments", required=True), F("Trust Ledger", required=True)),
      F("Distributions", FILE("## Street Name, Distributions.xlsx"), F("Distribution Receipts"))),
    F("Legal and Admin",
      F("APS", LIVE("Latest APS and amendments"), F("Latest LOI", required=True), A(),
        note="Moved over from Underwriting on closing"),
      F("Closing Documents", F("Land Title", required=True), F("Registrations and Charges", required=True), A()),
      F("Corporation Document", F("Articles of Incorporation", required=True),
        F("Certificates of Incorporation", required=True), F("Minute Book", required=True),
        F("Corporate Amendments", required=True), note="Moved over from Underwriting on closing"),
      F("Investor Documents", _investor_doc("Term Sheet"), _investor_doc("Shareholders Agreement"),
        _investor_doc("Subscription Agreement"),
        F("Other Investor Documents",
          F("Shareholder Directions", F("Fin Track Forms"), F("Directions"), F("Schedules"))))),
    F("Property",
      F("Financial Model", LIVE("Latest"), A()),
      F("Contracts - Agreements",
        F("Vendor", F("Agreements", required=True), repeat="vendors"),
        F("Pest Control"), F("Lawn Mowing"), F("Snow Removal"), F("Hydro"), F("Waste Management")),
      F("Initial DD Documents", F("Receipts from Broker - Seller"),
        F("Consultant Reports", _report("Appraisal"), _report("BCA"), _report("ESA"))),
      F("Construction", F("Quotes"), F("Contracts"), FILE("Construction Cost Tracking.xlsx"),
        naming="Unit #, ## Street Name, Name of Supplier, Date  ·  Common Area, ## Street Name, Name of Supplier, Date"),
      F("Leasing",
        F("Leases", F("Units", F("Cash for Keys"), F("N-11, N-12 etc")), F("Rent Increase Documents"),
          F("Tenant Forms", required=True)),
        F("Listings (Marketing)")),
      F("Investor Updates", LIVE("Latest monthly update"), A()),
      F("General Property Documents", note="Lock combinations, lockbox information etc.")),
)

# on closing: copied from Underwriting into Asset Management
TRANSFERS = [("Underwriting/Legal and Admin/Purchase and Sales Agreement", "Asset Management/Legal and Admin/APS"),
             ("Underwriting/Legal and Admin/Corporate Documents", "Asset Management/Legal and Admin/Corporation Document")]


# ---- what the repeated folders are for this deal ----

@dataclass
class Context:
    street: str = "## Street Name"
    vendors: list[str] = field(default_factory=list)
    units: list[str] = field(default_factory=list)
    years: list[int] = field(default_factory=list)
    loans: int = 1


def street_of(deal) -> str:
    """"45 Elm St" from the address "45 Elm St, Hamilton ON" (or the deal's name)."""
    addr = (getattr(deal, "address", "") or "").split(",")[0].strip()
    return addr or deal.name.strip()


def context(pf, deal, today: date | None = None) -> Context:
    from . import operations as op
    from . import portfolio as pfm

    today = today or date.today()
    ctx = Context(street_of(deal))
    h = next((x for x in pf.holdings() if x.id == deal.holding_id), None) if deal.holding_id else None
    if h is not None:
        try:
            from . import op_costs as oc

            ctx.vendors = sorted({ln.provider.strip() for ln in oc.lines(pf, h.id) if ln.provider.strip()},
                                 key=str.lower)
        except Exception:  # noqa: BLE001
            pass
        ctx.units = [u.unit for u in op.units(pf, h.id) if u.unit.strip()]
        try:
            ctx.loans = max(1, len(pfm.loans(pf, h, with_books=False)))
        except Exception:  # noqa: BLE001
            pass
    start = (deal.closing_date or (h.acquired if h else None) or today.isoformat())[:4]
    ctx.years = list(range(min(int(start), today.year), today.year + 1))
    return ctx


def _safe(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]+', "-", name).strip().rstrip(".")
    return re.sub(r"\s+", " ", name) or "Folder"


@dataclass
class Item:
    """One folder or file of the standard, laid out for this deal."""
    rel: str                       # path relative to the deal folder
    node: Node
    depth: int
    label: str                     # the name shown
    naming: str = ""               # with the street filled in

    @property
    def is_folder(self) -> bool:
        return self.node.kind == "folder"


def _expand(node: Node, ctx: Context, month: int | None = None) -> list[tuple[str, Node]]:
    """(folder name, node) for each copy of a repeated folder."""
    if node.repeat == "vendors":
        names = ctx.vendors or []
        return [(f"{v} Invoices" if node.name == "Vendor Invoices" else v, node) for v in names]
    if node.repeat == "units":
        return [(f"Unit {u}" if not u.lower().startswith(("unit", "suite", "apt")) else u, node) for u in ctx.units]
    if node.repeat == "years":
        return [(str(y), node) for y in ctx.years]
    if node.repeat == "months":
        return [(f"{m:02d} {MONTHS[m - 1]}", node) for m in range(1, 13)]
    if node.repeat == "loans":
        return [(f"{ORDINALS[i] if i < len(ORDINALS) else f'#{i + 1}'} Mortgage", node) for i in range(ctx.loans)]
    return [(node.name, node)]


def layout(ctx: Context, *, asset_management: bool = True) -> list[Item]:
    """Every folder and file of the standard for this deal, in the document's order."""
    out: list[Item] = []

    def walk(node: Node, rel: str, depth: int) -> None:
        for name, n in _expand(node, ctx):
            label = name if n.kind == "folder" else n.name.replace("## Street Name", ctx.street)
            path = f"{rel}/{_safe(label)}" if rel else _safe(label)
            naming = n.naming.replace("## Street Name", ctx.street)
            if n.kind == "live":
                path = rel                     # a live file sits directly in its folder
            out.append(Item(path, n, depth, label, naming))
            if n.kind == "folder":
                for c in n.children:
                    walk(c, path, depth + 1)

    walk(UNDERWRITING, "", 0)
    if asset_management:
        walk(ASSET_MANAGEMENT, "", 0)
    return out


# ---- on disk ----

def deal_folder(root: str | Path, deal) -> Path:
    return Path(root) / _safe(deal.name)


def create(folder: Path, ctx: Context, *, asset_management: bool = False) -> int:
    """Make the folders that aren't there yet. Returns how many were made."""
    n = 0
    for it in layout(ctx, asset_management=asset_management):
        if it.is_folder:
            p = folder / it.rel
            if not p.exists():
                p.mkdir(parents=True, exist_ok=True)
                n += 1
    return n


def _files(p: Path) -> list[Path]:
    try:
        return [x for x in p.iterdir() if x.is_file() and not x.name.startswith((".", "~$"))]
    except OSError:
        return []


def _any_file(p: Path) -> bool:
    try:
        return any(x.is_file() and not x.name.startswith((".", "~$")) for x in p.rglob("*"))
    except OSError:
        return False


@dataclass
class Status:
    item: Item
    exists: bool
    files: int                    # files directly in it (a folder) / matching files (a live or named file)
    ok: bool                      # a required item that's in place (or not required)

    @property
    def missing(self) -> bool:
        return self.item.node.required and not self.ok


def check(folder: Path, ctx: Context, *, asset_management: bool = False) -> list[Status]:
    out = []
    for it in layout(ctx, asset_management=asset_management):
        p = folder / it.rel
        if it.node.kind == "folder":
            files = len(_files(p)) if p.exists() else 0
            ok = (not it.node.required) or (p.exists() and _any_file(p))
            out.append(Status(it, p.exists(), files, ok))
        elif it.node.kind == "live":
            files = [f for f in _files(p) if not it.node.ext or f.suffix.lower() == it.node.ext]
            out.append(Status(it, p.exists(), len(files), (not it.node.required) or bool(files)))
        else:                                   # a named file: matched by its name without the extension
            want = Path(_safe(it.label)).stem.lower()
            files = [f for f in _files(p.parent) if f.stem.lower().startswith(want)]
            out.append(Status(it, bool(files), len(files), (not it.node.required) or bool(files)))
    return out


def summary(rows: list[Status]) -> tuple[int, int]:
    """(required items in place, required items)."""
    req = [s for s in rows if s.item.node.required]
    return sum(1 for s in req if s.ok), len(req)


# ---- live file / archive ----

VERSION_BITS = [r"\(\d+\)", r"\bcopy\b", r"\b(v|ver|version|rev|r)\.?\s*\d+(\.\d+)*\b", r"\b\d+\.\d+(\.\d+)*\b",
                r"\b\d{4}[-_.]\d{1,2}[-_.]\d{1,2}\b", r"\b\d{1,2}[-_.]\d{1,2}[-_.]\d{2,4}\b", r"\b\d{8}\b",
                r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s*\d{1,2}(st|nd|rd|th)?,?\s*\d{4}\b",
                r"\b(draft|final|updated|update|latest|new|old|revised|clean)\b"]


def doc_key(name: str) -> str:
    """A file's name without its version: "Model 3.0.xlsx" and "model v2 (1).xlsx" both give "model|.xlsx"."""
    p = Path(name)
    s = p.stem.lower()
    for pat in VERSION_BITS:
        s = re.sub(pat, " ", s)
    s = re.sub(r"[\s_\-.,]+", " ", s).strip()
    return f"{s}|{p.suffix.lower()}"


def _version(p: Path) -> tuple:
    """Sort key: the highest version number in the name, then the date in it, then when it was last changed."""
    s = p.stem.lower()
    nums = [tuple(int(x) for x in m.group(2).split(".")) for m in
            re.finditer(r"\b(v|ver|version|rev|r)?\.?\s*(\d+(?:\.\d+)+|\d+)\b", s)
            if m.group(1) or "." in m.group(2)]
    d = re.search(r"\b(\d{4})[-_.](\d{1,2})[-_.](\d{1,2})\b", s)
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0
    return (max(nums) if nums else (), tuple(int(x) for x in d.groups()) if d else (), mtime)


def old_versions(folder: Path) -> list[tuple[Path, Path]]:
    """(file, the newer file it's an older version of) for files in a folder with an Archive folder (or one of the
    standard's Archive places)."""
    groups: dict[str, list[Path]] = {}
    for f in _files(folder):
        groups.setdefault(doc_key(f.name), []).append(f)
    out = []
    for files in groups.values():
        if len(files) < 2:
            continue
        files.sort(key=_version)
        newest = files[-1]
        out += [(f, newest) for f in files[:-1]]
    return out


def archive_plan(folder: Path, ctx: Context, *, asset_management: bool = True) -> list[tuple[Path, Path]]:
    """Every older version in the deal's folders that have an Archive: [(file, where it goes)]."""
    out = []
    for it in layout(ctx, asset_management=asset_management):
        if it.is_folder and any(c.name == ARCHIVE for c in it.node.children):
            p = folder / it.rel
            if p.exists():
                for f, _newer in old_versions(p):
                    out.append((f, p / ARCHIVE / f.name))
    return out


def archive(plan: list[tuple[Path, Path]]) -> int:
    n = 0
    for src, dest in plan:
        dest.parent.mkdir(parents=True, exist_ok=True)
        target, k = dest, 2
        while target.exists():
            target = dest.with_name(f"{dest.stem} ({k}){dest.suffix}")
            k += 1
        shutil.move(str(src), str(target))
        n += 1
    return n


# ---- closing ----

def transfer_on_closing(folder: Path) -> int:
    """Copy the underwriting's legal documents into Asset Management (files already there are left). Returns how
    many files were copied."""
    n = 0
    for src_rel, dest_rel in TRANSFERS:
        src, dest = folder / src_rel, folder / dest_rel
        if not src.exists():
            continue
        for f in src.rglob("*"):
            if not f.is_file():
                continue
            target = dest / f.relative_to(src)
            if target.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            n += 1
    return n


# ---- filing a document ----

def suggested_name(item: Item, source: Path, *, vendor: str = "", on: date | None = None) -> str:
    """The name the folder's naming convention gives a file (the original name when it has none)."""
    if not item.naming:
        return source.name
    if on is None:
        on = date.fromtimestamp(source.stat().st_mtime) if source.exists() else date.today()
    d = f"{on:%b} {on.day}, {on.year}"
    name = item.naming.split("  ·  ")[0]
    name = (name.replace("Name of Supplier", vendor or "Supplier").replace("Vendor", vendor or "Vendor")
            .replace("Month", f"{on:%B} {on.year}").replace("(Date)", f"({d})").replace("Date", d))
    if "Entity Signing Name" in name:
        name = name.replace("Entity Signing Name", vendor or "Entity")
    return _safe(name) + source.suffix.lower()


def file_into(folder: Path, item: Item, source: str | Path, name: str | None = None, *, move: bool = False) -> Path:
    """Copy (or move) a file into the item's folder under ``name`` (never overwriting)."""
    source = Path(source)
    dest_dir = folder / item.rel
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _safe(Path(name).stem) + (Path(name).suffix or source.suffix) if name else source.name
    dest, k = dest_dir / name, 2
    while dest.exists():
        dest = dest_dir / f"{Path(name).stem} ({k}){Path(name).suffix}"
        k += 1
    (shutil.move if move else shutil.copy2)(str(source), str(dest))
    return dest


# ---- where the folders are (kept in the portfolio file) ----

def root(pf) -> str:
    r = pf.conn.execute("SELECT value FROM settings WHERE key='deal_folders_root'").fetchone()
    return r[0] if r else ""


def set_root(pf, path: str) -> None:
    with pf.conn:
        pf.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('deal_folders_root', ?)",
                        (str(path).strip(),))


def folder_of(pf, deal) -> Path | None:
    """The deal's folder: the one picked for it, else <Deals folder>/<deal name>; None without a Deals folder."""
    r = pf.conn.execute("SELECT value FROM settings WHERE key=?", (f"deal_folder:{deal.id}",)).fetchone()
    if r and r[0]:
        return Path(r[0])
    base = root(pf)
    return deal_folder(base, deal) if base else None


def set_folder_of(pf, deal, path: str | Path) -> None:
    with pf.conn:
        pf.conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)",
                        (f"deal_folder:{deal.id}", str(path)))


def wants_asset_management(deal) -> bool:
    return deal.stage == "Closed" or bool(deal.holding_id)


def on_closing(pf, deal) -> tuple[int, int]:
    """When a deal closes: its Asset Management folders, and the underwriting's legal documents copied over.
    Returns (folders made, files copied); (0, 0) without a deal folder."""
    folder = folder_of(pf, deal)
    if folder is None or not folder.exists():
        return 0, 0
    ctx = context(pf, deal)
    made = create(folder, ctx, asset_management=True)
    return made, transfer_on_closing(folder)


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")
