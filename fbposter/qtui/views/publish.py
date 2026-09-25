"""Publish screen: the one place that decides *when* a post goes out.

The last step of three: Compose says what, Groups says where, this says when.
It owns nothing but the moment, in three modes:

* **Post now** — straight onto the queue.
* **Schedule** — once, at a chosen moment.
* **Repeat** — daily, or on chosen days, at up to three times of day.

Repeat is the mode with a wrinkle worth reading. Sending the same words to the
same group twice is refused outright by `guards.check_repeat_text`, because
repetitive content -- not post count -- is what gets accounts restricted. So a
repeating post cannot live on one wording: the Compose text is the first, this
screen collects alternates, and each run rotates them so no group ever sees the
same text twice.

Nothing here talks to a browser. Post now and Schedule hand the work to
`ComposeView.add_to_queue`, and Repeat writes a definition the worker
materialises into an ordinary batch when its moment arrives -- so every guard,
the serial queue and the inter-group gap all apply unchanged.
"""

from __future__ import annotations

from PySide6.QtCore import QDate, QDateTime, QTime, Qt
from PySide6.QtGui import QColor, QTextCharFormat
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QButtonGroup,
    QCalendarWidget,
    QCheckBox,
    QDateTimeEdit,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock, recurrence
from fbposter.guards import rule_labels
from fbposter.text import strip_invisible
from fbposter.db.models import SCHEDULE_ACTIVE, SCHEDULE_PAUSED, utcnow

from .. import theme
from ..widgets import card, clear, row

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
# section takes a mouse wheel notch straight to 2028, which is what happened.
SCHEDULE_HORIZON_DAYS = 365

# How far one step moves a time, by the section the cursor is in. Stepping by
# seconds rather than by section is what lets the minutes carry into the hour:
# a stock QTimeEdit steps each section on its own, so with the cursor in the
# minutes "down" at 09:00 did nothing at all -- and every default time is on
# the hour, so the arrow looked broken more often than not.
STEP_SECONDS = {
    QDateTimeEdit.MinuteSection: 60,
    QDateTimeEdit.HourSection: 3600,
}


