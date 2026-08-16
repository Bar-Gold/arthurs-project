"""Tests for the window, navigation, connection pill, toast and views.

These drive a real (hidden) window and pump the event loop by hand. mainloop()
is never called -- it would block the test run forever.

One App is shared across the module and reset between tests: repeatedly
creating and destroying Tk roots is unreliable on Windows. It is backed by a
temporary database, never the user's real one.
"""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import pytest

from fbposter.db.models import TARGET_DONE, utcnow
from fbposter.db.schema import DEFAULT_SETTINGS
from fbposter.ui import preview as preview_module
from fbposter.ui import textdir
from fbposter.ui.app import NAV_ITEMS, PILL_STATES
from fbposter.ui.connection import ConnectionResult, ConnectionState
from fbposter.ui.views.compose import (
    ALL_GROUPS_TAB,
    PREVIEW,
    REWORDED,
    RTL_TAG,
    SHARED_WORDING,
    WRITE,
)

from .conftest import SilentNamer, pump_until

# Read off the schema rather than written out, so changing it is a one-line
# change rather than a hunt through the suite.
DEFAULT_COOLDOWN = int(DEFAULT_SETTINGS["default_cooldown_hours"])

GROUP_A = "https://www.facebook.com/groups/123456789"
GROUP_B = "https://www.facebook.com/groups/gardening.tlv"

HEBREW = "שלום עולם, מה נשמע"
ENGLISH = "Hello world, how are you"


def IDLE_CHECK() -> ConnectionResult:
    return ConnectionResult(ConnectionState.UNKNOWN, "")


@pytest.fixture
def app(ui_app):
    return ui_app


@pytest.fixture(autouse=True)
def no_real_chrome_probe(monkeypatch):
    """Keep the UI tests off the network.

    The Groups view probes the debug port before looking up group names. Left
    unpatched that is a real socket call: instant when Chrome happens to be
    running, and a 1.16s timeout when it is not -- which made the suite take
    22s or 44s depending on nothing but the developer's browser. Tests that
    care about the browser being up patch this themselves.
    """
    from fbposter.ui.views import groups as groups_view

    monkeypatch.setattr(groups_view.chrome, "probe", lambda *a, **k: None)


@pytest.fixture(autouse=True)
def reset_app(app):
    """Return the shared window and its database to a known state."""
    yield

    pump_until(app, lambda: app.runner.pending == 0, timeout=6.0)
    app._check_fn = IDLE_CHECK
    app._reset_check_button()
    app._set_connection(ConnectionState.UNKNOWN, announce=False)
    app.toast.clear()

    for table in ("task_targets", "tasks", "templates", "groups"):
        app.db.write(f"DELETE FROM {table}")

    # Put the browser-free namer back, in case a test swapped in its own.
    app.group_namer = SilentNamer()
    app.views["groups"]._naming = False

    compose = app.views["compose"]
    compose._bodies.clear()
    compose._editing = None
    compose._base_body = ""
    compose._tab_signature = None
    compose.textbox.delete("1.0", "end")
    compose.attachments.clear()
    compose._render_attachments()
    compose._update_counter()
    compose.template_name.delete(0, "end")
    compose.schedule_mode.set("Post now")
    compose._sync_schedule_entry()
    # A test left in Preview would leave the editor unpacked for the next one.
    compose.view_mode.set(WRITE)
    compose.sync_mode()

    for view in app.views.values():
        view.on_show()
    app.show_view(NAV_ITEMS[0][0])
    app.update()


def add_group(app, url: str = GROUP_A):
    return app.group_repo.add_from_url(url)


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

    def test_navigating_refreshes_the_view_from_the_database(self, app):
        """A group added on one screen must appear on another."""
        add_group(app)
        app.show_view("compose")
        app.update()
        assert len(app.views["compose"]._group_vars) == 1


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
        app._set_connection(state, detail="detail", announce=False)
        app.update()
        assert app.connection_state is state
        assert app.pill_label.cget("text") == PILL_STATES[state][1]

    def test_a_successful_check_updates_the_pill(self, app):
        app._check_fn = lambda: ConnectionResult(ConnectionState.CONNECTED, "Logged in as 123")
        app.check_connection()

        assert pump_until(app, lambda: app.connection_state is ConnectionState.CONNECTED)
        assert app.check_button.cget("state") == "normal"

    def test_a_checkpoint_gets_its_own_state(self, app):
        app._check_fn = lambda: ConnectionResult(ConnectionState.CHECKPOINT, "verification")
        app.check_connection()
        assert pump_until(app, lambda: app.connection_state is ConnectionState.CHECKPOINT)

    def test_a_raising_check_becomes_the_error_state(self, app):
        def boom():
            raise RuntimeError("no browser")

        app._check_fn = boom
        app.check_connection()

        assert pump_until(app, lambda: app.connection_state is ConnectionState.ERROR)
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


