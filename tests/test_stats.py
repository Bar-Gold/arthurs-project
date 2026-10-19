"""The Dashboard's numbers, worked out without a database, a window or a clock.

Every day here is an Israel day, judged on calendar dates: a post at 01:00 on
Tuesday is Tuesday's even though UTC still calls it Monday, and "the last 7
days" is seven dates, not 168 hours, so a daylight-saving change cannot move a
post into the wrong day.
"""

from __future__ import annotations

from datetime import timedelta

from fbposter import clock, stats
from fbposter.db.models import (
    SCHEDULE_ACTIVE,
    SCHEDULE_PAUSED,
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_PENDING,
    TARGET_SKIPPED,
    TARGET_UNCONFIRMED,
    TASK_PENDING,
    TASK_RUNNING,
    Group,
    PostRecord,
    Schedule,
    Task,
)

# A Wednesday, at noon in Israel.
NOW = clock.parse_local("2026-10-14 12:00")


def at(text: str):
    return clock.parse_local(text)


def sent(when: str, group_id: int = 1, body: str = "Bike for sale", state=TARGET_DONE):
    return PostRecord(group_id=group_id, state=state, at=at(when), body=body)


def summary(records, archived=None):
    return stats.summary(records, archived or {}, NOW)


class TestToday:
    def test_counts_only_today_in_israel(self):
        records = [
            sent("2026-10-14 00:30"),
            sent("2026-10-14 11:00"),
            sent("2026-10-13 23:30"),
        ]
        assert summary(records).today == 2

    def test_a_post_after_midnight_israel_is_today_though_utc_says_yesterday(self):
        # 00:30 in Israel is 21:30 UTC the day before.
        record = sent("2026-10-14 00:30")
        assert record.at.date().isoformat() == "2026-10-13"
        assert summary([record]).today == 1

    def test_counts_what_counts_as_sent(self):
        """Posted, waiting for an admin and might-have-posted all count; a
        failure or a skip does not."""
        records = [
            sent("2026-10-14 09:00", state=TARGET_DONE),
            sent("2026-10-14 09:30", state=TARGET_AWAITING_APPROVAL),
            sent("2026-10-14 10:00", state=TARGET_UNCONFIRMED),
            sent("2026-10-14 10:30", state=TARGET_FAILED),
            sent("2026-10-14 11:00", state=TARGET_SKIPPED),
        ]
        assert summary(records).today == 3


class TestWeeksAndMonths:
    def test_the_last_seven_days_include_today(self):
        records = [sent("2026-10-14 09:00"), sent("2026-10-08 09:00"), sent("2026-10-07 09:00")]
        result = summary(records)
        assert result.last_week == 2
        assert result.week_before == 1

    def test_the_week_before_is_the_seven_days_before_that(self):
        records = [sent("2026-10-07 09:00"), sent("2026-10-01 09:00"), sent("2026-09-30 09:00")]
        assert summary(records).week_before == 2

    def test_thirty_days(self):
        records = [sent("2026-09-15 09:00"), sent("2026-09-14 09:00")]
        assert summary(records).last_month == 1

    def test_a_day_across_the_clock_change_still_counts_once(self):
        """Israel's clocks went back on 2026-10-25; a seven-date window
        around it still holds exactly the posts made on those dates."""
        now = at("2026-10-28 12:00")
        records = [sent("2026-10-22 00:30"), sent("2026-10-21 23:30")]
        result = stats.summary(records, {}, now)
        assert result.last_week == 1


class TestAllTime:
    def test_is_what_is_stored_plus_what_was_pruned(self):
        archived = {(1, TARGET_DONE): (40, at("2026-01-02 10:00"))}
        result = summary([sent("2026-10-14 09:00")], archived)
        assert result.all_time == 41

    def test_only_sent_states_are_taken_from_the_archive(self):
        archived = {
            (1, TARGET_DONE): (5, at("2026-01-02 10:00")),
            (1, TARGET_FAILED): (9, at("2025-12-01 10:00")),
        }
        result = summary([], archived)
        assert result.all_time == 5
        assert result.since == at("2026-01-02 10:00")

    def test_since_is_the_first_post_on_record(self):
        result = summary([sent("2026-10-01 09:00"), sent("2026-10-10 09:00")])
        assert result.since == at("2026-10-01 09:00")

    def test_nothing_sent_has_no_since(self):
        assert summary([]).since is None
        assert summary([]).all_time == 0


