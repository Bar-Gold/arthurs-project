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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from fbposter import clock, recurrence
from fbposter.db.models import (
    SCHEDULE_ACTIVE,
    TASK_PENDING,
    utcnow,
)
from fbposter.qtui.views.publish import (
    NOW,
    ONCE,
    RECIPIENTS_MAX_HEIGHT,
    REPEAT,
)

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


def add_version(compose, text):
    """Another version of the post, written in step 1."""
    compose.add_version()
    compose._show(text)
    compose.capture()


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

    def test_it_says_nothing_about_the_groups(self, qt_app, views):
        """The "Sending to" card went (the user's request, 2026-10-24): the
        groups are step 2's, and step 3 lists them under "Going out"."""
        from PySide6.QtWidgets import QLabel

        compose, _publish = views
        qt_app.show_view("compose")
        words = [label.text() for label in compose.findChildren(QLabel)]
        assert "Sending to" not in words
        assert not any("Demo 0" in text for text in words)


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
        assert not publish.add_time_button.isVisibleTo(publish)
        publish.set_mode(REPEAT)
        assert publish.add_time_button.isVisibleTo(publish)

    def test_there_are_no_alternate_wordings_here(self, views):
        """They are versions in step 1 now (2026-10-23): one way to vary
        the words, not two."""
        _compose, publish = views
        for gone in ("add_wording", "alternates", "wording_hint", "wording_area"):
            assert not hasattr(publish, gone), gone

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

    def test_it_shares_out_the_versions(self, qt_app, views):
        compose, publish = views
        add_version(compose, "A different wording")

        publish.publish()
        task = qt_app.task_repo.list_recent()[0]
        bodies = [t.body for t in qt_app.task_repo.targets_for(task.id)]
        assert bodies == [BODY, "A different wording", BODY]
        assert task.body == BODY

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
    def test_the_first_version_is_the_first_wording(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert publish.wordings() == [BODY]

    def test_the_other_versions_follow_it(self, views):
        compose, publish = views
        add_version(compose, "Another way of saying it")
        publish.set_mode(REPEAT)
        assert publish.wordings() == [BODY, "Another way of saying it"]

    def test_an_empty_version_is_left_out(self, views):
        compose, publish = views
        compose.add_version()
        add_version(compose, "Another way of saying it")
        publish.set_mode(REPEAT)
        assert publish.wordings() == [BODY, "Another way of saying it"]

    def test_it_writes_a_schedule(self, qt_app, views):
        compose, publish = views
        add_version(compose, "Second wording")
        publish.set_mode(REPEAT)
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
        """As people say it, "Sat 24 Oct 2026 at 12:30" (2026-10-24)."""
        from fbposter.qtui.views.publish import DATE_FORMAT, ENGLISH

        _compose, publish = views
        publish.set_mode(ONCE)
        picked = publish.schedule_entry.dateTime()
        said = ENGLISH.toString(picked, f"{DATE_FORMAT} 'at' HH:mm")
        assert f"once on {said}." in publish.summary.text()

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

    def test_one_version_says_where_to_add_more(self, views):
        """With one version, every run sends it to every group, and more are
        added in step 1."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        assert publish.rotation_note.isVisibleTo(publish)
        assert "Add more in step\u00a01" in publish.rotation_note.text()

    def test_the_rotation_note_counts_the_versions(self, views):
        compose, publish = views
        add_version(compose, "Second wording")
        publish.set_mode(REPEAT)
        assert publish.rotation_note.text().startswith("2 versions take turns")


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
        assert publish.recipient_count.text() == ""

    def test_a_hairline_separates_the_rows(self, views):
        """A line between each two rows (the user's request, 2026-10-19: the
        names ran into each other), drawn by the shared list, as every group
        list is since 2026-10-20."""
        from fbposter.qtui.widgets import RowList

        _compose, publish = views
        publish.on_show()
        assert isinstance(publish.recipient_list, RowList)
        assert publish.recipient_list.lines() == 2

    def test_the_count_is_beside_the_heading(self, views):
        _compose, publish = views
        publish.on_show()
        assert publish.recipient_count.text() == "3 groups"

    def test_a_hebrew_name_starts_at_the_left_like_the_rest(self, qt_app, views):
        """Left to itself a Hebrew name went to the far right of its row, so a
        list mixing the two zigzagged from side to side."""
        _compose, publish = views
        hebrew = qt_app.group_repo.add_from_url(
            "https://www.facebook.com/groups/demo9", name="מוכרים-קונים כרטיסים להופעות"
        )
        qt_app.set_group_selected(hebrew.id, True)
        publish.on_show()
        names = [label for label in publish.recipients.findChildren(QLabel)
                 if label.objectName() != "Muted"]
        assert "מוכרים-קונים כרטיסים להופעות" in [label.text() for label in names]
        for label in names:
            assert label.alignment() & Qt.AlignAbsolute, label.text()
            assert label.alignment() & Qt.AlignLeft, label.text()


class TestALongListScrolls:
    """A client sends one post to 40-80 groups. Laid out bare, 45 names were
    squeezed into the window and drawn over each other."""

    @pytest.fixture
    def many(self, qt_app):
        for index in range(45):
            group = qt_app.group_repo.add_from_url(
                f"https://www.facebook.com/groups/many{index}", name=f"Group {index}"
            )
            qt_app.set_group_selected(group.id, True)
        qt_app.show_view("publish")
        return qt_app.views["publish"]

    def test_it_stops_growing_and_scrolls(self, many):
        area = many.recipient_area
        assert sum(area.row_heights()) > RECIPIENTS_MAX_HEIGHT
        assert area.sizeHint().height() == RECIPIENTS_MAX_HEIGHT
        assert area.minimumSizeHint().height() < RECIPIENTS_MAX_HEIGHT

    def test_repeat_gives_it_the_same_room(self, many):
        """Repeat had its alternate wordings under the list, and gave it less
        room, until they became versions in step 1 (2026-10-23)."""
        many.set_mode(REPEAT)
        assert many.recipient_area.sizeHint().height() == RECIPIENTS_MAX_HEIGHT

    def test_a_short_list_is_as_tall_as_its_rows(self, views):
        """No empty space under the last group."""
        _compose, publish = views
        publish.on_show()
        area = publish.recipient_area
        assert 0 < area.sizeHint().height() == sum(area.row_heights()) < RECIPIENTS_MAX_HEIGHT

    def test_it_reads_committed_state_not_the_live_editor(self, views):
        """capture() runs on show, so an uncommitted keystroke is not lost."""
        compose, publish = views
        compose._show("typed but never captured")
        publish.on_show()
        assert "typed but never captured" in publish.preview_note.text()


class TestTheRepeatingPostsListMoved:
    """The repeating posts already made are on their own screen now,
    Repeating posts in the sidebar (the user's choice, 2026-10-21)."""

    def test_step_3_no_longer_lists_them(self, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        shown = [label.text() for label in publish.findChildren(QLabel)]
        assert "Existing repeating posts" not in shown
        for gone in ("schedule_area", "schedule_box", "toggle_schedule", "delete_schedule"):
            assert not hasattr(publish, gone), gone

    def test_starting_one_says_where_it_went(self, qt_app, views):
        """The window goes to Home once it is saved, so the banner says
        where to find it."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        assert len(qt_app.schedule_repo.list()) == 1
        assert "Find it in Repeating posts." in qt_app.toast_label.text()


def start_again(qt_app, body=BODY, mode=REPEAT):
    """A post that goes out starts a fresh one, so the next is written anew."""
    qt_app.show_view("groups")
    qt_app.views["groups"].set_all(True)
    compose = qt_app.views["compose"]
    qt_app.show_view("compose")
    compose._show(body)
    compose.capture()
    publish = qt_app.views["publish"]
    qt_app.show_view("publish")
    publish.set_mode(mode)
    return publish


class TestTheSameRepeatingPostTwice:
    """A client set up the same repeating post twice, 31 seconds apart, which
    sends every group the same post twice at every slot. An exact copy is
    refused; anything else goes through (the user's choice, 2026-10-20)."""

    def test_an_exact_copy_is_refused_and_says_which(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.name_entry.setText("Flats")
        assert publish.publish() is True

        again = start_again(qt_app)
        assert again.publish() is False
        assert len(qt_app.schedule_repo.list()) == 1
        assert "Flats" in qt_app.toast_label.text()

    def test_a_refused_copy_keeps_the_form(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()

        again = start_again(qt_app)
        again.publish()
        assert qt_app.views["compose"].base_body() == BODY
        assert again.mode == REPEAT

    def test_the_same_post_at_another_time_goes_through(self, qt_app, views):
        """Morning and evening, like a client's two posts of one listing."""
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()

        again = start_again(qt_app)
        again._time_rows[0].setTime(again._time_rows[0].time().addSecs(3600 * 9))
        assert again.publish() is True
        assert len(qt_app.schedule_repo.list()) == 2

    def test_the_same_post_to_other_groups_goes_through(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()

        again = start_again(qt_app)
        first = qt_app.selected_group_ids()[0]
        qt_app.selected_groups.discard(first)
        assert again.publish() is True
        assert len(qt_app.schedule_repo.list()) == 2

    def test_other_words_go_through(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(REPEAT)
        publish.publish()
        assert start_again(qt_app, body="Another flat").publish() is True

    def test_posting_now_twice_is_never_refused(self, qt_app, views):
        _compose, publish = views
        publish.set_mode(NOW)
        assert publish.publish() is True
        assert start_again(qt_app, mode=NOW).publish() is True
        assert len(qt_app.task_repo.list_recent()) == 2


class TestTheWindowItself:
    def test_every_view_builds(self, qt_app):
        # "welcome" is built like the rest but deliberately kept out of the
        # sidebar -- setup is something you finish, not a step you return to.
        # So is "schedule_edit", reached from a repeating post's card.
        assert set(qt_app.views) == {
            "compose", "publish", "groups", "dashboard", "settings", "welcome",
            "schedule_edit", "group_sets", "tips", "home", "create", "repeating",
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
        compose, publish = views
        assert publish.publish() is True
        # A queued post starts a fresh one (2026-10-19), so write it again.
        compose._show(BODY)
        compose.capture()
        qt_app.views["groups"].set_all(True)
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
    def test_it_is_not_shown_for_one_version_going_out_now(self, views):
        _compose, publish = views
        publish.set_mode(NOW)
        assert publish.rotation_note.isHidden()

    def test_now_says_the_versions_are_shared_out(self, views):
        compose, publish = views
        add_version(compose, "Road bike, 54cm, barely ridden.")
        publish.set_mode(NOW)
        assert not publish.rotation_note.isHidden()
        assert publish.rotation_note.text().startswith(
            "2 versions, shared out across the groups."
        )

    def test_pictures_alone_have_nothing_to_say(self, views):
        compose, publish = views
        compose._show("")
        compose.capture()
        compose.attachments = [Path("C:/pictures/bike.jpg")]
        publish.set_mode(REPEAT)
        assert publish.rotation_note.isHidden()


class TestTheSnippet:
    def test_a_whole_post_has_no_ellipsis(self, views):
        _compose, publish = views
        assert publish.preview_note.text() == BODY

    def test_a_long_post_says_it_was_cut(self, qt_app, views):
        compose, publish = views
        qt_app.show_view("compose")
        compose._show("word " * 60)
        compose.capture()
        qt_app.show_view("publish")
        assert publish.preview_note.text().endswith("…")


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


class TestPicturesWithWords:
    """Step 3 showed the words alone, and nothing said a picture was going
    too (2026-10-21)."""

    def test_the_post_says_what_goes_with_it(self, views):
        compose, publish = views
        compose.attachments = [Path("C:/pictures/bike.jpg"), Path("C:/pictures/flat.jpg")]
        publish.refresh_recipients()
        assert publish.preview_note.text() == BODY
        assert publish.media_note.text() == "With 2 pictures."
        assert publish.media_note.isVisibleTo(publish)

    def test_words_alone_say_nothing_more(self, views):
        _compose, publish = views
        publish.refresh_recipients()
        assert not publish.media_note.isVisibleTo(publish)

    def test_pictures_alone_are_said_once(self, views):
        """"Pictures only (1 picture, no text)" says it already."""
        compose, publish = views
        compose._show("")
        compose.capture()
        compose.attachments = [Path("C:/pictures/bike.jpg")]
        publish.refresh_recipients()
        assert not publish.media_note.isVisibleTo(publish)


class TestATimeIsOneBox:
    def test_the_hour_and_minutes_share_a_box(self, views):
        """Two boxes side by side looked like two settings (2026-10-21)."""
        _compose, publish = views
        picker = publish._time_rows[0]
        assert picker.objectName() == "TimeField"
        assert picker.hour.objectName() == picker.minute.objectName() == "TimePart"


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
