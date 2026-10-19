"""Telling the user where they are and what just happened, and taking back a slip.

Each of these was a gap found by reviewing the window against Apple's design
principles (2026-10-12):

* Wayfinding: the setup screen and the repeating-post editor left whichever
  sidebar item was clicked last lit -- often Settings.
* Feedback: status was one line of small grey text that went after 4.5
  seconds, errors included.
* Forgiveness: Delete beside Edit, Remove on every group and Cancel on every
  batch each destroyed work in one click with no way back.
* A warning set in the same grey as the facts around it read as a fact.
"""

from __future__ import annotations

import pytest

from fbposter import recurrence
from fbposter.db.models import (
    TARGET_DONE,
    TARGET_PENDING,
    TARGET_SKIPPED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_PENDING,
    utcnow,
)
from fbposter.db.repo import GROUP_REMOVED

BODY = "Selling a road bike, 54cm frame."


@pytest.fixture
def groups(qt_app):
    return [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
        for index in range(2)
    ]


def lit(qt_app) -> list[str]:
    return [key for key, button in qt_app.nav_buttons.items() if button.isChecked()]


class TestWhereAmI:
    def test_a_sidebar_screen_lights_itself(self, qt_app):
        qt_app.show_view("settings")
        qt_app.show_view("dashboard")
        assert lit(qt_app) == ["dashboard"]

    def test_the_setup_screen_lights_nothing(self, qt_app):
        qt_app.show_view("settings")
        qt_app.show_view("welcome")
        assert lit(qt_app) == []

    def test_editing_a_repeating_post_lights_create_a_post(self, qt_app):
        qt_app.show_view("settings")
        qt_app.show_view("schedule_edit")
        assert lit(qt_app) == ["create"]

    def test_coming_back_lights_one_again(self, qt_app):
        qt_app.show_view("welcome")
        qt_app.show_view("compose")
        assert lit(qt_app) == ["create"]


class TestHowLongAMessageStays:
    def test_short_ones_get_the_minimum(self, qt_application):
        from fbposter.qtui.widgets import MESSAGE_MIN_MS, message_duration

        assert message_duration("Saved.", "success") == MESSAGE_MIN_MS

    def test_long_ones_get_time_to_be_read(self, qt_application):
        from fbposter.qtui.widgets import MESSAGE_MIN_MS, message_duration

        long = "A sentence of some length that takes a while to read. " * 3
        assert message_duration(long, "warning") > MESSAGE_MIN_MS

    def test_there_is_a_ceiling(self, qt_application):
        from fbposter.qtui.widgets import MESSAGE_MAX_MS, message_duration

        assert message_duration("x" * 5000, "info") == MESSAGE_MAX_MS

    def test_an_error_stays_until_closed(self, qt_application):
        from fbposter.qtui.widgets import message_duration

        assert message_duration("The batch stopped.", "error") is None

    def test_an_undo_leaves_time_to_reach_it(self, qt_application):
        from fbposter.qtui.widgets import UNDO_MIN_MS, message_duration

        assert message_duration("Deleted.", "info", can_undo=True) == UNDO_MIN_MS


class TestTheBanner:
    def test_it_is_not_there_until_there_is_something_to_say(self, qt_app):
        assert qt_app.banner.isHidden()

    def test_a_message_shows_with_its_kind(self, qt_app):
        qt_app.toast("Posting rules saved.", "success")
        assert not qt_app.banner.isHidden()
        assert qt_app.toast_label.text() == "Posting rules saved."
        assert qt_app.banner.level == "success"
        assert qt_app.banner.bar.property("level") == "success"

    def test_it_goes_on_its_own(self, qt_app):
        qt_app.toast("Saved.", "info")
        assert qt_app.banner.timer.isActive()
        qt_app.banner.timer.timeout.emit()
        assert qt_app.banner.isHidden() and qt_app.toast_label.text() == ""

    def test_an_error_does_not(self, qt_app):
        qt_app.toast("The batch stopped.", "error")
        assert not qt_app.banner.timer.isActive()

    def test_it_can_be_closed(self, qt_app):
        qt_app.toast("The batch stopped.", "error")
        qt_app.banner.close_button.click()
        assert qt_app.banner.isHidden() and qt_app.toast_label.text() == ""

    def test_the_close_button_is_named_for_screen_readers(self, qt_app):
        assert qt_app.banner.close_button.accessibleName() == "Close this message"

    def test_the_words_are_in_the_body_colour_not_the_level_colour(self, qt_application):
        """Orange or red body text is harder to read; the bar says the kind."""
        from fbposter.qtui import theme

        sheet = theme.stylesheet()
        assert f'QLabel#ToastText {{ color: {theme.C["TEXT"]}; }}' in sheet


