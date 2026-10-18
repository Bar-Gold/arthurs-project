"""Tests for group sets on screen: the Group sets screen, and picking a set.

The user's choices (2026-10-15), each pinned here:

* sets are made on a screen of their own, in the sidebar;
* a set is picked whole, on Groups and on a repeating post's edit screen, and
  the groups it brings are shown ticked and greyed, never unticked one by one;
* picking a set adds to the groups already ticked;
* Now and Once take the set's groups as they are; a repeating post follows the
  set itself;
* saving a set that posts follow is never held up: what it newly breaks is
  said afterwards;
* deleting a set leaves its groups in the posts that used it.

Views are driven through their own methods, offscreen, as elsewhere.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from fbposter import recurrence
from fbposter.db.models import utcnow
from fbposter.guards import DAILY_CAP
from fbposter.qtui.views.publish import NOW, REPEAT

BODY = "Selling a road bike, 54cm frame."
WORDINGS = ["Selling a road bike", "Road bike for sale", "54cm road bike going"]


@pytest.fixture
def groups(qt_app):
    made = [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
        for index in range(4)
    ]
    # Judged in Israel time; the suite has to pass at 03:00 as well as noon.
    qt_app.settings_repo.set("posting_window_start_hour", 0)
    qt_app.settings_repo.set("posting_window_end_hour", 0)
    return made


@pytest.fixture
def screen(qt_app, groups):
    qt_app.show_view("group_sets")
    return qt_app.views["group_sets"]


@pytest.fixture
def groups_view(qt_app, groups):
    qt_app.show_view("groups")
    return qt_app.views["groups"]


def make_set(qt_app, name, members):
    return qt_app.group_set_repo.create(name, [g.id for g in members])


def make_schedule(qt_app, *, group_ids=(), set_ids=(), overrides=()):
    rule = recurrence.build(["09:00"])
    return qt_app.schedule_repo.create(
        name="Bikes",
        bodies=list(WORDINGS),
        group_ids=list(group_ids),
        set_ids=list(set_ids),
        times=list(rule.times),
        next_run_at=recurrence.next_occurrence(rule, utcnow()),
        overrides=overrides,
    )


def labels(widget) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel)]


class TestItsPlace:
    def test_it_is_in_the_sidebar_but_not_a_step(self, qt_app):
        from fbposter.qtui.app import FLOW_STEPS, NAV_ITEMS

        keys = [key for key, _label, _view in NAV_ITEMS]
        assert keys.index("group_sets") == keys.index("dashboard") + 1
        assert "group_sets" not in FLOW_STEPS
        assert qt_app.nav_buttons["group_sets"].text() == "Group sets"

    def test_with_no_sets_it_says_how_to_make_one(self, screen):
        assert any("No group sets yet" in text for text in labels(screen.list_page))


class TestMakingASet:
    def test_new_set_opens_the_editor_with_nothing_ticked(self, screen):
        assert screen.open_editor() is True
        assert screen.editor_page.isVisibleTo(screen)
        assert not screen.list_page.isVisibleTo(screen)
        assert screen.ticked_group_ids() == []

    def test_saving_one(self, qt_app, screen, groups):
        screen.open_editor()
        screen.name_entry.setText("Tel Aviv")
        screen.tick([groups[0].id, groups[2].id])
        assert screen.save() is True

        (stored,) = qt_app.group_set_repo.list()
        assert stored.name == "Tel Aviv"
        assert stored.group_ids == [groups[0].id, groups[2].id]
        assert screen.list_page.isVisibleTo(screen)
        assert "Tel Aviv saved: 2 groups." in qt_app.toast_label.text()

    def test_a_set_needs_a_name(self, qt_app, screen, groups):
        screen.open_editor()
        screen.tick([groups[0].id])
        assert screen.save() is False
        assert "needs a name" in qt_app.toast_label.text()
        assert screen.editor_page.isVisibleTo(screen)
        assert qt_app.group_set_repo.list() == []

    def test_cancel_writes_nothing(self, qt_app, screen, groups):
        screen.open_editor()
        screen.name_entry.setText("Tel Aviv")
        screen.tick([groups[0].id])
        screen.close_editor()
        assert qt_app.group_set_repo.list() == []

    def test_the_count_follows_the_ticks(self, screen, groups):
        screen.open_editor()
        screen.tick([groups[0].id, groups[1].id])
        assert screen.ticked_label.text() == "2 groups ticked"

    def test_the_search_hides_rows_but_keeps_their_ticks(self, screen, groups):
        screen.open_editor()
        screen.tick([groups[0].id])
        screen.search.setText("Demo 1")
        assert not screen._editor_rows[groups[0].id].isVisibleTo(screen)
        assert screen._editor_rows[groups[1].id].isVisibleTo(screen)
        assert screen.ticked_group_ids() == [groups[0].id]


class TestChangingASet:
    def test_edit_ticks_its_groups_and_saves_the_change(self, qt_app, screen, groups):
        made = make_set(qt_app, "Tel Aviv", groups[:2])
        screen.refresh()
        assert screen.open_editor(made.id) is True
        assert screen.ticked_group_ids() == [groups[0].id, groups[1].id]
        screen.tick([groups[1].id], on=False)
        screen.tick([groups[3].id])
        assert screen.save() is True
        assert qt_app.group_set_repo.get(made.id).group_ids == [groups[0].id, groups[3].id]

    def test_a_card_names_its_groups_and_the_posts_that_use_it(self, qt_app, screen, groups):
        made = make_set(qt_app, "Tel Aviv", groups[:2])
        make_schedule(qt_app, set_ids=[made.id])
        screen.refresh()
        texts = labels(screen.list_page)
        assert "Demo 0, Demo 1" in texts
        assert "Used by Bikes." in texts

    def test_a_rule_newly_broken_is_named_after_saving(self, qt_app, screen, groups):
        """The user chose to be told after, not asked first."""
        qt_app.settings_repo.set("daily_cap", 2)
        made = make_set(qt_app, "Tel Aviv", groups[:1])
        make_schedule(qt_app, set_ids=[made.id])
        screen.refresh()
        screen.open_editor(made.id)
        screen.tick([groups[1].id, groups[2].id])
        assert screen.save() is True

        text = qt_app.toast_label.text()
        assert "Bikes:" in text
        assert "daily limit of 2" in text
        assert qt_app.group_set_repo.get(made.id).group_ids == [g.id for g in groups[:3]]

    def test_post_anyway_is_said_to_cover_the_new_groups(self, qt_app, screen, groups):
        made = make_set(qt_app, "Tel Aviv", groups[:1])
        make_schedule(qt_app, set_ids=[made.id], overrides=[DAILY_CAP])
        screen.refresh()
        screen.open_editor(made.id)
        screen.tick([groups[1].id])
        screen.save()
        assert "covers the groups added to this set too" in qt_app.toast_label.text()

    def test_a_change_that_breaks_nothing_new_is_plain(self, qt_app, screen, groups):
        made = make_set(qt_app, "Tel Aviv", groups[:1])
        make_schedule(qt_app, set_ids=[made.id])
        screen.refresh()
        screen.open_editor(made.id)
        screen.tick([groups[1].id])
        screen.save()
        assert qt_app.toast_label.text() == "Tel Aviv saved: 2 groups."


class TestDeletingASet:
    def test_the_posts_keep_its_groups_and_undo_brings_it_back(self, qt_app, screen, groups):
        made = make_set(qt_app, "Tel Aviv", groups[:2])
        post = make_schedule(qt_app, set_ids=[made.id])
        screen.refresh()

        screen.delete_set(made.id)
        assert qt_app.group_set_repo.get(made.id) is None
        assert qt_app.schedule_repo.get(post.id).group_ids == [groups[0].id, groups[1].id]
        assert "keeps its groups" in qt_app.toast_label.text()

        qt_app.banner.undo_button.click()
        assert qt_app.group_set_repo.get(made.id).group_ids == [groups[0].id, groups[1].id]
        assert qt_app.schedule_repo.get(post.id).set_ids == [made.id]
        assert qt_app.schedule_repo.get(post.id).group_ids == []

    def test_a_picked_set_is_unpicked_when_deleted(self, qt_app, screen, groups):
        made = make_set(qt_app, "Tel Aviv", groups[:2])
        qt_app.set_set_selected(made.id, True)
        screen.delete_set(made.id)
        assert qt_app.selected_sets == set()


class TestPickingASetOnGroups:
    def test_the_sets_are_hidden_until_one_exists(self, qt_app, groups_view, groups):
        assert not groups_view.sets_section.isVisibleTo(groups_view)
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        assert groups_view.sets_section.isVisibleTo(groups_view)
        assert groups_view._set_boxes[made.id].text() == "Tel Aviv (2)"

    def test_picking_one_adds_its_groups_to_the_ticked_ones(self, qt_app, groups_view, groups):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        groups_view._checkboxes[groups[0].id].setChecked(True)
        groups_view._set_boxes[made.id].setChecked(True)
        assert qt_app.selected_group_ids() == [g.id for g in groups[:3]]
        assert groups_view.count_label.text() == "3 of 4 selected"

    def test_its_groups_are_greyed_and_name_the_set(self, qt_app, groups_view, groups):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        groups_view._set_boxes[made.id].setChecked(True)
        box = groups_view._checkboxes[groups[1].id]
        assert box.isChecked()
        assert not box.isEnabled()
        assert "in Tel Aviv" in labels(groups_view._rows[groups[1].id])
        assert groups_view._checkboxes[groups[0].id].isEnabled()

    def test_picking_does_not_rebuild_the_set_boxes(self, qt_app, groups_view, groups):
        """The box clicked would otherwise be destroyed under the click."""
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        box = groups_view._set_boxes[made.id]
        box.setChecked(True)
        assert groups_view._set_boxes[made.id] is box

    def test_unpicking_gives_the_rows_back_their_own_ticks(self, qt_app, groups_view, groups):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        groups_view._checkboxes[groups[1].id].setChecked(True)
        groups_view._set_boxes[made.id].setChecked(True)
        groups_view._set_boxes[made.id].setChecked(False)
        assert groups_view._checkboxes[groups[1].id].isChecked()
        assert groups_view._checkboxes[groups[1].id].isEnabled()
        assert not groups_view._checkboxes[groups[2].id].isChecked()
        assert qt_app.selected_group_ids() == [groups[1].id]

    def test_clear_unpicks_the_sets_too(self, qt_app, groups_view, groups):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        groups_view._set_boxes[made.id].setChecked(True)
        groups_view.set_all(False)
        assert qt_app.selected_sets == set()
        assert qt_app.selected_group_ids() == []
        assert not groups_view._set_boxes[made.id].isChecked()

    def test_a_click_on_a_greyed_row_leaves_it_ticked(
        self, qt_application, qt_app, groups_view, groups
    ):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        groups_view._set_boxes[made.id].setChecked(True)
        qt_app.resize(1000, 700)
        qt_app.show()
        qt_application.processEvents()
        try:
            row = groups_view._rows[groups[1].id]
            (note,) = [w for w in row.findChildren(QLabel) if w.text() == "in Tel Aviv"]
            point = note.geometry().center()
            target = row.childAt(point) or row
            QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, target.mapFrom(row, point))
            qt_application.processEvents()
            assert groups[1].id in qt_app.selected_group_ids()
            assert groups[1].id not in qt_app.selected_groups
        finally:
            qt_app.hide()

    def test_a_removed_group_leaves_the_set_and_the_toast_says_so(
        self, qt_app, groups_view, groups
    ):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        groups_view.refresh()
        groups_view.remove_group(groups[1].id)
        assert qt_app.group_set_repo.get(made.id).group_ids == [groups[2].id]
        assert "1 group set" in qt_app.toast_label.text()


@pytest.fixture
def ready(qt_app, groups):
    """A set of groups 1 and 2 picked on Groups, group 0 ticked by hand, and
    the post written on Compose."""
    made = make_set(qt_app, "Tel Aviv", groups[1:3])
    view = qt_app.views["groups"]
    qt_app.show_view("groups")
    view._checkboxes[groups[0].id].setChecked(True)
    view._set_boxes[made.id].setChecked(True)
    compose = qt_app.views["compose"]
    qt_app.show_view("compose")
    compose._show(BODY)
    compose.capture()
    qt_app.show_view("publish")
    return made, qt_app.views["publish"]


class TestPublishingWithASet:
    def test_post_now_goes_to_the_sets_groups_as_they_are(self, qt_app, ready, groups):
        _made, publish = ready
        publish.set_mode(NOW)
        assert publish.publish() is True
        (task,) = qt_app.task_repo.list_recent()
        targets = [t.group_id for t in qt_app.task_repo.targets_for(task.id)]
        assert targets == [g.id for g in groups[:3]]

    def test_a_repeating_post_stores_the_set_not_its_groups(self, qt_app, ready, groups):
        made, publish = ready
        publish.set_mode(REPEAT)
        publish.add_wording("Second wording")
        assert publish.publish() is True
        (stored,) = qt_app.schedule_repo.list()
        assert stored.group_ids == [groups[0].id]
        assert stored.set_ids == [made.id]
        assert stored.recipient_ids == [g.id for g in groups[:3]]

    def test_going_out_names_the_set(self, qt_app, ready):
        _made, publish = ready
        publish.refresh_recipients()
        assert "Group sets: Tel Aviv" in labels(publish.recipients)

    def test_the_repeat_summary_says_the_post_follows_it(self, ready):
        _made, publish = ready
        publish.set_mode(REPEAT)
        assert "Follows Tel Aviv" in publish.summary.text()

    def test_the_card_counts_the_set(self, qt_app, ready):
        _made, publish = ready
        publish.set_mode(REPEAT)
        publish.add_wording("Second wording")
        publish.publish()
        assert any(
            "1 group set · 3 groups" in text for text in labels(publish.schedule_area)
        )


class TestTheEditScreen:
    @pytest.fixture
    def editing(self, qt_app, groups):
        made = make_set(qt_app, "Tel Aviv", groups[1:3])
        schedule = make_schedule(qt_app, group_ids=[groups[0].id], set_ids=[made.id])
        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        publish.set_mode(REPEAT)
        assert publish.edit_schedule(schedule.id) is True
        return qt_app.views["schedule_edit"], schedule, made

    def test_its_set_is_ticked_and_its_groups_greyed(self, editing, groups):
        editor, _schedule, made = editing
        assert editor._set_boxes[made.id].isChecked()
        box = editor._group_boxes[groups[1].id]
        assert box.isChecked() and not box.isEnabled()
        assert editor._group_notes[groups[1].id].text() == "in Tel Aviv"
        assert editor._group_boxes[groups[0].id].isEnabled()
        assert editor.ticked_group_ids() == [g.id for g in groups[:3]]

    def test_unticking_the_set_saves_only_the_post_s_own_groups(self, qt_app, editing, groups):
        editor, schedule, made = editing
        editor._set_boxes[made.id].setChecked(False)
        assert not editor._group_boxes[groups[1].id].isChecked()
        assert editor.save() is True
        stored = qt_app.schedule_repo.get(schedule.id)
        assert stored.set_ids == []
        assert stored.group_ids == [groups[0].id]

    def test_ticking_a_set_saves_the_link(self, qt_app, groups):
        other = make_set(qt_app, "Haifa", groups[3:])
        schedule = make_schedule(qt_app, group_ids=[groups[0].id])
        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        publish.set_mode(REPEAT)
        publish.edit_schedule(schedule.id)
        editor = qt_app.views["schedule_edit"]
        editor._set_boxes[other.id].setChecked(True)
        assert "Follows Haifa" in editor.summary.text()
        assert editor.save() is True
        stored = qt_app.schedule_repo.get(schedule.id)
        assert stored.set_ids == [other.id]
        assert stored.recipient_ids == [groups[0].id, groups[3].id]
