"""Tests for the Groups screen and the one-off date picker.

Groups is the middle step of the flow. Compose says what, this says where,
Publish says when, so its job is two things at once: choosing recipients and
managing the list they are chosen from.

The date-picker tests exist because of a real report: "Once" appeared to
default to 2028. The value was right; the widget opened with the *year* section
focused and accepted the mouse wheel, so one scroll moved the post a year.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QDateTime, QEvent, QPoint, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QDateTimeEdit

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QPushButton

from fbposter.db.models import TARGET_DONE
from fbposter.qtui.views.groups import HIDE_REMOVED, SHOW_REMOVED
from fbposter.qtui.views.publish import (
    MINUTE_STEP,
    ONCE,
    SCHEDULE_HORIZON_DAYS,
    ScheduleEntry,
    next_slot,
)

URL = "https://www.facebook.com/groups/demo{}"


@pytest.fixture
def groups_view(qt_app):
    for index in range(3):
        qt_app.group_repo.add_from_url(URL.format(index), name=f"Demo {index}")
    view = qt_app.views["groups"]
    qt_app.show_view("groups")
    return view


class TestChoosingRecipients:
    def test_every_group_gets_a_checkbox(self, groups_view):
        assert len(groups_view._checkboxes) == 3

    def test_nothing_is_selected_to_begin_with(self, qt_app, groups_view):
        assert qt_app.selected_group_ids() == []

    def test_ticking_one_selects_it(self, qt_app, groups_view):
        group_id = next(iter(groups_view._checkboxes))
        groups_view._checkboxes[group_id].setChecked(True)
        assert qt_app.selected_group_ids() == [group_id]

    def test_unticking_deselects_it(self, qt_app, groups_view):
        group_id = next(iter(groups_view._checkboxes))
        groups_view._checkboxes[group_id].setChecked(True)
        groups_view._checkboxes[group_id].setChecked(False)
        assert qt_app.selected_group_ids() == []

    def test_select_all_and_clear(self, qt_app, groups_view):
        groups_view.set_all(True)
        assert len(qt_app.selected_group_ids()) == 3
        groups_view.set_all(False)
        assert qt_app.selected_group_ids() == []

    def test_the_count_is_shown(self, groups_view):
        groups_view.set_all(True)
        assert groups_view.count_label.text() == "3 of 3 selected"

    def test_the_next_button_is_dead_until_something_is_picked(self, groups_view):
        groups_view.set_all(False)
        assert not groups_view.next_button.isEnabled()
        groups_view.set_all(True)
        assert groups_view.next_button.isEnabled()

    def test_the_selection_survives_a_refresh(self, qt_app, groups_view):
        groups_view.set_all(True)
        groups_view.refresh()
        assert len(qt_app.selected_group_ids()) == 3
        assert all(b.isChecked() for b in groups_view._checkboxes.values())

    def test_selection_is_ordered_the_way_the_list_is(self, qt_app, groups_view):
        groups_view.set_all(True)
        assert qt_app.selected_group_ids() == [g.id for g in qt_app.group_repo.list()]


class TestNoPasteBox:
    """Every group comes off Facebook now (2026-10-14); the box for pasting a
    group's link is gone, and the only text box on the screen is the search."""

    def test_the_only_text_box_is_the_search(self, groups_view):
        assert groups_view.findChildren(QLineEdit) == [groups_view.search]

    def test_there_is_no_add_button(self, groups_view):
        labels = [b.text() for b in groups_view.findChildren(QPushButton)]
        assert "Add group" not in labels
        assert not hasattr(groups_view, "add_group")

    def test_an_empty_list_points_at_facebook(self, qt_app):
        qt_app.show_view("groups")
        view = qt_app.views["groups"]
        assert "Get my groups from Facebook" in view.empty_note.text()
        assert "link" not in view.empty_note.text().lower()


GROUPS = "https://www.facebook.com/groups/"


def shown(view) -> list[int]:
    """The rows on show, read off the widgets rather than the filter."""
    return [gid for gid, row in view._rows.items() if not row.isHidden()]