class TestUndo:
    def test_no_undo_no_button(self, qt_app):
        qt_app.toast("Saved.", "info")
        assert qt_app.banner.undo_button.isHidden()

    def test_undo_puts_it_back_and_says_so(self, qt_app):
        done = []
        qt_app.toast("Deleted.", "info", undo=lambda: done.append(1) or "It is back.")
        assert not qt_app.banner.undo_button.isHidden()
        qt_app.banner.undo_button.click()
        assert done == [1]
        assert qt_app.toast_label.text() == "It is back."
        assert qt_app.banner.level == "success"

    def test_too_late_says_so(self, qt_app):
        from fbposter.qtui.app import TOO_LATE_TO_UNDO

        qt_app.toast("Deleted.", "info", undo=lambda: None)
        qt_app.banner.undo_button.click()
        assert qt_app.toast_label.text() == TOO_LATE_TO_UNDO

    def test_a_failure_is_reported_not_swallowed(self, qt_app):
        def broken():
            raise RuntimeError("database is locked")

        qt_app.toast("Deleted.", "info", undo=broken)
        qt_app.banner.undo_button.click()
        assert "database is locked" in qt_app.toast_label.text()
        assert qt_app.banner.level == "error"

    def test_a_new_message_takes_the_undo_away(self, qt_app):
        qt_app.toast("Deleted.", "info", undo=lambda: "back")
        qt_app.toast("Something else.", "info")
        assert qt_app.banner.undo_button.isHidden()


class TestUndoingACancel:
    def test_the_batch_goes_back_as_it_was(self, qt_app, groups):
        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        qt_app.views["dashboard"].cancel(task.id)
        assert qt_app.task_repo.get(task.id).state == TASK_CANCELLED

        qt_app.banner.undo_button.click()
        restored = qt_app.task_repo.get(task.id)
        assert restored.state == TASK_PENDING
        assert restored.finished_at is None

    def test_not_once_it_has_finished(self, qt_app, groups):
        """Cancelled mid-post, and that post was its last: it is done now."""
        from fbposter.qtui.app import TOO_LATE_TO_UNDO

        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        qt_app.views["dashboard"].cancel(task.id)
        qt_app.task_repo.mark_task(task.id, TASK_DONE, finished=True)

        qt_app.banner.undo_button.click()
        assert qt_app.task_repo.get(task.id).state == TASK_DONE
        assert qt_app.toast_label.text() == TOO_LATE_TO_UNDO


class TestUndoingADelete:
    def make(self, qt_app, groups):
        rule = recurrence.build(["09:00"])
        schedule = qt_app.schedule_repo.create(
            name="Bikes",
            bodies=["One wording", "Two wording"],
            group_ids=[g.id for g in groups],
            times=list(rule.times),
            next_run_at=recurrence.next_occurrence(rule, utcnow()),
        )
        qt_app.schedule_repo.record_run(schedule.id, utcnow(), schedule.next_run_at)
        return qt_app.schedule_repo.get(schedule.id)

    def test_it_comes_back_whole_under_its_own_id(self, qt_app, groups):
        before = self.make(qt_app, groups)
        fired = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)], schedule_id=before.id)
        qt_app.views["publish"].delete_schedule(before.id)
        assert qt_app.schedule_repo.get(before.id) is None

        qt_app.banner.undo_button.click()
        after = qt_app.schedule_repo.get(before.id)
        assert after == before
        assert qt_app.task_repo.get(fired.id).schedule_id == after.id

    def test_a_group_removed_meanwhile_stays_out(self, qt_app, groups):
        before = self.make(qt_app, groups)
        qt_app.views["publish"].delete_schedule(before.id)
        undo = qt_app.banner.undo_button
        qt_app.group_repo.remove(groups[1].id)

        undo.click()
        assert qt_app.schedule_repo.get(before.id).group_ids == [groups[0].id]


