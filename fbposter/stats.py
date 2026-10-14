"""The Dashboard's numbers, as pure functions.

The same split as `guards.py` against `worker.py`: the repositories supply
rows, and everything worked out from them lives here, so every number on the
screen can be tested without a database, a window or the wall clock.

Two rules hold throughout:

* **"Sent" means what the daily limit counts** (`repo.SENT_STATES`: posted,
  waiting for an admin, or might have posted). "Posted today" and "3 of 25
  today" can then never disagree.
* **Days are Israel days**, judged on local calendar dates through `clock`,
  never by subtracting 24 hours: across a daylight-saving change a day is 23
  or 25 hours long, and "the last 7 days" is seven dates, not 168 hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Mapping, Sequence

from . import clock
from .db.models import (
    SCHEDULE_ACTIVE,
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_UNCONFIRMED,
    TASK_PENDING,
    TASK_RUNNING,
    PostRecord,
)
from .db.repo import SENT_STATES
from .guards import normalise

WEEK_DAYS = 7
MONTH_DAYS = 30
CHART_DAYS = 14

# Tried and went out, as far as the app could tell. Declined went out too: an
# admin turned it down afterwards.
WENT_OUT = (TARGET_DONE, TARGET_AWAITING_APPROVAL, TARGET_DECLINED)
# Tried and did not, or nobody can say. A skip is not here: that was the rules
# deciding not to try, which is the app working, not failing.
TRIED = WENT_OUT + (TARGET_FAILED, TARGET_UNCONFIRMED)

# {(group_id, state): (count, earliest)}, from `TaskRepo.archived_totals`.
Archived = Mapping[tuple[int, str], tuple[int, "datetime | None"]]


def local_day(moment: datetime) -> date:
    return clock.to_local(moment).date()


def _sent(records: Iterable[PostRecord]) -> list[PostRecord]:
    return [r for r in records if r.state in SENT_STATES and r.at is not None]


def _between(records: Iterable[PostRecord], first: date, last: date) -> list[PostRecord]:
    """Records whose Israel day is from `first` to `last`, both included."""
    return [r for r in records if r.at is not None and first <= local_day(r.at) <= last]


@dataclass(frozen=True)
class Summary:
    today: int
    daily_cap: int
    last_week: int
    week_before: int
    last_month: int
    all_time: int
    # When the first post on record went out; None before the first.
    since: datetime | None
    # 0..1 of what was tried in the last month; None when nothing was tried.
    success_rate: float | None
    tried: int
    wordings: int


def summary(
    records: Sequence[PostRecord],
    archived: Archived,
    now: datetime,
    daily_cap: int,
) -> Summary:
    today = local_day(now)
    sent = _sent(records)
    week_start = today - timedelta(days=WEEK_DAYS - 1)
    week = _between(sent, week_start, today)
    before = _between(
        sent, week_start - timedelta(days=WEEK_DAYS), week_start - timedelta(days=1)
    )
    month_start = today - timedelta(days=MONTH_DAYS - 1)

    kept = [(count, first) for (_g, state), (count, first) in archived.items()
            if state in SENT_STATES]
    starts = [r.at for r in sent] + [first for count, first in kept if count and first]

    tried = [r for r in _between(records, month_start, today) if r.state in TRIED]
    went = sum(1 for r in tried if r.state in WENT_OUT)

    return Summary(
        today=len(_between(sent, today, today)),
        daily_cap=daily_cap,
        last_week=len(week),
        week_before=len(before),
        last_month=len(_between(sent, month_start, today)),
        all_time=len(sent) + sum(count for count, _first in kept),
        since=min(starts) if starts else None,
        success_rate=(went / len(tried)) if tried else None,
        tried=len(tried),
        wordings=len({normalise(r.body) for r in week if normalise(r.body)}),
    )


def per_day(
    records: Sequence[PostRecord], now: datetime, days: int = CHART_DAYS
) -> list[tuple[date, int]]:
    """Posts sent on each of the last `days` Israel days, oldest first."""
    today = local_day(now)
    counts = {today - timedelta(days=back): 0 for back in range(days)}
    for record in _sent(records):
        day = local_day(record.at)
        if day in counts:
            counts[day] += 1
    return sorted(counts.items())


def outcomes(
    records: Sequence[PostRecord], now: datetime, days: int = MONTH_DAYS
) -> dict[str, int]:
    """How every turn of the last `days` Israel days ended, by state."""
    today = local_day(now)
    counted: dict[str, int] = {}
    for record in _between(records, today - timedelta(days=days - 1), today):
        counted[record.state] = counted.get(record.state, 0) + 1
    return counted


@dataclass(frozen=True)
class GroupRow:
    group_id: int
    name: str
    members: str
    today: int
    last_week: int
    all_time: int
    last_post_at: datetime | None
    # When the cooldown lets it be posted to again; None when it already can.
    free_at: datetime | None


def by_group(
    records: Sequence[PostRecord],
    archived: Archived,
    groups: Sequence,
    now: datetime,
) -> list[GroupRow]:
    """One row per group on the list, the busiest first.

    A removed group is not on the list, so it has no row here; what was sent
    to it still counts in the totals above.
    """
    today = local_day(now)
    week_start = today - timedelta(days=WEEK_DAYS - 1)
    sent = _sent(records)
    rows = []
    for group in groups:
        mine = [r for r in sent if r.group_id == group.id]
        kept = sum(
            count for (group_id, state), (count, _first) in archived.items()
            if group_id == group.id and state in SENT_STATES
        )
        last = max((r.at for r in mine), default=None) or group.last_posted_at
        free_at = None
        if group.last_posted_at is not None:
            ready = group.last_posted_at + timedelta(hours=group.cooldown_hours)
            free_at = ready if ready > now else None
        rows.append(
            GroupRow(
                group_id=group.id,
                name=group.display_name,
                members=group.members,
                today=len(_between(mine, today, today)),
                last_week=len(_between(mine, week_start, today)),
                all_time=len(mine) + kept,
                last_post_at=last,
                free_at=free_at,
            )
        )
    rows.sort(key=lambda row: (-row.all_time, -row.last_week, row.name.casefold()))
    return rows


# What the "Next scheduled" tile says.
NEXT_PAUSED = "paused"
NEXT_POSTING = "posting"
NEXT_DUE = "due"
NEXT_SCHEDULED = "scheduled"
NEXT_NONE = "none"


@dataclass(frozen=True)
class NextUp:
    kind: str
    at: datetime | None = None
    # A repeating post's name, or "" for a one-off post.
    name: str = ""


def next_scheduled(
    tasks: Sequence, schedules: Sequence, paused: bool, now: datetime
) -> NextUp:
    """The next thing due to go out, as stored. A fact, not a forecast.

    The worker may still hold it back (posting hours, the gap between posts,
    the daily limit); this says when it is set for, which is what the user set.
    """
    if paused:
        return NextUp(NEXT_PAUSED)
    for task in tasks:
        if task.state == TASK_RUNNING:
            # Between groups it waits 10-25 minutes, or overnight for posting
            # hours; `resume_at` is when its next group is due.
            later = task.resume_at if task.resume_at and task.resume_at > now else None
            return NextUp(NEXT_POSTING, later)
    names = {schedule.id: schedule.name for schedule in schedules}
    candidates: list[tuple[datetime, str]] = []
    for task in tasks:
        if task.state != TASK_PENDING:
            continue
        when = task.resume_at or task.scheduled_for
        if when is None or when <= now:
            return NextUp(NEXT_DUE, now, names.get(task.schedule_id, ""))
        candidates.append((when, names.get(task.schedule_id, "")))
    for schedule in schedules:
        if schedule.state == SCHEDULE_ACTIVE and schedule.next_run_at is not None:
            candidates.append((schedule.next_run_at, schedule.name))
    if not candidates:
        return NextUp(NEXT_NONE)
    when, name = min(candidates, key=lambda item: item[0])
    return NextUp(NEXT_SCHEDULED, when, name)


def when_text(moment: datetime, now: datetime) -> str:
    """"Today 18:00", "Tomorrow 09:00", or "Thu 16 Oct 09:00"."""
    day = local_day(moment)
    today = local_day(now)
    clock_time = clock.format_local(moment, "%H:%M")
    if day == today:
        return f"Today {clock_time}"
    if day == today + timedelta(days=1):
        return f"Tomorrow {clock_time}"
    if day == today - timedelta(days=1):
        return f"Yesterday {clock_time}"
    return clock.format_local(moment, "%a %d %b %H:%M")


def ago_text(moment: datetime | None, now: datetime) -> str:
    """"just now", "25m ago", "3h ago", "4d ago"; "never" for None."""
    if moment is None:
        return "never"
    seconds = max(0, int((now - moment).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"
