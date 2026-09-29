"""Settings screen: whether the app is posting, the rules it posts by, which
Facebook account it posts as, and what this PC does to keep it posting.

Not a step in the flow, so it sits apart from Compose, Groups and Publish in
the sidebar. Everything here is something you set once and come back to
rarely -- which is exactly why it is kept off the screens you use every day.
Pausing used to be a button in the sidebar on every screen, and changing
account a button beside "Check connection"; both stop or undo something, and
deserve a screen you go to on purpose.
"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fbposter import onboarding
from fbposter.db.schema import DEFAULT_SETTINGS
from fbposter.connection import ConnectionState

from .. import theme
from ..widgets import card, row

START_KEY = "posting_window_start_hour"
END_KEY = "posting_window_end_hour"
CAP_KEY = "daily_cap"
COOLDOWN_KEY = "default_cooldown_hours"

DEFAULTS = {key: int(DEFAULT_SETTINGS[key]) for key in (START_KEY, END_KEY, CAP_KEY, COOLDOWN_KEY)}
DEFAULT_COOLDOWN = DEFAULTS[COOLDOWN_KEY]

# At least an hour: nought would switch the rule off for every group, for
# good. Breaking it for one post is what "Post anyway" is for.
COOLDOWN_MIN = 1
COOLDOWN_MAX = 168
# At least one: nought is read as "no limit at all".
CAP_MIN = 1
CAP_MAX = 100
# Wide enough for "100 posts" and the steppers; the stock width stretched
# each field across half the card.
FIELD_WIDTH = 170
HOUR_WIDTH = FIELD_WIDTH
# Hours that count as the middle of the night, for the warning under the
# posting hours. Posting at 4am is the loudest automation signal there is.
NIGHT_HOURS = range(0, 6)

# What the scheduler row says for each worker state: headline, colour, detail.
STATUS = {
    "off": (
        "Not running",
        "TEXT_MUTED",
        "The scheduler starts a moment after the app opens.",
    ),
    "stopped": (
        "Not running",
        "TEXT_MUTED",
        "The scheduler starts a moment after the app opens.",
    ),
    "idle": (
        "Running",
        "SUCCESS",
        "Posts go out when they are due, one group at a time.",
    ),
    "posting": (
        "Posting now",
        "ACCENT_TEXT",
        "A post is going out. Pausing lets it finish first.",
    ),
    "paused": (
        "Paused",
        "WARNING",
        "Nothing is posted until you resume, and it stays paused if the app "
        "or the computer restarts. On resume, anything more than 2 hours "
        "overdue is marked missed rather than posted late.",
    ),
}

AUTOSTART_LABEL = "Start the app when I sign in to Windows"
AUTOSTART_HELP = (
    "Opens the app 45 seconds after you sign in, so posting carries on after "
    "Windows restarts for an update. Nothing starts until somebody has signed "
    "in to Windows."
)
AWAKE_LABEL = "Keep this PC awake"
AWAKE_HELP = (
    "Nothing posts while the PC is asleep. Plugged in, it stays awake with the "
    "lid open or closed. On battery, it stays awake while the lid is open and "
    "sleeps when you close it. With the lid closed, keep it somewhere with air "
    "around it, not in a bag."
)


def hours_summary(start: int, end: int) -> str:
    """The posting hours as a sentence, including the one that crosses midnight."""
    if start == end:
        return "The start and end can't be the same hour."
    if start < end:
        return f"Posts only start between {start:02d}:00 and {end:02d}:00, Israel time."
    return (
        f"Posts only start between {start:02d}:00 and {end:02d}:00 the next "
        "morning, Israel time."
    )


def covers_night(start: int, end: int) -> bool:
    """Whether the posting hours reach into the middle of the night."""
    if start == end:
        return False
    if start < end:
        hours = range(start, end)
    else:
        hours = [*range(start, 24), *range(0, end)]
    return any(hour in NIGHT_HOURS for hour in hours)


class _NoWheel:
    """Scrolling the page must not change a setting it passes over.

    Every field that decides when or how often a post goes out follows this
    rule. The wheel event is ignored, so it reaches the scroll area instead.
    """

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt's name
        event.ignore()


class CooldownEntry(_NoWheel, QSpinBox):
    """Hours between posts to one group."""

    def __init__(self) -> None:
        super().__init__()
        self.setRange(COOLDOWN_MIN, COOLDOWN_MAX)
        self.setSuffix(" h")
        self.setAccelerated(True)
        self.setAccessibleName("Cooldown between posts to the same group, in hours")


class CapEntry(_NoWheel, QSpinBox):
    """Posts a day, across every group."""

    def __init__(self) -> None:
        super().__init__()
        self.setRange(CAP_MIN, CAP_MAX)
        self.setSuffix(" posts")
        self.setAccelerated(True)
        self.setAccessibleName("Daily limit, in posts")


class HourEntry(_NoWheel, QSpinBox):
    """An hour of the day, shown as 08:00. Goes round midnight.

    Hours only: the posting window is stored in whole hours, and a minutes
    field that did nothing would be a lie.
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setRange(0, 23)
        self.setWrapping(True)
        self.setSuffix(":00")
        self.setAccessibleName(name)

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt's name
        return f"{value:02d}"


