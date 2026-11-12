"""The morning summary email: each company's cash, the portfolio, and your personal finances — worked out on this
computer from the company files and the portfolio, and sent from your own email account.

Settings (Summary → Morning Email Summary…) are kept in ``morning_summary.json`` next to the company list; the
password is encrypted for your Windows account (DPAPI), never stored as plain text. On Windows the app adds a daily
task to Task Scheduler that runs ``Aedalus Nexus --morning-summary`` at the time you pick — the app doesn't need to be
open, and if the computer was off it runs when it's next on. It sends once a day.
"""

from __future__ import annotations

import base64
import html as _html
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .money import fmt_money

TASK_NAME = "Aedalus Nexus Morning Summary"
PROVIDERS = {  # name: (server, port, security)
    "Outlook / Microsoft 365": ("smtp.office365.com", 587, "STARTTLS"),
    "Outlook.com / Hotmail": ("smtp-mail.outlook.com", 587, "STARTTLS"),
    "Gmail": ("smtp.gmail.com", 587, "STARTTLS"),
    "Other": ("", 587, "STARTTLS"),
}
SECURITY = ["STARTTLS", "SSL", "None"]


@dataclass
class Settings:
    enabled: bool = False
    time: str = "07:30"
    to: str = ""
    provider: str = "Outlook / Microsoft 365"
    server: str = "smtp.office365.com"
    port: int = 587
    security: str = "STARTTLS"
    username: str = ""                # your email address (sign-in)
    password: str = ""                # encrypted (see protect)
    companies: bool = True
    portfolio: bool = True
    personal: bool = True
    last_sent: str = ""               # YYYY-MM-DD
    last_error: str = ""


def settings_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / "morning_summary.json"


def load(data_dir: str | Path) -> Settings:
    try:
        d = json.loads(settings_path(data_dir).read_text(encoding="utf-8"))
        return Settings(**{k: v for k, v in d.items() if k in Settings.__dataclass_fields__})
    except (OSError, ValueError, TypeError):
        return Settings()


def save(data_dir: str | Path, s: Settings) -> None:
    p = settings_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(s), indent=2), encoding="utf-8")


# ---- the password: encrypted for this Windows account ----

def protect(secret: str) -> str:
    if not secret:
        return ""
    if sys.platform == "win32":
        return "dpapi:" + base64.b64encode(_dpapi(secret.encode("utf-8"), encrypt=True)).decode()
    return "b64:" + base64.b64encode(secret.encode("utf-8")).decode()     # (not Windows: tests and development)


def unprotect(blob: str) -> str:
    if not blob:
        return ""
    kind, _, data = blob.partition(":")
    raw = base64.b64decode(data)
    if kind == "dpapi":
        return _dpapi(raw, encrypt=False).decode("utf-8")
    return raw.decode("utf-8")


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if encrypt else ctypes.windll.crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("Windows couldn't " + ("encrypt" if encrypt else "decrypt") + " the email password")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


# ---- the figures ----

@dataclass
class CompanyCash:
    name: str
    cash: int = 0
    change_1d: int = 0
    in_mtd: int = 0
    out_mtd: int = 0
    in_1d: int = 0
    out_1d: int = 0
    unpaid_bills: int = 0
    bills_overdue: int = 0
    owed_to_you: int = 0
    error: str = ""


def company_cash(path: str | Path, name: str, today: date) -> CompanyCash:
    from . import receivables as rcv
    from .books import Books

    row = CompanyCash(name)
    try:
        b = Books.open(path, create=False)
    except Exception as e:  # noqa: BLE001
        row.error = f"couldn't open ({e})"
        return row
    try:
        banks = [a.id for a in b.bank_accounts()]
        y = (today - timedelta(days=1)).isoformat()
        first = today.replace(day=1).isoformat()
        row.cash = sum(b.account_balance(i, as_of=today.isoformat()) for i in banks)
        before = (today - timedelta(days=2)).isoformat()      # since yesterday morning: yesterday's activity too
        row.change_1d = row.cash - sum(b.account_balance(i, as_of=before) for i in banks)
        if banks:
            q = ",".join("?" * len(banks))

            def flows(start: str, end: str) -> tuple[int, int]:
                r = b.conn.execute(f"SELECT COALESCE(SUM(l.debit_cents), 0), COALESCE(SUM(l.credit_cents), 0) "
                                   f"FROM journal_lines l JOIN journal_entries e ON e.id = l.entry_id "
                                   f"WHERE l.account_id IN ({q}) AND e.date BETWEEN ? AND ?",
                                   [*banks, start, end]).fetchone()
                return r[0], r[1]
            row.in_mtd, row.out_mtd = flows(first, today.isoformat())
            row.in_1d, row.out_1d = flows(y, y)
        try:
            ap = rcv.aging(b, "bill", today)
            row.unpaid_bills, row.bills_overdue = ap.open_total, ap.overdue_total
            row.owed_to_you = rcv.aging(b, "invoice", today).open_total
        except Exception:  # noqa: BLE001 — no bills / invoices kept: nothing to show
            pass
    finally:
        b.close()
    return row


