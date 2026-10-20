"""Tests for group sets: storage, removal, deletion, and a post following a set.

A group set is a name for several groups, picked as one item (the user's
request, 2026-10-15). The case worth reading first is
TestARepeatingPostFollowsItsSets::test_a_group_added_to_the_set_later_gets_the_next_run:
a repeating post linked to a set follows it, so the set's groups are read at
every run, never copied when the post was made.

Real database, fake poster, hand-wound clock, as in test_schedules.py.
"""

from __future__ import annotations

import sqlite3

import pytest

from fbposter.db import Database
from fbposter.db import schema
from fbposter.db.models import SCHEDULE_PAUSED
from fbposter.db.repo import GroupRepo, GroupSetRepo, ScheduleRepo, TaskRepo

from .test_schedules import NOON, WORDINGS, Clock, add_groups, make_worker

from .conftest import fresh_database


@pytest.fixture
def db(tmp_path):
    database = fresh_database(tmp_path / "sets.db")
    yield database
    database.close()


@pytest.fixture
def groups(db):
    return GroupRepo(db)


@pytest.fixture
def sets(db):
    return GroupSetRepo(db)


@pytest.fixture
def schedules(db):
    return ScheduleRepo(db)


@pytest.fixture
def tasks(db):
    return TaskRepo(db)


def make_schedule(schedules, group_ids=(), set_ids=(), next_run_at=NOON):
    return schedules.create(
        name="Bikes",
        bodies=list(WORDINGS),
        group_ids=list(group_ids),
        set_ids=list(set_ids),
        times=["12:00"],
        next_run_at=next_run_at,
    )


def targets_of(tasks, task) -> list[int]:
    return [t.group_id for t in tasks.targets_for(task.id)]


class TestTheTables:
    def test_the_migration_adds_them(self, db):
        names = {
            row["name"]
            for row in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"group_sets", "group_set_members", "schedule_sets"} <= names

    def test_an_older_database_upgrades_with_its_rows_untouched(self, tmp_path):
        """The client's database is on 13. Only tables are added."""
        path = tmp_path / "v13.db"
        raw = sqlite3.connect(path, isolation_level=None)
        for index in range(13):
            schema.MIGRATIONS[index](raw)
            raw.execute(f"PRAGMA user_version = {index + 1}")
        raw.execute(
            "INSERT INTO groups (identifier, url, name, created_at) "
            "VALUES ('old', 'u', 'Old group', '2026-01-01')"
        )
        raw.close()

        upgraded = Database(path)
        try:
            assert schema.current_version(upgraded.connection) == schema.LATEST_VERSION
            group = GroupRepo(upgraded).list()[0]
            assert group.name == "Old group"
            made = GroupSetRepo(upgraded).create("Old ones", [group.id])
            assert GroupSetRepo(upgraded).get(made.id).group_ids == [group.id]
        finally:
            upgraded.close()


