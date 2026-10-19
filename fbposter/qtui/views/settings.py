"""Settings screen: whether the app is posting, which Facebook account it
posts as, and what this PC does to keep it posting.

There is no posting-rules card any more: the app has no rules to set (the
user's choice, 2026-10-18). Advice is on the Posting tips screen.

Not a step in the flow, so it sits apart from Compose, Groups and Publish in
the sidebar. Everything here is something you set once and come back to
rarely -- which is exactly why it is kept off the screens you use every day.
Pausing used to be a button in the sidebar on every screen, and changing
account a button beside "Check connection"; both stop or undo something, and
deserve a screen you go to on purpose.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fbposter import onboarding
from fbposter.connection import ConnectionState

from .. import theme
from ..widgets import Switch, badge, card, row, set_badge

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


class SettingsView(QWidget):
    title = "Settings"
    subtitle = "Whether the app is posting, the account it posts as, and this PC."

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
        outer.addSpacing(theme.HEADER_GAP)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_M)
        column.addWidget(self._build_scheduler())
        column.addWidget(self._build_account())
        column.addWidget(self._build_pc())
        column.addStretch(1)
        self.area.setWidget(holder)
        outer.addWidget(self.area, 1)

    # -- layout ------------------------------------------------------------
    # Laid out the way system settings are: each setting is one row, its name
    # and what it means on the left, its control on the right, with a hairline
    # between rows. The controls used to sit in a column of their own in the
    # middle of the card, with each hint under the control rather than under
    # the name it explained, so the card read as a form to fill in.
    @staticmethod
    def _card(
        heading: str, aside: QWidget | None = None, action: QWidget | None = None
    ) -> tuple[QWidget, QVBoxLayout]:
        """A card with its heading: `aside` (a status badge) beside it, and
        `action` (a button) at the far end of the same line."""
        box = card()
        inner = QVBoxLayout(box)
        inner.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        inner.setSpacing(theme.PAD_XS)
        label = QLabel(heading)
        label.setObjectName("CardTitle")
        top = QHBoxLayout()
        top.setSpacing(theme.PAD_S)
        top.addWidget(label)
        if aside is not None:
            top.addWidget(aside)
        top.addStretch(1)
        if action is not None:
            top.addWidget(action)
        inner.addLayout(top)
        return box, inner

    @staticmethod
    def _muted(text: str = "") -> QLabel:
        label = QLabel(text)
        label.setObjectName("Muted")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _divider(inner: QVBoxLayout) -> None:
        inner.addSpacing(theme.PAD_S)
        line = QFrame()
        line.setObjectName("Divider")
        line.setFixedHeight(1)
        inner.addWidget(line)
        inner.addSpacing(theme.PAD_S)

    @staticmethod
    def _setting(name: str, control, *hints: QLabel) -> QWidget:
        """One setting: its name with the hints under it, the control at the
        right, centred on the whole row."""
        holder = row()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.PAD_XL)
        words = QVBoxLayout()
        words.setSpacing(2)
        words.addWidget(QLabel(name))
        for hint in hints:
            words.addWidget(hint)
        line.addLayout(words, 1)
        if isinstance(control, QWidget):
            line.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        else:
            line.addLayout(control)
        return holder

    def _build_scheduler(self) -> QWidget:
        # Whether it is running, as a badge beside the heading, the way every
        # other status in the window is shown.
        self.status_label = badge("", "TEXT_MUTED")
        self.status_label.setAccessibleName("Scheduler status")
        self.pause_button = QPushButton("Pause posting")
        self.pause_button.clicked.connect(self.toggle)
        box, inner = self._card("Scheduler", self.status_label, self.pause_button)
        self.status_detail = self._muted()
        inner.addWidget(self.status_detail)
        self.waiting_label = self._muted()
        inner.addWidget(self.waiting_label)
        return box

    def _build_account(self) -> QWidget:
        self.switch_button = QPushButton(onboarding.SWITCH_ACTION)
        self.switch_button.clicked.connect(self.begin_switch)
        box, inner = self._card("Facebook account", action=self.switch_button)
        self.account_heading = QLabel("")
        self.account_heading.setStyleSheet("font-weight: 600;")
        self.account_heading.setWordWrap(True)
        inner.addWidget(self.account_heading)
        self.account_detail = self._muted()
        inner.addWidget(self.account_detail)
        self.account_note = self._muted()
        inner.addWidget(self.account_note)

        # The question, when it is asked, takes the place of the card's text,
        # and its answers sit under it. No dialog.
        self.confirm_row = row()
        line = QHBoxLayout(self.confirm_row)
        line.setContentsMargins(0, theme.PAD_S, 0, 0)
        line.addStretch(1)
        self.cancel_button = QPushButton(onboarding.SWITCH_CANCEL)
        self.cancel_button.clicked.connect(self.cancel_switch)
        line.addWidget(self.cancel_button)
        self.confirm_button = QPushButton(onboarding.SWITCH_CONFIRM)
        self.confirm_button.clicked.connect(self.confirm_switch)
        line.addWidget(self.confirm_button)
        inner.addWidget(self.confirm_row)
        return box

    def _build_pc(self) -> QWidget:
        box, inner = self._card("This PC")
        # Switches, not checkboxes: each one changes Windows the moment it is
        # flipped, with nothing to save.
        self.autostart_box = Switch(AUTOSTART_LABEL)
        self.autostart_box.clicked.connect(self.set_autostart)
        self._divider(inner)
        inner.addWidget(self._setting(
            AUTOSTART_LABEL, self.autostart_box, self._muted(AUTOSTART_HELP)
        ))
        self.awake_box = Switch(AWAKE_LABEL)
        self.awake_box.clicked.connect(self.set_keep_awake)
        self._divider(inner)
        inner.addWidget(self._setting(AWAKE_LABEL, self.awake_box, self._muted(AWAKE_HELP)))
        self.pc_note = self._muted()
        self.pc_note.setContentsMargins(0, theme.PAD_S, 0, 0)
        self.pc_note.setVisible(False)
        inner.addWidget(self.pc_note)
        return box

    # -- showing -----------------------------------------------------------
    def on_show(self) -> None:
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

        set_badge(self.status_label, headline, colour)
        self.status_detail.setText(detail)
        self.waiting_label.setText(
            f"Waiting: {waiting}." if waiting else "Nothing is waiting to go out."
        )
        self.pause_button.setEnabled(worker is not None)
        self.pause_button.setText("Resume posting" if paused else "Pause posting")

    # -- the scheduler -----------------------------------------------------
    def toggle(self) -> None:
        self.app.toggle_worker()

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
        self.confirm_row.setVisible(confirming)
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
            self._set_pc_note("Windows did not say whether the app starts when you sign in.")

    def _on_pc_failed(self, exc: Exception) -> None:
        self._set_pc_busy(False, "")
        self.app.toast(f"That did not work: {exc}", "error")
        # The boxes show what the user clicked, not what is true. Read it again.
        self.load_pc()

    def _set_pc_busy(self, busy: bool, note: str) -> None:
        self._pc_busy = busy
        self.autostart_box.setEnabled(not busy)
        self.awake_box.setEnabled(not busy)
        self._set_pc_note(note)

    def _set_pc_note(self, note: str) -> None:
        # Hidden when there is nothing to say, so it leaves no gap behind.
        self.pc_note.setText(note)
        self.pc_note.setVisible(bool(note))
