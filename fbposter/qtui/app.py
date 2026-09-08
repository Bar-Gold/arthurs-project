"""The main window: sidebar navigation, content area and connection pill.

Nothing here raises the window, forces focus, or opens a modal dialog for
status -- the same rule the Tk build followed, for the same reason: the user
keeps working while batches run. Feedback goes to the toast strip.
"""

from __future__ import annotations

import queue
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, login, onboarding, power
from ..automation.groupinfo import LiveGroupNamer
from ..db import Database
from ..db.models import SCHEDULE_ACTIVE
from ..db.repo import GroupRepo, ScheduleRepo, SettingsRepo, TaskRepo, TemplateRepo
from ..ui.connection import ConnectionResult, ConnectionState, check_connection
from ..worker import PostingWorker
from . import theme
from .views.compose import ComposeView
from .views.groups import GroupsView
from .views.publish import PublishView
from .views.queue import QueueView
from .views.welcome import WelcomeView
from .widgets import card

APP_TITLE = "Facebook Local Auto-Poster"

# Set the first time the connection check comes back CONNECTED. Until then
# the window opens on the setup wizard, because until then the app cannot
# actually do the thing it exists to do. A stored fact rather than a live
# check: deciding this at construction time would mean probing the debug
# port on the thread drawing the window, which is up to a full second of
# frozen grey before anything appears.
SETUP_COMPLETE_KEY = "setup_complete"

WORKER_POLL_MS = 700
# One automatic retry of the startup connection check, and only one.
# The logon task starts the app 45 seconds after sign-in, which is while
# Windows is still bringing the network up -- and the check ends in a real
# page load, so it can fail for a reason that fixes itself. A single
# delayed retry covers that without becoming a poll: nothing here should
# ever open Facebook on a timer.
STARTUP_RECHECK_MS = 15_000
TOAST_MS = 4500

# The order is the flow: what to say, who to say it to, when to say it, and
# what happened. The sidebar is the sequence, so it reads top to bottom, and
# the first three are numbered -- four equal-looking items gave no clue that
# they are meant to be walked in order.
NAV_ITEMS = (
    ("compose", "Compose", ComposeView),
    ("groups", "Groups", GroupsView),
    ("publish", "Publish", PublishView),
    ("queue", "Queue", QueueView),
)

# Keys that form the three-step path, in order. Queue is where you look
# afterwards, not a step, so it sits below a divider and is not numbered.
FLOW_STEPS = ("compose", "groups", "publish")

NAV_HINTS = {
    "compose": "Write the post",
    "groups": "Choose the groups",
    "publish": "Choose when",
    "queue": "See what happened",
}

PILL_STATES: dict[ConnectionState, tuple[str, str, str]] = {
    ConnectionState.UNKNOWN: ("NEUTRAL", "Not checked", "info"),
    ConnectionState.CHECKING: ("WARNING", "Checking…", "info"),
    ConnectionState.CONNECTED: ("SUCCESS", "Connected", "success"),
    ConnectionState.CHROME_DOWN: ("NEUTRAL", "Chrome not running", "warning"),
    ConnectionState.LOGGED_OUT: ("WARNING", "Logged out", "warning"),
    ConnectionState.CHECKPOINT: ("DANGER", "Checkpoint!", "error"),
    ConnectionState.ERROR: ("DANGER", "Error", "error"),
}

TOAST_COLOURS = {
    "info": "TEXT_MUTED",
    "success": "SUCCESS",
    "warning": "WARNING",
    "error": "DANGER",
}