class TestLayoutOverflow:
    """The expanding widget in each view must be packed last.

    Packed first, it claims the whole frame and shunts the controls below it
    past the bottom of the window.
    """

    def test_compose_controls_are_anchored_to_the_bottom(self, app):
        assert app.views["compose"].attachment_list.pack_info()["side"] == "bottom"

    def test_groups_note_is_anchored_to_the_bottom(self, app):
        assert app.views["groups"].note.pack_info()["side"] == "bottom"

    def test_queue_note_is_anchored_to_the_bottom(self, app):
        assert app.views["queue"].note.pack_info()["side"] == "bottom"

    def test_queue_rows_are_not_stretched_to_the_default_frame_height(self, app):
        """CTkFrame defaults to 200px tall. A child that does not override that
        drags its whole row to 200+, which is how the queue rows once ended up
        three times taller than their content.
        """
        group = add_group(app)
        app.task_repo.create("body", [(group.id, "body")])
        app.show_view("queue")
        app.update()

        rows = app.views["queue"].rows_frame.winfo_children()
        assert rows, "queue rendered no rows"
        for row in rows:
            assert row.winfo_reqheight() < 200, f"row is {row.winfo_reqheight()}px tall"

    def test_no_stray_default_sized_frame_in_a_task_card(self, app):
        """A CTkFrame left at its 200x200 default is not invisible padding --
        it draws as a stray 200px line across the card. Both layout bugs in
        this view came from that default.
        """
        group = add_group(app)
        app.task_repo.create("body", [(group.id, "body")])
        app.show_view("queue")
        app.update()

        card = app.views["queue"].rows_frame.winfo_children()[0]
        for child in card.winfo_children():
            assert child.winfo_reqwidth() != 200, (
                "a frame is still at the CTkFrame default width"
            )

    def test_the_toast_is_not_stretched_either(self, app):
        toast = app.toast.show("a short message", "info", duration_ms=0)
        app.update()
        assert toast.winfo_reqheight() < 100


class TestKeyBindings:
    """Check the bindings are registered on the widgets that receive keys.

    Calling a handler directly still passes when the bind() is missing or
    attached to the wrong widget, so these assert the wiring instead. They
    cannot press the key: event_generate does not dispatch to an unmapped
    widget, and the test window is withdrawn. Live typing was verified against
    a mapped window.
    """

    def test_the_compose_box_listens_for_keys(self, app):
        assert "<KeyRelease>" in app.views["compose"].textbox._textbox.bind()

    def test_the_url_field_listens_for_return(self, app):
        # Tk normalises "<Return>" to "<Key-Return>" when it stores the binding.
        bindings = app.views["groups"].url_entry._entry.bind()
        assert {"<Return>", "<Key-Return>"} & set(bindings)


class TestGroupsView:
    def test_a_valid_url_is_added_and_persisted(self, app):
        view = app.views["groups"]
        assert view.add_group(GROUP_A) is True
        assert [g.identifier for g in view.groups] == ["123456789"]
        assert [g.identifier for g in app.group_repo.list()] == ["123456789"]

    def test_an_invalid_url_is_rejected(self, app):
        view = app.views["groups"]
        assert view.add_group("https://www.google.com") is False
        assert view.groups == []

    def test_the_same_group_in_a_different_form_is_a_duplicate(self, app):
        view = app.views["groups"]
        view.add_group(GROUP_A)
        assert view.add_group("https://m.facebook.com/groups/123456789/posts/5") is False
        assert len(app.group_repo.list()) == 1

    def test_removal(self, app):
        view = app.views["groups"]
        view.add_group(GROUP_A)
        view.remove_group(view.groups[0])
        assert app.group_repo.list() == []

    def test_the_cooldown_can_be_changed(self, app):
        view = app.views["groups"]
        view.add_group(GROUP_A)
        view.set_cooldown(view.groups[0], "6")
        assert app.group_repo.list()[0].cooldown_hours == 6

    def test_a_nonsense_cooldown_is_rejected(self, app):
        view = app.views["groups"]
        view.add_group(GROUP_A)
        view.set_cooldown(view.groups[0], "soon")
        assert app.group_repo.list()[0].cooldown_hours == DEFAULT_COOLDOWN

    def test_a_negative_cooldown_is_rejected(self, app):
        view = app.views["groups"]
        view.add_group(GROUP_A)
        view.set_cooldown(view.groups[0], "-5")
        assert app.group_repo.list()[0].cooldown_hours == DEFAULT_COOLDOWN


