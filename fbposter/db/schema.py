"""Schema and migrations.

Migrations are an ordered list applied against PRAGMA user_version. That is
more machinery than one release needs, but the alternative is discovering at
Phase 5 that there is no way to add a column without wiping the user's groups.

To change the schema: append a new entry. Never edit an existing one -- it has
already run on the user's database.
"""

from __future__ import annotations

import sqlite3
from typing import Callable

# Defaults seeded on first run; confirmed with the user (see README section 7).
DEFAULT_SETTINGS = {
    "daily_cap": "25",
    # Hours are Israel local time, never UTC -- see fbposter/clock.py.
    "posting_window_start_hour": "8",
    "posting_window_end_hour": "23",
    # Lowered from 24 by the user; migration 005 carries the change onto
    # databases that were seeded with the old value.
    "default_cooldown_hours": "8",
    "posting_timezone": "Asia/Jerusalem",
    # How long a finished batch stays on the queue screen. A view filter
    # only -- the rows themselves are never deleted, because the repeated
    # -text guard and the daily cap both read them.
    "queue_retention_hours": "24",
}

_MIGRATION_001 = """
CREATE TABLE groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    identifier      TEXT    NOT NULL UNIQUE,
    url             TEXT    NOT NULL,
    name            TEXT    NOT NULL DEFAULT '',
    cooldown_hours  INTEGER NOT NULL DEFAULT 24,
    last_posted_at  TEXT,
    notes           TEXT    NOT NULL DEFAULT '',
    archived        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);

CREATE TABLE templates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL UNIQUE,
    body         TEXT NOT NULL,
    media_paths  TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    body           TEXT NOT NULL,
    media_paths    TEXT NOT NULL DEFAULT '[]',
    scheduled_for  TEXT,
    -- Added by migration 003 for databases that predate it.
    state          TEXT NOT NULL DEFAULT 'pending',
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    error          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE task_targets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL REFERENCES tasks(id)  ON DELETE CASCADE,
    group_id      INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    body          TEXT    NOT NULL,
    state         TEXT    NOT NULL DEFAULT 'pending',
    attempted_at  TEXT,
    posted_at     TEXT,
    post_url      TEXT    NOT NULL DEFAULT '',
    error         TEXT    NOT NULL DEFAULT '',
    -- The idempotency guard. One batch can never target the same group twice,
    -- enforced by the database rather than by hopeful application code.
    UNIQUE (task_id, group_id)
);

CREATE INDEX idx_targets_task  ON task_targets(task_id);
CREATE INDEX idx_targets_state ON task_targets(state);
CREATE INDEX idx_targets_posted ON task_targets(posted_at);
CREATE INDEX idx_tasks_state   ON tasks(state);

CREATE TABLE settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def _migration_001(connection: sqlite3.Connection) -> None:
    connection.executescript(_MIGRATION_001)
    connection.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        list(DEFAULT_SETTINGS.items()),
    )


def _migration_002(connection: sqlite3.Connection) -> None:
    """Record the posting time zone.

    The window hours were always meant as Israel local time, but the guard read
    them against UTC -- three hours out. Storing the zone makes the intent
    explicit for databases created before the fix.
    """
    connection.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('posting_timezone', ?)",
        (DEFAULT_SETTINGS["posting_timezone"],),
    )


def _migration_003(connection: sqlite3.Connection) -> None:
    """Give a task somewhere to record when the worker may next touch it.

    This is where the 10-25 minute gap between groups lives. Keeping it in the
    database rather than in a sleep() is what makes a batch survive closing the
    app or the machine suspending: on restart the worker re-reads an absolute
    instant instead of resuming a countdown that stopped ticking.
    """
    connection.execute("ALTER TABLE tasks ADD COLUMN resume_at TEXT")
    connection.execute("CREATE INDEX idx_tasks_resume ON tasks(resume_at)")


_MIGRATION_004 = """
CREATE TABLE schedules (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL DEFAULT '',
    -- The wordings this schedule rotates through, newest run taking the next
    -- one. A single body would be refused by the repeated-text guard on the
    -- second run, so the plural is the whole point.
    bodies       TEXT    NOT NULL DEFAULT '[]',
    media_paths  TEXT    NOT NULL DEFAULT '[]',
    -- "HH:MM" strings in Israel local time, and days as 0=Monday..6=Sunday
    -- with an empty list meaning every day. Wall-clock rather than an interval
    -- in seconds, so 09:00 stays 09:00 across a daylight-saving change.
    times        TEXT    NOT NULL DEFAULT '[]',
    days         TEXT    NOT NULL DEFAULT '[]',
    state        TEXT    NOT NULL DEFAULT 'active',
    run_count    INTEGER NOT NULL DEFAULT 0,
    next_run_at  TEXT,
    last_run_at  TEXT,
    created_at   TEXT    NOT NULL
);

