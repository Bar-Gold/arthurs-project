"""The main window: sidebar navigation, content area and connection pill.

Nothing here raises the window, forces focus, or opens a modal dialog for
status -- the same rule the Tk build followed, for the same reason: the user
keeps working while batches run. Feedback goes to the toast strip.
"""

from __future__ import annotations

import queue
import threading
import time

import shiboken6
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import (
    QApplication,
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

from .. import always_on, chrome, config, keepalive, login, onboarding, power, session, tabs
from ..automation.groupfinder import LiveGroupFinder
from ..automation.groupinfo import LiveGroupNamer
from ..db import Database
from ..db.models import SCHEDULE_ACTIVE
from ..db.repo import (
    GroupRepo,
    GroupSetRepo,
    ScheduleRepo,
    SettingsRepo,
    TaskRepo,
    TemplateRepo,
)
from ..connection import ConnectionResult, ConnectionState, check_connection
from ..worker import PostingWorker
from . import icons, theme
from .views.create import CreatePostView
from .views.dashboard import DashboardView
from .views.group_sets import GroupSetsView
from .views.groups import IMPORT_PENDING_KEY
from .views.home import HomeView
from .views.schedule_edit import ScheduleEditView
from .views.settings import SettingsView
from .views.tips import TipsView
from .views.welcome import WelcomeView
from .widgets import AppStyle, StatusBanner, row

APP_TITLE = "Facebook Local Auto-Poster"
# The logo at the top of the sidebar, and the room between it and the name.
BRAND_SIZE = 24
BRAND_GAP = 4

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
# How often the window looks for the background Chrome; see keepalive.py.
CHROME_WATCH_MS = keepalive.WATCH_EVERY_S * 1000
CHECK_DEADLINE_MS = keepalive.CHECK_DEADLINE_S * 1000

# Home, then the road to a post, then how it is going. Create a post holds the
# three steps as one road (the user's request, 2026-10-19): they used to be
# three items of their own. Each item has an icon (icons.py). Settings is
# somewhere you go on purpose, so it sits at the foot of the sidebar. Group
# sets is not a step: it is where sets are made, and they are picked in step 2
# (its own item, the user's choice, 2026-10-15). Posting tips is advice to
# read, in three languages (2026-10-18).
NAV_ITEMS = (
    ("home", "Home", HomeView),
    ("create", "Create a post", CreatePostView),
    ("dashboard", "Dashboard", DashboardView),
    ("group_sets", "Group sets", GroupSetsView),
    ("tips", "Posting tips", TipsView),
    ("settings", "Settings", SettingsView),
)

# The three steps inside Create a post, in order. Each is still a view of its
# own in `App.views`, and `show_view` routes its key to the step. The
# Dashboard is where you look afterwards, not a step, so it sits below a
# divider. It replaced the Queue screen (2026-10-14).
FLOW_STEPS = ("compose", "groups", "publish")

# Screens that are not in the sidebar, and the item that stays lit while one
# is open. Editing a repeating post is reached from Publish, so Publish is
# where you are. The setup wizard belongs to no step, so nothing is lit: it
# used to leave whichever item was clicked last lit, often Settings, which
# answered "where am I?" wrongly.
NAV_PARENT = {
    "compose": "create",
    "groups": "create",
    "publish": "create",
    "schedule_edit": "create",
}

NAV_HINTS = {
    "home": "Where to start",
    "create": "Write the post, choose the groups, then choose when",
    "dashboard": "See how the posting is going",
    "group_sets": "Keep groups together, to pick as one",
    "tips": "Advice for posting safely, in Hebrew, English or Russian",
    "settings": "Pause posting, the Facebook account, and this PC",
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

# Said when Undo is pressed but what it would put back has changed since: a
# batch cancelled and then finished, a group already added back.
TOO_LATE_TO_UNDO = "Too late to undo: it changed in the meantime."


class App(QMainWindow):
    """check_fn, db, group_namer and confirm_close are injectable.

    Constructing one deliberately does not start the posting worker; only
    start_worker() does, which is what lets a test build a window without a
    thread quietly posting to Facebook.
    """

    _background_done = Signal(object, object)
    _worker_event = Signal(object)

    def __init__(self, check_fn=check_connection, db: Database | None = None,
                 group_namer=None, confirm_close=None, group_finder=None) -> None:
        super().__init__()
        self._check_fn = check_fn
        # The real one by default, which opens a modal dialog. closeEvent asks
        # only while a worker is attached, which a test window does not have
        # unless it adds one -- and then it passes a stub here, or detaches the
        # worker before closing, since a real dialog would hang the suite.
        self._confirm_close = confirm_close if confirm_close is not None else ask_before_closing
        self.worker: PostingWorker | None = None
        self.worker_events: "queue.Queue" = queue.Queue()
        self.group_namer = group_namer if group_namer is not None else LiveGroupNamer()
        # Reads the account's groups off Facebook (automation/groupfinder.py).
        # None means switched off, and that is the default: run() switches the
        # real one on, so no window the suite builds can open Facebook, even
        # with the app's Chrome running on the machine.
        self.group_finder = group_finder
        self.db = db if db is not None else Database(config.database_path())
        self.group_repo = GroupRepo(self.db)
        self.template_repo = TemplateRepo(self.db)
        self.task_repo = TaskRepo(self.db)
        self.settings_repo = SettingsRepo(self.db)
        self.schedule_repo = ScheduleRepo(self.db)
        self.group_set_repo = GroupSetRepo(self.db)
        self.connection_state = ConnectionState.UNKNOWN
        # The full result, which the setup wizard needs -- the pill only
        # ever needed the state.
        self.connection_result: ConnectionResult | None = None
        # Armed by begin_startup_checks and spent by the first result, so
        # a window a test builds never schedules a timer.
        self._startup_retry_pending = False

        # Keeping the background Chrome alive (keepalive.py). The timer is
        # started by begin_startup_checks, never here, for the usual reason: a
        # window a test builds must not start a browser. The three functions
        # are seams, so the tests can say "Chrome has gone" without it having.
        # Looked up at call time, not captured: a test that patches
        # login.start_chrome after the window exists must still be obeyed, or
        # the real one runs -- and on a machine without Chrome up, launches it.
        self._keeper = keepalive.ChromeKeeper()
        self._watching = False
        self._chrome_probe = lambda: chrome.probe()
        self._chrome_installed = lambda: login.chrome_installed()
        self._chrome_start = lambda: login.start_chrome()
        self._chrome_profile_busy = lambda: chrome.profile_in_use(config.resolve_profile_dir())
        # Consecutive looks that found Chrome holding the profile but silent.
        self._busy_looks = 0
        self._monotonic = time.monotonic
        # Looking inside Chrome for a tab that has stopped answering, and the
        # last-resort restart (tabs.py). Inert here; run() wires up the real
        # ones. Every window the suite builds keeps these, so no test can look
        # inside -- let alone close a tab in -- a Chrome that happens to be
        # running on the machine.
        self._clear_stuck_tabs = lambda: []
        self._restart_chrome = lambda: tabs.GONE
        # Closing the app's Chrome when this window closes. Inert for the same
        # reason, or every window the suite closes would close it.
        self._close_chrome = lambda: tabs.GONE
        # Starting with Windows and keeping the PC awake (always_on.py). Inert
        # for the same reason: the real one changes the machine's power plan
        # and scheduled tasks, and run() is the only place that wires it up.
        self.always_on = always_on.Inert()
        self._last_tab_sweep: float | None = None
        self._last_stall_restart: float | None = None
        # Each check is numbered, so the answer to one given up on at its
        # deadline is known to be stale if it ever does arrive.
        self._check_serial = 0

        # Which groups this post is going to. It lives on the window rather
        # than in a view because three screens need to agree on it: Groups
        # ticks them, Compose opens a wording tab per group, and Publish sends
        # to them. Held as ids, so a group deleted underneath simply drops out.
        self.selected_groups: set[int] = set()
        # The group sets picked on Groups, each as one item. Their groups go
        # with the post on top of the ones ticked; a repeating post follows
        # the sets themselves.
        self.selected_sets: set[int] = set()

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

        self._pump = QTimer(self)
        self._pump.timeout.connect(self._drain_worker_events)
        self._pump.start(WORKER_POLL_MS)

        self._chrome_watch = QTimer(self)
        self._chrome_watch.timeout.connect(self.watch_chrome)

        self._check_deadline = QTimer(self)
        self._check_deadline.setSingleShot(True)
        self._check_deadline.timeout.connect(self._on_check_stalled)

        self.show_view(self._opening_view())

    def _opening_view(self) -> str:
        """The setup wizard until the app has connected once, then Home.

        Reading a stored setting rather than checking Chrome: the check is slow
        enough to be visible, and this runs before the window is on screen.
        """
        if self.settings_repo.get(SETUP_COMPLETE_KEY) == "1":
            return NAV_ITEMS[0][0]
        return "welcome"

    # -- layout ------------------------------------------------------------
    def _build_sidebar(self, layout: QHBoxLayout) -> None:
        sidebar = self.sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_WIDTH)
        column = QVBoxLayout(sidebar)
        column.setContentsMargins(theme.PAD_M, theme.PAD_L, theme.PAD_M, theme.PAD_M)
        column.setSpacing(theme.PAD_XS)

        # The app's own icon beside its name, the one on the taskbar. It leads
        # Home, the way a website's logo does (the user's request,
        # 2026-10-19). The mark is drawn wider than tall to give the name the
        # same gap it had as two labels: Qt fixes a button's at 4px.
        self.brand_button = QPushButton("Auto-Poster")
        self.brand_button.setObjectName("Brand")
        self.brand_button.setIcon(QIcon(icons.brand(BRAND_SIZE, BRAND_SIZE + BRAND_GAP)))
        self.brand_button.setIconSize(QSize(BRAND_SIZE + BRAND_GAP, BRAND_SIZE))
        self.brand_button.setCursor(Qt.PointingHandCursor)
        self.brand_button.setToolTip("Home")
        self.brand_button.setAccessibleName("Auto-Poster: Home")
        self.brand_button.clicked.connect(lambda: self.show_view("home"))
        column.addWidget(self.brand_button)
        column.addSpacing(theme.PAD_L)

        group = QButtonGroup(self)
        self._nav_group = group
        for key, label, _view in NAV_ITEMS:
            if key == "settings":
                # Down at the foot, with the connection: somewhere you go on
                # purpose, not a step you walk through.
                column.addStretch(1)
                self._build_paused_notice(column)
            if key == "dashboard":
                # Not a step: it is where you look afterwards.
                column.addSpacing(theme.PAD_S)
                divider = QFrame()
                divider.setObjectName("Divider")
                divider.setFixedHeight(1)
                column.addWidget(divider)
                column.addSpacing(theme.PAD_S)

            button = QPushButton(label)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(icons.nav_icon(key))
            button.setIconSize(icons.NAV_ICON_BOX)
            # Spoken by screen readers and shown on hover; the visible label
            # stays short so the sidebar does not turn into prose.
            button.setToolTip(NAV_HINTS.get(key, label))
            button.setAccessibleName(f"{label}: {NAV_HINTS[key]}" if key in NAV_HINTS else label)
            button.clicked.connect(lambda _checked, k=key: self.show_view(k))
            group.addButton(button)
            column.addWidget(button)
            self.nav_buttons[key] = button

        column.addSpacing(theme.PAD_S)
        self._build_pill(column)
        layout.addWidget(sidebar)

    def _build_paused_notice(self, column: QVBoxLayout) -> None:
        """A line that exists only while posting is paused.

        The control lives on Settings, but the fact cannot live only there:
        a pause survives restarts now, and one forgotten about is every post
        silently not happening. Hidden the rest of the time, so an app that
        is posting normally shows nothing here at all.
        """
        self.paused_notice = QPushButton("Posting is paused")
        self.paused_notice.setObjectName("PausedNotice")
        self.paused_notice.setToolTip("Open Settings to resume")
        self.paused_notice.setAccessibleName("Posting is paused. Open Settings to resume")
        self.paused_notice.setVisible(False)
        self.paused_notice.clicked.connect(lambda: self.show_view("settings"))
        column.addWidget(self.paused_notice)

    def _build_pill(self, column: QVBoxLayout) -> None:
        # The foot of the sidebar, under a rule rather than in a box of its
        # own: a card inside the sidebar was a third kind of surface on screen,
        # and the boxed "Not checked" read like a form field.
        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        column.addWidget(divider)
        column.addSpacing(theme.PAD_S)

        box = row()
        inner = QVBoxLayout(box)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(theme.PAD_S)
        # A drawn dot in the state's colour, then the words. The dot was a
        # "●" in the text, which sat on the baseline, a size smaller than
        # the words and a little low.
        status = QHBoxLayout()
        status.setContentsMargins(theme.PAD_S, 0, 0, 0)
        status.setSpacing(theme.PAD_S)
        self.pill_dot = QLabel()
        status.addWidget(self.pill_dot)
        self.pill_label = QLabel()
        status.addWidget(self.pill_label, 1)
        inner.addLayout(status)
        # Deliberately not #Primary. Exactly one accent-filled button belongs on
        # screen at a time -- the next step in the flow -- and this is a utility
        # that sits on every screen. Filled, it outranked the step it was
        # sitting next to on all four.
        self.check_button = QPushButton("Check connection")
        self.check_button.setObjectName("SidebarButton")
        self.check_button.clicked.connect(self.check_connection)
        inner.addWidget(self.check_button)

        # Says what it will do about whatever the pill is reporting, and does
        # it by navigating -- the wizard owns every action. Before this the pill
        # reported "Chrome not running" and the detail told the user to run a
        # terminal command, which is not a thing the person this app was built
        # for is ever going to do. When there is nothing wrong it is hidden;
        # changing account lives on the Settings screen.
        self.fix_button = QPushButton()
        self.fix_button.setObjectName("SidebarButton")
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
        # Not in the sidebar either: it edits one repeating post, and is
        # reached from that post's card on Publish.
        self.views["schedule_edit"] = ScheduleEditView(self)
        self.stack.addWidget(self.views["schedule_edit"])
        column.addWidget(self.stack, 1)

        # Status lives here, never in a dialog.
        self.banner = StatusBanner()
        # The words, by the name every test and caller already reads them by.
        self.toast_label = self.banner.text
        column.addWidget(self.banner)
        layout.addWidget(holder, 1)

    # -- the shared group selection ----------------------------------------
    def selected_group_ids(self) -> list[int]:
        """The chosen groups, in the order the Groups screen lists them: the
        ones ticked, and every group in a picked set.

        Derived from the live group list rather than kept as an ordered field,
        so a group removed while it was ticked disappears from here too instead
        of surviving as an id that no longer resolves. Compose, Now and Once
        read this, so a set reaches them as the groups it holds right now.
        """
        chosen = self.selected_groups | set(self.covered_by_sets())
        return [g.id for g in self.group_repo.list() if g.id in chosen]

    def selected_set_ids(self) -> list[int]:
        """The picked group sets, in the order they are offered. A set deleted
        while picked drops out, as a removed group does."""
        return [s.id for s in self.group_set_repo.list() if s.id in self.selected_sets]

    def covered_by_sets(self) -> dict[int, list[str]]:
        """Each group a picked set brings, with the names of those sets."""
        covered: dict[int, list[str]] = {}
        for group_set in self.group_set_repo.list():
            if group_set.id in self.selected_sets:
                for group_id in group_set.group_ids:
                    covered.setdefault(group_id, []).append(group_set.name)
        return covered

    def plain_group_ids(self) -> list[int]:
        """The ticked groups no picked set brings.

        What a repeating post keeps as its own groups, beside the sets it
        follows. A group that is both ticked and in a picked set is the set's:
        it is shown that way, and leaves the post if it leaves the set.
        """
        covered = self.covered_by_sets()
        return [
            g.id for g in self.group_repo.list()
            if g.id in self.selected_groups and g.id not in covered
        ]

    def set_group_selected(self, group_id: int, selected: bool) -> None:
        if selected:
            self.selected_groups.add(group_id)
        else:
            self.selected_groups.discard(group_id)

    def set_set_selected(self, set_id: int, selected: bool) -> None:
        if selected:
            self.selected_sets.add(set_id)
        else:
            self.selected_sets.discard(set_id)

    # -- navigation --------------------------------------------------------
    def show_view(self, key: str) -> None:
        # Read before the screen changes: changing it moves focus.
        keyboard_in_sidebar = self._focus_in_sidebar()
        if key == "create":
            # The sidebar item: back to whichever step the post was at.
            key = self.views["create"].current_key
        if key in FLOW_STEPS:
            # A step lives inside Create a post, not in the window's stack.
            flow = self.views["create"]
            self.stack.setCurrentWidget(flow)
            self.current_view = key
            self._light(key)
            flow.go_to(key)
            if not keyboard_in_sidebar:
                self._park_focus()
            return
        view = self.views[key]
        self.stack.setCurrentWidget(view)
        if not keyboard_in_sidebar:
            self._park_focus()
        self.current_view = key
        self._light(key)
        on_show = getattr(view, "on_show", None)
        if on_show is not None:
            on_show()

    def _focus_in_sidebar(self) -> bool:
        focused = QApplication.focusWidget()
        return focused is not None and self.sidebar.isAncestorOf(focused)

    def _park_focus(self) -> None:
        """Give keyboard focus to the page area, which draws no ring.

        Left to Qt, focus went to the first button in the window as it opened,
        the logo, and its gold ring showed whenever the window was in front
        (the user's report, 2026-10-20). And when a screen with a field in
        focus was hidden, Qt passed focus on to the next button, which then
        wore the ring nobody had tabbed to. Tab still moves from here to the
        logo, ring and all, so the keyboard loses nothing. A keyboard user
        working the sidebar keeps their place there.
        """
        self.stack.setFocus(Qt.OtherFocusReason)

    def _light(self, key: str) -> None:
        lit = NAV_PARENT.get(key, key)
        # An exclusive group refuses to leave nothing checked, which is what
        # kept the last item lit on a screen that is in no step at all.
        self._nav_group.setExclusive(False)
        for name, button in self.nav_buttons.items():
            button.setChecked(name == lit)
        self._nav_group.setExclusive(True)

    # -- toast -------------------------------------------------------------
    def toast(self, message: str, level: str = "info", undo=None) -> None:
        """Say what just happened, in the banner under the screen.

        `undo` offers an Undo button for as long as the message stays: a
        callable that puts things back and returns what to say about it, or
        None when it no longer can. Forgiveness instead of "are you sure?" --
        a slip is one click to take back, and a deliberate action costs
        nothing extra.
        """
        action = None
        if undo is not None:
            def action() -> None:
                try:
                    said = undo()
                except Exception as exc:  # reported, never swallowed
                    self.toast(f"Could not undo: {exc}", "error")
                    return
                if said:
                    self.toast(said, "success")
                else:
                    self.toast(TOO_LATE_TO_UNDO, "warning")
        self.banner.show_message(message, level, action)

    # -- background work ---------------------------------------------------
    def run_in_background(self, fn, on_success=None, on_error=None) -> None:
        """Off the GUI thread, back onto it through a queued signal.

        Same contract as the Tk BackgroundRunner: nothing touches a widget from
        the worker thread.
        """

        def report(callback, payload):
            try:
                self._background_done.emit(callback, payload)
            except RuntimeError:
                # The window was deleted while this ran (the app closing, or a
                # test window thrown away): nobody is left to tell.
                if shiboken6.isValid(self):
                    raise

        def work():
            try:
                result = fn()
            except Exception as exc:  # reported, never swallowed
                report(on_error, exc)
            else:
                report(on_success, result)

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
        self._check_serial += 1
        serial = self._check_serial
        # Armed before the work starts, so a runner that answers at once still
        # finds it running and stops it.
        self._check_deadline.start(CHECK_DEADLINE_MS)
        self.run_in_background(
            self._check_fn,
            lambda result: self._check_answered(serial, self._on_check_result, result),
            lambda exc: self._check_answered(serial, self._on_check_error, exc),
        )

    def _check_answered(self, serial: int, handler, payload) -> None:
        if serial != self._check_serial:
            # Given up on at its deadline and already dealt with. Chrome being
            # put right under it is usually what finally let it return.
            return
        self._check_deadline.stop()
        handler(payload)

    def _on_check_stalled(self) -> None:
        """A check has not come back in CHECK_DEADLINE_S, so it never will.

        This used to leave the pill on "Checking..." for good: a stuck tab
        hangs Playwright's connect with no timeout at all (tabs.py). What the
        client did by hand is done for them, least drastic first -- close any
        tab that has stopped answering, which alone lets a hung connect finish,
        and only when there is none, restart the app's own Chrome. Never
        mid-post, and at most once in STALL_RESTART_EVERY_S.
        """
        self._check_serial += 1  # its answer, if it ever comes, is stale now
        self.check_button.setEnabled(True)
        detail = "Chrome stopped answering. The app is putting it right."
        self.connection_result = ConnectionResult(ConnectionState.ERROR, detail)
        self._set_connection(ConnectionState.ERROR, detail)
        self._refresh_welcome()
        now = self._monotonic()
        may_restart = (
            self._last_stall_restart is None
            or now - self._last_stall_restart >= keepalive.STALL_RESTART_EVERY_S
        )
        # The keep-alive must not start a Chrome of its own while this one is
        # being closed and started again.
        self._watching = True

        def unstick():
            if self._clear_stuck_tabs():
                return "cleared"
            if not may_restart:
                return "gave_up"
            return self._restart_chrome()

        self.run_in_background(unstick, self._on_unstuck, self._on_unstick_failed)

    def _on_unstuck(self, outcome: str) -> None:
        self._watching = False
        if outcome == "cleared":
            self.toast("A tab in the app's Chrome had stopped answering, so the app closed it.")
            self.check_connection()
            return
        if outcome in (tabs.RESTARTED, tabs.WOULD_NOT_CLOSE):
            self._last_stall_restart = self._monotonic()
        if outcome == tabs.RESTARTED:
            self._keeper.restarted()
            self.toast("Chrome had stopped answering, so the app restarted it.")
            self.check_connection()
            return
        details = {
            tabs.POSTING: "The connection check did not finish. A post is going out, "
                          "so Chrome was left alone; press Check connection once it is done.",
            tabs.NOT_OURS: "The connection check did not finish. Something other than "
                           "the app's Chrome is using its connection, so the app left it alone.",
            tabs.GONE: "The connection check did not finish, and the app's Chrome has "
                       "closed. The app starts it again within a minute.",
        }
        self._report_stuck(details.get(outcome))

    def _on_unstick_failed(self, _exc) -> None:
        self._watching = False
        self._report_stuck(None)

    def _report_stuck(self, detail: str | None) -> None:
        detail = detail or (
            "Chrome is not answering. Close it from the taskbar and the app "
            "will start it again."
        )
        self.connection_result = ConnectionResult(ConnectionState.ERROR, detail)
        self._set_connection(ConnectionState.ERROR, detail)
        self._refresh_welcome()

    def _on_check_result(self, result: ConnectionResult) -> None:
        self.check_button.setEnabled(True)
        self.connection_result = result
        if result.state is ConnectionState.CONNECTED:
            # Only a real connection retires the wizard. Anything less and the
            # window goes on opening there, which is where the fix is.
            if self.settings_repo.get(SETUP_COMPLETE_KEY) != "1":
                # The first login: the client's groups are read off Facebook
                # next (below), so nobody pastes links. Stored, so a read that
                # fails is tried at the next connection. A client upgrading
                # from an older version is already set up, so never gets it.
                self.settings_repo.set(IMPORT_PENDING_KEY, "1")
            self.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        self._set_connection(result.state, result.detail)
        self._refresh_welcome()
        if result.state is ConnectionState.CONNECTED:
            # A group added while Chrome was still starting could not have its
            # name read, and used to show its number until the Groups screen
            # was next opened. This is the first moment it can be read. Only
            # nameless groups are looked up, so usually this does nothing.
            self.views["groups"].look_up_missing_names()
            if self.settings_repo.get(IMPORT_PENDING_KEY) == "1":
                self.views["groups"].import_from_facebook(automatic=True)

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
        # The sidebar's own shade of the colour: it sits on navy.
        colour = theme.C[f"SIDEBAR_{key}"]
        self.pill_dot.setPixmap(icons.pixmap("dot", colour, 12))
        self.pill_label.setText(label)
        self.pill_label.setStyleSheet(f"color: {colour};")
        self._refresh_fix_button()
        # Settings offers the account switch only while there is a session to
        # end, so it hears about every change the pill does.
        settings = self.views.get("settings")
        if settings is not None:
            settings.refresh_account()
        groups = self.views.get("groups")
        if groups is not None:
            groups.refresh_import_button()
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
            # Nothing is wrong, so there is nothing to repair. Changing which
            # account it posts as used to be offered here, one press from
            # "Check connection" on every screen; it lives on Settings now.
            button.setVisible(False)
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
        self._refresh_scheduler()

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
                self._chrome_start()
            except Exception as exc:
                # Deliberately broad. run_in_background routes an exception to
                # the on_error callback, and there is none here -- so anything
                # escaping would skip the connection check entirely and leave
                # the pill reading "Not checked" for ever, which is the one
                # state that offers the user no way forward. Returned instead,
                # so the keeper counts it as a failed start.
                return exc
            return None

        def started(problem):
            if problem is not None:
                self._keeper.failed(self._monotonic())
            self.check_connection()

        self.run_in_background(work, started)
        # From here on, Chrome closing is noticed and put right.
        self._chrome_watch.start(CHROME_WATCH_MS)

    # -- keeping Chrome running --------------------------------------------
    def watch_chrome(self) -> None:
        """Look for the background Chrome, and start it again if it has gone.

        Off the drawing thread: the look is an HTTP request to the debugging
        port, which can take a full second when something else holds it, and a
        restart waits up to thirty for the port to open. Never more than one
        at a time -- a tick that arrives while the last is still looking or
        starting simply does nothing.
        """
        if self._watching:
            return
        self._watching = True

        def look():
            if self._chrome_probe() is not None:
                return "up"
            if not self._chrome_installed():
                return "missing"
            if self._chrome_profile_busy():
                # Still running on the profile, only slow to answer. It used
                # to be taken for gone after one slow second -- on a machine
                # running flat out -- and the launch that followed replaced
                # the app's Chrome outright. A post in progress would go with
                # it.
                return "busy"
            return "down"

        self.run_in_background(look, self._on_chrome_looked, self._on_chrome_look_failed)

    def _on_chrome_looked(self, found: str) -> None:
        if found == "busy":
            self._watching = False
            self._busy_looks += 1
            if self._busy_looks == keepalive.BUSY_LOOKS_BEFORE_TELLING:
                self.note_connection(
                    ConnectionResult(
                        ConnectionState.CHROME_DOWN,
                        "The app's Chrome is open but not answering. If this "
                        "lasts, close it and the app will start it again.",
                    )
                )
            return
        self._busy_looks = 0
        if found == "up":
            self._keeper.seen_running()
            if self.connection_state is ConnectionState.CHROME_DOWN:
                # It came back -- the user started it, or a restart took a
                # moment -- so the pill is stale. One check, on the change,
                # never on the timer.
                self.check_connection()
            now = self._monotonic()
            if (self._last_tab_sweep is None
                    or now - self._last_tab_sweep >= keepalive.TAB_SWEEP_EVERY_S):
                # A port that answers is not a Chrome that works: one stuck
                # tab hangs every connection while the port answers on. Still
                # "watching" until this lands, so only one runs at a time.
                self._last_tab_sweep = now
                self.run_in_background(
                    self._clear_stuck_tabs, self._on_tabs_swept, self._on_tabs_sweep_failed
                )
                return
            self._watching = False
            return
        if found == "missing":
            # Nothing to start. The wizard says to install it.
            self._watching = False
            return
        now = self._monotonic()
        if not self._keeper.may_restart(now):
            self._watching = False
            return

        self.note_connection(
            ConnectionResult(
                ConnectionState.CHROME_DOWN,
                "Chrome was closed. The app is starting it again.",
            )
        )
        self.run_in_background(
            lambda: self._chrome_start(), self._on_chrome_restarted, self._on_chrome_restart_failed
        )

    def _on_chrome_look_failed(self, _exc) -> None:
        self._watching = False

    def _on_tabs_swept(self, closed) -> None:
        self._watching = False
        if not closed:
            return
        self.toast("A tab in the app's Chrome had stopped answering, so the app closed it.")
        if self.connection_state not in (ConnectionState.CONNECTED, ConnectionState.CHECKING):
            # A check hung on that tab finishes by itself; any other result
            # on the pill predates the fix. One check, on the change.
            self.check_connection()

    def _on_tabs_sweep_failed(self, _exc) -> None:
        self._watching = False

    def _on_chrome_restarted(self, _started) -> None:
        self._watching = False
        self._keeper.restarted()
        self.toast("Chrome had closed, so the app started it again.", "info")
        # Chrome is back; whether Facebook still is, only a check can say.
        self.check_connection()

    def _on_chrome_restart_failed(self, _exc) -> None:
        self._watching = False
        now = self._monotonic()
        self._keeper.failed(now)
        if self._keeper.gave_up:
            detail = (
                "Chrome would not start again. Close any Chrome windows the app "
                "opened, then press Start Chrome."
            )
        else:
            detail = (
                "Chrome would not start. The app will try again "
                f"{self._keeper.wait_text(now)}."
            )
        self.note_connection(ConnectionResult(ConnectionState.CHROME_DOWN, detail))
        self.toast(detail, "warning")

    def toggle_worker(self) -> None:
        if self.worker is None:
            return
        if self.worker.paused:
            self.worker.resume()
            self.toast("Posting resumed.", "success")
        else:
            self.worker.pause()
            self.toast("Posting paused. It stays paused until you resume it.", "warning")
        self._refresh_scheduler()

    def _refresh_scheduler(self) -> None:
        """The paused line in the sidebar, the Settings card and Home's status
        line, together."""
        paused = self.worker is not None and self.worker.paused
        if self.paused_notice.isHidden() == paused:
            self.paused_notice.setVisible(paused)
        self.views["settings"].refresh_status()
        self.views["home"].refresh_status()

    def _drain_worker_events(self) -> None:
        while True:
            try:
                event = self.worker_events.get_nowait()
            except queue.Empty:
                break
            self._handle_worker_event(event)
        self._refresh_scheduler()

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
            # One group is gone; the batch carried on without it.
            "unavailable": "warning",
            # Found by its words only, which were in the group before; the
            # batch carried on. The user should look at that group.
            "unconfirmed": "warning",
            # Facebook's reply gave a post no id, so checks fell back to words.
            "no_post_id": "warning",
            # Nothing has gone wrong yet, but it is about to unless the user
            # does something -- which is exactly what a warning is for.
            "power": "warning",
        }.get(event.kind, "info")
        self.toast(event.message, level)
        dashboard = self.views.get("dashboard")
        if dashboard is not None and hasattr(dashboard, "refresh"):
            dashboard.refresh()
        # A schedule that just fired has a new "next run"; the card would go on
        # showing the slot it already used otherwise. A group that turned out
        # to be unavailable may have left a repeating post's groups.
        if event.kind in ("scheduled", "schedule_error", "missed", "unavailable"):
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
        # A closed window has nothing to keep Chrome running for. And a window
        # a test closed must not go on firing this and start a real Chrome.
        self._chrome_watch.stop()
        self._check_deadline.stop()
        self.db.close()
        # Then Chrome itself goes (the user's choice, 2026-10-14): nothing
        # posts while the app is shut, and the next start opens it again.
        # After the watcher has stopped, so nothing starts it straight back,
        # and with the window already hidden, so a Chrome slow to answer
        # cannot leave it sitting on screen. Never fails the close.
        self.hide()
        try:
            self._close_chrome()
        except Exception:
            pass
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
        "again, because the scheduler runs in this window, not in the background."
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
    theme.load_fonts()
    # The style before the stylesheet: the stylesheet wraps whatever style is
    # installed when it is set.
    application.setStyle(AppStyle())
    application.setStyleSheet(theme.stylesheet())
    # Family only. Sizes belong to the stylesheet -- setting a point size here
    # as well meant two sources of truth, and the smaller one was winning.
    application.setFont(QFont(theme.FONT_FAMILY))

    window = App()
    # Only the real app looks inside its Chrome for a stuck tab (tabs.py), and
    # only it may restart one. Every window the suite builds keeps the inert
    # defaults, so no test can reach a Chrome running on the machine.
    window._clear_stuck_tabs = lambda: tabs.clear_stuck_tabs()
    window.group_finder = LiveGroupFinder()
    window._restart_chrome = lambda: tabs.restart_chrome()
    window._close_chrome = lambda: tabs.close_chrome()
    window.always_on = always_on.AlwaysOn()
    session.before_attach = tabs.clear_stuck_tabs
    window.show()
    # Only the real GUI starts the scheduler; constructing an App does not.
    QTimer.singleShot(500, window.start_worker)
    # Same rule, same reason: a window a test builds must not launch a browser.
    QTimer.singleShot(50, window.begin_startup_checks)
    return application.exec()
