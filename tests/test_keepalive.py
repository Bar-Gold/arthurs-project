"""The background Chrome is started again when it closes, without a loop.

Reported as a wish: "if the app is running but the Chrome in the background is
not, detect it and run it -- I don't want the user to have problems with it."
Before this, a closed Chrome was noticed by nothing: the pill kept its last
word and every post waited on a connection until someone pressed Start Chrome.

Two halves, as usual here: `keepalive.ChromeKeeper` decides (pure, instant to
test), and the window does the looking and starting through seams that the
tests replace -- no test here starts a browser or waits a real second.
"""

from __future__ import annotations

import threading
import time

import pytest

from fbposter import chrome
from fbposter.errors import ChromeLaunchError
from fbposter.keepalive import BACKOFF_S, MAX_ATTEMPTS, ChromeKeeper
from fbposter.ui.connection import ConnectionState


class TestTheKeeper:
    def test_the_first_restart_is_immediate(self):
        assert ChromeKeeper().may_restart(now=0)

    def test_a_failure_backs_off(self):
        keeper = ChromeKeeper()
        keeper.failed(now=100)
        assert not keeper.may_restart(now=100 + BACKOFF_S[0] - 1)
        assert keeper.may_restart(now=100 + BACKOFF_S[0])

    def test_the_wait_grows(self):
        keeper = ChromeKeeper()
        keeper.failed(now=0)
        keeper.failed(now=BACKOFF_S[0])
        assert not keeper.may_restart(now=BACKOFF_S[0] + BACKOFF_S[1] - 1)

    def test_it_gives_up_rather_than_loop(self):
        """A Chrome already open on the profile without the debugging port takes
        every launch as "open another window": retrying for ever piles them up."""
        keeper = ChromeKeeper()
        for attempt in range(MAX_ATTEMPTS):
            keeper.failed(now=attempt * 10_000)
        assert keeper.gave_up
        assert not keeper.may_restart(now=10**9)

    def test_seeing_chrome_alive_starts_the_count_afresh(self):
        keeper = ChromeKeeper()
        for attempt in range(MAX_ATTEMPTS):
            keeper.failed(now=attempt)
        keeper.seen_running()
        assert keeper.may_restart(now=0) and not keeper.gave_up

    def test_it_says_when_it_will_try_again(self):
        keeper = ChromeKeeper()
        keeper.failed(now=0)
        assert keeper.wait_text(now=0) == "in a minute"
        keeper.failed(now=0)
        assert keeper.wait_text(now=0) == "in 5 minutes"


class Browser:
    """Stands in for Chrome: up or down, and what happens when started."""

    def __init__(self, up: bool, installed: bool = True, starts: bool = True) -> None:
        self.up = up
        self.installed = installed
        self.starts = starts
        # Holding the profile without answering the port: slow, not gone.
        self.busy = False
        self.launches = 0

    def probe(self):
        return {"Browser": "Chrome"} if self.up else None

    def start(self):
        self.launches += 1
        if not self.starts:
            raise ChromeLaunchError("the port never opened")
        self.up = True
        return True


@pytest.fixture
def watched(qt_app, monkeypatch):
    """A window whose watcher talks to a pretend Chrome, synchronously."""
    browser = Browser(up=False)
    qt_app._chrome_probe = browser.probe
    qt_app._chrome_installed = lambda: browser.installed
    qt_app._chrome_start = browser.start
    qt_app._chrome_profile_busy = lambda: browser.busy
    clock = {"now": 1000.0}
    qt_app._monotonic = lambda: clock["now"]
    monkeypatch.setattr(
        qt_app, "run_in_background",
        lambda fn, ok=None, err=None: _run(fn, ok, err),
    )
    checks = []
    monkeypatch.setattr(qt_app, "check_connection", lambda: checks.append(1))
    return qt_app, browser, clock, checks


def _run(fn, ok, err):
    try:
        result = fn()
    except Exception as exc:  # routed like the real runner
        if err is not None:
            err(exc)
        return
    if ok is not None:
        ok(result)


