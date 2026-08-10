"""Tests for Israel-time handling.

Storage is UTC; every human judgement is local. Getting this wrong is not
cosmetic -- reading the hour off a UTC timestamp allowed posting at 01:00 local
and refused it at 09:00, which is exactly the 4am-activity signal the whole
posting window exists to avoid.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fbposter import clock, guards


def utc(year, month, day, hour, minute=0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestZone:
    def test_the_israel_zone_is_available(self):
        """Windows ships no IANA database, so tzdata is a real dependency."""
        assert clock.posting_zone() is not None, "install tzdata"

    def test_summer_is_utc_plus_three(self):
        assert clock.to_local(utc(2026, 8, 10, 16, 0)).hour == 19

    def test_winter_is_utc_plus_two(self):
        """DST must come from the tz database, not a hardcoded offset."""
        assert clock.to_local(utc(2026, 1, 15, 12, 0)).hour == 14

    def test_a_naive_datetime_is_treated_as_utc(self):
        naive = datetime(2026, 8, 10, 16, 0)
        assert clock.to_local(naive).hour == 19


class TestPostingWindowUsesLocalTime:
    """The exact cases the UTC bug got wrong, in both directions."""

    def test_one_am_israel_is_refused(self):
        # 22:00 UTC is 01:00 the next day in Israel. Reading the UTC hour saw
        # 22, judged it inside 08-23, and would have posted at 1am.
        violation = guards.check_posting_window(utc(2026, 8, 10, 22, 0), 8, 23)
        assert violation is not None
        assert violation.rule == "posting_window"

    def test_nine_am_israel_is_allowed(self):
        # 06:00 UTC is 09:00 in Israel. The UTC hour of 6 looked like the
        # middle of the night and refused a perfectly normal morning post.
        assert guards.check_posting_window(utc(2026, 8, 10, 6, 0), 8, 23) is None

    def test_midnight_israel_is_refused(self):
        assert guards.check_posting_window(utc(2026, 8, 10, 21, 0), 8, 23) is not None

    def test_ten_pm_israel_is_allowed(self):
        assert guards.check_posting_window(utc(2026, 8, 10, 19, 0), 8, 23) is None

    def test_the_message_says_israel_time(self):
        violation = guards.check_posting_window(utc(2026, 8, 10, 22, 0), 8, 23)
        assert "Israel" in violation.message
        assert "01:00" in violation.message

    def test_the_window_holds_across_the_dst_change(self):
        """08:00 local is 06:00 UTC in summer and 05:00 UTC in winter."""
        assert guards.check_posting_window(utc(2026, 8, 10, 6, 0), 8, 23) is None
        assert guards.check_posting_window(utc(2026, 1, 15, 6, 0), 8, 23) is None
        # An hour earlier, local time is 07:00 in both -- outside the window.
        assert guards.check_posting_window(utc(2026, 8, 10, 4, 0), 8, 23) is not None
        assert guards.check_posting_window(utc(2026, 1, 15, 5, 0), 8, 23) is not None


class TestLocalDay:
    def test_the_day_starts_at_local_midnight(self):
        """Counted for the daily cap: a post at 00:30 belongs to that day."""
        start = clock.start_of_local_day(utc(2026, 8, 10, 16, 0))
        assert clock.to_local(start).hour == 0
        assert clock.to_local(start).date() == clock.to_local(utc(2026, 8, 10, 16, 0)).date()

    def test_late_evening_utc_already_belongs_to_the_next_local_day(self):
        # 22:00 UTC on the 10th is 01:00 on the 11th in Israel.
        moment = utc(2026, 8, 10, 22, 0)
        assert clock.to_local(clock.start_of_local_day(moment)).day == 11

    def test_the_returned_value_is_utc(self):
        start = clock.start_of_local_day(utc(2026, 8, 10, 16, 0))
        assert start.tzinfo == timezone.utc


class TestParsingAndFormatting:
    def test_typed_local_times_round_trip(self):
        parsed = clock.parse_local("2026-08-10 21:30")
        assert clock.format_local(parsed) == "2026-08-10 21:30"

    def test_a_typed_local_time_is_stored_as_utc(self):
        parsed = clock.parse_local("2026-08-10 21:30")
        assert parsed.tzinfo == timezone.utc
        assert parsed.hour == 18  # 21:30 IDT is 18:30 UTC

    def test_a_typed_winter_time_uses_the_winter_offset(self):
        assert clock.parse_local("2026-01-15 21:30").hour == 19  # IST is UTC+2

    def test_a_malformed_time_raises(self):
        with pytest.raises(ValueError):
            clock.parse_local("next tuesday")

    def test_formatting_nothing_gives_an_empty_string(self):
        assert clock.format_local(None) == ""


class TestScheduleEntryUsesTheSameClock:
    def test_the_compose_parser_matches_the_clock_module(self):
        from fbposter.ui.views.compose import parse_schedule

        assert parse_schedule("2026-08-10 21:30") == clock.parse_local("2026-08-10 21:30")
