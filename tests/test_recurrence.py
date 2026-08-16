"""Tests for the repeating-schedule rules.

Pure functions, so a year of firings costs no time at all. The cases that
matter most are the ones a hand test would never catch: a daylight-saving
change, a slot missed while the machine slept, and the wording rotation that
keeps a repeating post from being refused by the repeated-text guard on its
second run.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter import clock, recurrence
from fbposter.recurrence import InvalidRecurrence, Recurrence

GRACE = timedelta(hours=2)

# Israel time. Sunday 2026-08-09 is a working day there; 2026-08-10 is a Monday.
MONDAY_NOON = clock.parse_local("2026-08-10 12:00")


class TestParsing:
    def test_a_plain_time_of_day(self):
        assert recurrence.parse_hhmm("09:30") == (9, 30)

    def test_midnight(self):
        assert recurrence.parse_hhmm("00:00") == (0, 0)

    @pytest.mark.parametrize("bad", ["9", "9.30", "", "24:00", "12:60", "-1:00", "ab:cd"])
    def test_anything_else_is_refused(self, bad):
        with pytest.raises(InvalidRecurrence):
            recurrence.parse_hhmm(bad)

    def test_times_are_sorted_and_deduplicated(self):
        rule = recurrence.build(["18:00", "9:00", "09:00"])
        assert rule.times == ("09:00", "18:00")

    def test_more_than_the_ceiling_is_refused(self):
        too_many = ["06:00", "10:00", "14:00", "18:00"]
        assert len(too_many) > recurrence.MAX_TIMES_PER_DAY
        with pytest.raises(InvalidRecurrence):
            recurrence.build(too_many)

    def test_three_times_a_day_is_allowed(self):
        rule = recurrence.build(["09:00", "14:00", "20:00"])
        assert rule.per_day == 3

    def test_no_times_at_all_is_refused(self):
        with pytest.raises(InvalidRecurrence):
            recurrence.build([])

    def test_all_seven_days_is_stored_as_every_day(self):
        rule = recurrence.build(["09:00"], days=[0, 1, 2, 3, 4, 5, 6])
        assert rule.days == ()
        assert rule.is_daily

    def test_a_day_outside_the_week_is_refused(self):
        with pytest.raises(InvalidRecurrence):
            recurrence.build(["09:00"], days=[7])


class TestNextOccurrence:
    def test_later_today(self):
        moment = recurrence.next_occurrence(recurrence.build(["18:00"]), MONDAY_NOON)
        assert clock.format_local(moment) == "2026-08-10 18:00"

    def test_a_time_already_past_rolls_to_tomorrow(self):
        moment = recurrence.next_occurrence(recurrence.build(["09:00"]), MONDAY_NOON)
        assert clock.format_local(moment) == "2026-08-11 09:00"

    def test_the_current_minute_does_not_count_as_the_next_one(self):
        """Strictly after, or a schedule would fire itself again immediately."""
        rule = recurrence.build(["12:00"])
        moment = recurrence.next_occurrence(rule, MONDAY_NOON)
        assert moment > MONDAY_NOON
        assert clock.format_local(moment) == "2026-08-11 12:00"

    def test_the_earliest_of_several_times_wins(self):
        rule = recurrence.build(["09:00", "14:00", "20:00"])
        moment = recurrence.next_occurrence(rule, MONDAY_NOON)
        assert clock.format_local(moment) == "2026-08-10 14:00"

    def test_days_of_the_week_are_honoured(self):
        # Wednesday only. From Monday noon that is two days out.
        rule = recurrence.build(["09:00"], days=[2])
        moment = recurrence.next_occurrence(rule, MONDAY_NOON)
        assert clock.format_local(moment) == "2026-08-12 09:00"

    def test_several_days_pick_the_nearest(self):
        rule = recurrence.build(["09:00"], days=[0, 3])  # Mon and Thu
        moment = recurrence.next_occurrence(rule, MONDAY_NOON)
        assert clock.format_local(moment) == "2026-08-13 09:00"

    def test_a_weekly_rule_wraps_round_the_week(self):
        rule = recurrence.build(["09:00"], days=[0])  # Mondays
        moment = recurrence.next_occurrence(rule, MONDAY_NOON)
        assert clock.format_local(moment) == "2026-08-17 09:00"

    def test_stepping_a_full_week_lands_on_the_same_local_time(self):
        """Fourteen steps of a twice-daily rule is exactly one week on."""
        rule = recurrence.build(["09:00", "21:00"])
        moment = MONDAY_NOON
        for _ in range(14):
            moment = recurrence.next_occurrence(rule, moment)
        assert clock.format_local(moment) == "2026-08-17 09:00"

    def test_occurrences_only_ever_move_forwards(self):
        rule = recurrence.build(["08:15", "13:45", "22:30"], days=[0, 2, 4])
        moment = MONDAY_NOON
        for _ in range(60):
            following = recurrence.next_occurrence(rule, moment)
            assert following > moment
            moment = following


class TestDaylightSaving:
    """The reason times are stored as wall clock rather than as an interval.

    Israel moves to daylight time at 02:00 on Friday 2026-03-27.
    """

    def setup_method(self):
        if clock.posting_zone() is None:
            pytest.skip("no IANA time zone database; tzdata is missing")

    def test_nine_in_the_morning_stays_nine_across_the_spring_change(self):
        rule = recurrence.build(["09:00"])
        moment = clock.parse_local("2026-03-26 08:00")
        shown = []
        for _ in range(3):
            moment = recurrence.next_occurrence(rule, moment)
            shown.append(clock.format_local(moment))
        assert shown == ["2026-03-26 09:00", "2026-03-27 09:00", "2026-03-28 09:00"]

    def test_the_step_across_the_change_is_23_hours_not_24(self):
        """Which is exactly what adding timedelta(days=1) would have got wrong."""
        rule = recurrence.build(["09:00"])
        before = recurrence.next_occurrence(rule, clock.parse_local("2026-03-26 08:00"))
        across = recurrence.next_occurrence(rule, before)
        assert across - before == timedelta(hours=23)


class TestEvaluateDue:
    def test_a_future_slot_is_not_due(self):
        rule = recurrence.build(["09:00"])
        later = MONDAY_NOON + timedelta(hours=1)
        assert recurrence.evaluate_due(rule, later, MONDAY_NOON, GRACE) is None

    def test_a_slot_that_has_just_arrived_fires(self):
        rule = recurrence.build(["12:00"])
        verdict = recurrence.evaluate_due(rule, MONDAY_NOON, MONDAY_NOON, GRACE)
        assert verdict is not None
        assert verdict.fire and not verdict.missed

    def test_firing_advances_to_the_next_slot(self):
        rule = recurrence.build(["12:00"])
        verdict = recurrence.evaluate_due(rule, MONDAY_NOON, MONDAY_NOON, GRACE)
        assert clock.format_local(verdict.next_run_at) == "2026-08-11 12:00"

    def test_a_slot_inside_the_grace_period_still_fires(self):
        rule = recurrence.build(["12:00"])
        verdict = recurrence.evaluate_due(
            rule, MONDAY_NOON, MONDAY_NOON + timedelta(minutes=90), GRACE
        )
        assert verdict.fire

    def test_a_slot_older_than_the_grace_period_is_missed_not_fired(self):
        rule = recurrence.build(["12:00"])
        verdict = recurrence.evaluate_due(
            rule, MONDAY_NOON, MONDAY_NOON + timedelta(hours=5), GRACE
        )
        assert verdict.missed and not verdict.fire

    def test_a_missed_slot_still_advances_past_now(self):
        """Otherwise waking the machine would fire every slot it slept through."""
        rule = recurrence.build(["09:00", "12:00", "18:00"])
        woke = MONDAY_NOON + timedelta(hours=5)  # 17:00
        verdict = recurrence.evaluate_due(rule, MONDAY_NOON, woke, GRACE)
        assert verdict.next_run_at > woke
        assert clock.format_local(verdict.next_run_at) == "2026-08-10 18:00"

    def test_a_schedule_with_no_next_run_is_initialised_not_fired(self):
        rule = recurrence.build(["18:00"])
        verdict = recurrence.evaluate_due(rule, None, MONDAY_NOON, GRACE)
        assert not verdict.fire and not verdict.missed
        assert clock.format_local(verdict.next_run_at) == "2026-08-10 18:00"


class TestPickBody:
    VARIANTS = ["first wording", "second wording", "third wording"]

    def test_the_offset_chooses_the_variant(self):
        assert recurrence.pick_body(self.VARIANTS, 0) == "first wording"
        assert recurrence.pick_body(self.VARIANTS, 1) == "second wording"

    def test_the_offset_wraps(self):
        assert recurrence.pick_body(self.VARIANTS, 3) == "first wording"
        assert recurrence.pick_body(self.VARIANTS, 7) == "second wording"

    def test_a_wording_already_sent_to_this_group_is_skipped(self):
        chosen = recurrence.pick_body(self.VARIANTS, 0, ["first wording"])
        assert chosen == "second wording"

    def test_skipping_respects_the_same_folding_as_the_guard(self):
        """Trailing whitespace and case are not content variation."""
        chosen = recurrence.pick_body(self.VARIANTS, 0, ["  First   Wording \n"])
        assert chosen == "second wording"

    def test_none_when_every_wording_has_already_been_used(self):
        assert recurrence.pick_body(self.VARIANTS, 0, self.VARIANTS) is None

    def test_none_when_there_are_no_wordings(self):
        assert recurrence.pick_body([], 0) is None

    def test_blank_wordings_are_ignored(self):
        assert recurrence.pick_body(["", "   ", "real"], 0) == "real"

    def test_each_group_in_a_run_gets_a_different_wording(self):
        """The reason the offset exists: three groups, three wordings, no repeats."""
        chosen = [recurrence.pick_body(self.VARIANTS, position) for position in range(3)]
        assert len(set(chosen)) == 3

    def test_the_cycle_shifts_on_the_next_run(self):
        run_one = [recurrence.pick_body(self.VARIANTS, 0 + p) for p in range(3)]
        run_two = [recurrence.pick_body(self.VARIANTS, 1 + p) for p in range(3)]
        assert run_one != run_two


class TestIdenticalReach:
    def test_one_wording_per_group_reaches_one(self):
        assert recurrence.identical_reach(3, 3) == 1

    def test_fewer_wordings_than_groups_doubles_up(self):
        assert recurrence.identical_reach(5, 2) == 3

    def test_no_wordings_means_everything_is_the_same_text(self):
        assert recurrence.identical_reach(4, 0) == 4

    def test_no_groups_reaches_nothing(self):
        assert recurrence.identical_reach(0, 3) == 0


class TestDescribe:
    def test_daily_at_one_time(self):
        assert recurrence.describe(recurrence.build(["09:00"])) == "Every day at 09:00"

    def test_daily_at_two_times(self):
        text = recurrence.describe(recurrence.build(["09:00", "18:00"]))
        assert text == "Every day at 09:00 and 18:00"

    def test_daily_at_three_times(self):
        text = recurrence.describe(recurrence.build(["09:00", "14:00", "20:00"]))
        assert text == "Every day at 09:00, 14:00 and 20:00"

    def test_a_single_day(self):
        text = recurrence.describe(recurrence.build(["09:00"], days=[0]))
        assert text == "Every Mon at 09:00"

    def test_several_days(self):
        text = recurrence.describe(recurrence.build(["09:00"], days=[0, 2, 4]))
        assert text == "Mon, Wed, Fri at 09:00"


class TestPreview:
    def test_it_lists_the_next_few_runs(self):
        report = recurrence.preview(recurrence.build(["09:00"]), MONDAY_NOON, ahead=3)
        assert len(report.next_runs) == 3
        assert clock.format_local(report.next_runs[0]) == "2026-08-11 09:00"

    def test_a_time_outside_the_posting_window_is_flagged(self):
        report = recurrence.preview(
            recurrence.build(["03:00"]),
            MONDAY_NOON,
            window_start_hour=8,
            window_end_hour=23,
        )
        assert any("posting window" in w for w in report.warnings)

    def test_a_time_inside_the_window_is_not_flagged(self):
        report = recurrence.preview(
            recurrence.build(["10:00"]),
            MONDAY_NOON,
            window_start_hour=8,
            window_end_hour=23,
            cooldown_hours=0,
        )
        assert not any("posting window" in w for w in report.warnings)

    def test_posting_faster_than_the_cooldown_is_flagged(self):
        """Three times a day against a 24h cooldown skips two runs in three."""
        report = recurrence.preview(
            recurrence.build(["09:00", "14:00", "20:00"]),
            MONDAY_NOON,
            cooldown_hours=24,
        )
        assert any("cooldown" in w for w in report.warnings)

    def test_once_a_day_against_a_daily_cooldown_is_fine(self):
        report = recurrence.preview(
            recurrence.build(["09:00"]), MONDAY_NOON, cooldown_hours=24
        )
        assert not any("cooldown" in w for w in report.warnings)

    def test_too_few_wordings_for_the_groups_is_flagged(self):
        report = recurrence.preview(
            recurrence.build(["09:00"]),
            MONDAY_NOON,
            group_count=6,
            variant_count=2,
            cooldown_hours=0,
        )
        assert any("Repetitive content" in w for w in report.warnings)

    def test_a_wording_per_group_is_not_flagged(self):
        report = recurrence.preview(
            recurrence.build(["09:00"]),
            MONDAY_NOON,
            group_count=3,
            variant_count=3,
            cooldown_hours=0,
        )
        assert not any("Repetitive content" in w for w in report.warnings)


class TestRecurrenceDefaults:
    def test_an_empty_day_list_means_daily(self):
        assert Recurrence(times=("09:00",)).is_daily

    def test_per_day_counts_the_times(self):
        assert Recurrence(times=("09:00", "18:00")).per_day == 2
