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


@pytest.fixture(scope="session")
def ui_app():
    """The one and only Tk interpreter for the test session."""
    try:
        from fbposter.ui.app import App
        from fbposter.ui.connection import ConnectionResult, ConnectionState

        application = App(check_fn=lambda: ConnectionResult(ConnectionState.UNKNOWN, ""))
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
