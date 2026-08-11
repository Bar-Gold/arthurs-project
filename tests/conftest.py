"""Shared helpers for the GUI tests.

Tk is unhappy with more than one interpreter in a process, and unhappy again
about creating a root after an earlier one was destroyed -- on Windows both
show up as an intermittent "Can't find a usable init.tcl". So the entire test
session shares exactly one App, and tests take frames inside it rather than
roots of their own.
"""

from __future__ import annotations

import time
from typing import Callable

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
def ui_app(tmp_path_factory):
    """The one and only Tk interpreter for the test session.

    Backed by a throwaway database and a namer that never opens a browser: the
    tests must touch neither C:\\FBAutomation\\fbposter.db nor Facebook.
    """
    try:
        from fbposter.db import Database
        from fbposter.ui.app import App
        from fbposter.ui.connection import ConnectionResult, ConnectionState

        db = Database(tmp_path_factory.mktemp("uidb") / "ui.db")
        application = App(
            check_fn=lambda: ConnectionResult(ConnectionState.UNKNOWN, ""),
            db=db,
            group_namer=SilentNamer(),
        )
    except Exception as exc:  # no display, no Tcl, no GUI tests
        pytest.skip(f"Tk is unavailable here: {exc}")

    application.withdraw()
    application.update()
    yield application
    application.destroy()


def pump_until(widget, predicate: Callable[[], bool], timeout: float = 5.0) -> bool:
    """Run the Tk event loop until predicate() is true or the timeout expires.

    after() callbacks -- which is how background results reach the UI -- only
    fire while the event loop is running, so tests have to pump it by hand
    instead of calling mainloop().
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        widget.update()
        if predicate():
            return True
        time.sleep(0.01)
    return False
