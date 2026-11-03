"""The client's speed and quiet hours, judged without a database or a clock."""

from __future__ import annotations

import random

import pytest

from fbposter import pace
from fbposter.automation.humanize import Humanizer


class TestTheSpeed:
    def test_safe_is_what_the_app_always_did(self):
        assert pace.DEFAULT_SPEED == "safe"
        assert pace.gap_span("safe") == (120, 240)

    def test_each_is_quicker_than_the_last(self):
        spans = [pace.gap_span(key) for key in ("safe", "normal", "fast")]
        assert spans == sorted(spans, reverse=True)

    def test_nothing_is_faster_than_thirty_seconds(self):
        assert min(low for _label, (low, _high) in pace.SPEEDS.values()) == 30

    @pytest.mark.parametrize("stored", ["", "turbo", "SAFE"])
    def test_anything_unknown_is_safe(self, stored):
        assert pace.speed(stored) == "safe"

    @pytest.mark.parametrize(
        "key, words", [("safe", "2-4 min"), ("normal", "1-2 min"), ("fast", "30-90 sec")]
    )
    def test_how_it_is_said(self, key, words):
        assert pace.describe_span(key) == words

    def test_the_humanizer_draws_from_the_range_given(self):
        human = Humanizer(rng=random.Random(1), sleep=lambda _s: None)
        for _ in range(50):
            assert 30 <= human.gap_between_groups((30, 90)) <= 90


class TestQuietHours:
    def test_off_is_never_quiet(self):
        assert not pace.is_quiet(False, 23, 8, 2)

    @pytest.mark.parametrize("hour, quiet", [(22, False), (23, True), (2, True), (7, True), (8, False)])
    def test_hours_across_midnight(self, hour, quiet):
        assert pace.is_quiet(True, 23, 8, hour) is quiet

    @pytest.mark.parametrize("hour, quiet", [(12, False), (13, True), (14, True), (15, False)])
    def test_hours_within_one_day(self, hour, quiet):
        assert pace.is_quiet(True, 13, 15, hour) is quiet

    def test_the_same_hour_twice_is_never_quiet(self):
        # clock.inside_window reads equal hours as "no restriction"; for quiet
        # hours that would be every hour of the day, and nothing would post.
        assert not any(pace.is_quiet(True, 9, 9, hour) for hour in range(24))

    def test_a_broken_stored_hour_falls_back(self):
        assert pace.describe_quiet(99, -4) == "23:00-08:00"

    def test_how_they_are_said(self):
        assert pace.describe_quiet(23, 8) == "23:00-08:00"
