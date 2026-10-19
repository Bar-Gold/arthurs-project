"""Publish screen: the one place that decides *when* a post goes out.

The last step of three: Compose says what, Groups says where, this says when.
It owns nothing but the moment, in three modes:

* **Post now**: straight onto the queue.
* **Schedule**: once, at a chosen moment.
* **Repeat**: daily, or on chosen days, at as many times of day as wanted.

Repeat rotates wordings: the Compose text is the first, this screen collects
alternates, and each run hands them out across the groups, so one run never
sends the same text to all of them. Nothing refuses a repeat (the user's
choice, 2026-10-18): varying the wording is advice, on the Posting tips screen.

Nothing here talks to a browser. Post now and Schedule hand the work to
`ComposeView.add_to_queue`, and Repeat writes a definition the worker
materialises into an ordinary batch when its moment arrives -- so the serial
queue and the gap between posts apply unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import QDate, QDateTime, QTime, Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QButtonGroup,
    QCalendarWidget,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock, recurrence
from fbposter.text import strip_invisible
from fbposter.db.models import SCHEDULE_ACTIVE, SCHEDULE_PAUSED, utcnow

from .. import theme
from ..widgets import badge, card, clear, row, segmented, set_note_lines
from .compose import media_only

RIGHT_COLUMN_WIDTH = 340
# Room for the column's scroll bar, so the column keeps its width when it shows.
RIGHT_COLUMN_SCROLLBAR = 12
WORDING_MIN_HEIGHT = 80
DEFAULT_TIMES = ("09:00", "18:00", "21:00")
SNIPPET_CHARS = 90

NOW = "now"
ONCE = "once"
REPEAT = "repeat"

BUTTON_LABELS = {
    NOW: "Post now",
    ONCE: "Add to queue",
    REPEAT: "Start repeating",
}

# How far ahead a one-off post may be scheduled. Not a technical limit -- it is
# the range in which a mis-scroll is recoverable. Without a ceiling the year
# section took a mouse wheel notch straight to 2028, which is what happened.
SCHEDULE_HORIZON_DAYS = 365

# The minutes a time can be set to (the user's choice, 2026-10-14). A time
# saved earlier on another minute keeps it; see TimeList.set_value.
MINUTE_STEP = 15
HOURS = tuple(range(24))
MINUTES = tuple(range(0, 60, MINUTE_STEP))
TIME_LIST_WIDTH = 96


def next_slot(moment: QDateTime) -> QDateTime:
    """The first whole `MINUTE_STEP` at or after `moment`.

    Seconds count: at 14:45:20, 14:45 is already past, so it is 15:00. Dropping
    them offered a time in the past for the first minute of every quarter hour.
    """
    whole = QDateTime(moment.date(), QTime(moment.time().hour(), moment.time().minute()))
    if whole < moment:
        whole = whole.addSecs(60)
    late = whole.time().minute() % MINUTE_STEP
    return whole.addSecs((MINUTE_STEP - late) * 60) if late else whole


def last_slot(moment: QDateTime) -> QDateTime:
    """The last whole `MINUTE_STEP` at or before `moment`."""
    moment = QDateTime(moment.date(), QTime(moment.time().hour(), moment.time().minute()))
    return moment.addSecs(-(moment.time().minute() % MINUTE_STEP) * 60)


def day_boxes(grid: QGridLayout, on_change, checked: bool = False) -> list[QCheckBox]:
    """The seven day boxes, Sunday first, as the week runs in Israel.

    The list is indexed by `datetime.weekday()` (0 is Monday), which is how a
    schedule stores its days; only the order on screen starts on Sunday (the
    user's choice, 2026-10-14). Four and three rather than seven across: at the
    column's width seven clip their own labels down to "Mo".
    """
    boxes: dict[int, QCheckBox] = {}
    for position, day in enumerate(recurrence.WEEK_ORDER):
        box = QCheckBox(recurrence.DAY_NAMES[day])
        box.setChecked(checked)
        box.stateChanged.connect(lambda _s: on_change())
        grid.addWidget(box, position // 4, position % 4)
        boxes[day] = box
    return [boxes[day] for day in range(7)]


def following(set_names) -> str | None:
    """The one thing a repeating post linked to group sets does differently,
    said where it is set up and where it is edited."""
    if not set_names:
        return None
    them = "it" if len(set_names) == 1 else "them"
    return (
        f"Follows {', '.join(set_names)}: a group added to {them} later gets "
        "this post from its next run."
    )


def next_time(taken) -> str:
    """The time a newly added row starts on.

    The next of DEFAULT_TIMES not already listed, then the first whole hour
    after the last time that is free. There is no limit on how many times a
    day (the user's choice, 2026-10-18), so a fourth row must not simply
    repeat the third.
    """
    taken = list(taken)
    for text in DEFAULT_TIMES:
        if text not in taken:
            return text
    hour = recurrence.parse_hhmm(taken[-1])[0] if taken else 0
    for step in range(1, 25):
        text = f"{(hour + step) % 24:02d}:00"
        if text not in taken:
            return text
    return DEFAULT_TIMES[0]


class TimeList(QComboBox):
    """A short list of numbers to pick from: the hours, or the minutes.

    Picked, never typed, and the wheel does nothing: scrolling the page past
    it must not change when a post goes out.
    """

    def __init__(self, values, name: str) -> None:
        super().__init__()
        # The default delegate ignores the stylesheet's ::item rules.
        self.setItemDelegate(QStyledItemDelegate(self))
        self.setMaxVisibleItems(8)
        self.setAccessibleName(name)
        # Two digits and the arrow. Qt's own guess was half as wide again,
        # and pushed the column past the edge of the window.
        self.setFixedWidth(TIME_LIST_WIDTH)
        for value in values:
            self.addItem(f"{value:02d}", value)

    def value(self) -> int:
        return self.currentData()

    def set_value(self, value: int) -> None:
        """Choose `value`, adding it to the list if it is not there.

        A repeating post saved at 09:10, before the minutes came in steps,
        must open as 09:10 and save as 09:10, not quietly move to 09:15.
        """
        index = self.findData(value)
        if index < 0:
            index = sum(1 for i in range(self.count()) if self.itemData(i) < value)
            self.insertItem(index, f"{value:02d}", value)
        self.setCurrentIndex(index)

    def set_choosable(self, index: int, choosable: bool) -> None:
        """Grey out one entry, as the calendar greys out a past day."""
        self.model().item(index).setEnabled(choosable)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        event.ignore()


class TimePicker(QWidget):
    """A time of day as two lists, the hour and the minutes.

    It replaced a typed field with arrows, which the user found awkward to use
    and to change (2026-10-14). There is nothing to type, so nothing is ever
    half-typed, and no arrow can be dead.
    """

    timeChanged = Signal(QTime)  # noqa: N815 - named like QTimeEdit's

    def __init__(self, hour: int = 9, minute: int = 0) -> None:
        super().__init__()
        # Transparent, like row(): it sits inside a card.
        self.setObjectName("Row")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_XS)
        self.hour = TimeList(HOURS, "Hour")
        self.minute = TimeList(MINUTES, "Minutes")
        layout.addWidget(self.hour)
        layout.addWidget(QLabel(":"))
        layout.addWidget(self.minute)
        self.hour.set_value(hour)
        self.minute.set_value(minute)
        self.hour.currentIndexChanged.connect(lambda _i: self._changed())
        self.minute.currentIndexChanged.connect(lambda _i: self._changed())

    def time(self) -> QTime:
        return QTime(self.hour.value(), self.minute.value())

    def setTime(self, time: QTime) -> None:  # noqa: N802 - named like QTimeEdit's
        before = self.time()
        for part, value in ((self.hour, time.hour()), (self.minute, time.minute())):
            part.blockSignals(True)
            part.set_value(value)
            part.blockSignals(False)
        if self.time() != before:
            self._changed()

    def _changed(self) -> None:
        self.timeChanged.emit(self.time())


class DateEntry(QDateEdit):
    """The one-off post's day: a calendar drop-down that ignores the wheel.

    A stock date field opens on the *year* and moves with the mouse wheel, so
    one scroll over it moved a post a year out; two notches read as "the
    default is 2028". It starts on the day instead, and the wheel does nothing.
    """

    def __init__(self) -> None:
        super().__init__()
        # Styled as a drop-down rather than a stepper; see theme.py.
        self.setObjectName("DateEntry")
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd")
        self.setAccessibleName("Date")
        self.setCurrentSection(QDateTimeEdit.DaySection)
        self._quiet_calendar()

    def _quiet_calendar(self) -> None:
        """Take the platform's colours out of the calendar; theme.py adds ours.

        Qt paints Saturday and Sunday red, from a locale default that is not
        even Israel's weekend, and shades the day-name row grey. An empty
        format drops the red without overriding the dimming of days that are
        out of range. The week starts on Sunday, as it does in Israel.
        """
        calendar = self.calendarWidget()
        calendar.setFirstDayOfWeek(Qt.Sunday)
        calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        calendar.setGridVisible(False)
        for day in (Qt.Saturday, Qt.Sunday):
            calendar.setWeekdayTextFormat(day, QTextCharFormat())
        header = QTextCharFormat()
        header.setForeground(QColor(theme.C["TEXT_MUTED"]))
        header.setBackground(QColor(theme.C["SURFACE"]))
        calendar.setHeaderTextFormat(header)

    def dim_past_days(self, today: QDate) -> None:
        """Grey out the days before today, which cannot be picked.

        Qt marks an out-of-range day only by giving it the window colour as a
        background, and the calendar is styled one colour throughout -- so the
        past looked exactly as choosable as the future. Six weeks back covers
        every day a month page can show.
        """
        calendar = self.calendarWidget()
        calendar.setDateTextFormat(QDate(), QTextCharFormat())  # null date: clear all
        past = QTextCharFormat()
        past.setForeground(QColor(theme.C["BORDER_STRONG"]))
        for back in range(1, 43):
            calendar.setDateTextFormat(today.addDays(-back), past)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Scrolling must never change when a post goes out."""
        event.ignore()


class ScheduleEntry(QWidget):
    """The one-off moment: a day from a calendar, a time from two lists.

    Bounded to `[now, now + SCHEDULE_HORIZON_DAYS]`. An hour or a minute that
    would land outside it is greyed out in its list, and a day picked with a
    time already gone moves the time to the first one left.
    """

    dateTimeChanged = Signal(QDateTime)  # noqa: N815 - named like QDateTimeEdit's

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Row")
        # The day above the time: side by side they are wider than the column.
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_XS)
        self.date_entry = DateEntry()
        self.time_picker = TimePicker()
        layout.addWidget(self.date_entry)
        layout.addWidget(self.time_picker, 0, Qt.AlignLeft)
        self._minimum = QDateTime.currentDateTime()
        self._maximum = self._minimum.addDays(SCHEDULE_HORIZON_DAYS)
        self._shown = QDateTime()
        self.date_entry.dateChanged.connect(lambda _d: self._parts_changed())
        self.time_picker.timeChanged.connect(lambda _t: self._parts_changed())
        self.reset()

    def calendarWidget(self) -> QCalendarWidget:  # noqa: N802 - Qt's name
        return self.date_entry.calendarWidget()

    def dateTime(self) -> QDateTime:  # noqa: N802 - Qt's name
        return QDateTime(self.date_entry.date(), self.time_picker.time())

    def minimumDateTime(self) -> QDateTime:  # noqa: N802 - Qt's name
        return self._minimum

    def maximumDateTime(self) -> QDateTime:  # noqa: N802 - Qt's name
        return self._maximum

    def setMinimumDateTime(self, moment: QDateTime) -> None:  # noqa: N802 - Qt's name
        self._minimum = moment
        self._apply_range()

    def setMaximumDateTime(self, moment: QDateTime) -> None:  # noqa: N802 - Qt's name
        self._maximum = moment
        self._apply_range()

    def earliest(self) -> QDateTime:
        """The first time on the lists that is not in the past."""
        return next_slot(self._minimum)

    def latest(self) -> QDateTime:
        return last_slot(self._maximum)

    def _apply_range(self) -> None:
        self.date_entry.blockSignals(True)
        self.date_entry.setDateRange(self.earliest().date(), self.latest().date())
        self.date_entry.blockSignals(False)
        self.setDateTime(self.dateTime())

    def setDateTime(self, moment: QDateTime) -> None:  # noqa: N802 - Qt's name
        """Show `moment`, moved inside the range if it is outside it."""
        moment = max(self.earliest(), min(moment, self.latest()))
        self.date_entry.blockSignals(True)
        self.time_picker.blockSignals(True)
        self.date_entry.setDate(moment.date())
        self.time_picker.setTime(moment.time())
        self.date_entry.blockSignals(False)
        self.time_picker.blockSignals(False)
        self._mark_choosable()
        self._announce()

    def reset(self) -> None:
        """Default to the next quarter hour, and bound the range around now.

        The next quarter rather than an hour out: an arbitrary offset is one
        more thing to notice and undo. The current minute is not on the list,
        and the quarter before it has already gone.

        Called every time the panel is shown rather than once at startup: the
        app is left open for days, and a default set at launch is a time in the
        past by the afternoon.
        """
        now = QDateTime.currentDateTime()
        self._minimum = now
        self._maximum = now.addDays(SCHEDULE_HORIZON_DAYS)
        self.date_entry.blockSignals(True)
        self.date_entry.setDateRange(self.earliest().date(), self.latest().date())
        self.date_entry.blockSignals(False)
        self.date_entry.setCurrentSection(QDateTimeEdit.DaySection)
        self.date_entry.dim_past_days(now.date())
        self.setDateTime(self.earliest())

    def interpretText(self) -> None:  # noqa: N802 - Qt's name
        """The day can still be typed; take a half-typed one at its word."""
        self.date_entry.interpretText()

    def _parts_changed(self) -> None:
        moment = self.dateTime()
        if moment < self.earliest() or moment > self.latest():
            self.setDateTime(moment)
            return
        self._mark_choosable()
        self._announce()

    def _mark_choosable(self) -> None:
        """Grey out every hour and minute that would fall outside the range."""
        day = self.date_entry.date()
        low, high = self.earliest(), self.latest()
        hours, minutes = self.time_picker.hour, self.time_picker.minute
        minute_values = [minutes.itemData(i) for i in range(minutes.count())]

        def fits(hour: int, minute: int) -> bool:
            return low <= QDateTime(day, QTime(hour, minute)) <= high

        for index in range(hours.count()):
            hour = hours.itemData(index)
            hours.set_choosable(index, any(fits(hour, m) for m in minute_values))
        for index, minute in enumerate(minute_values):
            minutes.set_choosable(index, fits(hours.value(), minute))

    def _announce(self) -> None:
        moment = self.dateTime()
        if moment != self._shown:
            self._shown = moment
            self.dateTimeChanged.emit(moment)


class PublishView(QWidget):
    title = "Publish"
    subtitle = "The last step: now, at a set time, or on repeat."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # The recipient rows as drawn; None means nothing has been drawn yet.
        self._recipients_shown = None
        self.mode = NOW
        self._wordings: list[QTextEdit] = []
        self._time_rows: list[TimePicker] = []
        self._day_boxes: list[QCheckBox] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        # Hidden inside Create a post, which has one title for all three steps.
        self.heading = QLabel(self.title)
        self.heading.setObjectName("Title")
        outer.addWidget(self.heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        outer.addSpacing(theme.HEADER_GAP)

        body = QHBoxLayout()
        body.setSpacing(theme.PAD_M)
        outer.addLayout(body, 1)
        self._build_left(body)
        self._build_right(body)

        self.add_time(DEFAULT_TIMES[0])
        self.set_mode(NOW)

    # -- the Compose side of things ----------------------------------------
    @property
    def compose(self):
        return self.app.views["compose"]

    def on_show(self) -> None:
        # Compose may still have uncommitted keystrokes in its editor; body_for
        # reads committed state only, so capture first or this screen would
        # publish the text from before the last edit.
        self.compose.capture()
        self.refresh_recipients()
        self.refresh_schedules()
        self.refresh_summary()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    def selected_group_ids(self) -> list[int]:
        """Chosen on the Groups screen. This view only reads them."""
        return self.app.selected_group_ids()

    def picked_set_names(self) -> list[str]:
        """The group sets picked on the Groups screen, by name."""
        picked = self.app.selected_sets
        return [s.name for s in self.app.group_set_repo.list() if s.id in picked]

    def base_body(self) -> str:
        return self.compose.base_body().strip()

    # -- left column: what is about to go out ------------------------------
    def _build_left(self, parent: QHBoxLayout) -> None:
        column = QVBoxLayout()
        column.setSpacing(theme.PAD_XS)
        parent.addLayout(column, 1)

        going = QLabel("Going out")
        going.setObjectName("SectionHeading")
        column.addWidget(going)

        self.recipients = card()
        recipients_layout = QVBoxLayout(self.recipients)
        recipients_layout.setContentsMargins(
            theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S
        )
        recipients_layout.setSpacing(theme.PAD_XS)
        self.recipient_box = QVBoxLayout()
        self.recipient_box.setSpacing(theme.PAD_XS)
        recipients_layout.addLayout(self.recipient_box)
        column.addWidget(self.recipients)

        self.preview_note = QLabel("")
        self.preview_note.setObjectName("Muted")
        self.preview_note.setWordWrap(True)
        column.addWidget(self.preview_note)

        # Everything below is Repeat-only, and lives in one container so that
        # hiding it collapses cleanly. Hiding the widgets one by one left their
        # spacing behind and spread the rest of the column down the screen.
        self.repeat_extras = QWidget()
        extras = QVBoxLayout(self.repeat_extras)
        extras.setContentsMargins(0, 0, 0, 0)
        extras.setSpacing(theme.PAD_XS)

        header = QHBoxLayout()
        label = QLabel("Alternate wordings")
        label.setObjectName("SectionHeading")
        header.addWidget(label)
        header.addStretch(1)
        self.add_wording_button = QPushButton("Add wording")
        self.add_wording_button.clicked.connect(lambda: self.add_wording())
        header.addWidget(self.add_wording_button)
        extras.addLayout(header)

        # The empty state says what a wording is for: with none added, every
        # run sends every group the Compose text.
        self.wording_hint = QLabel(
            "No alternates yet, so every run sends the Compose text. Add other "
            "wordings and each run shares them out across the groups."
        )
        self.wording_hint.setObjectName("Muted")
        self.wording_hint.setWordWrap(True)
        extras.addWidget(self.wording_hint)

        self.wording_area = QScrollArea()
        self.wording_area.setWidgetResizable(True)
        holder = QWidget()
        self.wording_box = QVBoxLayout(holder)
        self.wording_box.setContentsMargins(0, 0, 0, 0)
        self.wording_box.setSpacing(theme.PAD_S)
        self.wording_box.addStretch(1)
        self.wording_area.setWidget(holder)
        extras.addWidget(self.wording_area, 1)

        self.rotation_note = QLabel("")
        self.rotation_note.setObjectName("Muted")
        self.rotation_note.setWordWrap(True)
        extras.addWidget(self.rotation_note)

        existing_label = QLabel("Existing repeating posts")
        existing_label.setObjectName("SectionHeading")
        extras.addWidget(existing_label)

        self.schedule_area = QScrollArea()
        self.schedule_area.setWidgetResizable(True)
        schedule_holder = QWidget()
        self.schedule_box = QVBoxLayout(schedule_holder)
        self.schedule_box.setContentsMargins(0, 0, 0, 0)
        self.schedule_box.setSpacing(theme.PAD_XS)
        self.schedule_area.setWidget(schedule_holder)
        self.schedule_area.setMinimumHeight(160)
        extras.addWidget(self.schedule_area)
        column.addWidget(self.repeat_extras, 1)

        # Takes the slack in the two modes where the extras are hidden, so the
        # recipients card stays at the top instead of drifting to the middle.
        self.filler = QWidget()
        column.addWidget(self.filler, 1)

    def _recipient_plan(self, selected) -> list[tuple[int, str, bool]]:
        """The rows as (id, name, reworded) -- what to draw, not the drawing.

        One query for every group rather than one per recipient. This runs on
        every refresh, ahead of the guard, so it is the read that never gets
        skipped; it is still a fresh read, just not a round trip per row.
        """
        names = {group.id: group.display_name for group in self.app.group_repo.list()}
        return [
            (group_id, names[group_id], self.compose.has_rewrite(group_id))
            for group_id in selected
            if group_id in names
        ]

    def refresh_recipients(self) -> None:
        selected = self.selected_group_ids()
        body = self.base_body()

        # The snippet is a setText and always runs; only the rows are guarded.
        # The ellipsis only when something was cut: after a whole post it
        # read as though the rest of it was missing.
        flat = " ".join(body.split())
        snippet = flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
        if snippet:
            note = f"“{snippet}”"
        elif self.compose.attachments:
            note = f"{media_only(self.compose.attachments)}."
        else:
            note = "Nothing written yet. Start in step 1."
        self.preview_note.setText(note)

        # None never equals a tuple, so the first call always draws -- including
        # the empty case, which has its own "no groups picked" note.
        sets = tuple(self.picked_set_names())
        plan = (sets, self._recipient_plan(selected))
        if plan == self._recipients_shown:
            return
        self._recipients_shown = plan

        clear(self.recipient_box)
        if not selected:
            note = QLabel("No groups picked. Choose them in step 2.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.recipient_box.addWidget(note)
        if sets:
            source = QLabel(f"Group sets: {', '.join(sets)}")
            source.setObjectName("Muted")
            source.setWordWrap(True)
            self.recipient_box.addWidget(source)
        plan = plan[1]
        for group_id, name, reworded in plan:
            holder = row()
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(name), 1)
            if reworded:
                tag = QLabel("reworded")
                tag.setObjectName("Muted")
                line.addWidget(tag)
            self.recipient_box.addWidget(holder)

    # -- right column: when ------------------------------------------------
    def _build_right(self, parent: QHBoxLayout) -> None:
        column = QVBoxLayout()
        column.setSpacing(theme.PAD_S)
        holder = QWidget()
        holder.setLayout(column)
        # Scrolls rather than squashes. Repeat mode with many times of day is
        # taller than the window, and a plain column then crushed the "When"
        # card until its name field, times and days were drawn on top of one
        # another.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(RIGHT_COLUMN_WIDTH + RIGHT_COLUMN_SCROLLBAR)
        scroll.setWidget(holder)
        parent.addWidget(scroll)

        picker = card()
        picker_layout = QVBoxLayout(picker)
        picker_layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        picker_layout.setSpacing(theme.PAD_S)
        label = QLabel("When")
        label.setObjectName("SectionHeading")
        picker_layout.addWidget(label)

        choices = ((NOW, "Now"), (ONCE, "Once"), (REPEAT, "Repeat"))
        toggle, buttons = segmented([text for _key, text in choices], expand=True)
        self.mode_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        for index, ((key, _text), button) in enumerate(zip(choices, buttons)):
            group.addButton(button, index)
            button.clicked.connect(lambda _c, k=key: self.set_mode(k))
            self.mode_buttons[key] = button
        picker_layout.addWidget(toggle)

        # One slot, three sets of controls -- the same trick Compose uses for
        # Write/Preview, so the column does not jump around as modes change.
        self.slot = QStackedWidget()
        self.slot.addWidget(self._build_now_panel())
        self.slot.addWidget(self._build_once_panel())
        self.slot.addWidget(self._build_repeat_panel())
        picker_layout.addWidget(self.slot)
        column.addWidget(picker)

        # The summary and the button that acts on it sit directly under the
        # card they describe. They used to be anchored to the bottom of the
        # window with the stretch above them, which in Now and Once modes --
        # where the panel is one line -- left a void most of the screen tall
        # between the choice and the button that carries it out.
        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

        self.go_button = QPushButton(BUTTON_LABELS[NOW])
        self.go_button.setObjectName("Primary")
        # A lambda, so clicked() cannot pass its `checked` flag into publish().
        self.go_button.clicked.connect(lambda: self.publish())
        column.addWidget(self.go_button)

        back = QPushButton("← Back: choose the groups")
        back.clicked.connect(lambda: self.app.show_view("groups"))
        column.addWidget(back)

        # The slack goes below everything, so the column reads top-down and
        # grows downwards when Repeat opens.
        column.addStretch(1)

    def _build_now_panel(self) -> QWidget:
        panel = row()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel(
            "Goes onto the queue straight away. If a batch is already running "
            "this one waits behind it, never alongside."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return panel

    def _build_once_panel(self) -> QWidget:
        panel = row()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_XS)
        self.schedule_entry = ScheduleEntry()
        self.schedule_entry.dateTimeChanged.connect(lambda _d: self.refresh_summary())
        layout.addWidget(self.schedule_entry)
        note = QLabel(
            f"Israel time, like every time in this app. Up to "
            f"{SCHEDULE_HORIZON_DAYS} days ahead."
        )
        note.setObjectName("Muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        return panel

    def _build_repeat_panel(self) -> QWidget:
        panel = row()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.PAD_XS)

        name_label = QLabel("Name")
        name_label.setObjectName("SectionHeading")
        layout.addWidget(name_label)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText("e.g. Bikes for sale")
        layout.addWidget(self.name_entry)

        header = QHBoxLayout()
        times_label = QLabel("Times of day")
        times_label.setObjectName("SectionHeading")
        header.addWidget(times_label)
        header.addStretch(1)
        self.add_time_button = QPushButton("Add a time")
        self.add_time_button.clicked.connect(lambda: self.add_time())
        header.addWidget(self.add_time_button)
        layout.addLayout(header)

        self.time_box = QVBoxLayout()
        self.time_box.setSpacing(theme.PAD_XS)
        layout.addLayout(self.time_box)

        days_label = QLabel("Days")
        days_label.setObjectName("SectionHeading")
        layout.addWidget(days_label)
        days_grid = QGridLayout()
        days_grid.setSpacing(theme.PAD_XS)
        self._day_boxes = day_boxes(days_grid, self.refresh_summary, checked=True)
        layout.addLayout(days_grid)
        return panel

    # -- modes -------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        was = self.mode
        self.mode = mode
        for key, button in self.mode_buttons.items():
            button.setChecked(key == mode)
        if mode == ONCE and was != ONCE:
            # Fresh every time it is opened; see ScheduleEntry.reset.
            self.schedule_entry.reset()
        self.slot.setCurrentIndex({NOW: 0, ONCE: 1, REPEAT: 2}[mode])
        self.go_button.setText(BUTTON_LABELS[mode])

        # A QStackedWidget is as tall as its tallest page, which left a large
        # void under the one-line Now panel. Ignoring the height of every page
        # but the current one makes the card fit what is actually showing.
        for index in range(self.slot.count()):
            page = self.slot.widget(index)
            page.setSizePolicy(
                QSizePolicy.Preferred,
                QSizePolicy.Preferred if index == self.slot.currentIndex()
                else QSizePolicy.Ignored,
            )
        self.slot.adjustSize()

        repeating = mode == REPEAT
        self.repeat_extras.setVisible(repeating)
        self.filler.setVisible(not repeating)
        self.refresh_summary()

    # -- wordings (Repeat only) --------------------------------------------
    def add_wording(self, text: str = "") -> QTextEdit:
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(theme.PAD_S, theme.PAD_S, theme.PAD_S, theme.PAD_S)

        editor = QTextEdit()
        editor.setAcceptRichText(False)
        editor.setPlainText(text)
        editor.setMinimumHeight(WORDING_MIN_HEIGHT)
        editor.setPlaceholderText("Another way of saying the same thing…")
        editor.textChanged.connect(self.refresh_summary)
        layout.addWidget(editor, 1)

        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=row, e=editor: self._remove_wording(w, e))
        layout.addWidget(remove, 0, Qt.AlignTop)

        self.wording_box.insertWidget(self.wording_box.count() - 1, row)
        self._wordings.append(editor)
        self.refresh_summary()
        return editor

    def _remove_wording(self, row: QWidget, editor: QTextEdit) -> None:
        self._wordings.remove(editor)
        self.wording_box.removeWidget(row)
        row.deleteLater()
        self.refresh_summary()

    def alternates(self) -> list[str]:
        """Cleaned on read, like the Compose editor: these become post bodies."""
        cleaned = (strip_invisible(e.toPlainText()).strip() for e in self._wordings)
        return [text for text in cleaned if text]

    def wordings(self) -> list[str]:
        """Everything a repeating post would rotate through.

        The Compose text is always the first: this screen collects alternates,
        not the post itself, so there is never a question of which one is "the"
        wording.
        """
        body = self.base_body()
        return ([body] if body else []) + self.alternates()

    # -- times and days (Repeat only) --------------------------------------
    def add_time(self, text: str = "") -> bool:
        if not text:
            text = next_time(self.times())
        hour, minute = recurrence.parse_hhmm(text)

        # row(), not a bare QWidget: this sits inside the "When" card, and a
        # bare one painted a grey band behind every time and its Remove.
        line = row()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        entry = TimePicker(hour, minute)
        entry.timeChanged.connect(lambda _t: self.refresh_summary())
        layout.addWidget(entry)
        layout.addStretch(1)
        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=line, e=entry: self._remove_time(w, e))
        layout.addWidget(remove)

        self.time_box.addWidget(line)
        self._time_rows.append(entry)
        self.refresh_summary()
        return True

    def _remove_time(self, line: QWidget, entry: TimePicker) -> None:
        if len(self._time_rows) == 1:
            self.notify("A repeating post needs at least one time of day.", "warning")
            return
        self._time_rows.remove(entry)
        self.time_box.removeWidget(line)
        line.deleteLater()
        self.refresh_summary()

    def times(self) -> list[str]:
        return [e.time().toString("HH:mm") for e in self._time_rows]

    def commit_typed_times(self) -> None:
        """Take a half-typed date at its word before acting on it.

        Clicking a button used to take focus from the field, and losing focus
        is what makes Qt read what was typed. Buttons no longer take focus on a
        click (widgets.AppStyle), so the actions that use these values ask for
        it themselves. Deliberately not in refresh_summary(), which runs on
        every keystroke: interpreting mid-typing finishes the field for you
        after its first digit. The times are picked from lists, never typed.
        """
        self.schedule_entry.interpretText()

    def days(self) -> list[int]:
        return [i for i, box in enumerate(self._day_boxes) if box.isChecked()]

    def days_chosen(self) -> bool:
        """An empty list means *every* day to `recurrence`, and *no* day here.

        Untick all seven and the rule would silently become a daily one, which
        is the opposite of what the boxes say, so the view resolves the
        ambiguity before the rule is ever built.
        """
        return bool(self.days())

    def rule(self):
        """The repeat rule as currently set, or None if it cannot be built."""
        if not self.days_chosen():
            return None
        try:
            return recurrence.build(self.times(), self.days())
        except recurrence.InvalidRecurrence:
            return None

    # -- when, as a UTC instant --------------------------------------------
    def scheduled_for(self):
        if self.mode != ONCE:
            return None
        return clock.parse_local(
            self.schedule_entry.dateTime().toString("yyyy-MM-dd HH:mm")
        )

    # -- what is about to happen -------------------------------------------
    def refresh_summary(self) -> None:
        groups = self.selected_group_ids()
        count = f"{len(groups)} group{'s' if len(groups) != 1 else ''}"

        if self.mode == NOW:
            set_note_lines(
                self.summary, [f"{count}, starting as soon as nothing else is posting."]
            )
            return
        if self.mode == ONCE:
            when = self.schedule_entry.dateTime().toString("yyyy-MM-dd HH:mm")
            set_note_lines(self.summary, [f"{count}, once at {when}."])
            return

        wordings = self.wordings()
        self.wording_hint.setVisible(not self._wordings)
        # Said only when there is a rotation to speak of: with no alternates
        # the hint above says it, and "1 wording in rotation" is not one.
        extra = len(self.alternates())
        self.rotation_note.setVisible(extra > 0)
        self.rotation_note.setText(
            f"{len(wordings)} wordings in rotation: "
            + ("the Compose text and " if self.base_body() else "")
            + f"{extra} alternate{'s' if extra != 1 else ''}. Each run shares them "
            "out across the groups, a different one to each in turn."
        )

        rule = self.rule()
        if rule is None:
            set_note_lines(self.summary, [], ["Pick at least one day and one time."])
            return
        report = recurrence.preview(rule, utcnow(), ahead=1)
        lines = [f"{report.summary} · {count}"]
        if report.next_runs:
            lines.append(f"First run {clock.format_local(report.next_runs[0])}")
        follows = following(self.picked_set_names())
        if follows:
            lines.append(follows)
        set_note_lines(self.summary, lines)

    # -- doing it ----------------------------------------------------------
    def publish(self) -> bool:
        """The step's button. A post that went out or was queued starts a
        fresh one and lands on Home; a refused one keeps everything."""
        self.commit_typed_times()
        if self.mode == REPEAT:
            done = self.create_schedule()
        else:
            try:
                when = self.scheduled_for()
            except ValueError:
                self.notify("That is not a valid date and time.", "error")
                return False
            done = self.compose.add_to_queue(when)
            if done:
                self.refresh_recipients()
        if done:
            flow = self.app.views.get("create")
            if flow is not None:
                flow.start_over()
        return done

    def reset_form(self) -> None:
        """Back to how the step opens: Now, one time of day, every day, no
        alternates and no name. Part of starting a fresh post."""
        for editor in list(self._wordings):
            self._remove_wording(editor.parentWidget(), editor)
        self.name_entry.clear()
        for entry in list(self._time_rows):
            line = entry.parentWidget()
            self._time_rows.remove(entry)
            self.time_box.removeWidget(line)
            line.deleteLater()
        self.add_time(DEFAULT_TIMES[0])
        for box in self._day_boxes:
            box.setChecked(True)
        self.schedule_entry.reset()
        self.set_mode(NOW)

    def create_schedule(self) -> bool:
        wordings = self.wordings()
        # Pictures or a video alone may repeat too (the user's choice,
        # 2026-10-18); only a post with nothing in it at all is refused.
        if not wordings and not self.compose.attachments:
            self.notify("Write the post in step 1 first.", "error")
            return False

        groups = self.selected_group_ids()
        if not groups:
            self.notify("Pick at least one group in step 2.", "error")
            return False

        if not self.days_chosen():
            self.notify("Tick at least one day of the week.", "error")
            return False

        try:
            rule = recurrence.build(self.times(), self.days())
        except recurrence.InvalidRecurrence as exc:
            self.notify(str(exc), "error")
            return False

        if any(self.compose.has_rewrite(group_id) for group_id in groups):
            self.notify(
                "Per-group rewrites are not carried into a repeating post. It "
                "rotates the Compose text and the alternate wordings instead.",
                "warning",
            )

        first = recurrence.next_occurrence(rule, utcnow())
        # A picked set is stored as the set, not as its groups: the post
        # follows it from here on. The ticked groups no set brings are its own.
        schedule = self.app.schedule_repo.create(
            name=self.name_entry.text().strip(),
            bodies=wordings,
            group_ids=self.app.plain_group_ids(),
            set_ids=self.app.selected_set_ids(),
            times=list(rule.times),
            days=list(rule.days),
            media_paths=[str(p) for p in self.compose.attachments],
            next_run_at=first,
        )
        self.notify(
            f"{schedule.display_name}: {recurrence.describe(rule)}. First run "
            f"{clock.format_local(first)}.",
            "success",
        )

        self.name_entry.clear()
        self.refresh_schedules()
        return True

    # -- existing repeating posts ------------------------------------------
    def toggle_schedule(self, schedule_id: int) -> None:
        schedule = self.app.schedule_repo.get(schedule_id)
        if schedule is None:
            return
        if schedule.active:
            self.app.schedule_repo.set_state(schedule_id, SCHEDULE_PAUSED)
            self.notify(f"{schedule.display_name} paused.", "info")
        else:
            # Resuming recomputes the next slot rather than firing the one that
            # went by while it was paused.
            rule = recurrence.build(schedule.times, schedule.days)
            self.app.schedule_repo.set_next_run(
                schedule_id, recurrence.next_occurrence(rule, utcnow())
            )
            self.app.schedule_repo.set_state(schedule_id, SCHEDULE_ACTIVE)
            self.notify(f"{schedule.display_name} resumed.", "success")
        self.refresh_schedules()

    def edit_schedule(self, schedule_id: int) -> bool:
        """Open the repeating post on its own screen, where all of it can change."""
        editor = self.app.views["schedule_edit"]
        if not editor.load(schedule_id):
            self.notify("That repeating post no longer exists.", "error")
            self.refresh_schedules()
            return False
        self.app.show_view("schedule_edit")
        return True

    def delete_schedule(self, schedule_id: int) -> None:
        schedule = self.app.schedule_repo.get(schedule_id)
        self.app.schedule_repo.delete(schedule_id)
        self.refresh_schedules()
        if schedule is not None:
            # "Delete" sits beside "Edit" on every card. A slip takes the
            # wordings with it, so it is one click to take back.
            self.app.toast(
                f"{schedule.display_name} deleted.",
                "info",
                undo=lambda: self._restore_schedule(schedule),
            )

    def _restore_schedule(self, schedule) -> str | None:
        if not self.app.schedule_repo.restore(schedule):
            return None
        self.refresh_schedules()
        return f"{schedule.display_name} is back."

    def refresh_schedules(self) -> None:
        clear(self.schedule_box)
        schedules = self.app.schedule_repo.list()
        if not schedules:
            note = QLabel("Nothing repeating yet.")
            note.setObjectName("Muted")
            self.schedule_box.addWidget(note)
            self.schedule_box.addStretch(1)
            return

        for schedule in schedules:
            self.schedule_box.addWidget(self._card(schedule))
        self.schedule_box.addStretch(1)

    def _card(self, schedule) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)

        # The name and whether it is on, then what can be done to it. The
        # state used to sit among the buttons, in coloured words the same size
        # as "Delete" beside it.
        header = QHBoxLayout()
        header.setSpacing(theme.PAD_S)
        title = QLabel(schedule.display_name)
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)
        header.addWidget(
            badge(
                "Active" if schedule.active else "Paused",
                "SUCCESS" if schedule.active else "TEXT_MUTED",
            )
        )
        header.addStretch(1)

        toggle = QPushButton("Pause" if schedule.active else "Resume")
        toggle.clicked.connect(lambda _c, s=schedule.id: self.toggle_schedule(s))
        header.addWidget(toggle)
        edit = QPushButton("Edit")
        edit.clicked.connect(lambda _c, s=schedule.id: self.edit_schedule(s))
        header.addWidget(edit)
        remove = QPushButton("Delete")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, s=schedule.id: self.delete_schedule(s))
        header.addWidget(remove)
        layout.addLayout(header)

        try:
            summary = recurrence.describe(recurrence.build(schedule.times, schedule.days))
        except recurrence.InvalidRecurrence as exc:
            summary = f"Unusable rule: {exc}"
        # recipient_ids: the groups its next run goes to, sets included.
        groups = len(schedule.recipient_ids)
        sets = len(schedule.set_ids)
        detail = QLabel(
            f"{summary} · "
            + (f"{sets} group set{'s' if sets != 1 else ''} · " if sets else "")
            + f"{groups} group{'s' if groups != 1 else ''} · "
            f"{len(schedule.bodies)} wording{'s' if len(schedule.bodies) != 1 else ''}"
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        when = QLabel(
            f"Next {clock.format_local(schedule.next_run_at)}"
            if schedule.active and schedule.next_run_at
            else "Not scheduled"
        )
        when.setObjectName("Muted")
        layout.addWidget(when)

        if schedule.bodies:
            flat = " ".join(schedule.bodies[0].split())
            snippet = flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
            first = QLabel(snippet)
            first.setWordWrap(True)
            layout.addWidget(first)
        return box
