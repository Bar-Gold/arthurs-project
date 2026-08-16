"""Tests for repeating posts: storage, and the worker turning them into batches.

Real database, fake poster, hand-wound clock -- the same arrangement as
test_worker.py, because a schedule's whole job is to produce an ordinary batch
and then get out of the way.

The case worth reading first is
TestWordingRotation::test_the_second_run_does_not_repeat_the_first_runs_text.
`guards.check_repeat_text` refuses the same words to the same group outright,
so a repeating post that could only hold one wording would work exactly once.
"""

from __future__ import annotations

import queue
import random
from datetime import timedelta

import pytest

from fbposter import clock, recurrence
from fbposter.automation.humanize import HumanProfile, Humanizer
from fbposter.automation.poster import PostOutcome, PostRequest
from fbposter.db import Database
from fbposter.db.models import (
    SCHEDULE_ACTIVE,
    SCHEDULE_PAUSED,
    TASK_MISSED,
    TASK_PENDING,
)
from fbposter.db.repo import GroupRepo, ScheduleRepo, SettingsRepo, TaskRepo
from fbposter.power import SleepBlocker
from fbposter.worker import MISSED_GRACE, PostingWorker

WORDINGS = ["First way of putting it", "Second way", "Third way"]

# Israel time, comfortably inside the 08:00-23:00 window.
NOON = clock.parse_local("2026-08-10 12:00")


class FakePoster:
    def __init__(self) -> None:
        self.requests: list[PostRequest] = []

    def post(self, request: PostRequest) -> PostOutcome:
        self.requests.append(request)
        return PostOutcome(posted=True, verified=True, detail="ok")

    def verify(self, group_url: str, body: str) -> bool:
        return False

    @property
    def bodies(self) -> list[str]:
        return [r.body for r in self.requests]


class Clock:
    def __init__(self, start=NOON) -> None:
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "schedules.db")
    yield database
    database.close()


@pytest.fixture
def repos(db):
    return GroupRepo(db), TaskRepo(db), ScheduleRepo(db), SettingsRepo(db)


