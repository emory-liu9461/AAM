"""Outstanding receivables and payables, line by line (the AR & AP tab and its Excel export)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import receivables as rx
from .books import Books

BUCKETS = ["Current", "1–30", "31–60", "61–90", "90+"]


def bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "Current"
    if days_overdue <= 30:
        return "1–30"
    if days_overdue <= 60:
        return "31–60"
    if days_overdue <= 90:
        return "61–90"
    return "90+"


@dataclass
class Item:
    doc_id: int
    kind: str          # invoice | bill
    account: str       # AR / AP account it's owed in
    party: str
    number: str
    issue_date: str
    due_date: str
    days_overdue: int
    amount_cents: int
    paid_cents: int
    balance_cents: int
    description: str
    files: list = None       # [(path, how)] — the invoice / bill document(s) we have
    source: str = "document"  # document | bank | opening | journal  (the last three: posted straight to AR / AP)
    key: str = ""            # ledger items: 'tx:<id>' or 'je:<entry>:<account>' — names and files are kept by this
    tx_id: int | None = None

    @property
    def is_document(self) -> bool:
        return self.source == "document"

    @property
    def aging(self) -> str:
        return bucket(self.days_overdue)

    @property
    def has_document(self) -> bool:
        return bool(self.files)

    @property
    def document_status(self) -> str:
        if not self.files:
            return "Missing" if self.is_document else {"bank": "No invoice (bank entry)",
                                                       "opening": "No invoice (opening balance)",
                                                       "journal": "No invoice (journal entry)"}[self.source]
        hows = {h for _p, h in self.files}
        if hows == {"created"}:
            return "Created in app"
        return "Attached" + (f" ({len(self.files)})" if len(self.files) > 1 else "")


def document_files(books: Books, doc_id: int | None = None) -> dict[int, list[tuple[Path, str]]]:
    """Files we have for each invoice / bill: attached or app-made files in Invoices & Bills, and bills
    waiting in the Invoice Dump's Future Payables. {document id: [(path, 'attached'|'created'|'future payable')]}"""
    from . import doc_folder as df

    out: dict[int, list[tuple[Path, str]]] = {}
    cfg = df.get_config(books)
    if cfg.enabled:
        for f in df.files_for(books, doc_id):
            p = f.path(cfg)
            if p.exists():
                out.setdefault(f.document_id, []).append((p, "created" if f.generated else "attached"))
    if books._has_table("dump_files"):
        try:
            from . import invoice_dump as dump

            root = dump.dump_root(books)
            if root is not None:
                cols = [c[1] for c in books.conn.execute("PRAGMA table_info(dump_files)")]
                if "document_id" in cols:
                    sql = "SELECT document_id, rel_path FROM dump_files WHERE document_id IS NOT NULL"
                    args: tuple = ()
                    if doc_id is not None:
                        sql += " AND document_id=?"
                        args = (doc_id,)
                    for d, rel in books.conn.execute(sql, args):
                        p = dump._abs(books, rel)
                        if p.exists():
                            out.setdefault(d, []).append((p, "future payable"))
        except Exception:  # noqa: BLE001 - a missing dump folder just means no files from there
            pass
    return out


LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS arap_labels (
    item_key  TEXT PRIMARY KEY,
    party     TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS arap_files (
    id        INTEGER PRIMARY KEY,
    item_key  TEXT NOT NULL,
    rel_path  TEXT NOT NULL
);
"""
SUPPORT_DIR = "AR-AP Support"   # files attached to items posted straight to AR / AP (in Invoices & Bills)


def _ensure_ledger_schema(books: Books) -> None:
    books.conn.executescript(LEDGER_SCHEMA)


def _party_from(description: str, memo: str) -> str:
    """Best guess at who a bank line is with: e-Transfer / cheque notes, then the description."""
    from .books import merchant_key

    m = re.search(r"(?:↔ e-Transfer (?:to|from)|✎ (?:Cheque|Draft)(?: #\S+)? to)\s+(.+?)(?:\s+“|$)", memo)
    if m:
        return m.group(1).strip()
    return (merchant_key(description).title() or description.strip())[:60]


def ledger_items(books: Books, kind: str, as_of: date | None = None, search: str = "") -> list[Item]:
    """Everything in the AR / AP accounts that isn't an invoice or bill: bank transactions categorized to
    AR / AP, opening balances and journal entries — one line each, signed so + means owed (to you for AR,
    by you for AP). Bank lines that pay invoices / bills only show the part not applied to them."""
    as_of = as_of or date.today()
    _ensure_ledger_schema(books)
    ids = rx.control_ids(books, kind)
    if not ids:
        return []
    doc_entries = {r[0] for r in books.conn.execute("SELECT entry_id FROM documents WHERE entry_id IS NOT NULL")}
    applied = dict(books.conn.execute("SELECT transaction_id, SUM(amount_cents) FROM document_payments "
                                      "GROUP BY transaction_id").fetchall())
    labels = dict(books.conn.execute("SELECT item_key, party FROM arap_labels").fetchall())
    from . import parties

    tx_names = parties.names(books)
    files: dict[str, list] = {}
    from . import doc_folder as df

    cfg = df.get_config(books)
    if cfg.enabled:
        for k, rel in books.conn.execute("SELECT item_key, rel_path FROM arap_files"):
            p = cfg.root / rel
            if p.exists():
                files.setdefault(k, []).append((p, "attached"))
    has_party = "party" in {r[1] for r in books.conn.execute("PRAGMA table_info(journal_lines)")}
    q = ("SELECT l.account_id, l.debit_cents, l.credit_cents, e.id AS entry_id, e.date, e.memo AS je_memo, e.kind, "
         f"e.transaction_id, {'l.party' if has_party else chr(39) * 2} AS line_party FROM journal_lines l "
         "JOIN journal_entries e ON e.id = l.entry_id "
         f"WHERE l.account_id IN ({','.join('?' * len(ids))}) AND e.date <= ? ORDER BY e.date, e.id")
    entry_party = books.entry_parties()
    out: list[Item] = []
    for row in books.conn.execute(q, [*ids, as_of.isoformat()]):
        if row["entry_id"] in doc_entries:
            continue
        net = row["debit_cents"] - row["credit_cents"]
        amount = net if kind == "invoice" else -net
        tx = None
        if row["transaction_id"]:
            tx = books.transaction(row["transaction_id"])
            used = applied.get(tx.id, 0)
            if used:
                left = abs(tx.amount_cents) - used
                if left <= 0:
                    continue
                amount = left if amount > 0 else -left
            source, key = "bank", f"tx:{tx.id}"
            desc = tx.description + (f" · {tx.memo}" if tx.memo else "")
            party = labels.get(key) or (tx_names[tx.id].name if tx.id in tx_names else
                                        _party_from(tx.description, tx.memo))
        else:
            source = "opening" if row["kind"] == "opening" else "journal"
            key = f"je:{row['entry_id']}:{row['account_id']}"
            desc = row["je_memo"]
            party = (labels.get(key) or (row["line_party"] or "").strip()          # named on the entry's line
                     or entry_party.get(row["entry_id"], ("", ""))[0 if kind == "invoice" else 1]   # … its side
                     or ("Opening balance" if source == "opening" else (row["je_memo"] or "Journal entry")))
        if amount == 0:
            continue
        if search and search.lower() not in f"{party} {desc}".lower():
            continue
        age = (as_of - date.fromisoformat(row["date"])).days
        acct = books.account(row["account_id"]).name
        out.append(Item(0, kind, acct, party, "", row["date"], "", age, amount, 0, amount, desc,
                        files.get(key, []), source, key, tx.id if tx else None))
    return out


def set_party(books: Books, item_key: str, party: str) -> None:
    """Name the customer / vendor for an item posted straight to AR / AP (blank: back to the automatic name).
    Bank lines get the name everywhere (To Categorize, Transactions), the same as naming them there."""
    _ensure_ledger_schema(books)
    if item_key.startswith("tx:"):
        from . import parties

        parties.set_name(books, [int(item_key[3:])], party)
        with books.conn:
            books.conn.execute("DELETE FROM arap_labels WHERE item_key=?", (item_key,))
        return
    with books.conn:
        if party.strip():
            books.conn.execute("INSERT INTO arap_labels(item_key, party) VALUES (?,?) ON CONFLICT(item_key) "
                               "DO UPDATE SET party=excluded.party", (item_key, party.strip()))
        else:
            books.conn.execute("DELETE FROM arap_labels WHERE item_key=?", (item_key,))


def attach_to_item(books: Books, item: "Item", source: Path) -> Path:
    """Attach a file (invoice, statement, agreement…) to any AR / AP line, invoice or not."""
    if item.is_document:
        return attach(books, item.doc_id, source)
    from . import doc_folder as df
    from .companies import safe_filename
    from .money import fmt_money
    from .pdf_to_excel import unique_path

    cfg = df.get_config(books)
    if not cfg.enabled:
        raise ValueError("Set up the company folder first (File → Company Folder & Auto-Save)")
    _ensure_ledger_schema(books)
    side = "Receivable" if item.kind == "invoice" else "Payable"
    stem = safe_filename(f"{item.issue_date} {item.party} {fmt_money(abs(item.amount_cents))}")
    dest = unique_path(cfg.root / SUPPORT_DIR / side / item.issue_date[:4] / f"{stem}{source.suffix.lower()}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)
    with books.conn:
        books.conn.execute("INSERT INTO arap_files(item_key, rel_path) VALUES (?,?)",
                           (item.key, dest.relative_to(cfg.root).as_posix()))
    return dest


def attach(books: Books, doc_id: int, source: Path, *, move: bool = False) -> Path:
    """Attach an invoice / bill file (copied into Invoices & Bills, renamed and sorted)."""
    from . import doc_folder as df

    return df.attach_file(books, doc_id, source, move=move)


def outstanding(books: Books, kind: str, as_of: date | None = None, search: str = "",
                overdue_only: bool = False, include_ledger: bool = True) -> list[Item]:
    """Everything outstanding in AR (kind='invoice') or AP ('bill') as of a date, by customer / vendor: open
    invoices / bills, plus amounts posted straight to the AR / AP accounts (bank entries, opening balances,
    journal entries) so the total equals the balance in the books."""
    as_of = as_of or date.today()
    names: dict[int, str] = {}

    def acct(i: int) -> str:
        if i not in names:
            try:
                names[i] = books.account(i).name
            except KeyError:
                names[i] = "?"
        return names[i]

    files = document_files(books)
    out = []
    for d in rx.documents(books, kind, status="open", as_of=as_of, search=search):
        late = d.days_overdue(as_of)
        if overdue_only and late <= 0:
            continue
        out.append(Item(d.id, kind, acct(d.control_id), d.party, d.number, d.issue_date, d.due_date, late,
                        d.amount_cents, d.paid_cents, d.balance_cents, d.memo, files.get(d.id, [])))
    if include_ledger and not overdue_only:
        out += ledger_items(books, kind, as_of, search)
    from . import contacts

    amap = contacts.alias_map(books)  # other names → the contact on the Customers & Vendors list
    for i in out:
        i.party = amap.get(i.party.lower(), i.party)
    out.sort(key=lambda i: (i.party.lower(), i.account, i.issue_date, i.number))  # by customer / vendor
    return out


@dataclass
class Totals:
    count: int
    balance_cents: int
    overdue_cents: int
    buckets: dict[str, int]
    ledger_cents: int      # balance of the AR / AP accounts in the books
    not_itemized: int      # part of the ledger not from invoices / bills (e.g. opening balances)


def totals(books: Books, kind: str, items: list[Item], as_of: date | None = None) -> Totals:
    b = dict.fromkeys(BUCKETS, 0)
    for i in items:
        b[i.aging] += i.balance_cents
    summary = rx.aging(books, kind, as_of or date.today())
    listed = sum(i.balance_cents for i in items)
    return Totals(len(items), listed, sum(i.balance_cents for i in items if i.is_document and i.days_overdue > 0),
                  b, summary.ledger_balance, summary.ledger_balance - listed)


def write_xlsx(books: Books, path: Path, as_of: date | None = None) -> Path:
    """Workbook with Receivable and Payable sheets: every open item, aging and totals (formulas)."""
    from openpyxl import Workbook
    from openpyxl.styles import Border, Font, PatternFill, Side

    as_of = as_of or date.today()
    wb = Workbook()
    money = '#,##0.00;[Red](#,##0.00);"-"'
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="E8EEF6")
    for n, kind in enumerate(("invoice", "bill")):
        ws = wb.active if n == 0 else wb.create_sheet()
        ws.title = "Receivable (AR)" if kind == "invoice" else "Payable (AP)"
        party = "Customer" if kind == "invoice" else "Vendor"
        ws.append([books.company_name])
        ws.append(["Accounts Receivable – outstanding" if kind == "invoice" else "Accounts Payable – outstanding"])
        ws.append([f"As of {as_of:%B} {as_of.day}, {as_of.year}"])
        ws.append([])
        for r in (1, 2):
            ws.cell(row=r, column=1).font = Font(bold=True, size=13)
        head = ["Account", party, "Number", "Date", "Due", "Days overdue", "Aging", "Amount", "Paid", "Balance",
                "Description", "Invoice on file" if kind == "invoice" else "Bill on file"]
        ws.append(head)
        hr = ws.max_row
        for c in range(1, len(head) + 1):
            ws.cell(row=hr, column=c).font = bold
            ws.cell(row=hr, column=c).fill = fill
            ws.cell(row=hr, column=c).border = Border(bottom=Side(style="thin"))
        items = outstanding(books, kind, as_of)
        for i in items:
            ws.append([i.account, i.party, i.number, i.issue_date, i.due_date,
                       max(i.days_overdue, 0) if i.is_document else None, i.aging,
                       i.amount_cents / 100, i.paid_cents / 100, i.balance_cents / 100, i.description,
                       i.document_status])
        first, last = hr + 1, ws.max_row
        if items:
            ws.append(["Total", "", "", "", "", "", "", f"=ROUND(SUM(H{first}:H{last}),2)",
                       f"=ROUND(SUM(I{first}:I{last}),2)", f"=ROUND(SUM(J{first}:J{last}),2)"])
            tot = ws.max_row
            for c in range(1, 11):
                ws.cell(row=tot, column=c).font = bold
            ws.append([])
            ws.append(["Aging"])
            ws.cell(row=ws.max_row, column=1).font = bold
            for b in BUCKETS:
                ws.append([b, "", "", "", "", "", "", "", "", f'=SUMIF(G{first}:G{last},"{b}",J{first}:J{last})'])
        t = totals(books, kind, items, as_of)
        ws.append([])
        ws.append(["Balance in the books", "", "", "", "", "", "", "", "", t.ledger_cents / 100])
        for row in ws.iter_rows(min_row=first, max_row=ws.max_row, min_col=8, max_col=10):
            for cell in row:
                cell.number_format = money
        ws.freeze_panes = ws.cell(row=first, column=1)
        if items:
            ws.auto_filter.ref = f"A{hr}:L{last}"
        for col, w in zip("ABCDEFGHIJKL", (26, 28, 14, 12, 12, 13, 10, 14, 14, 14, 36, 16)):
            ws.column_dimensions[col].width = w
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.page_setup.orientation = "landscape"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def assign(books: Books, items: list[Item], contact_name: str, *, move_account: bool = False) -> int:
    """Assign AR / AP lines to a customer / vendor (added to the Customers & Vendors list if new).

    Invoices / bills get the contact as their customer / vendor; bank lines and other entries are named after it
    (everywhere, not only here). With ``move_account``, lines move into the contact's own receivable / payable
    account (e.g. *Due from Sister Co*) — invoices / bills and bank lines; opening balances and journal entries
    stay where they were posted. Returns how many lines were assigned."""
    from . import contacts, parties

    if not items:
        return 0
    kind = items[0].kind
    c = contacts.ensure_contact(books, contact_name, "customer" if kind == "invoice" else "vendor")
    if c is None:
        raise ValueError("Choose a customer / vendor")
    target = (c.receivable_id if kind == "invoice" else c.payable_id) if move_account else None
    done = 0
    for it in items:
        if it.is_document:
            d = rx.get_document(books, it.doc_id)
            rx.update_document(books, d.id, party=c.name, issue_date=d.issue_date, due_date=d.due_date,
                               amount_cents=d.amount_cents, category_id=d.category_id, number=d.number,
                               memo=d.memo, control_id=target or d.control_id)
        elif it.tx_id is not None:
            parties.set_name(books, [it.tx_id], c.name)
            if target:
                books.set_category(it.tx_id, target, source="manual", reviewed=True)
        else:
            set_party(books, it.key, c.name)
        done += 1
    return done
