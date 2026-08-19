"""The redraw guards, and the one blocking call that was on the UI thread.

Every view rebuilt all of its widgets from scratch on every visit. Opening the
Queue with 25 batches cost 110ms of widget construction to draw exactly what
was already on screen, and the worker refreshed it again on every event -- so
during a batch, which is when someone is actually watching it, it rebuilt
several times a minute. These tests pin that an unchanged screen is not
rebuilt, and that a changed one still is.
"""

from __future__ import annotations

import inspect

import pytest


def widgets_in(layout) -> list:
    return [
        layout.itemAt(index).widget()
        for index in range(layout.count())
        if layout.itemAt(index).widget() is not None
    ]


@pytest.fixture
def stocked(qt_app):
    """A window with groups and a batch, so the lists have something in them."""
    groups = qt_app.group_repo
    made = []
    for index in range(4):
        group = groups.add_from_url(f"https://www.facebook.com/groups/perf{index}")
        groups.set_name(group.id, f"Perf Group {index}")
        made.append(group)
    body = "Selling a road bike, 54cm frame."
    qt_app.task_repo.create(body, [(made[0].id, body), (made[1].id, body + "!")])
    qt_app.selected_groups = {made[0].id, made[1].id}
    return qt_app, made


class TestTheQueueIsNotRebuiltForNothing:
    def test_an_unchanged_queue_keeps_its_widgets(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)
        assert before, "nothing was drawn to begin with"

        view.refresh()
        assert widgets_in(view.rows) == before, "rebuilt an identical queue"

    def test_a_changed_batch_does_rebuild(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)

        task = app.task_repo.list_recent()[0]
        app.task_repo.cancel(task.id)
        view.refresh()
        assert widgets_in(view.rows) != before, "a cancelled batch was not redrawn"

    def test_a_changed_target_does_rebuild(self, stocked):
        """The card shows per-group state, so a target moving has to redraw."""
        from fbposter.db.models import TARGET_DONE

        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)

        task = app.task_repo.list_recent()[0]
        target = app.task_repo.targets_for(task.id)[0]
        app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_switching_scope_rebuilds(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)
        view.set_scope(True)
        assert widgets_in(view.rows) != before

    def test_force_rebuilds_anyway(self, stocked):
        app, _ = stocked
        view = app.views["queue"]
        view.refresh()
        before = widgets_in(view.rows)
        view.refresh(force=True)
        assert widgets_in(view.rows) != before


class TestTheGroupListIsNotRebuiltForNothing:
    def test_an_unchanged_list_keeps_its_widgets(self, stocked):
        app, _ = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)
        assert before

        view.refresh()
        assert widgets_in(view.rows) == before

    def test_a_renamed_group_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)

        app.group_repo.set_name(made[0].id, "Renamed Entirely")
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_a_changed_cooldown_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)

        app.group_repo.set_cooldown(made[0].id, 12)
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_a_new_group_rebuilds(self, stocked):
        app, _ = stocked
        view = app.views["groups"]
        view.refresh()
        before = widgets_in(view.rows)

        app.group_repo.add_from_url("https://www.facebook.com/groups/brandnew")
        view.refresh()
        assert widgets_in(view.rows) != before

    def test_the_checkboxes_still_resolve(self, stocked):
        """A skipped rebuild must not leave the map pointing at dead widgets."""
        app, made = stocked
        view = app.views["groups"]
        view.refresh()
        view.refresh()
        assert set(view._checkboxes) == {group.id for group in made}
        for box in view._checkboxes.values():
            box.text()  # a deleted C++ object raises here


class TestTheWordingTabsAreNotRebuiltForNothing:
    def test_unchanged_tabs_are_kept(self, stocked):
        app, _ = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        before = widgets_in(view.tab_bar)
        assert before

        view.refresh_tabs()
        assert widgets_in(view.tab_bar) == before

    def test_a_new_selection_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        before = widgets_in(view.tab_bar)

        app.set_group_selected(made[2].id, True)
        view.refresh_tabs()
        after = widgets_in(view.tab_bar)
        assert after != before
        assert len(after) == len(before) + 1

    def test_a_renamed_group_relabels_its_tab(self, stocked):
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        before = [button.text() for button in widgets_in(view.tab_bar)]

        app.group_repo.set_name(made[0].id, "Totally New Name")
        view.refresh_tabs()
        after = [button.text() for button in widgets_in(view.tab_bar)]
        assert after != before, "the strip went stale after a rename"


