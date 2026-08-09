"""Tests for the window, navigation, connection pill and toast.

These drive a real (hidden) window and pump the event loop by hand. mainloop()
is never called -- it would block the test run forever.

One App is shared across the module and reset between tests: repeatedly
creating and destroying Tk roots is unreliable on Windows.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from fbposter.ui.app import NAV_ITEMS, PILL_STATES
from fbposter.ui.connection import ConnectionResult, ConnectionState

from .conftest import pump_until


def IDLE_CHECK() -> ConnectionResult:
    return ConnectionResult(ConnectionState.UNKNOWN, "")


@pytest.fixture
def app(ui_app):
    return ui_app


@pytest.fixture(autouse=True)
def reset_app(app):
    """Return the shared window to a known state after every test."""
    yield

    pump_until(app, lambda: app.runner.pending == 0, timeout=6.0)
    app._check_fn = IDLE_CHECK
    app._reset_check_button()
    app._set_connection(ConnectionState.UNKNOWN, announce=False)
    app.toast.clear()
    app.show_view(NAV_ITEMS[0][0])

    groups = app.views["groups"]
    groups.groups.clear()
    groups._render()

    compose = app.views["compose"]
    compose.textbox.delete("1.0", "end")
    compose.attachments.clear()
    compose._render_attachments()
    compose._update_counter()

    app.update()


class TestLayout:
    def test_every_nav_item_has_a_view_and_a_button(self, app):
        for key, _label, _cls in NAV_ITEMS:
            assert key in app.views
            assert key in app.nav_buttons

    def test_opens_on_the_first_view(self, app):
        assert app.current_view == NAV_ITEMS[0][0]

    def test_connection_starts_unknown(self, app):
        assert app.connection_state is ConnectionState.UNKNOWN


class TestNavigation:
    def test_switching_changes_the_current_view(self, app):
        app.show_view("groups")
        assert app.current_view == "groups"
        app.show_view("queue")
        assert app.current_view == "queue"

    def test_the_active_button_is_highlighted_and_others_are_not(self, app):
        app.show_view("groups")
        app.update()
        assert app.nav_buttons["groups"].cget("fg_color") != "transparent"
        assert app.nav_buttons["queue"].cget("fg_color") == "transparent"


class TestToast:
    def test_show_then_clear(self, app):
        app.toast.show("hello", "info", duration_ms=0)
        assert app.toast.visible
        app.toast.clear()
        assert not app.toast.visible

    def test_it_dismisses_itself(self, app):
        app.toast.show("temporary", "info", duration_ms=60)
        assert app.toast.visible
        assert pump_until(app, lambda: not app.toast.visible, timeout=3.0)

    def test_a_new_toast_replaces_the_old_one(self, app):
        first = app.toast.show("first", "info", duration_ms=0)
        second = app.toast.show("second", "error", duration_ms=0)
        assert first is not second
        assert not first.winfo_exists()


class TestConnectionPill:
    @pytest.mark.parametrize("state", list(ConnectionState))
    def test_every_state_renders(self, app, state):
        """Including the ones that are hard to reach with a real browser."""
        app._set_connection(state, detail="detail", announce=False)
        app.update()
        assert app.connection_state is state
        assert app.pill_label.cget("text") == PILL_STATES[state][1]

    def test_a_successful_check_updates_the_pill(self, app):
        app._check_fn = lambda: ConnectionResult(
            ConnectionState.CONNECTED, "Logged in as 123"
        )
        app.check_connection()

        assert pump_until(app, lambda: app.connection_state is ConnectionState.CONNECTED)
        assert app.check_button.cget("state") == "normal"

    def test_a_checkpoint_gets_its_own_state(self, app):
        """A checkpoint must never look like an ordinary error -- it means stop
        and go look at the browser."""
        app._check_fn = lambda: ConnectionResult(
            ConnectionState.CHECKPOINT, "verification screen"
        )
        app.check_connection()

        assert pump_until(app, lambda: app.connection_state is ConnectionState.CHECKPOINT)

    def test_a_raising_check_becomes_the_error_state(self, app):
        def boom():
            raise RuntimeError("no browser")

        app._check_fn = boom
        app.check_connection()

        assert pump_until(app, lambda: app.connection_state is ConnectionState.ERROR)
        # The button has to come back or the user is stuck.
        assert app.check_button.cget("state") == "normal"

    def test_the_button_is_disabled_while_checking(self, app):
        release = threading.Event()

        def slow():
            release.wait(timeout=5)
            return ConnectionResult(ConnectionState.CONNECTED, "ok")

        app._check_fn = slow
        app.check_connection()
        app.update()

        try:
            assert app.check_button.cget("state") == "disabled"
            assert app.connection_state is ConnectionState.CHECKING
        finally:
            release.set()

    def test_a_second_check_is_ignored_while_one_is_running(self, app):
        release = threading.Event()
        calls = []

        def slow():
            calls.append(1)
            release.wait(timeout=5)
            return ConnectionResult(ConnectionState.CONNECTED, "ok")

        app._check_fn = slow
        app.check_connection()
        app.update()
        app.check_connection()  # must be a no-op, not a second browser session
        app.update()

        try:
            assert app.runner.pending == 1
        finally:
            release.set()
            pump_until(app, lambda: app.runner.pending == 0)
        assert calls == [1]


class TestGroupsView:
    def test_a_valid_url_is_added(self, app):
        view = app.views["groups"]
        assert view.add_group("https://www.facebook.com/groups/123456789") is True
        assert [g.identifier for g in view.groups] == ["123456789"]

    def test_an_invalid_url_is_rejected(self, app):
        view = app.views["groups"]
        assert view.add_group("https://www.google.com") is False
        assert view.groups == []

    def test_the_same_group_in_a_different_form_is_a_duplicate(self, app):
        view = app.views["groups"]
        view.add_group("https://www.facebook.com/groups/123456789")
        assert view.add_group("https://m.facebook.com/groups/123456789/posts/5") is False
        assert len(view.groups) == 1

    def test_removal(self, app):
        view = app.views["groups"]
        view.add_group("https://www.facebook.com/groups/123456789")
        view.remove_group(view.groups[0])
        assert view.groups == []


class TestLayoutOverflow:
    """The expanding widget in each view must be packed last.

    Packed first, it claims the whole frame and shunts the controls below it
    past the bottom of the window -- which is exactly what happened to the
    'Attach images' row before this was fixed.
    """

    def test_compose_controls_are_anchored_to_the_bottom(self, app):
        view = app.views["compose"]
        assert view.attachment_list.pack_info()["side"] == "bottom"

    def test_groups_note_is_anchored_to_the_bottom(self, app):
        assert app.views["groups"].note.pack_info()["side"] == "bottom"

    def test_queue_note_is_anchored_to_the_bottom(self, app):
        assert app.views["queue"].note.pack_info()["side"] == "bottom"

    def test_queue_rows_are_not_stretched_to_the_default_frame_height(self, app):
        """CTkFrame defaults to 200px tall. A child that does not override that
        drags its whole row to 200+, which is how the queue rows ended up three
        times taller than their content.
        """
        app.show_view("queue")
        app.update()
        rows = app.views["queue"].rows_frame.winfo_children()
        assert rows, "queue rendered no rows"
        for row in rows:
            assert row.winfo_reqheight() < 100, f"row is {row.winfo_reqheight()}px tall"

    def test_the_toast_is_not_stretched_either(self, app):
        toast = app.toast.show("a short message", "info", duration_ms=0)
        app.update()
        assert toast.winfo_reqheight() < 100


class TestComposeView:
    def test_text_round_trips(self, app):
        view = app.views["compose"]
        view.textbox.insert("1.0", "hello groups")
        assert view.get_text() == "hello groups"

    def test_the_counter_follows_the_text(self, app):
        view = app.views["compose"]
        view.textbox.insert("1.0", "abcde")
        view._update_counter()
        assert view.counter.cget("text") == "5 characters"

    def test_singular_counter(self, app):
        view = app.views["compose"]
        view.textbox.insert("1.0", "x")
        view._update_counter()
        assert view.counter.cget("text") == "1 character"

    def test_attachments_can_be_removed(self, app):
        view = app.views["compose"]
        view.attachments = [Path("a.png"), Path("b.png")]
        view._render_attachments()
        view._remove_attachment(Path("a.png"))
        assert view.attachments == [Path("b.png")]
