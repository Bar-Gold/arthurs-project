"""Repeating schedules, as pure functions.

A recurrence is a *definition*: "these wordings, to these groups, at 09:00 and
18:00 every day". It is not a queue entry. Each time one comes due the worker
materialises an ordinary `tasks` row from it, so everything downstream -- the
serial worker, the inter-group gap, crash recovery, the daily cap -- keeps
working exactly as it already did, with no second code path.

Nothing here touches the database or the clock: callers pass `now` in and get a
judgement back, the same arrangement as `guards.py`. That is what makes a
year's worth of firings testable in milliseconds.

Two things this module exists to get right:

* **Occurrences are absolute instants, computed in Israel local time.** "Every
  day at 09:00" means nine in the morning to a human, across a daylight-saving
  change, which is a different number of seconds each time. Adding 24h to the
  last run would drift an hour twice a year; the times are recomputed from the
  local wall clock instead.
* **A missed slot is dropped, never fired late.** Same rule as
  `worker.MISSED_GRACE`: waking the machine at noon must not fire the 09:00
  post, and certainly must not fire yesterday's as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from . import clock
from .guards import normalise

# A daily schedule may fire at most this many times. This is the user's own
# ceiling ("two or three times a day"), kept here as a rail rather than only in
# the UI so nothing can write a rule the safety story was not designed for.
MAX_TIMES_PER_DAY = 3

# A day-of-week rule can skip at most six days, so eight covers every rule that
# can be expressed with room to spare.
SEARCH_DAYS = 8

# 0 == Monday, matching datetime.weekday().
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
EVERY_DAY: tuple[int, ...] = ()


class InvalidRecurrence(ValueError):
    """The rule as described cannot be scheduled."""


@dataclass(frozen=True)
class Recurrence:
    """When a schedule fires, in Israel local wall-clock terms.

    `times` are "HH:MM" strings rather than minute counts because that is what
    the user typed, what the UI shows, and what survives a daylight-saving
    change unchanged. `days` empty means every day.
    """

    times: tuple[str, ...] = ("09:00",)
    days: tuple[int, ...] = EVERY_DAY

    @property
    def per_day(self) -> int:
        return len(self.times)

    @property
    def is_daily(self) -> bool:
        return not self.days or len(self.days) == 7


def parse_hhmm(text: str) -> tuple[int, int]:
    """Read one "HH:MM" local time. Raises rather than guessing."""
    parts = str(text).strip().split(":")
    if len(parts) != 2:
        raise InvalidRecurrence(f"{text!r} is not a time of day (use HH:MM).")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise InvalidRecurrence(f"{text!r} is not a time of day (use HH:MM).") from exc
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise InvalidRecurrence(f"{text!r} is not a real time of day.")
    return hour, minute


def build(times: Sequence[str], days: Sequence[int] = ()) -> Recurrence:
    """Validate and normalise a rule: sorted, de-duplicated, within the rails."""
    cleaned = sorted({f"{h:02d}:{m:02d}" for h, m in (parse_hhmm(t) for t in times)})
    if not cleaned:
        raise InvalidRecurrence("A repeating post needs at least one time of day.")
    if len(cleaned) > MAX_TIMES_PER_DAY:
        raise InvalidRecurrence(
            f"At most {MAX_TIMES_PER_DAY} times a day. More than that stops "
            "looking like a person posting."
        )

    chosen = sorted({int(d) for d in days})
    if any(d < 0 or d > 6 for d in chosen):
        raise InvalidRecurrence("Days of the week run from 0 (Monday) to 6 (Sunday).")
    if len(chosen) == 7:
        chosen = []  # every day; store it as the simpler form
    return Recurrence(times=tuple(cleaned), days=tuple(chosen))


def next_occurrence(rule: Recurrence, after: datetime) -> datetime:
    """The first firing strictly after `after`, as UTC.

    Walked day by day in *local* time and converted back, so a rule saying
    09:00 keeps meaning 09:00 through a daylight-saving change instead of
    sliding to 08:00 or 10:00.
    """
    local = clock.to_local(after)
    for offset in range(SEARCH_DAYS):
        day = (local + timedelta(days=offset)).date()
        if rule.days and day.weekday() not in rule.days:
            continue
        for hour, minute in (parse_hhmm(t) for t in rule.times):
            moment = clock.parse_local(f"{day.isoformat()} {hour:02d}:{minute:02d}")
            if moment > after:
                return moment
    # Unreachable for any rule build() will produce; a loud failure beats
    # returning something plausible and posting at the wrong time.
    raise InvalidRecurrence(f"No occurrence of {describe(rule)} within {SEARCH_DAYS} days.")


@dataclass(frozen=True)
class DueVerdict:
    """What to do with a schedule whose moment has arrived."""

    fire: bool
    missed: bool
    next_run_at: datetime


def evaluate_due(
    rule: Recurrence,
    next_run_at: datetime | None,
    now: datetime,
    grace: timedelta,
) -> DueVerdict | None:
    """Judge one schedule against the wall clock. None means "not yet".

    A slot older than `grace` is reported as missed and skipped -- the machine
    was asleep, and firing a backlog the moment it wakes is precisely the burst
    of activity the whole schedule exists to avoid.
    """
    if next_run_at is None:
        return DueVerdict(fire=False, missed=False, next_run_at=next_occurrence(rule, now))
    if next_run_at > now:
        return None

    missed = now - next_run_at > grace
    return DueVerdict(
        fire=not missed,
        missed=missed,
        next_run_at=next_occurrence(rule, now),
    )


def pick_body(
    variants: Sequence[str], offset: int, recent_bodies: Sequence[str] = ()
) -> str | None:
    """Choose this run's wording for one group, or None if nothing is fresh.

    Starting at `offset` and wrapping is what keeps a run from sending one
    wording to every group at once: group 0 takes variant 0, group 1 takes
    variant 1, and the whole cycle shifts by one on the next run. Anything
    already posted to *this* group is skipped, because sending it again is
    exactly what `guards.check_repeat_text` refuses at the manual door and what
    gets accounts restricted.
    """
    usable = [v for v in variants if v.strip()]
    if not usable:
        return None

    seen = {normalise(body) for body in recent_bodies}
    for step in range(len(usable)):
        candidate = usable[(offset + step) % len(usable)]
        if normalise(candidate) not in seen:
            return candidate
    return None


def identical_reach(group_count: int, variant_count: int) -> int:
    """How many groups the busiest single wording would reach in one run.

    Feeds the same warning threshold as `guards.variation_warning`, but before
    the schedule exists rather than after it has fired.
    """
    if group_count <= 0:
        return 0
    if variant_count <= 0:
        return group_count
    return -(-group_count // variant_count)  # ceil


def describe(rule: Recurrence) -> str:
    """One line for the UI: "Every day at 09:00 and 18:00"."""
    times = list(rule.times)
    if len(times) == 1:
        when = times[0]
    else:
        when = ", ".join(times[:-1]) + f" and {times[-1]}"

    if rule.is_daily:
        days = "Every day"
    elif len(rule.days) == 1:
        days = f"Every {DAY_NAMES[rule.days[0]]}"
    else:
        days = ", ".join(DAY_NAMES[d] for d in rule.days)
    return f"{days} at {when}"


@dataclass(frozen=True)
class RulePreview:
    """What creating this schedule would actually mean, before it is created."""

    summary: str
    next_runs: tuple[datetime, ...] = ()
    warnings: tuple[str, ...] = ()


def preview(
    rule: Recurrence,
    now: datetime,
    *,
    group_count: int = 0,
    variant_count: int = 0,
    cooldown_hours: int = 8,
    window_start_hour: int = 8,
    window_end_hour: int = 23,
    ahead: int = 3,
) -> RulePreview:
    """Describe a rule and flag the ways it will disappoint.

    Every warning here is something the schedule would otherwise hit silently at
    three in the morning: a slot outside the posting window that gets deferred,
    a frequency the per-group cooldown will refuse most of, or too few wordings
    to keep the content varied. None of them block -- the guards downstream
    still do the actual refusing -- but being told now beats finding out from
    the queue a week later.
    """
    warnings: list[str] = []

    moment = now
    runs: list[datetime] = []
    for _ in range(max(0, ahead)):
        moment = next_occurrence(rule, moment)
        runs.append(moment)

    if window_start_hour != window_end_hour:
        outside = [
            t for t in rule.times
            if not (window_start_hour <= parse_hhmm(t)[0] < window_end_hour)
        ]
        if outside:
            warnings.append(
                f"{', '.join(outside)} is outside the "
                f"{window_start_hour:02d}:00-{window_end_hour:02d}:00 posting window, "
                "so those runs will wait until it reopens."
            )

    if cooldown_hours > 0:
        gap_hours = 24 / rule.per_day if rule.is_daily else 24 * 7 / (rule.per_day * max(1, len(rule.days)))
        if gap_hours < cooldown_hours:
            warnings.append(
                f"That is roughly one post per group every {gap_hours:.0f}h, inside "
                f"the {cooldown_hours}h cooldown. Most runs will be skipped unless "
                "you lower the cooldown or spread the groups out."
            )

    if variant_count and group_count:
        reach = identical_reach(group_count, variant_count)
        if reach > 2:
            warnings.append(
                f"With {variant_count} wording{'s' if variant_count != 1 else ''} across "
                f"{group_count} groups, the same text reaches {reach} of them in one run. "
                "Repetitive content is the main thing that gets accounts restricted."
            )

    return RulePreview(
        summary=describe(rule), next_runs=tuple(runs), warnings=tuple(warnings)
    )
