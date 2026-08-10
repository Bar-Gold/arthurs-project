"""Tests for the posting worker.

Real database, fake poster, fake clock. `run_once()` is driven directly rather
than starting the thread, so the whole schedule -- including hour-long gaps and
next-morning deferrals -- is exercised instantly and deterministically.
"""

from __future__ import annotations

import queue
import random
from datetime import timedelta

import pytest

from fbposter import clock
from fbposter.automation.humanize import HumanProfile, Humanizer
from fbposter.automation.poster import PostOutcome, PostRequest
from fbposter.db import Database
from fbposter.db.models import (
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_PENDING,
    TARGET_RUNNING,
    TARGET_SKIPPED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_HALTED,
    TASK_MISSED,
    TASK_RUNNING,
)
from fbposter.db.repo import GroupRepo, SettingsRepo, TaskRepo
from fbposter.errors import AutomationHalted, PostNotVerified
from fbposter.power import SleepBlocker
from fbposter.worker import MISSED_GRACE, PostingWorker

BODY = "Selling a road bike, 54cm frame."

# Midday Israel time, comfortably inside the 08:00-23:00 window.
NOON = clock.parse_local("2026-08-10 12:00")


class FakePoster:
    """Records requests; behaviour per group is scripted by the test."""

    def __init__(self, raises=None, verify_result: bool = False) -> None:
        self.requests: list[PostRequest] = []
        self.raises = raises or {}
        self.verify_result = verify_result
        self.verify_calls: list[str] = []

    def post(self, request: PostRequest) -> PostOutcome:
        self.requests.append(request)
        problem = self.raises.get(request.group_url)
        if problem is not None:
            raise problem
        return PostOutcome(posted=True, verified=True, detail="ok")

    def verify(self, group_url: str, body: str) -> bool:
        self.verify_calls.append(group_url)
        return self.verify_result

    @property
    def group_urls(self) -> list[str]:
        return [r.group_url for r in self.requests]


class Clock:
    """A hand-wound clock, so a 25 minute gap costs no real time."""

    def __init__(self, start=NOON) -> None:
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "worker.db")
    yield database
    database.close()


@pytest.fixture
def repos(db):
    return GroupRepo(db), TaskRepo(db), SettingsRepo(db)


def make_worker(db, poster=None, clock_fn=None, gap_seconds=(600, 1500)):
    return PostingWorker(
        db,
        poster=poster or FakePoster(),
        now=clock_fn or Clock(),
        sleep=lambda _s: None,
        humanizer=Humanizer(
            profile=HumanProfile(group_gap_seconds=gap_seconds),
            rng=random.Random(3),
            sleep=lambda _s: None,
        ),
        blocker=SleepBlocker(setter=lambda _flags: None),
        events=queue.Queue(),
    )


def add_groups(groups, count=2):
    return [
        groups.add_from_url(f"https://www.facebook.com/groups/g{index}")
        for index in range(count)
    ]


def drain(worker, limit=40):
    """Run until nothing more can be done right now."""
    for _ in range(limit):
        if not worker.run_once():
            return
    raise AssertionError("worker did not settle")


def kinds(worker) -> list[str]:
    out = []
    while not worker.events.empty():
        out.append(worker.events.get_nowait().kind)
    return out


class TestSerialExecution:
    def test_one_group_per_iteration(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY), (two.id, "different text")])

        poster = FakePoster()
        worker = make_worker(db, poster)

        worker.run_once()
        assert len(poster.requests) == 1, "posted more than one group in one step"

    def test_the_gap_blocks_the_next_group_until_it_expires(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, "second text")])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)

        worker.run_once()
        assert len(poster.requests) == 1

        # Nothing more is due yet: the gap is a stored instant, not a sleep.
        assert worker.run_once() is False
        assert len(poster.requests) == 1

        stored = tasks.get(task.id).resume_at
        assert stored is not None and stored > ticker.now

        ticker.advance(minutes=30)
        worker.run_once()
        assert len(poster.requests) == 2

    def test_the_gap_is_inside_the_ten_to_twentyfive_minute_band(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, "second")])

        ticker = Clock()
        worker = make_worker(db, FakePoster(), ticker)
        worker.run_once()

        gap = (tasks.get(task.id).resume_at - ticker.now).total_seconds()
        assert 600 <= gap <= 1500

    def test_a_batch_already_running_goes_before_a_newer_one(self, db, repos):
        """Batches never interleave."""
        groups, tasks, _ = repos
        one, two, three = add_groups(groups, 3)
        first = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])
        tasks.create("newer", [(three.id, "c")])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)

        worker.run_once()
        ticker.advance(minutes=30)
        worker.run_once()

        assert tasks.get(first.id).state == TASK_DONE
        assert len(poster.requests) == 2
        assert poster.group_urls[1] == two.url

    def test_a_whole_batch_completes(self, db, repos):
        groups, tasks, _ = repos
        made = add_groups(groups, 3)
        task = tasks.create(BODY, [(g.id, f"text {i}") for i, g in enumerate(made)])

        ticker = Clock()
        worker = make_worker(db, FakePoster(), ticker)
        for _ in range(6):
            worker.run_once()
            ticker.advance(minutes=30)

        assert tasks.get(task.id).state == TASK_DONE
        assert all(t.state == TARGET_DONE for t in tasks.targets_for(task.id))


