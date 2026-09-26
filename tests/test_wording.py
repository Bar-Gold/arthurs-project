"""The words the rules use, pinned where the logic of them matters.

The screenshot that started this: the "Post anyway" panel listed "...so the
11:00 run would be skipped" -- directly above the button that makes the 11:00
run go out. A refusal says what is true; what happens next is the user's call,
and the panel says what each button does.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter import clock, recurrence
from fbposter.guards import (
    PlannedTarget,
    check_cooldown,
    check_daily_cap,
    evaluate_batch,
    span,
)
from fbposter.recurrence import ScheduleTarget, check_schedule

MONDAY_NOON = clock.parse_local("2026-08-10 12:00")

# Words that predict an outcome. "Post anyway" makes every one of them false.
OUTCOMES = ("skip", "would", "will ", "reword", "nothing fresh", "first.")


def predicts(message: str) -> list[str]:
    lowered = message.lower()
    return [word for word in OUTCOMES if word in lowered]


def every_schedule_refusal():
    """One schedule that breaks every rule a schedule can break."""
    return check_schedule(
        recurrence.build(["03:00", "13:00", "14:00"]),
        MONDAY_NOON,
        targets=[
            ScheduleTarget(
                "Bikes",
                last_posted_at=MONDAY_NOON - timedelta(hours=1),
                recent_bodies=("only wording",),
            )
        ],
        wordings=["only wording"],
        cooldown_hours=8,
        window_start_hour=8,
        window_end_hour=23,
        daily_cap=2,
    )


def every_batch_refusal(when=None):
    return evaluate_batch(
        [
            PlannedTarget(
                1,
                "Bikes",
                "same words",
                last_posted_at=MONDAY_NOON - timedelta(hours=1),
                recent_bodies=("same words",),
            )
        ],
        now=MONDAY_NOON,
        when=when,
        daily_cap=1,
        posted_today=1,
        window_start_hour=13,
        window_end_hour=23,
    ).blocked


class TestARefusalStatesAFact:
    def test_every_schedule_refusal(self):
        found = every_schedule_refusal()
        assert {v.rule for v in found} == {
            "posting_window", "cooldown", "daily_cap", "repeat_text"
        }
        for violation in found:
            assert predicts(violation.message) == [], violation.message

    def test_every_batch_refusal(self):
        found = every_batch_refusal()
        assert len(found) == 4
        for violation in found:
            assert predicts(violation.message) == [], violation.message

    def test_the_screenshot(self):
        (violation,) = [
            v for v in check_schedule(
                recurrence.build(["09:00", "11:00"]), MONDAY_NOON, cooldown_hours=8
            )
        ]
        assert violation.message == (
            "Runs at 09:00 and 11:00 are only 2h apart, less than the 8h "
            "cooldown between posts to the same group."
        )


class TestItSaysWhenItMeans:
    def test_a_scheduled_batch_is_judged_at_its_time(self):
        later = MONDAY_NOON + timedelta(hours=2)
        messages = [v.message for v in every_batch_refusal(when=later)]
        assert any("at the scheduled time" in m for m in messages)

    def test_a_batch_for_another_day_is_not_about_today(self):
        tomorrow = MONDAY_NOON + timedelta(days=1)
        (cap,) = [v for v in every_batch_refusal(when=tomorrow) if v.rule == "daily_cap"]
        assert "that day" in cap.message
        assert "today" not in cap.message

    def test_a_batch_for_now_is_about_today(self):
        (cap,) = [v for v in every_batch_refusal() if v.rule == "daily_cap"]
        assert "posts today" in cap.message

    def test_the_first_run_is_named(self):
        early = [v for v in every_schedule_refusal() if "Bikes was posted" in v.message]
        assert early and early[0].message.endswith("still left at the first run.")

    def test_the_daily_cap_names_the_rule_as_the_rest_of_the_app_does(self):
        assert "daily limit of 25" in check_daily_cap(25, 1, 25).message


class TestSpan:
    @pytest.mark.parametrize(
        "delta, said",
        [
            (timedelta(seconds=5), "1 min"),  # never "0.0h"
            (timedelta(minutes=40), "40 min"),
            (timedelta(hours=2), "2h"),
            (timedelta(hours=3, minutes=10), "3h 10m"),
            (timedelta(hours=7, minutes=59, seconds=1), "8h"),  # rounded up
        ],
    )
    def test_it_reads_like_a_person(self, delta, said):
        assert span(delta) == said

    def test_a_cooldown_about_to_end_is_not_zero(self):
        found = check_cooldown(
            MONDAY_NOON - timedelta(hours=8) + timedelta(seconds=30), MONDAY_NOON, 8, "Bikes"
        )
        assert "1 min of the 8h cooldown" in found.message
