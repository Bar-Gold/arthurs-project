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
from pathlib import Path

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
    """Compose and Publish, with three groups picked."""
    for index in range(3):
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
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

    def test_there_is_no_ceiling_on_times(self, views):
        """Three a day was the most until 2026-10-18."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        for _ in range(5):
            assert publish.add_time() is True
        assert len(set(publish.times())) == 6, "a new row repeated a time already listed"

    def test_many_times_a_day_reach_the_database(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        for text in ("12:00", "14:00", "20:00", "23:00"):
            publish.add_time(text)
        assert publish.publish() is True
        stored = qt_app.schedule_repo.list()[0]
        assert stored.times == ["09:00", "12:00", "14:00", "20:00", "23:00"]

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

    def test_the_days_on_screen_run_sunday_to_saturday(self, views):
        """The order shown is Israel's week; the stored numbers are not."""
        from PySide6.QtWidgets import QGridLayout

        _compose, publish = views
        grid = next(
            g for g in publish.findChildren(QGridLayout)
            if g.indexOf(publish._day_boxes[0]) >= 0
        )
        shown = sorted(
            publish._day_boxes,
            key=lambda box: grid.getItemPosition(grid.indexOf(box))[:2],
        )
        assert [box.text() for box in shown] == [
            "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat",
        ]
        assert publish._day_boxes[6].text() == "Sun"


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

    def test_nothing_is_flagged(self, views):
        """No posting rules (2026-10-18): one wording to every group, at 03:00,
        is said as a fact and warned about not at all."""
        from fbposter.qtui import theme

        _compose, publish = views
        publish.set_mode(REPEAT)
        publish._time_rows[0].setTime(
            publish._time_rows[0].time().fromString("03:00", "HH:mm")
        )
        text = publish.summary.text()
        assert "Every day at 03:00" in text
        assert theme.C["WARNING"] not in text
        assert "Repetitive content" not in text and "posting hours" not in text

    def test_the_empty_wordings_hint_shows_when_there_are_no_alternates(self, views):
        """It says what a wording is for: with none, every run sends the
        Compose text."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert publish.wording_hint.isVisibleTo(publish)
        assert "every run sends the Compose text" in publish.wording_hint.text()

    def test_the_hint_goes_away_once_an_alternate_exists(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_wording("Second wording")
        assert not publish.wording_hint.isVisibleTo(publish)

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
        # "welcome" is built like the rest but deliberately kept out of the
        # sidebar -- setup is something you finish, not a step you return to.
        # So is "schedule_edit", reached from a repeating post's card.
        assert set(qt_app.views) == {
            "compose", "publish", "groups", "dashboard", "settings", "welcome",
            "schedule_edit", "group_sets", "tips",
        }

    def test_the_wizard_is_not_a_sidebar_step(self, qt_app):
        assert "welcome" not in qt_app.nav_buttons

    def test_constructing_it_does_not_start_the_worker(self, qt_app):
        assert qt_app.worker is None


class TestThereAreNoRules:
    """The user's choice, 2026-10-18: one click posts, whatever was posted
    before. There is no "Post anyway" panel and nothing is recorded as an
    exception, because there is nothing to be an exception to."""

    @staticmethod
    def recently_posted(qt_app):
        for group in qt_app.group_repo.list():
            qt_app.group_repo.mark_posted(group.id, utcnow() - timedelta(hours=1))

    def test_groups_posted_to_an_hour_ago_are_queued_at_once(self, qt_app, views):
        _compose, publish = views
        self.recently_posted(qt_app)
        assert publish.publish() is True
        (task,) = qt_app.task_repo.list_recent()
        assert len(qt_app.task_repo.targets_for(task.id)) == 3
        assert qt_app.toast_label.text() == "Queued for 3 groups."

    def test_there_is_no_post_anyway_panel(self, views):
        _compose, publish = views
        assert not hasattr(publish, "override_card")
        assert not hasattr(publish, "post_anyway")

    def test_the_same_text_again_the_same_day_is_queued(self, qt_app, views):
        _compose, publish = views
        assert publish.publish() is True
        assert publish.publish() is True
        assert len(qt_app.task_repo.list_recent()) == 2

    def test_the_old_daily_limit_refuses_nothing(self, qt_app, views):
        _compose, publish = views
        qt_app.settings_repo.set("daily_cap", 1)
        assert publish.publish() is True

    def test_a_repeat_three_hours_apart_on_one_wording_is_created(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_time("12:00")  # beside the default 09:00
        assert publish.publish() is True
        (schedule,) = qt_app.schedule_repo.list()
        assert schedule.times == ["09:00", "12:00"]
        assert schedule.bodies == [BODY]

    def test_what_is_not_a_rule_is_still_refused(self, qt_app, views):
        """No groups is not a rule: there is nothing to post to."""
        _compose, publish = views
        qt_app.selected_groups.clear()
        assert publish.publish() is False
        assert qt_app.task_repo.list_recent() == []


class TestTheRotationNote:
    def test_it_is_not_shown_without_a_rotation(self, views):
        """With no alternates the hint says it all; "1 wording in rotation
        (the Compose text plus these)" described a list that was not there."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert publish.rotation_note.isHidden()

    def test_it_counts_what_is_really_there(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.add_wording("Road bike, 54cm, barely ridden.")
        assert not publish.rotation_note.isHidden()
        assert publish.rotation_note.text().startswith(
            "2 wordings in rotation: the Compose text and 1 alternate."
        )


class TestTheSnippet:
    def test_a_whole_post_has_no_ellipsis(self, views):
        _compose, publish = views
        assert publish.preview_note.text() == f"“{BODY}”"

    def test_a_long_post_says_it_was_cut(self, qt_app, views):
        compose, publish = views
        qt_app.show_view("compose")
        compose._show("word " * 60)
        compose.capture()
        qt_app.show_view("publish")
        assert publish.preview_note.text().endswith("…”")


class TestPicturesOnly:
    """Pictures with no text: a post of its own, for Now and Once."""

    PICTURE = Path("C:/pictures/bike.jpg")

    @pytest.fixture
    def pictures(self, views, monkeypatch):
        compose, publish = views
        compose._show("")
        compose.capture()
        compose.attachments = [self.PICTURE]
        said = []
        monkeypatch.setattr(compose, "notify", lambda m, level="info": said.append(m))
        monkeypatch.setattr(publish, "notify", lambda m, level="info": said.append(m))
        return compose, publish, said

    def test_post_now_queues_it(self, qt_app, pictures):
        _compose, publish, _said = pictures
        publish.set_mode(NOW)
        assert publish.publish() is True

        task = qt_app.task_repo.list_recent()[0]
        assert task.media_paths == [str(self.PICTURE)]
        assert all(t.body == "" for t in qt_app.task_repo.targets_for(task.id))
        assert len(qt_app.task_repo.targets_for(task.id)) == 3

    def test_once_queues_it(self, qt_app, pictures):
        _compose, publish, _said = pictures
        publish.set_mode(ONCE)
        publish.schedule_entry.setDateTime(publish.schedule_entry.dateTime().addDays(1))
        assert publish.publish() is True
        assert qt_app.task_repo.list_recent()[0].scheduled_for is not None

    def test_no_text_and_no_pictures_is_still_refused(self, qt_app, pictures):
        compose, publish, said = pictures
        compose.attachments = []
        publish.set_mode(NOW)
        assert publish.publish() is False
        assert qt_app.task_repo.list_recent() == []
        assert "text or a picture" in said[-1]

    def test_repeat_is_created_too(self, qt_app, pictures):
        """Pictures alone may repeat (the user's choice, 2026-10-18)."""
        _compose, publish, _said = pictures
        publish.set_mode(REPEAT)
        assert publish.publish() is True
        (schedule,) = qt_app.schedule_repo.list()
        assert schedule.bodies == []
        assert schedule.media_paths == [str(self.PICTURE)]

    def test_the_summary_says_pictures_only(self, pictures):
        _compose, publish, _said = pictures
        publish.refresh_recipients()
        assert publish.preview_note.text() == "Pictures only (1 picture, no text)."


class TestOneWordingIsEnough:
    """Two runs a day on one wording needed a second wording until 2026-10-18."""

    def test_two_runs_a_day_on_one_wording_is_created(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish._time_rows[0].setTime(publish._time_rows[0].time().fromString("10:00", "HH:mm"))
        publish.add_time("20:00")
        assert publish.publish() is True

    def test_one_run_a_day_on_one_wording_is_fine(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert publish.publish() is True
