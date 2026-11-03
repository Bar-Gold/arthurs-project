"""How fast posts go out, and the client's quiet hours. Pure: no database,
no Qt, no browser.

Both are the client's own choice on the Settings screen (the user's request,
2026-11-03): clients post several listings a day to 80+ groups, and a fixed
2-4 minutes between groups ran all day and all night. They are read off the
settings on every tick, so a change reaches posts already queued, and a batch
half-way through, at once.

The keys are not seeded (the migrations seed from `schema.DEFAULT_SETTINGS`,
which must not change), so every read carries its own default.
"""

from __future__ import annotations

from . import clock

SPEED_KEY = "post_speed"
# Up to 10 groups per post through Facebook's own "Add groups" (2026-11-03).
# On unless the client turns it off: "1" or "0".
TOGETHER_KEY = "post_together"
TOGETHER_DEFAULT = "1"
QUIET_ON_KEY = "quiet_hours_on"
QUIET_START_KEY = "quiet_hours_start"
QUIET_END_KEY = "quiet_hours_end"

# The wait between one post and the next, in seconds: picked at random in the
# range each time. Safe is the 2-4 minutes the app always used, and the default,
# so nothing changes for a client until they choose.
SPEEDS: dict[str, tuple[str, tuple[float, float]]] = {
    "safe": ("Safe", (120, 240)),
    "normal": ("Normal", (60, 120)),
    "fast": ("Fast", (30, 90)),
}
DEFAULT_SPEED = "safe"

# Off until the client turns them on (the user's choice). These are only what
# the hour lists show the first time.
DEFAULT_QUIET_START = 23
DEFAULT_QUIET_END = 8


def speed(value: str) -> str:
    """A stored speed, or the default for anything unknown."""
    return value if value in SPEEDS else DEFAULT_SPEED


def gap_span(value: str) -> tuple[float, float]:
    """The range the wait between posts is drawn from."""
    return SPEEDS[speed(value)][1]


def describe_span(value: str) -> str:
    """'2-4 min', '30-90 sec': how the Settings screen names a speed."""
    low, high = gap_span(value)
    if low >= 60:
        return f"{low / 60:g}-{high / 60:g} min"
    return f"{low:g}-{high:g} sec"


def is_quiet(on: bool, start: int, end: int, local_hour: int) -> bool:
    """Whether an hour falls in the quiet hours.

    The same hour for From and To is no quiet hours at all, never all day:
    `clock.inside_window` reads it as "no restriction", which for quiet hours
    would silently stop every post.
    """
    if not on:
        return False
    start = clock.sane_hour(start, DEFAULT_QUIET_START)
    end = clock.sane_hour(end, DEFAULT_QUIET_END)
    if start == end:
        return False
    return clock.inside_window(local_hour, start, end)


def describe_quiet(start: int, end: int) -> str:
    """'23:00-08:00'."""
    start = clock.sane_hour(start, DEFAULT_QUIET_START)
    end = clock.sane_hour(end, DEFAULT_QUIET_END)
    return f"{start:02d}:00-{end:02d}:00"