class TestTheWindowStartsItAgain:
    def test_a_closed_chrome_is_started_again(self, watched):
        app, browser, _clock, _checks = watched
        app.watch_chrome()
        assert browser.launches == 1 and browser.up

    def test_the_user_is_told_and_the_connection_is_rechecked(self, watched):
        app, _browser, _clock, checks = watched
        app.watch_chrome()
        assert "started it again" in app.toast_label.text()
        assert checks == [1]

    def test_a_running_chrome_is_left_alone(self, watched):
        """And no check: a check is a Facebook page load, never on a timer."""
        app, browser, _clock, checks = watched
        browser.up = True
        app.watch_chrome()
        assert browser.launches == 0
        assert checks == []

    def test_nothing_is_started_when_chrome_is_not_installed(self, watched):
        app, browser, _clock, _checks = watched
        browser.installed = False
        app.watch_chrome()
        assert browser.launches == 0

    def test_a_chrome_that_came_back_on_its_own_refreshes_a_stale_pill(self, watched):
        app, browser, _clock, checks = watched
        app._set_connection(ConnectionState.CHROME_DOWN, announce=False)
        browser.up = True
        app.watch_chrome()
        assert browser.launches == 0 and checks == [1]

    def test_one_look_at_a_time(self, watched, monkeypatch):
        """A tick that lands while a restart is still waiting on the port."""
        app, browser, _clock, _checks = watched
        held = []
        monkeypatch.setattr(app, "run_in_background", lambda fn, ok=None, err=None: held.append(1))
        app.watch_chrome()
        app.watch_chrome()
        assert held == [1]


class TestABusyChromeIsNeverStartedOver:
    """Seen live: with the machine running flat out, Chrome was slow to answer
    for a moment, the watcher took it for gone, and the launch that followed
    replaced the app's Chrome outright. Mid-post, that loses the post."""

    def test_a_chrome_holding_the_profile_is_left_alone(self, watched):
        app, browser, _clock, _checks = watched
        browser.busy = True
        for _ in range(3):
            app.watch_chrome()
        assert browser.launches == 0

    def test_the_user_is_told_once_if_it_lasts(self, watched):
        from fbposter.keepalive import BUSY_LOOKS_BEFORE_TELLING

        app, browser, _clock, _checks = watched
        browser.busy = True
        for _ in range(BUSY_LOOKS_BEFORE_TELLING - 1):
            app.watch_chrome()
        assert app.connection_state is not ConnectionState.CHROME_DOWN
        app.watch_chrome()
        assert app.connection_state is ConnectionState.CHROME_DOWN
        assert "not answering" in app.connection_result.detail

    def test_once_it_answers_the_count_starts_again(self, watched):
        app, browser, _clock, _checks = watched
        browser.busy = True
        app.watch_chrome()
        browser.busy, browser.up = False, True
        app.watch_chrome()
        assert app._busy_looks == 0


class TestTheProfileLock:
    """chrome.profile_in_use reads the lock Chrome holds on its profile."""

    def test_no_lock_file_means_no_chrome(self, tmp_path):
        assert chrome.profile_in_use(tmp_path) is False

    def test_a_lock_file_nobody_holds_is_left_over(self, tmp_path):
        (tmp_path / "lockfile").write_bytes(b"")
        assert chrome.profile_in_use(tmp_path) is False

    def test_a_lock_file_held_like_chrome_holds_it(self, tmp_path):
        """Open, delete-on-close, as Chrome's process singleton does."""
        import os

        handle = os.open(tmp_path / "lockfile", os.O_CREAT | os.O_RDWR | os.O_TEMPORARY)
        try:
            assert chrome.profile_in_use(tmp_path) is True
        finally:
            os.close(handle)
        assert chrome.profile_in_use(tmp_path) is False  # gone with its holder

    def test_launch_never_starts_a_second_chrome_on_a_held_profile(self, monkeypatch, tmp_path):
        popened = []
        monkeypatch.setattr(chrome, "is_running", lambda port=None: False)
        monkeypatch.setattr(chrome, "profile_in_use", lambda _dir: True)
        monkeypatch.setattr(chrome.subprocess, "Popen", lambda *a, **k: popened.append(a))
        monkeypatch.setattr(chrome, "wait_for_cdp", lambda port=None, timeout=None: {"Browser": "x"})
        assert chrome.launch(tmp_path, visible=False) is False
        assert popened == []

    def test_one_that_never_answers_is_reported_not_replaced(self, monkeypatch, tmp_path):
        popened = []

        def silent(port=None, timeout=None):
            raise ChromeLaunchError("no port")

        monkeypatch.setattr(chrome, "is_running", lambda port=None: False)
        monkeypatch.setattr(chrome, "profile_in_use", lambda _dir: True)
        monkeypatch.setattr(chrome.subprocess, "Popen", lambda *a, **k: popened.append(a))
        monkeypatch.setattr(chrome, "wait_for_cdp", silent)
        with pytest.raises(ChromeLaunchError):
            chrome.launch(tmp_path, visible=False)
        assert popened == []