class SettingsView(QWidget):
    title = "Settings"
    subtitle = "Whether the app is posting, the rules it posts by, and the account it posts as."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # What the scheduler card shows, so the 700ms worker pump does not
        # re-set identical text on every turn.
        self._status_shown = None
        # True while "Switch account" is waiting for its yes or no.
        self._confirming = False
        # True while the PC settings are being read or changed.
        self._pc_busy = False

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

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_S)
        column.addWidget(self._build_scheduler())
        column.addWidget(self._build_rules())
        column.addWidget(self._build_account())
        column.addWidget(self._build_pc())
        column.addStretch(1)
        self.area.setWidget(holder)
        outer.addWidget(self.area, 1)

    # -- layout ------------------------------------------------------------
    @staticmethod
    def _card(heading: str) -> tuple[QWidget, QVBoxLayout]:
        box = card()
        inner = QVBoxLayout(box)
        inner.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        inner.setSpacing(theme.PAD_XS)
        label = QLabel(heading)
        label.setObjectName("SectionHeading")
        inner.addWidget(label)
        return box, inner

    @staticmethod
    def _muted(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        return label

    def _build_scheduler(self) -> QWidget:
        box, inner = self._card("Scheduler")

        self.status_label = QLabel("")
        self.status_label.setAccessibleName("Scheduler status")
        inner.addWidget(self.status_label)
        self.status_detail = self._muted()
        inner.addWidget(self.status_detail)
        self.waiting_label = self._muted()
        inner.addWidget(self.waiting_label)

        inner.addSpacing(theme.PAD_XS)
        line = QHBoxLayout()
        self.pause_button = QPushButton("Pause posting")
        self.pause_button.clicked.connect(self.toggle)
        line.addWidget(self.pause_button)
        line.addStretch(1)
        inner.addLayout(line)
        return box

    def _build_rules(self) -> QWidget:
        box, inner = self._card("Posting rules")
        inner.addWidget(self._muted(
            "Every batch and every repeating post follows these, including "
            "ones already queued. \"Post anyway\" on the Publish screen can "
            "break them for one post."
        ))
        inner.addSpacing(theme.PAD_XS)

        grid_holder = row()
        grid = QGridLayout(grid_holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(theme.PAD_M)
        grid.setVerticalSpacing(theme.PAD_S)

        # Posting hours.
        grid.addWidget(QLabel("Posting hours"), 0, 0)
        hours = QHBoxLayout()
        hours.setSpacing(theme.PAD_S)
        self.start_entry = HourEntry("Posting hours start")
        self.end_entry = HourEntry("Posting hours end")
        for entry in (self.start_entry, self.end_entry):
            entry.setFixedWidth(HOUR_WIDTH)
        hours.addWidget(QLabel("From"))
        hours.addWidget(self.start_entry)
        hours.addWidget(QLabel("to"))
        hours.addWidget(self.end_entry)
        hours.addStretch(1)
        grid.addLayout(hours, 0, 1)
        self.hours_note = self._muted()
        grid.addWidget(self.hours_note, 1, 1)
        self.night_note = QLabel(
            "These hours include the middle of the night. Posts at night are "
            "the clearest sign of automation there is."
        )
        self.night_note.setWordWrap(True)
        self.night_note.setStyleSheet(f"color: {theme.C['WARNING']};")
        grid.addWidget(self.night_note, 2, 1)

        # Daily limit.
        grid.addWidget(QLabel("Daily limit"), 3, 0)
        self.cap_entry = CapEntry()
        self.cap_entry.setFixedWidth(FIELD_WIDTH)
        cap_line = QHBoxLayout()
        cap_line.addWidget(self.cap_entry)
        cap_line.addStretch(1)
        grid.addLayout(cap_line, 3, 1)
        grid.addWidget(self._muted(
            "Across all groups together, per day in Israel time."
        ), 4, 1)

        # Cooldown.
        grid.addWidget(QLabel("Cooldown per group"), 5, 0)
        self.cooldown_entry = CooldownEntry()
        self.cooldown_entry.setFixedWidth(FIELD_WIDTH)
        cooldown_line = QHBoxLayout()
        cooldown_line.addWidget(self.cooldown_entry)
        cooldown_line.addStretch(1)
        grid.addLayout(cooldown_line, 5, 1)
        grid.addWidget(self._muted(
            "After posting to a group, the app waits at least this long before "
            "posting to that group again."
        ), 6, 1)
        grid.setColumnStretch(1, 1)
        inner.addWidget(grid_holder)

        for entry in (self.start_entry, self.end_entry, self.cap_entry, self.cooldown_entry):
            entry.valueChanged.connect(self._rules_edited)

        inner.addSpacing(theme.PAD_XS)
        line = QHBoxLayout()
        self.save_button = QPushButton("Save")
        self.save_button.clicked.connect(self.save_rules)
        line.addWidget(self.save_button)
        self.defaults_button = QPushButton(
            f"Use the defaults ({DEFAULTS[START_KEY]:02d}:00–{DEFAULTS[END_KEY]:02d}:00, "
            f"{DEFAULTS[CAP_KEY]} a day, {DEFAULT_COOLDOWN} h)"
        )
        self.defaults_button.clicked.connect(self.use_defaults)
        line.addWidget(self.defaults_button)
        line.addStretch(1)
        inner.addLayout(line)
        return box

    def _build_account(self) -> QWidget:
        box, inner = self._card("Facebook account")
        self.account_heading = QLabel("")
        self.account_heading.setStyleSheet("font-weight: 600;")
        self.account_heading.setWordWrap(True)
        inner.addWidget(self.account_heading)
        self.account_detail = self._muted()
        inner.addWidget(self.account_detail)

        inner.addSpacing(theme.PAD_XS)
        line = QHBoxLayout()
        self.switch_button = QPushButton(onboarding.SWITCH_ACTION)
        self.switch_button.clicked.connect(self.begin_switch)
        line.addWidget(self.switch_button)
        self.confirm_button = QPushButton(onboarding.SWITCH_CONFIRM)
        self.confirm_button.clicked.connect(self.confirm_switch)
        line.addWidget(self.confirm_button)
        self.cancel_button = QPushButton(onboarding.SWITCH_CANCEL)
        self.cancel_button.clicked.connect(self.cancel_switch)
        line.addWidget(self.cancel_button)
        line.addStretch(1)
        inner.addLayout(line)
        self.account_note = self._muted()
        inner.addWidget(self.account_note)
        return box

    def _build_pc(self) -> QWidget:
        box, inner = self._card("This PC")
        self.autostart_box = QCheckBox(AUTOSTART_LABEL)
        self.autostart_box.clicked.connect(self.set_autostart)
        inner.addWidget(self.autostart_box)
        inner.addWidget(self._muted(AUTOSTART_HELP))
        inner.addSpacing(theme.PAD_XS)
        self.awake_box = QCheckBox(AWAKE_LABEL)
        self.awake_box.clicked.connect(self.set_keep_awake)
        inner.addWidget(self.awake_box)
        inner.addWidget(self._muted(AWAKE_HELP))
        self.pc_note = self._muted()
        inner.addWidget(self.pc_note)
        return box

    # -- showing -----------------------------------------------------------
    def on_show(self) -> None:
        self.load_rules()
        self.refresh_status()
        self.refresh_account()
        self.load_pc()

    def refresh_status(self) -> None:
        """Say what the scheduler is doing. Cheap: runs on every worker tick."""
        worker = self.app.worker
        state = "off" if worker is None else worker.state
        headline, colour, detail = STATUS.get(state, STATUS["idle"])
        paused = worker is not None and worker.paused
        waiting = None
        if getattr(self.app, "current_view", None) == "settings":
            # Two queries; only worth it while someone is looking.
            waiting = self.app.unfinished_work()
        snapshot = (state, paused, waiting)
        if snapshot == self._status_shown:
            return
        self._status_shown = snapshot

        self.status_label.setText(headline)
        self.status_label.setStyleSheet(f"color: {theme.C[colour]}; font-weight: 600;")
        self.status_detail.setText(detail)
        self.waiting_label.setText(
            f"Waiting: {waiting}." if waiting else "Nothing is waiting to go out."
        )
        self.pause_button.setEnabled(worker is not None)
        self.pause_button.setText("Resume posting" if paused else "Pause posting")

    # -- the scheduler -----------------------------------------------------
    def toggle(self) -> None:
        self.app.toggle_worker()

    # -- the rules ---------------------------------------------------------
    def stored_rules(self) -> dict[str, int]:
        get = self.app.settings_repo.get_int
        return {key: get(key, default) for key, default in DEFAULTS.items()}

    def shown_rules(self) -> dict[str, int]:
        return {
            START_KEY: self.start_entry.value(),
            END_KEY: self.end_entry.value(),
            CAP_KEY: self.cap_entry.value(),
            COOLDOWN_KEY: self.cooldown_entry.value(),
        }

    def load_rules(self) -> None:
        stored = self.stored_rules()
        self.start_entry.setValue(stored[START_KEY])
        self.end_entry.setValue(stored[END_KEY])
        self.cap_entry.setValue(stored[CAP_KEY])
        self.cooldown_entry.setValue(stored[COOLDOWN_KEY])
        self._rules_edited()

    def stored_cooldown(self) -> int:
        return self.stored_rules()[COOLDOWN_KEY]

    def _rules_edited(self, _value=None) -> None:
        shown = self.shown_rules()
        start, end = shown[START_KEY], shown[END_KEY]
        valid = start != end
        self.hours_note.setText(hours_summary(start, end))
        self.hours_note.setStyleSheet(
            "" if valid else f"color: {theme.C['DANGER']};"
        )
        self.night_note.setVisible(covers_night(start, end))
        # Enabled only when there is something valid to save, so the button
        # itself says whether what is on screen is what is in force.
        self.save_button.setEnabled(valid and shown != self.stored_rules())
        self.defaults_button.setVisible(shown != DEFAULTS)

    def use_defaults(self) -> None:
        for entry, key in (
            (self.start_entry, START_KEY), (self.end_entry, END_KEY),
            (self.cap_entry, CAP_KEY), (self.cooldown_entry, COOLDOWN_KEY),
        ):
            entry.setValue(DEFAULTS[key])
        self.save_rules()

    def save_rules(self) -> None:
        # A click does not take focus from a field (widgets.AppStyle), so a
        # half-typed number is read here rather than on focus loss.
        for entry in (self.start_entry, self.end_entry, self.cap_entry, self.cooldown_entry):
            entry.interpretText()
        shown = self.shown_rules()
        if shown == self.stored_rules():
            self._rules_edited()
            return
        if shown[START_KEY] == shown[END_KEY]:
            self._rules_edited()
            self.app.toast(hours_summary(shown[START_KEY], shown[END_KEY]), "error")
            return
        self.app.settings_repo.set_posting_rules(
            start_hour=shown[START_KEY],
            end_hour=shown[END_KEY],
            daily_cap=shown[CAP_KEY],
            cooldown_hours=shown[COOLDOWN_KEY],
        )
        self._rules_edited()
        self.app.toast("Posting rules saved.", "success")

    # -- the Facebook account ----------------------------------------------
    def _connected(self) -> bool:
        return self.app.connection_state is ConnectionState.CONNECTED

    def refresh_account(self) -> None:
        """Offer the switch only while there is a session to end."""
        if not self._connected():
            # The question stops making sense the moment the answer changes:
            # there is nothing left to sign out of.
            self._confirming = False
        confirming = self._confirming
        if confirming:
            self.account_heading.setText(onboarding.SWITCH_HEADLINE)
            self.account_detail.setText(onboarding.SWITCH_WARNING)
        else:
            self.account_heading.setText("")
            self.account_detail.setText(onboarding.SWITCH_DETAIL)
        self.account_heading.setVisible(confirming)
        self.switch_button.setVisible(not confirming)
        self.switch_button.setEnabled(self._connected())
        self.confirm_button.setVisible(confirming)
        self.cancel_button.setVisible(confirming)
        self.account_note.setText(
            "" if self._connected() else "Available once the app is connected to Facebook."
        )
        self.account_note.setVisible(not self._connected())

    def _posting_now(self) -> bool:
        worker = self.app.worker
        return worker is not None and worker.state == "posting"

    def begin_switch(self) -> None:
        """Ask before signing anybody out. The question replaces the card
        rather than opening a dialog over it: no modal dialogs for this."""
        if self._posting_now():
            # Dropping the cookies with a post half-typed into the composer
            # fails that post, and the batch then halts on a verification that
            # never could have succeeded. It is a wait, not a refusal.
            self.app.toast(onboarding.SWITCH_BUSY, "warning")
            return
        if not self._connected():
            return
        self._confirming = True
        self.refresh_account()

    def cancel_switch(self) -> None:
        self._confirming = False
        self.refresh_account()

    def confirm_switch(self) -> None:
        """Hand over to the setup screen, which signs out and runs the login."""
        self._confirming = False
        self.refresh_account()
        if self._posting_now():
            # Checked again: a post may have started while they were reading.
            self.app.toast(onboarding.SWITCH_BUSY, "warning")
            return
        if self.app.views["welcome"].start_switch():
            self.app.show_view("welcome")

    # -- this PC -----------------------------------------------------------
    def load_pc(self) -> None:
        """Read where things stand, off the drawing thread: it asks Windows."""
        if self._pc_busy:
            return
        self._set_pc_busy(True, "")
        self.app.run_in_background(
            self.app.always_on.status, self._on_pc_status, self._on_pc_failed
        )

    def set_autostart(self, on: bool) -> None:
        self._change_pc(lambda: self.app.always_on.set_autostart(on),
                        "The app starts when you sign in." if on
                        else "The app no longer starts when you sign in.")

    def set_keep_awake(self, on: bool) -> None:
        self._change_pc(lambda: self.app.always_on.set_keep_awake(on),
                        "This PC stays awake now." if on
                        else "This PC sleeps normally again.")

    def _change_pc(self, change, done_message: str) -> None:
        if self._pc_busy:
            return
        self._set_pc_busy(True, "Changing Windows settings…")

        def work():
            change()
            return self.app.always_on.status()

        def done(status):
            self._on_pc_status(status)
            self.app.toast(done_message, "success")

        self.app.run_in_background(work, done, self._on_pc_failed)

    def _on_pc_status(self, status) -> None:
        self._set_pc_busy(False, "")
        self.autostart_box.setChecked(bool(status.autostart))
        self.awake_box.setChecked(status.keep_awake)
        if status.autostart is None:
            self.pc_note.setText("Windows did not say whether the app starts when you sign in.")

    def _on_pc_failed(self, exc: Exception) -> None:
        self._set_pc_busy(False, "")
        self.app.toast(f"That did not work: {exc}", "error")
        # The boxes show what the user clicked, not what is true. Read it again.
        self.load_pc()

    def _set_pc_busy(self, busy: bool, note: str) -> None:
        self._pc_busy = busy
        self.autostart_box.setEnabled(not busy)
        self.awake_box.setEnabled(not busy)
        self.pc_note.setText(note)