@dataclass
class Summary:
    today: date
    companies: list[CompanyCash] = field(default_factory=list)
    attention: list[tuple[str, str]] = field(default_factory=list)     # (company, issue)
    portfolio_nav: int | None = None
    net_worth: int | None = None
    net_worth_change: int | None = None
    since: str = ""
    spent_mtd: int | None = None
    budget_month: int | None = None
    income_mtd: int | None = None
    business_mtd: int | None = None

    @property
    def total_cash(self) -> int:
        return sum(c.cash for c in self.companies)


def build(companies: list[tuple[str, str]], portfolio_path: str | Path | None, s: Settings,
          today: date | None = None) -> Summary:
    """``companies``: [(name, company file)]."""
    today = today or date.today()
    out = Summary(today)
    if s.companies:
        for name, path in companies:
            if path and Path(path).exists():
                out.companies.append(company_cash(path, name, today))
    pf = None
    if (s.portfolio or s.personal) and portfolio_path and Path(portfolio_path).exists():
        from . import portfolio as pfm

        pf = pfm.Portfolio(portfolio_path)
    try:
        if pf is not None and s.portfolio:
            from . import health
            from . import portfolio as pfm

            try:
                out.portfolio_nav = pfm.summary(pf, today, include_exited=False).nav
            except Exception:  # noqa: BLE001
                pass
            for h in pf.holdings():
                if h.status == "active":
                    out.attention += [(h.name, i.text) for i in health.company_issues(pf, h, today)]
        if pf is not None and s.personal:
            from . import personal as pers
            from . import personal_finance as pfi

            st = pers.statement(pf, as_of=today)
            out.net_worth = st.net_worth
            earlier = [x for x in pers.history(pf) if x.date < today.isoformat()]
            if earlier:
                out.net_worth_change, out.since = st.net_worth - earlier[-1].net_worth, earlier[-1].date
            bud = pfi.budget(pf, today.year)
            out.spent_mtd = bud.total(pfi.EXPENSE, today.month)
            out.budget_month = bud.budget_total(pfi.EXPENSE)
            out.income_mtd = bud.total(pfi.INCOME, today.month)
            out.business_mtd = pfi.business_income(pf, today.year).by_month().get(today.month, 0)
    finally:
        if pf is not None:
            pf.close()
    return out


def _signed(c: int) -> str:
    return ("+" if c > 0 else "") + fmt_money(c)


def subject(sm: Summary) -> str:
    bits = []
    if sm.companies:
        bits.append(f"cash {fmt_money(sm.total_cash)}")
    if sm.net_worth is not None:
        bits.append(f"net worth {fmt_money(sm.net_worth)}")
    return f"Morning summary — {sm.today:%a %b} {sm.today.day}" + (f": {', '.join(bits)}" if bits else "")


