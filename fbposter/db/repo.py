"""Query layer.

One repository per table group. Repositories return model objects and never
make policy decisions -- the safety rules live in fbposter/guards.py as pure
functions, so they can be tested without a database.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Sequence

from ..errors import DuplicateGroup, InvalidGroupURL
from ..groups import parse_group_url
from .connection import Database
from .models import (
    TARGET_DONE,
    TARGET_PENDING,
    TARGET_RUNNING,
    TASK_CANCELLED,
    TASK_PENDING,
    TASK_RUNNING,
    Group,
    Task,
    TaskTarget,
    Template,
    to_iso,
    utcnow,
)


class SettingsRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def get(self, key: str, default: str = "") -> str:
        row = self.db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row is not None else default

    def get_int(self, key: str, default: int) -> int:
        try:
            return int(self.get(key, str(default)))
        except (TypeError, ValueError):
            return default

    def set(self, key: str, value: str | int) -> None:
        self.db.write(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def all(self) -> dict[str, str]:
        return {row["key"]: row["value"] for row in self.db.query("SELECT key, value FROM settings")}


class GroupRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self, include_archived: bool = False) -> list[Group]:
        sql = "SELECT * FROM groups"
        if not include_archived:
            sql += " WHERE archived = 0"
        sql += " ORDER BY id"
        return [Group.from_row(row) for row in self.db.query(sql)]

    def get(self, group_id: int) -> Group | None:
        row = self.db.query_one("SELECT * FROM groups WHERE id = ?", (group_id,))
        return Group.from_row(row) if row else None

    def get_by_identifier(self, identifier: str) -> Group | None:
        row = self.db.query_one("SELECT * FROM groups WHERE identifier = ?", (identifier,))
        return Group.from_row(row) if row else None

    def add_from_url(self, raw: str, name: str = "", cooldown_hours: int | None = None) -> Group:
        """Store a group from a pasted URL.

        The URL is canonicalised first, so the same group pasted as a mobile
        link, with a /posts/ suffix, or with tracking parameters all collapse to
        one row -- which is what stops it being posted to twice in a batch.
        """
        ref = parse_group_url(raw)
        if ref is None:
            raise InvalidGroupURL(f"Not a Facebook group URL: {raw!r}")

        if self.get_by_identifier(ref.identifier) is not None:
            raise DuplicateGroup(f"Group {ref.identifier} is already in the list.")

        default_cooldown = SettingsRepo(self.db).get_int("default_cooldown_hours", 24)
        try:
            group_id = self.db.write(
                "INSERT INTO groups (identifier, url, name, cooldown_hours, notes, created_at) "
                "VALUES (?, ?, ?, ?, '', ?)",
                (
                    ref.identifier,
                    ref.url,
                    name,
                    cooldown_hours if cooldown_hours is not None else default_cooldown,
                    to_iso(utcnow()),
                ),
            )
        except sqlite3.IntegrityError as exc:  # lost a race against another writer
            raise DuplicateGroup(f"Group {ref.identifier} is already in the list.") from exc

        stored = self.get(group_id)
        assert stored is not None
        return stored

    def remove(self, group_id: int) -> None:
        self.db.write("DELETE FROM groups WHERE id = ?", (group_id,))

    def set_name(self, group_id: int, name: str) -> None:
        """Store the human-readable name read off the group's page."""
        self.db.write("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))

    def missing_names(self) -> list[Group]:
        """Groups still showing an identifier because no name was ever fetched."""
        rows = self.db.query(
            "SELECT * FROM groups WHERE archived = 0 AND TRIM(name) = '' ORDER BY id"
        )
        return [Group.from_row(row) for row in rows]

    def set_cooldown(self, group_id: int, hours: int) -> None:
        self.db.write("UPDATE groups SET cooldown_hours = ? WHERE id = ?", (hours, group_id))

    def mark_posted(self, group_id: int, when: datetime | None = None) -> None:
        self.db.write(
            "UPDATE groups SET last_posted_at = ? WHERE id = ?",
            (to_iso(when or utcnow()), group_id),
        )

    def recent_bodies(self, group_id: int, limit: int = 20) -> list[str]:
        """Text already posted to this group, newest first.

        Feeds the repeated-text guard: posting to an active group often is fine,
        posting the same words to it twice is the thing that gets accounts
        restricted.
        """
        rows = self.db.query(
            "SELECT body FROM task_targets WHERE group_id = ? AND state = ? "
            "ORDER BY posted_at DESC LIMIT ?",
            (group_id, TARGET_DONE, limit),
        )
        return [row["body"] for row in rows]


class TemplateRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list(self) -> list[Template]:
        return [
            Template.from_row(row)
            for row in self.db.query("SELECT * FROM templates ORDER BY name COLLATE NOCASE")
        ]

    def get_by_name(self, name: str) -> Template | None:
        row = self.db.query_one("SELECT * FROM templates WHERE name = ?", (name,))
        return Template.from_row(row) if row else None

    def save(self, name: str, body: str, media_paths: Sequence[str] = ()) -> Template:
        """Create or overwrite a template by name."""
        import json

        now = to_iso(utcnow())
        self.db.write(
            "INSERT INTO templates (name, body, media_paths, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET "
            "  body = excluded.body, "
            "  media_paths = excluded.media_paths, "
            "  updated_at = excluded.updated_at",
            (name, body, json.dumps([str(p) for p in media_paths]), now, now),
        )
        stored = self.get_by_name(name)
        assert stored is not None
        return stored

    def delete(self, template_id: int) -> None:
        self.db.write("DELETE FROM templates WHERE id = ?", (template_id,))


class TaskRepo:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        body: str,
        targets: Sequence[tuple[int, str]],
        media_paths: Sequence[str] = (),
        scheduled_for: datetime | None = None,
    ) -> Task:
        """Create a batch and its per-group targets atomically.

        `targets` is (group_id, body-for-that-group). A half-written batch --
        a task row with no targets, or targets without a task -- would leave the
        worker with an impossible queue, so both go in one transaction.
        """
        import json

        if not targets:
            raise ValueError("A task needs at least one target group.")

        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks (body, media_paths, scheduled_for, state, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    body,
                    json.dumps([str(p) for p in media_paths]),
                    to_iso(scheduled_for),
                    TASK_PENDING,
                    to_iso(utcnow()),
                ),
            )
            task_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO task_targets (task_id, group_id, position, body) VALUES (?, ?, ?, ?)",
                [
                    (task_id, group_id, position, target_body)
                    for position, (group_id, target_body) in enumerate(targets)
                ],
            )

        stored = self.get(task_id)
        assert stored is not None
        return stored

    def get(self, task_id: int) -> Task | None:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return Task.from_row(row) if row else None

    def list_recent(self, limit: int = 50) -> list[Task]:
        return [
            Task.from_row(row)
            for row in self.db.query(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            )
        ]

    def targets_for(self, task_id: int) -> list[TaskTarget]:
        rows = self.db.query(
            "SELECT t.*, g.identifier AS group_identifier, g.name AS group_name "
            "FROM task_targets t JOIN groups g ON g.id = t.group_id "
            "WHERE t.task_id = ? ORDER BY t.position",
            (task_id,),
        )
        return [TaskTarget.from_row(row) for row in rows]

    def cancel(self, task_id: int) -> None:
        self.db.write(
            "UPDATE tasks SET state = ?, finished_at = ? WHERE id = ?",
            (TASK_CANCELLED, to_iso(utcnow()), task_id),
        )

    # -- worker queries ----------------------------------------------------
    def claimable(self, now: datetime) -> list[Task]:
        """Tasks the worker may act on now.

        A batch already under way sorts ahead of everything else, so batches
        never interleave -- a "post now" raised mid-batch waits its turn rather
        than running alongside.
        """
        stamp = to_iso(now)
        rows = self.db.query(
            "SELECT * FROM tasks WHERE state IN (?, ?) "
            "  AND (scheduled_for IS NULL OR scheduled_for <= ?) "
            "  AND (resume_at IS NULL OR resume_at <= ?) "
            "ORDER BY CASE WHEN state = ? THEN 0 ELSE 1 END, "
            "         COALESCE(started_at, scheduled_for, created_at), id",
            (TASK_PENDING, TASK_RUNNING, stamp, stamp, TASK_RUNNING),
        )
        return [Task.from_row(row) for row in rows]

    def active_batches(self) -> int:
        """Batches under way. Drives the keep-awake request."""
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE state = ?", (TASK_RUNNING,)
        )
        return int(row["n"]) if row else 0

    def next_pending_target(self, task_id: int) -> TaskTarget | None:
        row = self.db.query_one(
            "SELECT t.*, g.identifier AS group_identifier, g.name AS group_name "
            "FROM task_targets t JOIN groups g ON g.id = t.group_id "
            "WHERE t.task_id = ? AND t.state = ? ORDER BY t.position LIMIT 1",
            (task_id, TARGET_PENDING),
        )
        return TaskTarget.from_row(row) if row else None

    def running_targets(self) -> list[TaskTarget]:
        """Targets left mid-flight by a crash.

        Whether these actually posted is unknown, which is why the worker
        verifies rather than assuming either way.
        """
        rows = self.db.query(
            "SELECT t.*, g.identifier AS group_identifier, g.name AS group_name "
            "FROM task_targets t JOIN groups g ON g.id = t.group_id "
            "WHERE t.state = ? ORDER BY t.id",
            (TARGET_RUNNING,),
        )
        return [TaskTarget.from_row(row) for row in rows]

    def set_resume_at(self, task_id: int, when: datetime | None) -> None:
        self.db.write(
            "UPDATE tasks SET resume_at = ? WHERE id = ?", (to_iso(when), task_id)
        )

    def mark_task(
        self,
        task_id: int,
        state: str,
        *,
        error: str = "",
        started: bool = False,
        finished: bool = False,
    ) -> None:
        sets = ["state = ?", "error = ?"]
        params: list[Any] = [state, error]
        if started:
            sets.append("started_at = COALESCE(started_at, ?)")
            params.append(to_iso(utcnow()))
        if finished:
            sets.append("finished_at = ?")
            params.append(to_iso(utcnow()))
        params.append(task_id)
        self.db.write(f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?", tuple(params))

    def mark_target(
        self,
        target_id: int,
        state: str,
        *,
        error: str = "",
        attempted: bool = False,
        posted: bool = False,
        post_url: str = "",
    ) -> None:
        """Record one group's outcome.

        Committed the moment it happens: that is what lets a crash resume a
        batch rather than start it again.
        """
        sets = ["state = ?", "error = ?"]
        params: list[Any] = [state, error]
        if attempted:
            sets.append("attempted_at = ?")
            params.append(to_iso(utcnow()))
        if posted:
            sets.append("posted_at = ?")
            params.append(to_iso(utcnow()))
        if post_url:
            sets.append("post_url = ?")
            params.append(post_url)
        params.append(target_id)
        self.db.write(
            f"UPDATE task_targets SET {', '.join(sets)} WHERE id = ?", tuple(params)
        )

    def remaining_targets(self, task_id: int) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM task_targets WHERE task_id = ? AND state = ?",
            (task_id, TARGET_PENDING),
        )
        return int(row["n"]) if row else 0

    def posted_count_since(self, since: datetime) -> int:
        """Group-posts completed since a moment. Feeds the daily cap."""
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM task_targets WHERE state = ? AND posted_at >= ?",
            (TARGET_DONE, to_iso(since)),
        )
        return int(row["n"]) if row else 0