CREATE TABLE schedule_targets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id  INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    group_id     INTEGER NOT NULL REFERENCES groups(id)    ON DELETE CASCADE,
    position     INTEGER NOT NULL,
    UNIQUE (schedule_id, group_id)
);

CREATE INDEX idx_schedules_next    ON schedules(next_run_at);
CREATE INDEX idx_schedule_targets  ON schedule_targets(schedule_id);
"""


def _migration_004(connection: sqlite3.Connection) -> None:
    """Repeating posts.

    A schedule is a definition, not a queue entry: when it comes due the worker
    materialises an ordinary task from it. That keeps one posting path -- the
    serial worker, the inter-group gap, crash recovery and the guards all apply
    unchanged -- instead of a second one that would have to re-earn its safety.
    """
    connection.executescript(_MIGRATION_004)
    # Which schedule spawned a batch, so the queue can say so and so a schedule
    # never stacks a second batch on top of one still waiting to go out.
    connection.execute("ALTER TABLE tasks ADD COLUMN schedule_id INTEGER")
    connection.execute("CREATE INDEX idx_tasks_schedule ON tasks(schedule_id)")


# What the per-group cooldown used to default to, before the user lowered it.
PREVIOUS_DEFAULT_COOLDOWN_HOURS = 24


def _migration_005(connection: sqlite3.Connection) -> None:
    """Lower the default per-group cooldown from 24 hours to 8.

    Changing DEFAULT_SETTINGS alone would do nothing here: settings are seeded
    once, on first run, and the user's database was seeded long ago. So the
    stored value is updated too.

    Existing groups are moved only if they are still sitting on the old default.
    A group the user deliberately set to something else is left alone -- there
    is no way to tell "24 because I chose it" from "24 because it was the
    default", so the safer reading is that an untouched 24 was the default.
    """
    connection.execute(
        "UPDATE settings SET value = ? WHERE key = 'default_cooldown_hours' AND value = ?",
        (DEFAULT_SETTINGS["default_cooldown_hours"], str(PREVIOUS_DEFAULT_COOLDOWN_HOURS)),
    )
    connection.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('default_cooldown_hours', ?)",
        (DEFAULT_SETTINGS["default_cooldown_hours"],),
    )
    connection.execute(
        "UPDATE groups SET cooldown_hours = ? WHERE cooldown_hours = ?",
        (
            int(DEFAULT_SETTINGS["default_cooldown_hours"]),
            PREVIOUS_DEFAULT_COOLDOWN_HOURS,
        ),
    )


# Index i applies when user_version == i, and bumps it to i + 1.
MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migration_001,
    _migration_002,
    _migration_003,
    _migration_004,
    _migration_005,
]

LATEST_VERSION = len(MIGRATIONS)


def current_version(connection: sqlite3.Connection) -> int:
    return connection.execute("PRAGMA user_version").fetchone()[0]


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Bring the database up to LATEST_VERSION. Safe to call on every open."""
    version = current_version(connection)
    for index in range(version, LATEST_VERSION):
        with connection:
            MIGRATIONS[index](connection)
            # No parameter binding for PRAGMA, hence the f-string; the value is
            # a loop index, never user input.
            connection.execute(f"PRAGMA user_version = {index + 1}")
    return current_version(connection)
