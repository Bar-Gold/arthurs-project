"""Query layer.

One repository per table group. Repositories return model objects and never
make policy decisions. There are no posting rules (the user's choice,
2026-10-18), and the Dashboard's numbers are worked out in fbposter/stats.py.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Iterable, NamedTuple, Sequence

from .. import clock
from ..errors import DuplicateGroup, InvalidGroupURL
from ..groups import parse_group_url
from ..text import strip_invisible
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
    GroupSet,
    PostRecord,
    Schedule,
    Task,
    TaskTarget,
    Template,
    from_iso,
    to_iso,
    utcnow,
)


# How far back `recent_bodies` looks, per group: what tells the worker these
# words were sent to a group before (`PostRequest.sent_before`). The history
# prune keeps at least this many posted bodies per group for ever -- the two
# numbers must be the same one, or pruning quietly weakens that check.
RECENT_BODIES_LIMIT = 20

# Every state meaning the words reached the group, or may have: posted,
# waiting for an admin, or stopped after Post was clicked. All three count as
# sent on the Dashboard and for `sent_before` (the user's choice for the last,
# 2026-10-07).
SENT_STATES = (TARGET_DONE, TARGET_AWAITING_APPROVAL, TARGET_UNCONFIRMED)

# "Leave this column as it is", where None already means "clear it".
KEEP = object()

# What a queued post records for a group the user removed before its turn.
GROUP_REMOVED = "You removed this group from your list before its turn."

# A member count that could not be read is tried again after this long, not
# on every visit to the Groups screen (`GroupRepo.missing_details`).
MEMBERS_RETRY = timedelta(days=1)

# Batch ids per statement when the prune counts what it is about to delete,
# well inside SQLite's limit on bound parameters.
PRUNE_CHUNK = 500


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
    # (set_id, position) of every group set it was taken out of.
    set_rows: tuple = ()
    # Ids of the queued posts it was taken out of.
    batch_ids: tuple = ()

    @property
    def sets(self) -> int:
        return len(self.set_rows)


class Replacement(NamedTuple):
    """What `GroupRepo.replace_with` changed, for the Groups screen to say."""

    added: list
    brought_back: list
    # group id -> what removing it took it out of.
    removals: dict


def _insert_found(connection, identifier: str, url: str, name: str, cooldown: int) -> int:
    """Store one group read off Facebook. Returns its id."""
    cursor = connection.execute(
        "INSERT INTO groups (identifier, url, name, cooldown_hours, notes, created_at) "
        "VALUES (?, ?, ?, ?, '', ?)",
        (identifier, url, name, cooldown, to_iso(utcnow())),
    )
    return cursor.lastrowid


class AccountReset(NamedTuple):
    """What switching Facebook account deleted, for the screen to say."""

    waiting: int
    repeating: int
    sets: int


def start_new_account(db: Database) -> AccountReset:
    """Delete everything that belonged to the account just signed out.

    The user's choices (2026-10-19): a new account starts with nothing of the
    old one's but the templates. Every batch goes, waiting or finished, and
    with it the whole posting history; every repeating post; every group set;
    the counts the history prune kept (`post_totals`); and the removed groups,
    which were only kept for their history. The groups still listed stay until
    the new account's groups are read (`GroupRepo.replace_with`), with their
    last-posted time cleared.

    That also closes the gap between the switch and that read: nothing is left
    that could post as the new account to the old one's groups. The price is
    that `sent_before` forgets the old account's posts, so in a group both
    accounts are in, an old post with the same words is no longer known about.

    One transaction: a switch that emptied the queue but left a repeating post
    to fill it again would not be a fresh start.
    """
    with db.transaction() as connection:
        waiting = connection.execute(
            "SELECT COUNT(*) FROM tasks WHERE state IN (?, ?)", (TASK_PENDING, TASK_RUNNING)
        ).fetchone()[0]
        # task_targets go with their tasks, schedule_targets and schedule_sets
        # with their schedules, and members with their sets: ON DELETE CASCADE.
        connection.execute("DELETE FROM tasks")
        repeating = connection.execute("DELETE FROM schedules").rowcount
        sets = connection.execute("DELETE FROM group_sets").rowcount
        connection.execute("DELETE FROM post_totals")
        connection.execute("DELETE FROM groups WHERE archived = 1")
        connection.execute("UPDATE groups SET last_posted_at = NULL")
    return AccountReset(waiting=waiting, repeating=repeating, sets=sets)


class SetDeletion(NamedTuple):
    """What deleting a group set changed, so Undo can put exactly that back."""

    group_set: GroupSet
    # (schedule_id, position) of every repeating post that followed the set.
    links: tuple = ()
    # (schedule_id, group_id) of the plain groups the delete gave those posts.
    added: tuple = ()


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
                added.append(_insert_found(connection, identifier, url, name, default_cooldown))
        return [group for group in (self.get(group_id) for group_id in added) if group]

    def replace_with(self, found: Iterable[tuple[str, str, str]]) -> Replacement:
        """Make the list exactly the groups read off Facebook, after an account switch.

        The user's choices (2026-10-19): once another account is logged in, the
        list holds its groups and only its groups. A group it is not in is
        removed exactly as the Remove button removes one (taken out of
        repeating posts, queued posts and group sets), and then deleted
        outright unless something was sent to it since the switch: the switch
        already deleted the old account's history (`start_new_account`), so
        there is nothing to keep it for, and it must not sit in the new
        account's removed groups. A group it is in that was removed earlier
        comes back. A group in both accounts is not touched.

        One transaction: a list half replaced would be posted to as it stood.
        The caller only asks once the whole list was read (`FoundGroups.complete`);
        a partial read here would remove groups the account is in.
        """
        found = list(found)
        wanted = {identifier.lower() for identifier, _url, _name in found}
        default_cooldown = SettingsRepo(self.db).get_int("default_cooldown_hours", 8)
        added: list[int] = []
        back: list[int] = []
        removals: dict[int, Removal] = {}
        with self.db.transaction() as connection:
            for identifier, url, name in found:
                known = connection.execute(
                    "SELECT id, archived FROM groups WHERE lower(identifier) = lower(?)",
                    (identifier,),
                ).fetchone()
                if known is None:
                    added.append(_insert_found(connection, identifier, url, name, default_cooldown))
                elif known["archived"]:
                    connection.execute(
                        "UPDATE groups SET archived = 0, "
                        "name = CASE WHEN name = '' THEN ? ELSE name END WHERE id = ?",
                        (name, known["id"]),
                    )
                    back.append(known["id"])
            listed = connection.execute(
                "SELECT id, identifier FROM groups WHERE archived = 0 ORDER BY id"
            ).fetchall()
            for row in listed:
                if row["identifier"].lower() not in wanted:
                    removals[row["id"]] = self._archive(connection, row["id"])
                    connection.execute(
                        "DELETE FROM groups WHERE id = ? AND NOT EXISTS "
                        "(SELECT 1 FROM task_targets WHERE group_id = ?)",
                        (row["id"], row["id"]),
                    )
        groups = lambda ids: [g for g in (self.get(i) for i in ids) if g]  # noqa: E731
        return Replacement(added=groups(added), brought_back=groups(back), removals=removals)

    def remove(self, group_id: int) -> Removal:
        """Take a group out of the list without destroying what went to it.

        This was a DELETE, and `task_targets.group_id` is ON DELETE CASCADE --
        so removing a group took every post ever made to it along with it, and
        that is exactly the window `recent_bodies` reads. It then blocked the
        same advert going to a group twice; now it tells the worker the words
        are already in that group's feed (`sent_before`), and the Dashboard
        counts them. Two clicks in the UI, no warning, nothing on screen to
        show it had gone.

        Archiving hides the row and keeps the history. `list()` already
        excludes archived groups, so the screens need no change; `bring_back`
        un-archives, from the Removed list on the Groups screen.

        It also takes the group out of every repeating post and every queued
        post still to go out, and adding it back puts it in neither: the user's
        choice (2026-09-30 and 2026-10-01). A removed group used to stay in both
        and be posted to again the moment it was re-added. Group sets are the
        same: a repeating post follows its sets, so a removed group left in
        one would be posted to again by "Bring back" alone.

        In a queued post only a *pending* turn is taken: one already running is
        mid-post and cannot be taken back. The turn is marked skipped rather
        than deleted, so the Queue says what became of it, and the conditional
        state is what stops this racing the worker's `claim_target`. A queued
        post left with nothing to do is finished here rather than at its time:
        cancelled if it never started, done if it did, as the worker would.
        """
        with self.db.transaction() as connection:
            return self._archive(connection, group_id)

    @staticmethod
    def _archive(connection, group_id: int) -> Removal:
        """`remove`, inside a transaction the caller already holds."""
        unfinished = (TASK_PENDING, TASK_RUNNING)
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
        set_rows = tuple(
            (row["set_id"], row["position"])
            for row in connection.execute(
                "SELECT set_id, position FROM group_set_members WHERE group_id = ?",
                (group_id,),
            )
        )
        connection.execute("DELETE FROM group_set_members WHERE group_id = ?", (group_id,))

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
            set_rows=set_rows,
            batch_ids=tuple(batches),
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
            for set_id, position in removal.set_rows:
                connection.execute(
                    "INSERT OR IGNORE INTO group_set_members (set_id, group_id, position) "
                    "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM group_sets WHERE id = ?)",
                    (set_id, group_id, position, set_id),
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
        sent, but its place in repeating posts, queued posts and group sets
        does not. Only Undo,
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

    def unfinished(self) -> list[Task]:
        """Batches still due to go out, the soonest first: the Dashboard's
        "Going out", where each can be cancelled."""
        rows = self.db.query(
            "SELECT * FROM tasks WHERE state IN (?, ?) "
            "ORDER BY state = ? DESC, COALESCE(scheduled_for, created_at), id",
            (TASK_PENDING, TASK_RUNNING, TASK_RUNNING),
        )
        return [Task.from_row(row) for row in rows]

    def recent_targets(self, limit: int = 15) -> list[TaskTarget]:
        """The last groups the worker reached, newest first, with their names.

        A turn that never came (still pending, or left behind by a stopped or
        cancelled batch) has no time on it and is not activity.
        """
        rows = self.db.query(
            "SELECT t.*, g.identifier AS group_identifier, g.name AS group_name "
            "FROM task_targets t JOIN groups g ON g.id = t.group_id "
            "WHERE t.state != ? AND COALESCE(t.posted_at, t.attempted_at) IS NOT NULL "
            "ORDER BY COALESCE(t.posted_at, t.attempted_at) DESC, t.id DESC LIMIT ?",
            (TARGET_PENDING, limit),
        )
        return [TaskTarget.from_row(row) for row in rows]

    def history_records(self) -> list[PostRecord]:
        """Every group's turn still on record, reduced to what is counted.

        One query for the whole Dashboard. Together with `archived_totals`
        it is everything ever sent: the prune moves counts there before it
        deletes rows here, so nothing is counted twice or lost.
        """
        rows = self.db.query(
            "SELECT group_id, state, body, COALESCE(posted_at, attempted_at) AS at "
            "FROM task_targets"
        )
        return [
            PostRecord(
                group_id=row["group_id"],
                state=row["state"],
                at=from_iso(row["at"]),
                body=row["body"] or "",
            )
            for row in rows
        ]

    def archived_totals(self) -> dict[tuple[int, str], tuple[int, datetime | None]]:
        """What the prune has deleted, as {(group, state): (count, earliest)}."""
        return {
            (row["group_id"], row["state"]): (int(row["count"]), from_iso(row["first_at"]))
            for row in self.db.query(
                "SELECT group_id, state, count, first_at FROM post_totals"
            )
        }

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
          follow-up's only record of it, and the post would still be sitting
          in the group's moderation queue.
        * **The newest `keep_per_group` posted bodies for each group**, however
          old. That is exactly what `GroupRepo.recent_bodies` reads, so the
          repeated-text guard keeps refusing wording a group has already had --
          which is the app's main protection and worth a few kilobytes for ever.
        * **Everything inside the window**, which is far wider than the daily
          cap's local-midnight lookback, so the cap is never miscounted.

        What is deleted is counted first, into `post_totals`, so the
        Dashboard's "all time" is not deleted with it.

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
            # Counted before they go, in the same transaction, so the
            # Dashboard's "all time" never shrinks (the user's choice,
            # 2026-10-14): a prune that failed half way must not leave rows
            # both counted and still there.
            for start in range(0, len(doomed), PRUNE_CHUNK):
                chunk = doomed[start:start + PRUNE_CHUNK]
                marks = ",".join("?" * len(chunk))
                connection.execute(
                    "INSERT INTO post_totals (group_id, state, count, first_at) "
                    "SELECT group_id, state, COUNT(*), "
                    "       MIN(COALESCE(posted_at, attempted_at)) "
                    f"FROM task_targets WHERE task_id IN ({marks}) "
                    "GROUP BY group_id, state "
                    "ON CONFLICT (group_id, state) DO UPDATE SET "
                    "  count = count + excluded.count, "
                    "  first_at = CASE "
                    "    WHEN first_at IS NULL THEN excluded.first_at "
                    "    WHEN excluded.first_at IS NULL THEN first_at "
                    "    ELSE MIN(first_at, excluded.first_at) END",
                    tuple(chunk),
                )
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

    def _set_ids(self, schedule_id: int) -> list[int]:
        return [
            row["set_id"]
            for row in self.db.query(
                "SELECT set_id FROM schedule_sets WHERE schedule_id = ? ORDER BY position",
                (schedule_id,),
            )
        ]

    def _recipient_ids(self, schedule_id: int) -> list[int]:
        """Every group a run goes to, read now: a set is followed, not copied.

        The plain groups first, in their order, then each set's groups in the
        set's order. A group in two places goes once. The order is part of
        which wording each group gets (`run_count + position`), so it must not
        depend on anything but what is stored.
        """
        plain = self.db.query(
            "SELECT st.group_id FROM schedule_targets st "
            "JOIN groups g ON g.id = st.group_id "
            "WHERE st.schedule_id = ? AND g.archived = 0 ORDER BY st.position",
            (schedule_id,),
        )
        from_sets = self.db.query(
            "SELECT m.group_id FROM schedule_sets ss "
            "JOIN group_set_members m ON m.set_id = ss.set_id "
            "JOIN groups g ON g.id = m.group_id "
            "WHERE ss.schedule_id = ? AND g.archived = 0 "
            "ORDER BY ss.position, m.position",
            (schedule_id,),
        )
        return list(dict.fromkeys(row["group_id"] for row in (*plain, *from_sets)))

    def _from_row(self, row: Any) -> Schedule:
        schedule_id = row["id"]
        return Schedule.from_row(
            row,
            self._group_ids(schedule_id),
            self._set_ids(schedule_id),
            self._recipient_ids(schedule_id),
        )

    def _hydrate(self, rows: Sequence[Any]) -> list[Schedule]:
        return [self._from_row(row) for row in rows]

    def list(self) -> list[Schedule]:
        return self._hydrate(self.db.query("SELECT * FROM schedules ORDER BY id"))

    def get(self, schedule_id: int) -> Schedule | None:
        row = self.db.query_one("SELECT * FROM schedules WHERE id = ?", (schedule_id,))
        if row is None:
            return None
        return self._from_row(row)

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
        set_ids: Sequence[int] = (),
    ) -> Schedule:
        """Write a schedule and its groups in one transaction.

        A schedule with no groups, or groups with no schedule, would sit in the
        database doing nothing visible -- so, as with tasks, both halves land
        together or neither does. `set_ids` are group sets it follows, on top
        of the groups ticked one by one. Pictures or a video alone need no
        wording (the user's choice, 2026-10-18).
        """
        cleaned = [b for b in bodies if b.strip()]
        if not cleaned and not media_paths:
            raise ValueError("A repeating post needs some text or a picture or video.")
        if not group_ids and not set_ids:
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
            _write_schedule_sets(connection, schedule_id, set_ids)

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
        next_run_at: datetime | None | object = KEEP,
        set_ids: Sequence[int] = (),
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
        if not cleaned and not media_paths:
            raise ValueError("A repeating post needs some text or a picture or video.")
        if not group_ids and not set_ids:
            raise ValueError("A repeating post needs at least one group.")
        if not times:
            raise ValueError("A repeating post needs at least one time of day.")

        columns = "name = ?, bodies = ?, media_paths = ?, times = ?, days = ?"
        values: list[Any] = [
            name,
            json.dumps(list(cleaned)),
            json.dumps([str(p) for p in media_paths]),
            json.dumps(list(times)),
            json.dumps([int(d) for d in days]),
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
            connection.execute("DELETE FROM schedule_sets WHERE schedule_id = ?", (schedule_id,))
            _write_schedule_sets(connection, schedule_id, set_ids)

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
                "state, run_count, next_run_at, last_run_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
            for position, group_id in enumerate(schedule.group_ids):
                connection.execute(
                    "INSERT INTO schedule_targets (schedule_id, group_id, position) "
                    "SELECT ?, ?, ? WHERE EXISTS "
                    "(SELECT 1 FROM groups WHERE id = ? AND archived = 0)",
                    (schedule.id, group_id, position, group_id),
                )
            _write_schedule_sets(connection, schedule.id, schedule.set_ids)
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


def _write_schedule_sets(connection, schedule_id: int, set_ids: Sequence[int]) -> None:
    """Link a repeating post to its group sets, in order.

    A set deleted since the screen was drawn is left out rather than linked
    to nothing.
    """
    for position, set_id in enumerate(dict.fromkeys(set_ids)):
        connection.execute(
            "INSERT INTO schedule_sets (schedule_id, set_id, position) "
            "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM group_sets WHERE id = ?)",
            (schedule_id, set_id, position, set_id),
        )


class GroupSetRepo:
    """Group sets: named collections of groups, picked as one item.

    A set is not a way of posting. Now and Once copy its groups into an
    ordinary batch when the button is pressed, and a repeating post follows it
    through `schedule_sets`: `ScheduleRepo` reads the set's groups afresh for
    every run. So the worker and the queue never see a set at all.
    """

    def __init__(self, db: Database) -> None:
        self.db = db

    def _members(self) -> dict[int, list[int]]:
        """Every set's groups in one query, removed groups left out."""
        members: dict[int, list[int]] = {}
        for row in self.db.query(
            "SELECT m.set_id, m.group_id FROM group_set_members m "
            "JOIN groups g ON g.id = m.group_id "
            "WHERE g.archived = 0 ORDER BY m.set_id, m.position"
        ):
            members.setdefault(row["set_id"], []).append(row["group_id"])
        return members

    def list(self) -> list[GroupSet]:
        """Every set, by name: the order they are offered in."""
        members = self._members()
        rows = self.db.query("SELECT * FROM group_sets ORDER BY name COLLATE NOCASE, id")
        return [GroupSet.from_row(row, members.get(row["id"], ())) for row in rows]

    def get(self, set_id: int) -> GroupSet | None:
        row = self.db.query_one("SELECT * FROM group_sets WHERE id = ?", (set_id,))
        if row is None:
            return None
        return GroupSet.from_row(row, self._members().get(set_id, ()))

    def create(self, name: str, group_ids: Sequence[int]) -> GroupSet:
        name, wanted = _check_set(name, group_ids)
        with self.db.transaction() as connection:
            _refuse_taken(connection, name)
            try:
                set_id = connection.execute(
                    "INSERT INTO group_sets (name, created_at) VALUES (?, ?)",
                    (name, to_iso(utcnow())),
                ).lastrowid
            except sqlite3.IntegrityError as exc:  # lost a race for the name
                raise ValueError(_taken(name)) from exc
            _write_members(connection, set_id, wanted)
        stored = self.get(set_id)
        assert stored is not None
        return stored

    def update(self, set_id: int, name: str, group_ids: Sequence[int]) -> GroupSet:
        """Rename a set and change its groups, in one transaction.

        Groups already in it keep their place and new ones go after them, so
        adding a group does not move the others: their place is part of which
        wording each one gets from a repeating post that follows the set.
        """
        name, wanted = _check_set(name, group_ids)
        with self.db.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM group_sets WHERE id = ?", (set_id,)
            ).fetchone() is None:
                raise ValueError("That group set no longer exists.")
            _refuse_taken(connection, name, set_id)
            connection.execute("UPDATE group_sets SET name = ? WHERE id = ?", (name, set_id))
            before = [
                row["group_id"]
                for row in connection.execute(
                    "SELECT group_id FROM group_set_members WHERE set_id = ? ORDER BY position",
                    (set_id,),
                )
            ]
            ordered = [g for g in before if g in wanted] + [g for g in wanted if g not in before]
            connection.execute("DELETE FROM group_set_members WHERE set_id = ?", (set_id,))
            _write_members(connection, set_id, ordered)
        stored = self.get(set_id)
        assert stored is not None
        return stored

    def used_by(self, set_id: int) -> list[int]:
        """The repeating posts that follow this set."""
        return [
            row["schedule_id"]
            for row in self.db.query(
                "SELECT schedule_id FROM schedule_sets WHERE set_id = ? ORDER BY schedule_id",
                (set_id,),
            )
        ]

    def delete(self, set_id: int) -> SetDeletion | None:
        """Delete a set. The repeating posts that followed it keep its groups.

        The user's choice (2026-10-15): nothing stops posting by surprise. In
        the same transaction, each of those posts gets the set's groups as
        ordinary groups, after the ones it already had. None if it is gone.
        """
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM group_sets WHERE id = ?", (set_id,)
            ).fetchone()
            if row is None:
                return None
            members = [
                member["group_id"]
                for member in connection.execute(
                    "SELECT m.group_id FROM group_set_members m "
                    "JOIN groups g ON g.id = m.group_id "
                    "WHERE m.set_id = ? AND g.archived = 0 ORDER BY m.position",
                    (set_id,),
                )
            ]
            group_set = GroupSet.from_row(row, members)
            links = tuple(
                (link["schedule_id"], link["position"])
                for link in connection.execute(
                    "SELECT schedule_id, position FROM schedule_sets "
                    "WHERE set_id = ? ORDER BY schedule_id",
                    (set_id,),
                )
            )
            added = []
            for schedule_id, _position in links:
                plain = {
                    target["group_id"]
                    for target in connection.execute(
                        "SELECT group_id FROM schedule_targets WHERE schedule_id = ?",
                        (schedule_id,),
                    )
                }
                position = connection.execute(
                    "SELECT COALESCE(MAX(position) + 1, 0) FROM schedule_targets "
                    "WHERE schedule_id = ?",
                    (schedule_id,),
                ).fetchone()[0]
                for group_id in members:
                    if group_id in plain:
                        continue
                    connection.execute(
                        "INSERT INTO schedule_targets (schedule_id, group_id, position) "
                        "VALUES (?, ?, ?)",
                        (schedule_id, group_id, position),
                    )
                    added.append((schedule_id, group_id))
                    position += 1
            # The members and the links go with it (ON DELETE CASCADE).
            connection.execute("DELETE FROM group_sets WHERE id = ?", (set_id,))
        return SetDeletion(group_set=group_set, links=links, added=tuple(added))

    def restore(self, deletion: SetDeletion) -> bool:
        """Undo a delete, straight after it. False if it can no longer be.

        The set comes back under its own id, with its groups and its place in
        the repeating posts that followed it, and those posts lose the plain
        groups the delete gave them. Only what is still there is put back: a
        group removed since, or a post deleted since, stays gone.
        """
        group_set = deletion.group_set
        with self.db.transaction() as connection:
            if connection.execute(
                "SELECT 1 FROM group_sets WHERE id = ? OR name = ?",
                (group_set.id, group_set.name),
            ).fetchone():
                return False
            connection.execute(
                "INSERT INTO group_sets (id, name, created_at) VALUES (?, ?, ?)",
                (group_set.id, group_set.name, to_iso(group_set.created_at or utcnow())),
            )
            _write_members(connection, group_set.id, group_set.group_ids, require=False)
            for schedule_id, position in deletion.links:
                connection.execute(
                    "INSERT OR IGNORE INTO schedule_sets (schedule_id, set_id, position) "
                    "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM schedules WHERE id = ?)",
                    (schedule_id, group_set.id, position, schedule_id),
                )
            for schedule_id, group_id in deletion.added:
                connection.execute(
                    "DELETE FROM schedule_targets WHERE schedule_id = ? AND group_id = ?",
                    (schedule_id, group_id),
                )
        return True