def html(sm: Summary) -> str:
    esc = _html.escape
    td = "padding:4px 10px;border-bottom:1px solid #e3e3e3"
    num = td + ";text-align:right;white-space:nowrap"
    out = [f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:14px;color:#222'>",
           f"<h2 style='margin:0 0 4px 0'>Good morning</h2><div style='color:#666'>{sm.today:%A, %B} {sm.today.day}, "
           f"{sm.today.year}</div>"]
    if sm.companies:
        out.append("<h3 style='margin:18px 0 6px 0'>Company cash</h3><table cellspacing='0' style='border-collapse:"
                   "collapse'><tr>" + "".join(f"<th style='{td};text-align:{'left' if i == 0 else 'right'}'>{h}</th>"
                                             for i, h in enumerate(["Company", "Cash", "Since yesterday", "In (month)",
                                                                    "Out (month)", "Unpaid bills",
                                                                    "Owed to you"])) + "</tr>")
        for c in sm.companies:
            if c.error:
                out.append(f"<tr><td style='{td}'>{esc(c.name)}</td><td colspan='6' style='{td};color:#b3261e'>"
                           f"{esc(c.error)}</td></tr>")
                continue
            ch = f"<span style='color:{'#1b7f3b' if c.change_1d >= 0 else '#b3261e'}'>{_signed(c.change_1d)}</span>" \
                if c.change_1d else "—"
            bills = fmt_money(c.unpaid_bills) + (f"<br><span style='color:#b3261e'>{fmt_money(c.bills_overdue)} "
                                                 "overdue</span>" if c.bills_overdue else "")
            out.append(f"<tr><td style='{td}'>{esc(c.name)}</td><td style='{num}'><b>{fmt_money(c.cash)}</b></td>"
                       f"<td style='{num}'>{ch}</td><td style='{num}'>{fmt_money(c.in_mtd)}</td>"
                       f"<td style='{num}'>{fmt_money(c.out_mtd)}</td><td style='{num}'>{bills}</td>"
                       f"<td style='{num}'>{fmt_money(c.owed_to_you)}</td></tr>")
        out.append(f"<tr><td style='{td}'><b>Total</b></td><td style='{num}'><b>{fmt_money(sm.total_cash)}</b></td>"
                   f"<td style='{num}'>{_signed(sum(c.change_1d for c in sm.companies))}</td>"
                   f"<td style='{num}'>{fmt_money(sum(c.in_mtd for c in sm.companies))}</td>"
                   f"<td style='{num}'>{fmt_money(sum(c.out_mtd for c in sm.companies))}</td>"
                   f"<td style='{num}'>{fmt_money(sum(c.unpaid_bills for c in sm.companies))}</td>"
                   f"<td style='{num}'>{fmt_money(sum(c.owed_to_you for c in sm.companies))}</td></tr></table>")
    if sm.portfolio_nav is not None or sm.attention:
        out.append("<h3 style='margin:18px 0 6px 0'>Portfolio</h3>")
        if sm.portfolio_nav is not None:
            out.append(f"<div>Net asset value: <b>{fmt_money(sm.portfolio_nav)}</b></div>")
        if sm.attention:
            out.append("<div style='margin-top:6px'>Needs attention:</div><ul style='margin:4px 0'>"
                       + "".join(f"<li><b>{esc(n)}</b> — {esc(t)}</li>" for n, t in sm.attention[:15]) + "</ul>")
            if len(sm.attention) > 15:
                out.append(f"<div style='color:#666'>… and {len(sm.attention) - 15} more</div>")
        else:
            out.append("<div style='color:#1b7f3b'>Nothing needs attention.</div>")
    if sm.net_worth is not None:
        out.append("<h3 style='margin:18px 0 6px 0'>Personal</h3><table cellspacing='0' style='border-collapse:"
                   "collapse'>")
        rows = [("Net worth", fmt_money(sm.net_worth) + (f" ({_signed(sm.net_worth_change)} since {sm.since})"
                                                          if sm.net_worth_change is not None else ""))]
        if sm.spent_mtd is not None:
            left = (sm.budget_month or 0) - sm.spent_mtd
            rows.append(("Spent this month", f"{fmt_money(sm.spent_mtd)}"
                         + (f" of {fmt_money(sm.budget_month)} budget ({fmt_money(left)} "
                            f"{'left' if left >= 0 else 'over'})" if sm.budget_month else "")))
            rows.append(("Income this month", fmt_money(sm.income_mtd or 0)))
            rows.append(("From your businesses this month", fmt_money(sm.business_mtd or 0)))
        out += [f"<tr><td style='{td}'>{a}</td><td style='{num}'><b>{b}</b></td></tr>" for a, b in rows]
        out.append("</table>")
    out.append("<p style='color:#888;font-size:12px;margin-top:22px'>Sent by Aedalus Nexus from your computer. Change "
               "or turn it off in Summary → Morning Email Summary.</p></div>")
    return "".join(out)


