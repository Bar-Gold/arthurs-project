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
from PySide6.QtWidgets import QLabel, QPushButton

from fbposter import clock, recurrence
from fbposter.db.models import (
    SCHEDULE_ACTIVE,
    SCHEDULE_PAUSED,
    TARGET_DONE,
    utcnow,
)

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


def make_schedule(qt_app, group_ids, *, times=("09:00",), **extra):
    rule = recurrence.build(list(times))
    fields = dict(
        name="Bikes",
        bodies=list(WORDINGS),
        group_ids=list(group_ids),
        times=list(rule.times),
        next_run_at=recurrence.next_occurrence(rule, utcnow()),
    )
    fields.update(extra)
    return qt_app.schedule_repo.create(**fields)


@pytest.fixture
def editing(qt_app, groups):
    """A post to groups 0 and 1, open on the edit screen."""
    schedule = make_schedule(qt_app, [groups[0].id, groups[1].id])
    qt_app.show_view("repeating")
    assert qt_app.views["repeating"].edit_schedule(schedule.id) is True
    return qt_app.views["schedule_edit"], schedule


def stored(qt_app, schedule):
    return qt_app.schedule_repo.get(schedule.id)


def set_time(editor, index, text):
    editor._time_rows[index].setTime(QTime.fromString(text, "HH:mm"))


class TestReachingIt:
    def test_every_repeating_post_card_has_an_edit_button(self, qt_app, groups):
        make_schedule(qt_app, [groups[0].id])
        repeating = qt_app.views["repeating"]
        qt_app.show_view("repeating")
        texts = [b.text() for b in repeating.area.findChildren(QPushButton)]
        assert "Edit" in texts

    def test_edit_opens_the_screen(self, qt_app, editing):
        assert qt_app.current_view == "schedule_edit"

    def test_it_is_not_a_sidebar_step(self, qt_app):
        assert "schedule_edit" not in qt_app.nav_buttons

    def test_a_post_that_has_gone_is_not_opened(self, qt_app, groups):
        repeating = qt_app.views["repeating"]
        qt_app.show_view("repeating")
        assert repeating.edit_schedule(9999) is False
        assert qt_app.current_view == "repeating"
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


class TestAnEditCannotMakeAnExactCopy:
    """Only an exact copy of another repeating post is refused (the user's
    choice, 2026-10-20). The post being edited is never its own copy."""

    def test_saving_unchanged_is_not_a_copy_of_itself(self, editing):
        editor, _schedule = editing
        assert editor.save() is True

    def test_turning_it_into_another_post_is_refused(self, qt_app, groups, editing):
        editor, schedule = editing
        make_schedule(qt_app, [groups[0].id, groups[1].id], name="Evening", times=("21:00",))
        set_time(editor, 0, "21:00")
        assert editor.save() is False
        assert "Evening" in qt_app.toast_label.text()
        assert stored(qt_app, schedule).times == ["09:00"]

    def test_the_same_words_at_another_time_save(self, qt_app, groups, editing):
        editor, schedule = editing
        make_schedule(qt_app, [groups[0].id, groups[1].id], name="Evening", times=("21:00",))
        set_time(editor, 0, "18:00")
        assert editor.save() is True


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

    def test_it_returns_to_repeating_posts_and_says_so(self, qt_app, editing):
        editor, _schedule = editing
        editor.save()
        assert qt_app.current_view == "repeating"
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

    def test_a_group_removed_from_the_list_is_out_of_the_post(self, qt_app, groups):
        """Removing it took it out, and saving must not write it back."""
        schedule = make_schedule(qt_app, [g.id for g in groups])
        qt_app.group_repo.remove(groups[2].id)
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)

        assert groups[2].id not in editor._group_boxes
        editor._group_boxes[groups[0].id].setChecked(False)
        editor.save()
        assert stored(qt_app, schedule).group_ids == [groups[1].id]

    def test_adding_it_back_leaves_it_unticked(self, qt_app, groups):
        schedule = make_schedule(qt_app, [g.id for g in groups])
        qt_app.group_repo.remove(groups[2].id)
        qt_app.group_repo.add_from_url(groups[2].url)
        editor = qt_app.views["schedule_edit"]
        editor.load(schedule.id)

        assert not editor._group_boxes[groups[2].id].isChecked()


class TestBackWithoutSaving:
    def test_it_writes_nothing(self, qt_app, editing, groups):
        editor, schedule = editing
        before = stored(qt_app, schedule)
        editor.name_entry.setText("Changed")
        editor._wordings[0].setPlainText("Changed")
        editor._group_boxes[groups[2].id].setChecked(True)
        set_time(editor, 0, "10:30")
        editor.back_button.click()

        assert qt_app.current_view == "repeating"
        assert stored(qt_app, schedule) == before

    def test_opening_it_again_starts_from_what_is_stored(self, qt_app, editing):
        editor, schedule = editing
        editor.name_entry.setText("Changed")
        editor.leave()
        qt_app.views["repeating"].edit_schedule(schedule.id)
        assert editor.name_entry.text() == "Bikes"