def make_worker(db, poster=None, clock_fn=None):
    return PostingWorker(
        db,
        poster=poster or FakePoster(),
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


def add_groups(groups, count=2):
    made = [
        groups.add_from_url(f"https://www.facebook.com/groups/g{index}")
        for index in range(count)
    ]
    # No cooldown by default: these tests are about the schedule, and the
    # cooldown has its own coverage in test_worker.py.
    for group in made:
        groups.set_cooldown(group.id, 0)
    return made


def make_schedule(schedules, group_ids, *, times=("12:00",), bodies=None, next_run_at=NOON):
    return schedules.create(
        name="Bikes",
        bodies=list(bodies if bodies is not None else WORDINGS),
        group_ids=list(group_ids),
        times=list(times),
        next_run_at=next_run_at,
    )


def kinds(worker) -> list[str]:
    out = []
    while not worker.events.empty():
        out.append(worker.events.get_nowait().kind)
    return out


def drain(worker, limit=60):
    for _ in range(limit):
        if not worker.run_once():
            return
    raise AssertionError("worker did not settle")


class TestScheduleStorage:
    def test_a_schedule_round_trips(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, two = add_groups(groups)
        made = make_schedule(schedules, [one.id, two.id], times=("09:00", "18:00"))

        stored = schedules.get(made.id)
        assert stored.name == "Bikes"
        assert stored.bodies == WORDINGS
        assert stored.times == ["09:00", "18:00"]
        assert stored.group_ids == [one.id, two.id]
        assert stored.state == SCHEDULE_ACTIVE
        assert stored.run_count == 0

    def test_group_order_is_preserved(self, repos):
        groups, _tasks, schedules, _settings = repos
        made_groups = add_groups(groups, 3)
        wanted = [made_groups[2].id, made_groups[0].id, made_groups[1].id]
        stored = make_schedule(schedules, wanted)
        assert stored.group_ids == wanted

    def test_a_group_listed_twice_is_stored_once(self, repos):
        """Same reason as UNIQUE(task_id, group_id): never post twice in one run."""
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        stored = make_schedule(schedules, [one.id, one.id])
        assert stored.group_ids == [one.id]

    def test_days_round_trip_as_integers(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        stored = schedules.create(
            name="Weekly",
            bodies=WORDINGS,
            group_ids=[one.id],
            times=["09:00"],
            days=[0, 3],
        )
        assert schedules.get(stored.id).days == [0, 3]

    def test_a_schedule_with_no_wordings_is_refused(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        with pytest.raises(ValueError):
            schedules.create(name="x", bodies=["  "], group_ids=[one.id], times=["09:00"])

    def test_a_schedule_with_no_groups_is_refused(self, repos):
        _groups, _tasks, schedules, _settings = repos
        with pytest.raises(ValueError):
            schedules.create(name="x", bodies=WORDINGS, group_ids=[], times=["09:00"])

    def test_nothing_is_written_when_creation_is_refused(self, repos):
        """Half a schedule would sit in the database doing nothing visible."""
        _groups, _tasks, schedules, _settings = repos
        with pytest.raises(ValueError):
            schedules.create(name="x", bodies=WORDINGS, group_ids=[], times=["09:00"])
        assert schedules.list() == []

    def test_deleting_a_group_deletes_its_schedule_rows(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, two = add_groups(groups)
        made = make_schedule(schedules, [one.id, two.id])
        groups.remove(one.id)
        assert schedules.get(made.id).group_ids == [two.id]

    def test_due_returns_only_active_schedules(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])
        assert [s.id for s in schedules.due(NOON)] == [made.id]

        schedules.set_state(made.id, SCHEDULE_PAUSED)
        assert schedules.due(NOON) == []

    def test_due_ignores_a_slot_still_in_the_future(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        make_schedule(schedules, [one.id], next_run_at=NOON + timedelta(hours=1))
        assert schedules.due(NOON) == []

    def test_recording_a_run_advances_the_rotation(self, repos):
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])
        later = NOON + timedelta(days=1)
        schedules.record_run(made.id, NOON, later)

        stored = schedules.get(made.id)
        assert stored.run_count == 1
        assert stored.last_run_at == NOON
        assert stored.next_run_at == later


class TestFiring:
    def test_a_due_schedule_becomes_a_task(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, two = add_groups(groups)
        made = make_schedule(schedules, [one.id, two.id])

        worker = make_worker(db)
        assert worker.run_once() is True

        queued = tasks.list_recent()
        assert len(queued) == 1
        assert queued[0].schedule_id == made.id
        assert queued[0].state == TASK_PENDING
        assert len(tasks.targets_for(queued[0].id)) == 2

    def test_the_task_carries_the_schedules_media(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        schedules.create(
            name="With pictures",
            bodies=WORDINGS,
            group_ids=[one.id],
            times=["12:00"],
            media_paths=["C:/pictures/bike.jpg"],
            next_run_at=NOON,
        )
        make_worker(db).run_once()
        assert tasks.list_recent()[0].media_paths == ["C:/pictures/bike.jpg"]

    def test_firing_advances_the_next_run(self, db, repos):
        groups, _tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])

        make_worker(db).run_once()
        stored = schedules.get(made.id)
        assert stored.run_count == 1
        assert clock.format_local(stored.next_run_at) == "2026-08-11 12:00"

    def test_it_does_not_fire_twice_for_one_slot(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        make_schedule(schedules, [one.id])

        worker = make_worker(db)
        worker.run_once()
        drain(worker)
        assert len([t for t in tasks.list_recent()]) == 1

    def test_a_paused_schedule_never_fires(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])
        schedules.set_state(made.id, SCHEDULE_PAUSED)

        drain(make_worker(db))
        assert tasks.list_recent() == []

    def test_a_schedule_with_no_next_run_is_initialised_rather_than_fired(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id], times=("18:00",), next_run_at=None)

        make_worker(db).run_once()
        assert tasks.list_recent() == []
        stored = schedules.get(made.id)
        assert stored.run_count == 0
        assert clock.format_local(stored.next_run_at) == "2026-08-10 18:00"

    def test_a_slot_older_than_the_grace_period_is_not_fired_late(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])

        ticker = Clock(NOON + MISSED_GRACE + timedelta(minutes=1))
        worker = make_worker(db, clock_fn=ticker)
        worker.run_once()

        assert tasks.list_recent() == []
        assert "missed" in kinds(worker)
        assert schedules.get(made.id).next_run_at > ticker.now

    def test_a_slot_inside_the_grace_period_still_fires(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        make_schedule(schedules, [one.id])

        worker = make_worker(db, clock_fn=Clock(NOON + timedelta(minutes=90)))
        worker.run_once()
        assert len(tasks.list_recent()) == 1

    def test_a_night_asleep_produces_one_batch_not_a_burst(self, db, repos):
        """The whole point of the grace period, at schedule level."""
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        schedules.create(
            name="Thrice daily",
            bodies=WORDINGS,
            group_ids=[one.id],
            times=["09:00", "14:00", "20:00"],
            next_run_at=clock.parse_local("2026-08-10 09:00"),
        )

        # Woke at 19:00, having slept through 09:00 and 14:00.
        ticker = Clock(clock.parse_local("2026-08-10 19:00"))
        worker = make_worker(db, clock_fn=ticker)
        drain(worker)
        assert tasks.list_recent() == []

        ticker.now = clock.parse_local("2026-08-10 20:00")
        worker.run_once()
        assert len(tasks.list_recent()) == 1

    def test_it_does_not_stack_a_batch_on_an_unfinished_one(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])

        ticker = Clock()
        worker = make_worker(db, clock_fn=ticker)
        worker.run_once()  # first batch, left pending
        assert len(tasks.list_recent()) == 1

        # Tomorrow's slot arrives while yesterday's is still queued.
        ticker.now = clock.parse_local("2026-08-11 12:00")
        schedules.set_next_run(made.id, ticker.now)
        worker.run_once()

        assert len(tasks.list_recent()) == 1
        assert "skipped" in kinds(worker)

    def test_an_unusable_rule_pauses_the_schedule_instead_of_guessing(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups)
        made = make_schedule(schedules, [one.id])
        # Something no build() would ever produce, but a hand-edited database
        # or a future migration might.
        db.write("UPDATE schedules SET times = ? WHERE id = ?", ('["nonsense"]', made.id))

        worker = make_worker(db)
        worker.run_once()

        assert schedules.get(made.id).state == SCHEDULE_PAUSED
        assert tasks.list_recent() == []
        assert "schedule_error" in kinds(worker)

    def test_a_removed_group_is_skipped_not_fatal(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, two = add_groups(groups)
        make_schedule(schedules, [one.id, two.id])
        groups.remove(two.id)

        make_worker(db).run_once()
        queued = tasks.list_recent()[0]
        assert [t.group_id for t in tasks.targets_for(queued.id)] == [one.id]


class TestWordingRotation:
    def test_each_group_gets_a_different_wording_in_one_run(self, db, repos):
        groups, tasks, schedules, _settings = repos
        made_groups = add_groups(groups, 3)
        make_schedule(schedules, [g.id for g in made_groups])

        make_worker(db).run_once()
        queued = tasks.list_recent()[0]
        bodies = [t.body for t in tasks.targets_for(queued.id)]
        assert len(set(bodies)) == 3, "the same text went to more than one group"

    def test_the_second_run_does_not_repeat_the_first_runs_text(self, db, repos):
        """The reason a schedule holds several wordings at all.

        guards.check_repeat_text refuses the same words to the same group, so
        a schedule that reused one wording would be dead after its first run.
        """
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups, 2)
        made = make_schedule(schedules, [one.id])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        drain(worker)  # fires, then posts
        first = poster.bodies[0]

        ticker.now = clock.parse_local("2026-08-11 12:00")
        assert schedules.get(made.id).next_run_at == ticker.now
        drain(worker)

        assert len(poster.bodies) == 2
        assert poster.bodies[1] != first

    def test_a_wording_already_posted_to_a_group_is_never_chosen_again(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups, 2)
        make_schedule(schedules, [one.id])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        for day in range(10, 13):
            ticker.now = clock.parse_local(f"2026-08-{day} 12:00")
            drain(worker)

        assert sorted(poster.bodies) == sorted(WORDINGS)

    def test_running_out_of_wordings_skips_the_group_and_says_so(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups, 2)
        make_schedule(schedules, [one.id], bodies=["Only one wording"])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        drain(worker)
        assert len(poster.bodies) == 1

        ticker.now = clock.parse_local("2026-08-11 12:00")
        drain(worker)

        assert len(poster.bodies) == 1, "reposted text that had already gone out"
        assert len(tasks.list_recent()) == 1, "queued a batch with nothing in it"
        assert any("wording" in event for event in messages(worker))

    def test_running_out_for_one_group_does_not_stop_the_others(self, db, repos):
        groups, tasks, schedules, _settings = repos
        one, two = add_groups(groups, 2)
        make_schedule(schedules, [one.id, two.id], bodies=["Alpha wording", "Beta wording"])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        drain(worker)

        # Only group one has seen "Alpha"; group two has seen "Beta". Force the
        # rotation so group one has nothing left by giving it Beta as well.
        ticker.now = clock.parse_local("2026-08-11 12:00")
        drain(worker)
        ticker.now = clock.parse_local("2026-08-12 12:00")
        drain(worker)

        # Every group-post that happened used text that group had not seen.
        for group in (one, two):
            seen = groups.recent_bodies(group.id)
            assert len(seen) == len(set(seen)), "a group was sent the same text twice"


def messages(worker) -> list[str]:
    out = []
    while not worker.events.empty():
        out.append(worker.events.get_nowait().message)
    return out


class TestScheduledBatchesGoThroughTheNormalPath:
    def test_the_batch_posts_group_by_group_with_a_gap(self, db, repos):
        groups, tasks, schedules, _settings = repos
        made_groups = add_groups(groups, 3)
        make_schedule(schedules, [g.id for g in made_groups])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)

        worker.run_once()  # materialise
        worker.run_once()  # first group
        assert len(poster.requests) == 1

        assert worker.run_once() is False, "did not wait out the inter-group gap"
        ticker.advance(minutes=30)
        worker.run_once()
        assert len(poster.requests) == 2

    def test_the_daily_cap_still_applies_to_a_scheduled_batch(self, db, repos):
        groups, tasks, schedules, settings = repos
        made_groups = add_groups(groups, 3)
        settings.set("daily_cap", 1)
        make_schedule(schedules, [g.id for g in made_groups])

        poster = FakePoster()
        ticker = Clock()
        worker = make_worker(db, poster, ticker)
        drain(worker)
        assert len(poster.requests) == 1

    def test_a_slot_outside_the_window_defers_rather_than_being_dropped(self, db, repos):
        """The missed check must not eat a batch the worker itself parked."""
        groups, tasks, schedules, _settings = repos
        one, _two = add_groups(groups, 2)
        late = clock.parse_local("2026-08-10 23:30")
        make_schedule(schedules, [one.id], times=("23:30",), next_run_at=late)

        poster = FakePoster()
        ticker = Clock(late)
        worker = make_worker(db, poster, ticker)
        worker.run_once()  # materialise
        worker.run_once()  # defers: outside the 08:00-23:00 window

        task = tasks.list_recent()[0]
        assert task.resume_at is not None
        assert clock.format_local(task.resume_at) == "2026-08-11 08:00"

        ticker.now = task.resume_at
        drain(worker)
        assert tasks.get(task.id).state != TASK_MISSED
        assert len(poster.requests) == 1
