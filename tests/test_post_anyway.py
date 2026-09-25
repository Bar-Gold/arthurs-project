""""Post anyway": breaking a posting rule on purpose, and only on purpose.

The rules used to be absolute: a post inside the 8h cooldown, outside posting
hours, over the daily limit or repeating text was refused, and a repeating post
that broke them was created anyway and then quietly skipped at every run. Now
each refusal can be overridden -- but only by an explicit "Post anyway", only
for the rules that were shown, and the choice is recorded on the batch so the
worker, which re-checks everything at the moment of posting, honours it.

The half that matters most is that nothing *else* changed: without "Post
anyway" every rule refuses exactly as before.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter import clock, recurrence
from fbposter.db.models import TARGET_DONE, TARGET_SKIPPED
from fbposter.db.repo import ScheduleRepo
from fbposter.guards import COOLDOWN, DAILY_CAP, POSTING_WINDOW, REPEAT_TEXT, rule_labels
from fbposter.recurrence import ScheduleTarget, check_schedule

from .test_worker import BODY, Clock, FakePoster, add_groups, drain, make_worker

MONDAY_NOON = clock.parse_local("2026-08-10 12:00")


def rules(violations) -> set[str]:
    return {v.rule for v in violations}


class TestTheRepeatChecksLookAtTheRealGaps:
    """The old check averaged: 09:00 and 12:00 is "every 12h" on average, well
    outside an 8h cooldown -- and the 12:00 run was skipped every day."""

    def test_two_runs_three_hours_apart_break_the_cooldown(self):
        found = check_schedule(recurrence.build(["09:00", "12:00"]), MONDAY_NOON)
        assert COOLDOWN in rules(found)
        message = next(v.message for v in found if v.rule == COOLDOWN)
        assert "09:00" in message and "12:00" in message and "3h" in message

    def test_twelve_hours_apart_is_fine(self):
        found = check_schedule(recurrence.build(["09:00", "21:00"]), MONDAY_NOON)
        assert COOLDOWN not in rules(found)

    def test_exactly_the_cooldown_is_fine(self):
        found = check_schedule(recurrence.build(["08:00", "16:00"]), MONDAY_NOON)
        assert COOLDOWN not in rules(found)

    def test_the_gap_across_midnight_counts_too(self):
        """21:00 and 04:00 are twelve hours apart one way and seven the other."""
        found = check_schedule(
            recurrence.build(["04:00", "21:00"]), MONDAY_NOON,
            window_start_hour=0, window_end_hour=0,
        )
        message = next(v.message for v in found if v.rule == COOLDOWN)
        assert "7h" in message and "next day" in message

    def test_once_a_week_never_breaks_it(self):
        found = check_schedule(recurrence.build(["09:00"], days=[0]), MONDAY_NOON)
        assert COOLDOWN not in rules(found)

    def test_three_a_day_inside_posting_hours_always_does(self):
        """08:00-23:00 is fifteen hours; three runs cannot all be 8h apart."""
        found = check_schedule(recurrence.build(["08:00", "15:00", "22:00"]), MONDAY_NOON)
        assert COOLDOWN in rules(found)


class TestTheOtherRepeatChecks:
    def test_a_time_outside_posting_hours(self):
        found = check_schedule(recurrence.build(["06:00", "18:00"]), MONDAY_NOON)
        window = [v for v in found if v.rule == POSTING_WINDOW]
        assert window and "06:00" in window[0].message and "18:00" not in window[0].message

    def test_a_window_crossing_midnight_is_judged_like_the_worker_judges_it(self):
        found = check_schedule(
            recurrence.build(["23:30"]), MONDAY_NOON,
            window_start_hour=22, window_end_hour=6,
        )
        assert POSTING_WINDOW not in rules(found)

    def test_too_many_posts_a_day_for_the_limit(self):
        targets = [ScheduleTarget(name=f"G{i}") for i in range(10)]
        found = check_schedule(
            recurrence.build(["09:00", "21:00"]), MONDAY_NOON, targets=targets, daily_cap=15
        )
        assert DAILY_CAP in rules(found)

    def test_a_group_posted_to_just_before_the_first_run(self):
        first = recurrence.next_occurrence(recurrence.build(["13:00"]), MONDAY_NOON)
        recent = ScheduleTarget(name="Bikes", last_posted_at=first - timedelta(hours=2))
        found = check_schedule(recurrence.build(["13:00"]), MONDAY_NOON, targets=[recent])
        message = next(v.message for v in found if v.rule == COOLDOWN)
        assert "Bikes" in message and "first run" in message

    def test_a_group_that_has_had_every_wording(self):
        seen = ScheduleTarget(name="Bikes", recent_bodies=("One", "two  "))
        found = check_schedule(
            recurrence.build(["09:00"]), MONDAY_NOON, targets=[seen], wordings=["one", "Two"]
        )
        assert REPEAT_TEXT in rules(found)

    def test_one_fresh_wording_is_enough(self):
        seen = ScheduleTarget(name="Bikes", recent_bodies=("one",))
        found = check_schedule(
            recurrence.build(["09:00"]), MONDAY_NOON, targets=[seen], wordings=["one", "three"]
        )
        assert REPEAT_TEXT not in rules(found)

    def test_a_schedule_inside_every_rule_breaks_none(self):
        targets = [ScheduleTarget(name="Bikes")]
        found = check_schedule(
            recurrence.build(["09:00", "18:00"]), MONDAY_NOON,
            targets=targets, wordings=["a", "b"],
        )
        assert found == ()


class TestRotationWithPostAnyway:
    def test_fresh_wordings_are_still_preferred(self):
        assert recurrence.pick_body(["a", "b"], 0, ["a"], allow_repeats=True) == "b"

    def test_when_none_is_fresh_the_rotation_carries_on(self):
        assert recurrence.pick_body(["a", "b"], 1, ["a", "b"], allow_repeats=True) == "b"

    def test_without_it_nothing_is_repeated(self):
        assert recurrence.pick_body(["a", "b"], 1, ["a", "b"]) is None


class TestTheWorkerHonoursIt:
    def test_without_post_anyway_the_cooldown_still_skips(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        ticker = Clock()
        groups.mark_posted(one.id, ticker.now - timedelta(hours=1))
        task = tasks.create(BODY, [(one.id, BODY)])
        make_worker(db, FakePoster(), ticker).run_once()
        assert tasks.targets_for(task.id)[0].state == TARGET_SKIPPED

    def test_with_it_the_post_goes_out(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        ticker = Clock()
        groups.mark_posted(one.id, ticker.now - timedelta(hours=1))
        task = tasks.create(BODY, [(one.id, BODY)], overrides={COOLDOWN})
        poster = FakePoster()
        make_worker(db, poster, ticker).run_once()
        assert tasks.targets_for(task.id)[0].state == TARGET_DONE
        assert poster.group_urls == [one.url]

    def test_repeated_text_goes_out_only_with_it(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        # This one is about the words only; the cooldown has its own test.
        groups.set_cooldown(one.id, 0)
        ticker = Clock()
        first = tasks.create(BODY, [(one.id, BODY)])
        worker = make_worker(db, FakePoster(), ticker)
        worker.run_once()
        assert tasks.targets_for(first.id)[0].state == TARGET_DONE

        ticker.advance(hours=9)
        refused = tasks.create(BODY, [(one.id, BODY)])
        worker.run_once()
        assert tasks.targets_for(refused.id)[0].state == TARGET_SKIPPED

        ticker.advance(minutes=30)
        allowed = tasks.create(BODY, [(one.id, BODY)], overrides={REPEAT_TEXT})
        drain(worker)
        assert tasks.targets_for(allowed.id)[0].state == TARGET_DONE

    def test_outside_posting_hours_only_with_it(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        ticker = Clock(clock.parse_local("2026-08-10 23:30"))
        waits = tasks.create(BODY, [(one.id, BODY)])
        poster = FakePoster()
        worker = make_worker(db, poster, ticker)
        worker.run_once()
        assert poster.requests == [], "posted outside the window without being told to"
        assert tasks.get(waits.id).resume_at is not None

        tasks.cancel(waits.id)
        tasks.create(BODY + " tonight", [(one.id, BODY + " tonight")], overrides={POSTING_WINDOW})
        worker.run_once()
        assert len(poster.requests) == 1

    def test_it_breaks_only_what_it_was_allowed_to(self, db, repos):
        """Allowed the cooldown, not the window: a late batch still waits."""
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        ticker = Clock(clock.parse_local("2026-08-10 23:30"))
        groups.mark_posted(one.id, ticker.now - timedelta(hours=1))
        task = tasks.create(BODY, [(one.id, BODY)], overrides={COOLDOWN})
        poster = FakePoster()
        make_worker(db, poster, ticker).run_once()
        assert poster.requests == []
        assert tasks.get(task.id).resume_at is not None

    def test_a_schedule_hands_it_to_every_batch_it_fires(self, db, repos):
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        schedules = ScheduleRepo(db)
        ticker = Clock()
        schedules.create(
            name="Bikes", bodies=["a", "b"], group_ids=[one.id], times=["12:00"],
            next_run_at=ticker.now, overrides={COOLDOWN},
        )
        make_worker(db, FakePoster(), ticker).run_once()
        fired = tasks.list_recent(1)
        assert fired and fired[0].overrides == frozenset({COOLDOWN})

    def test_a_schedule_allowed_to_repeat_keeps_rotating(self, db, repos):
        """Otherwise a group that has had every wording is skipped for ever."""
        groups, tasks, _ = repos
        (one,) = add_groups(groups, 1)
        groups.set_cooldown(one.id, 0)
        schedules = ScheduleRepo(db)
        ticker = Clock()
        worker = make_worker(db, FakePoster(), ticker)
        # The group has already had the only wording.
        done = tasks.create("only", [(one.id, "only")])
        worker.run_once()
        assert tasks.targets_for(done.id)[0].state == TARGET_DONE

        ticker.advance(hours=1)
        schedules.create(
            name="Again", bodies=["only"], group_ids=[one.id], times=["13:00"],
            next_run_at=ticker.now, overrides={REPEAT_TEXT},
        )
        before = len(tasks.targets_for(done.id))
        for _ in range(4):
            ticker.advance(minutes=30)
            worker.run_once()
        posted = tasks.posted_count_since(ticker.now - timedelta(days=1))
        assert before == 1 and posted == 2


class TestLabels:
    def test_one_rule(self):
        assert rule_labels({COOLDOWN}) == "cooldown between posts to a group"

    def test_several_read_as_a_sentence(self):
        assert rule_labels({DAILY_CAP, POSTING_WINDOW}) == "posting hours and daily limit"


@pytest.fixture
def db(tmp_path):
    from fbposter.db import Database

    database = Database(tmp_path / "anyway.db")
    yield database
    database.close()


@pytest.fixture
def repos(db):
    from fbposter.db.repo import GroupRepo, SettingsRepo, TaskRepo

    return GroupRepo(db), TaskRepo(db), SettingsRepo(db)
