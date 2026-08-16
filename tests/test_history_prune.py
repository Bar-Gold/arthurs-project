"""Tests for permanently deleting old queue history.

Two things are in tension here and the tests exist to hold both:

* the database must not grow for ever, and
* `GroupRepo.recent_bodies` must keep seeing what a group has already been
  sent, because that is what `guards.check_repeat_text` reads to refuse
  reposting the same wording — the app's main protection against a restriction.

So the prune deletes old batches but keeps the newest `RECENT_BODIES_LIMIT`
posted bodies per group for ever. The test that matters most is
TestTheGuardsSurvive::test_a_wording_from_years_ago_is_still_refused.
"""

from __future__ import annotations

import queue
import random
from datetime import timedelta

import pytest

from fbposter import clock, guards
from fbposter.automation.humanize import HumanProfile, Humanizer
from fbposter.automation.poster import PostOutcome, PostRequest
from fbposter.db import Database
from fbposter.db.models import (
    TARGET_DONE,
    TARGET_FAILED,
    TASK_DONE,
    TASK_HALTED,
    TASK_PENDING,
    to_iso,
)
from fbposter.db.repo import (
    RECENT_BODIES_LIMIT,
    GroupRepo,
    SettingsRepo,
    TaskRepo,
)
from fbposter.power import SleepBlocker
from fbposter.worker import PRUNE_EVERY, PostingWorker

NOON = clock.parse_local("2026-08-10 12:00")


class FakePoster:
    def post(self, request: PostRequest) -> PostOutcome:
        return PostOutcome(posted=True, verified=True, detail="ok")

    def verify(self, group_url: str, body: str) -> bool:
        return False


class Clock:
    def __init__(self, start=NOON) -> None:
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "prune.db")
    yield database
    database.close()


@pytest.fixture
def repos(db):
    return GroupRepo(db), TaskRepo(db), SettingsRepo(db)


def add_group(groups, name="g0"):
    return groups.add_from_url(f"https://www.facebook.com/groups/{name}")


def posted_batch(db, tasks, group, body, days_ago, state=TASK_DONE,
                 target_state=TARGET_DONE):
    """A finished batch, `days_ago` days back.

    `target_state` matters: only a *done* target holds a body the repeated-text
    guard reads, so only those join the keep-set. A batch that never posted is
    ordinary history and can go.
    """
    when = NOON - timedelta(days=days_ago)
    task = tasks.create(body, [(group.id, body)])
    target = tasks.targets_for(task.id)[0]
    db.write(
        "UPDATE task_targets SET state = ?, posted_at = ? WHERE id = ?",
        (target_state, to_iso(when) if target_state == TARGET_DONE else None, target.id),
    )
    db.write(
        "UPDATE tasks SET state = ?, finished_at = ? WHERE id = ?",
        (state, to_iso(when), task.id),
    )
    return task


def fill_keep_set(db, tasks, group, count=RECENT_BODIES_LIMIT, days_ago=10):
    """Enough recent posts that older ones stop being protected."""
    for index in range(count):
        posted_batch(db, tasks, group, f"filler {index}", days_ago=days_ago + index)


def make_worker(db, clock_fn=None):
    return PostingWorker(
        db,
        poster=FakePoster(),
        now=clock_fn or Clock(),
        sleep=lambda _s: None,
        humanizer=Humanizer(
            profile=HumanProfile(group_gap_seconds=(600, 1500)),
            rng=random.Random(3),
            sleep=lambda _s: None,
        ),
        blocker=SleepBlocker(setter=lambda _flags: None),
        events=queue.Queue(),
    )


