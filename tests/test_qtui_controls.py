"""The controls themselves: that a click lands, and that every state shows.

Written after the Repeat time field's arrows were reported as "partially
working or not working at all". Both were true, for different reasons: the
text box lay over the left half of "up", so clicks there went into the text;
and with the cursor in the minutes, "down" at 09:00 did nothing. The times are
now picked from two lists (2026-10-14), but the Settings screen's number boxes
still have steppers, so the click tests below run against one of those.

The click tests use real hit-testing geometry under the real stylesheet, since
that is where the first bug lived -- no amount of calling stepBy() finds it.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest
from PySide6.QtCore import QDate, QDateTime, QPoint, Qt, QTime
from PySide6.QtGui import QColor, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QComboBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QVBoxLayout,
    QWidget,
)

from fbposter.qtui import theme
from fbposter.qtui.views.publish import (
    HOURS,
    MINUTE_STEP,
    MINUTES,
    ScheduleEntry,
    TimePicker,
)
from fbposter.qtui.widgets import AppStyle


def wheel_over(widget) -> QWheelEvent:
    return QWheelEvent(
        QPoint(10, 10),
        widget.mapToGlobal(QPoint(10, 10)),
        QPoint(0, 0),
        QPoint(0, 120),
        Qt.NoButton,
        Qt.NoModifier,
        Qt.NoScrollPhase,
        False,
    )


def choosable(combo: QComboBox) -> list[int]:
    return [
        combo.itemData(i)
        for i in range(combo.count())
        if combo.model().item(i).isEnabled()
    ]


class TestAClickOnAStepperLands:
    """The bug as reported: half of "up" was dead, under the text box.

    The number boxes it was found on went with the posting rules (2026-10-18),
    so a plain spin box carries the app's stylesheet here: the stepper rules
    are still in it, and the next number box gets them.
    """

    @pytest.fixture
    def entry(self, qt_application):
        holder = QWidget()
        holder.setStyleSheet(theme.stylesheet())
        layout = QVBoxLayout(holder)
        entry = QSpinBox()
        entry.setRange(0, 23)
        entry.setValue(9)
        layout.addWidget(entry)
        holder.resize(240, 60)
        holder.show()
        qt_application.processEvents()
        yield entry
        holder.close()

    @staticmethod
    def rect(entry, control):
        option = QStyleOptionSpinBox()
        entry.initStyleOption(option)
        return entry.style().subControlRect(QStyle.CC_SpinBox, option, control, entry)

    @pytest.mark.parametrize(
        "control", [QStyle.SC_SpinBoxUp, QStyle.SC_SpinBoxDown], ids=["up", "down"]
    )
    def test_the_text_box_covers_neither_arrow(self, entry, control):
        arrow = self.rect(entry, control)
        assert arrow.width() >= theme.STEPPER_WIDTH - 1
        assert not entry.lineEdit().geometry().intersects(arrow)

    @pytest.mark.parametrize(
        "control,expected",
        [(QStyle.SC_SpinBoxUp, "10"), (QStyle.SC_SpinBoxDown, "8")],
        ids=["up", "down"],
    )
    def test_every_part_of_each_arrow_steps(self, qt_application, entry, control, expected):
        arrow = self.rect(entry, control)
        points = [
            QPoint(x, y)
            for x in range(arrow.left() + 1, arrow.right(), 4)
            for y in range(arrow.top() + 1, arrow.bottom(), 4)
        ]
        assert points
        for point in points:
            # A real click goes to whatever child is under the pointer. The
            # text box being there is exactly the bug.
            assert entry.childAt(point) is None, f"{point} lands in the text box"
            entry.setValue(9)
            QTest.mouseClick(entry, Qt.LeftButton, Qt.NoModifier, point)
            qt_application.processEvents()
            assert entry.text() == expected, f"click at {point} did not step"


class TestATimeIsPicked:
    """The typed field with arrows, replaced by two lists (2026-10-14)."""

    def test_the_hours_are_the_whole_day(self, qt_application):
        picker = TimePicker()
        shown = [picker.hour.itemData(i) for i in range(picker.hour.count())]
        assert shown == list(HOURS) == list(range(24))

    def test_the_minutes_come_every_quarter_hour(self, qt_application):
        picker = TimePicker()
        shown = [picker.minute.itemData(i) for i in range(picker.minute.count())]
        assert shown == list(MINUTES) == list(range(0, 60, MINUTE_STEP))
        assert MINUTE_STEP == 15

    def test_it_shows_two_digits(self, qt_application):
        picker = TimePicker(7, 0)
        assert (picker.hour.currentText(), picker.minute.currentText()) == ("07", "00")

    def test_it_reads_back_what_was_set(self, qt_application):
        picker = TimePicker()
        picker.setTime(QTime(21, 45))
        assert picker.time() == QTime(21, 45)

    def test_a_saved_time_off_the_quarter_is_kept(self, qt_application):
        """A post saved at 09:10 must not quietly move when it is edited."""
        picker = TimePicker(9, 10)
        assert picker.time() == QTime(9, 10)
        shown = [picker.minute.itemData(i) for i in range(picker.minute.count())]
        assert shown == sorted(shown)

    def test_picking_says_so(self, qt_application):
        picker = TimePicker(9, 0)
        seen = []
        picker.timeChanged.connect(seen.append)
        picker.hour.setCurrentIndex(picker.hour.findData(18))
        picker.minute.setCurrentIndex(picker.minute.findData(30))
        assert seen == [QTime(18, 0), QTime(18, 30)]

    def test_nothing_is_typed(self, qt_application):
        picker = TimePicker()
        assert not picker.hour.isEditable()
        assert not picker.minute.isEditable()

    def test_the_wheel_does_not_move_it(self, qt_application):
        """Scrolling the page past a time must not change when a post goes out."""
        picker = TimePicker(9, 0)
        for part in (picker.hour, picker.minute):
            part.wheelEvent(wheel_over(part))
        assert picker.time() == QTime(9, 0)


class TestTheOneOffPickerStaysInRange:
    def test_a_past_hour_today_is_greyed_out(self, qt_application):
        entry = ScheduleEntry()
        earliest = entry.earliest()
        if earliest.date() != QDate.currentDate():
            pytest.skip("too close to midnight: no time of today is left")
        assert choosable(entry.time_picker.hour) == list(range(earliest.time().hour(), 24))

    def test_every_time_of_a_later_day_can_be_picked(self, qt_application):
        entry = ScheduleEntry()
        entry.setDateTime(QDateTime(QDate.currentDate().addDays(2), QTime(10, 0)))
        assert choosable(entry.time_picker.hour) == list(HOURS)
        assert choosable(entry.time_picker.minute) == list(MINUTES)

    def test_a_past_hour_picked_anyway_moves_to_the_first_left(self, qt_application):
        entry = ScheduleEntry()
        earliest = entry.earliest()
        if earliest.time().hour() == 0:
            pytest.skip("no earlier hour today")
        hour = entry.time_picker.hour
        hour.setCurrentIndex(hour.findData(earliest.time().hour() - 1))
        assert entry.dateTime() == earliest

    def test_today_picked_with_its_time_gone_moves_to_the_first_left(self, qt_application):
        entry = ScheduleEntry()
        earliest = entry.earliest()
        entry.setDateTime(QDateTime(earliest.date().addDays(1), QTime(0, 0)))
        entry.date_entry.setDate(earliest.date())
        assert entry.dateTime() == earliest

    def test_it_cannot_go_past_the_horizon(self, qt_application):
        entry = ScheduleEntry()
        entry.setDateTime(entry.maximumDateTime().addDays(3))
        assert entry.dateTime() <= entry.maximumDateTime()


class TestTheCalendar:
    def test_the_week_starts_on_sunday(self, qt_application):
        """As it does in Israel (the user's choice, 2026-10-14)."""
        entry = ScheduleEntry()  # the calendar dies with it
        assert entry.calendarWidget().firstDayOfWeek() == Qt.Sunday

    def test_the_weekend_is_not_painted_red(self, qt_application):
        entry = ScheduleEntry()  # the calendar dies with it
        calendar = entry.calendarWidget()
        for day in (Qt.Saturday, Qt.Sunday):
            assert calendar.weekdayTextFormat(day).foreground().style() == Qt.NoBrush

    def test_past_days_are_dimmed_and_today_is_not(self, qt_application):
        entry = ScheduleEntry()  # the calendar dies with it
        calendar = entry.calendarWidget()
        today = QDate.currentDate()
        dim = QColor(theme.C["BORDER_STRONG"])
        assert calendar.dateTextFormat(today.addDays(-1)).foreground().color() == dim
        assert calendar.dateTextFormat(today).foreground().style() == Qt.NoBrush


class TestTheGroupsScreenHasNoCooldownControl:
    def test_no_row_offers_a_number_to_change(self, qt_app):
        """One rule for every group; breaking it is "Post anyway", per post."""
        qt_app.group_repo.add_from_url("https://www.facebook.com/groups/42/", name="G")
        qt_app.show_view("groups")
        assert qt_app.views["groups"].findChildren(QSpinBox) == []


class TestPublishReadsWhatWasTyped:
    def test_a_half_typed_date_is_taken_before_publishing(self, qt_app, monkeypatch):
        """Buttons no longer take focus from a field on a click, and losing
        focus is what made Qt read what had been typed. The day can still be
        typed; the times are picked from lists."""
        publish = qt_app.views["publish"]
        seen = []
        entry = publish.schedule_entry
        monkeypatch.setattr(entry, "interpretText", lambda: seen.append(entry))
        publish.publish()
        assert seen == [entry]

    def test_a_time_row_does_not_paint_the_window_colour(self, qt_app):
        """It sits in the white "When" card; a bare QWidget drew a grey band."""
        publish = qt_app.views["publish"]
        assert publish._time_rows[0].parentWidget().objectName() == "Row"


class TestAppStyle:
    def test_a_button_gets_a_hand_and_keyboard_only_focus(self, qt_application):
        button = QPushButton("Go")
        AppStyle().polish(button)
        assert button.cursor().shape() == Qt.PointingHandCursor
        assert button.focusPolicy() == Qt.TabFocus

    def test_a_drop_down_gets_a_hand_but_keeps_click_focus(self, qt_application):
        combo = QComboBox()
        before = combo.focusPolicy()
        AppStyle().polish(combo)
        assert combo.cursor().shape() == Qt.PointingHandCursor
        assert combo.focusPolicy() == before

    def test_a_field_is_left_alone(self, qt_application):
        spin = QSpinBox()
        before = spin.focusPolicy()
        AppStyle().polish(spin)
        assert spin.focusPolicy() == before
        assert spin.cursor().shape() != Qt.PointingHandCursor

    def test_the_real_window_installs_it_before_the_stylesheet(self):
        """The stylesheet wraps whatever style is installed when it is set,
        and it is the window's, set as the window is built (App.__init__)."""
        from fbposter.qtui import app

        source = inspect.getsource(app.run)
        assert "setStyle(AppStyle())" in source
        assert source.index("setStyle(AppStyle())") < source.index("stylesheet=theme.stylesheet()")


@pytest.fixture(params=[False, True], ids=["light", "dark"])
def sheet(request):
    theme.activate(dark=request.param)
    yield theme.stylesheet()
    theme.activate(dark=False)


class TestTheStylesheet:
    def test_every_icon_it_names_exists(self, sheet):
        urls = re.findall(r"url\(([^)]+)\)", sheet)
        assert len(urls) >= 8
        for url in urls:
            assert "\\" not in url, f"{url}: a backslash is an escape in a stylesheet"
            assert Path(url).exists(), url

    @pytest.mark.parametrize("name", theme.ICON_NAMES)
    @pytest.mark.parametrize("mode", ["light", "dark"])
    def test_every_icon_exists_in_both_modes(self, name, mode):
        assert theme.icon_path(name, mode).exists()

    @pytest.mark.parametrize(
        "selector",
        [
            "QPushButton:pressed",
            "QPushButton#Primary:pressed",
            "QPushButton#Nav:pressed",
            "QPushButton#Tab:checked:pressed",
            "QPushButton#Link:pressed",
            "QCheckBox::indicator:pressed",
            "QAbstractSpinBox::up-button:pressed",
        ],
    )
    def test_every_kind_of_button_shows_it_is_pressed(self, sheet, selector):
        """A button that does not change as it is pressed feels as if the
        click did not land."""
        assert selector in sheet

    @pytest.mark.parametrize("kind", ["QPushButton", "QPushButton#Primary"])
    def test_pressed_is_stated_after_focus(self, sheet, kind):
        """Both are true at the moment of a click; the later rule wins."""
        assert sheet.index(f"{kind}:pressed") > sheet.index(f"{kind}:focus")

    def test_the_text_leaves_room_for_the_steppers(self, sheet):
        needed = theme.STEPPER_INSET + 2 * theme.STEPPER_WIDTH
        assert theme.STEPPER_ROOM > needed
        assert f"padding-right: {theme.STEPPER_ROOM}px" in sheet

    def test_an_arrow_at_its_limit_is_dimmed(self, sheet):
        assert "up-arrow:off" in sheet and "down-arrow:off" in sheet


class TestTheCalendarYearBoxFits:
    """Clicking the year opens a spin box, and the step-button rules reached
    it: sized from its hint, with 68px of room for buttons, it ran off the
    calendar's edge and covered the next-month arrow."""

    def test_it_leaves_the_next_month_arrow_clear(self, qt_application):
        from PySide6.QtWidgets import QToolButton

        holder = QWidget()
        holder.setStyleSheet(theme.stylesheet())
        layout = QVBoxLayout(holder)
        entry = ScheduleEntry()
        calendar = entry.calendarWidget()
        layout.addWidget(calendar)
        holder.resize(320, 280)
        holder.show()
        holder.grab()  # lays the navigation bar out
        qt_application.processEvents()

        calendar.findChild(QToolButton, "qt_calendar_yearbutton").click()
        qt_application.processEvents()
        year = calendar.findChild(QSpinBox, "qt_calendar_yearedit")
        text = year.lineEdit()
        needed = text.fontMetrics().horizontalAdvance(text.text())
        assert text.text().isdigit()
        assert text.width() >= needed, f"{text.width()}px for {needed}px of year"
        after = calendar.findChild(QToolButton, "qt_calendar_nextmonth")
        assert year.parentWidget() is after.parentWidget()
        assert year.geometry().right() < after.geometry().left(), (
            f"year box {year.geometry().getRect()} covers {after.geometry().getRect()}"
        )
        holder.close()
