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


@dataclass(frozen=True)
class Group:
    id: int
    identifier: str
    url: str
    name: str = ""
    cooldown_hours: int = 24
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
        )