class TestStoringASet:
    def test_a_set_round_trips_in_the_order_given(self, groups, sets):
        one, two, three = add_groups(groups, 3)
        made = sets.create("Tel Aviv", [three.id, one.id])
        stored = sets.get(made.id)
        assert stored.name == "Tel Aviv"
        assert stored.group_ids == [three.id, one.id]

    def test_sets_are_listed_by_name(self, groups, sets):
        one, _two = add_groups(groups)
        sets.create("north", [one.id])
        sets.create("Haifa", [one.id])
        sets.create("eilat", [one.id])
        assert [s.name for s in sets.list()] == ["eilat", "Haifa", "north"]

    def test_the_name_is_tidied(self, groups, sets):
        """A pasted direction mark would make two names that look the same
        different, which defeats the rule that names are unique."""
        one, _two = add_groups(groups)
        made = sets.create("  Tel‏  Aviv ", [one.id])
        assert made.name == "Tel Aviv"

    def test_a_set_needs_a_name(self, groups, sets):
        one, _two = add_groups(groups)
        with pytest.raises(ValueError, match="needs a name"):
            sets.create("   ", [one.id])

    def test_a_set_needs_a_group(self, sets):
        with pytest.raises(ValueError, match="at least one group"):
            sets.create("Empty", [])
        assert sets.list() == []

    def test_a_removed_group_cannot_be_put_in_a_set(self, groups, sets):
        one, _two = add_groups(groups)
        groups.remove(one.id)
        with pytest.raises(ValueError, match="at least one group"):
            sets.create("Gone", [one.id])
        assert sets.list() == []

    def test_two_sets_cannot_share_a_name_whatever_the_case(self, groups, sets):
        one, _two = add_groups(groups)
        sets.create("Tel Aviv", [one.id])
        with pytest.raises(ValueError, match="already a group set"):
            sets.create("tel aviv", [one.id])

    def test_a_set_keeps_its_own_name_on_saving(self, groups, sets):
        one, two = add_groups(groups)
        made = sets.create("Tel Aviv", [one.id])
        assert sets.update(made.id, "Tel Aviv", [one.id, two.id]).group_ids == [one.id, two.id]

    def test_a_rename_cannot_take_another_sets_name(self, groups, sets):
        one, _two = add_groups(groups)
        sets.create("Haifa", [one.id])
        made = sets.create("Tel Aviv", [one.id])
        with pytest.raises(ValueError, match="already a group set"):
            sets.update(made.id, "HAIFA", [one.id])
        assert sets.get(made.id).name == "Tel Aviv"

    def test_adding_a_group_does_not_move_the_others(self, groups, sets):
        """Their place is part of which wording each one gets."""
        one, two, three = add_groups(groups, 3)
        made = sets.create("Mixed", [three.id, one.id])
        updated = sets.update(made.id, "Mixed", [one.id, two.id, three.id])
        assert updated.group_ids == [three.id, one.id, two.id]


class TestRemovingAGroup:
    def test_it_leaves_every_set(self, groups, sets):
        one, two = add_groups(groups)
        first = sets.create("First", [one.id, two.id])
        second = sets.create("Second", [one.id])
        removal = groups.remove(one.id)
        assert removal.sets == 2
        assert sets.get(first.id).group_ids == [two.id]
        assert sets.get(second.id).group_ids == []

    def test_undo_puts_it_back_in_its_place(self, groups, sets):
        one, two = add_groups(groups)
        made = sets.create("First", [one.id, two.id])
        removal = groups.remove(one.id)
        assert groups.restore(one.id, removal) is True
        assert sets.get(made.id).group_ids == [one.id, two.id]

    def test_bringing_it_back_later_does_not(self, groups, sets):
        """Same rule as repeating posts: Bring back restores the history, not
        its place, or a post following the set would start again unasked."""
        one, two = add_groups(groups)
        made = sets.create("First", [one.id, two.id])
        groups.remove(one.id)
        groups.bring_back(one.id)
        assert sets.get(made.id).group_ids == [two.id]


class TestDeletingASet:
    def test_a_post_that_used_it_keeps_its_groups(self, groups, sets, schedules):
        """The user's choice: nothing stops posting because a set went."""
        one, two, three = add_groups(groups, 3)
        made = sets.create("Pair", [two.id, three.id])
        post = make_schedule(schedules, [one.id], [made.id])

        deletion = sets.delete(made.id)
        assert sets.get(made.id) is None
        after = schedules.get(post.id)
        assert after.set_ids == []
        assert after.group_ids == [one.id, two.id, three.id]
        assert after.recipient_ids == [one.id, two.id, three.id]
        assert deletion.links == ((post.id, 0),)

    def test_a_group_the_post_already_had_is_not_added_twice(self, groups, sets, schedules):
        one, two = add_groups(groups)
        made = sets.create("Pair", [one.id, two.id])
        post = make_schedule(schedules, [one.id], [made.id])
        sets.delete(made.id)
        assert schedules.get(post.id).group_ids == [one.id, two.id]

    def test_undo_puts_the_set_and_the_link_back(self, groups, sets, schedules):
        one, two, three = add_groups(groups, 3)
        made = sets.create("Pair", [two.id, three.id])
        post = make_schedule(schedules, [one.id], [made.id])

        deletion = sets.delete(made.id)
        assert sets.restore(deletion) is True
        back = sets.get(made.id)
        assert back.name == "Pair"
        assert back.group_ids == [two.id, three.id]
        after = schedules.get(post.id)
        assert after.set_ids == [made.id]
        assert after.group_ids == [one.id]

    def test_undo_is_refused_once_the_name_is_taken_again(self, groups, sets):
        one, _two = add_groups(groups)
        made = sets.create("Pair", [one.id])
        deletion = sets.delete(made.id)
        sets.create("pair", [one.id])
        assert sets.restore(deletion) is False

    def test_deleting_a_post_leaves_its_sets(self, groups, sets, schedules):
        one, _two = add_groups(groups)
        made = sets.create("Pair", [one.id])
        post = make_schedule(schedules, [], [made.id])
        schedules.delete(post.id)
        assert sets.get(made.id) is not None
        assert sets.used_by(made.id) == []