class TestSearching:
    @pytest.fixture
    def view(self, qt_app):
        for identifier, name in [
            ("bikes", "Bikes Tel Aviv"),
            ("yad2", "יד שנייה חיפה"),
            ("bikesh", "Haifa BIKES"),
            ("12345", ""),
        ]:
            qt_app.group_repo.add_from_url(f"{GROUPS}{identifier}", name=name)
        qt_app.show_view("groups")
        return qt_app.views["groups"]

    def ids(self, qt_app, *names):
        by_name = {g.display_name: g.id for g in qt_app.group_repo.list()}
        return [by_name[name] for name in names]

    def test_everything_shows_with_no_search(self, qt_app, view):
        assert len(shown(view)) == 4
        assert view.empty_note.isHidden()

    def test_it_matches_the_name_ignoring_case(self, qt_app, view):
        view.search.setText("bikes")
        assert shown(view) == self.ids(qt_app, "Bikes Tel Aviv", "Haifa BIKES")

    def test_it_matches_hebrew(self, qt_app, view):
        view.search.setText("חיפה")
        assert shown(view) == self.ids(qt_app, "יד שנייה חיפה")

    def test_a_group_with_no_name_matches_on_its_number(self, qt_app, view):
        view.search.setText("234")
        assert shown(view) == self.ids(qt_app, "12345")

    def test_a_named_group_does_not_match_on_its_link(self, qt_app, view):
        """The user's choice: the search matches what the row shows."""
        view.search.setText("yad2")
        assert shown(view) == []

    def test_invisible_marks_do_not_stop_a_match(self, qt_app, view):
        view.search.setText("\u200fחיפה")
        assert shown(view) == self.ids(qt_app, "יד שנייה חיפה")

    def test_no_match_says_so(self, view):
        view.search.setText("nothing like it")
        assert shown(view) == []
        assert not view.empty_note.isHidden()
        assert "nothing like it" in view.empty_note.text()

    def test_clearing_the_search_shows_everything_again(self, view):
        view.search.setText("bikes")
        view.search.clear()
        assert len(shown(view)) == 4

    def test_typing_does_not_rebuild_the_rows(self, view):
        before = dict(view._rows)
        view.search.setText("bikes")
        assert view._rows == before

    def test_select_all_ticks_only_the_matches(self, qt_app, view):
        view.search.setText("bikes")
        view.set_all(True)
        assert qt_app.selected_group_ids() == self.ids(qt_app, "Bikes Tel Aviv", "Haifa BIKES")

    def test_clear_clears_only_the_matches(self, qt_app, view):
        view.set_all(True)
        view.search.setText("bikes")
        view.set_all(False)
        assert qt_app.selected_group_ids() == self.ids(qt_app, "יד שנייה חיפה", "12345")

    def test_a_hidden_group_stays_ticked(self, qt_app, view):
        """Searching narrows what is on show, never what the post goes to."""
        view.set_all(True)
        view.search.setText("bikes")
        assert len(qt_app.selected_group_ids()) == 4
        assert view.count_label.text() == "4 of 4 selected"

    def test_the_search_survives_a_refresh(self, view):
        view.search.setText("bikes")
        view.refresh(force=True)
        assert len(shown(view)) == 2


class TestRemovedGroups:
    def test_nothing_is_offered_until_a_group_is_removed(self, groups_view):
        assert groups_view.removed_toggle.isHidden()

    def test_a_removed_group_is_offered_under_the_list(self, qt_app, groups_view):
        group_id = qt_app.group_repo.list()[0].id
        groups_view.remove_group(group_id)
        assert not groups_view.removed_toggle.isHidden()
        assert groups_view.removed_toggle.text() == SHOW_REMOVED.format(count=1)
        assert groups_view._removed_rows == {}, "shown only when asked for"

        groups_view.toggle_removed()
        assert list(groups_view._removed_rows) == [group_id]
        assert groups_view.removed_toggle.text() == HIDE_REMOVED

    def test_bringing_one_back(self, qt_app, groups_view):
        group_id = qt_app.group_repo.list()[0].id
        groups_view.remove_group(group_id)
        groups_view.toggle_removed()
        groups_view.bring_back(group_id)
        assert qt_app.group_repo.active(group_id) is not None
        assert group_id in groups_view._checkboxes
        assert groups_view.removed_toggle.isHidden()
        assert qt_app.toast_label.text() == "Brought Demo 0 back."

    def test_a_group_brought_back_starts_ticked(self, qt_app, groups_view):
        """It was brought back in order to be posted to."""
        group_id = qt_app.group_repo.list()[0].id
        groups_view.remove_group(group_id)
        groups_view.bring_back(group_id)
        assert group_id in qt_app.selected_group_ids()

    def test_the_toast_mentions_its_history(self, qt_app, groups_view):
        """The repeat guard already knows what it was sent, so it is said."""
        group_id = qt_app.group_repo.list()[0].id
        task = qt_app.task_repo.create("Selling a bike", [(group_id, "Selling a bike")])
        target = qt_app.task_repo.targets_for(task.id)[0]
        qt_app.task_repo.claim_target(target.id)
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        groups_view.remove_group(group_id)
        groups_view.bring_back(group_id)
        assert "along with its posting history" in qt_app.toast_label.text()

    def test_it_is_not_put_back_in_its_repeating_posts(self, qt_app, groups_view):
        group_id = qt_app.group_repo.list()[0].id
        schedule = qt_app.schedule_repo.create(
            name="Bikes", bodies=["one", "two"], group_ids=[group_id], times=["09:00"]
        )
        groups_view.remove_group(group_id)
        groups_view.bring_back(group_id)
        assert qt_app.schedule_repo.get(schedule.id).group_ids == []

    def test_the_search_covers_removed_groups_too(self, qt_app, groups_view):
        one, two = (g.id for g in qt_app.group_repo.list()[:2])
        groups_view.remove_group(one)
        groups_view.remove_group(two)
        groups_view.toggle_removed()
        groups_view.search.setText("Demo 1")
        visible = [gid for gid, row in groups_view._removed_rows.items() if not row.isHidden()]
        assert visible == [two]

    def test_the_removal_toast_points_at_the_removed_groups(self, qt_app, groups_view):
        groups_view.remove_group(qt_app.group_repo.list()[0].id)
        text = qt_app.toast_label.text()
        assert "removed groups under the list" in text
        assert "link" not in text.lower()


