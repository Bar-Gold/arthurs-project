"""Tests for queue retention.

The queue screen used to grow forever. It now shows live batches plus recent
history, with an "All" toggle for the rest.

The thing these tests are really guarding is that retention is a *view* filter.
`task_targets` rows are what `GroupRepo.recent_bodies` reads to refuse sending
the same words to a group twice, and what `posted_count_since` counts for the
daily cap. Deleting history to tidy the screen would quietly switch off both.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter.db.models import (
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_SKIPPED,
    TARGET_UNCONFIRMED,
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
        """Failures age out the same way successes do: they asked for both."""
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


class TestResolvingAPendingPostFromTheQueue:
    """The user is the only one who knows what the admin decided, so the app
    asks rather than guessing -- but it must actually offer somewhere to say."""

    def pending(self, qt_app):
        group = qt_app.group_repo.list()[0]
        task = qt_app.task_repo.create(BODY, [(group.id, BODY)])
        target = qt_app.task_repo.targets_for(task.id)[0]
        qt_app.task_repo.mark_target(target.id, TARGET_AWAITING_APPROVAL, posted=True)
        return group, task, target

    def test_a_pending_target_offers_both_answers(self, qt_app, repos):
        from PySide6.QtWidgets import QPushButton

        self.pending(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        labels = {b.text() for b in view.holder.findChildren(QPushButton)}
        assert {"Went live", "Declined"} <= labels

    def test_an_ordinary_target_does_not(self, qt_app, repos):
        from PySide6.QtWidgets import QPushButton

        group = qt_app.group_repo.list()[0]
        task = qt_app.task_repo.create(BODY, [(group.id, BODY)])
        target = qt_app.task_repo.targets_for(task.id)[0]
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)

        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        labels = {b.text() for b in view.holder.findChildren(QPushButton)}
        assert "Declined" not in labels

    def test_declining_frees_the_wording(self, qt_app, repos):
        group, _task, target = self.pending(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")

        view.resolve_pending(target.id, approved=False)
        assert BODY not in qt_app.group_repo.recent_bodies(group.id)

    def test_marking_it_live_keeps_the_wording_claimed(self, qt_app, repos):
        group, _task, target = self.pending(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")

        view.resolve_pending(target.id, approved=True)
        assert BODY in qt_app.group_repo.recent_bodies(group.id)

    def test_resolving_twice_is_reported_not_silent(self, qt_app, repos):
        _group, _task, target = self.pending(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        view.resolve_pending(target.id, approved=False)
        view.resolve_pending(target.id, approved=False)
        assert "no longer awaiting" in qt_app.toast_label.text()

    def test_the_state_shows_as_declined_afterwards(self, qt_app, repos):
        _group, task, target = self.pending(qt_app)
        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        view.resolve_pending(target.id, approved=False)
        assert qt_app.task_repo.targets_for(task.id)[0].state == TARGET_DECLINED


class TestAPicturesOnlyBatch:
    def test_its_card_says_so_rather_than_nothing(self, qt_app, repos):
        from PySide6.QtWidgets import QLabel

        group = qt_app.group_repo.list()[0]
        qt_app.task_repo.create("", [(group.id, "")], media_paths=["a.jpg", "b.jpg"])
        view = qt_app.views["queue"]
        qt_app.show_view("queue")

        titles = [label.text() for label in view.findChildren(QLabel)]
        assert "Pictures only (2 pictures, no text)" in titles


class TestEveryLabelSaysWhetherItPosted:
    """The worker's own words -- "halted", "skipped", "pending" -- told the user
    nothing, and two of them were wrong: groups left behind by a stopped batch
    said "Pending" for ever, and a batch where every group was skipped said
    "Done" in green."""

    def card_texts(self, qt_app):
        from PySide6.QtWidgets import QLabel

        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        view.refresh(force=True)
        return [label.text() for label in view.holder.findChildren(QLabel)]

    def batch(self, qt_app, count=2, **create):
        groups = qt_app.group_repo.list()[:count]
        task = qt_app.task_repo.create(
            BODY, [(g.id, f"{BODY} {i}") for i, g in enumerate(groups)], **create
        )
        return task, qt_app.task_repo.targets_for(task.id)

    def finish(self, qt_app, task, state, error=""):
        qt_app.task_repo.mark_task(task.id, state, error=error, finished=True)

    def test_a_stopped_batch(self, qt_app, repos):
        task, (first, _second) = self.batch(qt_app)
        reason = "Facebook showed its login page."
        qt_app.task_repo.mark_target(first.id, TARGET_FAILED, error=reason)
        self.finish(qt_app, task, TASK_HALTED, error=reason)

        texts = self.card_texts(qt_app)
        assert "Stopped" in texts
        assert texts.count("Not posted") == 2, "the group after it said Pending"
        assert texts.count(reason) == 1, "the reason was printed twice"
        assert any(
            t.startswith("Stopped at Demo 0") and "after it were not posted" in t
            for t in texts
        )

    def test_a_post_that_may_be_out_never_says_not_posted(self, qt_app, repos):
        task, (first,) = self.batch(qt_app, count=1)
        qt_app.task_repo.mark_target(first.id, TARGET_UNCONFIRMED, error="Check it.")
        self.finish(qt_app, task, TASK_HALTED, error="Check it.")

        texts = self.card_texts(qt_app)
        assert "Might have posted" in texts
        assert "Not posted" not in texts

    def test_every_group_skipped_is_not_done(self, qt_app, repos):
        task, targets = self.batch(qt_app)
        for target in targets:
            qt_app.task_repo.mark_target(target.id, TARGET_SKIPPED, error="Cooldown.")
        self.finish(qt_app, task, TASK_DONE)

        texts = self.card_texts(qt_app)
        assert "Nothing posted" in texts
        assert texts.count("Not posted") == 2
        assert "Done" not in texts

    def test_every_group_posted(self, qt_app, repos):
        task, targets = self.batch(qt_app)
        for target in targets:
            qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        self.finish(qt_app, task, TASK_DONE)

        texts = self.card_texts(qt_app)
        assert texts.count("Posted") == 3  # the batch and both groups

    def test_some_posted(self, qt_app, repos):
        task, (first, second) = self.batch(qt_app)
        qt_app.task_repo.mark_target(first.id, TARGET_DONE, posted=True)
        qt_app.task_repo.mark_target(second.id, TARGET_SKIPPED, error="Cooldown.")
        self.finish(qt_app, task, TASK_DONE)

        assert "Posted to 1 of 2 groups" in self.card_texts(qt_app)

    def test_a_cancelled_batch_leaves_nothing_pending(self, qt_app, repos):
        task, _targets = self.batch(qt_app)
        qt_app.task_repo.cancel(task.id)

        texts = self.card_texts(qt_app)
        assert "Cancelled" in texts
        assert texts.count("Not posted") == 2
        assert "Pending" not in texts

    def test_a_batch_still_to_go(self, qt_app, repos):
        self.batch(qt_app)
        texts = self.card_texts(qt_app)
        assert "Queued" in texts
        assert texts.count("Waiting its turn") == 2

    def test_a_waiting_batch_says_why(self, qt_app, repos):
        task, _targets = self.batch(qt_app)
        note = "Waiting: it is outside the posting hours (08:00-23:00)."
        qt_app.task_repo.set_resume_at(task.id, utcnow() + timedelta(hours=8), note=note)
        assert note in self.card_texts(qt_app)

    def test_a_post_anyway_batch_that_went_out_reads_posted(self, qt_app, repos):
        task, (first,) = self.batch(qt_app, count=1, overrides={"cooldown"})
        qt_app.task_repo.mark_target(first.id, TARGET_DONE, posted=True)
        self.finish(qt_app, task, TASK_DONE)

        texts = self.card_texts(qt_app)
        assert texts.count("Posted") == 2
        assert not any("Not posted" in t for t in texts)

    def test_no_label_uses_the_worker_s_own_words(self, qt_app, repos):
        for state in (TASK_HALTED, TASK_DONE, TASK_PENDING):
            task, targets = self.batch(qt_app)
            qt_app.task_repo.mark_target(targets[0].id, TARGET_SKIPPED)
            if state != TASK_PENDING:
                self.finish(qt_app, task, state)
        texts = self.card_texts(qt_app)
        for word in ("Halted", "Pending", "Done", "Failed", "Skipped", "Running"):
            assert word not in texts


class TestPicturesNobodyCouldCheck:
    """A post of pictures that Facebook's reply did not confirm: the post
    window closed, and that is all there was to go on."""

    def picture_batch(self, qt_app, post_url=""):
        group = qt_app.group_repo.list()[0]
        task = qt_app.task_repo.create("", [(group.id, "")], media_paths=["a.jpg"])
        target = qt_app.task_repo.targets_for(task.id)[0]
        qt_app.task_repo.mark_target(
            target.id,
            TARGET_DONE,
            posted=True,
            post_url=post_url,
            error="" if post_url else "Only pictures were uploaded.",
        )
        qt_app.task_repo.mark_task(task.id, TASK_DONE, finished=True)

    def texts(self, qt_app):
        from PySide6.QtWidgets import QLabel

        view = qt_app.views["queue"]
        qt_app.show_view("queue")
        view.refresh(force=True)
        return [label.text() for label in view.holder.findChildren(QLabel)]

    def test_it_says_uploaded_not_verified(self, qt_app, repos):
        self.picture_batch(qt_app)
        texts = self.texts(qt_app)
        assert texts.count("Uploaded, not verified") == 2  # the batch and the group
        assert "Posted" not in texts

    def test_confirmed_by_facebook_it_says_posted(self, qt_app, repos):
        self.picture_batch(qt_app, post_url="https://www.facebook.com/groups/1/permalink/2/")
        texts = self.texts(qt_app)
        assert texts.count("Posted") == 2
