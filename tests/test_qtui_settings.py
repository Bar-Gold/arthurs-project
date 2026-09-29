"""The Settings screen: pausing, the posting rules, the account, and this PC.

Pausing used to be a card in the sidebar on every screen, and changing
account a button beside "Check connection". Both moved here so the controls
that stop or undo something are somewhere you go on purpose -- and because a
pause now outlives a restart, the sidebar still says so while one is in force.
"""

from __future__ import annotations

import pytest

from fbposter import login, onboarding
from fbposter.always_on import Status
from fbposter.db.repo import GroupRepo, SettingsRepo, TaskRepo
from fbposter.db.schema import DEFAULT_SETTINGS
from fbposter.qtui.app import FLOW_STEPS, NAV_ITEMS
from fbposter.connection import ConnectionResult, ConnectionState

DEFAULT_COOLDOWN = int(DEFAULT_SETTINGS["default_cooldown_hours"])
DEFAULT_CAP = int(DEFAULT_SETTINGS["daily_cap"])
DEFAULT_START = int(DEFAULT_SETTINGS["posting_window_start_hour"])
DEFAULT_END = int(DEFAULT_SETTINGS["posting_window_end_hour"])


class StubWorker:
    """Enough of a worker for the pump, the sidebar and the Settings card."""

    def __init__(self, state: str = "idle", paused: bool = False) -> None:
        self.state = state
        self.paused = paused
        self.stopped = False

    def pause(self) -> None:
        self.paused = True
        self.state = "paused"

    def resume(self) -> None:
        self.paused = False
        self.state = "idle"

    def stop(self) -> None:
        self.stopped = True


class FakeAlwaysOn:
    """Stands in for always_on.AlwaysOn: records, and changes nothing real."""

    def __init__(self, autostart=False, keep_awake=False, fails=None) -> None:
        self.autostart = autostart
        self.keep_awake = keep_awake
        self.fails = fails
        self.calls: list[tuple[str, bool]] = []

    def status(self) -> Status:
        return Status(self.autostart, self.keep_awake)

    def set_autostart(self, on: bool) -> None:
        self.calls.append(("autostart", on))
        if self.fails:
            raise self.fails
        self.autostart = on

    def set_keep_awake(self, on: bool) -> None:
        self.calls.append(("keep_awake", on))
        if self.fails:
            raise self.fails
        self.keep_awake = on


@pytest.fixture(autouse=True)
def detach_worker(qt_app):
    """Take the worker off before qt_app closes the window.

    With a worker attached and a batch waiting, closeEvent raises the real
    "Posting stops when this closes" dialog -- which does not fail the run,
    it hangs it.
    """
    yield
    qt_app.worker = None


@pytest.fixture
def inline(qt_app, monkeypatch):
    """Run background work on the spot, so a test sees its result at once."""

    def run(fn, on_success=None, on_error=None):
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 - same contract as the real one
            if on_error is not None:
                on_error(exc)
            return
        if on_success is not None:
            on_success(result)

    monkeypatch.setattr(qt_app, "run_in_background", run)
    return qt_app


@pytest.fixture
def settings(qt_app):
    qt_app.show_view("settings")
    return qt_app.views["settings"]


def attach(app, worker):
    app.worker = worker
    app._refresh_scheduler()
    return worker


def connect(app, state=ConnectionState.CONNECTED) -> None:
    app._on_check_result(ConnectionResult(state, "detail"))


class TestItIsNotOnTheMainPath:
    def test_the_sidebar_has_no_pause_button(self, qt_app):
        from PySide6.QtWidgets import QPushButton

        sidebar = qt_app.nav_buttons["compose"].parent()
        labels = [b.text() for b in sidebar.findChildren(QPushButton)]
        assert not any("Pause" in text or "Resume" in text for text in labels), labels

    def test_settings_is_in_the_sidebar_but_not_a_step(self, qt_app):
        assert "settings" in [key for key, _label, _view in NAV_ITEMS]
        assert "settings" not in FLOW_STEPS
        assert qt_app.nav_buttons["settings"].text() == "Settings"

    def test_it_sits_below_the_queue(self, qt_app):
        column = qt_app.nav_buttons["queue"].parent().layout()
        queue = column.indexOf(qt_app.nav_buttons["queue"])
        settings = column.indexOf(qt_app.nav_buttons["settings"])
        assert settings > queue

    def test_it_has_no_filled_button(self, qt_app):
        """The one accent fill on a screen is the next step in the flow, and
        Settings is not in the flow."""
        from PySide6.QtWidgets import QPushButton

        view = qt_app.views["settings"]
        assert [b for b in view.findChildren(QPushButton) if b.objectName() == "Primary"] == []

    def test_it_opens_no_dialog(self):
        """No modal dialogs for any of this, the account switch included."""
        from pathlib import Path

        import fbposter.qtui.views.settings as module

        assert "QMessageBox" not in Path(module.__file__).read_text(encoding="utf-8")


