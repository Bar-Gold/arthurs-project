"""Repeating schedules, as pure functions.

A recurrence is a *definition*: "these wordings, to these groups, at 09:00 and
18:00 every day". It is not a queue entry. Each time one comes due the worker
materialises an ordinary `tasks` row from it, so everything downstream -- the
serial worker, the gap between posts, crash recovery -- keeps working exactly
as it already did, with no second code path.

Nothing here touches the database or the clock: callers pass `now` in and get a
judgement back, the same arrangement as `stats.py`. That is what makes a
year's worth of firings testable in milliseconds.

There are no posting rules here any more (the user's choice, 2026-10-18): as
many times a day as the user picks, any number of wordings, pictures alone.

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
from .text import normalise

# A day-of-week rule can skip at most six days, so eight covers every rule that
# can be expressed with room to spare.
SEARCH_DAYS = 8

# 0 == Monday, matching datetime.weekday(). That is how a schedule stores its
# days, and it must not change: stored rules already use it.
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
# The order days are shown in: Sunday to Saturday, as the week runs in Israel
# (the user's choice, 2026-10-14).
WEEK_ORDER = (6, 0, 1, 2, 3, 4, 5)
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
    """Validate and normalise a rule: sorted and de-duplicated."""
    cleaned = sorted({f"{h:02d}:{m:02d}" for h, m in (parse_hhmm(t) for t in times)})
    if not cleaned:
        raise InvalidRecurrence("A repeating post needs at least one time of day.")

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
    variants: Sequence[str],
    offset: int,
    recent_bodies: Sequence[str] = (),
) -> str:
    """Choose this run's wording for one group, or "" when there is none.

    Starting at `offset` and wrapping is what keeps a run from sending one
    wording to every group at once: group 0 takes variant 0, group 1 takes
    variant 1, and the whole cycle shifts by one on the next run. A wording
    this group has not had today (`recent_bodies`) is preferred, and when it
    has had them all the rotation carries on regardless: nothing holds a
    repeat back (the user's choice, 2026-10-18).

    "" is a post of pictures or a video alone.
    """
    usable = [v for v in variants if v.strip()]
    if not usable:
        return ""

    seen = {normalise(body) for body in recent_bodies}
    for step in range(len(usable)):
        candidate = usable[(offset + step) % len(usable)]
        if normalise(candidate) not in seen:
            return candidate
    return usable[offset % len(usable)]


@dataclass(frozen=True)
class PostShape:
    """Everything that makes two repeating posts the same post: what goes
    out, where, and when. The name is left out on purpose, and so is the
    order of the wordings and groups, which only changes who gets which
    wording first."""

    wordings: frozenset[str]
    media: tuple[tuple[str, int | None], ...]
    times: tuple[str, ...]
    days: tuple[int, ...]
    groups: frozenset[int]


def shape(
    bodies: Sequence[str],
    media: Sequence[tuple[str, int | None]],
    times: Sequence[str],
    days: Sequence[int],
    groups: Sequence[int],
) -> PostShape:
    """`media` is each attachment's `attachments.identity`. Raises
    InvalidRecurrence for times or days that cannot be scheduled."""
    rule = build(times, days)
    return PostShape(
        wordings=frozenset(normalise(body) for body in bodies if body.strip()),
        media=tuple(sorted(media, key=lambda item: (item[0], item[1] or -1))),
        times=rule.times,
        days=rule.days,
        groups=frozenset(groups),
    )


def find_copy(candidate: PostShape, existing: Sequence[tuple[object, PostShape]]):
    """The first existing post that is an exact copy of `candidate`, or None.

    Only an exact copy (the user's choice, 2026-10-20): two of those send
    every group the same post twice at every slot. A client set up the same
    repeating post twice, 31 seconds apart. The same post at another time, on
    other days, to other groups, or with other words or pictures is
    legitimate and goes through.
    """
    for item, other in existing:
        if other == candidate:
            return item
    return None


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
        ordered = sorted(rule.days, key=WEEK_ORDER.index)
        days = ", ".join(DAY_NAMES[d] for d in ordered)
    return f"{days} at {when}"


@dataclass(frozen=True)
class RulePreview:
    """What creating this schedule would mean, before it is created."""

    summary: str
    next_runs: tuple[datetime, ...] = ()


def preview(rule: Recurrence, now: datetime, *, ahead: int = 3) -> RulePreview:
    """Describe a rule and list its next few runs."""
    moment = now
    runs: list[datetime] = []
    for _ in range(max(0, ahead)):
        moment = next_occurrence(rule, moment)
        runs.append(moment)
    return RulePreview(summary=describe(rule), next_runs=tuple(runs))
