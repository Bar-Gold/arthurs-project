"""Putting a Chrome window in front of the user, and taking a session away.

Nothing here opens a browser. `session.attach()` is replaced by a fake context
that records what was asked of it, which is enough to pin the two things that
matter and that reading the code cannot settle: that the cookies are dropped
*before* the navigation, and that an ordinary re-login never drops them at all.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from fbposter import chrome, config, login, session, strings
from fbposter.errors import FBPosterError


class FakePage:
    def __init__(self, log: list) -> None:
        self._log = log
        self.closed = False

    def goto(self, url, **kwargs) -> None:
        self._log.append(("goto", url))

    def close(self) -> None:
        self.closed = True
        self._log.append(("close", None))


class FakeCdp:
    def __init__(self, log: list, *, refuse: bool = False) -> None:
        self._log = log
        self._refuse = refuse

    def send(self, method, params=None):
        if self._refuse:
            raise RuntimeError("Chrome would not answer")
        self._log.append((method, params))
        if method == "Browser.getWindowForTarget":
            return {"windowId": 7}
        return {}


class FakeContext:
    """Only the surface `login.py` actually uses."""

    def __init__(self, *, refuse_bounds: bool = False, refuse_clear: bool = False) -> None:
        self.log: list = []
        self.pages: list[FakePage] = []
        self._refuse_bounds = refuse_bounds
        self._refuse_clear = refuse_clear

    def new_page(self) -> FakePage:
        page = FakePage(self.log)
        self.pages.append(page)
        self.log.append(("new_page", None))
        return page

    def new_cdp_session(self, page) -> FakeCdp:
        return FakeCdp(self.log, refuse=self._refuse_bounds)

    def clear_cookies(self) -> None:
        if self._refuse_clear:
            raise RuntimeError("refused")
        self.log.append(("clear_cookies", None))

    # -- what the assertions read -----------------------------------------
    def methods(self) -> list[str]:
        return [name for name, _ in self.log]

    def bounds_sent(self):
        for name, params in self.log:
            if name == "Browser.setWindowBounds":
                return params["bounds"]
        return None


def attaching_to(context, monkeypatch):
    @contextmanager
    def attach(endpoint=None):
        yield context

    monkeypatch.setattr(session, "attach", attach)


@pytest.fixture
def browser(monkeypatch):
    """A Chrome that is already running, and records instead of doing."""
    context = FakeContext()
    attaching_to(context, monkeypatch)
    monkeypatch.setattr(chrome, "is_running", lambda *a, **k: True)
    monkeypatch.setattr(
        chrome, "launch",
        lambda *a, **k: pytest.fail("Chrome was already running; nothing should launch"),
    )
    return context


class TestSwitchingAccountEndsTheSessionFirst:
    """Facebook shows whoever is logged in. Opening its home page without
    ending the session first shows the account being switched away from, and
    leaves the user hunting for a Log out menu in a window the app has just
    told them is ready for somebody else."""

    def test_the_cookies_go_before_the_navigation(self, browser):
        login.switch_account()
        methods = browser.methods()
        assert "clear_cookies" in methods
        assert methods.index("clear_cookies") < methods.index("goto")

    def test_it_lands_on_facebook(self, browser):
        login.switch_account()
        assert ("goto", strings.HOME_URL) in browser.log

    def test_the_window_is_brought_on_screen(self, browser):
        login.switch_account()
        assert browser.bounds_sent() == config.LOGIN_WINDOW_BOUNDS

    def test_the_page_is_left_open(self, browser):
        """It is what they log in through."""
        login.switch_account()
        assert browser.pages and not browser.pages[0].closed


class TestAnOrdinaryReLoginSignsNobodyOut:
    """The two flows share a window and nothing else. A session that has
    expired needs no clearing, and clearing one that has not would take an
    account away from somebody who only pressed Log in."""

    def test_the_login_button_leaves_the_cookies_alone(self, browser):
        login.open_login_window()
        assert "clear_cookies" not in browser.methods()
        assert ("goto", strings.HOME_URL) in browser.log


class TestWhenChromeIsNotRunningYet:
    def start_from_nothing(self, monkeypatch, launched):
        context = FakeContext()
        attaching_to(context, monkeypatch)
        monkeypatch.setattr(chrome, "is_running", lambda *a, **k: False)
        monkeypatch.setattr(
            chrome, "launch",
            lambda profile, visible=False: launched.append(visible) or True,
        )
        return context

    def test_it_launches_one_where_the_user_can_see_it(self, monkeypatch):
        launched: list = []
        context = self.start_from_nothing(monkeypatch, launched)
        login.switch_account()
        assert launched == [True]

    def test_a_window_launched_on_screen_is_not_then_moved(self, monkeypatch):
        context = self.start_from_nothing(monkeypatch, [])
        login.switch_account()
        assert context.bounds_sent() is None

    def test_it_still_clears_before_going_to_facebook(self, monkeypatch):
        context = self.start_from_nothing(monkeypatch, [])
        login.switch_account()
        methods = context.methods()
        assert methods.index("clear_cookies") < methods.index("goto")


class TestNothingIsHalfDone:
    def test_a_window_that_will_not_move_signs_nobody_out(self, monkeypatch):
        """Otherwise the user is looking at their own desktop with the account
        they still think they are using quietly signed out behind it."""
        context = FakeContext(refuse_bounds=True)
        attaching_to(context, monkeypatch)
        monkeypatch.setattr(chrome, "is_running", lambda *a, **k: True)

        with pytest.raises(FBPosterError):
            login.switch_account()

        assert "clear_cookies" not in context.methods()
        assert context.pages[0].closed

    def test_a_failed_sign_out_never_shows_facebook(self, monkeypatch):
        """Landing on the old account's feed after being told you were signed
        out is how somebody posts as the wrong person."""
        context = FakeContext(refuse_clear=True)
        attaching_to(context, monkeypatch)
        monkeypatch.setattr(chrome, "is_running", lambda *a, **k: True)

        with pytest.raises(FBPosterError) as caught:
            login.switch_account()

        assert "goto" not in context.methods()
        assert "sign the current account out" in str(caught.value)

    def test_the_failure_reads_like_something_a_client_can_act_on(self, monkeypatch):
        context = FakeContext(refuse_clear=True)
        attaching_to(context, monkeypatch)
        monkeypatch.setattr(chrome, "is_running", lambda *a, **k: True)

        with pytest.raises(FBPosterError) as caught:
            login.switch_account()

        message = str(caught.value).lower()
        for word in ("main.py", "cookie", "cdp", "terminal", ".venv"):
            assert word not in message


class TestPuttingTheWindowBack:
    def test_it_parks_the_window_off_screen(self, browser):
        login.hide_login_window()
        assert browser.bounds_sent() == config.OFFSCREEN_BOUNDS

    def test_a_failure_is_swallowed(self, monkeypatch):
        """An exception here would undo a login that actually worked."""

        @contextmanager
        def attach(endpoint=None):
            raise RuntimeError("Chrome went away")
            yield  # pragma: no cover

        monkeypatch.setattr(session, "attach", attach)
        login.hide_login_window()  # must not raise
