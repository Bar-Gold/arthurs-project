"""Query layer.

One repository per table group. Repositories return model objects and never
make policy decisions -- the safety rules live in fbposter/guards.py as pure
functions, so they can be tested without a database.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable, NamedTuple, Sequence

from .. import clock
from ..errors import DuplicateGroup, InvalidGroupURL
from ..groups import parse_group_url
from .connection import Database
from .models import (
    SCHEDULE_ACTIVE,
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
    TARGET_DONE,
    TARGET_PENDING,
    TARGET_RUNNING,
    TARGET_SKIPPED,
    TARGET_UNCONFIRMED,
    TASK_CANCELLED,
    TASK_DONE,
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

# Every state meaning the words reached the group, or may have: posted,
# waiting for an admin, or stopped after Post was clicked. All three count for
# the repeated-text rule, the cooldown and the daily limit (the user's choice
# for the last, 2026-10-07).
SENT_STATES = (TARGET_DONE, TARGET_AWAITING_APPROVAL, TARGET_UNCONFIRMED)

# "Leave this column as it is", where None already means "clear it".
KEEP = object()

# What a queued post records for a group the user removed before its turn.
GROUP_REMOVED = "You removed this group from your list before its turn."

# A member count that could not be read is tried again after this long, not
# on every visit to the Groups screen (`GroupRepo.missing_details`).
MEMBERS_RETRY = timedelta(days=1)


class Removal(NamedTuple):
    """What removing a group took it out of, for the Groups screen to say.

    The rest is exactly what changed, so `GroupRepo.restore` can put it back
    for an Undo -- and only what is still as the removal left it.
    """

    schedules: int
    batches: int
    # (schedule_id, position) of every repeating post it was taken out of.
    schedule_rows: tuple = ()
    # Turns in queued posts marked skipped with GROUP_REMOVED.
    skipped_targets: tuple = ()
    # (task_id, state before, state after) of batches left with nothing to do.
    finished: tuple = ()


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

    def set_default_cooldown(self, hours: int) -> None:
        """Change the one cooldown rule, on every group, in one transaction."""
        _check_cooldown(hours)
        with self.db.transaction() as connection:
            _write_cooldown(connection, hours)

    def set_posting_rules(
        self, *, start_hour: int, end_hour: int, daily_cap: int, cooldown_hours: int
    ) -> None:
        """Save the posting hours, the daily limit and the cooldown together.

        All three are checked before anything is written, and written in one
        transaction, so the Settings screen can never leave half of what the
        user pressed Save on in force.

        Start and end may not be the same hour: `clock.inside_window` reads
        that as "no posting hours at all", which would let a batch post at 4am.
        A window that crosses midnight (22 to 6) is allowed; the clock handles
        it. The daily limit may not be nought, which `check_daily_cap` reads as
        "no limit".
        """
        for name, hour in (("start", start_hour), ("end", end_hour)):
            if not 0 <= hour <= 23:
                raise ValueError(f"{name} hour must be 0-23, not {hour}")
        if start_hour == end_hour:
            raise ValueError("the posting hours cannot start and end at the same hour")
        if daily_cap < 1:
            raise ValueError(f"the daily limit must be at least 1, not {daily_cap}")
        _check_cooldown(cooldown_hours)
        with self.db.transaction() as connection:
            for key, value in (
                ("posting_window_start_hour", start_hour),
                ("posting_window_end_hour", end_hour),
                ("daily_cap", daily_cap),
            ):
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
            _write_cooldown(connection, cooldown_hours)


def _check_cooldown(hours: int) -> None:
    if hours < 1:
        raise ValueError(f"cooldown must be at least 1 hour, not {hours}")


def _write_cooldown(connection, hours: int) -> None:
    """The setting and every group's own copy of it.

    The setting alone is not enough: the worker and Compose read each group's
    own `cooldown_hours`, and migration 009 only copied the value across once.
    Archived groups are included, so pasting one back in brings it back on the
    current rule rather than an old one.
    """
    connection.execute(
        "INSERT INTO settings (key, value) VALUES ('default_cooldown_hours', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(hours),),
    )
    connection.execute("UPDATE groups SET cooldown_hours = ?", (hours,))


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

        existing = self.get_by_identifier(ref.identifier)
        if existing is not None:
            if not existing.archived:
                raise DuplicateGroup(f"Group {ref.identifier} is already in the list.")
            # Removing a group archives it, so the row is still here holding
            # every post ever made to it. Adding it again has to bring the
            # history back with it -- returning a fresh empty group would be
            # the repeated-text guard going blank, which is the thing archiving
            # exists to prevent.
            self.db.write("UPDATE groups SET archived = 0 WHERE id = ?", (existing.id,))
            restored = self.get(existing.id)
            assert restored is not None
            return restored

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

    def import_found(self, found: Iterable[tuple[str, str, str]]) -> list[Group]:
        """Add the groups read off Facebook, as (identifier, url, name).

        Only groups not in the table at all are added. One the user removed is
        archived, not gone, and it stays removed: unlike pasting its link,
        which is the user asking for it back, an import is the app deciding,
        and it must never overrule a removal. Facebook writes a group's short
        name in any case it likes, so the match ignores case.

        The name comes with it, so the Groups screen has nothing to look up.
        Fifty nameless groups would be fifty page loads in a row.
        """
        default_cooldown = SettingsRepo(self.db).get_int("default_cooldown_hours", 8)
        added: list[int] = []
        with self.db.transaction() as connection:
            for identifier, url, name in found:
                known = connection.execute(
                    "SELECT 1 FROM groups WHERE lower(identifier) = lower(?)", (identifier,)
                ).fetchone()
                if known:
                    continue
                cursor = connection.execute(
                    "INSERT INTO groups (identifier, url, name, cooldown_hours, notes, created_at) "
                    "VALUES (?, ?, ?, ?, '', ?)",
                    (identifier, url, name, default_cooldown, to_iso(utcnow())),
                )
                added.append(cursor.lastrowid)
        return [group for group in (self.get(group_id) for group_id in added) if group]

    def remove(self, group_id: int) -> Removal:
        """Take a group out of the list without destroying what went to it.

        This was a DELETE, and `task_targets.group_id` is ON DELETE CASCADE --
        so removing a group took every post ever made to it along with it, and
        that is exactly the window `recent_bodies` reads for
        `guards.check_repeat_text`. Remove a group and add it back (to correct
        a name, say) and the guard went blank: the advert that group had
        already been sent could go to it again, which is the ban vector this
        whole app is built around. Two clicks in the UI, no warning, nothing
        on screen to show it had happened.

        Archiving hides the row and keeps the history. `list()` already
        excludes archived groups, so the screens need no change; `bring_back`
        un-archives, from the Removed list on the Groups screen.

        It also takes the group out of every repeating post and every queued
        post still to go out, and adding it back puts it in neither: the user's
        choice (2026-09-30 and 2026-10-01). A removed group used to stay in both
        and be posted to again the moment it was re-added.

        In a queued post only a *pending* turn is taken: one already running is
        mid-post and cannot be taken back. The turn is marked skipped rather
        than deleted, so the Queue says what became of it, and the conditional
        state is what stops this racing the worker's `claim_target`. A queued
        post left with nothing to do is finished here rather than at its time:
        cancelled if it never started, done if it did, as the worker would.
        """
        unfinished = (TASK_PENDING, TASK_RUNNING)
        with self.db.transaction() as connection:
            connection.execute("UPDATE groups SET archived = 1 WHERE id = ?", (group_id,))
            schedule_rows = tuple(
                (row["schedule_id"], row["position"])
                for row in connection.execute(
                    "SELECT schedule_id, position FROM schedule_targets WHERE group_id = ?",
                    (group_id,),
                )
            )
            schedules = connection.execute(
                "DELETE FROM schedule_targets WHERE group_id = ?", (group_id,)
            ).rowcount

            in_unfinished = "task_id IN (SELECT id FROM tasks WHERE state IN (?, ?))"
            batches = [
                row["task_id"]
                for row in connection.execute(
                    "SELECT DISTINCT task_id FROM task_targets "
                    f"WHERE group_id = ? AND state = ? AND {in_unfinished}",
                    (group_id, TARGET_PENDING, *unfinished),
                )
            ]
            skipped_targets = tuple(
                row["id"]
                for row in connection.execute(
                    "SELECT id FROM task_targets "
                    f"WHERE group_id = ? AND state = ? AND {in_unfinished}",
                    (group_id, TARGET_PENDING, *unfinished),
                )
            )
            connection.execute(
                "UPDATE task_targets SET state = ?, error = ? "
                f"WHERE group_id = ? AND state = ? AND {in_unfinished}",
                (TARGET_SKIPPED, GROUP_REMOVED, group_id, TARGET_PENDING, *unfinished),
            )

            finished = []
            for task_id in batches:
                left = connection.execute(
                    "SELECT COUNT(*) FROM task_targets WHERE task_id = ? AND state IN (?, ?)",
                    (task_id, TARGET_PENDING, TARGET_RUNNING),
                ).fetchone()[0]
                if left:
                    continue
                task = connection.execute(
                    "SELECT state, started_at FROM tasks WHERE id = ?", (task_id,)
                ).fetchone()
                state, error = (
                    (TASK_DONE, "") if task["started_at"]
                    else (TASK_CANCELLED, "Every group in it was removed.")
                )
                connection.execute(
                    "UPDATE tasks SET state = ?, error = ?, finished_at = ?, "
                    "resume_at = NULL WHERE id = ?",
                    (state, error, to_iso(utcnow()), task_id),
                )
                finished.append((task_id, task["state"], state))
        return Removal(
            schedules=schedules,
            batches=len(batches),
            schedule_rows=schedule_rows,
            skipped_targets=skipped_targets,
            finished=tuple(finished),
        )

    def restore(self, group_id: int, removal: Removal) -> bool:
        """Undo a removal, straight after it. False if it can no longer be.

        Puts back exactly what `remove` changed, and only what is still as it
        left it: a repeating post deleted since stays deleted, and a queued
        post's turn is handed back only while it still reads "removed". Undo
        is not re-adding -- "Bring back" on the Removed list still puts the
        group back into neither -- it is taking back a click a moment ago.
        """
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT archived FROM groups WHERE id = ?", (group_id,)
            ).fetchone()
            if row is None or not row["archived"]:
                return False  # gone, or already added back
            connection.execute("UPDATE groups SET archived = 0 WHERE id = ?", (group_id,))
            for schedule_id, position in removal.schedule_rows:
                connection.execute(
                    "INSERT OR IGNORE INTO schedule_targets (schedule_id, group_id, position) "
                    "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM schedules WHERE id = ?)",
                    (schedule_id, group_id, position, schedule_id),
                )
            for target_id in removal.skipped_targets:
                connection.execute(
                    "UPDATE task_targets SET state = ?, error = '' "
                    "WHERE id = ? AND state = ? AND error = ?",
                    (TARGET_PENDING, target_id, TARGET_SKIPPED, GROUP_REMOVED),
                )
            for task_id, before, after in removal.finished:
                connection.execute(
                    "UPDATE tasks SET state = ?, error = '', finished_at = NULL "
                    "WHERE id = ? AND state = ?",
                    (before, task_id, after),
                )
        return True

    def removed(self) -> list[Group]:
        """The groups the user removed, for the Groups screen's Removed list."""
        rows = self.db.query("SELECT * FROM groups WHERE archived = 1 ORDER BY id")
        return [Group.from_row(row) for row in rows]

    def bring_back(self, group_id: int) -> Group | None:
        """Put a removed group back in the list, history and all.

        The Removed list's "Bring back", which replaced pasting the link again
        when the paste box went (2026-10-14). Same rule as that was: the
        history comes back, so the repeat guard still knows what the group was
        sent, but its place in repeating and queued posts does not. Only Undo,
        straight after removing, puts those back (`restore`). None if the
        group is gone or already back.
        """
        with self.db.transaction() as connection:
            changed = connection.execute(
                "UPDATE groups SET archived = 0 WHERE id = ? AND archived = 1", (group_id,)
            ).rowcount
        return self.get(group_id) if changed else None

    def active(self, group_id: int) -> "Group | None":
        """The group, but only if it is still in the user's list.

        `get()` deliberately still finds an archived group, because the queue
        has to name a group it posted to last week. Anything deciding whether
        to *post* must ask this instead -- an archived group is one the user
        removed, and before archiving existed such a group was simply gone.
        """
        group = self.get(group_id)
        return None if group is None or group.archived else group

    def set_name(self, group_id: int, name: str) -> None:
        """Store the human-readable name read off the group's page."""
        self.db.write("UPDATE groups SET name = ? WHERE id = ?", (name, group_id))

    def missing_names(self) -> list[Group]:
        """Groups still showing an identifier because no name was ever fetched."""
        rows = self.db.query(
            "SELECT * FROM groups WHERE archived = 0 AND TRIM(name) = '' ORDER BY id"
        )
        return [Group.from_row(row) for row in rows]

    def missing_details(self, now: datetime | None = None) -> list[Group]:
        """Groups whose page is worth opening: no name yet, or no member count.

        A member count that could not be read is tried again only after
        `MEMBERS_RETRY`, not on every visit to the Groups screen. Every try is
        a real page load, and if Facebook ever moves the count, every group
        would otherwise be reopened each time the screen is shown.
        """
        before = to_iso((now or utcnow()) - MEMBERS_RETRY)
        rows = self.db.query(
            "SELECT * FROM groups WHERE archived = 0 AND ("
            "  TRIM(name) = ''"
            "  OR (members = '' AND (members_read_at IS NULL OR members_read_at < ?))"
            ") ORDER BY id",
            (before,),
        )
        return [Group.from_row(row) for row in rows]

    def set_members(self, group_id: int, members: str, when: datetime | None = None) -> None:
        """Store the member count read off the group's page, even an empty one,
        so a count that cannot be read waits `MEMBERS_RETRY` before the next try."""
        self.db.write(
            "UPDATE groups SET members = ?, members_read_at = ? WHERE id = ?",
            (members, to_iso(when or utcnow()), group_id),
        )

    def set_cooldown(self, group_id: int, hours: int) -> None:
        self.db.write("UPDATE groups SET cooldown_hours = ? WHERE id = ?", (hours, group_id))

    def mark_posted(self, group_id: int, when: datetime | None = None) -> None:
        self.db.write(
            "UPDATE groups SET last_posted_at = ? WHERE id = ?",
            (to_iso(when or utcnow()), group_id),
        )

    def recent_bodies(self, group_id: int, limit: int = RECENT_BODIES_LIMIT) -> list[str]:
        """Text already sent to this group, newest first.

        The group's history, not the repeated-text rule -- that reads
        `sent_on_day`. Verification reads this: an older post with the same
        words may still be in the group's feed, so finding the words there is
        no proof of a new post.
        """
        rows = self.db.query(
            "SELECT body FROM task_targets WHERE group_id = ? AND state IN (?, ?, ?) "
            "ORDER BY COALESCE(posted_at, attempted_at) DESC LIMIT ?",
            (group_id, *SENT_STATES, limit),
        )
        return [row["body"] for row in rows]

    def sent_on_day(self, group_id: int, moment: datetime) -> list[str]:
        """Text sent to this group on the Israel day `moment` falls on.

        Feeds the repeated-text guard. The same words may go to a group once a
        day -- the user's choice, 2026-10-07, replacing "never again" -- so
        only that day's posts count. One that may have gone out counts too:
        if it did, a second copy the same day is the duplicate this guards.
        """
        start = clock.start_of_local_day(moment)
        # Past the next midnight whatever daylight saving does, then back to it.
        end = clock.start_of_local_day(start + timedelta(hours=26))
        rows = self.db.query(
            "SELECT body FROM task_targets WHERE group_id = ? AND state IN (?, ?, ?) "
            "  AND COALESCE(posted_at, attempted_at) >= ? "
            "  AND COALESCE(posted_at, attempted_at) < ?",
            (group_id, *SENT_STATES, to_iso(start), to_iso(end)),
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
        overrides: Iterable[str] = (),
    ) -> Task:
        """Create a batch and its per-group targets atomically.

        `overrides` are the rules the user chose "Post anyway" over; see
        `guards.OVERRIDABLE`.

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
                "(body, media_paths, scheduled_for, state, created_at, schedule_id, "
                "overrides) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    body,
                    json.dumps([str(p) for p in media_paths]),
                    to_iso(scheduled_for),
                    TASK_PENDING,
                    to_iso(utcnow()),
                    schedule_id,
                    json.dumps(sorted(overrides)),
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

    def targets_for_tasks(self, task_ids: Sequence[int]) -> dict[int, list[TaskTarget]]:
        """The same rows as `targets_for`, for many batches in one query.

        The Queue screen reads every target twice on every refresh -- once to
        decide whether anything changed, once to draw. One query per batch was
        fine at three batches and is 25 round trips at 25, on the thread
        drawing the window, several times a minute while a batch runs.

        This is emphatically *not* a cache: it reads the same rows the loop
        would have read, at the same moment, and the answer is thrown away
        when the refresh ends. A stale read would be a wrong screen.
        """
        if not task_ids:
            return {}
        marks = ",".join("?" * len(task_ids))
        rows = self.db.query(
            "SELECT t.*, g.identifier AS group_identifier, g.name AS group_name "
            "FROM task_targets t JOIN groups g ON g.id = t.group_id "
            f"WHERE t.task_id IN ({marks}) ORDER BY t.task_id, t.position",
            tuple(task_ids),
        )
        grouped: dict[int, list[TaskTarget]] = {task_id: [] for task_id in task_ids}
        for row in rows:
            grouped[row["task_id"]].append(TaskTarget.from_row(row))
        return grouped

    def uncancel(self, task_id: int, state: str) -> bool:
        """Undo a cancel, straight after it. False if it can no longer be.

        Only a batch still cancelled goes back, to the state it was in. One
        that finished in the meantime -- cancelled mid-post, and that post was
        its last -- is left as it is.
        """
        if state not in (TASK_PENDING, TASK_RUNNING):
            return False
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tasks SET state = ?, finished_at = NULL WHERE id = ? AND state = ?",
                (state, task_id, TASK_CANCELLED),
            )
            return cursor.rowcount == 1

    def cancel(self, task_id: int) -> None:
        # The note goes too: a "waiting for the posting hours" line under a
        # cancelled batch would say it is still going to post.
        self.db.write(
            "UPDATE tasks SET state = ?, error = '', finished_at = ? WHERE id = ?",
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

    def active_batches(self, before: datetime | None = None) -> int:
        """Batches under way. Drives the keep-awake request.

        `before` narrows it to the ones that will actually do something by
        then, and that bound is the whole point. A batch that ran out of
        posting window at 23:00 stays `running` with `resume_at` set to 08:00
        the next morning -- counting it held the machine awake all night for
        work nine hours away. On a desktop that is a wasted night; on a laptop
        it is the battery.
        """
        if before is None:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM tasks WHERE state = ?", (TASK_RUNNING,)
            )
        else:
            row = self.db.query_one(
                "SELECT COUNT(*) AS n FROM tasks WHERE state = ? "
                "  AND (resume_at IS NULL OR resume_at <= ?)",
                (TASK_RUNNING, to_iso(before)),
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

    def release_target(self, target_id: int) -> bool:
        """Hand a claimed target back, unattempted.

        The mirror of `claim_target`, and conditional for the same reason:
        only a worker holding the claim may give it up, so this can never drag
        a finished target back into the queue.

        It exists for the one failure that happens before the browser is
        touched at all -- Chrome not reachable over CDP. Nothing was typed and
        nothing was published, so failing the group would be recording a
        defeat that never took place; `attempted_at` is cleared for the same
        reason. Anything that happens *after* a page exists keeps the old
        behaviour and halts, because by then it might have posted.
        """
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_targets SET state = ?, attempted_at = NULL "
                "WHERE id = ? AND state = ?",
                (TARGET_PENDING, target_id, TARGET_RUNNING),
            )
            return cursor.rowcount == 1

    def awaiting_approval_targets(self) -> list[TaskTarget]:
        """Posts submitted to a moderated group and not yet resolved."""
        rows = self.db.query(
            "SELECT t.*, g.identifier AS group_identifier, g.name AS group_name "
            "FROM task_targets t JOIN groups g ON g.id = t.group_id "
            "WHERE t.state = ? ORDER BY t.posted_at",
            (TARGET_AWAITING_APPROVAL,),
        )
        return [TaskTarget.from_row(row) for row in rows]

    def record_resolve_miss(self, target_id: int) -> int:
        """One more follow-up that could not find it. Returns the new total."""
        self.db.write(
            "UPDATE task_targets SET resolve_misses = resolve_misses + 1 "
            "WHERE id = ?",
            (target_id,),
        )
        row = self.db.query_one(
            "SELECT resolve_misses FROM task_targets WHERE id = ?", (target_id,)
        )
        return int(row["resolve_misses"]) if row else 0

    def clear_resolve_misses(self, target_id: int) -> None:
        """It turned up again, so the count starts over."""
        self.db.write(
            "UPDATE task_targets SET resolve_misses = 0 WHERE id = ?", (target_id,)
        )

    def resolve_pending(
        self, target_id: int, approved: bool, note: str = ""
    ) -> bool:
        """Record what an admin did with a post that was awaiting approval.

        Approved leaves the wording claimed, because it is now live. Declined
        releases it: nothing was published, so refusing to let the user send
        those words again would be blocking them on a post that never existed.

        `note` says how it was found out -- the worker resolves these on its
        own, and a row saying the user confirmed it when they did nothing of
        the sort is a small lie the queue screen would go on repeating.

        Conditional on the target still being `awaiting_approval`, so this can
        only ever resolve a genuinely pending post -- it cannot resurrect a
        failed one or overwrite a real success.
        """
        state = TARGET_DONE if approved else TARGET_DECLINED
        note = note or (
            "Confirmed live by the user."
            if approved
            else "An admin declined it, so nothing was published."
        )
        with self.db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE task_targets SET state = ?, error = ? "
                "WHERE id = ? AND state = ?",
                (state, note, target_id, TARGET_AWAITING_APPROVAL),
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

    def set_resume_at(
        self, task_id: int, when: datetime | None, note: str | None = None
    ) -> None:
        """When the worker may next act on this batch, and optionally why.

        `note` says why a batch is waiting -- the posting hours, the daily
        limit, Chrome -- so the Queue can say it after the toast has gone.
        Posting the next group clears it.
        """
        if note is None:
            self.db.write(
                "UPDATE tasks SET resume_at = ? WHERE id = ?", (to_iso(when), task_id)
            )
        else:
            self.db.write(
                "UPDATE tasks SET resume_at = ?, error = ? WHERE id = ?",
                (to_iso(when), note, task_id),
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
        posted_at: datetime | None = None,
    ) -> None:
        """Record one group's outcome.

        Committed the moment it happens: that is what lets a crash resume a
        batch rather than start it again. `posted_at` overrides "now" for a
        post that happened earlier, such as one found after a crash.
        """
        sets = ["state = ?", "error = ?"]
        params: list[Any] = [state, error]
        if attempted:
            sets.append("attempted_at = ?")
            params.append(to_iso(utcnow()))
        if posted:
            sets.append("posted_at = ?")
            params.append(to_iso(posted_at or utcnow()))
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
            "SELECT COUNT(*) AS n FROM task_targets "
            "WHERE state IN (?, ?, ?) AND posted_at >= ?",
            (*SENT_STATES, to_iso(since)),
        )
        return int(row["n"]) if row else 0

    def prune_history(
        self,
        now: datetime,
        keep_days: int,
        keep_per_group: int = RECENT_BODIES_LIMIT,
    ) -> int:
        """Delete finished batches older than `keep_days`. Returns how many.

        Four things are never deleted, and each one is load-bearing:

        * **Anything unfinished.** A batch still due to go out is not history.
        * **Anything still awaiting an admin.** The batch around it counts as
          finished, but the post has not landed yet: deleting it would take the
          Queue row, the two override buttons and the follow-up's only record
          of it all at once, and the post would still be sitting in the group's
          moderation queue.
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
                "    FROM task_targets WHERE state IN (?, ?)"
                "  ) WHERE rank_in_group <= ?"
                ") "
                "SELECT id FROM tasks "
                "WHERE state NOT IN (?, ?) "
                "  AND COALESCE(finished_at, created_at) < ? "
                "  AND id NOT IN (SELECT task_id FROM keep) "
                "  AND id NOT IN ("
                "    SELECT task_id FROM task_targets WHERE state = ?"
                "  )",
                (TARGET_DONE, TARGET_AWAITING_APPROVAL, keep_per_group,
                 TASK_PENDING, TASK_RUNNING, cutoff, TARGET_AWAITING_APPROVAL),
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

    def unfinished_count(self) -> int:
        """Batches still due to go out: queued, waiting for a slot, or mid-run.

        Read when the window is closing, because closing it stops the worker.
        """
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM tasks WHERE state IN (?, ?)",
            (TASK_PENDING, TASK_RUNNING),
        )
        return int(row["n"]) if row else 0

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

    def names_by_id(self) -> dict[int, str]:
        """Every schedule's display name, in one query.

        The Queue labels each batch with the schedule that made it. Fetching
        each one through `get()` cost two queries per card -- the row, then its
        group ids, which the label does not use at all.
        """
        return {
            row["id"]: (row["name"] or f"Schedule {row['id']}")
            for row in self.db.query("SELECT id, name FROM schedules")
        }

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
        overrides: Iterable[str] = (),
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
                "(name, bodies, media_paths, times, days, state, next_run_at, created_at, "
                "overrides) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name,
                    json.dumps(list(cleaned)),
                    json.dumps([str(p) for p in media_paths]),
                    json.dumps(list(times)),
                    json.dumps([int(d) for d in days]),
                    SCHEDULE_ACTIVE,
                    to_iso(next_run_at),
                    to_iso(utcnow()),
                    json.dumps(sorted(overrides)),
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

    def update(
        self,
        schedule_id: int,
        *,
        name: str,
        bodies: Sequence[str],
        group_ids: Sequence[int],
        times: Sequence[str],
        days: Sequence[int] = (),
        media_paths: Sequence[str] = (),
        overrides: Iterable[str] = (),
        next_run_at: datetime | None | object = KEEP,
    ) -> Schedule:
        """Rewrite what a schedule posts, where, and when, in one transaction.

        Everything the worker keeps for itself is left alone: the state, so an
        edit never pauses or resumes anything; run_count, which rotates the
        wordings; and last_run_at. next_run_at changes only when it is passed,
        because the caller is the one that knows whether the times moved.

        Batches it has already queued are ordinary tasks with their own text
        and groups, so they go out as they were; only later runs see this.
        """
        cleaned = [b for b in bodies if b.strip()]
        if not cleaned:
            raise ValueError("A repeating post needs at least one wording.")
        if not group_ids:
            raise ValueError("A repeating post needs at least one group.")
        if not times:
            raise ValueError("A repeating post needs at least one time of day.")

        columns = (
            "name = ?, bodies = ?, media_paths = ?, times = ?, days = ?, overrides = ?"
        )
        values: list[Any] = [
            name,
            json.dumps(list(cleaned)),
            json.dumps([str(p) for p in media_paths]),
            json.dumps(list(times)),
            json.dumps([int(d) for d in days]),
            json.dumps(sorted(overrides)),
        ]
        if next_run_at is not KEEP:
            columns += ", next_run_at = ?"
            values.append(to_iso(next_run_at))

        with self.db.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE schedules SET {columns} WHERE id = ?",
                (*values, schedule_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("That repeating post no longer exists.")
            connection.execute(
                "DELETE FROM schedule_targets WHERE schedule_id = ?", (schedule_id,)
            )
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

    def restore(self, schedule: Schedule) -> bool:
        """Undo a delete, straight after it. False if it can no longer be.

        Written back under its own id, so the batches it fired -- which keep
        that id in `tasks.schedule_id` -- are its own again on the Queue, and
        with everything the worker keeps for itself: the state, `run_count`
        (which rotates the wordings) and the next slot. A group removed in
        the meantime is not put back into it, as removing one would have
        taken it out.
        """
        with self.db.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM schedules WHERE id = ?", (schedule.id,)
            ).fetchone():
                return False
            connection.execute(
                "INSERT INTO schedules (id, name, bodies, media_paths, times, days, "
                "state, run_count, next_run_at, last_run_at, created_at, overrides) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    schedule.id,
                    schedule.name,
                    json.dumps(list(schedule.bodies)),
                    json.dumps(list(schedule.media_paths)),
                    json.dumps(list(schedule.times)),
                    json.dumps([int(d) for d in schedule.days]),
                    schedule.state,
                    schedule.run_count,
                    to_iso(schedule.next_run_at),
                    to_iso(schedule.last_run_at),
                    to_iso(schedule.created_at or utcnow()),
                    json.dumps(sorted(schedule.overrides)),
                ),
            )
            for position, group_id in enumerate(schedule.group_ids):
                connection.execute(
                    "INSERT INTO schedule_targets (schedule_id, group_id, position) "
                    "SELECT ?, ?, ? WHERE EXISTS "
                    "(SELECT 1 FROM groups WHERE id = ? AND archived = 0)",
                    (schedule.id, group_id, position, group_id),
                )
        return True

    def due(self, now: datetime) -> list[Schedule]:
        """Active schedules whose moment has arrived, oldest slot first."""
        rows = self.db.query(
            "SELECT * FROM schedules WHERE state = ? "
            "  AND (next_run_at IS NULL OR next_run_at <= ?) "
            "ORDER BY COALESCE(next_run_at, created_at), id",
            (SCHEDULE_ACTIVE, to_iso(now)),
        )
        return self._hydrate(rows)