class TestSuccessRate:
    def test_is_what_went_out_of_what_was_tried(self):
        records = [
            sent("2026-10-14 09:00"),
            sent("2026-10-13 09:00", state=TARGET_AWAITING_APPROVAL),
            sent("2026-10-12 09:00", state=TARGET_DECLINED),
            sent("2026-10-11 09:00", state=TARGET_FAILED),
        ]
        result = summary(records)
        assert result.tried == 4
        assert result.success_rate == 0.75

    def test_a_skip_is_not_a_failure(self):
        records = [sent("2026-10-14 09:00"), sent("2026-10-14 10:00", state=TARGET_SKIPPED)]
        assert summary(records).success_rate == 1.0

    def test_might_have_posted_is_not_counted_as_a_success(self):
        records = [sent("2026-10-14 09:00"), sent("2026-10-14 10:00", state=TARGET_UNCONFIRMED)]
        assert summary(records).success_rate == 0.5

    def test_nothing_tried_has_no_rate(self):
        assert summary([]).success_rate is None
        assert summary([sent("2026-10-14 10:00", state=TARGET_SKIPPED)]).success_rate is None

    def test_older_than_thirty_days_is_left_out(self):
        records = [sent("2026-08-01 09:00", state=TARGET_FAILED), sent("2026-10-14 09:00")]
        assert summary(records).success_rate == 1.0


class TestWordings:
    def test_counts_different_texts_in_the_last_week(self):
        records = [
            sent("2026-10-14 09:00", body="Bike for sale"),
            sent("2026-10-13 09:00", body="Bike for sale"),
            sent("2026-10-12 09:00", body="A road bike, barely used"),
            sent("2026-09-01 09:00", body="Something from last month"),
        ]
        assert summary(records).wordings == 2

    def test_an_invisible_mark_does_not_make_a_new_wording(self):
        records = [
            sent("2026-10-14 09:00", body="Bike for sale"),
            sent("2026-10-13 09:00", body="Bike‏ for sale"),
        ]
        assert summary(records).wordings == 1

    def test_pictures_without_text_are_not_a_wording(self):
        assert summary([sent("2026-10-14 09:00", body="")]).wordings == 0


class TestPerDay:
    def test_one_entry_per_day_oldest_first_ending_today(self):
        days = stats.per_day([], NOW)
        assert len(days) == stats.CHART_DAYS
        assert days[-1][0].isoformat() == "2026-10-14"
        assert days[0][0].isoformat() == "2026-10-01"
        assert all(count == 0 for _day, count in days)

    def test_counts_land_on_their_israel_day(self):
        days = dict(stats.per_day([sent("2026-10-14 00:30"), sent("2026-10-13 23:30")], NOW))
        assert [days[d] for d in sorted(days)][-2:] == [1, 1]

    def test_older_posts_and_failures_are_left_out(self):
        records = [sent("2026-09-01 09:00"), sent("2026-10-14 09:00", state=TARGET_FAILED)]
        assert all(count == 0 for _day, count in stats.per_day(records, NOW))


class TestOutcomes:
    def test_counts_every_result_of_the_last_thirty_days(self):
        records = [
            sent("2026-10-14 09:00"),
            sent("2026-10-14 10:00"),
            sent("2026-10-13 10:00", state=TARGET_SKIPPED),
            sent("2026-10-12 10:00", state=TARGET_FAILED),
            sent("2026-08-01 10:00", state=TARGET_FAILED),
        ]
        assert stats.outcomes(records, NOW) == {
            TARGET_DONE: 2, TARGET_SKIPPED: 1, TARGET_FAILED: 1,
        }

    def test_a_turn_that_never_came_is_not_a_result(self):
        record = PostRecord(group_id=1, state=TARGET_PENDING, at=None)
        assert stats.outcomes([record], NOW) == {}


def group(group_id, name="", last=None, cooldown=8, members=""):
    return Group(
        id=group_id, identifier=str(group_id), url="", name=name,
        cooldown_hours=cooldown, last_posted_at=last, members=members,
    )


