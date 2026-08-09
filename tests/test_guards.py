"""Tests for the safety rules.

These are pure functions, so the whole accept/reject table is covered here --
including the boundaries, which is where a guard usually gets it wrong.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from fbposter import guards
from fbposter.guards import PlannedTarget, evaluate_batch

NOW = datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)


def target(**overrides) -> PlannedTarget:
    base = dict(group_id=1, group_name="gardening.tlv", body="Selling a bike")
    base.update(overrides)
    return PlannedTarget(**base)


class TestDailyCap:
    def test_under_the_cap_is_fine(self):
        assert guards.check_daily_cap(posted_today=10, adding=5, cap=25) is None

    def test_landing_exactly_on_the_cap_is_allowed(self):
        assert guards.check_daily_cap(posted_today=20, adding=5, cap=25) is None

    def test_one_over_is_refused(self):
        violation = guards.check_daily_cap(posted_today=21, adding=5, cap=25)
        assert violation is not None
        assert violation.rule == "daily_cap"

    def test_a_cap_of_zero_disables_the_rule(self):
        assert guards.check_daily_cap(posted_today=100, adding=50, cap=0) is None


class TestCooldown:
    def test_a_group_never_posted_to_is_free(self):
        assert guards.check_cooldown(None, NOW, 24) is None

    def test_inside_the_cooldown_is_refused(self):
        last = NOW - timedelta(hours=5)
        violation = guards.check_cooldown(last, NOW, 24)
        assert violation is not None
        assert violation.rule == "cooldown"

    def test_exactly_at_the_boundary_is_allowed(self):
        last = NOW - timedelta(hours=24)
        assert guards.check_cooldown(last, NOW, 24) is None

    def test_a_second_before_the_boundary_is_refused(self):
        last = NOW - timedelta(hours=24) + timedelta(seconds=1)
        assert guards.check_cooldown(last, NOW, 24) is not None

    def test_a_shortened_cooldown_lets_an_active_group_take_more(self):
        """The user asked to post more than once a day to big, active groups."""
        last = NOW - timedelta(hours=7)
        assert guards.check_cooldown(last, NOW, 24) is not None
        assert guards.check_cooldown(last, NOW, 6) is None

    def test_zero_hours_disables_the_rule(self):
        assert guards.check_cooldown(NOW, NOW, 0) is None


class TestPostingWindow:
    @pytest.mark.parametrize("hour", [8, 12, 22])
    def test_inside_the_window(self, hour):
        assert guards.check_posting_window(NOW.replace(hour=hour), 8, 23) is None

    @pytest.mark.parametrize("hour", [7, 23, 3, 0])
    def test_outside_the_window(self, hour):
        violation = guards.check_posting_window(NOW.replace(hour=hour), 8, 23)
        assert violation is not None
        assert violation.rule == "posting_window"

    def test_the_start_hour_is_inclusive_and_the_end_hour_is_not(self):
        assert guards.check_posting_window(NOW.replace(hour=8, minute=0), 8, 23) is None
        assert guards.check_posting_window(NOW.replace(hour=22, minute=59), 8, 23) is None
        assert guards.check_posting_window(NOW.replace(hour=23, minute=0), 8, 23) is not None


class TestRepeatText:
    def test_new_text_is_fine(self):
        assert guards.check_repeat_text("Something fresh", ["Older post"]) is None

    def test_identical_text_is_refused(self):
        violation = guards.check_repeat_text("Selling a bike", ["Selling a bike"])
        assert violation is not None
        assert violation.rule == "repeat_text"

    @pytest.mark.parametrize(
        "variant",
        [
            "selling a bike",
            "Selling  a   bike",
            "  Selling a bike\n",
            "Selling a bike\n\n",
        ],
    )
    def test_trivial_differences_do_not_count_as_variation(self, variant):
        """Whitespace and capital changes are not new content; treating them as
        such would make the guard give false reassurance."""
        assert guards.check_repeat_text(variant, ["Selling a bike"]) is not None

    def test_genuinely_reworded_text_passes(self):
        assert guards.check_repeat_text("Road bike for sale", ["Selling a bike"]) is None

    def test_empty_text_is_not_judged(self):
        assert guards.check_repeat_text("   ", ["Selling a bike"]) is None


class TestVariationWarning:
    def test_two_identical_bodies_do_not_warn(self):
        assert guards.variation_warning(["same", "same"]) is None

    def test_three_identical_bodies_warn(self):
        assert guards.variation_warning(["same", "same", "same"]) is not None

    def test_varied_bodies_do_not_warn(self):
        assert guards.variation_warning(["one", "two", "three", "four"]) is None

    def test_it_counts_the_worst_group_not_the_total(self):
        bodies = ["a", "a", "a", "b", "c"]
        assert guards.variation_warning(bodies) is not None

    def test_blank_bodies_are_ignored(self):
        assert guards.variation_warning(["", "", "", ""]) is None


class TestEvaluateBatch:
    def test_a_clean_batch_is_allowed(self):
        verdict = evaluate_batch(
            [target(group_id=1, body="one"), target(group_id=2, body="two")],
            now=NOW,
            posted_today=0,
        )
        assert verdict.allowed
        assert verdict.warnings == ()

    def test_no_targets_is_refused(self):
        verdict = evaluate_batch([], now=NOW)
        assert not verdict.allowed
        assert verdict.blocked[0].rule == "empty"

    def test_the_cap_blocks_the_whole_batch(self):
        verdict = evaluate_batch(
            [target(group_id=i, body=f"body {i}") for i in range(5)],
            now=NOW,
            posted_today=24,
            daily_cap=25,
        )
        assert not verdict.allowed
        assert any(v.rule == "daily_cap" for v in verdict.blocked)

    def test_one_group_in_cooldown_blocks_the_batch(self):
        verdict = evaluate_batch(
            [
                target(group_id=1, body="one"),
                target(group_id=2, body="two", last_posted_at=NOW - timedelta(hours=2)),
            ],
            now=NOW,
        )
        assert not verdict.allowed
        assert any(v.rule == "cooldown" for v in verdict.blocked)

    def test_repeated_text_to_one_group_blocks_the_batch(self):
        verdict = evaluate_batch(
            [target(group_id=1, body="Selling a bike", recent_bodies=("Selling a bike",))],
            now=NOW,
        )
        assert not verdict.allowed
        assert any(v.rule == "repeat_text" for v in verdict.blocked)

    def test_a_scheduled_time_outside_the_window_blocks(self):
        verdict = evaluate_batch(
            [target()], now=NOW, when=NOW.replace(hour=4), daily_cap=25
        )
        assert not verdict.allowed
        assert any(v.rule == "posting_window" for v in verdict.blocked)

    def test_the_window_is_judged_on_the_scheduled_time_not_now(self):
        """Queueing at 02:00 for 10:00 tomorrow must be allowed."""
        verdict = evaluate_batch(
            [target()], now=NOW.replace(hour=2), when=NOW.replace(hour=10)
        )
        assert verdict.allowed

    def test_repetition_warns_without_blocking(self):
        verdict = evaluate_batch(
            [target(group_id=i, body="identical") for i in range(3)],
            now=NOW,
        )
        assert verdict.allowed
        assert verdict.warnings
