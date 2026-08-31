"""The first-run wizard: the screen a non-technical user meets first.

Everything here runs offscreen against a throwaway database and a check
function that never opens a browser, like the rest of the Qt suite.
"""

from __future__ import annotations

from fbposter import login, onboarding
from fbposter.onboarding import SetupStep
from fbposter.ui.connection import ConnectionResult, ConnectionState


def build_app(tmp_path, *, setup_complete: bool):
    from fbposter.db import Database
    from fbposter.db.repo import SettingsRepo
    from fbposter.qtui.app import SETUP_COMPLETE_KEY, App

    from .conftest import SilentNamer

    db = Database(tmp_path / "welcome.db")
    if setup_complete:
        SettingsRepo(db).set(SETUP_COMPLETE_KEY, "1")
    return App(
        check_fn=lambda: ConnectionResult(ConnectionState.UNKNOWN, ""),
        db=db,
        group_namer=SilentNamer(),
    )


def deliver(app, state: ConnectionState) -> None:
    """Deliver a connection result the way the background check would."""
    app._on_check_result(ConnectionResult(state, "detail"))


class TestTheWindowOpensWhereTheUserActuallyIs:
    """A client who has just installed this has no groups, no login and no idea
    what a debugging port is. Opening on Compose would show them a text box and
    a Post button that cannot work."""

    def test_a_fresh_install_opens_on_the_wizard(self, qt_application, tmp_path):
        app = build_app(tmp_path, setup_complete=False)
        try:
            assert app.current_view == "welcome"
        finally:
            app.close()

    def test_once_it_has_worked_it_opens_on_compose(self, qt_application, tmp_path):
        app = build_app(tmp_path, setup_complete=True)
        try:
            assert app.current_view == "compose"
        finally:
            app.close()

    def test_only_a_real_connection_retires_the_wizard(self, qt_application, tmp_path):
        """Anything short of CONNECTED and the window must go on opening where
        the fix is."""
        from fbposter.db.repo import SettingsRepo
        from fbposter.qtui.app import SETUP_COMPLETE_KEY

        app = build_app(tmp_path, setup_complete=False)
        try:
            for state in (
                ConnectionState.CHROME_DOWN,
                ConnectionState.LOGGED_OUT,
                ConnectionState.CHECKPOINT,
                ConnectionState.ERROR,
            ):
                deliver(app, state)
                assert SettingsRepo(app.db).get(SETUP_COMPLETE_KEY) != "1", state

            deliver(app, ConnectionState.CONNECTED)
            assert SettingsRepo(app.db).get(SETUP_COMPLETE_KEY) == "1"
        finally:
            app.close()


class TestTheRepairButtonBesideTheLight:
    """The pill used to report "Chrome not running" and leave it at that. The
    button is what turns a diagnosis into something the user can press."""

    def test_it_is_hidden_when_everything_is_fine(self, qt_app):
        deliver(qt_app, ConnectionState.CONNECTED)
        assert not qt_app.fix_button.isVisible()

    def test_it_offers_to_start_chrome(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.CHROME_DOWN)
        expected = onboarding.guidance(SetupStep.CHROME_DOWN).action
        assert qt_app.fix_button.text() == expected

    def test_it_offers_the_login(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        expected = onboarding.guidance(SetupStep.LOGGED_OUT).action
        assert qt_app.fix_button.text() == expected

    def test_it_never_shows_an_empty_label(self, qt_app, monkeypatch):
        """CHROME_MISSING offers no action, so taking the guidance label as-is
        would draw a blank rectangle."""
        monkeypatch.setattr(login, "chrome_installed", lambda: False)
        deliver(qt_app, ConnectionState.ERROR)
        assert qt_app.fix_button.text().strip()

    def test_pressing_it_lands_on_the_wizard(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        qt_app.fix_button.click()
        assert qt_app.current_view == "welcome"


class TestTheWizardItself:
    def view(self, app):
        return app.views["welcome"]

    def test_it_tracks_the_connection(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        assert self.view(qt_app).step is SetupStep.LOGGED_OUT

    def test_a_missing_chrome_hides_the_button(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: False)
        deliver(qt_app, ConnectionState.CHROME_DOWN)
        view = self.view(qt_app)
        view.refresh(force=True)
        assert view.step is SetupStep.CHROME_MISSING
        assert not view.action_button.isVisible()

    def test_when_ready_the_button_leads_into_the_app(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.CONNECTED)
        view = self.view(qt_app)
        view.refresh(force=True)
        assert view.step is SetupStep.READY
        view.do_action()
        assert qt_app.current_view == "compose"

    def test_the_slow_work_never_runs_on_the_drawing_thread(self, qt_app, monkeypatch):
        """Launching Chrome and loading Facebook both block for seconds. On the
        UI thread that is a frozen window, which reads as a crash."""
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.CHROME_DOWN)
        view = self.view(qt_app)
        view.refresh(force=True)

        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        view.do_action()
        assert sent == [login.start_chrome]

    def test_the_login_button_opens_a_visible_window(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        view.do_action()
        assert sent == [login.open_login_window]

    def test_a_failure_is_reported_not_swallowed(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        said = []
        monkeypatch.setattr(
            qt_app, "toast",
            lambda msg, level="info": said.append((msg, level)),
        )
        view._on_failed(RuntimeError("Chrome would not come on screen"))
        assert said and said[0][1] == "error"
        assert "on screen" in said[0][0]

    def test_it_does_not_redraw_when_nothing_changed(self, qt_app, monkeypatch):
        """The same guard every other view uses."""
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        drawn = []
        monkeypatch.setattr(view, "_draw", lambda: drawn.append(1))
        view.refresh()
        assert drawn == []

    def test_it_redraws_when_the_step_changes(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        drawn = []
        monkeypatch.setattr(view, "_draw", lambda: drawn.append(1))
        deliver(qt_app, ConnectionState.CONNECTED)
        view.refresh()
        assert drawn == [1]

    def test_one_accent_button(self, qt_app):
        from PySide6.QtWidgets import QPushButton

        primaries = [
            b for b in self.view(qt_app).findChildren(QPushButton)
            if b.objectName() == "Primary"
        ]
        assert len(primaries) == 1


class TestTheLoginWindowIsPutBack:
    """The whole app depends on that Chrome sitting off-screen at -32000. A
    login window left where it landed would sit over the user's desktop for
    ever, and the next batch would run in a browser they can see."""

    def view(self, app):
        return app.views["welcome"]

    def test_a_successful_login_parks_the_window(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        view.do_action()          # opens the login window
        deliver(qt_app, ConnectionState.CONNECTED)
        view.refresh()
        assert login.hide_login_window in sent

    def test_nothing_is_parked_if_no_window_was_opened(self, qt_app, monkeypatch):
        """Somebody who logged in through another route, or was never logged
        out, has no window of ours on screen to move."""
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        view = self.view(qt_app)

        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        deliver(qt_app, ConnectionState.CONNECTED)
        view.refresh()
        assert login.hide_login_window not in sent

    def test_it_is_not_parked_before_the_login_succeeds(self, qt_app, monkeypatch):
        """Parking it the moment the button is pressed would take the login
        form off screen while they were still typing into it."""
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        view.do_action()
        # Still logged out -- they have not finished yet.
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view.refresh()
        assert login.hide_login_window not in sent

    def test_it_is_only_parked_once(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)

        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        view.do_action()
        deliver(qt_app, ConnectionState.CONNECTED)
        view.refresh()
        view.refresh(force=True)
        assert sent.count(login.hide_login_window) == 1
