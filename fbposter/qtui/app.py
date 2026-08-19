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

from .. import config, power
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
from .widgets import card

APP_TITLE = "Facebook Local Auto-Poster"

WORKER_POLL_MS = 700
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

        self.show_view(NAV_ITEMS[0][0])

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
        self._set_connection(result.state, result.detail)

    def _on_check_error(self, exc: Exception) -> None:
        self.check_button.setEnabled(True)
        self._set_connection(ConnectionState.ERROR, str(exc))

    def _set_connection(self, state: ConnectionState, detail: str = "",
                        announce: bool = True) -> None:
        self.connection_state = state
        key, label, level = PILL_STATES[state]
        colour = theme.C[key]
        self.pill_label.setText(f"● {label}")
        self.pill_label.setStyleSheet(f"color: {colour};")
        if announce and detail:
            self.toast(detail, level)

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
    lock = SingleInstance()
    if not lock.acquired:
        print(
            "The auto-poster is already running — look for its window in the "
            "taskbar. Running two copies would mean two schedulers on one "
            "database, so this one will close."
        )
        return 1

    application = QApplication(sys.argv)
    theme.activate()
    application.setStyleSheet(theme.stylesheet())
    # Family only. Sizes belong to the stylesheet -- setting a point size here
    # as well meant two sources of truth, and the smaller one was winning.
    application.setFont(QFont(theme.FONT_FAMILY))

    window = App()
    window.show()
    # Only the real GUI starts the scheduler; constructing an App does not.
    QTimer.singleShot(500, window.start_worker)
    return application.exec()
