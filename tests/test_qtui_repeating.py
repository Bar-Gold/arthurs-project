"""The Repeating posts screen: every repeating post, in one list.

The user's request (2026-10-21). The list used to sit under Create a post,
step 3; it moved to a sidebar item of its own, beside Group sets. What it does
to a post (Pause, Resume, Edit, Delete with Undo) is what step 3 did.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from fbposter import clock, recurrence
from fbposter.db.models import SCHEDULE_ACTIVE, SCHEDULE_PAUSED, utcnow
from fbposter.qtui.app import FLOW_STEPS, NAV_ITEMS, NAV_SECTIONS
from fbposter.worker import WorkerEvent

BODY = "Selling a road bike, 54cm frame."


@pytest.fixture
def groups(qt_app):
    return [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
        for index in range(2)
    ]


@pytest.fixture
def screen(qt_app):
    qt_app.show_view("repeating")
    return qt_app.views["repeating"]


def make(qt_app, groups, name="Bikes", bodies=(BODY,)):
    rule = recurrence.build(["09:00"])
    return qt_app.schedule_repo.create(
        name=name, bodies=list(bodies), group_ids=[g.id for g in groups],
        times=list(rule.times), days=list(rule.days),
        next_run_at=recurrence.next_occurrence(rule, utcnow()),
    )


def labels(screen) -> list[str]:
    return [label.text() for label in screen.area.findChildren(QLabel)]


def buttons(screen) -> list[str]:
    return [button.text() for button in screen.area.findChildren(QPushButton)]


class TestItsPlace:
    def test_it_is_in_the_sidebar_beside_group_sets(self, qt_app):
        keys = [key for key, _label, _view in NAV_ITEMS]
        assert keys.index("repeating") == keys.index("group_sets") + 1
        assert "repeating" not in FLOW_STEPS
        assert qt_app.nav_buttons["repeating"].text() == "Repeating posts"

    def test_a_divider_starts_each_section(self, qt_app):
        """Home and Create a post, then what you keep, then what you look at
        (the user's choice, 2026-10-21)."""
        assert NAV_SECTIONS == ("group_sets", "dashboard")
        column = qt_app.nav_buttons["home"].parent().layout()
        for key in NAV_SECTIONS:
            index = column.indexOf(qt_app.nav_buttons[key])
            # The divider, then the room under it, then the item.
            above = column.itemAt(index - 2).widget()
            assert isinstance(above, QFrame) and above.objectName() == "Divider", key
        # And none between the items of a section.
        keys = [key for key, _label, _view in NAV_ITEMS]
        for key in keys[1:]:
            if key in NAV_SECTIONS or key == "settings":
                continue
            index = column.indexOf(qt_app.nav_buttons[key])
            assert column.itemAt(index - 1).widget() is not None, key
            assert column.itemAt(index - 1).widget().objectName() == "Nav", key

    def test_it_lights_its_own_item(self, qt_app, screen):
        assert qt_app.nav_buttons["repeating"].isChecked()

    def test_it_has_no_new_button(self, screen):
        """New ones are made in Create a post (the user's choice)."""
        assert not any("New" in text for text in buttons(screen))


class TestTheList:
    def test_with_none_it_says_where_to_make_one(self, screen):
        assert any("Nothing repeating yet" in text for text in labels(screen))
        assert screen.count_label.text() == ""

    def test_a_card_per_post(self, qt_app, groups, screen):
        make(qt_app, groups[:1], name="Bikes")
        make(qt_app, groups, name="Sofas", bodies=("A blue sofa.",))
        screen.refresh()
        shown = labels(screen)
        assert "Bikes" in shown and "Sofas" in shown
        assert screen.count_label.text() == "2 repeating posts"
        assert buttons(screen).count("Edit") == 2

    def test_an_unchanged_list_is_not_rebuilt(self, qt_app, groups, screen):
        make(qt_app, groups)
        screen.refresh()
        first = screen.schedule_box.itemAt(0).widget()
        screen.refresh()
        assert screen.schedule_box.itemAt(0).widget() is first

    def test_a_fired_post_shows_its_new_next_run(self, qt_app, groups, screen):
        """The worker moves the next run on when a post comes round; the
        `scheduled` event is what tells this screen."""
        schedule = make(qt_app, groups)
        screen.refresh()
        later = schedule.next_run_at + timedelta(days=1)
        qt_app.schedule_repo.record_run(schedule.id, utcnow(), later)
        qt_app._handle_worker_event(WorkerEvent("scheduled", "Bikes queued for 2 groups."))
        assert f"Next {clock.format_local(later)}" in labels(screen)


class TestWhatCanBeDone:
    def test_pausing_and_resuming(self, qt_app, groups, screen):
        schedule = make(qt_app, groups)
        screen.toggle_schedule(schedule.id)
        assert qt_app.schedule_repo.get(schedule.id).state == SCHEDULE_PAUSED
        assert "Resume" in buttons(screen)
        screen.toggle_schedule(schedule.id)
        assert qt_app.schedule_repo.get(schedule.id).state == SCHEDULE_ACTIVE
        assert "Pause" in buttons(screen)

    def test_resuming_recomputes_the_next_slot(self, qt_app, groups, screen):
        schedule = make(qt_app, groups)
        qt_app.schedule_repo.set_next_run(
            schedule.id, clock.parse_local("2020-01-01 09:00")
        )
        screen.toggle_schedule(schedule.id)
        screen.toggle_schedule(schedule.id)
        assert qt_app.schedule_repo.get(schedule.id).next_run_at > utcnow()

    def test_deleting_and_undoing_it(self, qt_app, groups, screen):
        schedule = make(qt_app, groups)
        screen.delete_schedule(schedule.id)
        assert qt_app.schedule_repo.list() == []
        assert any("Nothing repeating yet" in text for text in labels(screen))

        qt_app.banner.undo_button.click()
        assert qt_app.schedule_repo.get(schedule.id) is not None
        assert "Bikes" in labels(screen)

    def test_edit_opens_the_editor_and_comes_back_here(self, qt_app, groups, screen):
        schedule = make(qt_app, groups)
        assert screen.edit_schedule(schedule.id) is True
        assert qt_app.current_view == "schedule_edit"
        assert qt_app.nav_buttons["repeating"].isChecked()

        qt_app.views["schedule_edit"].back_button.click()
        assert qt_app.current_view == "repeating"