class ScheduleEntry(QDateTimeEdit):
    """The one-off date picker, with the two ways it went wrong closed off.

    A stock QDateTimeEdit opens with the *year* section selected and accepts
    the mouse wheel, so a single scroll over the widget silently moved a post a
    year out; two notches read as "the default is 2028". It also has no upper
    bound at all -- 9999 is reachable.
    """

    def __init__(self) -> None:
        super().__init__()
        # Styled as a drop-down rather than a stepper; see theme.py.
        self.setObjectName("DateEntry")
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd HH:mm")
        self._quiet_calendar()
        self.reset()

    def _quiet_calendar(self) -> None:
        """Take the platform's colours out of the calendar; theme.py adds ours.

        Qt paints Saturday and Sunday red, from a locale default that is not
        even Israel's weekend, and shades the day-name row grey. An empty
        format drops the red without overriding the dimming of days that are
        out of range.
        """
        calendar = self.calendarWidget()
        calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        calendar.setGridVisible(False)
        for day in (Qt.Saturday, Qt.Sunday):
            calendar.setWeekdayTextFormat(day, QTextCharFormat())
        header = QTextCharFormat()
        header.setForeground(QColor(theme.C["TEXT_MUTED"]))
        header.setBackground(QColor(theme.C["SURFACE"]))
        calendar.setHeaderTextFormat(header)

    def reset(self) -> None:
        """Default to the current time, and bound the range around it.

        Now rather than an hour out: an arbitrary offset is one more thing to
        notice and undo, and the common edit is "later today" typed straight
        over the minutes.

        Called every time the panel is shown rather than once at startup: the
        app is left open for days, and a default set at launch is a time in the
        past by the afternoon.
        """
        now = QDateTime.currentDateTime()
        now = now.addSecs(-now.time().second())  # whole minutes read better
        self.setMinimumDateTime(now)
        self.setMaximumDateTime(now.addDays(SCHEDULE_HORIZON_DAYS))
        self.setDateTime(now)
        # Land on the minutes, so the first arrow key or wheel notch moves the
        # post by a minute rather than by a year.
        self.setCurrentSection(QDateTimeEdit.MinuteSection)
        self._dim_past_days(now.date())

    def _dim_past_days(self, today: QDate) -> None:
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

    def stepBy(self, steps: int) -> None:  # noqa: N802 - Qt's name
        """Minutes and hours carry, as a clock does; see STEP_SECONDS.

        Clamped to the range, so the arrow keys can never step a post into the
        past or past the horizon. The date sections step as Qt steps them.
        """
        section = self.currentSection()
        seconds = STEP_SECONDS.get(section)
        if seconds is None:
            super().stepBy(steps)
            return
        target = self.dateTime().addSecs(steps * seconds)
        target = max(self.minimumDateTime(), min(target, self.maximumDateTime()))
        self.setDateTime(target)
        self.setSelectedSection(section)

    def stepEnabled(self):  # noqa: N802 - Qt's name
        if self.currentSection() not in STEP_SECONDS:
            return super().stepEnabled()
        enabled = QAbstractSpinBox.StepEnabledFlag.StepNone
        if self.dateTime() < self.maximumDateTime():
            enabled |= QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
        if self.dateTime() > self.minimumDateTime():
            enabled |= QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        return enabled

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Scrolling must never change when a post goes out.

        The wheel is for the page, not for the field under the pointer.
        """
        event.ignore()


class TimeEntry(QTimeEdit):
    """One time of day for a repeating post, with steppers that always answer.

    The stock field had three ways of ignoring a click, and the user met all of
    them: the text box lay over half of "up" (fixed in theme.py); "down" did
    nothing with the cursor in the minutes of a time on the hour; and "up" did
    nothing past 23. Now the minutes carry into the hour, the clock goes round
    in both directions, and the part being stepped is highlighted, so it is
    plain which one the arrows are moving -- the hours unless you click into
    the minutes.
    """

    def __init__(self, hour: int, minute: int) -> None:
        super().__init__(QTime(hour, minute))
        self.setDisplayFormat("HH:mm")
        # Holding a stepper speeds up, so 09:00 to 21:00 is one press, not
        # twelve clicks.
        self.setAccelerated(True)

    def stepBy(self, steps: int) -> None:  # noqa: N802 - Qt's name
        section = self.currentSection()
        if section not in STEP_SECONDS:
            section = QDateTimeEdit.HourSection
        # QTime arithmetic wraps at midnight, which is the point.
        self.setTime(self.time().addSecs(steps * STEP_SECONDS[section]))
        self.setSelectedSection(section)

    def stepEnabled(self):  # noqa: N802 - Qt's name
        return (
            QAbstractSpinBox.StepEnabledFlag.StepUpEnabled
            | QAbstractSpinBox.StepEnabledFlag.StepDownEnabled
        )

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Same rule as ScheduleEntry: scrolling the page must not move a post."""
        event.ignore()


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
        self._time_rows: list[TimeEntry] = []
        self._day_boxes: list[QCheckBox] = []
        # The rules the "Post anyway" panel is currently offering to break.
        self._offered: frozenset[str] = frozenset()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        heading = QLabel(self.title)
        heading.setObjectName("Title")
        outer.addWidget(heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        outer.addSpacing(theme.PAD_S)

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
        self.dismiss_override()
        self.refresh_recipients()
        self.refresh_schedules()
        self.refresh_summary()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    def selected_group_ids(self) -> list[int]:
        """Chosen on the Groups screen. This view only reads them."""
        return self.app.selected_group_ids()

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

        # The empty state carries the one thing about repeating posts that is
        # not obvious: one wording works exactly once, because sending the same
        # words to a group twice is refused.
        self.wording_hint = QLabel(
            "No alternates yet — the Compose text is the only wording, so this "
            "can run once per group and then has nothing fresh to send. Add one "
            "or two more."
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
        snippet = " ".join(body.split())[:SNIPPET_CHARS]
        self.preview_note.setText(
            f"“{snippet}…”" if snippet else "Nothing written yet — start on Compose."
        )

        # None never equals a list, so the first call always draws -- including
        # the empty case, which has its own "no groups picked" note.
        plan = self._recipient_plan(selected)
        if plan == self._recipients_shown:
            return
        self._recipients_shown = plan

        clear(self.recipient_box)
        if not selected:
            note = QLabel("No groups picked — choose them on the Groups screen.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.recipient_box.addWidget(note)
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
        # Scrolls rather than squashes. Repeat mode with a long summary, or
        # with the "Post anyway" panel open, is taller than the window, and a
        # plain column then crushed the "When" card until its name field,
        # times and days were drawn on top of one another.
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

        modes = QHBoxLayout()
        modes.setSpacing(theme.PAD_XS)
        self.mode_buttons: dict[str, QPushButton] = {}
        group = QButtonGroup(self)
        for index, (key, text) in enumerate(
            ((NOW, "Now"), (ONCE, "Once"), (REPEAT, "Repeat"))
        ):
            button = QPushButton(text)
            button.setObjectName("Tab")
            button.setCheckable(True)
            group.addButton(button, index)
            button.clicked.connect(lambda _c, k=key: self.set_mode(k))
            modes.addWidget(button, 1)
            self.mode_buttons[key] = button
        picker_layout.addLayout(modes)

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

        self._build_override(column)

        self.go_button = QPushButton(BUTTON_LABELS[NOW])
        self.go_button.setObjectName("Primary")
        # A lambda, because clicked() would otherwise pass its `checked` flag
        # in as publish()'s `allow`.
        self.go_button.clicked.connect(lambda: self.publish())
        column.addWidget(self.go_button)

        back = QPushButton("← Back to groups")
        back.clicked.connect(lambda: self.app.show_view("groups"))
        column.addWidget(back)

        # The slack goes below everything, so the column reads top-down and
        # grows downwards when Repeat opens.
        column.addStretch(1)

    def _build_override(self, column: QVBoxLayout) -> None:
        """What appears in place of the post button when the rules refuse.

        Not a dialog -- the app opens none for this kind of thing -- and it
        replaces the button rather than sitting beside it, so the choice in
        front of the user is exactly two: break these rules, or not.
        """
        self.override_card = card()
        layout = QVBoxLayout(self.override_card)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        layout.setSpacing(theme.PAD_S)
        title = QLabel("This breaks the posting rules")
        title.setObjectName("SectionHeading")
        title.setStyleSheet(f"color: {theme.C['WARNING']};")
        layout.addWidget(title)
        self.override_list = QLabel("")
        self.override_list.setWordWrap(True)
        layout.addWidget(self.override_list)
        why = QLabel(
            "You can post anyway. The rules are what keeps the account from "
            "looking automated, so break them only on purpose."
        )
        why.setObjectName("Muted")
        why.setWordWrap(True)
        layout.addWidget(why)
        buttons = QHBoxLayout()
        self.anyway_button = QPushButton("Post anyway")
        self.anyway_button.setObjectName("Danger")
        self.anyway_button.clicked.connect(lambda: self.post_anyway())
        buttons.addWidget(self.anyway_button, 1)
        self.keep_rules_button = QPushButton("Cancel")
        self.keep_rules_button.clicked.connect(lambda: self.dismiss_override())
        buttons.addWidget(self.keep_rules_button, 1)
        layout.addLayout(buttons)
        self.override_card.hide()
        column.addWidget(self.override_card)

    def offer_override(self, violations) -> None:
        """Show what the post would break, and offer to post anyway."""
        self._offered = frozenset(v.rule for v in violations)
        self.override_list.setText("\n".join(f"•  {v.message}" for v in violations))
        self.override_card.show()
        self.go_button.hide()
        # The summary says the same things; twice over, the column outgrew
        # the window.
        self.summary.hide()

    def dismiss_override(self) -> None:
        self._offered = frozenset()
        self.override_card.hide()
        self.go_button.show()
        self.summary.show()

    def post_anyway(self) -> bool:
        """Publish again, allowed to break exactly what was on the panel.

        Re-judged rather than trusted: if anything else would now be broken,
        the panel comes back listing it, and nothing is posted on the strength
        of a list the user did not see.
        """
        allowed = self._offered
        self.dismiss_override()
        return self.publish(allow=allowed)

    def _build_now_panel(self) -> QWidget:
        panel = row()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel(
            "Goes onto the queue straight away. If a batch is already running "
            "this one waits behind it — never alongside."
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
        # Four and three rather than seven across: at this column width seven
        # checkboxes clip their own labels down to "Mo", which is worse than
        # using a second row.
        days_grid = QGridLayout()
        days_grid.setSpacing(theme.PAD_XS)
        for index, name in enumerate(recurrence.DAY_NAMES):
            box = QCheckBox(name)
            box.setChecked(True)
            box.stateChanged.connect(lambda _s: self.refresh_summary())
            self._day_boxes.append(box)
            days_grid.addWidget(box, index // 4, index % 4)
        layout.addLayout(days_grid)
        return panel

    # -- modes -------------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        was = self.mode
        self.mode = mode
        # An offer made for one mode says nothing about another.
        self.dismiss_override()
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
        if len(self._time_rows) >= recurrence.MAX_TIMES_PER_DAY:
            self.notify(
                f"{recurrence.MAX_TIMES_PER_DAY} times a day is the ceiling — more "
                "than that stops looking like a person posting.",
                "warning",
            )
            return False

        if not text:
            text = DEFAULT_TIMES[min(len(self._time_rows), len(DEFAULT_TIMES) - 1)]
        hour, minute = recurrence.parse_hhmm(text)

        # row(), not a bare QWidget: this sits inside the "When" card, and a
        # bare one painted a grey band behind every time and its Remove.
        line = row()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        entry = TimeEntry(hour, minute)
        entry.timeChanged.connect(lambda _t: self.refresh_summary())
        layout.addWidget(entry, 1)
        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=line, e=entry: self._remove_time(w, e))
        layout.addWidget(remove)

        self.time_box.addWidget(line)
        self._time_rows.append(entry)
        self.refresh_summary()
        return True

    def _remove_time(self, line: QWidget, entry: TimeEntry) -> None:
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
        """Take a half-typed time at its word before acting on it.

        Clicking a button used to take focus from the field, and losing focus
        is what makes Qt read what was typed. Buttons no longer take focus on a
        click (widgets.AppStyle), so the actions that use these values ask for
        it themselves. Deliberately not in times(): refresh_summary() calls
        that on every keystroke, and interpreting mid-typing would finish the
        hour for you after its first digit.
        """
        for entry in self._time_rows:
            entry.interpretText()
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
        # Anything edited -- a time, a day, a wording, the date -- and the
        # offer on screen describes a post that no longer exists. It is
        # withdrawn, and the button comes back to be judged again.
        card = getattr(self, "override_card", None)
        if card is not None and not card.isHidden():
            self.dismiss_override()
        groups = self.selected_group_ids()
        count = f"{len(groups)} group{'s' if len(groups) != 1 else ''}"

        if self.mode == NOW:
            self.summary.setText(f"{count}, starting as soon as the worker is free.")
            return
        if self.mode == ONCE:
            when = self.schedule_entry.dateTime().toString("yyyy-MM-dd HH:mm")
            self.summary.setText(f"{count}, once at {when}.")
            return

        wordings = self.wordings()
        self.wording_hint.setVisible(not self._wordings)
        self.rotation_note.setText(
            f"{len(wordings)} wording{'s' if len(wordings) != 1 else ''} in rotation "
            "(the Compose text plus these). Each run picks a different one per "
            "group, so no group sees the same text twice."
        )

        rule = self.rule()
        if rule is None:
            self.summary.setText("Pick at least one day and one time.")
            return
        report = self._preview(rule, len(groups), len(wordings), ahead=1)
        lines = [f"{report.summary} · {count}"]
        if report.next_runs:
            lines.append(f"First run {clock.format_local(report.next_runs[0])}")
        # Said before the button is pressed, from the same checks it applies.
        lines.extend(
            v.message
            for v in self._schedule_violations(rule, groups, wordings, history=False)
        )
        note = recurrence.variation_note(len(groups), len(wordings))
        if note is not None:
            lines.append(note)
        self.summary.setText("\n".join(lines))

    def _schedule_violations(self, rule, group_ids, wordings, history: bool = True):
        """Every posting rule a schedule would break.

        `history=False` skips each group's last post and past wordings -- a
        query or two per group -- for the live summary, which runs on every
        keystroke. The button always judges with them.
        """
        targets = []
        for group_id in group_ids:
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            targets.append(
                recurrence.ScheduleTarget(
                    name=group.display_name,
                    last_posted_at=group.last_posted_at if history else None,
                    recent_bodies=(
                        tuple(self.app.group_repo.recent_bodies(group.id))
                        if history else ()
                    ),
                )
            )
        settings = self.app.settings_repo
        return recurrence.check_schedule(
            rule,
            utcnow(),
            targets=targets,
            wordings=wordings if history else (),
            cooldown_hours=settings.get_int("default_cooldown_hours", 8),
            window_start_hour=settings.get_int("posting_window_start_hour", 8),
            window_end_hour=settings.get_int("posting_window_end_hour", 23),
            daily_cap=settings.get_int("daily_cap", 25),
        )

    def _preview(self, rule, group_count: int, variant_count: int, ahead: int = 1):
        settings = self.app.settings_repo
        return recurrence.preview(
            rule,
            utcnow(),
            group_count=group_count,
            variant_count=variant_count,
            cooldown_hours=settings.get_int("default_cooldown_hours", 8),
            window_start_hour=settings.get_int("posting_window_start_hour", 8),
            window_end_hour=settings.get_int("posting_window_end_hour", 23),
            ahead=ahead,
        )

    # -- doing it ----------------------------------------------------------
    def publish(self, allow: frozenset[str] = frozenset()) -> bool:
        """`allow` is set only by post_anyway(): the rules the user accepted."""
        self.commit_typed_times()
        if self.mode == REPEAT:
            return self.create_schedule(allow)
        try:
            when = self.scheduled_for()
        except ValueError:
            self.notify("That is not a valid date and time.", "error")
            return False
        queued = self.compose.add_to_queue(
            when, allow=allow, on_blocked=self.offer_override
        )
        if queued:
            self.refresh_recipients()
        return queued

    def create_schedule(self, allow: frozenset[str] = frozenset()) -> bool:
        wordings = self.wordings()
        if not wordings:
            self.notify("Write the post on Compose first.", "error")
            return False

        groups = self.selected_group_ids()
        if not groups:
            self.notify("Pick at least one group on the Groups screen.", "error")
            return False

        if not self.days_chosen():
            self.notify("Tick at least one day of the week.", "error")
            return False

        try:
            rule = recurrence.build(self.times(), self.days())
        except recurrence.InvalidRecurrence as exc:
            self.notify(str(exc), "error")
            return False

        # The same rules the worker applies each time this fires, judged now:
        # before, a schedule that broke them was created anyway and quietly
        # skipped or deferred run after run.
        violations = self._schedule_violations(rule, groups, wordings)
        if any(v.rule not in allow for v in violations):
            self.offer_override(violations)
            return False
        overrides = frozenset(v.rule for v in violations)

        if any(self.compose.has_rewrite(group_id) for group_id in groups):
            self.notify(
                "Per-group rewrites are not carried into a repeating post — it "
                "rotates the wordings below instead.",
                "warning",
            )

        first = recurrence.next_occurrence(rule, utcnow())
        schedule = self.app.schedule_repo.create(
            name=self.name_entry.text().strip(),
            bodies=wordings,
            group_ids=groups,
            times=list(rule.times),
            days=list(rule.days),
            media_paths=[str(p) for p in self.compose.attachments],
            next_run_at=first,
            overrides=overrides,
        )

        note = recurrence.variation_note(len(groups), len(wordings))
        if note is not None:
            self.notify(note, "warning")
        elif overrides:
            self.notify(
                f"{schedule.display_name}: {recurrence.describe(rule)}, posting "
                f"anyway despite the {rule_labels(overrides)}. First run "
                f"{clock.format_local(first)}.",
                "warning",
            )
        else:
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

    def delete_schedule(self, schedule_id: int) -> None:
        schedule = self.app.schedule_repo.get(schedule_id)
        self.app.schedule_repo.delete(schedule_id)
        if schedule is not None:
            self.notify(f"{schedule.display_name} deleted.", "info")
        self.refresh_schedules()

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

        header = QHBoxLayout()
        title = QLabel(schedule.display_name)
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title, 1)

        state = QLabel("Active" if schedule.active else "Paused")
        state.setStyleSheet(
            f"color: {theme.C['SUCCESS' if schedule.active else 'TEXT_MUTED']};"
        )
        header.addWidget(state)

        toggle = QPushButton("Pause" if schedule.active else "Resume")
        toggle.clicked.connect(lambda _c, s=schedule.id: self.toggle_schedule(s))
        header.addWidget(toggle)
        remove = QPushButton("Delete")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, s=schedule.id: self.delete_schedule(s))
        header.addWidget(remove)
        layout.addLayout(header)

        try:
            summary = recurrence.describe(recurrence.build(schedule.times, schedule.days))
        except recurrence.InvalidRecurrence as exc:
            summary = f"Unusable rule: {exc}"
        detail = QLabel(
            f"{summary} · {len(schedule.group_ids)} group"
            f"{'s' if len(schedule.group_ids) != 1 else ''} · "
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

        if schedule.overrides:
            anyway = QLabel(
                f"Post anyway: allowed to break the {rule_labels(schedule.overrides)}."
            )
            anyway.setStyleSheet(f"color: {theme.C['WARNING']};")
            anyway.setWordWrap(True)
            layout.addWidget(anyway)

        if schedule.bodies:
            snippet = " ".join(schedule.bodies[0].split())[:SNIPPET_CHARS]
            first = QLabel(snippet)
            first.setWordWrap(True)
            layout.addWidget(first)
        return box
