"""Tests for the SQLite layer.

Every test runs against a throwaway database in tmp_path. Nothing here touches
the user's real C:\\FBAutomation\\fbposter.db.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import timedelta

import pytest

from fbposter.db import Database
from fbposter.db.models import TARGET_DONE, utcnow
from fbposter.db.repo import GroupRepo, SettingsRepo, TaskRepo, TemplateRepo
from fbposter.db.schema import DEFAULT_SETTINGS, LATEST_VERSION, current_version
from fbposter.errors import DuplicateGroup, InvalidGroupURL

# Read off the schema rather than written out, so lowering it again is a
# one-line change rather than a hunt through the suite.
DEFAULT_COOLDOWN = int(DEFAULT_SETTINGS["default_cooldown_hours"])


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def groups(db):
    return GroupRepo(db)


class TestSchema:
    def test_migrations_bring_a_new_database_up_to_date(self, db):
        assert current_version(db.connection) == LATEST_VERSION

    def test_the_expected_tables_exist(self, db):
        rows = db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        names = {row["name"] for row in rows}
        assert {"groups", "templates", "tasks", "task_targets", "settings"} <= names

    def test_the_cut_tables_are_not_created(self):
        """tags, group_tags and run_log were cut from v1 on purpose."""
        from fbposter.db import schema

        for cut in ("tags", "group_tags", "run_log"):
            assert cut not in schema._MIGRATION_001

    def test_reopening_does_not_rerun_migrations(self, tmp_path):
        path = tmp_path / "reopen.db"
        first = Database(path)
        GroupRepo(first).add_from_url("https://www.facebook.com/groups/123")
        first.close()

        second = Database(path)
        assert current_version(second.connection) == LATEST_VERSION
        assert len(GroupRepo(second).list()) == 1
        second.close()

    def test_an_older_database_upgrades_without_losing_data(self, tmp_path):
        """The entire justification for having migrations at all.

        Builds a database at version 1, puts a group in it, then opens it
        normally and checks that 002 and 003 applied and the group survived.
        """
        import sqlite3

        from fbposter.db import schema

        path = tmp_path / "old.db"
        raw = sqlite3.connect(path, isolation_level=None)
        # No explicit transaction: _migration_001 uses executescript, which
        # implicitly commits, so wrapping it would leave nothing to commit.
        schema._migration_001(raw)
        raw.execute("PRAGMA user_version = 1")
        raw.execute(
            "INSERT INTO groups (identifier, url, created_at) VALUES ('old', 'u', '2026-01-01')"
        )
        raw.close()

        upgraded = Database(path)
        try:
            assert current_version(upgraded.connection) == LATEST_VERSION
            columns = [r["name"] for r in upgraded.query("PRAGMA table_info(tasks)")]
            assert "resume_at" in columns  # 003
            assert "schedule_id" in columns  # 004
            assert SettingsRepo(upgraded).get("posting_timezone") == "Asia/Jerusalem"  # 002
            assert [g.identifier for g in GroupRepo(upgraded).list()] == ["old"]
        finally:
            upgraded.close()

    def test_the_version_the_user_is_actually_on_upgrades_and_still_works(self, tmp_path):
        """Version 3 is what shipped, so 3 -> 4 is the step that runs for real.

        Reaching LATEST_VERSION is not enough on its own: the new tables have to
        be usable afterwards, on a database that already has rows in it.
        """
        import sqlite3

        from fbposter.db import schema
        from fbposter.db.repo import ScheduleRepo, TaskRepo

        path = tmp_path / "v3.db"
        raw = sqlite3.connect(path, isolation_level=None)
        for index in range(3):
            schema.MIGRATIONS[index](raw)
            raw.execute(f"PRAGMA user_version = {index + 1}")
        raw.execute(
            "INSERT INTO groups (identifier, url, created_at) VALUES ('old', 'u', '2026-01-01')"
        )
        raw.close()

        upgraded = Database(path)
        try:
            assert current_version(upgraded.connection) == LATEST_VERSION
            group = GroupRepo(upgraded).list()[0]

            schedule = ScheduleRepo(upgraded).create(
                name="Bikes", bodies=["one", "two"], group_ids=[group.id], times=["09:00"]
            )
            assert ScheduleRepo(upgraded).get(schedule.id).group_ids == [group.id]

            task = TaskRepo(upgraded).create(
                "one", [(group.id, "one")], schedule_id=schedule.id
            )
            assert TaskRepo(upgraded).get(task.id).schedule_id == schedule.id
        finally:
            upgraded.close()

    def test_the_lowered_cooldown_reaches_a_database_seeded_with_the_old_one(
        self, tmp_path
    ):
        """Changing DEFAULT_SETTINGS alone would not have touched the user.

        Settings are seeded once, on first run, so their database still held 24
        and every group with it. Migration 005 is what actually lowers it.
        """
        import sqlite3

        from fbposter.db import schema

        path = tmp_path / "cooldown.db"
        raw = sqlite3.connect(path, isolation_level=None)
        for index in range(4):
            schema.MIGRATIONS[index](raw)
            raw.execute(f"PRAGMA user_version = {index + 1}")
        # What the old default looked like, plus a group the user chose to set
        # differently.
        raw.execute(
            "UPDATE settings SET value = '24' WHERE key = 'default_cooldown_hours'"
        )
        raw.execute(
            "INSERT INTO groups (identifier, url, cooldown_hours, created_at) "
            "VALUES ('untouched', 'u', 24, '2026-01-01')"
        )
        raw.execute(
            "INSERT INTO groups (identifier, url, cooldown_hours, created_at) "
            "VALUES ('deliberate', 'u2', 48, '2026-01-01')"
        )
        raw.close()

        upgraded = Database(path)
        try:
            assert (
                SettingsRepo(upgraded).get_int("default_cooldown_hours", 0)
                == DEFAULT_COOLDOWN
            )
            stored = {g.identifier: g.cooldown_hours for g in GroupRepo(upgraded).list()}
            assert stored["untouched"] == DEFAULT_COOLDOWN
            assert stored["deliberate"] == 48, "a chosen cooldown was overwritten"
        finally:
            upgraded.close()

    def test_settings_added_after_the_database_was_made_still_appear(self, tmp_path):
        """Settings are seeded once; a key added later never showed up."""
        import sqlite3

        from fbposter.db import schema

        path = tmp_path / "seeded.db"
        raw = sqlite3.connect(path, isolation_level=None)
        schema._migration_001(raw)
        raw.execute("PRAGMA user_version = 1")
        raw.execute("DELETE FROM settings WHERE key = 'daily_cap'")
        raw.execute("UPDATE settings SET value = '99' WHERE key = 'posting_window_end_hour'")
        raw.close()

        upgraded = Database(path)
        try:
            stored = SettingsRepo(upgraded).all()
            for key in DEFAULT_SETTINGS:
                assert key in stored, f"{key} was never seeded"
            # A value the user changed is not overwritten.
            assert stored["posting_window_end_hour"] == "99"
        finally:
            upgraded.close()

    def test_foreign_keys_are_enforced(self, db):
        with pytest.raises(sqlite3.IntegrityError):
            db.write(
                "INSERT INTO task_targets (task_id, group_id, position, body) "
                "VALUES (999, 999, 0, 'x')"
            )


class TestSettings:
    def test_the_confirmed_defaults_are_seeded(self, db):
        settings = SettingsRepo(db)
        assert settings.get_int("daily_cap", 0) == 25
        assert settings.get_int("posting_window_start_hour", 0) == 8
        assert settings.get_int("posting_window_end_hour", 0) == 23
        assert settings.get_int("default_cooldown_hours", 0) == DEFAULT_COOLDOWN

    def test_set_then_get(self, db):
        settings = SettingsRepo(db)
        settings.set("daily_cap", 30)
        assert settings.get_int("daily_cap", 0) == 30

    def test_a_missing_key_falls_back(self, db):
        assert SettingsRepo(db).get_int("nope", 7) == 7

    def test_a_corrupt_value_falls_back_rather_than_crashing(self, db):
        settings = SettingsRepo(db)
        settings.set("daily_cap", "not-a-number")
        assert settings.get_int("daily_cap", 25) == 25


class TestGroups:
    def test_add_and_list(self, groups):
        groups.add_from_url("https://www.facebook.com/groups/gardening.tlv")
        assert [g.identifier for g in groups.list()] == ["gardening.tlv"]

    def test_a_bad_url_is_rejected(self, groups):
        with pytest.raises(InvalidGroupURL):
            groups.add_from_url("https://www.google.com")

    def test_the_same_group_in_another_url_form_is_a_duplicate(self, groups):
        """Canonicalisation is what stops one group being posted to twice."""
        groups.add_from_url("https://www.facebook.com/groups/123456789")
        with pytest.raises(DuplicateGroup):
            groups.add_from_url("https://m.facebook.com/groups/123456789/posts/5?ref=x")
        assert len(groups.list()) == 1

    def test_new_groups_take_the_default_cooldown(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/123")
        assert group.cooldown_hours == DEFAULT_COOLDOWN

    def test_the_cooldown_can_be_shortened_per_group(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/busy")
        groups.set_cooldown(group.id, 6)
        assert groups.get(group.id).cooldown_hours == 6

    def test_mark_posted_records_the_time(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/123")
        assert group.last_posted_at is None
        groups.mark_posted(group.id)
        assert groups.get(group.id).last_posted_at is not None

    def test_remove(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/123")
        groups.remove(group.id)
        assert groups.list() == []

    def test_display_name_falls_back_to_the_identifier(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/123")
        assert group.display_name == "123"

    def test_a_stored_name_replaces_the_identifier(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/2509198906266893")
        assert group.display_name == "2509198906266893"

        groups.set_name(group.id, "bar-test")
        assert groups.get(group.id).display_name == "bar-test"

    def test_a_hebrew_name_round_trips(self, groups):
        """Group names are not ASCII; this is the likeliest thing to break."""
        group = groups.add_from_url("https://www.facebook.com/groups/464241678849975")
        groups.set_name(group.id, "מוכרים-קונים כרטיסים להופעות")
        assert groups.get(group.id).display_name == "מוכרים-קונים כרטיסים להופעות"

    def test_missing_names_lists_only_the_unnamed(self, groups):
        first = groups.add_from_url("https://www.facebook.com/groups/111")
        groups.add_from_url("https://www.facebook.com/groups/222")
        groups.set_name(first.id, "Named")

        assert [g.identifier for g in groups.missing_names()] == ["222"]

    def test_missing_names_is_empty_once_all_are_named(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/111")
        groups.set_name(group.id, "Named")
        assert groups.missing_names() == []

    def test_a_blank_name_still_counts_as_missing(self, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/111")
        groups.set_name(group.id, "   ")
        assert [g.id for g in groups.missing_names()] == [group.id]


class TestTemplates:
    def test_save_and_list(self, db):
        templates = TemplateRepo(db)
        templates.save("Bike ad", "Selling a bike", ["a.png"])
        stored = templates.list()
        assert [t.name for t in stored] == ["Bike ad"]
        assert stored[0].media_paths == ["a.png"]

    def test_saving_the_same_name_overwrites(self, db):
        templates = TemplateRepo(db)
        templates.save("Ad", "first")
        templates.save("Ad", "second")
        assert len(templates.list()) == 1
        assert templates.get_by_name("Ad").body == "second"

    def test_delete(self, db):
        templates = TemplateRepo(db)
        template = templates.save("Ad", "body")
        templates.delete(template.id)
        assert templates.list() == []


class TestTasks:
    def test_create_with_targets(self, db, groups):
        one = groups.add_from_url("https://www.facebook.com/groups/one")
        two = groups.add_from_url("https://www.facebook.com/groups/two")
        tasks = TaskRepo(db)

        task = tasks.create("shared body", [(one.id, "body one"), (two.id, "body two")])
        targets = tasks.targets_for(task.id)

        assert task.state == "pending"
        assert task.is_immediate
        assert [t.body for t in targets] == ["body one", "body two"]
        assert [t.position for t in targets] == [0, 1]
        assert [t.group_identifier for t in targets] == ["one", "two"]

    def test_a_task_needs_at_least_one_target(self, db):
        with pytest.raises(ValueError):
            TaskRepo(db).create("body", [])

    def test_the_same_group_cannot_be_targeted_twice_in_one_batch(self, db, groups):
        """A duplicate post is the worst failure mode in the project.

        create() collapses a repeated group to one target, keeping the first
        wording, rather than letting UNIQUE(task_id, group_id) surface as a raw
        sqlite3.IntegrityError at the caller.
        """
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        tasks = TaskRepo(db)

        task = tasks.create("body", [(group.id, "a"), (group.id, "b")])
        targets = tasks.targets_for(task.id)
        assert [t.body for t in targets] == ["a"]

    def test_the_database_still_refuses_a_duplicate_target(self, db, groups):
        """The index is the backstop, not the error message. It has to stay:
        de-duplicating in create() protects that one path, not every path."""
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        tasks = TaskRepo(db)
        task = tasks.create("body", [(group.id, "a")])

        with pytest.raises(sqlite3.IntegrityError):
            db.write(
                "INSERT INTO task_targets (task_id, group_id, position, body) "
                "VALUES (?, ?, 1, 'b')",
                (task.id, group.id),
            )

    def test_a_failed_create_leaves_nothing_behind(self, db, groups):
        """Task and targets are written in one transaction, so a rejected batch
        must not leave an orphan task for the worker to pick up."""
        tasks = TaskRepo(db)

        # A group id that does not exist trips the foreign key.
        with pytest.raises(sqlite3.IntegrityError):
            tasks.create("body", [(999_999, "a")])

        assert tasks.list_recent() == []

    def test_scheduled_tasks_are_not_immediate(self, db, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        when = utcnow() + timedelta(hours=3)
        task = TaskRepo(db).create("body", [(group.id, "body")], scheduled_for=when)
        assert not task.is_immediate
        assert task.scheduled_for is not None

    def test_cancel(self, db, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        tasks = TaskRepo(db)
        task = tasks.create("body", [(group.id, "body")])
        tasks.cancel(task.id)
        assert tasks.get(task.id).state == "cancelled"

    def test_deleting_a_group_removes_its_targets(self, db, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        tasks = TaskRepo(db)
        task = tasks.create("body", [(group.id, "body")])
        groups.remove(group.id)
        assert tasks.targets_for(task.id) == []


class TestCountsFeedingTheGuards:
    def test_posted_count_only_counts_completed_targets(self, db, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        tasks = TaskRepo(db)
        task = tasks.create("body", [(group.id, "body")])
        since = utcnow() - timedelta(days=1)

        assert tasks.posted_count_since(since) == 0

        db.write(
            "UPDATE task_targets SET state = ?, posted_at = ? WHERE task_id = ?",
            (TARGET_DONE, utcnow().isoformat(), task.id),
        )
        assert tasks.posted_count_since(since) == 1

    def test_recent_bodies_only_returns_what_was_actually_posted(self, db, groups):
        group = groups.add_from_url("https://www.facebook.com/groups/one")
        tasks = TaskRepo(db)
        task = tasks.create("body", [(group.id, "the posted text")])

        assert groups.recent_bodies(group.id) == []

        db.write(
            "UPDATE task_targets SET state = ?, posted_at = ? WHERE task_id = ?",
            (TARGET_DONE, utcnow().isoformat(), task.id),
        )
        assert groups.recent_bodies(group.id) == ["the posted text"]


class TestThreading:
    def test_another_thread_gets_its_own_working_connection(self, db, groups):
        """The Phase 5 worker runs on its own thread and must be able to commit
        results while the UI thread reads."""
        groups.add_from_url("https://www.facebook.com/groups/one")
        seen: list[int] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                GroupRepo(db).add_from_url("https://www.facebook.com/groups/two")
                seen.append(len(GroupRepo(db).list()))
            except Exception as exc:  # surfaced below rather than lost
                errors.append(exc)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=10)

        assert errors == []
        assert seen == [2]
        assert len(groups.list()) == 2