class App(QMainWindow):
    """Same seams as the Tk App: check_fn, db and group_namer are injectable.

    Constructing one deliberately does not start the posting worker; only
    start_worker() does, which is what lets a test build a window without a
    thread quietly posting to Facebook.
    """

    _background_done = Signal(object, object)
    _worker_event = Signal(object)

    def __init__(self, check_fn=check_connection, db: Database | None = None,
                 group_namer=None, confirm_close=None) -> None:
        super().__init__()
        self._check_fn = check_fn
        # Inert unless supplied, like check_fn and group_namer: the real one
        # opens a modal dialog, and a suite that popped one would hang rather
        # than fail.
        self._confirm_close = confirm_close if confirm_close is not None else ask_before_closing
        self.worker: PostingWorker | None = None
        self.worker_events: "queue.Queue" = queue.Queue()
        self.group_namer = group_namer if group_namer is not None else LiveGroupNamer()
        self.db = db if db is not None else Database(config.database_path())
        self.group_repo = GroupRepo(self.db)
        self.template_repo = TemplateRepo(self.db)
        self.task_repo = TaskRepo(self.db)
        self.settings_repo = SettingsRepo(self.db)
        self.schedule_repo = ScheduleRepo(self.db)
        self.connection_state = ConnectionState.UNKNOWN
        # The full result, which the setup wizard needs -- the pill only
        # ever needed the state.
        self.connection_result: ConnectionResult | None = None
        # Armed by begin_startup_checks and spent by the first result, so
        # a window a test builds never schedules a timer.
        self._startup_retry_pending = False

        # Which groups this post is going to. It lives on the window rather
        # than in a view because three screens need to agree on it: Groups
        # ticks them, Compose opens a wording tab per group, and Publish sends
        # to them. Held as ids, so a group deleted underneath simply drops out.
        self.selected_groups: set[int] = set()

        self.setWindowTitle(APP_TITLE)
        self.resize(*theme.WINDOW_DEFAULT)
        self.setMinimumSize(*theme.WINDOW_MIN)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.views: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self._build_sidebar(layout)
        self._build_content(layout)

        self._background_done.connect(self._on_background_done)
        self._worker_event.connect(self._handle_worker_event)

        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.timeout.connect(lambda: self.toast_label.setText(""))

        self._pump = QTimer(self)
        self._pump.timeout.connect(self._drain_worker_events)
        self._pump.start(WORKER_POLL_MS)

        self.show_view(self._opening_view())

    def _opening_view(self) -> str:
        """The setup wizard until the app has connected once, then Compose.

        Reading a stored setting rather than checking Chrome: the check is slow
        enough to be visible, and this runs before the window is on screen.
        """
        if self.settings_repo.get(SETUP_COMPLETE_KEY) == "1":
            return NAV_ITEMS[0][0]
        return "welcome"

    # -- layout ------------------------------------------------------------
    def _build_sidebar(self, layout: QHBoxLayout) -> None:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_WIDTH)
        column = QVBoxLayout(sidebar)
        column.setContentsMargins(theme.PAD_M, theme.PAD_L, theme.PAD_M, theme.PAD_M)
        column.setSpacing(theme.PAD_XS)

        brand = QLabel("Auto-Poster")
        brand.setObjectName("Brand")
        column.addWidget(brand)
        column.addSpacing(theme.PAD_M)

        group = QButtonGroup(self)
        for key, label, _view in NAV_ITEMS:
            if key == "queue":
                # Queue is not a step -- it is where you look afterwards.
                column.addSpacing(theme.PAD_S)
                divider = QFrame()
                divider.setObjectName("Divider")
                divider.setFixedHeight(1)
                column.addWidget(divider)
                column.addSpacing(theme.PAD_S)

            step = FLOW_STEPS.index(key) + 1 if key in FLOW_STEPS else None
            button = QPushButton(f"{step}.  {label}" if step else label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            # Spoken by screen readers and shown on hover; the visible label
            # stays short so the sidebar does not turn into prose.
            button.setToolTip(NAV_HINTS.get(key, label))
            button.setAccessibleName(f"{label} — {NAV_HINTS.get(key, '')}".strip(" —"))
            button.clicked.connect(lambda _checked, k=key: self.show_view(k))
            group.addButton(button)
            column.addWidget(button)
            self.nav_buttons[key] = button

        column.addStretch(1)
        self._build_worker_row(column)
        self._build_pill(column)
        layout.addWidget(sidebar)

    def _build_worker_row(self, column: QVBoxLayout) -> None:
        box = card()
        inner = QVBoxLayout(box)
        inner.setContentsMargins(theme.PAD_S, theme.PAD_S, theme.PAD_S, theme.PAD_S)
        self.worker_label = QLabel("Scheduler: off")
        self.worker_label.setObjectName("Muted")
        inner.addWidget(self.worker_label)
        self.worker_button = QPushButton("Pause")
        self.worker_button.clicked.connect(self.toggle_worker)
        inner.addWidget(self.worker_button)
        column.addWidget(box)

    def _build_pill(self, column: QVBoxLayout) -> None:
        box = card()
        inner = QVBoxLayout(box)
        inner.setContentsMargins(theme.PAD_S, theme.PAD_S, theme.PAD_S, theme.PAD_S)
        self.pill_label = QLabel()
        inner.addWidget(self.pill_label)
        # Deliberately not #Primary. Exactly one accent-filled button belongs on
        # screen at a time -- the next step in the flow -- and this is a utility
        # that sits on every screen. Filled, it outranked the step it was
        # sitting next to on all four.
        self.check_button = QPushButton("Check connection")
        self.check_button.clicked.connect(self.check_connection)
        inner.addWidget(self.check_button)

        # Says what it will do about whatever the pill is reporting, and does
        # it by navigating -- the wizard owns every action. Before this the pill
        # reported "Chrome not running" and the detail told the user to run a
        # terminal command, which is not a thing the person this app was built
        # for is ever going to do. When there is nothing wrong it offers the
        # account switch, which is otherwise unreachable.
        self.fix_button = QPushButton()
        self.fix_button.setVisible(False)
        self.fix_button.clicked.connect(lambda: self.show_view("welcome"))
        inner.addWidget(self.fix_button)

        column.addWidget(box)
        self._set_connection(ConnectionState.UNKNOWN, announce=False)

    def _build_content(self, layout: QHBoxLayout) -> None:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(theme.PAD_XL, theme.PAD_L, theme.PAD_XL, theme.PAD_M)
        column.setSpacing(theme.PAD_S)

        self.stack = QStackedWidget()
        for key, _label, view_class in NAV_ITEMS:
            view = view_class(self)
            self.views[key] = view
            self.stack.addWidget(view)
        # Reachable, but deliberately not in the sidebar: setup is something you
        # finish, not a step you keep coming back to. The pill's fix button and
        # the first launch are the two ways in.
        self.views["welcome"] = WelcomeView(self)
        self.stack.addWidget(self.views["welcome"])
        column.addWidget(self.stack, 1)

        # Status lives here, never in a dialog.
        self.toast_label = QLabel("")
        self.toast_label.setObjectName("Muted")
        self.toast_label.setWordWrap(True)
        column.addWidget(self.toast_label)
        layout.addWidget(holder, 1)

    # -- the shared group selection ----------------------------------------
    def selected_group_ids(self) -> list[int]:
        """The chosen groups, in the order the Groups screen lists them.

        Derived from the live group list rather than kept as an ordered field,
        so a group removed while it was ticked disappears from here too instead
        of surviving as an id that no longer resolves.
        """
        return [g.id for g in self.group_repo.list() if g.id in self.selected_groups]

    def set_group_selected(self, group_id: int, selected: bool) -> None:
        if selected:
            self.selected_groups.add(group_id)
        else:
            self.selected_groups.discard(group_id)

    # -- navigation --------------------------------------------------------
    def show_view(self, key: str) -> None:
        view = self.views[key]
        self.stack.setCurrentWidget(view)
        self.current_view = key
        for name, button in self.nav_buttons.items():
            button.setChecked(name == key)
        on_show = getattr(view, "on_show", None)
        if on_show is not None:
            on_show()

    # -- toast -------------------------------------------------------------
    def toast(self, message: str, level: str = "info") -> None:
        colour = theme.C[TOAST_COLOURS.get(level, "TEXT_MUTED")]
        self.toast_label.setStyleSheet(f"color: {colour};")
        self.toast_label.setText(message)
        self._toast_timer.start(TOAST_MS)

    # -- background work ---------------------------------------------------
    def run_in_background(self, fn, on_success=None, on_error=None) -> None:
        """Off the GUI thread, back onto it through a queued signal.

        Same contract as the Tk BackgroundRunner: nothing touches a widget from
        the worker thread.
        """

        def work():
            try:
                result = fn()
            except Exception as exc:  # reported, never swallowed
                self._background_done.emit(on_error, exc)
            else:
                self._background_done.emit(on_success, result)

        threading.Thread(target=work, daemon=True).start()

    def _on_background_done(self, callback, payload) -> None:
        if callback is not None:
            callback(payload)

    # -- connection --------------------------------------------------------
    def check_connection(self) -> None:
        if self.connection_state is ConnectionState.CHECKING:
            return
        self._set_connection(ConnectionState.CHECKING, announce=False)
        self.check_button.setEnabled(False)
        self.run_in_background(
            self._check_fn, self._on_check_result, self._on_check_error
        )

    def _on_check_result(self, result: ConnectionResult) -> None:
        self.check_button.setEnabled(True)
        self.connection_result = result
        if result.state is ConnectionState.CONNECTED:
            # Only a real connection retires the wizard. Anything less and the
            # window goes on opening there, which is where the fix is.
            self.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        self._set_connection(result.state, result.detail)
        self._refresh_welcome()

        if self._startup_retry_pending:
            self._startup_retry_pending = False
            if result.state is not ConnectionState.CONNECTED:
                QTimer.singleShot(STARTUP_RECHECK_MS, self.check_connection)

    def _on_check_error(self, exc: Exception) -> None:
        self.check_button.setEnabled(True)
        self.connection_result = ConnectionResult(ConnectionState.ERROR, str(exc))
        self._set_connection(ConnectionState.ERROR, str(exc))
        self._refresh_welcome()

    def note_connection(self, result: ConnectionResult) -> None:
        """Record something the app learned about the session without checking.

        Signing out for an account switch is the case that needs it: the app
        *knows* the session has gone the moment it drops the cookies, and
        waiting for a round trip to say so would leave the pill green and the
        wizard congratulating the user while a login form is already in front
        of them.

        Not announced as a toast. The screen that asked for this is showing the
        same fact in full, and the pill has already changed colour.
        """
        self.connection_result = result
        self._set_connection(result.state, result.detail, announce=False)
        self._refresh_welcome()

    def _refresh_welcome(self) -> None:
        view = self.views.get("welcome")
        if view is not None:
            view.refresh()

    def _set_connection(self, state: ConnectionState, detail: str = "",
                        announce: bool = True) -> None:
        self.connection_state = state
        key, label, level = PILL_STATES[state]
        colour = theme.C[key]
        self.pill_label.setText(f"● {label}")
        self.pill_label.setStyleSheet(f"color: {colour};")
        self._refresh_fix_button()
        if announce and detail:
            self.toast(detail, level)

    def _refresh_fix_button(self) -> None:
        """Offer the repair for whatever the pill is currently reporting.

        The button only navigates -- the wizard owns every action, so there is
        one implementation of "start Chrome", one of "log in" and one of "sign
        out and switch" rather than a second copy here that can drift. That
        matters most for the switch, which is the one that destroys something.
        """
        button = getattr(self, "fix_button", None)
        if button is None:  # called from _build_pill before it exists
            return
        if self.connection_state in (ConnectionState.CHECKING, ConnectionState.UNKNOWN):
            # Nothing has been established yet, so there is nothing to offer.
            button.setVisible(False)
            return
        if self.connection_state is ConnectionState.CONNECTED:
            # Nothing is wrong, so the only thing left worth offering is the one
            # thing a working setup might still need changed: which account it
            # posts as. Without this the wizard is unreachable once it has been
            # retired, and the alternative is renaming a folder by hand.
            button.setText(onboarding.SWITCH_ACTION)
            button.setVisible(True)
            return
        step = onboarding.plan(login.chrome_installed(), self.connection_result)
        guide = onboarding.guidance(step)
        button.setText(guide.action or "What do I do?")
        button.setVisible(True)

    # -- the posting worker ------------------------------------------------
    def start_worker(self) -> None:
        """Start the one posting worker. Never called from __init__."""
        if self.worker is not None:
            return
        self.worker = PostingWorker(
            self.db, events=self.worker_events, on_battery=power.on_battery
        )
        self.worker.start()
        self._refresh_worker_row()

    def begin_startup_checks(self) -> None:
        """Get Chrome up and the connection checked, without blocking the window.

        Started by run(), never by __init__, for the same reason start_worker
        is: a test builds a window and must not thereby launch a browser.

        The order matters and so does the threading. Launching Chrome waits on
        the debugging port for up to LAUNCH_TIMEOUT_S -- thirty seconds -- so
        doing it before the window is shown would mean thirty seconds of
        nothing on screen, which every user reads as a crash. The window comes
        up first, the wizard says Chrome is not running yet, and both correct
        themselves when this lands.
        """
        # Arms the one automatic retry, which only the startup check gets.
        self._startup_retry_pending = True

        def work():
            # A failure here is not worth reporting on its own: the connection
            # check that follows says the same thing in the user's terms, and
            # the wizard offers the button that fixes it.
            try:
                login.start_chrome()
            except Exception:
                # Deliberately broad. run_in_background routes an exception to
                # the on_error callback, and there is none here -- so anything
                # escaping would skip the connection check entirely and leave
                # the pill reading "Not checked" for ever, which is the one
                # state that offers the user no way forward.
                pass
            return None

        self.run_in_background(work, lambda _result: self.check_connection())

    def toggle_worker(self) -> None:
        if self.worker is None:
            return
        if self.worker.paused:
            self.worker.resume()
        else:
            self.worker.pause()
        self._refresh_worker_row()

    def _refresh_worker_row(self) -> None:
        if self.worker is None:
            self.worker_label.setText("Scheduler: off")
            self.worker_button.setEnabled(False)
            return
        state = self.worker.state
        self.worker_label.setText(f"Scheduler: {state}")
        self.worker_button.setEnabled(True)
        self.worker_button.setText("Resume" if self.worker.paused else "Pause")

    def _drain_worker_events(self) -> None:
        while True:
            try:
                event = self.worker_events.get_nowait()
            except queue.Empty:
                break
            self._handle_worker_event(event)
        self._refresh_worker_row()

    def _handle_worker_event(self, event) -> None:
        level = {
            "halted": "error",
            "schedule_error": "error",
            "posted": "success",
            "scheduled": "success",
            "skipped": "warning",
            "missed": "warning",
            # Neither is a success: one has not landed yet, the other never
            # will. An "info" toast reads as "that went fine".
            "pending": "warning",
            "declined": "warning",
            # Nothing has gone wrong yet, but it is about to unless the user
            # does something -- which is exactly what a warning is for.
            "power": "warning",
        }.get(event.kind, "info")
        self.toast(event.message, level)
        queue_view = self.views.get("queue")
        if queue_view is not None and hasattr(queue_view, "refresh"):
            queue_view.refresh()
        # A schedule that just fired has a new "next run"; the card would go on
        # showing the slot it already used otherwise.
        if event.kind in ("scheduled", "schedule_error", "missed"):
            publish_view = self.views.get("publish")
            if publish_view is not None:
                publish_view.refresh_schedules()

    def unfinished_work(self) -> str | None:
        """What closing the window would stop, phrased for a person.

        None when nothing is waiting, which is the common case and must not
        cost the user a dialog.
        """
        batches = self.task_repo.unfinished_count()
        repeats = sum(1 for s in self.schedule_repo.list() if s.state == SCHEDULE_ACTIVE)
        if not batches and not repeats:
            return None

        parts = []
        if batches:
            parts.append(f"{batches} batch{'es' if batches != 1 else ''} still to go out")
        if repeats:
            parts.append(f"{repeats} repeating post{'s' if repeats != 1 else ''}")
        return " and ".join(parts)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """Warn before closing, because closing is what stops the posting.

        The worker is this window's thread: closing stopped it silently, so a
        daily repeat set up and then closed away simply never ran again, with
        nothing on screen to say so. Only asked when something is actually
        waiting -- an empty queue closes without a word.
        """
        if self.worker is not None:
            waiting = self.unfinished_work()
            if waiting is not None and not self._confirm_close(self, waiting):
                event.ignore()
                return
            self.worker.stop()
        self.db.close()
        super().closeEvent(event)


def ask_before_closing(parent, waiting: str) -> bool:
    """The one dialog the app raises of its own accord, and why it is allowed.

    Status never gets a dialog -- that rule stands, and it exists so the app
    cannot interrupt someone working in another window. This cannot: it only
    appears because the user just clicked the close button on this window, so
    the app already has focus and the user is looking straight at it. Same
    reasoning as the media file picker in Compose.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Posting stops when this closes")
    box.setText("Nothing is posted while the app is shut.")
    box.setInformativeText(
        f"You have {waiting}. None of it will go out until you open the app "
        "again — the scheduler runs in this window, not in the background."
    )
    stay = box.addButton("Leave it open", QMessageBox.RejectRole)
    go = box.addButton("Close anyway", QMessageBox.AcceptRole)
    box.setDefaultButton(stay)
    box.exec()
    # Positively "they chose to close", not "they did not choose to stay":
    # Escape and the title-bar X both leave clickedButton() at the reject
    # button or None, and either way the window should stay open.
    return box.clickedButton() is go


def run() -> int:
    """Launch the GUI. Returns an exit code."""
    import sys

    from PySide6.QtWidgets import QApplication

    from ..single import SingleInstance

    # One app, one worker. A second copy would be a second worker on the same
    # database; TaskRepo.claim_target stops that becoming a duplicate post, but
    # two of them would still fight over Chrome and both hold the machine awake.
    # The QApplication comes first so the refusal below can be *seen*. Packaged
    # as a windowed .exe there is no console, so the print this used to do went
    # nowhere: double-clicking the shortcut twice made the second copy vanish
    # without a word, which reads as the app being broken.
    application = QApplication(sys.argv)

    lock = SingleInstance()
    if not lock.acquired:
        box = QMessageBox(QMessageBox.Information, APP_TITLE,
                          "The auto-poster is already running.")
        box.setInformativeText(
            "Look for its window in the taskbar. Two copies would mean two "
            "schedulers sharing one database, so this one will close."
        )
        box.exec()
        return 1

    theme.activate()
    application.setStyleSheet(theme.stylesheet())
    # Family only. Sizes belong to the stylesheet -- setting a point size here
    # as well meant two sources of truth, and the smaller one was winning.
    application.setFont(QFont(theme.FONT_FAMILY))

    window = App()
    window.show()
    # Only the real GUI starts the scheduler; constructing an App does not.
    QTimer.singleShot(500, window.start_worker)
    # Same rule, same reason: a window a test builds must not launch a browser.
    QTimer.singleShot(50, window.begin_startup_checks)
    return application.exec()
