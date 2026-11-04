from pathlib import Path

import pytest

from am_accounting.books import Books

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def books():
    b = Books.open(":memory:")
    yield b
    b.close()


@pytest.fixture
def fixtures():
    return FIXTURES


@pytest.fixture(autouse=True)
def _tidy_qt_objects():
    """After each test, free the windows and dialogs it made — here, on the GUI thread. Otherwise Python's garbage
    collector may free them later from a background thread (the statement-inbox scanner), which crashes Qt."""
    yield
    import gc

    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        if not w.isVisible():
            stop = getattr(w, "shutdown_services", None)
            if stop:
                try:
                    stop()
                except Exception:  # noqa: BLE001
                    pass
            w.deleteLater()
    app.processEvents()
    gc.collect()


def pytest_sessionfinish(session, exitstatus):
    """Close every window and stop background threads so the process exits cleanly (Windows CI)."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:  # noqa: BLE001
        return
    app = QApplication.instance()
    if app is None:
        return
    for w in list(app.topLevelWidgets()):
        stop = getattr(w, "shutdown_services", None)
        if stop:
            try:
                stop()
            except Exception:  # noqa: BLE001
                pass
        w.close()
        w.deleteLater()
    app.processEvents()
