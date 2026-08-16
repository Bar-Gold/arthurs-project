"""Tests for the Publish screen and the Compose split.

Compose owns what is written and who gets it; Publish owns nothing but when.
The tests that matter most are the ones that pin that seam: Compose no longer
carries any timing controls, and Publish reads Compose's *committed* state
rather than whatever happens to be in its editor.

Views are driven through their own methods rather than synthesised clicks, and
windows are offscreen -- this app must never take focus, including in its own
tests.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter import clock, recurrence
from fbposter.db.models import (
    SCHEDULE_ACTIVE,
    SCHEDULE_PAUSED,
    TASK_PENDING,
    utcnow,
)
from fbposter.qtui.views.publish import NOW, ONCE, REPEAT

HEBREW = "אני מוכר אופניים חשמליים Xiaomi M365 במחיר 1800 שקל"
BODY = "Selling a road bike, 54cm frame."


@pytest.fixture
def views(qt_app):
    """Compose and Publish, with three groups and the posting window open."""
    for index in range(3):
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
    # The window is judged in Israel time and the suite has to pass at 03:00
    # as readily as at noon.
    qt_app.settings_repo.set("posting_window_start_hour", 0)
    qt_app.settings_repo.set("posting_window_end_hour", 0)

    # Groups are chosen on the Groups screen now, not in Compose.
    groups = qt_app.views["groups"]
    qt_app.show_view("groups")
    groups.set_all(True)

    compose = qt_app.views["compose"]
    qt_app.show_view("compose")
    compose._show(BODY)
    compose.capture()

    publish = qt_app.views["publish"]
    qt_app.show_view("publish")
    return compose, publish


class TestComposeNoLongerDecidesWhen:
    def test_it_has_no_timing_controls_left(self, views):
        compose, _publish = views
        for gone in ("now_button", "later_button", "schedule_entry", "queue_button"):
            assert not hasattr(compose, gone), f"{gone} should have moved to Publish"

    def test_it_has_no_group_picker_left(self, views):
        compose, _publish = views
        assert not hasattr(compose, "_checkboxes"), "the picker moved to Groups"

    def test_it_still_owns_the_text_and_the_groups(self, views):
        compose, _publish = views
        assert compose.base_body() == BODY
        assert len(compose.selected_group_ids()) == 3

    def test_the_next_button_commits_and_moves_to_groups(self, qt_app, views):
        compose, _publish = views
        qt_app.show_view("compose")
        compose._show("edited after the last capture")
        compose.go_to_groups()
        assert qt_app.current_view == "groups"
        assert compose.base_body() == "edited after the last capture"

    def test_it_summarises_the_groups_chosen_elsewhere(self, qt_app, views):
        compose, _publish = views
        qt_app.show_view("compose")
        assert "3 groups" in compose.recipient_summary.text()
        assert "Demo 0" in compose.recipient_summary.text()


class TestTheThreeModes:
    def test_it_starts_on_post_now(self, views):
        _compose, publish = views
        assert publish.mode == NOW

    def test_switching_changes_the_button(self, views):
        _compose, publish = views
        publish.set_mode(ONCE)
        assert publish.go_button.text() == "Add to queue"
        publish.set_mode(REPEAT)
        assert publish.go_button.text() == "Start repeating"
        publish.set_mode(NOW)
        assert publish.go_button.text() == "Post now"

    def test_the_repeat_controls_only_show_in_repeat_mode(self, views):
        _compose, publish = views
        publish.set_mode(NOW)
        assert not publish.wording_area.isVisible()
        publish.set_mode(REPEAT)
        assert publish.wording_area.isVisibleTo(publish)

    def test_only_the_once_mode_produces_a_scheduled_time(self, views):
        _compose, publish = views
        publish.set_mode(NOW)
        assert publish.scheduled_for() is None
        publish.set_mode(REPEAT)
        assert publish.scheduled_for() is None
        publish.set_mode(ONCE)
        assert publish.scheduled_for() is not None


class TestPostNow:
    def test_it_queues_an_immediate_batch(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(NOW)
        assert publish.publish() is True

        tasks = qt_app.task_repo.list_recent()
        assert len(tasks) == 1
        assert tasks[0].scheduled_for is None
        assert tasks[0].state == TASK_PENDING
        assert len(qt_app.task_repo.targets_for(tasks[0].id)) == 3

    def test_it_uses_the_text_from_compose(self, qt_app, views):
        _compose, publish = views
        publish.publish()
        task = qt_app.task_repo.list_recent()[0]
        assert all(t.body == BODY for t in qt_app.task_repo.targets_for(task.id))

    def test_it_carries_per_group_rewrites(self, qt_app, views):
        compose, publish = views
        group_id = compose.selected_group_ids()[0]
        compose.select_tab(group_id)
        compose._show("A different wording for this one")
        compose.capture()

        publish.publish()
        task = qt_app.task_repo.list_recent()[0]
        bodies = {t.group_id: t.body for t in qt_app.task_repo.targets_for(task.id)}
        assert bodies[group_id] == "A different wording for this one"

    def test_no_groups_is_refused(self, qt_app, views):
        _compose, publish = views
        qt_app.views["groups"].set_all(False)
        assert publish.publish() is False
        assert qt_app.task_repo.list_recent() == []


class TestScheduleOnce:
    def test_it_queues_a_batch_for_the_chosen_time(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(ONCE)
        publish.schedule_entry.setDateTime(
            publish.schedule_entry.dateTime().addDays(1)
        )
        assert publish.publish() is True

        task = qt_app.task_repo.list_recent()[0]
        assert task.scheduled_for is not None
        assert task.scheduled_for > utcnow()

    def test_the_stored_time_matches_what_was_typed(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(ONCE)
        typed = publish.schedule_entry.dateTime().toString("yyyy-MM-dd HH:mm")
        publish.publish()
        stored = qt_app.task_repo.list_recent()[0].scheduled_for
        assert clock.format_local(stored) == typed


class TestRepeat:
    def test_the_compose_text_is_the_first_wording(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert publish.wordings() == [BODY]

    def test_alternates_are_added_after_it(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_wording("Another way of saying it")
        assert publish.wordings() == [BODY, "Another way of saying it"]

    def test_it_writes_a_schedule(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_wording("Second wording")
        publish.name_entry.setText("Bikes")
        assert publish.publish() is True

        stored = qt_app.schedule_repo.list()
        assert len(stored) == 1
        assert stored[0].name == "Bikes"
        assert stored[0].bodies == [BODY, "Second wording"]
        assert len(stored[0].group_ids) == 3
        assert stored[0].state == SCHEDULE_ACTIVE
        assert stored[0].next_run_at > utcnow()

    def test_it_writes_no_task(self, qt_app, views):
        """A schedule is a definition; the worker makes the batches."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        assert qt_app.task_repo.list_recent() == []

    def test_hebrew_survives_the_round_trip(self, qt_app, views):
        compose, publish = views
        compose._show(HEBREW)
        compose.capture()
        publish.set_mode(REPEAT)
        publish.publish()
        assert qt_app.schedule_repo.list()[0].bodies[0] == HEBREW

    def test_attachments_come_along(self, qt_app, views):
        from pathlib import Path

        compose, publish = views
        compose.attachments = [Path("C:/pictures/bike.jpg")]
        publish.set_mode(REPEAT)
        publish.publish()
        assert qt_app.schedule_repo.list()[0].media_paths == ["C:\\pictures\\bike.jpg"]

    def test_an_empty_compose_is_refused(self, qt_app, views):
        compose, publish = views
        compose._show("")
        compose.capture()
        publish.set_mode(REPEAT)
        assert publish.publish() is False
        assert qt_app.schedule_repo.list() == []

    def test_no_days_is_refused(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        for box in publish._day_boxes:
            box.setChecked(False)
        assert publish.publish() is False
        assert qt_app.schedule_repo.list() == []

    def test_times_stop_at_the_ceiling(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        while len(publish.times()) < recurrence.MAX_TIMES_PER_DAY:
            assert publish.add_time() is True
        assert publish.add_time() is False

    def test_three_times_a_day_reaches_the_database(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_time("14:00")
        publish.add_time("20:00")
        publish.publish()
        assert len(qt_app.schedule_repo.list()[0].times) == 3

    def test_all_seven_days_is_stored_as_every_day(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        assert qt_app.schedule_repo.list()[0].days == []

    def test_selected_days_reach_the_database(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        for index, box in enumerate(publish._day_boxes):
            box.setChecked(index in (0, 3))
        publish.publish()
        assert qt_app.schedule_repo.list()[0].days == [0, 3]


class TestSummaryAndWarnings:
    def test_now_mode_names_the_group_count(self, views):
        _compose, publish = views
        publish.set_mode(NOW)
        assert "3 groups" in publish.summary.text()

    def test_once_mode_names_the_time(self, views):
        _compose, publish = views
        publish.set_mode(ONCE)
        typed = publish.schedule_entry.dateTime().toString("yyyy-MM-dd HH:mm")
        assert typed in publish.summary.text()

    def test_repeat_mode_names_the_first_run(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert "First run" in publish.summary.text()

    def test_one_wording_across_three_groups_is_flagged(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert "Repetitive content" in publish.summary.text()

    def test_a_wording_per_group_is_not_flagged(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_wording("Second wording")
        publish.add_wording("Third wording")
        assert "Repetitive content" not in publish.summary.text()

    def test_a_time_outside_the_posting_window_is_flagged(self, qt_app, views):
        _compose, publish = views
        qt_app.settings_repo.set("posting_window_start_hour", 8)
        qt_app.settings_repo.set("posting_window_end_hour", 23)
        publish.set_mode(REPEAT)
        publish._time_rows[0].setTime(
            publish._time_rows[0].time().fromString("03:00", "HH:mm")
        )
        assert "posting window" in publish.summary.text()

    def test_the_rotation_note_counts_the_compose_text_too(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_wording("Second wording")
        assert publish.rotation_note.text().startswith("2 wordings")


class TestWhatIsGoingOut:
    def test_the_recipients_are_listed(self, views):
        _compose, publish = views
        publish.on_show()
        assert publish.recipient_box.count() == 3

    def test_a_snippet_of_the_post_is_shown(self, views):
        _compose, publish = views
        publish.on_show()
        assert BODY[:20] in publish.preview_note.text()

    def test_it_says_so_when_no_groups_are_picked(self, qt_app, views):
        _compose, publish = views
        qt_app.views["groups"].set_all(False)
        publish.on_show()
        assert publish.recipient_box.count() == 1  # the "no groups" note

    def test_it_reads_committed_state_not_the_live_editor(self, views):
        """capture() runs on show, so an uncommitted keystroke is not lost."""
        compose, publish = views
        compose._show("typed but never captured")
        publish.on_show()
        assert "typed but never captured" in publish.preview_note.text()


class TestManagingRepeatingPosts:
    def test_pausing_and_resuming(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        schedule_id = qt_app.schedule_repo.list()[0].id

        publish.toggle_schedule(schedule_id)
        assert qt_app.schedule_repo.get(schedule_id).state == SCHEDULE_PAUSED
        publish.toggle_schedule(schedule_id)
        assert qt_app.schedule_repo.get(schedule_id).state == SCHEDULE_ACTIVE

    def test_resuming_recomputes_the_next_slot(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        schedule_id = qt_app.schedule_repo.list()[0].id
        qt_app.schedule_repo.set_next_run(
            schedule_id, clock.parse_local("2020-01-01 09:00")
        )
        publish.toggle_schedule(schedule_id)
        publish.toggle_schedule(schedule_id)
        assert qt_app.schedule_repo.get(schedule_id).next_run_at > utcnow()

    def test_deleting(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        publish.delete_schedule(qt_app.schedule_repo.list()[0].id)
        assert qt_app.schedule_repo.list() == []

    def test_a_created_schedule_gets_a_card(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        assert publish.schedule_box.count() == 2  # one card plus the stretch


class TestTheWindowItself:
    def test_every_view_builds(self, qt_app):
        assert set(qt_app.views) == {"compose", "publish", "groups", "queue"}

    def test_constructing_it_does_not_start_the_worker(self, qt_app):
        assert qt_app.worker is None