class TestWhenItWillNotStart:
    def test_it_backs_off_and_says_when_it_will_retry(self, watched):
        app, browser, clock, _checks = watched
        browser.starts = False
        app.watch_chrome()
        assert browser.launches == 1
        assert app.connection_state is ConnectionState.CHROME_DOWN
        assert "try again in a minute" in app.toast_label.text()

        clock["now"] += 20  # the next tick, well inside the back-off
        app.watch_chrome()
        assert browser.launches == 1

        clock["now"] += BACKOFF_S[0]
        app.watch_chrome()
        assert browser.launches == 2

    def test_it_stops_and_hands_over_to_the_user(self, watched):
        app, browser, clock, _checks = watched
        browser.starts = False
        for _ in range(MAX_ATTEMPTS + 3):
            app.watch_chrome()
            clock["now"] += 10**6
        assert browser.launches == MAX_ATTEMPTS
        assert "Start Chrome" in app.toast_label.text()
        assert "terminal" not in app.toast_label.text().lower()


class TestItOnlyRunsInTheRealApp:
    def test_building_a_window_does_not_start_watching(self, qt_app):
        """The same promise as start_worker: a test's window starts nothing."""
        assert not qt_app._chrome_watch.isActive()

    def test_startup_starts_watching_and_closing_stops_it(self, qt_app, monkeypatch):
        monkeypatch.setattr(qt_app, "run_in_background", lambda fn, ok=None, err=None: None)
        qt_app.begin_startup_checks()
        assert qt_app._chrome_watch.isActive()
        qt_app.close()
        assert not qt_app._chrome_watch.isActive()

    def test_a_failed_start_at_launch_counts_towards_the_limit(self, qt_app, monkeypatch):
        def boom():
            raise ChromeLaunchError("port never opened")

        qt_app._chrome_start = boom
        monkeypatch.setattr(qt_app, "check_connection", lambda: None)
        monkeypatch.setattr(
            qt_app, "run_in_background", lambda fn, ok=None, err=None: _run(fn, ok, err)
        )
        qt_app.begin_startup_checks()
        assert qt_app._keeper.failures == 1


