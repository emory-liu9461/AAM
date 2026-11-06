"""Light / dark appearance.

The app follows the operating system's light or dark setting by default, or can
be forced either way (View → Appearance). Colours used for meaning (money in /
money out, warnings, links, highlights) come from here so they stay readable on
both backgrounds. Financial statements are always shown on a white "paper" page,
like the PDF export.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette

MODES = {"system": "Match System", "light": "Light", "dark": "Dark"}

LIGHT = {
    "green": "#1b7f3b", "red": "#b3261e", "amber": "#9a6700", "muted": "#6b7380", "link": "#2f6fde",
    "select_bg": "#dbe7fb", "select_border": "#2f6fde",
    "match_bg": "#e8f3ec", "match_border": "#b7dcc4", "match_text": "#14532d",
    "attention_bg": "#fff4d6", "attention_border": "#f0d58c", "attention_text": "#5c4300",
}
DARK = {
    "green": "#5fd38a", "red": "#ff8a80", "amber": "#f0c050", "muted": "#9aa4b2", "link": "#7fb2ff",
    "select_bg": "#24364f", "select_border": "#7fb2ff",
    "match_bg": "#173a26", "match_border": "#2e6b45", "match_text": "#c9f0d6",
    "attention_bg": "#3a2f12", "attention_border": "#6b5420", "attention_text": "#f6e3a8",
}

# Report "paper": always light, whatever the app theme.
PAPER_CSS = "QTextBrowser { background: #ffffff; color: #1c2430; border: 1px solid #c9ced6; }"


def _settings() -> QSettings:
    return QSettings("AM Accounting", "AM Accounting")


_MODE: str | None = None  # read from the settings once; set_mode updates it


def mode() -> str:
    global _MODE
    if _MODE is None:
        m = _settings().value("appearance", "system")
        _MODE = m if m in MODES else "system"
    return _MODE


def set_mode(m: str) -> None:
    global _MODE
    _settings().setValue("appearance", m)
    _MODE = m if m in MODES else "system"
    apply()


def is_dark() -> bool:
    m = mode()
    if m != "system":
        return m == "dark"
    app = QGuiApplication.instance()
    if app is None:
        return False
    try:
        scheme = app.styleHints().colorScheme()
        if scheme != Qt.ColorScheme.Unknown:
            return scheme == Qt.ColorScheme.Dark
    except AttributeError:
        pass
    return app.palette().color(QPalette.ColorRole.Window).lightness() < 128


def colors() -> dict[str, str]:
    return DARK if is_dark() else LIGHT


def c(name: str) -> str:
    return colors()[name]


def _dark_palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#1f2329", QPalette.ColorRole.WindowText: "#e6e9ee",
        QPalette.ColorRole.Base: "#272c33", QPalette.ColorRole.AlternateBase: "#2e343c",
        QPalette.ColorRole.Text: "#e6e9ee", QPalette.ColorRole.Button: "#2e343c",
        QPalette.ColorRole.ButtonText: "#e6e9ee", QPalette.ColorRole.ToolTipBase: "#2e343c",
        QPalette.ColorRole.ToolTipText: "#e6e9ee", QPalette.ColorRole.Highlight: "#3d6fc4",
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.Link: DARK["link"],
        QPalette.ColorRole.PlaceholderText: "#8b95a3", QPalette.ColorRole.Mid: "#4a525d",
        QPalette.ColorRole.Midlight: "#3a414a", QPalette.ColorRole.Dark: "#15181c",
        QPalette.ColorRole.BrightText: "#ffffff",
    }
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#7a8390"))
    return p


def _light_palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "#f0f1f3", QPalette.ColorRole.WindowText: "#1c2430",
        QPalette.ColorRole.Base: "#ffffff", QPalette.ColorRole.AlternateBase: "#f5f6f8",
        QPalette.ColorRole.Text: "#1c2430", QPalette.ColorRole.Button: "#f0f1f3",
        QPalette.ColorRole.ButtonText: "#1c2430", QPalette.ColorRole.ToolTipBase: "#ffffdc",
        QPalette.ColorRole.ToolTipText: "#1c2430", QPalette.ColorRole.Highlight: "#2f6fde",
        QPalette.ColorRole.HighlightedText: "#ffffff", QPalette.ColorRole.Link: LIGHT["link"],
        QPalette.ColorRole.PlaceholderText: "#7a8390", QPalette.ColorRole.Mid: "#b8bec7",
        QPalette.ColorRole.Midlight: "#d9dde3", QPalette.ColorRole.Dark: "#9aa1ab",
        QPalette.ColorRole.BrightText: "#ffffff",
    }
    for role, col in roles.items():
        p.setColor(role, QColor(col))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#9aa1ab"))
    return p


def apply() -> None:
    """Apply the chosen appearance to the whole app and update the shared meaning colours."""
    app = QGuiApplication.instance()
    if app is None:
        return
    m = mode()
    try:  # Qt 6.8+: tell the style which scheme to use
        app.styleHints().setColorScheme({"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}
                                        .get(m, Qt.ColorScheme.Unknown))
    except AttributeError:
        pass
    if m == "dark":
        app.setPalette(_dark_palette())
    elif m == "light":
        app.setPalette(_light_palette())
    else:
        app.setPalette(_dark_palette() if is_dark() else _light_palette())
    from . import common

    for qc, name in ((common.POSITIVE, "green"), (common.NEGATIVE, "red"), (common.MUTED, "muted")):
        qc.setRgb(QColor(c(name)).rgb())
    if hasattr(app, "setStyleSheet"):
        app.setStyleSheet(table_css())


def table_css() -> str:
    """Every table in the app looks like the Dashboard: no grid, no frame, no boxed headings or striped rows —
    quiet grey column titles, a little room around each figure, a soft shade for the selected row."""
    dark = is_dark()
    sel = "rgba(127, 178, 255, 0.22)" if dark else "rgba(47, 111, 222, 0.13)"
    return f"""
QTableView, QTableWidget {{
    border: none;
    gridline-color: transparent;
    background: palette(base);
    alternate-background-color: palette(base);
    selection-background-color: {sel};
    selection-color: palette(text);
}}
QTableView::item {{ border: none; padding: 2px 8px; }}
QTableView::item:selected {{ background: {sel}; color: palette(text); }}
QHeaderView {{ border: none; background: transparent; }}
QHeaderView::section {{
    border: none;
    background: transparent;
    color: {c("muted")};
    padding: 4px 8px;
}}
QTableCornerButton::section {{ border: none; background: transparent; }}
"""
