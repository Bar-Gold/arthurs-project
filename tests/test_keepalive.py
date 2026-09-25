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
        """Read as code, not text: the comments that explain the ban mention it."""
        import ast
        from pathlib import Path

        import fbposter

        banned_calls = {"kill", "terminate", "TerminateProcess"}
        banned_text = ("taskkill", "Stop-Process", "Browser.close")
        offenders = []
        for path in Path(fbposter.__file__).parent.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                    owner = ast.unparse(node.func.value).lower()
                    if name in banned_calls or (name == "close" and "browser" in owner):
                        offenders.append(f"{path.name}:{node.lineno} {ast.unparse(node)}")
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if any(text in node.value for text in banned_text) and len(node.value) < 80:
                        offenders.append(f"{path.name}:{node.lineno} {node.value!r}")
        assert offenders == []