class TestByGroup:
    def test_counts_each_group_and_sorts_the_busiest_first(self):
        records = [
            sent("2026-10-14 09:00", group_id=2),
            sent("2026-10-13 09:00", group_id=2),
            sent("2026-09-01 09:00", group_id=1),
        ]
        rows = stats.by_group(records, {}, [group(1, "Quiet"), group(2, "Busy")], NOW)
        assert [r.name for r in rows] == ["Busy", "Quiet"]
        busy = rows[0]
        assert (busy.today, busy.last_week, busy.all_time) == (1, 2, 2)
        assert busy.last_post_at == at("2026-10-14 09:00")

    def test_all_time_includes_what_was_pruned(self):
        archived = {(1, TARGET_DONE): (30, None), (1, TARGET_FAILED): (4, None)}
        (row,) = stats.by_group([sent("2026-10-14 09:00")], archived, [group(1)], NOW)
        assert row.all_time == 31

    def test_a_group_never_posted_to(self):
        (row,) = stats.by_group([], {}, [group(1, members="12K members")], NOW)
        assert (row.all_time, row.last_post_at) == (0, None)
        assert row.members == "12K members"
        assert row.name == "1"  # no name yet: its identifier

    def test_a_removed_group_has_no_row(self):
        """Only the groups passed in (the list on screen) get a row."""
        rows = stats.by_group([sent("2026-10-14 09:00", group_id=9)], {}, [group(1)], NOW)
        assert [r.group_id for r in rows] == [1]


def task(task_id=1, state=TASK_PENDING, scheduled=None, resume=None, schedule_id=None):
    return Task(
        id=task_id, body="Bike", state=state, scheduled_for=scheduled,
        resume_at=resume, schedule_id=schedule_id,
    )


def schedule(schedule_id=1, name="Bikes", state=SCHEDULE_ACTIVE, next_run=None):
    return Schedule(id=schedule_id, name=name, state=state, next_run_at=next_run)


class TestNextScheduled:
    def test_nothing_at_all(self):
        assert stats.next_scheduled([], [], False, NOW).kind == stats.NEXT_NONE

    def test_paused_wins(self):
        result = stats.next_scheduled([task(state=TASK_RUNNING)], [], True, NOW)
        assert result.kind == stats.NEXT_PAUSED

    def test_a_running_batch_says_when_its_next_group_is_due(self):
        resume = at("2026-10-14 12:20")
        result = stats.next_scheduled([task(state=TASK_RUNNING, resume=resume)], [], False, NOW)
        assert (result.kind, result.at) == (stats.NEXT_POSTING, resume)

    def test_a_running_batch_mid_post_has_no_time(self):
        result = stats.next_scheduled([task(state=TASK_RUNNING)], [], False, NOW)
        assert (result.kind, result.at) == (stats.NEXT_POSTING, None)

    def test_a_batch_due_now(self):
        assert stats.next_scheduled([task()], [], False, NOW).kind == stats.NEXT_DUE

    def test_the_soonest_of_one_off_and_repeating(self):
        later = task(scheduled=at("2026-10-14 18:00"))
        sooner = schedule(next_run=at("2026-10-14 15:00"))
        result = stats.next_scheduled([later], [sooner], False, NOW)
        assert (result.kind, result.at, result.name) == (
            stats.NEXT_SCHEDULED, at("2026-10-14 15:00"), "Bikes"
        )

    def test_a_one_off_post_has_no_name(self):
        result = stats.next_scheduled([task(scheduled=at("2026-10-15 09:00"))], [], False, NOW)
        assert result.name == ""

    def test_a_paused_repeating_post_is_not_next(self):
        paused = schedule(state=SCHEDULE_PAUSED, next_run=at("2026-10-14 13:00"))
        assert stats.next_scheduled([], [paused], False, NOW).kind == stats.NEXT_NONE


class TestWords:
    def test_when_today_tomorrow_and_later(self):
        assert stats.when_text(at("2026-10-14 18:00"), NOW) == "Today 18:00"
        assert stats.when_text(at("2026-10-15 09:00"), NOW) == "Tomorrow 09:00"
        assert stats.when_text(at("2026-10-13 09:00"), NOW) == "Yesterday 09:00"
        assert stats.when_text(at("2026-10-20 09:00"), NOW) == "Tue 20 Oct 09:00"

    def test_ago(self):
        assert stats.ago_text(None, NOW) == "never"
        assert stats.ago_text(NOW - timedelta(seconds=20), NOW) == "just now"
        assert stats.ago_text(NOW - timedelta(minutes=25), NOW) == "25m ago"
        assert stats.ago_text(NOW - timedelta(hours=3), NOW) == "3h ago"
        assert stats.ago_text(NOW - timedelta(days=4), NOW) == "4d ago"
