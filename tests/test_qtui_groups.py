"""Tests for the Groups screen and the one-off date picker.

Groups is the middle step of the flow — Compose says what, this says where,
Publish says when — so its job is two things at once: choosing recipients and
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

from fbposter.qtui.views.publish import ONCE, SCHEDULE_HORIZON_DAYS, ScheduleEntry

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


class TestAddingAndRemoving:
    def test_a_new_group_can_be_added_from_here(self, qt_app, groups_view):
        groups_view.url_entry.setText("https://www.facebook.com/groups/brandnew")
        assert groups_view.add_group() is True
        assert len(qt_app.group_repo.list()) == 4

    def test_a_group_added_here_starts_ticked(self, qt_app, groups_view):
        """It was added in order to be posted to."""
        groups_view.url_entry.setText("https://www.facebook.com/groups/brandnew")
        groups_view.add_group()
        added = qt_app.group_repo.get_by_identifier("brandnew")
        assert added.id in qt_app.selected_group_ids()

    def test_the_url_box_is_cleared_on_success(self, groups_view):
        groups_view.url_entry.setText("https://www.facebook.com/groups/brandnew")
        groups_view.add_group()
        assert groups_view.url_entry.text() == ""

    def test_a_bad_url_is_refused_and_kept_for_editing(self, qt_app, groups_view):
        groups_view.url_entry.setText("https://www.google.com")
        assert groups_view.add_group() is False
        assert len(qt_app.group_repo.list()) == 3
        assert groups_view.url_entry.text() == "https://www.google.com"

    def test_a_duplicate_is_refused(self, qt_app, groups_view):
        groups_view.url_entry.setText(URL.format(0))
        assert groups_view.add_group() is False
        assert len(qt_app.group_repo.list()) == 3

    def test_removing_a_group_also_deselects_it(self, qt_app, groups_view):
        groups_view.set_all(True)
        group_id = qt_app.group_repo.list()[0].id
        groups_view.remove_group(group_id)
        assert group_id not in qt_app.selected_group_ids()
        assert group_id not in qt_app.selected_groups

    def test_the_cooldown_can_be_changed_here(self, qt_app, groups_view):
        group_id = qt_app.group_repo.list()[0].id
        groups_view.set_cooldown(group_id, 12)
        assert qt_app.group_repo.get(group_id).cooldown_hours == 12


class TestTheFlowBetweenScreens:
    def test_the_sidebar_reads_in_flow_order(self, qt_app):
        from fbposter.qtui.app import NAV_ITEMS

        assert [key for key, _label, _cls in NAV_ITEMS] == [
            "compose",
            "groups",
            "publish",
            "queue",
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

    def test_a_wording_tab_appears_per_chosen_group(self, qt_app, groups_view):
        compose = qt_app.views["compose"]
        groups_view.set_all(True)
        qt_app.show_view("compose")
        # "All groups" plus one per group.
        assert compose.tab_bar.count() == 4


class TestTheOnceDatePicker:
    """The 2028 report. The default was never wrong; the widget was."""

    def test_it_defaults_to_the_current_time(self, qt_application):
        """Not an hour out: an arbitrary offset is one more thing to undo."""
        entry = ScheduleEntry()
        assert abs(entry.dateTime().secsTo(QDateTime.currentDateTime())) < 60

    def test_the_default_has_whole_minutes(self, qt_application):
        assert ScheduleEntry().dateTime().time().second() == 0

    def test_it_does_not_default_two_years_out(self, qt_application):
        entry = ScheduleEntry()
        assert entry.dateTime().date().year() == QDateTime.currentDateTime().date().year()

    def test_the_cursor_starts_on_the_minutes_not_the_year(self, qt_application):
        """A stock QDateTimeEdit starts on the year, which is how one wheel
        notch became a year."""
        entry = ScheduleEntry()
        assert entry.currentSection() == QDateTimeEdit.MinuteSection

    def test_the_year_cannot_be_scrolled_into_the_future(self, qt_application):
        entry = ScheduleEntry()
        entry.setCurrentSection(QDateTimeEdit.YearSection)
        entry.stepBy(2)
        horizon = QDateTime.currentDateTime().addDays(SCHEDULE_HORIZON_DAYS)
        assert entry.dateTime() <= horizon

    def test_the_wheel_does_not_move_it_at_all(self, qt_application):
        entry = ScheduleEntry()
        before = entry.dateTime()
        event = QWheelEvent(
            QPoint(10, 10),
            entry.mapToGlobal(QPoint(10, 10)),
            QPoint(0, 0),
            QPoint(0, 120),
            Qt.NoButton,
            Qt.NoModifier,
            Qt.NoScrollPhase,
            False,
        )
        entry.wheelEvent(event)
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
        assert abs(entry.dateTime().secsTo(QDateTime.currentDateTime())) < 60

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
