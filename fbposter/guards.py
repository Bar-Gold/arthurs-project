"""The safety rules, as pure functions.

These decide whether a batch may be queued. Nothing here touches the database
or the browser: callers gather the numbers, these functions judge them. That
keeps the entire safety table testable without fixtures, which matters because
these rules are the difference between an account surviving and not.

The rules come from README section 7, and the thresholds were confirmed with
the user: 25 group-posts a day, an 08:00-23:00 window, and a per-group cooldown
defaulting to 24 hours.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from . import clock

# Sending byte-identical text to more than this many groups earns a warning.
IDENTICAL_TEXT_WARN_THRESHOLD = 2


@dataclass(frozen=True)
class Violation:
    rule: str
    message: str


@dataclass(frozen=True)
class PlannedTarget:
    """One group about to be posted to, with the history needed to judge it."""

    group_id: int
    group_name: str
    body: str
    last_posted_at: datetime | None = None
    cooldown_hours: int = 24
    recent_bodies: tuple[str, ...] = ()


@dataclass(frozen=True)
class BatchVerdict:
    blocked: tuple[Violation, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def allowed(self) -> bool:
        return not self.blocked


def normalise(text: str) -> str:
    """Fold text for comparison.

    Deliberately stricter than the "byte-identical" wording in the README: a
    trailing newline or a changed capital is not content variation, and letting
    it defeat the check would make the guard worse than useless by giving false
    reassurance.
    """
    return " ".join(text.split()).casefold()


def check_daily_cap(posted_today: int, adding: int, cap: int) -> Violation | None:
    """Refuse to exceed the daily ceiling. Landing exactly on it is fine."""
    if cap <= 0:
        return None
    if posted_today + adding > cap:
        return Violation(
            "daily_cap",
            f"That would make {posted_today + adding} posts today, over the cap of {cap}.",
        )
    return None


def check_cooldown(
    last_posted_at: datetime | None,
    now: datetime,
    cooldown_hours: int,
    group_name: str = "this group",
) -> Violation | None:
    """Enforce the per-group gap. Exactly at the boundary is allowed."""
    if last_posted_at is None or cooldown_hours <= 0:
        return None

    ready_at = last_posted_at + timedelta(hours=cooldown_hours)
    if now < ready_at:
        remaining = ready_at - now
        hours = remaining.total_seconds() / 3600
        return Violation(
            "cooldown",
            f"{group_name} was posted to too recently; {hours:.1f}h of its "
            f"{cooldown_hours}h cooldown left.",
        )
    return None


def check_posting_window(when: datetime, start_hour: int, end_hour: int) -> Violation | None:
    """Keep activity inside waking hours, judged in Israel time.

    The hour is taken from the local clock, never from the stored UTC value.
    Reading it off UTC put the window three hours out: it allowed 01:00 local
    and refused 09:00. Posting at 4am is a strong automation signal, so this
    has to be right.

    end_hour is exclusive, so 08:00-23:00 means the last post may start at
    22:59 local.
    """
    if start_hour == end_hour:
        return None

    local = clock.to_local(when)
    if start_hour <= local.hour < end_hour:
        return None
    return Violation(
        "posting_window",
        f"{local.strftime('%H:%M')} Israel time is outside the "
        f"{start_hour:02d}:00-{end_hour:02d}:00 posting window.",
    )


def check_repeat_text(
    body: str,
    recent_bodies: Sequence[str],
    group_name: str = "this group",
) -> Violation | None:
    """Never send the same words to the same group twice.

    This is the rule that does the real work. Meta restricts accounts at *low*
    posting frequencies when the content is repetitive, so posting often to an
    active group is safe only while the wording changes.
    """
    if not body.strip():
        return None

    target = normalise(body)
    if any(normalise(previous) == target for previous in recent_bodies):
        return Violation(
            "repeat_text",
            f"This exact text has already been posted to {group_name}. Reword it first.",
        )
    return None


def variation_warning(bodies: Sequence[str]) -> str | None:
    """Warn when one wording is going to too many groups in a single batch."""
    counts: dict[str, int] = {}
    for body in bodies:
        if body.strip():
            key = normalise(body)
            counts[key] = counts.get(key, 0) + 1

    worst = max(counts.values(), default=0)
    if worst > IDENTICAL_TEXT_WARN_THRESHOLD:
        return (
            f"The same text is going to {worst} groups. Repetitive content is the "
            "main thing that gets accounts restricted — vary the wording."
        )
    return None


def evaluate_batch(
    targets: Sequence[PlannedTarget],
    *,
    now: datetime,
    when: datetime | None = None,
    daily_cap: int = 25,
    posted_today: int = 0,
    window_start_hour: int = 8,
    window_end_hour: int = 23,
) -> BatchVerdict:
    """Judge a whole batch before anything is written to the database.

    `when` is the moment the batch would run -- the scheduled time, or now for
    an immediate post.
    """
    blocked: list[Violation] = []
    warnings: list[str] = []

    if not targets:
        return BatchVerdict(blocked=(Violation("empty", "Pick at least one group."),))

    cap = check_daily_cap(posted_today, len(targets), daily_cap)
    if cap is not None:
        blocked.append(cap)

    window = check_posting_window(when or now, window_start_hour, window_end_hour)
    if window is not None:
        blocked.append(window)

    for target in targets:
        cooldown = check_cooldown(
            target.last_posted_at, when or now, target.cooldown_hours, target.group_name
        )
        if cooldown is not None:
            blocked.append(cooldown)

        repeat = check_repeat_text(target.body, target.recent_bodies, target.group_name)
        if repeat is not None:
            blocked.append(repeat)

    variation = variation_warning([target.body for target in targets])
    if variation is not None:
        warnings.append(variation)

    return BatchVerdict(blocked=tuple(blocked), warnings=tuple(warnings))
