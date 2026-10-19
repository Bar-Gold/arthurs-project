"""The Dashboard: the numbers on screen, Cancel, and every label in plain words.

It replaced the Queue screen (2026-10-14). The arithmetic is tested on its own
in test_stats.py; these tests check that the screen shows it, and that what the
Queue did that the user kept (Cancel, with Undo) still works.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from PySide6.QtWidgets import QLabel, QPushButton

from fbposter import recurrence
from fbposter.db.models import (
    TARGET_AWAITING_APPROVAL,
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_SKIPPED,
    TARGET_UNCONFIRMED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_HALTED,
    TASK_PENDING,
    to_iso,
    utcnow,
)
from fbposter.qtui.views.dashboard import (
    DayBars,
    OutcomeBar,
    target_label,
    task_label,
)

BODY = "Selling a road bike, 54cm frame."


@pytest.fixture
def groups(qt_app):
    return [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
        for index in range(2)
    ]


@pytest.fixture
def view(qt_app):
    qt_app.show_view("dashboard")
    return qt_app.views["dashboard"]


def texts(view) -> list[str]:
    view.refresh(force=True)
    return [label.text() for label in view.holder.findChildren(QLabel)]


def recent_texts(view) -> list[str]:
    """The Recent activity list only: the results legend also says "Posted"."""
    view.refresh(force=True)
    shown = []
    for index in range(view.recent_box.count()):
        widget = view.recent_box.itemAt(index).widget()
        if isinstance(widget, QLabel):
            shown.append(widget.text())
        elif widget is not None:
            shown.extend(label.text() for label in widget.findChildren(QLabel))
    return shown


def post(qt_app, group, body=BODY, state=TARGET_DONE, ago=timedelta(minutes=5), error=""):
    """A finished one-group batch, `ago` before now."""
    task = qt_app.task_repo.create(body, [(group.id, body)])
    (target,) = qt_app.task_repo.targets_for(task.id)
    when = to_iso(utcnow() - ago)
    sent = state in (TARGET_DONE, TARGET_AWAITING_APPROVAL, TARGET_UNCONFIRMED)
    qt_app.db.write(
        "UPDATE task_targets SET state = ?, attempted_at = ?, posted_at = ?, error = ? "
        "WHERE id = ?",
        (state, when, when if sent else None, error, target.id),
    )
    qt_app.task_repo.mark_task(task.id, TASK_DONE, finished=True)
    return task


class TestTheSidebar:
    def test_the_dashboard_took_the_queue_s_place(self, qt_app):
        from fbposter.qtui.app import NAV_ITEMS

        keys = [key for key, _label, _cls in NAV_ITEMS]
        assert "queue" not in keys
        # Below the road to a post, which holds the three steps (2026-10-19).
        assert keys.index("dashboard") == keys.index("create") + 1
        assert qt_app.nav_buttons["dashboard"].text() == "Dashboard"


class TestTheTiles:
    def test_today(self, qt_app, groups, view):
        """No "of 25 allowed today": there is no daily limit (2026-10-18)."""
        post(qt_app, groups[0])
        post(qt_app, groups[1], body="Another wording")
        view.refresh()
        tile = view.tiles["today"]
        assert tile.value.text() == "2"
        assert tile.detail.text() == "Since midnight, Israel time"
        assert not hasattr(view, "meter")

    def test_all_time_includes_what_was_pruned(self, qt_app, groups, view):
        post(qt_app, groups[0])
        qt_app.db.write(
            "INSERT INTO post_totals (group_id, state, count, first_at) VALUES (?, ?, ?, ?)",
            (groups[0].id, TARGET_DONE, 40, to_iso(utcnow() - timedelta(days=200))),
        )
        view.refresh()
        assert view.tiles["all_time"].value.text() == "41"
        assert view.tiles["all_time"].detail.text().startswith("Since ")

    def test_success_rate(self, qt_app, groups, view):
        post(qt_app, groups[0])
        post(qt_app, groups[1], state=TARGET_FAILED, error="Facebook said no.")
        view.refresh()
        assert view.tiles["success"].value.text() == "50%"

    def test_next_scheduled_names_the_repeating_post(self, qt_app, groups, view):
        rule = recurrence.build(["09:00"])
        qt_app.schedule_repo.create(
            name="Bikes", bodies=["a"], group_ids=[groups[0].id], times=list(rule.times),
            next_run_at=recurrence.next_occurrence(rule, utcnow()),
        )
        view.refresh()
        assert view.tiles["next"].detail.text() == "Repeating: Bikes"
        assert view.tiles["repeating"].value.text() == "1"

    def test_paused_says_so(self, qt_app, view):
        from fbposter.worker import PAUSED_KEY

        qt_app.settings_repo.set(PAUSED_KEY, "1")
        view.refresh()
        assert view.tiles["next"].value.text() == "Paused"

    def test_an_empty_database_reads_calmly(self, view):
        view.refresh()
        assert view.tiles["today"].value.text() == "0"
        assert view.tiles["success"].value.text() == "-"
        assert view.tiles["next"].value.text() == "Nothing scheduled"
        assert not view.chart.isVisibleTo(view)
        assert view.chart_empty.isVisibleTo(view)


class TestTheGroupTable:
    def test_a_row_per_group_with_its_counts(self, qt_app, groups, view):
        post(qt_app, groups[1])
        post(qt_app, groups[1], body="Another wording", ago=timedelta(days=3))
        shown = texts(view)
        assert "Demo 1" in shown and "Demo 0" in shown
        assert shown.index("Demo 1") < shown.index("Demo 0"), "the busiest is not first"

    def test_a_long_name_stays_on_one_line_and_is_whole_on_hover(self, qt_app, view):
        """Wrapped, it came out two lines tall in a one-line row and overlapped
        the next group (seen on the real list, 2026-10-14)."""
        from fbposter.qtui.views.dashboard import NAME_CHARS

        name = "Sneakers and streetwear, buy sell and swap, the biggest group in Israel"
        qt_app.group_repo.add_from_url("https://www.facebook.com/groups/long", name=name)
        view.refresh(force=True)
        (label,) = [
            label for label in view.holder.findChildren(QLabel)
            if label.toolTip() == name
        ]
        assert len(label.text()) == NAME_CHARS
        assert label.text().endswith("…")
        assert not label.wordWrap()

    def test_there_is_no_cooldown_column(self, qt_app, groups, view):
        qt_app.group_repo.mark_posted(groups[0].id, utcnow() - timedelta(hours=1))
        shown = texts(view)
        assert "Can post" not in shown
        assert not any(text.startswith("From ") for text in shown)


class TestGoingOut:
    def test_a_waiting_batch_can_be_cancelled_and_undone(self, qt_app, groups, view):
        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        view.refresh()
        cancel = [b for b in view.holder.findChildren(QPushButton) if b.text() == "Cancel"]
        assert len(cancel) == 1

        cancel[0].click()
        assert qt_app.task_repo.get(task.id).state == TASK_CANCELLED
        assert view.going_heading.isHidden()

        qt_app.banner.undo_button.click()
        assert qt_app.task_repo.get(task.id).state == TASK_PENDING
        assert not view.going_heading.isHidden()

    def test_nothing_waiting_hides_the_section(self, qt_app, groups, view):
        post(qt_app, groups[0])
        view.refresh()
        assert view.going_heading.isHidden()
        assert not [b for b in view.holder.findChildren(QPushButton) if b.text() == "Cancel"]

    def test_it_says_how_far_the_batch_got(self, qt_app, groups, view):
        task = qt_app.task_repo.create(BODY, [(g.id, BODY) for g in groups])
        first = qt_app.task_repo.targets_for(task.id)[0]
        qt_app.task_repo.mark_target(first.id, TARGET_DONE, posted=True)
        assert any("1 of 2 groups done" in text for text in texts(view))

    def test_a_waiting_batch_says_why(self, qt_app, groups, view):
        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        note = "Waiting: Chrome is not reachable."
        qt_app.task_repo.set_resume_at(task.id, utcnow() + timedelta(hours=8), note=note)
        assert note in texts(view)

    def test_a_batch_from_the_post_anyway_days_says_nothing_about_it(
        self, qt_app, groups, view
    ):
        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        qt_app.db.write(
            "UPDATE tasks SET overrides = '[\"cooldown\"]' WHERE id = ?", (task.id,)
        )
        assert not any("Post anyway" in text for text in texts(view))

    def test_a_pictures_only_batch_says_so_rather_than_nothing(self, qt_app, groups, view):
        qt_app.task_repo.create("", [(groups[0].id, "")], media_paths=["a.jpg", "b.jpg"])
        assert "Pictures only (2 pictures, no text)" in texts(view)


class TestNoApprovalButtons:
    def test_a_post_held_by_an_admin_offers_no_buttons(self, qt_app, groups, view):
        """Dropped by the user's choice (2026-10-14): the worker follows these
        up by itself."""
        post(qt_app, groups[0], state=TARGET_AWAITING_APPROVAL)
        shown = texts(view)
        assert "Waiting for admin approval" in shown
        captions = {b.text() for b in view.holder.findChildren(QPushButton)}
        assert not captions & {"Went live", "Declined"}


class TestRecentActivity:
    def test_newest_first_with_the_reason_under_a_failure(self, qt_app, groups, view):
        reason = "Facebook showed a warning about posting too fast."
        post(qt_app, groups[0], ago=timedelta(hours=2))
        post(qt_app, groups[1], state=TARGET_FAILED, ago=timedelta(minutes=10), error=reason)
        shown = recent_texts(view)
        assert reason in shown
        assert shown.index("Not posted") < shown.index("Posted")

    def test_a_post_that_may_be_out_never_says_not_posted(self, qt_app, groups, view):
        post(qt_app, groups[0], state=TARGET_UNCONFIRMED, error="Check the group.")
        shown = recent_texts(view)
        assert "Might have posted" in shown
        assert "Not posted" not in shown

    def test_pictures_nobody_could_check_say_so(self, qt_app, groups, view):
        task = qt_app.task_repo.create("", [(groups[0].id, "")], media_paths=["a.jpg"])
        (target,) = qt_app.task_repo.targets_for(task.id)
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        qt_app.task_repo.mark_task(task.id, TASK_DONE, finished=True)
        shown = recent_texts(view)
        assert "Uploaded, not verified" in shown
        assert "Posted" not in shown

    def test_a_turn_that_never_came_is_not_activity(self, qt_app, groups, view):
        qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        assert "Nothing yet. Write one in Create a post." in recent_texts(view)

    def test_no_label_uses_the_worker_s_own_words(self, qt_app, groups, view):
        post(qt_app, groups[0], state=TARGET_SKIPPED, error="Cooldown.")
        post(qt_app, groups[1], state=TARGET_FAILED)
        qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        shown = recent_texts(view)
        for word in ("Halted", "Pending", "Done", "Failed", "Skipped", "Running"):
            assert word not in shown


class TestTheLabels:
    """Moved over from the Queue: every label answers "did it post?"."""

    def batch(self, qt_app, groups):
        task = qt_app.task_repo.create(
            BODY, [(g.id, f"{BODY} {i}") for i, g in enumerate(groups)]
        )
        return task, qt_app.task_repo.targets_for(task.id)

    def finish(self, qt_app, task, state):
        qt_app.task_repo.mark_task(task.id, state, finished=True)
        return qt_app.task_repo.get(task.id), qt_app.task_repo.targets_for(task.id)

    def test_every_group_posted(self, qt_app, groups):
        task, targets = self.batch(qt_app, groups)
        for target in targets:
            qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True, post_url="u")
        assert task_label(*self.finish(qt_app, task, TASK_DONE)) == ("Posted", "SUCCESS")

    def test_some_posted(self, qt_app, groups):
        task, (first, second) = self.batch(qt_app, groups)
        qt_app.task_repo.mark_target(first.id, TARGET_DONE, posted=True, post_url="u")
        qt_app.task_repo.mark_target(second.id, TARGET_SKIPPED)
        words, _colour = task_label(*self.finish(qt_app, task, TASK_DONE))
        assert words == "Posted to 1 of 2 groups"

    def test_every_group_skipped_is_not_done(self, qt_app, groups):
        task, targets = self.batch(qt_app, groups)
        for target in targets:
            qt_app.task_repo.mark_target(target.id, TARGET_SKIPPED)
        words, _colour = task_label(*self.finish(qt_app, task, TASK_DONE))
        assert words == "Nothing posted"

    def test_a_stopped_batch(self, qt_app, groups):
        task, _targets = self.batch(qt_app, groups)
        assert task_label(*self.finish(qt_app, task, TASK_HALTED)) == ("Stopped", "DANGER")

    def test_a_group_left_behind_by_a_stopped_batch_says_not_posted(self, qt_app, groups):
        _task, targets = self.batch(qt_app, groups)
        assert target_label(targets[0], TASK_HALTED)[0] == "Not posted"
        assert target_label(targets[0], TASK_PENDING)[0] == "Waiting its turn"


class TestTheDrawnParts:
    @pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
    def test_they_paint_in_both_modes(self, qt_application, dark):
        from datetime import date

        from fbposter.qtui import theme

        theme.activate(dark=dark)
        try:
            bars = DayBars()
            bars.set_days([(date(2026, 10, day), day % 4) for day in range(1, 15)])
            bars.resize(700, 170)
            outcome = OutcomeBar()
            outcome.set_counts({TARGET_DONE: 5, TARGET_FAILED: 1})
            outcome.resize(700, 14)
            for widget in (bars, outcome):
                assert not widget.grab().isNull()
        finally:
            theme.activate(dark=False)
