"""Switching Facebook account starts the new account fresh.

The user's choices (2026-10-19): a new account starts with nothing of the old
one's but the templates. Waiting and repeating posts, group sets, the removed
groups, the post being written and the whole posting history all go, so the
Dashboard starts from zero. The groups list is replaced too; that is in
`test_group_import.py`.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from fbposter.db.models import TARGET_DONE, to_iso, utcnow
from fbposter.db.repo import start_new_account

from .test_qtui_dashboard import post, recent_texts

G = "https://www.facebook.com/groups/"


@pytest.fixture
def groups(qt_app):
    return [qt_app.group_repo.add_from_url(f"{G}demo{i}", name=f"Demo {i}") for i in range(2)]


@pytest.fixture
def view(qt_app):
    qt_app.show_view("dashboard")
    return qt_app.views["dashboard"]


def repeating(qt_app, group, name="Daily"):
    return qt_app.schedule_repo.create(
        name=name, bodies=["Hi"], group_ids=[group.id], times=["09:00"]
    )


class TestTheReset:
    def test_waiting_posts_are_deleted(self, qt_app, groups):
        qt_app.task_repo.create("Hi", [(g.id, "Hi") for g in groups])
        reset = start_new_account(qt_app.db)
        assert reset.waiting == 1
        assert qt_app.task_repo.unfinished() == []

    def test_the_posting_history_is_deleted(self, qt_app, groups):
        post(qt_app, groups[0], body="Bike for sale")
        qt_app.group_repo.mark_posted(groups[0].id)
        reset = start_new_account(qt_app.db)
        assert reset.waiting == 0, "a finished batch is history, not waiting"
        assert qt_app.task_repo.history_records() == []
        assert qt_app.group_repo.recent_bodies(groups[0].id) == []
        assert qt_app.group_repo.get(groups[0].id).last_posted_at is None

    def test_repeating_posts_and_group_sets_are_deleted(self, qt_app, groups):
        repeating(qt_app, groups[0])
        repeating(qt_app, groups[1], name="Weekly")
        qt_app.group_set_repo.create("Mine", [g.id for g in groups])
        reset = start_new_account(qt_app.db)
        assert (reset.repeating, reset.sets) == (2, 1)
        assert qt_app.schedule_repo.list() == []
        assert qt_app.group_set_repo.list() == []

    def test_the_removed_groups_are_deleted(self, qt_app, groups):
        post(qt_app, groups[1])
        qt_app.group_repo.remove(groups[1].id)
        start_new_account(qt_app.db)
        assert qt_app.group_repo.removed() == []
        assert qt_app.group_repo.get(groups[1].id) is None

    def test_the_listed_groups_and_templates_stay(self, qt_app, groups):
        qt_app.template_repo.save("Bike", "Bike for sale")
        start_new_account(qt_app.db)
        assert [g.id for g in qt_app.group_repo.list()] == [g.id for g in groups]
        assert [t.name for t in qt_app.template_repo.list()] == ["Bike"]


class TestTheFreshDashboard:
    def test_the_counts_start_from_zero(self, qt_app, groups, view):
        post(qt_app, groups[0])
        qt_app.db.write(
            "INSERT INTO post_totals (group_id, state, count, first_at) VALUES (?, ?, ?, ?)",
            (groups[0].id, TARGET_DONE, 40, to_iso(utcnow() - timedelta(days=200))),
        )
        start_new_account(qt_app.db)
        view.refresh()
        assert view.tiles["today"].value.text() == "0"
        assert view.tiles["all_time"].value.text() == "0"

    def test_recent_activity_starts_empty(self, qt_app, groups, view):
        post(qt_app, groups[0])
        assert any("Demo 0" in text for text in recent_texts(view))
        start_new_account(qt_app.db)
        assert not any("Demo 0" in text for text in recent_texts(view))


class TestTheListAfterwards:
    def found(self, *identifiers):
        return [(i, f"{G}{i}/", i) for i in identifiers]

    def test_an_old_group_is_deleted_not_kept_as_removed(self, qt_app, groups):
        start_new_account(qt_app.db)
        qt_app.group_repo.replace_with(self.found("demo0"))
        assert [g.id for g in qt_app.group_repo.list()] == [groups[0].id]
        assert qt_app.group_repo.removed() == []

    def test_one_posted_to_since_the_switch_keeps_its_history(self, qt_app, groups):
        """Sent to as the new account before the list was read: removed the
        usual way, so what went to it still counts."""
        start_new_account(qt_app.db)
        post(qt_app, groups[1])
        qt_app.group_repo.replace_with(self.found("demo0"))
        assert [g.id for g in qt_app.group_repo.removed()] == [groups[1].id]