def _taken(name: str) -> str:
    return f"There is already a group set called \"{name}\"."


def _check_set(name: str, group_ids: Sequence[int]) -> tuple[str, list[int]]:
    """The name as it will be stored, and the groups, or ValueError.

    Invisible marks are stripped: the name is compared with the others, and a
    pasted direction mark would make two names that look the same different.
    """
    cleaned = " ".join(strip_invisible(name).split())
    if not cleaned:
        raise ValueError("A group set needs a name.")
    wanted = list(dict.fromkeys(group_ids))
    if not wanted:
        raise ValueError("A group set needs at least one group.")
    return cleaned, wanted


def _refuse_taken(connection, name: str, set_id: int = 0) -> None:
    # The column is COLLATE NOCASE, so this comparison ignores case too.
    if connection.execute(
        "SELECT 1 FROM group_sets WHERE name = ? AND id != ?", (name, set_id)
    ).fetchone():
        raise ValueError(_taken(name))


def _write_members(
    connection, set_id: int, group_ids: Sequence[int], require: bool = True
) -> None:
    """A set's groups, in order. Only groups still on the list go in."""
    written = 0
    for position, group_id in enumerate(group_ids):
        written += connection.execute(
            "INSERT INTO group_set_members (set_id, group_id, position) "
            "SELECT ?, ?, ? WHERE EXISTS (SELECT 1 FROM groups WHERE id = ? AND archived = 0)",
            (set_id, group_id, position, group_id),
        ).rowcount
    if require and not written:
        raise ValueError("A group set needs at least one group.")
