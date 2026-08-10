"""Time handling.

Everything is *stored* in UTC, but every human judgement is made in Israel
time: the posting window, what counts as "today" for the daily cap, and every
timestamp shown in the UI. That is the clock the user and the groups' members
are actually on.

This module exists because reading `.hour` straight off a UTC timestamp put the
posting window three hours out -- it would have allowed a post at 01:00 local
and refused one at 09:00.

Windows ships no IANA time zone database, so the `tzdata` package is a real
dependency here, not an optional one.
"""

from __future__ import annotations

from datetime import datetime, time, timezone, tzinfo
from functools import lru_cache

POSTING_TIMEZONE = "Asia/Jerusalem"

# Local wall-clock format used in the UI and accepted from the user.
LOCAL_FORMAT = "%Y-%m-%d %H:%M"


@lru_cache(maxsize=1)
def posting_zone() -> tzinfo | None:
    """The zone all human-facing times are expressed in.

    Returns None if the tz database is missing, in which case callers fall back
    to the machine's own zone. That is right often enough to be a sane
    fallback -- the machine is in Israel -- but it is a fallback, not the plan.
    """
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(POSTING_TIMEZONE)
    except Exception:
        return None


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_local(moment: datetime) -> datetime:
    """Convert any aware datetime into posting-local time."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    zone = posting_zone()
    return moment.astimezone(zone) if zone else moment.astimezone()


def local_now() -> datetime:
    return to_local(utcnow())


def local_hour(moment: datetime) -> int:
    """The hour a human would call it. What the posting window is judged on."""
    return to_local(moment).hour


def start_of_local_day(moment: datetime | None = None) -> datetime:
    """Midnight of the local day, returned in UTC.

    The daily cap counts a human "today", not a UTC one: a post at 01:00 on
    Tuesday belongs to Tuesday, even though UTC still calls it Monday.
    """
    local = to_local(moment or utcnow())
    midnight = datetime.combine(local.date(), time.min, tzinfo=local.tzinfo)
    return midnight.astimezone(timezone.utc)


def parse_local(text: str, fmt: str = LOCAL_FORMAT) -> datetime:
    """Read a local wall-clock string the user typed and return it as UTC."""
    naive = datetime.strptime(text.strip(), fmt)
    zone = posting_zone()
    aware = naive.replace(tzinfo=zone) if zone else naive.astimezone()
    return aware.astimezone(timezone.utc)


def format_local(moment: datetime | None, fmt: str = LOCAL_FORMAT) -> str:
    if moment is None:
        return ""
    return to_local(moment).strftime(fmt)