class TestTheActiveWordingTabStaysMarked:
    """The filled tab is the only thing on screen saying whose words are in the
    editor -- and typing into the base text clears every per-group rewrite, so
    a strip pointing at the wrong tab is how someone loses wording they had
    already written.

    None of these three change a single tab *label*, which is what the rebuild
    guard is keyed on, so none of them can be fixed by that key: the fill has
    to be restated outside it.
    """

    def marks(self, view) -> list[bool]:
        return [button.isChecked() for button in widgets_in(view.tab_bar)]

    def expected(self, view) -> list[bool]:
        return [target == view._editing for target, _label in view._tab_plan()]

    def test_switching_tabs_moves_the_highlight(self, stocked):
        """Switching tabs changes `_editing` and nothing else, so the guard
        skipped the rebuild and the highlight stayed on the tab just left --
        the editor holding one group's words under another group's name."""
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        assert self.marks(view) == self.expected(view)

        view.select_tab(made[0].id)
        assert view._editing == made[0].id
        assert self.marks(view) == self.expected(view), "the highlight went stale"

        view.select_tab(None)
        assert self.marks(view) == self.expected(view)

    def test_clicking_a_second_tab_does_not_leave_two_lit(self, stocked):
        """These buttons are checkable and in no exclusive group, so Qt checks
        the clicked one before select_tab is ever reached. With the rebuild
        skipped, the tab left behind stayed lit as well."""
        app, _ = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        widgets_in(view.tab_bar)[1].click()  # the first group's tab
        assert self.marks(view) == self.expected(view)
        assert self.marks(view).count(True) == 1, "two tabs were lit at once"

    def test_clicking_the_tab_already_open_keeps_it_lit(self, stocked):
        """Clicking the tab you are already on is a normal thing to do, and Qt
        toggles a checked button *off*. Nothing about the strip's contents
        changed, so nothing put it back: no tab was lit at all."""
        app, made = stocked
        view = app.views["compose"]
        view.refresh_tabs()
        view.select_tab(made[0].id)

        widgets_in(view.tab_bar)[1].click()  # the same tab again
        assert view._editing == made[0].id
        assert self.marks(view).count(True) == 1, "the strip lost its highlight"
        assert self.marks(view) == self.expected(view)


class TestPublishRecipientsAreNotRebuiltForNothing:
    def test_unchanged_recipients_are_kept(self, stocked):
        app, _ = stocked
        view = app.views["publish"]
        view.refresh_recipients()
        before = widgets_in(view.recipient_box)
        assert before

        view.refresh_recipients()
        assert widgets_in(view.recipient_box) == before

    def test_a_changed_selection_rebuilds(self, stocked):
        app, made = stocked
        view = app.views["publish"]
        view.refresh_recipients()
        before = widgets_in(view.recipient_box)

        app.set_group_selected(made[3].id, True)
        view.refresh_recipients()
        assert widgets_in(view.recipient_box) != before

    def test_the_snippet_still_updates_when_the_rows_do_not(self, stocked):
        """The body can change without the recipient list changing at all, so
        the snippet is deliberately outside the guard."""
        app, _ = stocked
        view = app.views["publish"]
        compose = app.views["compose"]
        compose.editor.setPlainText("first wording entirely")
        compose.capture()
        view.refresh_recipients()
        first = view.preview_note.text()

        compose.editor.setPlainText("second wording entirely")
        compose.capture()
        view.refresh_recipients()
        assert view.preview_note.text() != first, "the snippet went stale"


class TestNothingSlowRunsOnTheDrawingThread:
    def test_the_debug_port_is_probed_in_the_background(self):
        """chrome.probe() is a synchronous HTTP call to the debug port: 14ms
        when Chrome answers, and up to PROBE_TIMEOUT_S of a frozen window when
        something holds the port without replying. The Groups screen ran it on
        every show."""
        from fbposter.qtui.views import groups

        source = inspect.getsource(groups.GroupsView._fetch_missing_names)
        head, marker, tail = source.partition("def work()")
        assert marker, "the background worker went away"
        assert "chrome.probe" not in head, "the probe still runs before the thread"
        assert "chrome.probe" in tail, "the probe guard was dropped entirely"