class TestTheWholeRowTicks:
    """The user's request (2026-10-14): a click anywhere on a group's card
    ticks it, not only on the little box. Clicked where a person would click,
    at real geometry, so a child that swallowed the click would show."""

    @pytest.fixture
    def shown_view(self, qt_application, qt_app, groups_view):
        qt_app.group_repo.set_members(qt_app.group_repo.list()[0].id, "2 members")
        groups_view.refresh()
        qt_app.resize(1000, 700)
        qt_app.show()
        qt_application.processEvents()
        yield groups_view
        qt_app.hide()

    def click(self, qt_application, row, point):
        """Deliver the click to whatever is under the pointer, as Qt would."""
        target = row.childAt(point) or row
        QTest.mouseClick(target, Qt.LeftButton, Qt.NoModifier, target.mapFrom(row, point))
        qt_application.processEvents()

    def first(self, qt_app, view):
        group_id = qt_app.group_repo.list()[0].id
        return group_id, view._rows[group_id], view._checkboxes[group_id]

    def test_a_click_on_empty_space_ticks_it(self, qt_application, qt_app, shown_view):
        group_id, row, box = self.first(qt_app, shown_view)
        point = QPoint(box.geometry().right() - 5, row.height() // 2)
        self.click(qt_application, row, point)
        assert group_id in qt_app.selected_group_ids()
        self.click(qt_application, row, point)
        assert group_id not in qt_app.selected_group_ids()

    def test_a_click_on_the_member_count_ticks_it(self, qt_application, qt_app, shown_view):
        from PySide6.QtWidgets import QLabel

        group_id, row, _box = self.first(qt_app, shown_view)
        (members,) = [w for w in row.findChildren(QLabel) if w.text() == "2 members"]
        self.click(qt_application, row, members.geometry().center())
        assert group_id in qt_app.selected_group_ids()

    def test_a_click_on_the_box_ticks_it_once(self, qt_application, qt_app, shown_view):
        """Not twice: the box's own click must not also reach the card."""
        group_id, row, box = self.first(qt_app, shown_view)
        self.click(qt_application, row, box.geometry().topLeft() + QPoint(10, box.height() // 2))
        assert group_id in qt_app.selected_group_ids()

    def test_a_click_on_remove_does_not_tick_it(self, qt_application, qt_app, shown_view,
                                                monkeypatch):
        group_id, row, _box = self.first(qt_app, shown_view)
        removed = []
        monkeypatch.setattr(shown_view, "remove_group", removed.append)
        (remove,) = [b for b in row.findChildren(QPushButton) if b.text() == "Remove"]
        self.click(qt_application, row, remove.geometry().center())
        assert removed == [group_id]
        assert group_id not in qt_app.selected_group_ids()

    def test_the_row_shows_it_can_be_clicked(self, qt_app, shown_view):
        _group_id, row, _box = self.first(qt_app, shown_view)
        assert row.cursor().shape() == Qt.PointingHandCursor


class TestRemoving:
    def test_removing_a_group_also_deselects_it(self, qt_app, groups_view):
        groups_view.set_all(True)
        group_id = qt_app.group_repo.list()[0].id
        groups_view.remove_group(group_id)
        assert group_id not in qt_app.selected_group_ids()
        assert group_id not in qt_app.selected_groups

    def test_removing_a_group_says_it_left_its_repeating_posts(self, qt_app, groups_view):
        """Adding the group back does not undo that, so it is said now."""
        group_id = qt_app.group_repo.list()[0].id
        qt_app.schedule_repo.create(
            name="Bikes", bodies=["one", "two"], group_ids=[group_id], times=["09:00"]
        )
        groups_view.remove_group(group_id)
        assert "taken out of 1 repeating post." in qt_app.toast_label.text()

    def test_removing_a_group_says_it_left_its_queued_posts(self, qt_app, groups_view):
        group_id = qt_app.group_repo.list()[0].id
        qt_app.schedule_repo.create(
            name="Bikes", bodies=["one", "two"], group_ids=[group_id], times=["09:00"]
        )
        qt_app.task_repo.create("Selling a bike", [(group_id, "Selling a bike")])
        groups_view.remove_group(group_id)
        assert (
            "taken out of 1 repeating post and 1 queued post." in qt_app.toast_label.text()
        )


class TestTheFlowBetweenScreens:
    def test_the_sidebar_reads_in_flow_order(self, qt_app):
        from fbposter.qtui.app import NAV_ITEMS

        assert [key for key, _label, _cls in NAV_ITEMS] == [
            # Where the app opens (2026-10-19).
            "home",
            # The three steps, as one road (2026-10-19).
            "create",
            # What you keep, under a divider (the user's choice, 2026-10-21).
            # Where sets are made (2026-10-15), and the repeating posts.
            "group_sets",
            "repeating",
            # What you look at, under another: the numbers, then advice to
            # read (2026-10-18).
            "dashboard",
            "tips",
            # Not a step: somewhere you go on purpose, at the foot.
            "settings",
        ]

    def test_next_goes_to_publish(self, qt_app, groups_view):
        groups_view.set_all(True)
        qt_app.show_view("publish")
        assert qt_app.current_view == "publish"

    def test_compose_sees_the_groups_picked_here(self, qt_app, groups_view):
        groups_view.set_all(True)
        compose = qt_app.views["compose"]
        qt_app.show_view("compose")
        assert len(compose.selected_group_ids()) == 3

    def test_publish_sees_them_too(self, qt_app, groups_view):
        groups_view.set_all(True)
        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        assert len(publish.selected_group_ids()) == 3

    def test_the_version_tabs_do_not_follow_the_groups(self, qt_app, groups_view):
        """There was a tab per chosen group, which could only be filled in
        after picking the groups and going back (2026-10-23)."""
        compose = qt_app.views["compose"]
        groups_view.set_all(True)
        qt_app.show_view("compose")
        assert compose.tab_bar.count() == 1


class TestTheOnceDatePicker:
    """The 2028 report. The default was never wrong; the widget was."""

    @pytest.mark.parametrize(
        "now,expected",
        [
            ("14:45:00", "14:45"),
            ("14:45:20", "15:00"),  # was 14:45, already past: a flaky suite
            ("14:44:30", "14:45"),
            ("14:46:00", "15:00"),
            ("23:59:10", "00:00"),
        ],
    )
    def test_the_earliest_slot_is_never_in_the_past(self, qt_application, now, expected):
        from PySide6.QtCore import QDate, QTime

        moment = QDateTime(QDate(2026, 10, 19), QTime.fromString(now, "HH:mm:ss"))
        slot = next_slot(moment)
        assert slot >= moment
        assert slot.time().toString("HH:mm") == expected

    def test_it_defaults_to_the_next_quarter_hour(self, qt_application):
        """Not an hour out: an arbitrary offset is one more thing to undo. The
        minutes come every quarter hour, so the next one is the soonest."""
        entry = ScheduleEntry()
        ahead = QDateTime.currentDateTime().secsTo(entry.dateTime())
        assert -60 < ahead <= MINUTE_STEP * 60
        assert entry.dateTime().time().minute() % MINUTE_STEP == 0

    def test_the_default_has_whole_minutes(self, qt_application):
        assert ScheduleEntry().dateTime().time().second() == 0

    def test_it_does_not_default_two_years_out(self, qt_application):
        entry = ScheduleEntry()
        assert entry.dateTime().date().year() == QDateTime.currentDateTime().date().year()

    def test_the_cursor_starts_on_the_day_not_the_year(self, qt_application):
        """A stock date field starts on the year, which is how one wheel
        notch became a year."""
        entry = ScheduleEntry()
        assert entry.date_entry.currentSection() == QDateTimeEdit.DaySection

    def test_the_year_cannot_be_scrolled_into_the_future(self, qt_application):
        entry = ScheduleEntry()
        entry.date_entry.setCurrentSection(QDateTimeEdit.YearSection)
        entry.date_entry.stepBy(2)
        horizon = QDateTime.currentDateTime().addDays(SCHEDULE_HORIZON_DAYS)
        assert entry.dateTime() <= horizon

    def test_the_wheel_does_not_move_it_at_all(self, qt_application):
        entry = ScheduleEntry()
        before = entry.dateTime()
        parts = (entry.date_entry, entry.time_picker.hour, entry.time_picker.minute)
        for part in parts:
            event = QWheelEvent(
                QPoint(10, 10),
                part.mapToGlobal(QPoint(10, 10)),
                QPoint(0, 0),
                QPoint(0, 120),
                Qt.NoButton,
                Qt.NoModifier,
                Qt.NoScrollPhase,
                False,
            )
            part.wheelEvent(event)
        assert entry.dateTime() == before

    def test_it_refuses_a_time_in_the_past(self, qt_application):
        entry = ScheduleEntry()
        entry.setDateTime(QDateTime.currentDateTime().addDays(-5))
        assert entry.dateTime() >= entry.minimumDateTime()

    def test_reset_moves_a_stale_default_forward(self, qt_application):
        """The app is left open for days; a default set at launch goes stale."""
        entry = ScheduleEntry()
        entry.setMinimumDateTime(QDateTime.currentDateTime().addDays(-10))
        stale = QDateTime.currentDateTime().addDays(-1)
        entry.setDateTime(stale)
        entry.reset()
        assert entry.dateTime() > stale
        ahead = QDateTime.currentDateTime().secsTo(entry.dateTime())
        assert -60 < ahead <= MINUTE_STEP * 60

    def test_reset_also_moves_the_floor_forward(self, qt_application):
        entry = ScheduleEntry()
        entry.setMinimumDateTime(QDateTime.currentDateTime().addDays(-10))
        entry.reset()
        assert abs(entry.minimumDateTime().secsTo(QDateTime.currentDateTime())) < 60

    def test_opening_the_once_mode_refreshes_it(self, qt_app):
        publish = qt_app.views["publish"]
        publish.set_mode(ONCE)
        first = publish.schedule_entry.dateTime()
        publish.schedule_entry.setDateTime(first.addDays(30))
        publish.set_mode("now")
        publish.set_mode(ONCE)
        assert abs(publish.schedule_entry.dateTime().secsTo(first)) < 60


class TestAnAmpersandIsShown:
    """Qt reads "&" in a button's label as a keyboard-shortcut marker and
    draws nothing for it: a group called "Real estate - buy & sell" showed
    as "buy  sell" (reported 2026-10-20). Every label made from a name the
    user or Facebook chose has to show it."""

    NAME = "Real estate - buy & sell"

    @staticmethod
    def shows_the_ampersand(button) -> bool:
        # "&&" is one "&" drawn as itself, and no letter becomes a shortcut.
        from PySide6.QtGui import QKeySequence

        return "& sell" in button.text().replace("&&", "&") and QKeySequence.mnemonic(
            button.text()
        ).isEmpty()

    @pytest.fixture
    def named(self, qt_app):
        group = qt_app.group_repo.add_from_url(URL.format(9), name=self.NAME)
        return group

    def test_the_helper(self):
        from fbposter.qtui.widgets import literal

        assert literal("a & b") == "a && b"
        assert literal("plain") == "plain"

    def test_on_the_groups_list(self, qt_app, named):
        qt_app.show_view("groups")
        box = qt_app.views["groups"]._checkboxes[named.id]
        assert self.shows_the_ampersand(box)

    def test_on_a_set_box(self, qt_app, named):
        made = qt_app.group_set_repo.create("Buy & sell", [named.id])
        qt_app.show_view("groups")
        box = qt_app.views["groups"]._set_boxes[made.id]
        assert box.text() == "Buy && sell (1)"

    def test_in_the_group_set_editor(self, qt_app, named):
        view = qt_app.views["group_sets"]
        qt_app.show_view("group_sets")
        view.open_editor()
        assert self.shows_the_ampersand(view._boxes[named.id])
