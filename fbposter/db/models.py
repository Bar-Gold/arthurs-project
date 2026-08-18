"""Row objects.

Plain frozen dataclasses rather than an ORM: the schema is five tables and the
queries are simple, so an ORM would add a dependency and a layer of indirection
for nothing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# States a whole batch can be in.
TASK_PENDING = "pending"
TASK_RUNNING = "running"
TASK_DONE = "done"
TASK_FAILED = "failed"
TASK_CANCELLED = "cancelled"
TASK_HALTED = "halted"
# Its scheduled moment passed while nothing was running -- typically the
# machine was asleep. Reported, never fired late in a burst.
TASK_MISSED = "missed"

# States one group within a batch can be in.
TARGET_PENDING = "pending"
TARGET_RUNNING = "running"
TARGET_DONE = "done"
TARGET_FAILED = "failed"
TARGET_SKIPPED = "skipped"
# Submitted to a group that holds posts for admin approval. Counts as posted
# for every safety purpose -- cooldown, daily cap, repeated text -- because the
# words have left the building even though they are not on screen yet.
TARGET_AWAITING_APPROVAL = "awaiting_approval"
# An admin turned it down, so nothing was ever published. Excluded from
# recent_bodies on purpose: the wording never appeared in the group, so
# refusing to let the user send it again would be punishing them for a post
# that does not exist.
TARGET_DECLINED = "declined"

# States a repeating schedule can be in. It is either firing or it is not;
# a finished schedule is a deleted one.
SCHEDULE_ACTIVE = "active"
SCHEDULE_PAUSED = "paused"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(moment: datetime | None) -> str | None:
    """Store timestamps as ISO-8601 UTC so string ordering matches time order."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def from_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _paths_to_json(paths: Sequence[str]) -> str:
    return json.dumps([str(p) for p in paths])


def _paths_from_json(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def _ints_from_json(raw: str | None) -> list[int]:
    values = []
    for item in _paths_from_json(raw):
        try:
            values.append(int(item))
        except (TypeError, ValueError):
            continue
    return values


@dataclass(frozen=True)
class Group:
    id: int
    identifier: str
    url: str
    name: str = ""
    cooldown_hours: int = 8
    last_posted_at: datetime | None = None
    notes: str = ""
    archived: bool = False
    created_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Group":
        return cls(
            id=row["id"],
            identifier=row["identifier"],
            url=row["url"],
            name=row["name"] or "",
            cooldown_hours=row["cooldown_hours"],
            last_posted_at=from_iso(row["last_posted_at"]),
            notes=row["notes"] or "",
            archived=bool(row["archived"]),
            created_at=from_iso(row["created_at"]),
        )

    @property
    def display_name(self) -> str:
        return self.name or self.identifier


@dataclass(frozen=True)
class Template:
    id: int
    name: str
    body: str
    media_paths: list[str] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Template":
        return cls(
            id=row["id"],
            name=row["name"],
            body=row["body"],
            media_paths=_paths_from_json(row["media_paths"]),
            created_at=from_iso(row["created_at"]),
            updated_at=from_iso(row["updated_at"]),
        )


@dataclass(frozen=True)
class Task:
    id: int
    body: str
    media_paths: list[str] = field(default_factory=list)
    scheduled_for: datetime | None = None
    state: str = TASK_PENDING
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    # The instant the worker may next act on this task: the inter-group gap,
    # or a deferral until the posting window reopens.
    resume_at: datetime | None = None
    # Set when a repeating schedule materialised this batch, so the queue can
    # say where it came from.
    schedule_id: int | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Task":
        keys = row.keys()
        return cls(
            id=row["id"],
            body=row["body"],
            media_paths=_paths_from_json(row["media_paths"]),
            scheduled_for=from_iso(row["scheduled_for"]),
            state=row["state"],
            created_at=from_iso(row["created_at"]),
            started_at=from_iso(row["started_at"]),
            finished_at=from_iso(row["finished_at"]),
            error=row["error"] or "",
            resume_at=from_iso(row["resume_at"]) if "resume_at" in keys else None,
            schedule_id=row["schedule_id"] if "schedule_id" in keys else None,
        )

    @property
    def is_immediate(self) -> bool:
        """No scheduled time means post as soon as the worker gets to it."""
        return self.scheduled_for is None


@dataclass(frozen=True)
class TaskTarget:
    id: int
    task_id: int
    group_id: int
    position: int
    body: str
    state: str = TARGET_PENDING
    attempted_at: datetime | None = None
    posted_at: datetime | None = None
    post_url: str = ""
    error: str = ""
    group_identifier: str = ""
    group_name: str = ""
    # Consecutive follow-up checks that could not find an awaiting post.
    resolve_misses: int = 0

    @property
    def group_label(self) -> str:
        """What to show a human: the group's name, or its id if we lack one."""
        return self.group_name or self.group_identifier

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "TaskTarget":
        keys = row.keys()
        return cls(
            id=row["id"],
            task_id=row["task_id"],
            group_id=row["group_id"],
            position=row["position"],
            body=row["body"],
            state=row["state"],
            attempted_at=from_iso(row["attempted_at"]),
            posted_at=from_iso(row["posted_at"]),
            post_url=row["post_url"] or "",
            error=row["error"] or "",
            # Present only when the query joined groups.
            group_identifier=(row["group_identifier"] if "group_identifier" in keys else "") or "",
            group_name=(row["group_name"] if "group_name" in keys else "") or "",
            resolve_misses=(
                row["resolve_misses"] if "resolve_misses" in keys else 0
            ) or 0,
        )


@dataclass(frozen=True)
class Schedule:
    """A repeating post: several wordings, some groups, and when to fire.

    `bodies` is plural on purpose. One wording would be refused by
    `guards.check_repeat_text` the second time it reached a group, so a
    schedule that could only hold one would work exactly once.
    """

    id: int
    name: str = ""
    bodies: list[str] = field(default_factory=list)
    media_paths: list[str] = field(default_factory=list)
    times: list[str] = field(default_factory=list)
    days: list[int] = field(default_factory=list)
    state: str = SCHEDULE_ACTIVE
    run_count: int = 0
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    created_at: datetime | None = None
    # Filled in by the repository from schedule_targets, in position order.
    group_ids: list[int] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.state == SCHEDULE_ACTIVE

    @property
    def display_name(self) -> str:
        return self.name or f"Schedule {self.id}"

    @classmethod
    def from_row(cls, row: Mapping[str, Any], group_ids: Sequence[int] = ()) -> "Schedule":
        return cls(
            id=row["id"],
            name=row["name"] or "",
            bodies=_paths_from_json(row["bodies"]),
            media_paths=_paths_from_json(row["media_paths"]),
            times=_paths_from_json(row["times"]),
            days=_ints_from_json(row["days"]),
            state=row["state"],
            run_count=row["run_count"],
            next_run_at=from_iso(row["next_run_at"]),
            last_run_at=from_iso(row["last_run_at"]),
            created_at=from_iso(row["created_at"]),
            group_ids=list(group_ids),
        )
