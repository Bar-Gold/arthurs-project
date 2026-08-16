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

from PySide6.QtCore import QDateTime, QTime, Qt
from PySide6.QtWidgets import (
    QButtonGroup,
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
from fbposter.db.models import SCHEDULE_ACTIVE, SCHEDULE_PAUSED, utcnow

from .. import theme
from ..widgets import card, row

RIGHT_COLUMN_WIDTH = 340
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


class ScheduleEntry(QDateTimeEdit):
    """The one-off date picker, with the two ways it went wrong closed off.

    A stock QDateTimeEdit opens with the *year* section selected and accepts
    the mouse wheel, so a single scroll over the widget silently moved a post a
    year out; two notches read as "the default is 2028". It also has no upper
    bound at all -- 9999 is reachable.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setCalendarPopup(True)
        self.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.reset()

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

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Scrolling must never change when a post goes out.

        The wheel is for the page, not for the field under the pointer.
        """
        event.ignore()


class PublishView(QWidget):
    title = "Publish"
    subtitle = "The last step: now, at a set time, or on repeat."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.mode = NOW
        self._wordings: list[QTextEdit] = []
        self._time_rows: list[QTimeEdit] = []
        self._day_boxes: list[QCheckBox] = []

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

    def refresh_recipients(self) -> None:
        while self.recipient_box.count():
            item = self.recipient_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        selected = self.selected_group_ids()
        body = self.base_body()
        if not selected:
            note = QLabel("No groups picked — choose them on the Groups screen.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.recipient_box.addWidget(note)
        for group_id in selected:
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            holder = row()
            line = QHBoxLayout(holder)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(QLabel(group.display_name), 1)
            if self.compose.has_rewrite(group_id):
                tag = QLabel("reworded")
                tag.setObjectName("Muted")
                line.addWidget(tag)
            self.recipient_box.addWidget(holder)

        snippet = " ".join(body.split())[:SNIPPET_CHARS]
        self.preview_note.setText(
            f"“{snippet}…”" if snippet else "Nothing written yet — start on Compose."
        )

    # -- right column: when ------------------------------------------------
    def _build_right(self, parent: QHBoxLayout) -> None:
        column = QVBoxLayout()
        column.setSpacing(theme.PAD_S)
        holder = QWidget()
        holder.setFixedWidth(RIGHT_COLUMN_WIDTH)
        holder.setLayout(column)
        parent.addWidget(holder)

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

        column.addStretch(1)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

        self.go_button = QPushButton(BUTTON_LABELS[NOW])
        self.go_button.setObjectName("Primary")
        self.go_button.clicked.connect(self.publish)
        column.addWidget(self.go_button)

        back = QPushButton("← Back to groups")
        back.clicked.connect(lambda: self.app.show_view("groups"))
        column.addWidget(back)

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
        return [e.toPlainText().strip() for e in self._wordings if e.toPlainText().strip()]

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

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        entry = QTimeEdit()
        entry.setDisplayFormat("HH:mm")
        entry.setTime(QTime(hour, minute))
        entry.timeChanged.connect(lambda _t: self.refresh_summary())
        layout.addWidget(entry, 1)
        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=row, e=entry: self._remove_time(w, e))
        layout.addWidget(remove)

        self.time_box.addWidget(row)
        self._time_rows.append(entry)
        self.refresh_summary()
        return True

    def _remove_time(self, row: QWidget, entry: QTimeEdit) -> None:
        if len(self._time_rows) == 1:
            self.notify("A repeating post needs at least one time of day.", "warning")
            return
        self._time_rows.remove(entry)
        self.time_box.removeWidget(row)
        row.deleteLater()
        self.refresh_summary()

    def times(self) -> list[str]:
        return [e.time().toString("HH:mm") for e in self._time_rows]

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
            self.summary.setText(f"{count}, starting as soon as the worker is free.")
            return
        if self.mode == ONCE:
            when = self.schedule_entry.dateTime().toString("yyyy-MM-dd HH:mm")
            self.summary.setText(f"{count}, once at {when}.")
            return

        wordings = self.wordings()
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
        lines.extend(report.warnings)
        self.summary.setText("\n".join(lines))

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
    def publish(self) -> bool:
        if self.mode == REPEAT:
            return self.create_schedule()
        try:
            when = self.scheduled_for()
        except ValueError:
            self.notify("That is not a valid date and time.", "error")
            return False
        queued = self.compose.add_to_queue(when)
        if queued:
            self.refresh_recipients()
        return queued

    def create_schedule(self) -> bool:
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
        )

        report = self._preview(rule, len(groups), len(wordings))
        for warning in report.warnings:
            self.notify(warning, "warning")
        if not report.warnings:
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
        while self.schedule_box.count():
            item = self.schedule_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

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

        if schedule.bodies:
            snippet = " ".join(schedule.bodies[0].split())[:SNIPPET_CHARS]
            first = QLabel(snippet)
            first.setWordWrap(True)
            layout.addWidget(first)
        return box