class TestWhatGetsDeleted:
    def test_an_old_batch_past_the_keep_set_goes(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "ancient wording", days_ago=200)
        fill_keep_set(db, tasks, group)

        assert tasks.prune_history(NOON, keep_days=90) == 1
        assert "ancient wording" not in [t.body for t in tasks.list_recent()]

    def test_an_old_batch_that_never_posted_goes(self, db, repos):
        """A failed target holds no body the guard reads, so it is just history."""
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "failed wording", days_ago=200,
                     state=TASK_HALTED, target_state=TARGET_FAILED)
        assert tasks.prune_history(NOON, keep_days=90) == 1
        assert tasks.list_recent() == []

    def test_a_lone_old_post_is_kept_because_the_guard_still_needs_it(self, db, repos):
        """Deliberately conservative: with few posts to a group, nothing ages
        out, because every one of them is still in the guard's window."""
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "the only thing ever sent here", days_ago=3000)

        assert tasks.prune_history(NOON, keep_days=90) == 0
        assert groups.recent_bodies(group.id) == ["the only thing ever sent here"]

    def test_a_recent_batch_stays(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "recent wording", days_ago=3)

        assert tasks.prune_history(NOON, keep_days=90) == 0
        assert len(tasks.list_recent()) == 1

    def test_an_unfinished_batch_never_goes_however_old(self, db, repos):
        """A batch still due to go out is not history."""
        groups, tasks, _settings = repos
        group = add_group(groups)
        task = tasks.create("still waiting", [(group.id, "still waiting")])
        db.write(
            "UPDATE tasks SET created_at = ? WHERE id = ?",
            (to_iso(NOON - timedelta(days=900)), task.id),
        )

        assert tasks.prune_history(NOON, keep_days=90) == 0
        assert tasks.get(task.id).state == TASK_PENDING

    def test_the_targets_go_with_the_batch(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "failed wording", days_ago=200,
                     state=TASK_HALTED, target_state=TARGET_FAILED)

        tasks.prune_history(NOON, keep_days=90)
        left = db.query_one("SELECT COUNT(*) AS n FROM task_targets")["n"]
        assert left == 0

    def test_zero_days_disables_it_entirely(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "ancient wording", days_ago=9000)

        assert tasks.prune_history(NOON, keep_days=0) == 0
        assert len(tasks.list_recent()) == 1

    def test_nothing_to_do_is_not_an_error(self, db, repos):
        _groups, tasks, _settings = repos
        assert tasks.prune_history(NOON, keep_days=90) == 0


class TestTheGuardsSurvive:
    """The reason this is a keep-set and not a plain DELETE."""

    def test_the_newest_bodies_per_group_are_kept_for_ever(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        for index in range(RECENT_BODIES_LIMIT):
            posted_batch(db, tasks, group, f"wording {index}", days_ago=500 + index)

        assert tasks.prune_history(NOON, keep_days=90) == 0
        assert len(groups.recent_bodies(group.id)) == RECENT_BODIES_LIMIT

    def test_beyond_that_the_oldest_are_dropped(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        extra = 5
        for index in range(RECENT_BODIES_LIMIT + extra):
            # Higher index == older, so the first few are the newest.
            posted_batch(db, tasks, group, f"wording {index}", days_ago=500 + index)

        assert tasks.prune_history(NOON, keep_days=90) == extra
        kept = groups.recent_bodies(group.id)
        assert len(kept) == RECENT_BODIES_LIMIT
        assert "wording 0" in kept
        assert f"wording {RECENT_BODIES_LIMIT + extra - 1}" not in kept

    def test_a_wording_from_years_ago_is_still_refused(self, db, repos):
        """The whole point. Tidying the database must not let a repeat through."""
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "Selling a bike, 1800.", days_ago=1000)

        tasks.prune_history(NOON, keep_days=90)

        violation = guards.check_repeat_text(
            "Selling a bike, 1800.", groups.recent_bodies(group.id), group.display_name
        )
        assert violation is not None, "pruning let a repeat through"

    def test_the_keep_set_is_per_group_not_global(self, db, repos):
        groups, tasks, _settings = repos
        one = add_group(groups, "one")
        two = add_group(groups, "two")
        for index in range(RECENT_BODIES_LIMIT):
            posted_batch(db, tasks, one, f"one {index}", days_ago=500 + index)
        posted_batch(db, tasks, two, "two only", days_ago=900)

        tasks.prune_history(NOON, keep_days=90)
        assert len(groups.recent_bodies(one.id)) == RECENT_BODIES_LIMIT
        assert groups.recent_bodies(two.id) == ["two only"]

    def test_the_daily_cap_is_unaffected(self, db, repos):
        """The window is far wider than the cap's local-midnight lookback."""
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "today's wording", days_ago=0)
        posted_batch(db, tasks, group, "ancient wording", days_ago=400)

        tasks.prune_history(NOON, keep_days=90)
        counted = tasks.posted_count_since(clock.start_of_local_day(NOON))
        assert counted == 1

    def test_the_limit_matches_what_the_guard_reads(self):
        """Two numbers that must never drift apart."""
        import inspect

        source = inspect.signature(GroupRepo.recent_bodies)
        assert source.parameters["limit"].default == RECENT_BODIES_LIMIT