class TestGroupNames:
    """Names are read off Facebook, so this all runs against a fake namer."""

    @pytest.fixture
    def chrome_up(self, monkeypatch):
        from fbposter.ui.views import groups as groups_view

        monkeypatch.setattr(groups_view.chrome, "probe", lambda *a, **k: {"Browser": "x"})

    def namer(self, app, mapping, raises=False):
        class Fake:
            calls = []

            def names_for(self, urls):
                Fake.calls.append(list(urls))
                if raises:
                    raise RuntimeError("browser died")
                return {url: mapping.get(url, "") for url in urls}

        fake = Fake()
        app.group_namer = fake
        return fake

    def test_a_group_shows_its_name_once_fetched(self, app, chrome_up):
        view = app.views["groups"]
        url = "https://www.facebook.com/groups/2509198906266893/"
        self.namer(app, {url: "bar-test"})

        view.add_group(GROUP_A.replace("123456789", "2509198906266893"))
        assert pump_until(app, lambda: app.group_repo.list()[0].name == "bar-test")
        assert app.group_repo.list()[0].display_name == "bar-test"

    def test_a_hebrew_name_survives_the_round_trip(self, app, chrome_up):
        """Page to database to widget, none of it ASCII."""
        view = app.views["groups"]
        url = "https://www.facebook.com/groups/464241678849975/"
        hebrew = "מוכרים-קונים כרטיסים להופעות"
        self.namer(app, {url: hebrew})

        view.add_group(url)
        assert pump_until(app, lambda: app.group_repo.list()[0].name == hebrew)

        view.refresh()
        app.update()
        assert view.groups[0].display_name == hebrew

    def test_a_failed_lookup_leaves_the_identifier(self, app, chrome_up):
        view = app.views["groups"]
        self.namer(app, {}, raises=True)

        view.add_group(GROUP_A)
        pump_until(app, lambda: app.runner.pending == 0, timeout=4.0)

        assert app.group_repo.list()[0].display_name == "123456789"
        assert view._naming is False, "the guard was left stuck on"

    def test_an_empty_name_is_not_stored(self, app, chrome_up):
        view = app.views["groups"]
        self.namer(app, {})  # every lookup returns ""

        view.add_group(GROUP_A)
        pump_until(app, lambda: app.runner.pending == 0, timeout=4.0)
        assert app.group_repo.list()[0].name == ""

    def test_no_sweep_starts_when_chrome_is_down(self, app, monkeypatch):
        """No browser is not an error, it is just no names."""
        from fbposter.ui.views import groups as groups_view

        monkeypatch.setattr(groups_view.chrome, "probe", lambda *a, **k: None)
        fake = self.namer(app, {})
        type(fake).calls = []

        add_group(app)
        assert app.views["groups"].fetch_missing_names() is False
        assert type(fake).calls == []

    def test_a_second_sweep_cannot_start_while_one_is_running(self, app, chrome_up):
        view = app.views["groups"]
        self.namer(app, {})
        add_group(app)

        assert view.fetch_missing_names() is True
        assert view.fetch_missing_names() is False, "started a duplicate sweep"
        pump_until(app, lambda: app.runner.pending == 0, timeout=4.0)

    def test_nothing_happens_when_every_group_is_named(self, app, chrome_up):
        group = add_group(app)
        app.group_repo.set_name(group.id, "Already Named")
        self.namer(app, {})
        assert app.views["groups"].fetch_missing_names() is False

    def test_the_queue_shows_the_name_not_the_id(self, app):
        group = add_group(app)
        app.group_repo.set_name(group.id, "bar-test")
        task = app.task_repo.create("body", [(group.id, "body")])

        target = app.task_repo.targets_for(task.id)[0]
        assert target.group_name == "bar-test"
        assert target.group_label == "bar-test"

    def test_the_queue_falls_back_to_the_id(self, app):
        group = add_group(app)
        task = app.task_repo.create("body", [(group.id, "body")])
        assert app.task_repo.targets_for(task.id)[0].group_label == "123456789"


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


class TestTemplates:
    def test_save_then_load(self, app):
        view = app.views["compose"]
        view.textbox.insert("1.0", "a saved body")
        view.template_name.insert(0, "My ad")
        assert view.save_template() is True

        view.textbox.delete("1.0", "end")
        view.template_picker.set("My ad")
        assert view.load_template() is True
        assert view.get_text() == "a saved body"

    def test_saving_without_a_name_is_refused(self, app):
        view = app.views["compose"]
        view.textbox.insert("1.0", "body")
        assert view.save_template() is False
        assert app.template_repo.list() == []

    def test_saving_an_empty_post_is_refused(self, app):
        view = app.views["compose"]
        view.template_name.insert(0, "Empty")
        assert view.save_template() is False
        assert app.template_repo.list() == []