class TestUndoingARemove:
    def test_everything_it_was_taken_out_of_comes_back(self, qt_app, groups):
        one, two = groups
        rule = recurrence.build(["09:00"])
        schedule = qt_app.schedule_repo.create(
            name="Bikes", bodies=["Words"], group_ids=[one.id, two.id],
            times=list(rule.times),
        )
        shared = qt_app.task_repo.create(BODY, [(one.id, BODY), (two.id, BODY)])
        alone = qt_app.task_repo.create(BODY, [(one.id, BODY)])
        qt_app.set_group_selected(one.id, True)

        qt_app.views["groups"].remove_group(one.id)
        assert qt_app.group_repo.active(one.id) is None
        assert qt_app.task_repo.get(alone.id).state == TASK_CANCELLED

        qt_app.banner.undo_button.click()
        assert qt_app.group_repo.active(one.id) is not None
        assert qt_app.schedule_repo.get(schedule.id).group_ids == [one.id, two.id]
        targets = {t.group_id: t for t in qt_app.task_repo.targets_for(shared.id)}
        assert (targets[one.id].state, targets[one.id].error) == (TARGET_PENDING, "")
        assert qt_app.task_repo.get(alone.id).state == TASK_PENDING
        assert one.id in qt_app.selected_groups

    def test_a_turn_that_moved_on_is_left_alone(self, qt_app, groups):
        one, _two = groups
        task = qt_app.task_repo.create(BODY, [(one.id, BODY)])
        removal = qt_app.group_repo.remove(one.id)
        target = qt_app.task_repo.targets_for(task.id)[0]
        assert (target.state, target.error) == (TARGET_SKIPPED, GROUP_REMOVED)
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)

        assert qt_app.group_repo.restore(one.id, removal) is True
        assert qt_app.task_repo.targets_for(task.id)[0].state == TARGET_DONE

    def test_not_once_it_has_been_added_back(self, qt_app, groups):
        one, _two = groups
        removal = qt_app.group_repo.remove(one.id)
        qt_app.group_repo.add_from_url(one.url)
        assert qt_app.group_repo.restore(one.id, removal) is False

    def test_not_once_it_has_been_brought_back(self, qt_app, groups):
        one, _two = groups
        removal = qt_app.group_repo.remove(one.id)
        qt_app.group_repo.bring_back(one.id)
        assert qt_app.group_repo.restore(one.id, removal) is False


class TestWarningsLookLikeWarnings:
    def test_the_publish_summary_colours_its_warnings_only(self, qt_app, groups):
        from fbposter.qtui import theme
        from fbposter.qtui.views.publish import REPEAT

        for index in range(2, 4):
            qt_app.group_repo.add_from_url(
                f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
            )
        for group in qt_app.group_repo.list():
            qt_app.set_group_selected(group.id, True)
        qt_app.views["compose"].editor.setPlainText(BODY)
        qt_app.show_view("publish")
        publish = qt_app.views["publish"]
        publish.set_mode(REPEAT)

        html = publish.summary.text()
        warning = f'<div style="color: {theme.C["WARNING"]};">'
        assert "<div>Every day at" in html
        # No posting rules (2026-10-18), so a valid repeat warns about nothing.
        assert warning not in html

        for box in publish._day_boxes:
            box.setChecked(False)
        publish.refresh_summary()
        assert warning + "Pick at least one day" in publish.summary.text()

    def test_a_group_name_cannot_inject_markup(self, qt_application):
        from PySide6.QtWidgets import QLabel

        from fbposter.qtui.widgets import set_note_lines

        label = QLabel()
        set_note_lines(label, ["<b>Bikes & more</b>"])
        assert "&lt;b&gt;Bikes &amp; more&lt;/b&gt;" in label.text()


class TestTitles:
    """Polished the way the other AppStyle rules are tested: directly, because
    the shared test window runs on the default style."""

    def test_large_type_is_tracked_tighter(self, qt_application):
        from PySide6.QtWidgets import QLabel

        from fbposter.qtui.widgets import TRACKING, AppStyle

        title = QLabel("Queue")
        title.setObjectName("Title")
        AppStyle().polish(title)
        assert title.font().letterSpacing() == TRACKING["Title"]

    def test_body_text_is_left_alone(self, qt_application):
        from PySide6.QtWidgets import QLabel

        from fbposter.qtui.widgets import AppStyle

        body = QLabel("Selling a road bike")
        AppStyle().polish(body)
        assert body.font().letterSpacing() == 0