class TestTheWorkerRunsIt:
    def test_it_prunes_on_the_first_tick(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "ancient wording", days_ago=400,
                     state=TASK_HALTED, target_state=TARGET_FAILED)

        make_worker(db).run_once()
        assert tasks.list_recent() == []

    def test_it_does_not_prune_again_the_same_day(self, db, repos):
        groups, tasks, settings = repos
        group = add_group(groups)
        worker = make_worker(db)
        worker.run_once()
        first = settings.get("last_prune_at", "")

        posted_batch(db, tasks, group, "ancient wording", days_ago=400,
                     state=TASK_HALTED, target_state=TARGET_FAILED)
        worker.run_once()
        assert len(tasks.list_recent()) == 1, "pruned twice in one day"
        assert settings.get("last_prune_at", "") == first

    def test_it_prunes_again_the_next_day(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        ticker = Clock()
        worker = make_worker(db, ticker)
        worker.run_once()

        posted_batch(db, tasks, group, "ancient wording", days_ago=400,
                     state=TASK_HALTED, target_state=TARGET_FAILED)
        ticker.now = NOON + PRUNE_EVERY + timedelta(minutes=1)
        worker.run_once()
        assert tasks.list_recent() == []

    def test_the_setting_can_switch_it_off(self, db, repos):
        groups, tasks, settings = repos
        group = add_group(groups)
        settings.set("history_retention_days", 0)
        posted_batch(db, tasks, group, "ancient wording", days_ago=9000)

        make_worker(db).run_once()
        assert len(tasks.list_recent()) == 1

    def test_a_broken_prune_does_not_stop_posting(self, db, repos):
        """Housekeeping must never be able to stall the queue."""
        groups, tasks, _settings = repos
        group = add_group(groups)
        tasks.create("go out", [(group.id, "go out")])

        worker = make_worker(db)
        worker.tasks.prune_history = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("disk on fire")
        )
        assert worker.run_once() is True
        kinds = []
        while not worker.events.empty():
            kinds.append(worker.events.get_nowait().kind)
        assert "error" in kinds

    def test_it_reports_what_it_cleared(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        posted_batch(db, tasks, group, "ancient wording", days_ago=400,
                     state=TASK_HALTED, target_state=TARGET_FAILED)

        worker = make_worker(db)
        worker.run_once()
        messages = []
        while not worker.events.empty():
            messages.append(worker.events.get_nowait().message)
        assert any("Cleared 1 finished batch" in m for m in messages)


class TestReclaimingSpace:
    def test_vacuum_runs_without_error(self, db, repos):
        _groups, tasks, _settings = repos
        assert tasks.reclaim_space() is True

    def test_the_file_actually_shrinks(self, db, repos):
        groups, tasks, _settings = repos
        group = add_group(groups)
        big = "x" * 4000
        for index in range(60):
            posted_batch(db, tasks, group, f"{big} {index}", days_ago=500 + index)

        before = db.path.stat().st_size
        tasks.prune_history(NOON, keep_days=90)
        tasks.reclaim_space()
        assert db.path.stat().st_size < before
