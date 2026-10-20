"""Shared helpers for the GUI tests."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_TEMPLATE: Path | None = None


def fresh_database(path):
    """A new, fully migrated database at `path`.

    Copied from one migrated once per run. Migrating a new file took about
    70ms on Windows, because each of the fifteen migrations is its own
    committed transaction and waits for the disk, and over a thousand tests
    start from an empty database: more than building the test window itself.
    The tests of the migrations themselves still call Database() on a new path.
    """
    global _TEMPLATE
    from fbposter.db import Database

    if _TEMPLATE is None:
        folder = Path(tempfile.mkdtemp(prefix="fbposter-template-"))
        atexit.register(shutil.rmtree, folder, True)
        template = folder / "template.db"
        database = Database(template)
        # Everything into the main file, so a copy of it alone is complete.
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        database.close()
        _TEMPLATE = template
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(_TEMPLATE, path)
    return Database(path)


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

    def info_for(self, urls):
        return {}


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

    theme.activate()
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def qt_app(qt_application, tmp_path):
    """A fresh Qt window on a throwaway database.

    Constructing an App deliberately does not start the posting worker, which
    is the only reason a GUI test can exist at all.
    """
    from fbposter.qtui.app import App
    from fbposter.connection import ConnectionResult, ConnectionState

    db = fresh_database(tmp_path / "qtui.db")
    window = App(
        check_fn=lambda: ConnectionResult(ConnectionState.UNKNOWN, ""),
        db=db,
        group_namer=SilentNamer(),
    )
    yield window
    window.close()
    discard(window)


def discard(window) -> None:
    """Delete a test window's widgets now, rather than leave them alive.

    Closing a window does not free it: every signal connected to a lambda
    holds a reference back to it from the C++ side, where Python's garbage
    collector cannot see. Each window the suite built stayed alive, about 550
    widgets and their timers each, so the event loop of every later test had
    a thousand idle windows' timers to serve, the run grew to 2GB, and the
    process took minutes to close once the tests were done.
    """
    from shiboken6 import delete

    delete(window)
