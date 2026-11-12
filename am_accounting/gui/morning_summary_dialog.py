"""Summary → Morning Email Summary…: when the morning summary goes out, to whom, from which email account, and
what's in it (see morning_summary.py)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTime
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QSpinBox, QTextBrowser, QTimeEdit, QVBoxLayout)

from .. import morning_summary as ms
from .common import error, info


class MorningSummaryDialog(QDialog):
    def __init__(self, data_dir: str | Path, companies, portfolio_path, parent=None, *, scheduler=None):
        """``companies`` () -> [(name, file)]; ``scheduler`` (for tests) runs the Task Scheduler commands."""
        super().__init__(parent)
        self.data_dir, self.companies, self.portfolio_path = Path(data_dir), companies, portfolio_path
        self.scheduler = scheduler
        self.s = ms.load(self.data_dir)
        self.setWindowTitle("Morning Email Summary")
        self.resize(680, 600)
        lay = QVBoxLayout(self)
        intro = QLabel("Each morning, a summary of your companies' cash, the portfolio and your personal finances — "
                       "worked out on this computer and sent from your own email account. The app doesn't need to "
                       "be open: Windows runs it at the time below (or when the computer is next on).")
        intro.setWordWrap(True)
        lay.addWidget(intro)
        self.enabled = QCheckBox("Send me a summary every morning")
        self.enabled.setChecked(self.s.enabled)
        lay.addWidget(self.enabled)
        when = QFormLayout()
        self.time = QTimeEdit(QTime.fromString(self.s.time, "HH:mm"))
        self.time.setDisplayFormat("h:mm AP")
        when.addRow("At:", self.time)
        self.to = QLineEdit(self.s.to)
        self.to.setPlaceholderText("you@example.com (several: separate with commas)")
        when.addRow("Send to:", self.to)
        lay.addLayout(when)
        what = QGroupBox("What's in it")
        wl = QVBoxLayout(what)
        self.parts = {}
        for key, text in (("companies", "Company cash — balance, change since yesterday, money in and out this "
                                        "month, unpaid bills, owed to you"),
                          ("portfolio", "Portfolio — net asset value and what needs attention"),
                          ("personal", "Personal — net worth, spending against budget, income from your businesses")):
            c = QCheckBox(text)
            c.setChecked(getattr(self.s, key))
            wl.addWidget(c)
            self.parts[key] = c
        lay.addWidget(what)
        acct = QGroupBox("Send from")
        outer = QVBoxLayout(acct)
        al = QFormLayout()
        outer.addLayout(al)
        self.provider = QComboBox()
        self.provider.addItems(list(ms.PROVIDERS))
        self.provider.setCurrentText(self.s.provider)
        self.provider.currentTextChanged.connect(self._provider)
        al.addRow("Email:", self.provider)
        self.username = QLineEdit(self.s.username)
        self.username.setPlaceholderText("your email address")
        al.addRow("Your address:", self.username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setPlaceholderText("saved — type to change" if self.s.password else "password or app password")
        al.addRow("Password:", self.password)
        srv = QHBoxLayout()
        self.server = QLineEdit(self.s.server)
        srv.addWidget(self.server, 1)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(self.s.port)
        srv.addWidget(self.port)
        self.security = QComboBox()
        self.security.addItems(ms.SECURITY)
        self.security.setCurrentText(self.s.security)
        srv.addWidget(self.security)
        al.addRow("Server:", srv)
        self.note = QLabel()
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)
        lay.addWidget(acct)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        btns = QHBoxLayout()
        for text, slot in (("Preview", self.preview), ("Send a Test Now", self.send_now)):
            b = QPushButton(text)
            b.clicked.connect(lambda *_, f=slot: f())
            btns.addWidget(b)
        btns.addStretch(1)
        ok = QPushButton("Save")
        ok.setObjectName("primary")
        ok.clicked.connect(lambda *_: self.save_and_close())
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        btns.addWidget(ok)
        btns.addWidget(cancel)
        lay.addLayout(btns)
        self._provider(self.provider.currentText(), first=True)
        self._show_status()

    def _provider(self, name: str, first: bool = False) -> None:
        server, port, sec = ms.PROVIDERS.get(name, ("", 587, "STARTTLS"))
        other = name == "Other"
        if not first or not self.server.text():
            if not other:
                self.server.setText(server)
                self.port.setValue(port)
                self.security.setCurrentText(sec)
        for w in (self.server, self.port, self.security):
            w.setEnabled(other)
        self.note.setText({
            "Gmail": "Gmail needs an app password (Google Account → Security → 2-Step Verification → App passwords), "
                     "not your normal password.",
            "Outlook / Microsoft 365": "Your work or school Microsoft 365 account. If sending is refused, your "
                                       "administrator may need to allow SMTP sending (SMTP AUTH) for your mailbox.",
            "Outlook.com / Hotmail": "Your Outlook.com password, or an app password if you use two-step "
                                     "verification.",
        }.get(name, "Your email provider's outgoing (SMTP) server."))

    def _show_status(self) -> None:
        bits = [f"Last sent {self.s.last_sent}." if self.s.last_sent else "Not sent yet."]
        if self.s.last_error:
            bits.append(f"<span style='color:#b3261e'>Last attempt failed — {self.s.last_error}</span>")
        self.status.setText(" ".join(bits))

    def settings(self) -> ms.Settings:
        s = ms.Settings(**{**self.s.__dict__})
        s.enabled = self.enabled.isChecked()
        s.time = self.time.time().toString("HH:mm")
        s.to = self.to.text().strip()
        s.provider = self.provider.currentText()
        s.server, s.port, s.security = self.server.text().strip(), self.port.value(), self.security.currentText()
        s.username = self.username.text().strip()
        if self.password.text():
            s.password = ms.protect(self.password.text())
        for k, c in self.parts.items():
            setattr(s, k, c.isChecked())
        return s

    def _summary(self, s: ms.Settings):
        return ms.build(self.companies(), self.portfolio_path, s)

    def preview(self) -> QDialog:
        sm = self._summary(self.settings())
        d = QDialog(self)
        d.setWindowTitle(ms.subject(sm))
        d.resize(820, 640)
        v = QVBoxLayout(d)
        b = QTextBrowser()
        b.setHtml(ms.html(sm))
        v.addWidget(b)
        d.show()
        return d

    def send_now(self, *, smtp_factory=None) -> bool:
        s = self.settings()
        ms.save(self.data_dir, s)
        self.s = s
        try:
            ms.run(self.data_dir, self.companies(), self.portfolio_path, force=True, smtp_factory=smtp_factory)
        except Exception as e:  # noqa: BLE001
            self.s = ms.load(self.data_dir)
            self._show_status()
            if smtp_factory is None:
                error(self, "Couldn't send it", str(e))
            return False
        self.s = ms.load(self.data_dir)
        self._show_status()
        if smtp_factory is None:
            info(self, "Sent", f"The summary was sent to {s.to}.")
        return True

    def save_and_close(self) -> bool:
        s = self.settings()
        if s.enabled and not (s.to and s.username and s.server):
            error(self, "Not finished", "Fill in where to send it and the email account to send it from.")
            return False
        ms.save(self.data_dir, s)
        note = ms.schedule(s, runner=self.scheduler)
        if note and s.enabled:
            error(self, "Couldn't set the daily time", f"The settings are saved, but Windows didn't take the daily "
                                                       f"task:\n\n{note}")
        self.accept()
        return True