class TestARepeatingPostFollowsItsSets:
    def test_its_own_groups_come_first_then_each_sets_once(self, groups, sets, schedules):
        one, two, three, four = add_groups(groups, 4)
        first = sets.create("First", [three.id, two.id])
        second = sets.create("Second", [two.id, four.id])
        post = make_schedule(schedules, [two.id, one.id], [first.id, second.id])
        assert post.group_ids == [two.id, one.id]
        assert post.set_ids == [first.id, second.id]
        assert post.recipient_ids == [two.id, one.id, three.id, four.id]

    def test_a_post_of_only_sets_is_accepted(self, groups, sets, schedules):
        one, _two = add_groups(groups)
        made = sets.create("Pair", [one.id])
        assert make_schedule(schedules, [], [made.id]).recipient_ids == [one.id]

    def test_a_post_with_neither_is_refused(self, schedules):
        with pytest.raises(ValueError, match="at least one group"):
            make_schedule(schedules, [], [])

    def test_editing_a_post_replaces_its_sets(self, groups, sets, schedules):
        one, two = add_groups(groups)
        first = sets.create("First", [one.id])
        second = sets.create("Second", [two.id])
        post = make_schedule(schedules, [], [first.id])
        updated = schedules.update(
            post.id, name="Bikes", bodies=WORDINGS, group_ids=[], set_ids=[second.id],
            times=["12:00"],
        )
        assert updated.set_ids == [second.id]
        assert updated.recipient_ids == [two.id]

    def test_undoing_a_deleted_post_brings_its_sets_back(self, groups, sets, schedules):
        one, _two = add_groups(groups)
        made = sets.create("Pair", [one.id])
        post = make_schedule(schedules, [], [made.id])
        schedules.delete(post.id)
        assert schedules.restore(post) is True
        assert schedules.get(post.id).set_ids == [made.id]

    def test_a_group_added_to_the_set_later_gets_the_next_run(
        self, db, groups, sets, schedules, tasks
    ):
        one, two = add_groups(groups)
        made = sets.create("Pair", [one.id])
        post = make_schedule(schedules, [], [made.id])
        clock = Clock()
        worker = make_worker(db, clock_fn=clock)

        assert worker.run_once() is True
        first = tasks.list_recent()[0]
        assert targets_of(tasks, first) == [one.id]
        tasks.cancel(first.id)

        sets.update(made.id, "Pair", [one.id, two.id])
        clock.advance(days=1)
        assert worker.run_once() is True
        second = [t for t in tasks.list_recent() if t.id != first.id][0]
        assert second.schedule_id == post.id
        assert targets_of(tasks, second) == [one.id, two.id]

    def test_a_group_taken_out_of_the_set_is_not_posted_to(
        self, db, groups, sets, schedules, tasks
    ):
        one, two = add_groups(groups)
        made = sets.create("Pair", [one.id, two.id])
        make_schedule(schedules, [], [made.id])
        sets.update(made.id, "Pair", [two.id])
        make_worker(db).run_once()
        assert targets_of(tasks, tasks.list_recent()[0]) == [two.id]

    def test_a_post_whose_sets_have_no_groups_left_is_paused(
        self, db, groups, sets, schedules, tasks
    ):
        """Left active it would come round for ever and post nothing."""
        one, _two = add_groups(groups)
        made = sets.create("Pair", [one.id])
        post = make_schedule(schedules, [], [made.id])
        groups.remove(one.id)

        worker = make_worker(db)
        worker.run_once()
        assert tasks.list_recent() == []
        assert schedules.get(post.id).state == SCHEDULE_PAUSED
        events = []
        while not worker.events.empty():
            events.append(worker.events.get_nowait())
        assert any(e.kind == "schedule_error" and "group sets" in e.message for e in events)
