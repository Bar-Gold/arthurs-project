"""Shared helpers for the GUI tests."""

from __future__ import annotations

import os

import pytest


class SilentNamer:
    """Looks up no group names at all.

    The default for the shared App, and it matters more than it looks: the
    Groups view fetches names on its own whenever it is shown, and
    chrome.probe() succeeds on a developer machine with Chrome running. Without
    this the GUI suite quietly opened real Facebook pages -- which took the run
    from 20 seconds to nearly three minutes and hit the live site dozens of
    times.
    """

    def names_for(self, urls):
        return {}

    def name_for(self, url):
        return ""


@pytest.fixture(scope="session")
def qt_application():
    """The one QApplication for the session, rendering to nothing.

    Offscreen is not just tidiness: this app's central promise is that it never
    takes focus while the user is working, and a suite that popped real windows
    would break that on the developer's own machine every time it ran.

    Qt allows any number of windows but exactly one QApplication, so this is
    session-scoped and the window itself is not.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PySide6.QtWidgets import QApplication

        from fbposter.qtui import theme
    except Exception as exc:  # no Qt, no Qt tests
        pytest.skip(f"PySide6 is unavailable here: {exc}")

    theme.activate(dark=False)
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def qt_app(qt_application, tmp_path):
    """A fresh Qt window on a throwaway database.

    Constructing an App deliberately does not start the posting worker, which
    is the only reason a GUI test can exist at all.
    """
    from fbposter.db import Database
    from fbposter.qtui.app import App
    from fbposter.connection import ConnectionResult, ConnectionState

    db = Database(tmp_path / "qtui.db")
    window = App(
        check_fn=lambda: ConnectionResult(ConnectionState.UNKNOWN, ""),
        db=db,
        group_namer=SilentNamer(),
    )
    yield window
    window.close()
