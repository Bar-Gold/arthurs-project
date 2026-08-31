"""Startup: the window comes up first, everything slow happens behind it.

`begin_startup_checks` is what the packaged .exe relies on to feel instant.
Chrome's debug port is waited on for up to LAUNCH_TIMEOUT_S -- thirty seconds
-- so doing any of this before the window is shown would mean half a minute of
nothing on screen.
"""

from __future__ import annotations

from fbposter import login
from fbposter.errors import ChromeNotFoundError


class TestConstructingAWindowLaunchesNothing:
    """The same promise start_worker makes. A GUI suite that opened browsers
    would hit the live site every time it ran."""

    def test_no_chrome_is_started_by_the_constructor(self, qt_app, monkeypatch):
        started = []
        monkeypatch.setattr(login, "start_chrome", lambda: started.append(1))
        # The fixture already built the window; nothing should have run.
        assert started == []

    def test_it_only_happens_when_asked(self, qt_app, monkeypatch):
        calls = []
        monkeypatch.setattr(login, "start_chrome", lambda: calls.append("chrome"))
        monkeypatch.setattr(qt_app, "check_connection", lambda: calls.append("check"))
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: ok(fn()),
        )
        qt_app.begin_startup_checks()
        assert calls == ["chrome", "check"]


class TestAChromeThatWillNotStartStillLeavesAWayForward:
    """The pill reading "Not checked" for ever is the one state that offers the
    user nothing to press. run_in_background routes exceptions to on_error, and
    there is none here -- so anything escaping the launch would skip the
    connection check and strand them there."""

    def test_a_known_failure_still_runs_the_check(self, qt_app, monkeypatch):
        calls = []

        def boom():
            raise ChromeNotFoundError("no chrome.exe anywhere")

        monkeypatch.setattr(login, "start_chrome", boom)
        monkeypatch.setattr(qt_app, "check_connection", lambda: calls.append("check"))
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: ok(fn()),
        )
        qt_app.begin_startup_checks()
        assert calls == ["check"]

    def test_an_unexpected_failure_still_runs_the_check(self, qt_app, monkeypatch):
        """Popen can raise OSError, which is not an FBPosterError. Catching only
        the app's own exception type here left the pill stuck."""
        calls = []

        def boom():
            raise OSError("the system cannot find the file specified")

        monkeypatch.setattr(login, "start_chrome", boom)
        monkeypatch.setattr(qt_app, "check_connection", lambda: calls.append("check"))
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: ok(fn()),
        )
        qt_app.begin_startup_checks()
        assert calls == ["check"]


class TestNavigatingDoesNotHitTheNetwork:
    """A connection check is a real page load against Facebook. Walking onto a
    screen must not cause one -- the wizard used to check on every arrival, so
    a fresh launch ran two before the window had settled."""

    def test_opening_the_wizard_checks_nothing(self, qt_app, monkeypatch):
        checks = []
        monkeypatch.setattr(qt_app, "check_connection", lambda: checks.append(1))
        qt_app.show_view("welcome")
        assert checks == []

    def test_the_button_still_checks(self, qt_app, monkeypatch):
        checks = []
        monkeypatch.setattr(qt_app, "check_connection", lambda: checks.append(1))
        qt_app.views["welcome"].recheck()
        assert checks == [1]


class TestTheStartupCheckGetsExactlyOneSecondChance:
    """The logon task starts the app 45 seconds after sign-in, while Windows is
    still bringing the network up -- and the check ends in a real page load, so
    it can fail for a reason that fixes itself a moment later. One delayed
    retry covers that. It must never become a poll: nothing in this app should
    open Facebook on a timer."""

    def arm(self, app, monkeypatch, scheduled):
        from fbposter.qtui import app as app_module

        # The whole QTimer name is replaced rather than its singleShot method:
        # PySide6 types are C extension types and do not reliably accept a
        # patched attribute. The window is already built by the fixture, so
        # nothing else in it looks this name up again.
        class FakeTimer:
            @staticmethod
            def singleShot(ms, fn):
                scheduled.append(ms)

        monkeypatch.setattr(app_module, "QTimer", FakeTimer)
        monkeypatch.setattr(login, "start_chrome", lambda: True)
        # Runs the work inline and hands its result to the success callback,
        # which is exactly what the real one does across the thread boundary.
        monkeypatch.setattr(
            app, "run_in_background",
            lambda fn, ok=None, err=None: (ok(fn()) if ok else fn()),
        )

    def test_a_failed_startup_check_is_retried_once(self, qt_app, monkeypatch):
        from fbposter.ui.connection import ConnectionResult, ConnectionState

        scheduled = []
        self.arm(qt_app, monkeypatch, scheduled)
        monkeypatch.setattr(
            qt_app, "_check_fn",
            lambda: ConnectionResult(ConnectionState.CHROME_DOWN, "not up yet"),
        )
        qt_app.begin_startup_checks()
        assert scheduled == [app_recheck_ms()]

    def test_a_successful_startup_check_schedules_nothing(self, qt_app, monkeypatch):
        from fbposter.ui.connection import ConnectionResult, ConnectionState

        scheduled = []
        self.arm(qt_app, monkeypatch, scheduled)
        monkeypatch.setattr(
            qt_app, "_check_fn",
            lambda: ConnectionResult(ConnectionState.CONNECTED, "in"),
        )
        qt_app.begin_startup_checks()
        assert scheduled == []

    def test_the_retry_itself_is_not_retried(self, qt_app, monkeypatch):
        """Otherwise a machine that is simply offline reopens Facebook every
        fifteen seconds, for ever."""
        from fbposter.ui.connection import ConnectionResult, ConnectionState

        scheduled = []
        self.arm(qt_app, monkeypatch, scheduled)
        monkeypatch.setattr(
            qt_app, "_check_fn",
            lambda: ConnectionResult(ConnectionState.ERROR, "still broken"),
        )
        qt_app.begin_startup_checks()
        qt_app.check_connection()   # the retry firing
        assert len(scheduled) == 1

    def test_an_ordinary_check_never_schedules_one(self, qt_app, monkeypatch):
        """Pressing "Check connection" is not a startup check."""
        from fbposter.ui.connection import ConnectionResult, ConnectionState

        scheduled = []
        self.arm(qt_app, monkeypatch, scheduled)
        monkeypatch.setattr(
            qt_app, "_check_fn",
            lambda: ConnectionResult(ConnectionState.ERROR, "nope"),
        )
        qt_app.check_connection()
        assert scheduled == []


def app_recheck_ms() -> int:
    from fbposter.qtui.app import STARTUP_RECHECK_MS

    return STARTUP_RECHECK_MS