class TestThePausedLine:
    def test_hidden_while_posting_normally(self, qt_app):
        attach(qt_app, StubWorker())
        assert qt_app.paused_notice.isHidden()

    def test_hidden_with_no_worker(self, qt_app):
        qt_app._refresh_scheduler()
        assert qt_app.paused_notice.isHidden()

    def test_shown_while_paused(self, qt_app):
        attach(qt_app, StubWorker("paused", paused=True))
        assert not qt_app.paused_notice.isHidden()

    def test_it_leads_to_settings(self, qt_app):
        attach(qt_app, StubWorker("paused", paused=True))
        qt_app.paused_notice.click()
        assert qt_app.current_view == "settings"

    def test_it_goes_when_posting_resumes(self, qt_app, settings):
        attach(qt_app, StubWorker("paused", paused=True))
        settings.pause_button.click()
        assert qt_app.paused_notice.isHidden()


class TestTheSchedulerCard:
    def test_no_worker_reads_as_not_running_and_cannot_be_paused(self, settings):
        settings.refresh_status()
        assert settings.status_label.text() == "Not running"
        assert not settings.pause_button.isEnabled()

    def test_running(self, qt_app, settings):
        attach(qt_app, StubWorker("idle"))
        assert settings.status_label.text() == "Running"
        assert settings.pause_button.text() == "Pause posting"
        assert settings.pause_button.isEnabled()

    def test_posting(self, qt_app, settings):
        attach(qt_app, StubWorker("posting"))
        assert settings.status_label.text() == "Posting now"
        assert "finish" in settings.status_detail.text()

    def test_paused_says_it_lasts(self, qt_app, settings):
        attach(qt_app, StubWorker("paused", paused=True))
        assert settings.status_label.text() == "Paused"
        assert "restart" in settings.status_detail.text()
        assert settings.pause_button.text() == "Resume posting"

    def test_the_button_pauses_and_resumes(self, qt_app, settings):
        worker = attach(qt_app, StubWorker())
        settings.pause_button.click()
        assert worker.paused
        assert settings.status_label.text() == "Paused"
        settings.pause_button.click()
        assert not worker.paused
        assert settings.status_label.text() == "Running"

    def test_it_says_what_is_waiting(self, qt_app, settings):
        attach(qt_app, StubWorker())
        assert settings.waiting_label.text() == "Nothing is waiting to go out."

        group = GroupRepo(qt_app.db).add_from_url("https://www.facebook.com/groups/one")
        TaskRepo(qt_app.db).create("body", [(group.id, "body")])
        settings.refresh_status()
        assert "1 batch" in settings.waiting_label.text()

    def test_a_real_worker_pause_is_remembered(self, qt_app, settings):
        """End to end through the real worker: the pause is on disk, so the
        next worker the app builds starts paused."""
        from fbposter.worker import PAUSED_KEY, PostingWorker

        attach(qt_app, PostingWorker(qt_app.db, poster=object()))
        settings.pause_button.click()
        assert SettingsRepo(qt_app.db).get(PAUSED_KEY) == "1"
        assert PostingWorker(qt_app.db, poster=object()).paused