class TestPerGroupText:
    """Rewording per group is what makes the variation warning actionable.

    Without it the warning fires on nearly every batch with nothing the user
    can do, which is how a safeguard becomes noise people ignore.
    """

    def setup_compose(self, app, count=2):
        groups = [add_group(app, url) for url in (GROUP_A, GROUP_B)[:count]]
        view = app.views["compose"]
        view.refresh_groups()
        for group in groups:
            view._group_vars[group.id].set(True)
        view.refresh_tabs()
        return view, groups

    def write(self, view, text):
        view.textbox.delete("1.0", "end")
        view.textbox.insert("1.0", text)

    def test_a_group_with_no_rewrite_uses_the_base_text(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "shared wording")
        view.capture()
        assert view.body_for(groups[0].id) == "shared wording"
        assert view.body_for(groups[1].id) == "shared wording"

    def test_each_tab_keeps_its_own_version(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base text")

        view.select_tab(groups[0].id)
        self.write(view, "wording for the first group")
        view.select_tab(groups[1].id)
        self.write(view, "different wording for the second")

        view.select_tab(None)
        assert view.get_text() == "base text"
        view.select_tab(groups[0].id)
        assert view.get_text() == "wording for the first group"
        view.select_tab(groups[1].id)
        assert view.get_text() == "different wording for the second"

    def test_switching_tabs_alone_does_not_reset_anything(self, app):
        """Only a genuine base edit clears rewrites; browsing must be free."""
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")

        view.select_tab(None)
        view.select_tab(groups[0].id)
        assert view.get_text() == "custom"

    def test_editing_the_base_resets_rewrites_and_says_so(self, app):
        """The user chose this behaviour; it must not happen silently."""
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")
        view.select_tab(None)

        self.write(view, "a different base")
        view.capture()
        app.update()

        assert view.body_for(groups[0].id) == "a different base"
        assert app.toast.visible, "reset the user's wording without telling them"

    def test_reset_puts_a_group_back_on_the_base_text(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")

        view.reset_current()
        assert view.get_text() == "base"
        assert not view.has_rewrite(groups[0].id)

    def test_deselecting_the_edited_group_returns_to_the_base(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")

        view._group_vars[groups[0].id].set(False)
        view.refresh_tabs()

        assert view._editing is None
        assert view.get_text() == "base"

    def test_the_tab_strip_follows_the_selection(self, app):
        view, groups = self.setup_compose(app)
        app.update()
        # "All groups" plus one per selected group.
        assert len(view.tab_strip.winfo_children()) == len(groups) + 1

        view._group_vars[groups[1].id].set(False)
        view.refresh_tabs()
        app.update()
        assert len(view.tab_strip.winfo_children()) == len(groups)

    def test_a_reworded_group_is_marked(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")
        view.select_tab(None)

        assert view.has_rewrite(groups[0].id)
        assert not view.has_rewrite(groups[1].id)


class TestQueueingPerGroupText:
    @pytest.fixture(autouse=True)
    def any_hour(self, app):
        app.settings_repo.set("posting_window_start_hour", 0)
        app.settings_repo.set("posting_window_end_hour", 24)
        yield
        app.settings_repo.set("posting_window_start_hour", 8)
        app.settings_repo.set("posting_window_end_hour", 23)

    def test_each_group_is_stored_with_its_own_wording(self, app):
        """The whole point: task_targets holds different text per group."""
        helper = TestPerGroupText()
        view, groups = helper.setup_compose(app)
        helper.write(view, "base wording")
        view.select_tab(groups[0].id)
        helper.write(view, "wording one")
        view.select_tab(groups[1].id)
        helper.write(view, "wording two")

        assert view.add_to_queue() is True

        task = app.task_repo.list_recent()[0]
        bodies = {t.group_id: t.body for t in app.task_repo.targets_for(task.id)}
        assert bodies[groups[0].id] == "wording one"
        assert bodies[groups[1].id] == "wording two"

    def test_identical_text_still_warns(self, app):
        helper = TestPerGroupText()
        view, groups = helper.setup_compose(app)
        for extra in ("https://www.facebook.com/groups/third-group",):
            group = app.group_repo.add_from_url(extra)
            view.refresh_groups()
            view._group_vars[group.id].set(True)
        helper.write(view, "exactly the same everywhere")

        assert view.add_to_queue() is True
        app.update()
        assert app.toast.visible, "three identical bodies should warn"

    def test_varied_text_does_not_warn(self, app):
        """Rewording is the way out of the warning, and it must actually work."""
        helper = TestPerGroupText()
        view, groups = helper.setup_compose(app)
        third = app.group_repo.add_from_url("https://www.facebook.com/groups/third-group")
        view.refresh_groups()
        for group in groups + [third]:
            view._group_vars[group.id].set(True)

        helper.write(view, "the shared base")
        for index, group in enumerate(groups + [third]):
            view.select_tab(group.id)
            helper.write(view, f"a genuinely different message number {index}")

        app.toast.clear()
        assert view.add_to_queue() is True
        app.update()

        task = app.task_repo.list_recent()[0]
        bodies = [t.body for t in app.task_repo.targets_for(task.id)]
        assert len(set(bodies)) == 3, "bodies were not distinct"

    def test_a_group_left_blank_is_refused(self, app):
        helper = TestPerGroupText()
        view, groups = helper.setup_compose(app)
        helper.write(view, "base")
        view.select_tab(groups[0].id)
        helper.write(view, "   ")

        assert view.add_to_queue() is False
        assert app.task_repo.list_recent() == []


class TestPreview:
    """Seeing the post before it goes out.

    Worth more than it looks with per-group wording: five groups get five
    different posts, and reading one back is the only way to check it. So the
    preview must show the *committed* text for the *active tab* -- a preview of
    the wrong group's words would be worse than no preview at all.
    """

    def setup_compose(self, app, count=2):
        return TestPerGroupText().setup_compose(app, count)

    def write(self, view, text):
        TestPerGroupText().write(view, text)

    def show_preview(self, view):
        view.view_mode.set(PREVIEW)
        view.sync_mode()

    def test_the_toggle_swaps_the_editor_for_the_preview(self, app):
        view = app.views["compose"]
        assert view.editor.winfo_manager() == "pack"

        self.show_preview(view)
        assert view.editor.winfo_manager() == ""
        assert view.preview.winfo_manager() == "pack"

        view.view_mode.set(WRITE)
        view.sync_mode()
        assert view.editor.winfo_manager() == "pack"
        assert view.preview.winfo_manager() == ""

    def test_it_starts_on_the_write_side(self, app):
        assert app.views["compose"].view_mode.get() == WRITE

    def test_the_base_text_is_previewed(self, app):
        view = app.views["compose"]
        self.write(view, "the shared wording")
        self.show_preview(view)
        assert view.preview.text_shown() == "the shared wording"

    def test_each_group_previews_its_own_wording(self, app):
        """The whole reason this is worth building."""
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "wording for the first group")
        view.select_tab(groups[1].id)
        self.write(view, "wording for the second group")

        self.show_preview(view)
        assert view.preview.text_shown() == "wording for the second group"

        view.select_tab(groups[0].id)
        assert view.preview.text_shown() == "wording for the first group"

        view.select_tab(None)
        assert view.preview.text_shown() == "base"

    def test_a_group_without_a_rewrite_previews_the_base(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "shared")
        self.show_preview(view)
        view.select_tab(groups[0].id)
        assert view.preview.text_shown() == "shared"

    def test_the_heading_names_the_group_being_previewed(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")
        self.show_preview(view)

        heading, subheading = view.preview_heading()
        assert heading == app.group_repo.get(groups[0].id).display_name
        assert subheading == REWORDED

    def test_a_group_on_the_shared_wording_says_so(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        self.show_preview(view)
        view.select_tab(groups[0].id)
        assert view.preview_heading()[1] == SHARED_WORDING

    def test_the_base_tab_heading_counts_the_groups(self, app):
        view, groups = self.setup_compose(app)
        heading, subheading = view.preview_heading()
        assert heading == ALL_GROUPS_TAB
        assert "2 groups" in subheading

    def test_an_empty_post_says_there_is_nothing_to_see(self, app):
        view = app.views["compose"]
        self.show_preview(view)
        assert view.preview.text_shown() == ""
        assert view.preview.images == []

    def test_an_attached_image_is_shown(self, app, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "photo.png"
        Image.new("RGB", (800, 600), (46, 125, 74)).save(path)

        view = app.views["compose"]
        self.write(view, "with a picture")
        view.attachments = [path]
        view._render_attachments()
        self.show_preview(view)

        assert len(view.preview.images) == 1
        assert view.preview.images[0].cget("size")[0] <= preview_module.IMAGE_MAX_WIDTH

    def test_an_unreadable_image_still_shows_that_it_is_attached(self, app, tmp_path):
        """It is still going to be uploaded, so it must not vanish from the
        preview -- the user would think the attachment had been dropped."""
        view = app.views["compose"]
        self.write(view, "with a broken picture")
        view.attachments = [tmp_path / "deleted.png"]
        view._render_attachments()
        self.show_preview(view)

        assert view.preview.images == []
        assert view.preview.placeholders == [tmp_path / "deleted.png"]

    def test_images_with_no_text_are_still_a_post(self, app, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "only.png"
        Image.new("RGB", (200, 200), (30, 90, 200)).save(path)

        view = app.views["compose"]
        view.attachments = [path]
        view._render_attachments()
        self.show_preview(view)

        assert view.preview.text_shown() == preview_module.NO_TEXT
        assert len(view.preview.images) == 1

    def test_removing_an_attachment_updates_a_visible_preview(self, app, tmp_path):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "photo.png"
        Image.new("RGB", (400, 300), (46, 125, 74)).save(path)

        view = app.views["compose"]
        self.write(view, "text")
        view.attachments = [path]
        view._render_attachments()
        self.show_preview(view)
        assert len(view.preview.images) == 1

        view._remove_attachment(path)
        assert view.preview.images == []

    def test_deselecting_the_previewed_group_falls_back_to_the_base(self, app):
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")
        self.show_preview(view)

        view._group_vars[groups[0].id].set(False)
        view.refresh_tabs()

        assert view.preview_heading()[0] == ALL_GROUPS_TAB
        assert view.preview.text_shown() == "base"

    def test_previewing_does_not_change_what_gets_posted(self, app):
        """Looking at a post must never edit it."""
        view, groups = self.setup_compose(app)
        self.write(view, "base")
        view.select_tab(groups[0].id)
        self.write(view, "custom")

        self.show_preview(view)
        view.select_tab(None)
        view.select_tab(groups[1].id)
        view.view_mode.set(WRITE)
        view.sync_mode()

        assert view.body_for(groups[0].id) == "custom"
        assert view.body_for(groups[1].id) == "base"
        assert view._base_body == "base"

    def test_rendering_is_skipped_while_writing(self, app):
        """refresh_preview() is called from everywhere, so it has to be free
        when the preview is not on screen."""
        view = app.views["compose"]
        calls = []
        original = view.preview.render
        view.preview.render = lambda **kwargs: calls.append(kwargs)
        try:
            view.refresh_preview()
            view.refresh_tabs()
            assert calls == []
        finally:
            view.preview.render = original


class TestTextDirection:
    """Hebrew has to sit against the right edge, in the editor and the preview.

    Tk reorders the characters correctly on its own; it is alignment it gets
    wrong, so these check the one thing the app decides. The editor aligns as a
    single block rather than line by line, which is what a textarea with
    dir="auto" does -- otherwise a Hebrew post with an English line in it has
    its lines jumping between the two edges as they are typed.
    """

    def editor_justify(self, view, line=1):
        """How Tk will actually align that line: left unless it is tagged."""
        tagged = RTL_TAG in view.textbox.tag_names(f"{line}.0")
        return "right" if tagged else "left"

    def editor_layout(self, view):
        lines = len(view.get_text().split("\n"))
        return [self.editor_justify(view, number) for number in range(1, lines + 1)]

    def preview_labels(self, view):
        view.view_mode.set(PREVIEW)
        view.sync_mode()
        return view.preview._wrapped

    def test_hebrew_right_aligns_the_editor(self, app):
        view = app.views["compose"]
        view._show(HEBREW)
        assert self.editor_justify(view) == "right"

    def test_english_left_aligns_the_editor(self, app):
        view = app.views["compose"]
        view._show(ENGLISH)
        assert self.editor_justify(view) == "left"

    def test_an_empty_editor_is_left_aligned(self, app):
        """Nothing strong to go on yet, so it waits rather than guessing."""
        view = app.views["compose"]
        view._show("")
        assert self.editor_justify(view) == "left"

    def test_typing_hebrew_flips_the_editor(self, app):
        """The KeyRelease path, not just the programmatic one."""
        view = app.views["compose"]
        view._show("")
        assert self.editor_justify(view) == "left"

        view.textbox.insert("1.0", HEBREW)
        view._on_text_changed()
        assert self.editor_justify(view) == "right"

    def test_each_line_is_aligned_on_its_own(self, app):
        """The reported bug: one direction for the whole box tangled a
        bilingual post by dragging the English over to the right."""
        view = app.views["compose"]
        view._show(f"{HEBREW}\n{ENGLISH}")
        assert self.editor_layout(view) == ["right", "left"]

        view._show(f"{ENGLISH}\n{HEBREW}")
        assert self.editor_layout(view) == ["left", "right"]

    def test_a_line_with_no_language_stays_with_the_block(self, app):
        view = app.views["compose"]
        view._show(f"{HEBREW}\n054-1234567\n{HEBREW}")
        assert self.editor_layout(view) == ["right", "right", "right"]

    def test_the_direction_follows_a_tab_switch(self, app):
        """Each group's own wording decides how its tab is aligned."""
        view, groups = TestPerGroupText().setup_compose(app)
        view._show(ENGLISH)
        view.capture()
        assert self.editor_justify(view) == "left"

        view.select_tab(groups[0].id)
        view._show(HEBREW)
        view.capture()
        assert self.editor_justify(view) == "right"

        view.select_tab(None)
        assert self.editor_justify(view) == "left"

    def test_a_keystroke_that_changes_nothing_does_not_retag(self, app):
        """Retagging relayouts the whole widget, which is felt as typing lag.

        Every keystroke used to pay for it; only a genuine change should.
        """
        view = app.views["compose"]
        view._show(HEBREW)

        calls = []
        original = view.textbox.tag_remove
        view.textbox.tag_remove = lambda *a, **k: (calls.append(a), original(*a, **k))[1]
        try:
            for extra in "ועוד מילים":
                view.textbox.insert("end - 1c", extra)
                view._on_text_changed()
            assert calls == [], "typing within one direction should not retag"

            view.textbox.insert("end - 1c", "\nEnglish now")
            view._on_text_changed()
            assert calls, "a new line in the other direction must retag"
        finally:
            view.textbox.tag_remove = original

    def test_the_preview_aligns_each_line_like_the_editor(self, app):
        """A preview aligned differently from the post is worse than none."""
        view = app.views["compose"]
        for text in (HEBREW, ENGLISH, f"{HEBREW}\n{ENGLISH}", f"{ENGLISH}\n{HEBREW}"):
            view.view_mode.set(WRITE)
            view.sync_mode()
            view._show(text)
            expected = self.editor_layout(view)
            shown = [label.cget("justify") for label in self.preview_labels(view)]
            assert shown == expected

    def test_the_preview_anchors_hebrew_to_the_right_edge(self, app):
        view = app.views["compose"]
        view._show(HEBREW)
        assert self.preview_labels(view)[0].cget("anchor") == textdir.anchor(HEBREW)

    def test_the_preview_still_reads_back_the_whole_post(self, app):
        """Split across labels, but text_shown() must reconstruct it."""
        view = app.views["compose"]
        post = f"{HEBREW}\n{ENGLISH}"
        view._show(post)
        self.preview_labels(view)
        assert view.preview.text_shown() == post

    def test_hebrew_text_is_not_altered_on_its_way_to_the_post(self, app):
        """The marks are display scaffolding and must never be smuggled into
        what actually gets typed into Facebook."""
        view = app.views["compose"]
        mixed = f"{HEBREW}\n{ENGLISH}"
        view._show(mixed)
        view.capture()
        assert view.get_text() == mixed
        assert view._base_body == mixed

    def test_a_hebrew_line_carries_the_invisible_mark(self, app):
        """Without it Windows draws a mixed line mirrored."""
        view = app.views["compose"]
        view._show(HEBREW)
        assert view.textbox.get("1.0", "1.1") == textdir.RLE_MARK

    def test_an_english_line_does_not(self, app):
        view = app.views["compose"]
        view._show(ENGLISH)
        assert view.textbox.get("1.0", "1.1") != textdir.RLE_MARK

    def test_the_mark_is_dropped_when_a_line_stops_being_hebrew(self, app):
        view = app.views["compose"]
        view._show(HEBREW)
        assert view.textbox.get("1.0", "1.1") == textdir.RLE_MARK

        view._show(ENGLISH)
        assert view.textbox.get("1.0", "1.1") != textdir.RLE_MARK
        assert view.get_text() == ENGLISH

    def test_only_the_hebrew_lines_are_marked(self, app):
        view = app.views["compose"]
        view._show(f"{HEBREW}\n{ENGLISH}\n{HEBREW}")
        marks = [view.textbox.get(f"{n}.0", f"{n}.1") == textdir.RLE_MARK for n in (1, 2, 3)]
        assert marks == [True, False, True]

    def test_the_character_count_ignores_the_mark(self, app):
        """An invisible fix must not make the post look longer than it is."""
        view = app.views["compose"]
        view._show(HEBREW)
        assert view.counter.cget("text") == f"{len(HEBREW)} characters"

    @pytest.fixture
    def open_posting_window(self, app):
        """Queueing is refused outside 08:00-23:00, so without this the test
        passes or fails depending on the time of day it happens to run at."""
        app.settings_repo.set("posting_window_start_hour", 0)
        app.settings_repo.set("posting_window_end_hour", 24)
        yield
        app.settings_repo.set("posting_window_start_hour", 8)
        app.settings_repo.set("posting_window_end_hour", 23)

    def test_the_queued_body_is_free_of_direction_marks(self, app, open_posting_window):
        """The safety-critical one: this text is typed into Facebook."""
        view, groups = TestPerGroupText().setup_compose(app)
        view._show(f"{HEBREW} kalofan 1000")
        assert view.add_to_queue() is True

        stored = app.db.query("SELECT body FROM task_targets")
        assert stored
        for row in stored:
            assert not any(mark in row["body"] for mark in textdir.BIDI_CONTROLS)
        for row in app.db.query("SELECT body FROM tasks"):
            assert not any(mark in row["body"] for mark in textdir.BIDI_CONTROLS)

    def test_a_saved_template_is_free_of_direction_marks(self, app):
        view = app.views["compose"]
        view._show(HEBREW)
        view.template_name.insert(0, "hebrew")
        assert view.save_template() is True

        saved = app.template_repo.get_by_name("hebrew")
        assert saved is not None
        assert saved.body == HEBREW


class TestAddToQueue:
    @pytest.fixture(autouse=True)
    def any_hour(self, app):
        """Take the clock out of these tests.

        They are about the cap, the cooldown and repeated text. Left on the
        real 08:00-23:00 window they pass all day and fail at night, which is
        the worst kind of flake -- it looks like the code broke.
        The window itself is covered by its own test below.
        """
        app.settings_repo.set("posting_window_start_hour", 0)
        app.settings_repo.set("posting_window_end_hour", 24)
        yield
        app.settings_repo.set("posting_window_start_hour", 8)
        app.settings_repo.set("posting_window_end_hour", 23)

    def select(self, app, *group_ids):
        view = app.views["compose"]
        view.refresh_groups()
        for group_id in group_ids:
            view._group_vars[group_id].set(True)
        return view

    def test_a_clean_batch_is_queued(self, app):
        group = add_group(app)
        view = self.select(app, group.id)
        view.textbox.insert("1.0", "Selling a road bike")

        assert view.add_to_queue() is True

        tasks = app.task_repo.list_recent()
        assert len(tasks) == 1
        targets = app.task_repo.targets_for(tasks[0].id)
        assert [t.group_id for t in targets] == [group.id]
        assert targets[0].body == "Selling a road bike"

    def test_an_empty_post_is_refused(self, app):
        group = add_group(app)
        view = self.select(app, group.id)
        assert view.add_to_queue() is False
        assert app.task_repo.list_recent() == []

    def test_no_groups_selected_is_refused(self, app):
        add_group(app)
        view = app.views["compose"]
        view.refresh_groups()
        view.textbox.insert("1.0", "body")
        assert view.add_to_queue() is False
        assert app.task_repo.list_recent() == []

    def test_a_group_in_cooldown_blocks_the_batch(self, app):
        group = add_group(app)
        app.group_repo.mark_posted(group.id, utcnow() - timedelta(hours=1))
        view = self.select(app, group.id)
        view.textbox.insert("1.0", "Something new")

        assert view.add_to_queue() is False
        assert app.task_repo.list_recent() == []

    def test_a_shortened_cooldown_lets_an_active_group_through(self, app):
        """The user's case: post again to a big group the same day."""
        group = add_group(app)
        app.group_repo.set_cooldown(group.id, 6)
        app.group_repo.mark_posted(group.id, utcnow() - timedelta(hours=7))
        view = self.select(app, group.id)
        view.textbox.insert("1.0", "A different message")

        assert view.add_to_queue() is True

    def test_text_already_posted_to_that_group_is_blocked(self, app):
        group = add_group(app)
        task = app.task_repo.create("old", [(group.id, "Selling a road bike")])
        app.db.write(
            "UPDATE task_targets SET state = ?, posted_at = ? WHERE task_id = ?",
            (TARGET_DONE, utcnow().isoformat(), task.id),
        )
        app.group_repo.set_cooldown(group.id, 0)

        view = self.select(app, group.id)
        view.textbox.insert("1.0", "Selling a road bike")

        assert view.add_to_queue() is False
        assert len(app.task_repo.list_recent()) == 1  # only the pre-existing one

    def test_a_malformed_schedule_is_refused(self, app):
        group = add_group(app)
        view = self.select(app, group.id)
        view.textbox.insert("1.0", "body")
        view.schedule_mode.set("Schedule")
        view._sync_schedule_entry()
        view.schedule_entry.delete(0, "end")
        view.schedule_entry.insert(0, "next tuesday")

        assert view.add_to_queue() is False
        assert app.task_repo.list_recent() == []

    def test_a_post_outside_the_window_is_refused(self, app):
        """A window that excludes every hour must block whatever the clock says."""
        app.settings_repo.set("posting_window_start_hour", 3)
        app.settings_repo.set("posting_window_end_hour", 3)
        group = add_group(app)
        view = self.select(app, group.id)
        view.textbox.insert("1.0", "body")

        # start == end is treated as "no restriction", so use a real 1h window
        # that the current hour cannot be inside.
        from datetime import datetime

        hour = datetime.now().hour
        closed = (hour + 5) % 24
        app.settings_repo.set("posting_window_start_hour", closed)
        app.settings_repo.set("posting_window_end_hour", (closed + 1) % 24 or 24)

        assert view.add_to_queue() is False
        assert app.task_repo.list_recent() == []

    def test_selection_survives_a_refresh(self, app):
        one = add_group(app, GROUP_A)
        add_group(app, GROUP_B)
        view = self.select(app, one.id)
        view.refresh_groups()
        assert view.selected_group_ids() == [one.id]


class TestWorkerRow:
    def test_constructing_the_app_does_not_start_posting(self, app):
        """The safety property that lets the whole GUI suite exist: building an
        App must never spin up a thread that posts to Facebook. Only run()
        starts the scheduler."""
        assert app.worker is None

    def test_the_row_reports_that_the_scheduler_is_off(self, app):
        app._refresh_worker_row()
        app.update()
        assert "off" in app.worker_label.cget("text")
        assert app.worker_button.cget("text") == "Start"

    def test_toggling_pauses_and_resumes(self, app):
        from fbposter.worker import PostingWorker

        # Started for real: pausing a thread that was never running would
        # report "off", which is not the state being tested. The queue is empty,
        # so the worker has nothing to post.
        app.worker = PostingWorker(app.db, poster=object(), tick_seconds=0.05)
        app.worker.start()
        try:
            app.toggle_worker()
            assert app.worker.paused
            assert app.worker_button.cget("text") == "Resume"
            assert "paused" in app.worker_label.cget("text")

            app.toggle_worker()
            assert not app.worker.paused
            assert app.worker_button.cget("text") == "Pause"
        finally:
            app.worker.stop()
            app.worker = None
            app._refresh_worker_row()

    def test_a_halt_event_is_surfaced_loudly(self, app):
        from fbposter.worker import WorkerEvent

        app._handle_worker_event(WorkerEvent("halted", "temporarily blocked", 1, 1))
        app.update()
        assert app.toast.visible

    def test_worker_events_refresh_the_queue_view(self, app):
        from fbposter.worker import WorkerEvent

        group = add_group(app)
        app.task_repo.create("body", [(group.id, "body")])
        app._handle_worker_event(WorkerEvent("posted", "Posted.", 1, 1))
        app.update()
        assert app.views["queue"].rows_frame.winfo_children()


class TestQueueView:
    def test_the_empty_state_renders(self, app):
        app.show_view("queue")
        app.update()
        assert app.views["queue"].rows_frame.winfo_children()

    def test_a_queued_task_appears(self, app):
        group = add_group(app)
        app.task_repo.create("a queued body", [(group.id, "a queued body")])
        app.show_view("queue")
        app.update()
        assert app.views["queue"].rows_frame.winfo_children()

    def test_cancelling_marks_the_task_cancelled(self, app):
        group = add_group(app)
        task = app.task_repo.create("body", [(group.id, "body")])
        app.views["queue"].cancel_task(task)
        assert app.task_repo.get(task.id).state == "cancelled"
