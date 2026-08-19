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
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
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
from fbposter.errors import AutomationHalted, ConnectionFailed, PostNotVerified
from fbposter.power import SleepBlocker
from fbposter.worker import (
    CONNECTION_GIVE_UP,
    CONNECTION_RETRY,
    FOLLOW_UP_GIVE_UP,
    FOLLOW_UP_PER_SWEEP,
    KEEP_AWAKE_HORIZON,
    MISSED_GRACE,
    PostingWorker,
)

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
        self.verdict = "unknown"
        self.verdict_calls: list[str] = []

    def post(self, request: PostRequest) -> PostOutcome:
        self.requests.append(request)
        problem = self.raises.get(request.group_url)
        if problem is not None:
            raise problem
        return PostOutcome(posted=True, verified=True, detail="ok")

    def verify(self, group_url: str, body: str) -> bool:
        self.verify_calls.append(group_url)
        return self.verify_result

    def pending_verdict(self, group_url: str, body: str) -> str:
        """What an admin did with a post awaiting approval. Scripted per test."""
        self.verdict_calls.append(group_url)
        return self.verdict

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


def _explode(*_args, **_kwargs):
    raise RuntimeError("the database fell over")


def _raise_connection_failed(*_args, **_kwargs):
    raise ConnectionFailed("nothing is listening on port 9222")


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

    def test_posting_the_media_from_the_task(self, db, repos, tmp_path):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        # Real files: the worker refuses to post an attachment that is no
        # longer on disk, so placeholder names would never reach the poster.
        made = []
        for name in ("a.png", "b.png"):
            path = tmp_path / name
            path.write_bytes(b"x")
            made.append(str(path))
        tasks.create(BODY, [(one.id, BODY)], media_paths=made)

        poster = FakePoster()
        make_worker(db, poster).run_once()
        assert [p.name for p in poster.requests[0].media_paths] == ["a.png", "b.png"]

    def test_an_attachment_deleted_before_posting_halts_with_a_clear_reason(
        self, db, repos, tmp_path
    ):
        """Otherwise this arrives as a Playwright timeout inside
        set_input_files, naming nothing the user can act on."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        gone = tmp_path / "bike.jpg"
        task = tasks.create(BODY, [(one.id, BODY)], media_paths=[str(gone)])

        poster = FakePoster()
        make_worker(db, poster).run_once()

        assert poster.requests == [], "drove the browser with a missing file"
        assert tasks.get(task.id).state == TASK_HALTED
        assert "bike.jpg" in tasks.targets_for(task.id)[0].error

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

    def test_the_gap_between_groups_still_counts_as_under_way(self, db, repos):
        """Suspending during the gap strands the rest of the batch just as
        surely as suspending mid-post, so the gap is not a chance to sleep."""
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        ticker = Clock()
        worker = make_worker(db, FakePoster(), ticker, gap_seconds=(1500, 1500))
        worker.run_once()
        assert tasks.get(task.id).resume_at is not None

        worker._sync_power()
        assert worker.blocker.held, "machine could sleep during the inter-group gap"

    def test_a_batch_deferred_to_the_morning_lets_the_machine_sleep(self, db, repos):
        """It stays `running` all night with resume_at set to 08:00. Counting
        that as work in progress held the machine awake for nine hours of
        deliberate waiting -- on a laptop, the whole night's battery."""
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        ticker = Clock(clock.parse_local("2026-08-10 22:50"))
        worker = make_worker(db, FakePoster(), ticker)
        worker.run_once()   # posts to the first group
        ticker.advance(minutes=30)
        worker.run_once()   # window shut: defers to the morning

        stored = tasks.get(task.id)
        assert stored.state == TASK_RUNNING
        assert clock.to_local(stored.resume_at).hour == 8

        worker._sync_power()
        assert not worker.blocker.held, "held awake all night for nothing"

    def test_it_takes_hold_again_when_the_window_reopens(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        ticker = Clock(clock.parse_local("2026-08-10 22:50"))
        worker = make_worker(db, FakePoster(), ticker)
        worker.run_once()
        ticker.advance(minutes=30)
        worker.run_once()
        worker._sync_power()
        assert not worker.blocker.held

        ticker.now = clock.parse_local("2026-08-11 07:45")  # inside the horizon
        worker._sync_power()
        assert worker.blocker.held


class TestRunningOnBattery:
    """The keep-awake request only suppresses the idle timer, and the power
    plan that stops a closed lid suspending the machine is mains-only on
    purpose. Unplugged, a batch can be cut off with the app none the wiser."""

    def battery_worker(self, db, answer):
        worker = make_worker(db)
        worker._on_battery = lambda: answer
        return worker

    def test_it_says_so_when_a_batch_starts_on_battery(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        worker = self.battery_worker(db, True)
        worker.run_once()
        worker._sync_power()
        assert "power" in kinds(worker)

    def test_it_says_it_once(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        worker = self.battery_worker(db, True)
        worker.run_once()
        for _ in range(5):
            worker._sync_power()
        assert kinds(worker).count("power") == 1

    def test_nothing_is_said_on_mains(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        worker = self.battery_worker(db, False)
        worker.run_once()
        worker._sync_power()
        assert "power" not in kinds(worker)

    def test_an_unknown_answer_warns_nobody(self, db, repos):
        """A desktop and a driver that declines to answer both report unknown,
        and telling someone to plug in a laptop they do not have is worse than
        saying nothing."""
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        worker = self.battery_worker(db, None)
        worker.run_once()
        worker._sync_power()
        assert "power" not in kinds(worker)

    def test_the_default_worker_asks_nothing(self, db):
        """Wired up by the app, inert in the suite -- otherwise the answer
        would depend on whether the developer's machine was plugged in."""
        assert make_worker(db)._on_battery() is None

    def test_the_app_wires_the_real_one_up(self):
        """An inert default is only safe while production overrides it."""
        import inspect

        from fbposter.qtui import app as qt_app

        source = inspect.getsource(qt_app.App.start_worker)
        assert "on_battery=power.on_battery" in source


class TestChromeGoingAwayIsNotABatchLost:
    """ConnectionFailed comes out of session.attach(), before a page exists --
    so nothing was typed and nothing can have been published. It is the one
    failure it is safe to wait out, and halting on it threw away a whole
    scheduled batch every time Windows Update restarted the machine."""

    def broken(self, db, group, ticker=None):
        poster = FakePoster(raises={group.url: ConnectionFailed("port 9222 shut")})
        return poster, make_worker(db, poster, ticker or Clock())

    def test_the_group_goes_back_in_the_queue(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        _, worker = self.broken(db, one)
        worker.run_once()

        target = tasks.targets_for(task.id)[0]
        assert target.state == TARGET_PENDING, "failed a group it never attempted"
        assert target.attempted_at is None
        assert tasks.get(task.id).state != TASK_HALTED

    def test_it_tries_again_later(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        ticker = Clock()
        _, worker = self.broken(db, one, ticker)
        worker.run_once()

        resume = tasks.get(task.id).resume_at
        assert resume is not None and resume > ticker.now

    def test_the_batch_carries_on_once_chrome_is_back(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        ticker = Clock()
        poster, worker = self.broken(db, one, ticker)
        worker.run_once()
        assert tasks.targets_for(task.id)[0].state == TARGET_PENDING

        poster.raises = {}
        ticker.advance(seconds=CONNECTION_RETRY.total_seconds() + 1)
        drain(worker)

        assert tasks.targets_for(task.id)[0].state == TARGET_DONE
        assert tasks.get(task.id).state == TASK_DONE

    def test_it_is_announced_once_not_every_five_minutes(self, db, repos):
        """Repeating the same sentence through an overnight outage is how a
        real warning stops being read."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        ticker = Clock()
        _, worker = self.broken(db, one, ticker)
        for _ in range(6):
            worker.run_once()
            ticker.advance(seconds=CONNECTION_RETRY.total_seconds() + 1)

        assert kinds(worker).count("deferred") == 1

    def test_it_gives_up_eventually(self, db, repos):
        """A batch waiting for a browser that is never coming back should not
        sit in the queue looking alive."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        ticker = Clock()
        _, worker = self.broken(db, one, ticker)
        worker.run_once()

        ticker.advance(seconds=CONNECTION_GIVE_UP.total_seconds() + 60)
        worker.run_once()

        assert tasks.get(task.id).state == TASK_HALTED
        assert "Chrome" in tasks.targets_for(task.id)[0].error

    def test_the_patience_starts_over_after_a_good_post(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        ticker = Clock()
        poster, worker = self.broken(db, one, ticker)
        worker.run_once()                       # first group: Chrome is down

        poster.raises = {}
        ticker.advance(seconds=CONNECTION_RETRY.total_seconds() + 1)
        worker.run_once()                       # first group: posted

        # Long past the give-up horizon measured from that first outage.
        ticker.advance(seconds=CONNECTION_GIVE_UP.total_seconds() + 60)
        poster.raises = {two.url: ConnectionFailed("gone again")}
        worker.run_once()

        assert tasks.get(task.id).state != TASK_HALTED, "old outage still counting"

    def test_the_machine_stays_awake_for_the_retry(self, db, repos):
        """One rule decides this and it is the horizon in _sync_power: the
        retry is five minutes away, so it counts. Worth pinning, because the
        obvious reading -- "no browser, nothing to stay awake for" -- would
        have the machine asleep at the moment Chrome came back."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        _, worker = self.broken(db, one)
        worker.run_once()
        worker._sync_power()
        assert worker.blocker.held
        assert CONNECTION_RETRY < KEEP_AWAKE_HORIZON


class TestRecoveryWaitsForChrome:
    """Recovery runs seconds after a restart -- exactly when Chrome is least
    likely to be up. "Could not look" must not be recorded as "looked and
    found nothing"."""

    def interrupted(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.claim_target(target.id)
        return task, target

    def test_an_uncheckable_target_is_not_condemned(self, db, repos):
        _, tasks, _ = repos
        task, target = self.interrupted(db, repos)

        poster = FakePoster()
        poster.verify = _raise_connection_failed
        worker = make_worker(db, poster)
        worker.recover()

        assert tasks.targets_for(task.id)[0].state == TARGET_RUNNING
        assert tasks.get(task.id).state != TASK_HALTED

    def test_it_comes_round_again(self, db, repos):
        _, tasks, _ = repos
        task, _ = self.interrupted(db, repos)

        poster = FakePoster(verify_result=True)
        poster.verify = _raise_connection_failed
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        worker.recover()
        assert worker._recovery_deferred

        # Chrome comes back; the next tick picks the recovery up again.
        poster.verify = lambda group_url, body: True
        ticker.advance(seconds=CONNECTION_RETRY.total_seconds() + 1)
        worker.run_once()

        assert tasks.targets_for(task.id)[0].state == TARGET_DONE

    def test_it_is_announced_once(self, db, repos):
        self.interrupted(db, repos)

        poster = FakePoster()
        poster.verify = _raise_connection_failed
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        for _ in range(4):
            worker.recover()
            ticker.advance(seconds=CONNECTION_RETRY.total_seconds() + 1)

        assert kinds(worker).count("error") == 1

    def test_it_does_not_wait_for_ever(self, db, repos):
        """The task stays `running` while recovery waits, so the machine is
        held awake for it. Waiting for a browser that is never coming back
        would keep a laptop awake for ever too."""
        _, tasks, _ = repos
        task, _ = self.interrupted(db, repos)

        poster = FakePoster()
        poster.verify = _raise_connection_failed
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        worker.recover()
        assert tasks.targets_for(task.id)[0].state == TARGET_RUNNING

        ticker.advance(seconds=CONNECTION_GIVE_UP.total_seconds() + 60)
        worker.run_once()

        assert tasks.targets_for(task.id)[0].state == TARGET_FAILED
        assert tasks.get(task.id).state == TASK_HALTED

    def test_an_ordinary_failure_still_escalates(self, db, repos):
        """Only "the browser is not there" is patient. Anything else means the
        check ran and came back unusable, which is the user's problem to look
        at before the words go out again."""
        _, tasks, _ = repos
        task, _ = self.interrupted(db, repos)

        poster = FakePoster()
        poster.verify = _explode
        worker = make_worker(db, poster)
        worker.recover()

        assert tasks.targets_for(task.id)[0].state == TARGET_FAILED
        assert tasks.get(task.id).state == TASK_HALTED


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


class TestInterruptsDoNotStrandABatch:
    """KeyboardInterrupt and SystemExit are not Exception, so they slipped past
    the handler and killed the worker thread -- leaving the target stuck in
    `running` and the keep-awake request still held."""

    @pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
    def test_the_target_is_not_left_running(self, db, repos, interrupt):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db, FakePoster(raises={one.url: interrupt()}))
        with pytest.raises(interrupt):
            worker.run_once()

        assert tasks.targets_for(task.id)[0].state == TARGET_FAILED
        assert tasks.get(task.id).state == TASK_HALTED

    def test_the_machine_is_allowed_to_sleep_again(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        released = []
        blocker = SleepBlocker(setter=lambda flags: released.append(flags))
        worker = make_worker(db, FakePoster(raises={one.url: KeyboardInterrupt()}))
        worker.blocker = blocker
        with pytest.raises(KeyboardInterrupt):
            worker.run_once()

        assert not blocker.held, "keep-awake still held after an interrupt"

    def test_an_ordinary_exception_still_does_not_propagate(self):
        """The new handler must not have widened what escapes."""
        import inspect

        from fbposter import worker as worker_module

        source = inspect.getsource(worker_module.PostingWorker._post)
        assert "except BaseException" in source
        assert "raise" in source


class TestTwoWorkersCannotDoublePost:
    """A second copy of the app is a second worker on the same database.

    Racing two of them produced a duplicate post in 7 runs out of 40 before
    claim_target existed. A duplicate is the worst outcome this project has, so
    the claim is a conditional UPDATE and the transition itself is the lock.
    """

    def test_only_one_worker_can_claim_a_target(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]

        assert tasks.claim_target(target.id) is True
        assert tasks.claim_target(target.id) is False, "claimed twice"

    def test_a_claim_marks_it_running_and_attempted(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]

        tasks.claim_target(target.id)
        claimed = tasks.targets_for(task.id)[0]
        assert claimed.state == TARGET_RUNNING
        assert claimed.attempted_at is not None

    def test_an_already_finished_target_cannot_be_reclaimed(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_DONE, posted=True)

        assert tasks.claim_target(target.id) is False

    def test_a_second_worker_finding_it_taken_posts_nothing(self, db, repos):
        """The whole point: it must walk away, not post it as well."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.claim_target(target.id)          # the other worker got there first

        poster = FakePoster()
        worker = make_worker(db, poster)
        worker._post(tasks.get(task.id), target, groups.get(one.id))

        assert poster.requests == [], "posted a target another worker had claimed"


class TestAGroupThatHoldsPostsForApproval:
    """Read off SneakerHeads in Israel: the post is submitted, the composer
    closes, and it is NOT in the feed -- the group shows the author a
    "Pending admin approval" banner instead.

    Before this existed the app guessed, and which way it guessed depended on
    timing: sometimes a confident (wrong) "Done", sometimes a halted batch.
    """

    def pending_poster(self, url):
        from fbposter.automation.poster import PostOutcome

        class Poster(FakePoster):
            def post(self, request):
                self.requests.append(request)
                if request.group_url == url:
                    return PostOutcome(
                        posted=True, verified=False, pending=True,
                        detail="Submitted, and the group is holding it for an admin.",
                    )
                return PostOutcome(posted=True, verified=True, detail="ok")

        return Poster()

    def test_the_batch_carries_on_to_the_other_groups(self, db, repos):
        """One moderated group used to abandon every group after it."""
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY), (two.id, "second wording")])

        ticker = Clock()
        worker = make_worker(db, self.pending_poster(one.url), ticker)
        drain(worker)
        # The pending group is not a reason to skip the inter-group gap.
        ticker.advance(minutes=30)
        drain(worker)

        assert tasks.get(task.id).state == TASK_DONE
        states = [t.state for t in tasks.targets_for(task.id)]
        assert states == [TARGET_AWAITING_APPROVAL, TARGET_DONE]

    def test_it_is_not_recorded_as_a_failure(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db, self.pending_poster(one.url))
        drain(worker)
        assert tasks.targets_for(task.id)[0].state != TARGET_FAILED

    def test_the_cooldown_starts(self, db, repos):
        """The words have left the building even if they are not on screen."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        drain(make_worker(db, self.pending_poster(one.url)))
        assert groups.get(one.id).last_posted_at is not None

    def test_the_repeat_guard_remembers_the_wording(self, db, repos):
        """The dangerous one. Recorded as failed, the guard would be blind and
        the same text could be sent again -- two live posts once approved."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        drain(make_worker(db, self.pending_poster(one.url)))
        assert BODY in groups.recent_bodies(one.id)

    def test_it_counts_towards_the_daily_cap(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        drain(make_worker(db, self.pending_poster(one.url)))
        assert tasks.posted_count_since(clock.start_of_local_day(NOON)) == 1

    def test_the_user_is_told_it_is_not_visible_yet(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db, self.pending_poster(one.url))
        drain(worker)
        messages = []
        while not worker.events.empty():
            messages.append(worker.events.get_nowait().message)
        assert any("approval" in m and "not visible" in m for m in messages)


class TestResolvingAPostThatWasAwaitingApproval:
    """Blocking the wording while it is pending is right -- the app cannot know
    the outcome yet, and guessing "declined" risks two live posts. But an admin
    who says no must not cost the user that wording for ever.
    """

    def pending_target(self, groups, tasks):
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_AWAITING_APPROVAL, posted=True)
        return one, task, tasks.targets_for(task.id)[0]

    def test_while_pending_the_wording_is_held(self, db, repos):
        groups, tasks, _ = repos
        one, _task, _target = self.pending_target(groups, tasks)
        assert BODY in groups.recent_bodies(one.id)

    def test_declining_releases_the_wording(self, db, repos):
        """The bug: nothing was published, so the guard must let it go."""
        groups, tasks, _ = repos
        one, _task, target = self.pending_target(groups, tasks)

        assert tasks.resolve_pending(target.id, approved=False) is True
        assert BODY not in groups.recent_bodies(one.id)

    def test_and_the_post_can_then_be_sent_again(self, db, repos):
        groups, tasks, _ = repos
        one, _task, target = self.pending_target(groups, tasks)
        tasks.resolve_pending(target.id, approved=False)

        from fbposter import guards

        assert guards.check_repeat_text(BODY, groups.recent_bodies(one.id)) is None

    def test_approving_keeps_it_held(self, db, repos):
        """It is live now, so sending the same words again would be a repeat."""
        groups, tasks, _ = repos
        one, _task, target = self.pending_target(groups, tasks)

        assert tasks.resolve_pending(target.id, approved=True) is True
        assert BODY in groups.recent_bodies(one.id)

    def test_the_states_are_recorded(self, db, repos):
        groups, tasks, _ = repos
        _one, task, target = self.pending_target(groups, tasks)
        tasks.resolve_pending(target.id, approved=False)
        stored = tasks.targets_for(task.id)[0]
        assert stored.state == TARGET_DECLINED
        assert "declined" in stored.error.lower()

    def test_it_only_touches_a_genuinely_pending_target(self, db, repos):
        """It must not resurrect a failure or overwrite a real success."""
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_DONE, posted=True)

        assert tasks.resolve_pending(target.id, approved=False) is False
        assert tasks.targets_for(task.id)[0].state == TARGET_DONE

    def test_a_declined_post_does_not_count_towards_the_daily_cap(self, db, repos):
        groups, tasks, _ = repos
        _one, _task, target = self.pending_target(groups, tasks)
        before = tasks.posted_count_since(clock.start_of_local_day(NOON))
        tasks.resolve_pending(target.id, approved=False)
        assert tasks.posted_count_since(clock.start_of_local_day(NOON)) == before - 1


class TestFollowingUpOnPostsAwaitingApproval:
    """No manual step: the app finds out for itself what the admin did.

    A plain decline leaves no positive trace anywhere -- "Declined with
    Feedback" only lists the ones with a written reason -- so "declined" is
    reached by elimination, which is why it takes two consecutive misses.
    """

    def waiting(self, groups, tasks, posted_ago=timedelta(hours=3)):
        """A post submitted `posted_ago` before the fake clock's NOON.

        mark_target stamps posted_at from the real wall clock, which sits years
        away from the test clock, so it is set explicitly here -- otherwise
        every target looks newer than FOLLOW_UP_AFTER and is skipped.
        """
        one, _ = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])
        target = tasks.targets_for(task.id)[0]
        tasks.mark_target(target.id, TARGET_AWAITING_APPROVAL, posted=True)
        from fbposter.db.models import to_iso

        tasks.db.write(
            "UPDATE task_targets SET posted_at = ? WHERE id = ?",
            (to_iso(NOON - posted_ago), target.id),
        )
        return one, task, tasks.targets_for(task.id)[0]

    def follow_up(self, db, verdict, ticker=None):
        poster = FakePoster()
        poster.verdict = verdict
        worker = make_worker(db, poster, ticker or Clock())
        worker._follow_up_pending((ticker or Clock())())
        return poster, worker

    def test_approved_becomes_live(self, db, repos):
        groups, tasks, _ = repos
        _one, task, _t = self.waiting(groups, tasks)
        self.follow_up(db, "approved")
        assert tasks.targets_for(task.id)[0].state == TARGET_DONE

    def test_still_pending_is_left_alone(self, db, repos):
        groups, tasks, _ = repos
        _one, task, _t = self.waiting(groups, tasks)
        self.follow_up(db, "pending")
        assert tasks.targets_for(task.id)[0].state == TARGET_AWAITING_APPROVAL

    def test_unknown_changes_nothing(self, db, repos):
        """A page that did not render must never release a wording."""
        groups, tasks, _ = repos
        _one, task, _t = self.waiting(groups, tasks)
        self.follow_up(db, "unknown")
        stored = tasks.targets_for(task.id)[0]
        assert stored.state == TARGET_AWAITING_APPROVAL
        assert stored.resolve_misses == 0

    def test_one_miss_is_not_enough_to_declare_it_declined(self, db, repos):
        groups, tasks, _ = repos
        one, task, _t = self.waiting(groups, tasks)
        self.follow_up(db, "declined")
        stored = tasks.targets_for(task.id)[0]
        assert stored.state == TARGET_AWAITING_APPROVAL
        assert stored.resolve_misses == 1
        assert BODY in groups.recent_bodies(one.id), "released the wording too early"

    def test_two_consecutive_misses_declare_it_declined(self, db, repos):
        groups, tasks, _ = repos
        one, task, _t = self.waiting(groups, tasks)
        ticker = Clock()
        self.follow_up(db, "declined", ticker)
        ticker.advance(hours=7)
        self.follow_up(db, "declined", ticker)

        assert tasks.targets_for(task.id)[0].state == TARGET_DECLINED
        assert BODY not in groups.recent_bodies(one.id)

    def test_turning_up_again_resets_the_count(self, db, repos):
        """A transient miss must not accumulate towards a decline."""
        groups, tasks, _ = repos
        _one, task, _t = self.waiting(groups, tasks)
        ticker = Clock()
        self.follow_up(db, "declined", ticker)
        ticker.advance(hours=7)
        self.follow_up(db, "pending", ticker)
        assert tasks.targets_for(task.id)[0].resolve_misses == 0

        ticker.advance(hours=7)
        self.follow_up(db, "declined", ticker)
        assert tasks.targets_for(task.id)[0].state == TARGET_AWAITING_APPROVAL

    def test_nothing_waiting_means_no_page_loads_at_all(self, db, repos):
        groups, tasks, _ = repos
        one, _ = add_groups(groups)
        tasks.create(BODY, [(one.id, BODY)])
        poster, _ = self.follow_up(db, "declined")
        assert poster.verdict_calls == []

    def test_it_does_not_check_outside_the_posting_window(self, db, repos):
        """A group page opened at 4am is the same signal as a post at 4am."""
        groups, tasks, settings = repos
        self.waiting(groups, tasks)
        settings.set("posting_window_start_hour", 8)
        settings.set("posting_window_end_hour", 23)
        night = Clock(clock.parse_local("2026-08-10 03:00"))
        poster, _ = self.follow_up(db, "declined", night)
        assert poster.verdict_calls == []

    def test_it_does_not_check_the_same_post_twice_in_a_row(self, db, repos):
        groups, tasks, _ = repos
        self.waiting(groups, tasks)

        ticker = Clock()
        poster = FakePoster()
        poster.verdict = "pending"
        worker = make_worker(db, poster, ticker)
        worker._follow_up_pending(ticker())
        worker._follow_up_pending(ticker())
        assert len(poster.verdict_calls) == 1, "checked again without waiting"

    def test_a_brand_new_post_is_given_a_moment(self, db, repos):
        """An admin who was just sent something needs time to see it."""
        groups, tasks, _ = repos
        self.waiting(groups, tasks, posted_ago=timedelta(minutes=5))

        poster, _ = self.follow_up(db, "declined")
        assert poster.verdict_calls == []

    def test_a_failure_to_check_never_disturbs_the_queue(self, db, repos):
        groups, tasks, _ = repos
        _one, task, _t = self.waiting(groups, tasks)

        class Broken(FakePoster):
            def pending_verdict(self, group_url, body):
                raise RuntimeError("network gone")

        worker = make_worker(db, Broken())
        worker._follow_up_pending(Clock()())
        assert tasks.targets_for(task.id)[0].state == TARGET_AWAITING_APPROVAL

    def test_the_worker_says_how_it_found_out(self, db, repos):
        """The row used to claim the user confirmed it, which they had not."""
        groups, tasks, _ = repos
        _one, task, _t = self.waiting(groups, tasks)
        self.follow_up(db, "approved")
        assert "user" not in tasks.targets_for(task.id)[0].error.lower()


class TestASweepCannotRunAway:
    """Everything in a follow-up drives a real browser, so the sweep is bounded
    in three directions: how many groups it touches at once, how long it goes on
    chasing one post, and what it does when Facebook shows it something bad.

    None of these are tidiness. An unbounded sweep is a burst of page loads no
    member would produce, an unbounded chase reopens a group page about a dead
    post twice a day for ever, and carrying on through a rate-limit warning is
    the one thing this app must never do.
    """

    def waiting(self, groups, tasks, count, posted_ago=timedelta(hours=3)):
        """`count` groups, each holding a post for an admin."""
        from fbposter.db.models import to_iso

        made = add_groups(groups, count)
        task = tasks.create(BODY, [(g.id, f"{BODY} {g.id}") for g in made])
        for target in tasks.targets_for(task.id):
            tasks.mark_target(target.id, TARGET_AWAITING_APPROVAL, posted=True)
            tasks.db.write(
                "UPDATE task_targets SET posted_at = ? WHERE id = ?",
                (to_iso(NOON - posted_ago), target.id),
            )
        return made, task

    def test_only_a_few_groups_are_checked_in_one_sweep(self, db, repos):
        groups, tasks, _ = repos
        self.waiting(groups, tasks, 6)

        poster = FakePoster()
        poster.verdict ="pending"
        worker = make_worker(db, poster)
        worker._follow_up_pending(Clock()())

        assert len(poster.verdict_calls) == FOLLOW_UP_PER_SWEEP

    def test_the_next_sweep_picks_up_where_it_left_off(self, db, repos):
        """Otherwise three posts an admin has abandoned soak up every sweep and
        the fourth group is never looked at at all."""
        groups, tasks, _ = repos
        self.waiting(groups, tasks, 6)
        ticker = Clock()

        seen: list[str] = []
        for _ in range(2):
            poster = FakePoster()
            poster.verdict = "pending"
            make_worker(db, poster, ticker)._follow_up_pending(ticker())
            seen.extend(poster.verdict_calls)
            ticker.advance(hours=7)

        assert len(set(seen)) == 2 * FOLLOW_UP_PER_SWEEP, "checked the same ones twice"

    def test_a_post_nobody_ever_answers_is_eventually_left_alone(self, db, repos):
        groups, tasks, _ = repos
        self.waiting(groups, tasks, 1, posted_ago=FOLLOW_UP_GIVE_UP + timedelta(days=1))

        poster = FakePoster()
        poster.verdict ="declined"
        worker = make_worker(db, poster)
        worker._follow_up_pending(Clock()())

        assert poster.verdict_calls == []
        # Still on screen with its two buttons, and the wording still claimed:
        # giving up on asking is not the same as deciding.
        assert tasks.awaiting_approval_targets() != []

    def test_a_checkpoint_stops_the_sweep_rather_than_opening_more_pages(
        self, db, repos
    ):
        groups, tasks, _ = repos
        self.waiting(groups, tasks, 3)

        class Blocked(FakePoster):
            def pending_verdict(self, group_url, body):
                self.verdict_calls.append(group_url)
                raise AutomationHalted("rate_limit", "temporarily blocked")

        poster = Blocked()
        worker = make_worker(db, poster)
        worker._follow_up_pending(Clock()())

        assert len(poster.verdict_calls) == 1, "kept loading pages through a block"
        assert kinds(worker) == ["halted"]
        assert all(
            t.state == TARGET_AWAITING_APPROVAL for t in tasks.awaiting_approval_targets()
        )

    def test_a_broken_sweep_does_not_stop_the_queue(self, db, repos):
        """It runs at the top of every tick, so anything it raises would take
        the posting with it. Same rule as the pruner."""
        groups, tasks, _ = repos
        one, _two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, BODY)])

        worker = make_worker(db)
        worker.tasks.awaiting_approval_targets = _explode

        assert worker.run_once() is True
        assert tasks.targets_for(task.id)[0].state == TARGET_DONE
        assert "error" in kinds(worker)

    def test_a_group_removed_while_a_post_waits_stops_being_chased(self, db, repos):
        """A post that can never be resolved must not be asked about for ever.

        It used to leave the sweep by being deleted -- task_targets cascaded
        with the group. Removing a group archives it now, so the row survives
        and the sweep has to decline it on purpose. The record staying is the
        point: awaiting_approval already counts towards recent_bodies, so that
        wording goes on being refused to that group either way.
        """
        groups, tasks, _ = repos
        made, _task = self.waiting(groups, tasks, 1)
        groups.remove(made[0].id)

        poster = FakePoster()
        poster.verdict = "pending"
        worker = make_worker(db, poster)
        worker._follow_up_pending(Clock()())

        assert poster.verdict_calls == [], "opened a page for a group the user removed"
        assert len(tasks.awaiting_approval_targets()) == 1
        assert groups.recent_bodies(made[0].id) != []


class TestAGroupTakenOffTheListIsNotPostedTo:
    """Removing a group archives it rather than deleting it, so its history
    survives for the repeated-text guard. The row surviving must not mean the
    post still goes out: before archiving, a removed group was simply gone and
    the target failed, and that is the behaviour to keep.
    """

    def test_the_target_fails_and_the_batch_carries_on(self, db, repos):
        groups, tasks, _ = repos
        one, two = add_groups(groups)
        task = tasks.create(BODY, [(one.id, "a"), (two.id, "b")])

        groups.remove(one.id)

        poster = FakePoster()
        worker = make_worker(db, poster)
        drain(worker)

        states = [t.state for t in tasks.targets_for(task.id)]
        assert states[0] == TARGET_FAILED
        assert states[1] == TARGET_DONE
        assert poster.group_urls == [two.url], "posted to a group the user removed"

    def test_bringing_it_back_makes_it_postable_again(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, count=1)
        groups.remove(one.id)
        groups.add_from_url(one.url)

        task = tasks.create(BODY, [(one.id, "a")])
        poster = FakePoster()
        worker = make_worker(db, poster)
        drain(worker)

        assert tasks.targets_for(task.id)[0].state == TARGET_DONE
        assert poster.group_urls == [one.url]