class TestThePostingRules:
    def stored(self, app, key, default=0):
        return SettingsRepo(app.db).get_int(key, default)

    def test_it_shows_what_is_stored(self, qt_app):
        SettingsRepo(qt_app.db).set_posting_rules(
            start_hour=9, end_hour=21, daily_cap=12, cooldown_hours=10
        )
        qt_app.show_view("settings")
        view = qt_app.views["settings"]
        assert view.start_entry.value() == 9
        assert view.end_entry.value() == 21
        assert view.cap_entry.value() == 12
        assert view.cooldown_entry.value() == 10

    def test_hours_read_as_a_clock(self, settings):
        assert settings.start_entry.text() == f"{DEFAULT_START:02d}:00"

    def test_save_is_off_until_something_changes(self, settings):
        assert not settings.save_button.isEnabled()
        settings.cap_entry.setValue(DEFAULT_CAP - 5)
        assert settings.save_button.isEnabled()

    def test_saving_stores_all_three(self, qt_app, settings):
        settings.start_entry.setValue(9)
        settings.end_entry.setValue(22)
        settings.cap_entry.setValue(15)
        settings.cooldown_entry.setValue(10)
        settings.save_button.click()

        assert self.stored(qt_app, "posting_window_start_hour") == 9
        assert self.stored(qt_app, "posting_window_end_hour") == 22
        assert self.stored(qt_app, "daily_cap") == 15
        assert self.stored(qt_app, "default_cooldown_hours") == 10
        assert not settings.save_button.isEnabled()
        assert qt_app.toast_label.text() == "Posting rules saved."

    def test_the_cooldown_reaches_every_group(self, qt_app, settings):
        """The worker reads each group's own copy, not the setting."""
        groups = GroupRepo(qt_app.db)
        one = groups.add_from_url("https://www.facebook.com/groups/one")
        two = groups.add_from_url("https://www.facebook.com/groups/two")
        settings.cooldown_entry.setValue(10)
        settings.save_rules()
        assert groups.get(one.id).cooldown_hours == 10
        assert groups.get(two.id).cooldown_hours == 10

    def test_the_same_start_and_end_cannot_be_saved(self, qt_app, settings):
        """The clock reads it as "no posting hours", which would let a batch
        post at 4am."""
        settings.start_entry.setValue(10)
        settings.end_entry.setValue(10)
        assert not settings.save_button.isEnabled()
        assert "same hour" in settings.hours_note.text()

        settings.save_rules()
        assert self.stored(qt_app, "posting_window_start_hour") == DEFAULT_START
        assert self.stored(qt_app, "posting_window_end_hour") == DEFAULT_END

    def test_hours_that_cross_midnight_are_allowed_and_said(self, qt_app, settings):
        settings.start_entry.setValue(22)
        settings.end_entry.setValue(6)
        assert "next morning" in settings.hours_note.text()
        settings.save_rules()
        assert self.stored(qt_app, "posting_window_start_hour") == 22
        assert self.stored(qt_app, "posting_window_end_hour") == 6

    def test_night_hours_are_warned_about(self, settings):
        assert settings.night_note.isHidden()
        settings.start_entry.setValue(22)
        settings.end_entry.setValue(6)
        assert not settings.night_note.isHidden()

    def test_the_hour_goes_round_midnight(self, settings):
        settings.end_entry.setValue(23)
        settings.end_entry.stepBy(1)
        assert settings.end_entry.value() == 0

    def test_the_limits_cannot_reach_nought(self, settings):
        """Nought means "no limit" to the daily cap and "no cooldown" to the
        cooldown: off, for good, for every group."""
        settings.cap_entry.setValue(0)
        settings.cooldown_entry.setValue(0)
        assert settings.cap_entry.value() == 1
        assert settings.cooldown_entry.value() == 1

    def test_a_typed_value_is_read_without_leaving_the_field(self, qt_app, settings):
        """A click does not take focus from the field, so Save must read the
        text itself rather than wait for the field to commit it."""
        settings.cap_entry.lineEdit().setText("7 posts")
        settings.save_rules()
        assert self.stored(qt_app, "daily_cap") == 7

    def test_the_defaults_are_one_click_back(self, qt_app, settings):
        assert settings.defaults_button.isHidden()
        settings.cap_entry.setValue(3)
        settings.cooldown_entry.setValue(3)
        settings.save_rules()
        assert not settings.defaults_button.isHidden()

        settings.defaults_button.click()
        assert self.stored(qt_app, "daily_cap") == DEFAULT_CAP
        assert self.stored(qt_app, "default_cooldown_hours") == DEFAULT_COOLDOWN
        assert settings.defaults_button.isHidden()

    @pytest.mark.parametrize("field", ["start_entry", "end_entry", "cap_entry", "cooldown_entry"])
    def test_the_mouse_wheel_changes_nothing(self, settings, field):
        """Scrolling past a field must not change when or how often posts go out."""
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtWidgets import QApplication

        entry = getattr(settings, field)
        before = entry.value()
        event = QWheelEvent(
            QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        QApplication.sendEvent(entry, event)
        assert entry.value() == before


class TestSwitchingAccount:
    """Moved here from beside the connection light, where it sat one press
    from "Check connection" on every screen. The setup screen still carries
    out the sign-out and the login; this screen asks first."""

    def test_offered_only_while_connected(self, qt_app, settings):
        assert not settings.switch_button.isEnabled()
        assert not settings.account_note.isHidden()
        connect(qt_app)
        assert settings.switch_button.isEnabled()
        assert settings.account_note.isHidden()

    def test_the_first_press_only_asks(self, qt_app, settings, monkeypatch):
        connect(qt_app)
        sent = []
        monkeypatch.setattr(qt_app, "run_in_background", lambda fn, ok=None, err=None: sent.append(fn))
        settings.switch_button.click()
        assert sent == []
        assert not settings.confirm_button.isHidden()
        assert not settings.cancel_button.isHidden()
        assert settings.switch_button.isHidden()

    def test_the_question_carries_the_warning(self, qt_app, settings):
        connect(qt_app)
        settings.begin_switch()
        assert settings.account_heading.text() == onboarding.SWITCH_HEADLINE
        assert settings.account_detail.text() == onboarding.SWITCH_WARNING

    def test_cancelling_puts_it_back(self, qt_app, settings):
        connect(qt_app)
        settings.begin_switch()
        settings.cancel_button.click()
        assert settings.confirm_button.isHidden()
        assert not settings.switch_button.isHidden()
        assert settings.account_detail.text() == onboarding.SWITCH_DETAIL

    def test_the_question_goes_if_the_connection_does(self, qt_app, settings):
        """Chrome closing mid-question leaves nothing to sign out of."""
        connect(qt_app)
        settings.begin_switch()
        connect(qt_app, ConnectionState.CHROME_DOWN)
        assert settings.confirm_button.isHidden()
        assert settings._confirming is False

    def test_it_refuses_while_a_post_is_going_out(self, qt_app, settings, monkeypatch):
        """Dropping the cookies mid-post fails that post, and the batch then
        halts on a verification that could never have succeeded."""
        connect(qt_app)
        attach(qt_app, StubWorker("posting"))
        said = []
        monkeypatch.setattr(qt_app, "toast", lambda m, level="info": said.append((m, level)))
        settings.begin_switch()
        assert settings._confirming is False
        assert said == [(onboarding.SWITCH_BUSY, "warning")]

    def test_and_checks_again_at_the_yes(self, qt_app, settings, monkeypatch):
        """A post can start while they are reading the question."""
        connect(qt_app)
        worker = attach(qt_app, StubWorker("idle"))
        settings.begin_switch()
        worker.state = "posting"
        sent = []
        monkeypatch.setattr(qt_app, "run_in_background", lambda fn, ok=None, err=None: sent.append(fn))
        settings.confirm_switch()
        assert sent == []
        assert qt_app.current_view == "settings"

    def test_yes_signs_out_through_the_setup_screen(self, qt_app, settings, monkeypatch):
        """The sign-out and the login that follows are the setup screen's, so
        there is one implementation of each and one place that parks the
        window afterwards."""
        monkeypatch.setattr(login, "chrome_installed", lambda: True)
        connect(qt_app)
        sent = []
        monkeypatch.setattr(qt_app, "run_in_background", lambda fn, ok=None, err=None: sent.append(fn))
        settings.begin_switch()
        settings.confirm_button.click()
        assert sent == [login.switch_account]
        assert qt_app.current_view == "welcome"

    def test_the_connection_box_no_longer_offers_it(self, qt_app):
        connect(qt_app)
        assert qt_app.fix_button.isHidden()


class TestThisPC:
    def test_it_shows_what_windows_says(self, inline, settings):
        inline.always_on = FakeAlwaysOn(autostart=True, keep_awake=False)
        settings.load_pc()
        assert settings.autostart_box.isChecked()
        assert not settings.awake_box.isChecked()

    def test_turning_on_start_with_windows(self, inline, settings):
        fake = inline.always_on = FakeAlwaysOn()
        settings.load_pc()
        settings.autostart_box.click()
        assert fake.calls == [("autostart", True)]
        assert settings.autostart_box.isChecked()
        assert "sign in" in inline.toast_label.text()

    def test_turning_off_keep_awake(self, inline, settings):
        fake = inline.always_on = FakeAlwaysOn(keep_awake=True)
        settings.load_pc()
        settings.awake_box.click()
        assert fake.calls == [("keep_awake", False)]
        assert not settings.awake_box.isChecked()

    def test_a_failure_puts_the_box_back_to_the_truth(self, inline, settings):
        """The box shows what was clicked, not what happened. After a failure
        it is read again from Windows."""
        from fbposter.always_on import AlwaysOnError

        inline.always_on = FakeAlwaysOn(fails=AlwaysOnError("Access is denied."))
        settings.load_pc()
        settings.awake_box.click()
        assert not settings.awake_box.isChecked()
        assert "Access is denied." in inline.toast_label.text()

    def test_it_never_runs_on_the_drawing_thread(self, qt_app, settings, monkeypatch):
        """It asks Windows, which is a separate program starting up."""
        sent = []
        monkeypatch.setattr(qt_app, "run_in_background", lambda fn, ok=None, err=None: sent.append(fn))
        qt_app.always_on = FakeAlwaysOn()
        settings._pc_busy = False
        settings.set_keep_awake(True)
        assert len(sent) == 1
        assert qt_app.always_on.calls == []

    def test_a_window_the_suite_builds_changes_nothing(self, qt_app):
        """Only run() wires up the real one. A test that toggled it would
        change the power plan of whoever ran the suite."""
        from fbposter.always_on import Inert

        assert isinstance(qt_app.always_on, Inert)
