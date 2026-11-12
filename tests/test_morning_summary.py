"""The morning summary email: figures from the company files and the portfolio, sent from your own account."""

from datetime import date, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from am_accounting import morning_summary as ms
from am_accounting import personal as pers
from am_accounting import portfolio as pfm
from am_accounting.books import Books
from am_accounting.books import ParsedTransaction

TODAY = date(2026, 11, 12)


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


class FakeSMTP:
    sent: list = []

    def __init__(self, s):
        self.s, self.logins = s, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def login(self, user, pw):
        self.logins.append((user, pw))
        FakeSMTP.last_login = (user, pw)

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


def _setup(tmp_path):
    co = tmp_path / "Maple.amdb"
    b = Books.open(co, create=True)
    bank = b.account_by_code("1000").id
    b.import_transactions(bank, [ParsedTransaction(date(2026, 10, 30), "OPENING DEPOSIT", 50_000_00),
                                 ParsedTransaction(date(2026, 11, 3), "RENT", 8_000_00),
                                 ParsedTransaction(TODAY - timedelta(days=1), "HYDRO", -1_200_00)])
    b.close()
    pf = pfm.Portfolio(tmp_path / "portfolio.ampf")
    h = pfm.Holding("Maple Court", "Real estate", ownership_pct=100)
    pf.save_holding(h)
    pf.add_valuation(h.id, pfm.Valuation("2026-06-30", 1_000_000_00, "Appraisal", company_value_cents=3_000_000_00,
                                         debt_cents=2_000_000_00))
    pers.save(pf, pers.Saved("Pat", [pers.Entry("Cash and bank accounts", "", 40_000_00)]))
    pf.close()
    return [("Maple Court Ltd", str(co))], tmp_path / "portfolio.ampf"


def test_figures_and_email(tmp_path):
    cos, port = _setup(tmp_path)
    sm = ms.build(cos, port, ms.Settings(), TODAY)
    c = sm.companies[0]
    assert (c.cash, c.change_1d) == (56_800_00, -1_200_00)
    assert (c.in_mtd, c.out_mtd, c.out_1d) == (8_000_00, 1_200_00, 1_200_00)
    assert sm.net_worth == 40_000_00 + 3_000_000_00 - 2_000_000_00
    assert sm.portfolio_nav is not None and sm.spent_mtd == 0
    page = ms.html(sm)
    assert "Company cash" in page and "$56,800.00" in page and "Maple Court Ltd" in page and "Net worth" in page
    assert "cash $56,800.00" in ms.subject(sm)
    only = ms.build(cos, port, ms.Settings(portfolio=False, personal=False), TODAY)
    assert only.net_worth is None and "Personal" not in ms.html(only)


def test_password_kept_encrypted(tmp_path):
    blob = ms.protect("s3cret")
    assert "s3cret" not in blob and ms.unprotect(blob) == "s3cret"
    s = ms.Settings(username="me@example.com", password=blob)
    ms.save(tmp_path, s)
    assert "s3cret" not in (tmp_path / "morning_summary.json").read_text()
    assert ms.load(tmp_path) == s


def test_run_sends_once_a_day(tmp_path):
    cos, port = _setup(tmp_path)
    FakeSMTP.sent = []
    ms.save(tmp_path, ms.Settings(enabled=True, to="me@example.com", username="me@example.com",
                                  password=ms.protect("pw")))
    assert ms.run(tmp_path, cos, port, today=TODAY, smtp_factory=FakeSMTP) == "sent"
    assert ms.run(tmp_path, cos, port, today=TODAY, smtp_factory=FakeSMTP) == "skipped"     # once a day
    assert len(FakeSMTP.sent) == 1 and FakeSMTP.last_login == ("me@example.com", "pw")
    msg = FakeSMTP.sent[0]
    assert msg["To"] == "me@example.com" and "Morning summary" in msg["Subject"]
    assert "$56,800.00" in msg.get_body(("html",)).get_content()
    assert ms.load(tmp_path).last_sent == TODAY.isoformat()
    ms.save(tmp_path, ms.Settings(enabled=False, to="x@example.com", username="x@example.com"))
    assert ms.run(tmp_path, cos, port, today=TODAY + timedelta(days=1), smtp_factory=FakeSMTP) == "skipped"


def test_failure_is_kept(tmp_path):
    cos, port = _setup(tmp_path)

    class Down(FakeSMTP):
        def send_message(self, msg):
            raise OSError("connection refused")

    ms.save(tmp_path, ms.Settings(enabled=True, to="me@example.com", username="me@example.com"))
    with pytest.raises(OSError):
        ms.run(tmp_path, cos, port, today=TODAY, smtp_factory=Down)
    s = ms.load(tmp_path)
    assert "connection refused" in s.last_error and s.last_sent == ""


def test_daily_task(tmp_path):
    calls = []

    class R:
        returncode = 0

    def runner(args):
        calls.append(args)
        return R()

    assert ms.schedule(ms.Settings(enabled=True, time="06:45"), runner=runner) == ""
    assert calls[-1][:5] == ["schtasks", "/Create", "/F", "/TN", ms.TASK_NAME] and "/XML" in calls[-1]
    xml = ms.task_xml("06:45")
    assert "T06:45:00" in xml and "<StartWhenAvailable>true</StartWhenAvailable>" in xml and "--morning-summary" in xml
    ms.schedule(ms.Settings(enabled=False), runner=runner)
    assert calls[-1] == ["schtasks", "/Delete", "/F", "/TN", ms.TASK_NAME]


def test_menu_and_dialog(app, tmp_path):
    from am_accounting.books import CompanyProfile
    from am_accounting.companies import CompanyRegistry
    from am_accounting.gui.main_window import MainWindow

    reg = CompanyRegistry(tmp_path / "companies.json", tmp_path / "Companies")
    w = MainWindow(str(reg.create(CompanyProfile("Alder Court Ltd"))), registry=reg)
    menus = [a.text() for a in w.menuBar().actions()]
    assert "&Summary" in menus and menus.index("&Summary") < menus.index("&Help")
    calls = []

    def run(dlg):
        dlg.scheduler = lambda args: calls.append(args) or type("R", (), {"returncode": 0})()
        dlg.enabled.setChecked(True)
        dlg.to.setText("me@example.com")
        dlg.provider.setCurrentText("Gmail")
        assert dlg.server.text() == "smtp.gmail.com" and not dlg.server.isEnabled() and "app password" in \
            dlg.note.text()
        dlg.username.setText("me@gmail.com")
        dlg.password.setText("app-pw")
        dlg.parts["personal"].setChecked(False)
        FakeSMTP.sent = []
        assert dlg.send_now(smtp_factory=FakeSMTP)
        assert len(FakeSMTP.sent) == 1 and "Alder Court Ltd" in FakeSMTP.sent[0].get_body(("html",)).get_content()
        assert "Last sent" in dlg.status.text()
        prev = dlg.preview()
        assert "Morning summary" in prev.windowTitle()
        prev.close()
        return dlg.save_and_close()

    assert w.morning_summary(run=run)
    s = ms.load(tmp_path)
    assert s.enabled and s.to == "me@example.com" and s.server == "smtp.gmail.com" and not s.personal
    assert ms.unprotect(s.password) == "app-pw" and calls and calls[-1][1] == "/Create"
    w.close()
