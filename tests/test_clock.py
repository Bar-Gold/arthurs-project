"""Tests for Israel-time handling.

Storage is UTC; every human judgement is local. Getting this wrong is not
cosmetic -- reading the hour off a UTC timestamp allowed posting at 01:00 local
and refused it at 09:00. The posting hours no longer hold posts back (there are
no posting rules since 2026-10-18), but they still decide when the app opens a
group page to follow up a post waiting for an admin, so they still have to be
judged in Israel time.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from fbposter import clock


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


def inside(moment: datetime, start: int = 8, end: int = 23) -> bool:
    return clock.inside_window(clock.local_hour(moment), start, end)


class TestPostingWindowUsesLocalTime:
    """The exact cases the UTC bug got wrong, in both directions."""

    def test_one_am_israel_is_outside(self):
        # 22:00 UTC is 01:00 the next day in Israel. Reading the UTC hour saw
        # 22 and judged it inside 08-23.
        assert not inside(utc(2026, 8, 10, 22, 0))

    def test_nine_am_israel_is_inside(self):
        # 06:00 UTC is 09:00 in Israel. The UTC hour of 6 looked like the
        # middle of the night.
        assert inside(utc(2026, 8, 10, 6, 0))

    def test_midnight_israel_is_outside(self):
        assert not inside(utc(2026, 8, 10, 21, 0))

    def test_ten_pm_israel_is_inside(self):
        assert inside(utc(2026, 8, 10, 19, 0))

    def test_the_window_holds_across_the_dst_change(self):
        """08:00 local is 06:00 UTC in summer and 05:00 UTC in winter."""
        assert inside(utc(2026, 8, 10, 6, 0))
        assert inside(utc(2026, 1, 15, 6, 0))
        # An hour earlier, local time is 07:00 in both -- outside the window.
        assert not inside(utc(2026, 8, 10, 4, 0))
        assert not inside(utc(2026, 1, 15, 5, 0))


class TestAWindowThatCrossesMidnight:
    """22:00-06:00 used to exclude every hour of the day, and the deferral
    that waited for it waited one day at a time for ever."""

    @pytest.mark.parametrize("when", ["22:00", "23:30", "00:30", "02:00", "05:59"])
    def test_hours_inside_an_overnight_window(self, when):
        assert inside(clock.parse_local(f"2026-08-10 {when}"), 22, 6)

    @pytest.mark.parametrize("when", ["06:00", "12:00", "21:59"])
    def test_hours_outside_it(self, when):
        assert not inside(clock.parse_local(f"2026-08-10 {when}"), 22, 6)

    def test_it_reopens_at_the_start_hour(self):
        noon = clock.parse_local("2026-08-10 12:00")
        reopens = clock.next_window_open(noon, 22, 6)
        assert clock.format_local(reopens) == "2026-08-10 22:00"
        assert inside(reopens, 22, 6)

    def test_inside_the_window_it_does_not_wait_at_all(self):
        night = clock.parse_local("2026-08-11 01:00")
        assert clock.next_window_open(night, 22, 6) == night

    def test_the_ordinary_daytime_window(self):
        late = clock.parse_local("2026-08-10 23:30")
        assert not inside(late)
        assert clock.format_local(clock.next_window_open(late, 8, 23)) == "2026-08-11 08:00"


class TestNonsenseWindowHours:
    """A stored setting reads back as a valid int however silly it is, so -4
    and 99 reached datetime.replace() and raised "hour must be in 0..23"."""

    @pytest.mark.parametrize("start,end", [(-4, 23), (8, 99), (-1, -2), (99, 99), (24, 25)])
    def test_they_never_raise(self, start, end):
        clock.next_window_open(clock.parse_local("2026-08-10 12:00"), start, end)

    def test_an_out_of_range_hour_falls_back_to_the_default(self):
        assert clock.sane_hour(-4, 8) == 8
        assert clock.sane_hour(99, 23) == 23
        assert clock.sane_hour("nonsense", 8) == 8

    def test_a_valid_hour_is_kept(self):
        for hour in (0, 8, 23):
            assert clock.sane_hour(hour, 99) == hour


class TestLocalDay:
    def test_the_day_starts_at_local_midnight(self):
        """Counted on the Dashboard: a post at 00:30 belongs to that day."""
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