class TestOutcomesArePersisted:
    def test_each_group_is_committed_as_it_finishes(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        worker = make_worker(db)
        worker.run_once()

        states = [t.state for t in tasks.targets_for(task.id)]
        assert states == [TARGET_DONE, TARGET_PENDING]

    def test_the_group_last_posted_time_is_updated(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        make_worker(db).run_once()
        assert groups.get(one.id).last_posted_at is not None

    def test_posting_the_media_from_the_task(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)], media_paths=["a.png", "b.png"])

        poster = FakePoster()
        make_worker(db, poster).run_once()
        assert [p.name for p in poster.requests[0].media_paths] == ["a.png", "b.png"]

    def test_the_per_group_body_is_used_not_the_task_body(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create("task level", [(one.id, "group level")])

        poster = FakePoster()
        make_worker(db, poster).run_once()
        assert poster.requests[0].body == "group level"


class TestDryRunOutcomes:
    """A poster that published nothing must not be recorded as if it had.

    Marking a rehearsal 'done' would start the group's real cooldown and count
    against the real daily cap.
    """

    def test_a_dry_run_is_not_recorded_as_posted(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        class DryPoster(FakePoster):
            def post(self, request):
                super().post(request)
                return PostOutcome(
                    posted=False, dry_run=True, detail="Dry run: nothing published."
                )

        worker = make_worker(db, DryPoster())
        worker.run_once()

        assert tasks.targets_for(task.id)[0].state == TARGET_SKIPPED

    def test_a_dry_run_does_not_start_a_cooldown(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        class DryPoster(FakePoster):
            def post(self, request):
                super().post(request)
                return PostOutcome(posted=False, dry_run=True, detail="dry")

        make_worker(db, DryPoster()).run_once()
        assert groups.get(one.id).last_posted_at is None


class TestHalting:
    def test_an_anomaly_stops_the_rest_of_the_batch(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        poster = FakePoster(
            raises={one.url: AutomationHalted("rate_limit", "temporarily blocked")}
        )
        ticker = Clock()
        worker = make_worker(db, poster, ticker)

        worker.run_once()
        ticker.advance(minutes=60)
        drain(worker)

        assert tasks.get(task.id).state == TASK_HALTED
        assert len(poster.requests) == 1, "kept going after a halt"
        assert tasks.targets_for(task.id)[1].state == TARGET_PENDING

    def test_an_unverified_post_halts_rather_than_retrying(self, db, repos):
        """It may well have posted. Retrying would duplicate it."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        poster = FakePoster(raises={one.url: PostNotVerified("could not confirm")})
        worker = make_worker(db, poster)
        worker.run_once()
        drain(worker)

        assert tasks.get(task.id).state == TASK_HALTED
        assert len(poster.requests) == 1

    def test_an_unexpected_error_also_halts(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db, FakePoster(raises={one.url: RuntimeError("boom")}))
        worker.run_once()
        assert tasks.get(task.id).state == TASK_HALTED


class TestCancellation:
    def test_a_cancelled_batch_is_not_touched(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        tasks.cancel(task.id)

        poster = FakePoster()
        worker = make_worker(db, poster)
        assert worker.run_once() is False
        assert poster.requests == []

    def test_cancelling_mid_batch_stops_the_remaining_groups(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)

        worker.run_once()
        tasks.cancel(task.id)
        ticker.advance(minutes=30)
        drain(worker)

        assert len(poster.requests) == 1
        assert tasks.get(task.id).state == TASK_CANCELLED


class TestPostingWindow:
    def test_a_batch_crossing_the_close_defers_to_the_morning(self, db, repos):
        """The reason this exists: never post at 01:00."""
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        ticker = Clock(clock.parse_local("2026-08-10 22:50"))
        poster = FakePoster()
        worker = make_worker(db, poster, ticker)

        worker.run_once()  # 22:50 -- inside the window, posts
        assert len(poster.requests) == 1

        ticker.advance(minutes=30)  # 23:20 -- window shut
        worker.run_once()
        assert len(poster.requests) == 1, "posted outside the window"

        resume = tasks.get(task.id).resume_at
        assert clock.to_local(resume).hour == 8
        assert clock.to_local(resume).day == 11

    def test_it_resumes_once_the_window_reopens(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        ticker = Clock(clock.parse_local("2026-08-10 22:50"))
        poster = FakePoster()
        worker = make_worker(db, poster, ticker)

        worker.run_once()
        ticker.advance(minutes=30)
        worker.run_once()  # defers
        ticker.now = clock.parse_local("2026-08-11 08:30")
        worker.run_once()

        assert len(poster.requests) == 2


class TestMissedSlots:
    def test_a_stale_schedule_is_reported_not_fired(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        ticker = Clock()
        task = tasks.create(
            BODY, [(one.id, BODY)], scheduled_for=ticker.now - MISSED_GRACE - timedelta(minutes=5)
        )

        poster = FakePoster()
        worker = make_worker(db, poster, ticker)
        worker.run_once()

        assert tasks.get(task.id).state == TASK_MISSED
        assert poster.requests == [], "fired a missed slot late"

    def test_a_slot_inside_the_grace_still_runs(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        ticker = Clock()
        tasks.create(
            BODY, [(one.id, BODY)], scheduled_for=ticker.now - timedelta(minutes=30)
        )

        poster = FakePoster()
        make_worker(db, poster, ticker).run_once()
        assert len(poster.requests) == 1

    def test_a_batch_already_under_way_is_never_called_missed(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        ticker = Clock()
        task = tasks.create(
            BODY,
            [(one.id, "a"), (two.id, "b")],
            scheduled_for=ticker.now - timedelta(minutes=10),
        )

        worker = make_worker(db, FakePoster(), ticker)
        worker.run_once()
        ticker.advance(hours=5)  # a long gap, e.g. the machine slept
        worker.run_once()

        assert tasks.get(task.id).state == TASK_DONE


class TestDailyCap:
    def test_the_cap_defers_the_rest_of_the_batch(self, db, repos):
        groups, tasks, settings = repos
        settings.set("daily_cap", 1)
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)

        worker.run_once()
        ticker.advance(minutes=30)
        worker.run_once()

        assert len(poster.requests) == 1
        assert tasks.get(task.id).resume_at is not None


class TestCooldown:
    def test_a_group_in_cooldown_is_skipped_not_stalled(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        ticker = Clock()
        groups.mark_posted(one.id, ticker.now - timedelta(hours=1))
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        poster = FakePoster()
        worker = make_worker(db, poster, ticker)
        worker.run_once()  # skips group one
        worker.run_once()  # posts group two

        states = [t.state for t in tasks.targets_for(task.id)]
        assert states[0] == TARGET_SKIPPED
        assert states[1] == TARGET_DONE
        assert poster.group_urls == [two.url]


class TestCrashRecovery:
    def _interrupted(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_RUNNING, attempted=True)
        return task, target, one

    def test_a_post_that_did_go_out_is_not_repeated(self, db, repos):
        """The whole point: never double-post after a crash."""
        _, tasks, _ = repos
        task, target, group = self._interrupted(db, repos)

        poster = FakePoster(verify_result=True)
        worker = make_worker(db, poster)
        worker.recover()

        assert tasks.targets_for(task.id)[0].state == TARGET_DONE
        assert poster.verify_calls == [group.url]

        drain(worker)
        assert poster.requests == [], "posted again after recovery"

    def test_a_post_that_did_not_go_out_is_requeued(self, db, repos):
        _, tasks, _ = repos
        task, _, _ = self._interrupted(db, repos)

        poster = FakePoster(verify_result=False)
        worker = make_worker(db, poster)
        worker.recover()

        assert tasks.targets_for(task.id)[0].state == TARGET_PENDING

        worker.run_once()
        assert len(poster.requests) == 1

    def test_an_uncheckable_target_escalates_instead_of_guessing(self, db, repos):
        _, tasks, _ = repos
        task, _, _ = self._interrupted(db, repos)

        class Broken(FakePoster):
            def verify(self, group_url, body):
                raise RuntimeError("Chrome is not running")

        worker = make_worker(db, Broken())
        worker.recover()

        target = tasks.targets_for(task.id)[0]
        assert target.state == TARGET_FAILED
        assert "twice" in target.error
        assert tasks.get(task.id).state == TASK_HALTED


class TestSleepInhibition:
    def test_sleep_is_held_off_while_a_batch_is_under_way(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        worker = make_worker(db)
        worker.run_once()
        worker._sync_power()
        assert worker.blocker.held, "machine could sleep mid-batch"

    def test_it_is_released_once_the_queue_is_empty(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db)
        drain(worker)
        worker._sync_power()
        assert not worker.blocker.held


class TestLifecycle:
    def test_an_empty_queue_does_nothing(self, db):
        worker = make_worker(db)
        assert worker.run_once() is False

    def test_starting_twice_does_not_make_a_second_worker(self, db):
        """One worker, globally. Ever."""
        worker = make_worker(db)
        worker.start()
        first = worker._thread
        worker.start()
        try:
            assert worker._thread is first
        finally:
            worker.stop()

    def test_pause_and_resume_report_their_state(self, db):
        worker = make_worker(db)
        assert worker.state == "stopped"
        worker.pause()
        assert worker.paused
        worker.resume()
        assert not worker.paused

    def test_events_describe_what_happened(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db)
        drain(worker)
        assert "posted" in kinds(worker)
