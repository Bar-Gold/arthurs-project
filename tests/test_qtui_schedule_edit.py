"""Tests for editing a repeating post on its own screen.

Before this screen existed a repeating post could only be paused or deleted:
its groups, wordings and times were fixed the moment it was created, so the
only way to change one was to delete it and start again.

The decisions pinned here were the user's, and each is a place an edit could
quietly do more than it was asked to:

* the Compose draft and the group ticks are never touched by an edit;
* a batch already queued goes out as it was -- only later runs change;
* saving never pauses or resumes;
* a group removed from the Groups list stays in the post;
* the rules are judged again, but only a rule the post was not already allowed
  to break is asked about, and one it no longer breaks is dropped.

Views are driven through their own methods, offscreen, as elsewhere.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QPushButton

from fbposter import clock, recurrence
from fbposter.db.models import (
    SCHEDULE_ACTIVE,
    SCHEDULE_PAUSED,
    TARGET_DONE,
    utcnow,
)
from fbposter.qtui.views.publish import REPEAT

WORDINGS = ["Selling a road bike", "Road bike for sale", "54cm road bike going"]
DRAFT = "Something else entirely, still being written."


@pytest.fixture
def groups(qt_app):
    made = [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
        for index in range(3)
    ]
    # Judged in Israel time; the suite has to pass at 03:00 as well as noon.
    qt_app.settings_repo.set("posting_window_start_hour", 0)
    qt_app.settings_repo.set("posting_window_end_hour", 0)
    return made


def make_schedule(qt_app, group_ids, *, times=("09:00",), overrides=(), **extra):
    rule = recurrence.build(list(times))
    fields = dict(
        name="Bikes",
        bodies=list(WORDINGS),
        group_ids=list(group_ids),
        times=list(rule.times),
        next_run_at=recurrence.next_occurrence(rule, utcnow()),
        overrides=overrides,
    )
    fields.update(extra)
    return qt_app.schedule_repo.create(**fields)


@pytest.fixture
def editing(qt_app, groups):
    """A post to groups 0 and 1, open on the edit screen."""
    schedule = make_schedule(qt_app, [groups[0].id, groups[1].id])
    publish = qt_app.views["publish"]
    qt_app.show_view("publish")
    publish.set_mode(REPEAT)
    assert publish.edit_schedule(schedule.id) is True
    return qt_app.views["schedule_edit"], schedule


def stored(qt_app, schedule):
    return qt_app.schedule_repo.get(schedule.id)


def set_time(editor, index, text):
    editor._time_rows[index].setTime(QTime.fromString(text, "HH:mm"))


class TestReachingIt:
    def test_every_repeating_post_card_has_an_edit_button(self, qt_app, groups):
        make_schedule(qt_app, [groups[0].id])
        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        publish.set_mode(REPEAT)
        texts = [b.text() for b in publish.schedule_area.findChildren(QPushButton)]
        assert "Edit" in texts

    def test_edit_opens_the_screen(self, qt_app, editing):
        assert qt_app.current_view == "schedule_edit"

    def test_it_is_not_a_sidebar_step(self, qt_app):
        assert "schedule_edit" not in qt_app.nav_buttons

    def test_a_post_that_has_gone_is_not_opened(self, qt_app, groups):
        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        assert publish.edit_schedule(9999) is False
        assert qt_app.current_view == "publish"
        assert "no longer exists" in qt_app.toast_label.text()

    def test_it_has_one_accent_button_and_it_is_save(self, editing):
        editor, _schedule = editing
        primaries = [
            b.text() for b in editor.findChildren(QPushButton)
            if b.objectName() == "Primary"
        ]
        assert primaries == ["Save changes"]


class TestItOpensFilledIn:
    def test_the_name(self, editing):
        editor, _schedule = editing
        assert editor.name_entry.text() == "Bikes"

    def test_the_wordings(self, editing):
        editor, _schedule = editing
        assert editor.wordings() == WORDINGS

    def test_the_groups(self, editing, groups):
        editor, _schedule = editing
        assert editor.ticked_group_ids() == [groups[0].id, groups[1].id]

    def test_the_times_and_days(self, qt_app, groups):
        schedule = make_schedule(
            qt_app, [groups[0].id], times=("09:00", "20:00"), days=[0, 3]
        )
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)
        assert editor.times() == ["09:00", "20:00"]
        assert editor.days() == [0, 3]

    def test_every_day_ticks_all_seven(self, editing):
        editor, _schedule = editing
        assert editor.days() == list(range(7))

    def test_the_pictures(self, qt_app, groups):
        schedule = make_schedule(
            qt_app, [groups[0].id], media_paths=["C:\\pictures\\bike.jpg"]
        )
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)
        assert editor.pictures() == ["C:\\pictures\\bike.jpg"]


class TestSavingChangesIt:
    def test_the_wordings(self, qt_app, editing):
        editor, schedule = editing
        editor._wordings[0].setPlainText("A fresh way of saying it")
        editor.add_wording("And another")
        assert editor.save() is True
        assert stored(qt_app, schedule).bodies == [
            "A fresh way of saying it", WORDINGS[1], WORDINGS[2], "And another"
        ]

    def test_a_removed_wording_goes(self, qt_app, editing):
        editor, schedule = editing
        holder = editor.wording_box.itemAt(0).widget()
        editor._remove_wording(holder, editor._wordings[0])
        assert editor.save() is True
        assert stored(qt_app, schedule).bodies == WORDINGS[1:]

    def test_invisible_characters_are_cleaned(self, qt_app, editing):
        editor, schedule = editing
        editor._wordings[0].setPlainText("\u200fSelling a gravel bike\u200e")
        editor.save()
        assert stored(qt_app, schedule).bodies[0] == "Selling a gravel bike"

    def test_the_groups(self, qt_app, editing, groups):
        """Kept groups keep their place; a new one goes on the end."""
        editor, schedule = editing
        editor._group_boxes[groups[0].id].setChecked(False)
        editor._group_boxes[groups[2].id].setChecked(True)
        assert editor.save() is True
        assert stored(qt_app, schedule).group_ids == [groups[1].id, groups[2].id]

    def test_the_name(self, qt_app, editing):
        editor, schedule = editing
        editor.name_entry.setText("Road bikes")
        editor.save()
        assert stored(qt_app, schedule).name == "Road bikes"

    def test_the_times_and_days(self, qt_app, editing):
        editor, schedule = editing
        set_time(editor, 0, "10:30")
        for index, box in enumerate(editor._day_boxes):
            box.setChecked(index in (1, 5))
        assert editor.save() is True
        after = stored(qt_app, schedule)
        assert after.times == ["10:30"]
        assert after.days == [1, 5]

    def test_the_pictures(self, qt_app, editing):
        editor, schedule = editing
        editor.add_pictures(["C:\\pictures\\one.jpg", "C:\\pictures\\two.jpg"])
        editor.remove_picture("C:\\pictures\\one.jpg")
        editor.save()
        assert stored(qt_app, schedule).media_paths == ["C:\\pictures\\two.jpg"]

    def test_it_returns_to_publish_and_says_so(self, qt_app, editing):
        editor, _schedule = editing
        editor.save()
        assert qt_app.current_view == "publish"
        assert "saved" in qt_app.toast_label.text()


class TestTheNextRun:
    def test_a_new_time_gets_a_new_slot(self, qt_app, editing):
        editor, schedule = editing
        set_time(editor, 0, "10:30")
        editor.save()
        after = stored(qt_app, schedule).next_run_at
        assert after > utcnow()
        assert clock.to_local(after).strftime("%H:%M") == "10:30"

    def test_unchanged_times_keep_the_slot_already_set(self, qt_app, editing):
        """The worker may have moved it on since the screen opened."""
        editor, schedule = editing
        moved = utcnow() + timedelta(hours=5, minutes=7)
        qt_app.schedule_repo.set_next_run(schedule.id, moved)
        editor._wordings[0].setPlainText("Different words, same times")
        editor.save()
        assert stored(qt_app, schedule).next_run_at == moved

    def test_the_rotation_is_not_restarted(self, qt_app, editing):
        editor, schedule = editing
        qt_app.schedule_repo.record_run(schedule.id, utcnow(), schedule.next_run_at)
        editor.save()
        assert stored(qt_app, schedule).run_count == 1


class TestWhatItLeavesAlone:
    def test_the_compose_draft_and_the_group_ticks(self, qt_app, editing, groups):
        compose = qt_app.views["compose"]
        compose._show(DRAFT)
        compose.capture()
        qt_app.selected_groups = {groups[2].id}

        editor, _schedule = editing
        editor.load(_schedule.id)
        editor._wordings[0].setPlainText("Edited")
        editor.save()

        assert compose.base_body() == DRAFT
        assert qt_app.selected_groups == {groups[2].id}

    def test_a_paused_post_stays_paused(self, qt_app, editing):
        editor, schedule = editing
        qt_app.schedule_repo.set_state(schedule.id, SCHEDULE_PAUSED)
        editor.load(schedule.id)
        assert "Saving keeps it paused" in editor.summary.text()
        set_time(editor, 0, "10:30")
        editor.save()
        after = stored(qt_app, schedule)
        assert after.state == SCHEDULE_PAUSED
        # Resume finds the next slot; saving a paused post does not.
        assert after.next_run_at == schedule.next_run_at

    def test_an_active_post_stays_active(self, qt_app, editing):
        editor, schedule = editing
        editor.save()
        assert stored(qt_app, schedule).state == SCHEDULE_ACTIVE

    def test_a_batch_already_queued(self, qt_app, editing, groups):
        editor, schedule = editing
        task = qt_app.task_repo.create(
            WORDINGS[0],
            [(groups[0].id, WORDINGS[0]), (groups[1].id, WORDINGS[1])],
            schedule_id=schedule.id,
        )
        editor._wordings[0].setPlainText("Edited")
        editor._group_boxes[groups[0].id].setChecked(False)
        editor.save()

        targets = qt_app.task_repo.targets_for(task.id)
        assert [(t.group_id, t.body) for t in targets] == [
            (groups[0].id, WORDINGS[0]), (groups[1].id, WORDINGS[1])
        ]

    def test_a_group_removed_from_the_list_stays_in_the_post(self, qt_app, groups):
        schedule = make_schedule(qt_app, [g.id for g in groups])
        qt_app.group_repo.remove(groups[2].id)
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)

        assert groups[2].id not in editor._group_boxes
        assert not editor.removed_note.isHidden()
        editor._group_boxes[groups[0].id].setChecked(False)
        editor.save()
        assert stored(qt_app, schedule).group_ids == [groups[1].id, groups[2].id]

    def test_no_removed_group_no_note(self, editing):
        editor, _schedule = editing
        assert editor.removed_note.isHidden()


class TestBackWithoutSaving:
    def test_it_writes_nothing(self, qt_app, editing, groups):
        editor, schedule = editing
        before = stored(qt_app, schedule)
        editor.name_entry.setText("Changed")
        editor._wordings[0].setPlainText("Changed")
        editor._group_boxes[groups[2].id].setChecked(True)
        set_time(editor, 0, "10:30")
        editor.back_button.click()

        assert qt_app.current_view == "publish"
        assert stored(qt_app, schedule) == before

    def test_opening_it_again_starts_from_what_is_stored(self, qt_app, editing):
        editor, schedule = editing
        editor.name_entry.setText("Changed")
        editor.leave()
        qt_app.views["publish"].edit_schedule(schedule.id)
        assert editor.name_entry.text() == "Bikes"


class TestWhatIsRefused:
    def test_no_wording(self, qt_app, editing):
        editor, schedule = editing
        for text_edit in editor._wordings:
            text_edit.setPlainText("   ")
        assert editor.save() is False
        assert stored(qt_app, schedule).bodies == WORDINGS
        assert qt_app.current_view == "schedule_edit"

    def test_pictures_but_no_wording(self, qt_app, editing):
        editor, _schedule = editing
        editor.add_pictures(["C:\\pictures\\bike.jpg"])
        for text_edit in editor._wordings:
            text_edit.setPlainText("")
        assert editor.save() is False
        assert "text as well as pictures" in qt_app.toast_label.text()

    def test_no_group(self, qt_app, editing):
        editor, schedule = editing
        for box in editor._group_boxes.values():
            box.setChecked(False)
        assert editor.save() is False
        assert len(stored(qt_app, schedule).group_ids) == 2

    def test_no_day(self, qt_app, editing):
        editor, schedule = editing
        for box in editor._day_boxes:
            box.setChecked(False)
        assert editor.save() is False
        assert stored(qt_app, schedule).days == []

    def test_times_stop_at_the_ceiling(self, editing):
        editor, _schedule = editing
        assert editor.add_time("12:00")
        assert editor.add_time("18:00")
        assert editor.add_time("21:00") is False

    def test_a_post_deleted_meanwhile(self, qt_app, editing):
        editor, schedule = editing
        qt_app.schedule_repo.delete(schedule.id)
        assert editor.save() is False
        assert qt_app.current_view == "publish"


class TestTheRules:
    def test_a_newly_broken_rule_offers_post_anyway(self, qt_app, editing):
        editor, schedule = editing
        editor.add_time("12:00")  # three hours after 09:00, inside the 8h cooldown
        assert editor.save() is False
        assert not editor.override_card.isHidden()
        assert editor.save_button.isHidden()
        assert "3h apart" in editor.override_list.text()
        assert stored(qt_app, schedule).times == ["09:00"]

        assert editor.post_anyway() is True
        after = stored(qt_app, schedule)
        assert after.times == ["09:00", "12:00"]
        assert "cooldown" in after.overrides

    def test_cancel_saves_nothing(self, qt_app, editing):
        editor, schedule = editing
        editor.add_time("12:00")
        editor.save()
        editor.keep_rules_button.click()
        assert editor.override_card.isHidden()
        assert stored(qt_app, schedule).times == ["09:00"]

    def test_a_rule_already_allowed_is_not_asked_again(self, qt_app, groups):
        schedule = make_schedule(
            qt_app, [groups[0].id], times=("09:00", "12:00"), overrides={"cooldown"}
        )
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)
        editor._wordings[0].setPlainText("Edited")
        assert editor.save() is True
        assert editor.override_card.isHidden()
        assert stored(qt_app, schedule).overrides == frozenset({"cooldown"})

    def test_a_rule_no_longer_broken_is_dropped(self, qt_app, groups):
        schedule = make_schedule(
            qt_app, [groups[0].id], times=("09:00", "12:00"), overrides={"cooldown"}
        )
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)
        set_time(editor, 1, "21:00")
        assert editor.save() is True
        assert stored(qt_app, schedule).overrides == frozenset()

    def test_a_group_that_has_had_every_wording_is_caught(self, qt_app, editing, groups):
        """Adding a group is judged against its own history, like creating."""
        editor, schedule = editing
        for body in WORDINGS:
            task = qt_app.task_repo.create(body, [(groups[2].id, body)])
            target = qt_app.task_repo.targets_for(task.id)[0]
            qt_app.task_repo.claim_target(target.id)
            qt_app.task_repo.mark_target(target.id, TARGET_DONE)
        editor._group_boxes[groups[2].id].setChecked(True)
        assert editor.save() is False
        assert "every one of these wordings" in editor.override_list.text()
        assert groups[2].id not in stored(qt_app, schedule).group_ids

    def test_editing_withdraws_the_offer(self, editing):
        editor, _schedule = editing
        editor.add_time("12:00")
        editor.save()
        assert not editor.override_card.isHidden()
        set_time(editor, 1, "21:00")
        assert editor.override_card.isHidden()
        assert not editor.save_button.isHidden()

    def test_the_summary_says_so_before_save_is_pressed(self, editing):
        editor, _schedule = editing
        editor.add_time("12:00")
        assert "3h apart" in editor.summary.text()
