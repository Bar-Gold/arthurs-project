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

    def test_it_offers_the_account_switch_when_nothing_is_wrong(self, qt_app):
        """It used to disappear here. That left the wizard unreachable once it
        had been retired, and the only way to post as a different account was
        renaming a folder on disk."""
        deliver(qt_app, ConnectionState.CONNECTED)
        assert not qt_app.fix_button.isHidden()
        assert qt_app.fix_button.text() == onboarding.SWITCH_ACTION

    def test_it_offers_nothing_before_the_first_check(self, qt_app):
        """Nothing has been established, so there is nothing to offer."""
        assert qt_app.fix_button.isHidden()

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


class StubWorker:
    """Enough of a worker for the one question the switch asks of it.

    `stop` is here because the fixture closes every window it builds, and
    closeEvent stops the worker on the way out -- a stub without it fails the
    test that installed it, from teardown, several frames away from the cause.
    """

    paused = False

    def __init__(self, state: str = "idle") -> None:
        self.state = state
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class TestSwitchingFacebookAccount:
    """A client who wants to post as somebody else keeps everything else. The
    groups, templates, repeating posts and history live in the database and
    none of it is tied to an account -- so switching accounts is ending one
    Facebook session and starting another, and nothing more."""

    def view(self, app):
        return app.views["welcome"]

    def ready(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.CONNECTED)
        view = self.view(qt_app)
        view.refresh(force=True)
        assert view.step is SetupStep.READY
        return view

    def recorder(self, qt_app, monkeypatch):
        sent = []
        monkeypatch.setattr(
            qt_app, "run_in_background",
            lambda fn, ok=None, err=None: sent.append(fn),
        )
        return sent

    # -- where it is offered ----------------------------------------------
    def test_it_is_offered_only_when_there_is_a_session_to_end(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.LOGGED_OUT)
        view = self.view(qt_app)
        view.refresh(force=True)
        assert view.switch_button.isHidden()

        deliver(qt_app, ConnectionState.CONNECTED)
        view.refresh(force=True)
        assert not view.switch_button.isHidden()

    def test_the_pill_button_leads_here(self, qt_app, monkeypatch):
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        deliver(qt_app, ConnectionState.CONNECTED)
        qt_app.fix_button.click()
        assert qt_app.current_view == "welcome"

    # -- asking first ------------------------------------------------------
    def test_the_first_press_only_asks(self, qt_app, monkeypatch):
        """The button sits beside the connection light on every screen. One
        press away from signing the account out is too close to Check
        connection."""
        view = self.ready(qt_app, monkeypatch)
        sent = self.recorder(qt_app, monkeypatch)
        view.begin_switch()
        assert sent == []
        assert not view.confirm_button.isHidden()
        assert not view.cancel_button.isHidden()
        assert view.switch_button.isHidden()

    def test_the_question_carries_the_warning(self, qt_app, monkeypatch):
        view = self.ready(qt_app, monkeypatch)
        view.begin_switch()
        assert view.step_detail.text() == onboarding.SWITCH_WARNING

    def test_the_card_asks_rather_than_congratulating(self, qt_app, monkeypatch):
        """Left on "You are all set", the card praised and warned in the same
        breath and the two buttons under it answered nothing."""
        view = self.ready(qt_app, monkeypatch)
        view.begin_switch()
        assert view.step_headline.text() == onboarding.SWITCH_HEADLINE
        assert view.step_headline.text().endswith("?")

    def test_the_card_goes_back_to_congratulating_on_cancel(self, qt_app, monkeypatch):
        view = self.ready(qt_app, monkeypatch)
        view.begin_switch()
        view.cancel_switch()
        expected = onboarding.guidance(SetupStep.READY).headline
        assert view.step_headline.text() == expected

    def test_cancelling_puts_the_screen_back(self, qt_app, monkeypatch):
        view = self.ready(qt_app, monkeypatch)
        sent = self.recorder(qt_app, monkeypatch)
        view.begin_switch()
        view.cancel_switch()
        assert sent == []
        assert view.confirm_button.isHidden()
        assert not view.switch_button.isHidden()
        assert view.step_detail.text() == onboarding.guidance(SetupStep.READY).detail

    def test_it_is_not_a_dialog(self, qt_app):
        """No modal dialogs still holds. The three that are allowed are the
        file picker, the close warning and the single-instance refusal, and
        this is not one of them."""
        from pathlib import Path

        import fbposter.qtui.views.welcome as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "QMessageBox" not in source

    def test_the_question_goes_away_if_the_answer_changes(self, qt_app, monkeypatch):
        """Chrome closing mid-question leaves nothing to sign out of."""
        view = self.ready(qt_app, monkeypatch)
        view.begin_switch()
        deliver(qt_app, ConnectionState.CHROME_DOWN)
        view.refresh()
        assert view._confirming is False
        assert view.confirm_button.isHidden()

    # -- doing it ----------------------------------------------------------
    def test_confirming_signs_out_off_the_drawing_thread(self, qt_app, monkeypatch):
        """Clearing the cookies is a CDP round trip and the page load after it
        is a real one. On the UI thread that is a frozen window."""
        view = self.ready(qt_app, monkeypatch)
        sent = self.recorder(qt_app, monkeypatch)
        view.begin_switch()
        view.confirm_switch()
        assert sent == [login.switch_account]

    def test_it_refuses_while_a_post_is_going_out(self, qt_app, monkeypatch):
        """Dropping the cookies mid-post fails that post, and the batch then
        halts on a verification that could never have succeeded."""
        view = self.ready(qt_app, monkeypatch)
        qt_app.worker = StubWorker("posting")
        said = []
        monkeypatch.setattr(qt_app, "toast", lambda m, level="info": said.append((m, level)))
        sent = self.recorder(qt_app, monkeypatch)

        view.begin_switch()

        assert sent == []
        assert view._confirming is False
        assert said == [(onboarding.SWITCH_BUSY, "warning")]

    def test_an_idle_worker_does_not_block_it(self, qt_app, monkeypatch):
        view = self.ready(qt_app, monkeypatch)
        qt_app.worker = StubWorker("idle")
        view.begin_switch()
        assert view._confirming is True

    def test_the_app_is_told_the_session_has_gone(self, qt_app, monkeypatch):
        """Waiting for a check to come back and say so would leave the light
        green and this screen congratulating the user while a login form was
        already in front of them."""
        view = self.ready(qt_app, monkeypatch)
        self.recorder(qt_app, monkeypatch)
        view.begin_switch()
        view.confirm_switch()
        view._on_done(None)
        assert qt_app.connection_result.state is ConnectionState.LOGGED_OUT
        assert view.step is SetupStep.LOGGED_OUT

    def test_the_new_login_is_explained(self, qt_app, monkeypatch):
        view = self.ready(qt_app, monkeypatch)
        self.recorder(qt_app, monkeypatch)
        said = []
        monkeypatch.setattr(qt_app, "toast", lambda m, level="info": said.append(m))
        view.begin_switch()
        view.confirm_switch()
        view._on_done(None)
        assert any("log into facebook" in message.lower() for message in said)

    # -- the window it happens in -----------------------------------------
    def test_the_window_is_not_parked_while_they_are_typing(self, qt_app, monkeypatch):
        """The trap this flow sets for itself: at the moment the sign-out
        lands the app still believes it is connected, and the READY branch
        would park the very window the login form is about to appear in."""
        view = self.ready(qt_app, monkeypatch)
        sent = self.recorder(qt_app, monkeypatch)
        view.begin_switch()
        view.confirm_switch()
        view._on_done(None)
        assert login.hide_login_window not in sent

    def test_the_window_goes_back_once_the_new_account_is_in(self, qt_app, monkeypatch):
        view = self.ready(qt_app, monkeypatch)
        sent = self.recorder(qt_app, monkeypatch)
        view.begin_switch()
        view.confirm_switch()
        view._on_done(None)
        deliver(qt_app, ConnectionState.CONNECTED)
        view.refresh()
        assert login.hide_login_window in sent

    def test_a_failure_leaves_the_session_where_it_was(self, qt_app, monkeypatch):
        """login.py raises rather than clearing what it could not finish, so
        the app must not start claiming the user is logged out."""
        view = self.ready(qt_app, monkeypatch)
        self.recorder(qt_app, monkeypatch)
        said = []
        monkeypatch.setattr(qt_app, "toast", lambda m, level="info": said.append((m, level)))
        view.begin_switch()
        view.confirm_switch()
        view._on_failed(RuntimeError("Could not sign the current account out"))

        assert view._switching is False
        assert qt_app.connection_result.state is ConnectionState.CONNECTED
        assert said and said[0][1] == "error"

    def test_still_one_accent_button_while_confirming(self, qt_app, monkeypatch):
        """The accent belongs to the next step in the flow. While the question
        is on screen there is no next step -- there is a yes and a no."""
        from PySide6.QtWidgets import QPushButton

        view = self.ready(qt_app, monkeypatch)
        view.begin_switch()
        primaries = [
            b for b in view.findChildren(QPushButton)
            if b.objectName() == "Primary" and not b.isHidden()
        ]
        assert primaries == []
