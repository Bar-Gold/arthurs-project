"""Tests for queue retention.

The queue screen used to grow forever. It now shows live batches plus recent
history, with an "All" toggle for the rest.

The thing these tests are really guarding is that retention is a *view* filter.
`task_targets` rows are what `GroupRepo.recent_bodies` reads to refuse sending
the same words to a group twice, and what `posted_count_since` counts for the
daily cap — deleting history to tidy the screen would quietly switch off both.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter.db.models import (
    TARGET_DONE,
    TASK_DONE,
    TASK_HALTED,
    TASK_PENDING,
    to_iso,
    utcnow,
)
from fbposter.db.repo import GroupRepo, TaskRepo

BODY = "Selling a road bike, 54cm frame."


@pytest.fixture
def repos(qt_app):
    for index in range(2):
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
    return qt_app.group_repo, qt_app.task_repo


def make_task(qt_app, state=TASK_PENDING, finished_ago_hours=None, body=BODY):
    """A batch, optionally finished a given number of hours ago."""
    group = qt_app.group_repo.list()[0]
    task = qt_app.task_repo.create(body, [(group.id, body)])
    if finished_ago_hours is not None:
        qt_app.db.write(
            "UPDATE tasks SET state = ?, finished_at = ? WHERE id = ?",
            (state, to_iso(utcnow() - timedelta(hours=finished_ago_hours)), task.id),
        )
    return task


class TestTheRetentionQuery:
    def test_a_live_batch_is_always_listed(self, qt_app, repos):
        """However old. Hiding something still due to go out would be worse."""
        task = make_task(qt_app)
        qt_app.db.write(
            "UPDATE tasks SET created_at = ? WHERE id = ?",
            (to_iso(utcnow() - timedelta(days=90)), task.id),
        )
        listed = qt_app.task_repo.list_for_queue(utcnow(), 24)
        assert [t.id for t in listed] == [task.id]

    def test_a_recently_finished_batch_is_listed(self, qt_app, repos):
        task = make_task(qt_app, TASK_DONE, finished_ago_hours=2)
        listed = qt_app.task_repo.list_for_queue(utcnow(), 24)
        assert [t.id for t in listed] == [task.id]

    def test_an_old_finished_batch_is_not(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        assert qt_app.task_repo.list_for_queue(utcnow(), 24) == []

    def test_an_old_failed_batch_is_not_either(self, qt_app, repos):
        """Failures age out the same way successes do — they asked for both."""
        make_task(qt_app, TASK_HALTED, finished_ago_hours=48)
        assert qt_app.task_repo.list_for_queue(utcnow(), 24) == []

    def test_the_boundary_is_inclusive(self, qt_app, repos):
        task = make_task(qt_app, TASK_DONE, finished_ago_hours=24)
        listed = qt_app.task_repo.list_for_queue(utcnow() + timedelta(seconds=1), 24)
        assert [t.id for t in listed] == []
        listed = qt_app.task_repo.list_for_queue(utcnow() - timedelta(seconds=1), 24)
        assert [t.id for t in listed] == [task.id]

    def test_zero_hours_means_no_filtering(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=500)
        assert len(qt_app.task_repo.list_for_queue(utcnow(), 0)) == 1

    def test_hidden_batches_are_counted(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        make_task(qt_app, TASK_DONE, finished_ago_hours=72)
        make_task(qt_app, TASK_DONE, finished_ago_hours=1)
        make_task(qt_app)
        assert qt_app.task_repo.count_older_than(utcnow(), 24) == 2

    def test_live_batches_are_never_counted_as_hidden(self, qt_app, repos):
        task = make_task(qt_app)
        qt_app.db.write(
            "UPDATE tasks SET created_at = ? WHERE id = ?",
            (to_iso(utcnow() - timedelta(days=90)), task.id),
        )
        assert qt_app.task_repo.count_older_than(utcnow(), 24) == 0


class TestNothingIsDeleted:
    def test_hidden_history_still_blocks_a_repeat(self, qt_app, repos):
        """The whole reason retention is a filter and not a purge."""
        groups, tasks = repos
        group = groups.list()[0]
        task = tasks.create(BODY, [(group.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_DONE, posted=True)
        qt_app.db.write(
            "UPDATE tasks SET state = ?, finished_at = ? WHERE id = ?",
            (TASK_DONE, to_iso(utcnow() - timedelta(days=365)), task.id),
        )

        # Long gone from the screen...
        assert qt_app.task_repo.list_for_queue(utcnow(), 24) == []
        # ...but the guard can still see it.
        assert BODY in groups.recent_bodies(group.id)

    def test_hidden_history_still_counts_towards_the_daily_cap(self, qt_app, repos):
        groups, tasks = repos
        group = groups.list()[0]
        task = tasks.create(BODY, [(group.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_DONE, posted=True)
        qt_app.db.write(
            "UPDATE tasks SET state = ?, finished_at = ? WHERE id = ?",
            (TASK_DONE, to_iso(utcnow() - timedelta(hours=48)), task.id),
        )

        assert qt_app.task_repo.list_for_queue(utcnow(), 24) == []
        assert tasks.posted_count_since(utcnow() - timedelta(hours=1)) == 1


class TestTheQueueScreen:
    def test_it_starts_on_recent(self, qt_app):
        view = qt_app.views["queue"]
        assert view.show_all is False

    def test_old_batches_are_not_shown(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        # Just the "nothing recent" note plus the trailing stretch.
        assert view.rows.count() == 2

    def test_it_says_how_many_are_hidden(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        assert "1 finished batch older than 24h hidden" in view.hidden_label.text()

    def test_nothing_hidden_means_no_notice(self, qt_app, repos):
        make_task(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        assert view.hidden_label.text() == ""

    def test_the_all_toggle_brings_them_back(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        view.set_scope(True)
        assert view.rows.count() == 2  # one card plus the stretch
        assert view.all_button.isChecked()

    def test_switching_back_hides_them_again(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        view.set_scope(True)
        view.set_scope(False)
        assert view.recent_button.isChecked()
        assert "hidden" in view.hidden_label.text()

    def test_a_live_batch_shows_under_recent(self, qt_app, repos):
        make_task(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        assert view.rows.count() == 2  # one card plus the stretch

    def test_the_window_is_configurable(self, qt_app, repos):
        make_task(qt_app, TASK_DONE, finished_ago_hours=48)
        qt_app.settings_repo.set("queue_retention_hours", 72)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        assert view.retention_hours() == 72
        assert view.hidden_label.text() == ""