def text(sm: Summary) -> str:
    lines = [subject(sm), ""]
    for c in sm.companies:
        lines.append(f"{c.name}: {c.error or fmt_money(c.cash)}")
    if sm.attention:
        lines += ["", "Needs attention:"] + [f"- {n}: {t}" for n, t in sm.attention[:15]]
    if sm.net_worth is not None:
        lines += ["", f"Net worth: {fmt_money(sm.net_worth)}"]
    return "\n".join(lines)


# ---- sending ----

def send(s: Settings, subj: str, body_html: str, body_text: str, *, smtp_factory=None) -> None:
    import smtplib
    import ssl
    from email.message import EmailMessage

    if not (s.to and s.server and s.username):
        raise ValueError("Fill in the email address, server and where to send it")
    msg = EmailMessage()
    msg["Subject"] = subj
    msg["From"] = s.username
    msg["To"] = s.to
    msg.set_content(body_text)
    msg.add_alternative(body_html, subtype="html")
    ctx = ssl.create_default_context()
    if smtp_factory is not None:
        server = smtp_factory(s)
    elif s.security == "SSL":
        server = smtplib.SMTP_SSL(s.server, s.port, context=ctx, timeout=30)
    else:
        server = smtplib.SMTP(s.server, s.port, timeout=30)
    with server:
        if s.security == "STARTTLS" and smtp_factory is None:
            server.starttls(context=ctx)
        pw = unprotect(s.password)
        if pw:
            server.login(s.username, pw)
        server.send_message(msg)


def run(data_dir: str | Path, companies: list[tuple[str, str]], portfolio_path: str | Path | None, *,
        force: bool = False, today: date | None = None, smtp_factory=None) -> str:
    """Build and send today's summary (once a day unless ``force``). Returns what happened."""
    today = today or date.today()
    s = load(data_dir)
    if not force and (not s.enabled or s.last_sent == today.isoformat()):
        return "skipped"
    sm = build(companies, portfolio_path, s, today)
    try:
        send(s, subject(sm), html(sm), text(sm), smtp_factory=smtp_factory)
    except Exception as e:  # noqa: BLE001
        s.last_error = f"{datetime.now():%Y-%m-%d %H:%M}: {e}"
        save(data_dir, s)
        raise
    s.last_sent, s.last_error = today.isoformat(), ""
    save(data_dir, s)
    return "sent"


# ---- the daily task (Windows Task Scheduler) ----

def command() -> tuple[str, str]:
    """(program, arguments) that run the summary without opening the window."""
    if getattr(sys, "frozen", False):
        return sys.executable, "--morning-summary"
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe), "-m am_accounting --morning-summary"


def task_xml(at: str) -> str:
    prog, args = command()
    start = f"{date.today().isoformat()}T{at}:00"
    e = _html.escape
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Emails the Aedalus Nexus morning summary</Description></RegistrationInfo>
  <Triggers><CalendarTrigger><StartBoundary>{start}</StartBoundary><Enabled>true</Enabled>
    <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger></Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
  </Principal></Principals>
  <Settings><StartWhenAvailable>true</StartWhenAvailable><DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><RunOnlyIfNetworkAvailable>true</RunOnlyIfNetworkAvailable>
    <ExecutionTimeLimit>PT30M</ExecutionTimeLimit><Enabled>true</Enabled></Settings>
  <Actions Context="Author"><Exec><Command>{e(prog)}</Command><Arguments>{e(args)}</Arguments></Exec></Actions>
</Task>
"""


def schedule(s: Settings, *, runner=None) -> str:
    """Add (or remove, when turned off) the daily task. Returns a note; '' when it worked."""
    import subprocess
    import tempfile

    if sys.platform != "win32" and runner is None:
        return "The daily task can only be set up on Windows."
    run_ = runner or (lambda args: subprocess.run(args, capture_output=True, text=True,
                                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))
    if not s.enabled:
        run_(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        return ""
    with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-16") as f:
        f.write(task_xml(s.time))
        path = f.name
    try:
        r = run_(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/XML", path])
    finally:
        Path(path).unlink(missing_ok=True)
    if getattr(r, "returncode", 0):
        return (getattr(r, "stderr", "") or getattr(r, "stdout", "") or "Task Scheduler refused it").strip()
    return ""

