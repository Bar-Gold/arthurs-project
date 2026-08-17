"""Query layer.

One repository per table group. Repositories return model objects and never
make policy decisions -- the safety rules live in fbposter/guards.py as pure
functions, so they can be tested without a database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Sequence

from ..errors import DuplicateGroup, InvalidGroupURL
from ..groups import parse_group_url
from .connection import Database
from .models import (
    SCHEDULE_ACTIVE,
    TARGET_DONE,
    TARGET_PENDING,
    TARGET_RUNNING,
    TASK_CANCELLED,
    TASK_PENDING,
    TASK_RUNNING,
    Group,
    Schedule,
    Task,
    TaskTarget,
    Template,
    to_iso,
    utcnow,
)


# How far back the repeated-text guard looks, per group. The history prune
# keeps at least this many posted bodies per group for ever -- the two numbers
# must be the same one, or pruning quietly weakens the guard.
RECENT_BODIES_LIMIT = 20


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

        default_cooldown = SettingsRepo(self.db).get_int("default_cooldown_hours", 8)
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

    def recent_bodies(self, group_id: int, limit: int = RECENT_BODIES_LIMIT) -> list[str]:
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
        schedule_id: int | None = None,
    ) -> Task:
        """Create a batch and its per-group targets atomically.

        `targets` is (group_id, body-for-that-group). A half-written batch --
        a task row with no targets, or targets without a task -- would leave the
        worker with an impossible queue, so both go in one transaction.
        """
        if not targets:
            raise ValueError("A task needs at least one target group.")

        # First wording wins if a group is listed twice. UNIQUE(task_id,
        # group_id) would otherwise raise a raw sqlite3.IntegrityError at the
        # caller -- the index is the backstop, not the error message.
        deduped: dict[int, str] = {}
        for group_id, body_for_group in targets:
            deduped.setdefault(group_id, body_for_group)
        targets = list(deduped.items())

        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks "
                "(body, media_paths, scheduled_for, state, created_at, schedule_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    body,
                    json.dumps([str(p) for p in media_paths]),
                    to_iso(scheduled_for),
                    TASK_PENDING,
                    to_iso(utcnow()),
                    schedule_id,
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

    def list_for_queue(
        self, now: datetime, retention_hours: int = 24, limit: int = 50
    ) -> list[Task]:
        """What the queue screen shows: everything live, plus recent history.

        A batch that has not finished is always listed however old it is --
        hiding something still due to go out would be a good deal worse than a
        cluttered screen. Only finished batches age out.

        This filters the *view*. Nothing is deleted: `task_targets` rows are
        what `GroupRepo.recent_bodies` reads to refuse reposting the same words
        to a group, and what `posted_count_since` counts for the daily cap.
        Deleting history would quietly switch off both.
        """
        if retention_hours <= 0:
            return self.list_recent(limit)

        cutoff = to_iso(now - timedelta(hours=retention_hours))
        rows = self.db.query(
            "SELECT * FROM tasks "
            "WHERE state IN (?, ?) OR COALESCE(finished_at, created_at) >= ? "
            "ORDER BY id DESC LIMIT ?",
            (TASK_PENDING, TASK_RUNNING, cutoff, limit),
        )
        return [Task.from_row(row) for row in rows]

    def count_older_than(self, now: datetime, retention_hours: int = 24) -> int:
        """Finished batches the queue screen is currently hiding."""
        if retention_hours <= 0:
            return 0
        cutoff = to_iso(now - timedelta(hours=retention_hours))
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks "
            "WHERE state NOT IN (?, ?) AND COALESCE(finished_at, created_at) < ?",
            (TASK_PENDING, TASK_RUNNING, cutoff),
        )
        return int(row["n"]) if row else 0

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

    def claim_target(self, target_id: int) -> bool:
        """Take ownership of a target, or report that someone else has it.

        The conditional UPDATE is the whole point. Reading a pending target and
        then marking it running as two steps leaves a window in which a second
        worker -- a second copy of the app, on the same database -- reads the
        same target and posts it too. Racing two workers on one database
        produced a duplicate in 7 runs out of 40 before this existed, and a
        duplicate post is the worst thing this project can do.

        `WHERE state = pending` makes the transition itself the lock: SQLite
        serialises the writers, so exactly one of them changes a row.
        """
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_targets SET state = ?, attempted_at = ? "
                "WHERE id = ? AND state = ?",
                (TARGET_RUNNING, to_iso(utcnow()), target_id, TARGET_PENDING),
            )
            return cursor.rowcount == 1

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

    def prune_history(
        self,
        now: datetime,
        keep_days: int,
        keep_per_group: int = RECENT_BODIES_LIMIT,
    ) -> int:
        """Delete finished batches older than `keep_days`. Returns how many.

        Three things are never deleted, and each one is load-bearing:

        * **Anything unfinished.** A batch still due to go out is not history.
        * **The newest `keep_per_group` posted bodies for each group**, however
          old. That is exactly what `GroupRepo.recent_bodies` reads, so the
          repeated-text guard keeps refusing wording a group has already had --
          which is the app's main protection and worth a few kilobytes for ever.
        * **Everything inside the window**, which is far wider than the daily
          cap's local-midnight lookback, so the cap is never miscounted.

        `keep_days <= 0` disables pruning entirely.
        """
        if keep_days <= 0:
            return 0

        cutoff = to_iso(now - timedelta(days=keep_days))
        # Selected first rather than deleted in one statement: sqlite3 reports
        # rowcount as -1 for a DELETE that starts with a CTE, and a count of
        # "batches removed" that silently means "unknown" is worse than none.
        doomed = [
            row["id"]
            for row in self.db.query(
                "WITH keep AS ("
                "  SELECT task_id FROM ("
                "    SELECT task_id, ROW_NUMBER() OVER ("
                "      PARTITION BY group_id ORDER BY posted_at DESC, id DESC"
                "    ) AS rank_in_group"
                "    FROM task_targets WHERE state = ?"
                "  ) WHERE rank_in_group <= ?"
                ") "
                "SELECT id FROM tasks "
                "WHERE state NOT IN (?, ?) "
                "  AND COALESCE(finished_at, created_at) < ? "
                "  AND id NOT IN (SELECT task_id FROM keep)",
                (TARGET_DONE, keep_per_group, TASK_PENDING, TASK_RUNNING, cutoff),
            )
        ]
        if not doomed:
            return 0

        with self.db.transaction() as connection:
            # task_targets go with them: the foreign key is ON DELETE CASCADE
            # and connections run with PRAGMA foreign_keys = ON.
            connection.executemany(
                "DELETE FROM tasks WHERE id = ?", [(task_id,) for task_id in doomed]
            )
        return len(doomed)

    def reclaim_space(self) -> bool:
        """Hand freed pages back to the filesystem. Best effort.

        Deleting rows leaves the file the same size; only VACUUM shrinks it.
        It cannot run inside a transaction and needs no other writer, so a
        failure here is not worth surfacing -- the rows are already gone.
        """
        try:
            self.db.connection.execute("VACUUM")
            # Under WAL the rewritten pages sit in the -wal file until a
            # checkpoint, so without this the main database is no smaller on
            # disk and the whole exercise achieves nothing visible.
            self.db.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        except sqlite3.Error:
            return False

    def unfinished_for_schedule(self, schedule_id: int) -> int:
        """Batches from this schedule that have not gone out yet.

        A schedule firing again while its last batch is still queued would stack
        posts on top of each other -- the opposite of the spacing everything
        else in this app is built around -- so an occurrence that lands on top
        of an unfinished one is skipped rather than added.
        """
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE schedule_id = ? AND state IN (?, ?)",
            (schedule_id, TASK_PENDING, TASK_RUNNING),
        )
        return int(row["n"]) if row else 0


class ScheduleRepo:
    """Repeating posts.

    A schedule holds the definition; firing one writes an ordinary task through
    TaskRepo, so nothing downstream needs to know schedules exist.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def _group_ids(self, schedule_id: int) -> list[int]:
        return [
            row["group_id"]
            for row in self.db.query(
                "SELECT group_id FROM schedule_targets WHERE schedule_id = ? "
                "ORDER BY position",
                (schedule_id,),
            )
        ]

    def _hydrate(self, rows: Sequence[Any]) -> list[Schedule]:
        return [Schedule.from_row(row, self._group_ids(row["id"])) for row in rows]

    def list(self) -> list[Schedule]:
        return self._hydrate(self.db.query("SELECT * FROM schedules ORDER BY id"))

    def get(self, schedule_id: int) -> Schedule | None:
        row = self.db.query_one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        if row is None:
            return None
        return Schedule.from_row(row, self._group_ids(schedule_id))

    def create(
        self,
        *,
        name: str,
        bodies: Sequence[str],
        group_ids: Sequence[int],
        times: Sequence[str],
        days: Sequence[int] = (),
        media_paths: Sequence[str] = (),
        next_run_at: datetime | None = None,
    ) -> Schedule:
        """Write a schedule and its groups in one transaction.

        A schedule with no groups, or groups with no schedule, would sit in the
        database doing nothing visible -- so, as with tasks, both halves land
        together or neither does.
        """
        cleaned = [b for b in bodies if b.strip()]
        if not cleaned:
            raise ValueError("A repeating post needs at least one wording.")
        if not group_ids:
            raise ValueError("A repeating post needs at least one group.")
        if not times:
            raise ValueError("A repeating post needs at least one time of day.")

        with self.db.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO schedules "
                "(name, bodies, media_paths, times, days, state, next_run_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    json.dumps(list(cleaned)),
                    json.dumps([str(p) for p in media_paths]),
                    json.dumps(list(times)),
                    json.dumps([int(d) for d in days]),
                    SCHEDULE_ACTIVE,
                    to_iso(next_run_at),
                    to_iso(utcnow()),
                ),
            )
            schedule_id = cursor.lastrowid
            connection.executemany(
                "INSERT INTO schedule_targets (schedule_id, group_id, position) "
                "VALUES (?, ?, ?)",
                [
                    (schedule_id, group_id, position)
                    for position, group_id in enumerate(dict.fromkeys(group_ids))
                ],
            )

        stored = self.get(schedule_id)
        assert stored is not None
        return stored

    def set_state(self, schedule_id: int, state: str) -> None:
        self.db.write(
            "UPDATE schedules SET state = ? WHERE id = ?", (state, schedule_id)
        )

    def set_bodies(self, schedule_id: int, bodies: Sequence[str]) -> None:
        cleaned = [b for b in bodies if b.strip()]
        if not cleaned:
            raise ValueError("A repeating post needs at least one wording.")
        self.db.write(
            "UPDATE schedules SET bodies = ? WHERE id = ?",
            (json.dumps(cleaned), schedule_id),
        )

    def set_next_run(self, schedule_id: int, when: datetime | None) -> None:
        self.db.write(
            "UPDATE schedules SET next_run_at = ? WHERE id = ?",
            (to_iso(when), schedule_id),
        )

    def record_run(
        self, schedule_id: int, ran_at: datetime, next_run_at: datetime | None
    ) -> None:
        """Advance a schedule past the occurrence it just fired.

        run_count is what rotates the wordings, so it moves on every firing --
        that is the difference between each group getting its own text and all
        of them getting the same one.
        """
        self.db.write(
            "UPDATE schedules SET run_count = run_count + 1, last_run_at = ?, "
            "next_run_at = ? WHERE id = ?",
            (to_iso(ran_at), to_iso(next_run_at), schedule_id),
        )

    def delete(self, schedule_id: int) -> None:
        self.db.write("DELETE FROM schedules WHERE id = ?", (schedule_id,))

    def due(self, now: datetime) -> list[Schedule]:
        """Active schedules whose moment has arrived, oldest slot first."""
        rows = self.db.query(
            "SELECT * FROM schedules WHERE state = ? "
            "  AND (next_run_at IS NULL OR next_run_at <= ?) "
            "ORDER BY COALESCE(next_run_at, created_at), id",
            (SCHEDULE_ACTIVE, to_iso(now)),
        )
        return self._hydrate(rows)