class TestTwoStartsCannotRace:
    def test_only_one_chrome_is_launched(self, monkeypatch, tmp_path):
        """The watcher, the startup check and the wizard's button can all ask at
        once; each saw the port closed and each started a Chrome."""
        up = threading.Event()
        launched = []

        def popen(*_a, **_k):
            # A real launch takes a while to open the port; without the lock,
            # every thread checks inside that window and starts its own.
            launched.append(1)
            time.sleep(0.1)
            up.set()

        monkeypatch.setattr(chrome, "is_running", lambda port=None: up.is_set())
        monkeypatch.setattr(chrome, "find_chrome", lambda: tmp_path / "chrome.exe")
        monkeypatch.setattr(chrome.subprocess, "Popen", popen)
        monkeypatch.setattr(chrome, "wait_for_cdp", lambda port=None: up.wait(1))

        threads = [
            threading.Thread(target=chrome.launch, args=(tmp_path,), kwargs={"visible": False})
            for _ in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert launched == [1]


class TestTheUsersOwnChromeIsNeverTouched:
    """Asked for in so many words: a regular Chrome that is open must not be
    closed, and must not be taken for the app's.

    Both hold by construction, and these pin the construction. The app knows
    its Chrome only by the debugging port, which Chrome 136+ refuses to open on
    the everyday profile -- so a regular Chrome never answers there. It starts
    its own on a profile directory of its own, which Chrome keeps as a separate
    browser. And nothing in it closes or kills a Chrome at all.
    """

    def test_the_app_profile_is_never_chromes_everyday_one(self):
        from fbposter import config

        for path in (config.PREFERRED_PROFILE_DIR, config.FALLBACK_PROFILE_DIR):
            assert "user data" not in str(path).lower() or "google" not in str(path).lower()

    def test_every_launch_names_the_app_profile(self, tmp_path):
        args = chrome.build_args(tmp_path / "chrome.exe", tmp_path / "profile", visible=False)
        assert f"--user-data-dir={tmp_path / 'profile'}" in args

    def test_nothing_in_the_app_closes_or_kills_a_chrome(self):
        """Read as code, not text: the comments that explain the ban mention it.

        One exception, and only one, since 2026-09-27: `tabs.restart_chrome`,
        the last resort for a connection check that has stalled, sends
        `Browser.close` -- to the app's own Chrome, once its process has been
        shown to run on the app's profile (tests/test_tabs.py pins that a
        Chrome on any other profile is never closed). Nothing kills a process,
        anywhere.
        """
        import ast
        from pathlib import Path

        import fbposter

        banned_calls = {"kill", "terminate", "TerminateProcess"}
        banned_text = ("taskkill", "Stop-Process", "Browser.close")
        allowed = {("tabs.py", "restart_chrome", "Browser.close")}
        offenders = []
        for path in Path(fbposter.__file__).parent.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            # Each node's innermost enclosing function. ast.walk is breadth
            # first, so an inner function is reached after its outer one and
            # overwrites it.
            inside = {}
            for function in ast.walk(tree):
                if isinstance(function, ast.FunctionDef):
                    for node in ast.walk(function):
                        inside[node] = function.name
            for node in ast.walk(tree):
                where = inside.get(node, "<module>")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                    owner = ast.unparse(node.func.value).lower()
                    if name in banned_calls or (name == "close" and "browser" in owner):
                        offenders.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    text = next((t for t in banned_text if t in node.value), None)
                    if text and len(node.value) < 80 and (path.name, where, node.value) not in allowed:
                        offenders.append(f"{path.name}:{node.lineno} {where} {node.value!r}")
        assert sorted(set(offenders)) == []

    def test_the_one_close_is_behind_the_proof(self):
        """restart_chrome asks whose Chrome it is before it can reach the close."""
        import inspect

        from fbposter import tabs

        source = inspect.getsource(tabs.restart_chrome)
        assert source.index("_owned_client(") < source.index('"Browser.close"')
        assert "return NOT_OURS" in source


# -- a Chrome whose port answers but whose tabs do not --------------------------
class TestItLooksInsideChromeEveryTwoMinutes:
    """A port that answers is not a Chrome that works. One tab stuck on a
    JavaScript dialog hangs every connection while /json/version answers on,
    which is what left a client's pill on "Checking..." (see tabs.py)."""

    @pytest.fixture
    def swept(self, watched):
        app, browser, clock, checks = watched
        browser.up = True
        sweeps = []

        def clear():
            sweeps.append(clock["now"])
            return app.stuck_tabs_found

        app.stuck_tabs_found = []
        app._clear_stuck_tabs = clear
        return app, clock, checks, sweeps

    def test_the_first_look_at_a_running_chrome_sweeps(self, swept):
        app, _clock, _checks, sweeps = swept
        app.watch_chrome()
        assert len(sweeps) == 1

    def test_not_on_every_tick(self, swept):
        from fbposter.keepalive import TAB_SWEEP_EVERY_S, WATCH_EVERY_S

        app, clock, _checks, sweeps = swept
        app.watch_chrome()
        for _ in range(TAB_SWEEP_EVERY_S // WATCH_EVERY_S - 1):
            clock["now"] += WATCH_EVERY_S
            app.watch_chrome()
        assert len(sweeps) == 1
        clock["now"] += WATCH_EVERY_S
        app.watch_chrome()
        assert len(sweeps) == 2

    def test_nothing_found_says_nothing(self, swept):
        app, _clock, checks, _sweeps = swept
        app.watch_chrome()
        assert app.toast_label.text() == "" and checks == []

    def test_a_closed_tab_is_reported(self, swept):
        app, _clock, _checks, _sweeps = swept
        app.stuck_tabs_found = ["Facebook"]
        app.watch_chrome()
        assert "stopped answering, so the app closed it" in app.toast_label.text()

    def test_a_stale_pill_is_rechecked_once(self, swept):
        app, _clock, checks, _sweeps = swept
        app._set_connection(ConnectionState.ERROR, announce=False)
        app.stuck_tabs_found = ["Facebook"]
        app.watch_chrome()
        assert checks == [1]

    @pytest.mark.parametrize("state", [ConnectionState.CONNECTED, ConnectionState.CHECKING])
    def test_no_check_when_nothing_is_stale(self, swept, state):
        """A check hung on that tab finishes by itself once it is gone."""
        app, _clock, checks, _sweeps = swept
        app._set_connection(state, announce=False)
        app.stuck_tabs_found = ["Facebook"]
        app.watch_chrome()
        assert checks == []

    def test_no_sweep_when_chrome_is_down_or_busy(self, swept):
        app, _clock, _checks, sweeps = swept
        browser = Browser(up=False, starts=False)
        app._chrome_probe = browser.probe
        app._chrome_start = browser.start
        app.watch_chrome()
        browser.busy = True
        app._chrome_profile_busy = lambda: True
        app.watch_chrome()
        assert sweeps == []

    def test_one_at_a_time(self, swept, monkeypatch):
        app, clock, _checks, _sweeps = swept
        held = []

        def runner(fn, ok=None, err=None):
            if fn is app._clear_stuck_tabs:
                held.append(ok)  # the sweep has not come back yet
            else:
                _run(fn, ok, err)

        monkeypatch.setattr(app, "run_in_background", runner)
        app.watch_chrome()
        clock["now"] += 1000
        app.watch_chrome()
        assert len(held) == 1
        held[0]([])
        app.watch_chrome()
        assert len(held) == 2


class Held:
    """Stands in for run_in_background: jobs wait until released, in order."""

    def __init__(self) -> None:
        self.jobs = []

    def __call__(self, fn, ok=None, err=None):
        self.jobs.append((fn, ok, err))

    def release(self):
        fn, ok, err = self.jobs.pop(0)
        _run(fn, ok, err)


class TestACheckCannotHangForever:
    """The check used to have no deadline at all: a connect hung on a stuck
    tab left the pill on "Checking..." until the client closed Chrome by
    hand. Now the app does what they did, least drastic first."""

    @pytest.fixture
    def stalling(self, qt_app, monkeypatch):
        from fbposter import tabs
        from fbposter.ui.connection import ConnectionResult

        runner = Held()
        monkeypatch.setattr(qt_app, "run_in_background", runner)
        qt_app._check_fn = lambda: ConnectionResult(ConnectionState.CONNECTED, "Logged in as 1")
        clock = {"now": 5000.0}
        qt_app._monotonic = lambda: clock["now"]
        qt_app.found = []
        qt_app.restarts = []
        qt_app.restart_says = tabs.RESTARTED
        qt_app._clear_stuck_tabs = lambda: qt_app.found

        def restart():
            qt_app.restarts.append(clock["now"])
            if isinstance(qt_app.restart_says, Exception):
                raise qt_app.restart_says
            return qt_app.restart_says

        qt_app._restart_chrome = restart
        return qt_app, runner, clock

    def stall(self, app, runner):
        """A check that never comes back, then the deadline, then its fix."""
        app.check_connection()
        hung = runner.jobs.pop(0)
        app._on_check_stalled()
        runner.release()  # the fix
        return hung

    def test_a_check_arms_the_deadline_and_its_answer_stops_it(self, stalling):
        from fbposter.qtui.app import CHECK_DEADLINE_MS

        app, runner, _clock = stalling
        app.check_connection()
        assert app._check_deadline.isActive()
        assert app._check_deadline.interval() == CHECK_DEADLINE_MS == 90_000
        runner.release()
        assert not app._check_deadline.isActive()
        assert app.connection_state is ConnectionState.CONNECTED

    def test_the_pill_leaves_checking_and_the_button_comes_back(self, stalling, monkeypatch):
        app, runner, _clock = stalling
        app.check_connection()
        refreshed = []
        monkeypatch.setattr(app.views["welcome"], "refresh", lambda: refreshed.append(1))
        app._on_check_stalled()
        assert app.connection_state is ConnectionState.ERROR
        assert app.check_button.isEnabled()
        assert "stopped answering" in app.connection_result.detail
        assert refreshed, "the setup screen still showed the check in progress"

    def test_a_stuck_tab_is_closed_first_and_nothing_restarted(self, stalling):
        app, runner, _clock = stalling
        app.found = ["Facebook"]
        self.stall(app, runner)
        assert app.restarts == []
        assert "closed it" in app.toast_label.text() or app.connection_state is ConnectionState.CHECKING
        runner.release()  # the fresh check
        assert app.connection_state is ConnectionState.CONNECTED

    def test_with_no_stuck_tab_the_apps_chrome_is_restarted_and_rechecked(self, stalling):
        app, runner, _clock = stalling
        app._keeper.failed(0)
        self.stall(app, runner)
        assert len(app.restarts) == 1
        assert app._keeper.failures == 0
        runner.release()  # the fresh check
        assert app.connection_state is ConnectionState.CONNECTED

    def test_the_answer_of_the_check_given_up_on_is_ignored(self, stalling):
        from fbposter.ui.connection import ConnectionResult

        app, runner, _clock = stalling
        _fn, ok, _err = self.stall(app, runner)
        runner.jobs.clear()  # the fresh check has not answered yet
        ok(ConnectionResult(ConnectionState.LOGGED_OUT, "late"))
        assert app.connection_state is not ConnectionState.LOGGED_OUT

    def test_at_most_one_restart_in_half_an_hour(self, stalling):
        from fbposter.keepalive import STALL_RESTART_EVERY_S

        app, runner, clock = stalling
        self.stall(app, runner)
        runner.jobs.clear()
        app._set_connection(ConnectionState.UNKNOWN, announce=False)
        clock["now"] += STALL_RESTART_EVERY_S - 1
        self.stall(app, runner)
        assert len(app.restarts) == 1
        assert "Close it from the taskbar" in app.connection_result.detail
        runner.jobs.clear()
        app._set_connection(ConnectionState.UNKNOWN, announce=False)
        clock["now"] += 1
        self.stall(app, runner)
        assert len(app.restarts) == 2

    @pytest.mark.parametrize("says, expected", [
        ("posting", "A post is going out"),
        ("not_ours", "left it alone"),
        ("would_not_close", "Close it from the taskbar"),
        ("gone", "starts it again"),
    ])
    def test_every_other_outcome_is_said_and_nothing_is_rechecked(self, stalling, says, expected):
        app, runner, _clock = stalling
        app.restart_says = says
        self.stall(app, runner)
        assert expected in app.connection_result.detail
        assert app.connection_state is ConnectionState.ERROR
        assert runner.jobs == []  # no fresh check queued
        assert app._watching is False

    def test_a_restart_that_fails_is_said(self, stalling):
        from fbposter.errors import ChromeLaunchError

        app, runner, _clock = stalling
        app.restart_says = ChromeLaunchError("port never opened")
        self.stall(app, runner)
        assert "Close it from the taskbar" in app.connection_result.detail
        assert app._watching is False

    def test_the_keep_alive_waits_while_chrome_is_being_restarted(self, stalling):
        app, runner, _clock = stalling
        app.check_connection()
        runner.jobs.pop(0)
        app._on_check_stalled()
        assert app._watching  # the fix is still running
        looked = len(runner.jobs)
        app.watch_chrome()
        assert len(runner.jobs) == looked

    def test_closing_the_window_stops_the_deadline(self, qt_app, monkeypatch):
        monkeypatch.setattr(qt_app, "run_in_background", lambda fn, ok=None, err=None: None)
        qt_app.check_connection()
        assert qt_app._check_deadline.isActive()
        qt_app.close()
        assert not qt_app._check_deadline.isActive()

    def test_a_window_the_suite_builds_looks_inside_no_chrome(self, qt_app):
        from fbposter import tabs

        assert qt_app._clear_stuck_tabs() == []
        assert qt_app._restart_chrome() == tabs.GONE