class TestWhatIsRefused:
    def test_no_wording(self, qt_app, editing):
        editor, schedule = editing
        for text_edit in editor._wordings:
            text_edit.setPlainText("   ")
        assert editor.save() is False
        assert stored(qt_app, schedule).bodies == WORDINGS
        assert qt_app.current_view == "schedule_edit"

    def test_pictures_with_no_wording_are_saved(self, qt_app, editing):
        """Pictures alone may repeat (the user's choice, 2026-10-18)."""
        editor, schedule = editing
        editor.add_pictures(["C:\\pictures\\bike.jpg"])
        for text_edit in editor._wordings:
            text_edit.setPlainText("")
        assert editor.save() is True
        after = stored(qt_app, schedule)
        assert after.bodies == []
        assert after.media_paths == ["C:\\pictures\\bike.jpg"]

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

    def test_there_is_no_ceiling_on_times(self, qt_app, editing):
        editor, schedule = editing
        for text in ("12:00", "15:00", "18:00", "21:00"):
            assert editor.add_time(text)
        assert editor.save() is True
        assert stored(qt_app, schedule).times == [
            "09:00", "12:00", "15:00", "18:00", "21:00"
        ]

    def test_a_post_deleted_meanwhile(self, qt_app, editing):
        editor, schedule = editing
        qt_app.schedule_repo.delete(schedule.id)
        assert editor.save() is False
        assert qt_app.current_view == "repeating"


class TestThereAreNoRules:
    """Saving judges nothing (the user's choice, 2026-10-18): no "Post anyway"
    panel, no warnings, and nothing kept from the days when there was one."""

    def test_runs_three_hours_apart_save_straight_away(self, qt_app, editing):
        editor, schedule = editing
        editor.add_time("12:00")
        assert editor.save() is True
        assert stored(qt_app, schedule).times == ["09:00", "12:00"]

    def test_there_is_no_post_anyway_panel(self, editing):
        editor, _schedule = editing
        assert not hasattr(editor, "override_card")
        assert not hasattr(editor, "post_anyway")

    def test_a_group_that_has_had_every_wording_is_added(self, qt_app, editing, groups):
        editor, schedule = editing
        for body in WORDINGS:
            task = qt_app.task_repo.create(body, [(groups[2].id, body)])
            target = qt_app.task_repo.targets_for(task.id)[0]
            qt_app.task_repo.claim_target(target.id)
            qt_app.task_repo.mark_target(
                target.id, TARGET_DONE, posted=True, posted_at=utcnow() - timedelta(hours=1)
            )
        editor._group_boxes[groups[2].id].setChecked(True)
        assert editor.save() is True
        assert groups[2].id in stored(qt_app, schedule).group_ids

    def test_the_summary_warns_about_nothing(self, editing):
        from fbposter.qtui import theme

        editor, _schedule = editing
        editor.add_time("03:00")
        editor.add_time("03:30")
        assert theme.C["WARNING"] not in editor.summary.text()


class TestPreview:
    """Write and Preview, as in Create a post (the user's choice, 2026-10-21).
    A post with several wordings shows one at a time."""

    @staticmethod
    def picker(editor) -> list[str]:
        return [button.text() for button in editor._picker_group.buttons()]

    def test_it_opens_on_write(self, editing):
        editor, _schedule = editing
        assert editor.write_button.isChecked()
        assert not editor.write_page.isHidden()
        assert editor.preview_page.isHidden()

    def test_preview_shows_the_first_wording_in_place_of_the_column(self, editing):
        editor, _schedule = editing
        editor.preview_button.click()
        assert editor.write_page.isHidden()
        assert not editor.preview_page.isHidden()
        assert editor.preview.text_shown() == WORDINGS[0]
        # The right-hand card stays: the name, the times, Save.
        assert not editor.save_button.isHidden()

    def test_each_wording_can_be_picked(self, editing):
        editor, _schedule = editing
        editor.set_mode("preview")
        assert self.picker(editor) == ["Wording 1", "Wording 2", "Wording 3"]
        assert not editor.wording_picker.isHidden()

        editor._picker_group.buttons()[2].click()
        assert editor.preview.text_shown() == WORDINGS[2]
        lit = [button.isChecked() for button in editor._picker_group.buttons()]
        assert lit == [False, False, True]

    def test_one_wording_needs_no_picker(self, qt_app, groups):
        schedule = make_schedule(qt_app, [groups[0].id], bodies=["Only one"])
        qt_app.views["repeating"].edit_schedule(schedule.id)
        editor = qt_app.views["schedule_edit"]
        editor.set_mode("preview")
        assert editor.wording_picker.isHidden()
        assert editor.preview.text_shown() == "Only one"

    def test_it_shows_what_is_on_the_screen_not_what_is_saved(self, qt_app, editing):
        editor, schedule = editing
        editor._wordings[0].setPlainText("Typed, not saved")
        editor.set_mode("preview")
        assert editor.preview.text_shown() == "Typed, not saved"
        assert stored(qt_app, schedule).bodies == WORDINGS

    def test_it_is_headed_with_the_name_as_it_is_typed(self, editing):
        editor, _schedule = editing
        editor.set_mode("preview")
        editor.name_entry.setText("Road bikes")
        shown = [label.text() for label in editor.preview.findChildren(QLabel)]
        assert "Road bikes" in shown
        assert "Just now · 2 groups · wording 1 of 3" in shown

    def test_write_brings_the_column_back(self, editing):
        editor, _schedule = editing
        editor.set_mode("preview")
        editor.write_button.click()
        assert not editor.write_page.isHidden()
        assert editor.preview_page.isHidden()

    def test_the_next_post_opens_on_write_at_its_first_wording(self, qt_app, editing, groups):
        editor, _schedule = editing
        editor.set_mode("preview")
        editor.show_wording(2)
        other = make_schedule(qt_app, [groups[2].id], name="Other", bodies=["A", "B"])
        editor.leave()
        qt_app.views["repeating"].edit_schedule(other.id)
        assert editor.write_button.isChecked()
        editor.set_mode("preview")
        assert editor.preview.text_shown() == "A"
