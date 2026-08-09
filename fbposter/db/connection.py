"""Connection management.

The UI thread and the Phase 5 posting worker both read and write, so each
thread gets its own connection. Sharing one across threads is what
`check_same_thread` exists to stop, and the alternative -- a global lock around
every query -- would let a slow write stall the window.

WAL matters for the same reason: with it, the UI can read the queue while the
worker is committing a result, instead of blocking on it.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .schema import apply_migrations

BUSY_TIMEOUT_MS = 5000


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self._local = threading.local()
        # Migrate once, on the connection belonging to the creating thread.
        apply_migrations(self.connection)

    # -- connections -------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """This thread's connection, opened on first use."""
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            return existing

        connection = sqlite3.connect(
            self.path,
            timeout=BUSY_TIMEOUT_MS / 1000,
            isolation_level=None,  # explicit transactions via `with connection`
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        self._local.connection = connection
        return connection

    def close(self) -> None:
        """Close this thread's connection. Other threads close their own."""
        existing = getattr(self._local, "connection", None)
        if existing is not None:
            existing.close()
            self._local.connection = None

    # -- transactions ------------------------------------------------------
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run statements as one all-or-nothing unit.

        Connections are opened with isolation_level=None, which means autocommit
        and means `with connection:` does NOT start a transaction -- it commits
        a transaction that was never begun, so a later failure leaves earlier
        statements written. BEGIN has to be explicit.

        IMMEDIATE takes the write lock up front rather than upgrading mid-way,
        which is what prevents the UI thread and the worker thread deadlocking
        against each other under WAL.
        """
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    # -- helpers -----------------------------------------------------------
    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        return self.connection.execute(sql, params)

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self.connection.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def write(self, sql: str, params: tuple = ()) -> int:
        """Run a single statement in its own transaction; returns lastrowid."""
        with self.transaction() as connection:
            return connection.execute(sql, params).lastrowid
