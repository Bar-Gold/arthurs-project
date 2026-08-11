"""The main window: sidebar navigation, content area and connection pill.

Nothing here ever raises the window, forces focus, or opens a modal dialog for
status. Feedback goes to the toast area; the window stays exactly where the
user left it.
"""

from __future__ import annotations

import queue

import customtkinter as ctk

from .. import config
from ..automation.groupinfo import LiveGroupNamer
from ..db import Database
from ..db.repo import GroupRepo, SettingsRepo, TaskRepo, TemplateRepo
from ..worker import PostingWorker
from . import theme
from .background import BackgroundRunner
from .connection import ConnectionResult, ConnectionState, check_connection
from .toast import ToastHost
from .views.base import card
from .views.compose import ComposeView
from .views.groups import GroupsView
from .views.queue import QueueView

APP_TITLE = "Facebook Local Auto-Poster"

# How often the main thread drains the worker's event queue. Fast enough that
# the queue view feels live, slow enough to be free.
WORKER_POLL_MS = 700

NAV_ITEMS = (
    ("compose", "Compose", ComposeView),
    ("groups", "Groups", GroupsView),
    ("queue", "Queue", QueueView),
)

# dot colour, short label for the sidebar, toast level
PILL_STATES: dict[ConnectionState, tuple[theme.Color, str, str]] = {
    ConnectionState.UNKNOWN: (theme.NEUTRAL, "Not checked", "info"),
    ConnectionState.CHECKING: (theme.WARNING, "Checking…", "info"),
    ConnectionState.CONNECTED: (theme.SUCCESS, "Connected", "success"),
    ConnectionState.CHROME_DOWN: (theme.NEUTRAL, "Chrome not running", "warning"),
    ConnectionState.LOGGED_OUT: (theme.WARNING, "Logged out", "warning"),
    ConnectionState.CHECKPOINT: (theme.DANGER, "Checkpoint!", "error"),
    ConnectionState.ERROR: (theme.DANGER, "Error", "error"),
}


class App(ctk.CTk):
    def __init__(
        self,
        check_fn=check_connection,
        db: Database | None = None,
        group_namer=None,
    ) -> None:
        super().__init__()
        # Both injectable so tests can drive every pill state without a browser
        # and run against a throwaway database instead of the user's real one.
        self._check_fn = check_fn
        # The one posting worker. Created lazily by start_worker() so tests can
        # build an App without a thread quietly posting to Facebook.
        self.worker: PostingWorker | None = None
        self._worker_after_id: str | None = None
        # Injectable for the same reason as check_fn: the GUI tests must never
        # open a browser to look up a group's name.
        self.group_namer = group_namer if group_namer is not None else LiveGroupNamer()
        self.db = db if db is not None else Database(config.database_path())
        self.group_repo = GroupRepo(self.db)
        self.template_repo = TemplateRepo(self.db)
        self.task_repo = TaskRepo(self.db)
        self.settings_repo = SettingsRepo(self.db)

        self.title(APP_TITLE)
        self.geometry(theme.WINDOW_DEFAULT)
        self.minsize(*theme.WINDOW_MIN)
        self.configure(fg_color=theme.WINDOW_BG)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self.views: dict[str, ctk.CTkFrame] = {}
        self.nav_buttons: dict[str, ctk.CTkButton] = {}
        self.current_view: str | None = None
        self.connection_state = ConnectionState.UNKNOWN

        self._build_sidebar()
        self._build_content()

        self.toast = ToastHost(self.content)
        self.runner = BackgroundRunner(self)

        self.show_view(NAV_ITEMS[0][0])
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout ------------------------------------------------------------
    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(
            self,
            width=theme.SIDEBAR_WIDTH,
            corner_radius=0,
            fg_color=theme.SIDEBAR_BG,
        )
        sidebar.grid(row=0, column=0, sticky="nsw")
        sidebar.grid_propagate(False)
        sidebar.grid_rowconfigure(1, weight=1)

        header = ctk.CTkFrame(sidebar, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=theme.PAD_L, pady=(theme.PAD_XL, theme.PAD_L))

        ctk.CTkLabel(
            header,
            text="Auto-Poster",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_HEADING, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(fill="x")

        nav = ctk.CTkFrame(sidebar, fg_color="transparent")
        nav.grid(row=1, column=0, sticky="new", padx=theme.PAD_S)

        for key, label, _view in NAV_ITEMS:
            button = ctk.CTkButton(
                nav,
                text=label,
                anchor="w",
                height=38,
                corner_radius=theme.RADIUS,
                fg_color="transparent",
                text_color=theme.TEXT,
                hover_color=theme.NAV_ACTIVE_BG,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY),
                command=lambda k=key: self.show_view(k),
            )
            button.pack(fill="x", pady=(0, theme.PAD_XS))
            self.nav_buttons[key] = button

        self._build_worker_row(sidebar)
        self._build_pill(sidebar)

    def _build_worker_row(self, sidebar: ctk.CTkFrame) -> None:
        """Whether the scheduler is running, and a way to stop it.

        The worker starts automatically -- a scheduler you have to remember to
        switch on is one that misses slots -- so its state has to be visible
        from every screen rather than hidden in a menu.
        """
        box = card(sidebar)
        box.grid(row=2, column=0, sticky="ew", padx=theme.PAD_S)

        self.worker_label = ctk.CTkLabel(
            box,
            text="Scheduler: starting…",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=140,
        )
        self.worker_label.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, theme.PAD_XS))

        self.worker_button = ctk.CTkButton(
            box,
            text="Pause",
            height=28,
            corner_radius=theme.RADIUS,
            fg_color="transparent",
            border_width=1,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            hover_color=theme.NAV_ACTIVE_BG,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            command=self.toggle_worker,
        )
        self.worker_button.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

    def _build_pill(self, sidebar: ctk.CTkFrame) -> None:
        """The 'Test Connection' action from README section 4.

        It lives in the sidebar rather than a tab because the answer matters on
        every screen -- nothing else in the app works without it.
        """
        pill = card(sidebar)
        pill.grid(row=3, column=0, sticky="ew", padx=theme.PAD_S, pady=theme.PAD_M)

        row = ctk.CTkFrame(pill, fg_color="transparent")
        row.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, theme.PAD_XS))

        self.pill_dot = ctk.CTkLabel(
            row,
            text="●",
            width=12,
            text_color=theme.NEUTRAL,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY),
        )
        self.pill_dot.pack(side="left")

        self.pill_label = ctk.CTkLabel(
            row,
            text=PILL_STATES[ConnectionState.UNKNOWN][1],
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=130,
        )
        self.pill_label.pack(side="left", padx=(theme.PAD_XS, 0))

        self.check_button = ctk.CTkButton(
            pill,
            text="Check connection",
            height=30,
            corner_radius=theme.RADIUS,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT_ON_ACCENT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            command=self.check_connection,
        )
        self.check_button.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

    def _build_content(self) -> None:
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        for key, _label, view_class in NAV_ITEMS:
            view = view_class(self.content, app=self)
            view.grid(row=0, column=0, sticky="nsew")
            self.views[key] = view

    # -- navigation --------------------------------------------------------
    def show_view(self, key: str) -> None:
        view = self.views[key]
        view.tkraise()
        self.current_view = key
        # Views share state through the database, so each one re-reads on
        # arrival rather than trusting a cache from when it was built.
        view.on_show()

        for nav_key, button in self.nav_buttons.items():
            active = nav_key == key
            button.configure(
                fg_color=theme.NAV_ACTIVE_BG if active else "transparent",
                text_color=theme.ACCENT if active else theme.TEXT,
            )

    # -- connection --------------------------------------------------------
    def check_connection(self) -> None:
        if self.runner.pending:
            return

        self._set_connection(ConnectionState.CHECKING, announce=False)
        self.check_button.configure(state="disabled", text="Checking…")
        self.runner.submit(
            self._check_fn,
            on_success=self._on_check_result,
            on_error=self._on_check_error,
        )

    def _on_check_result(self, result: ConnectionResult) -> None:
        self._reset_check_button()
        self._set_connection(result.state, detail=result.detail)

    def _on_check_error(self, exc: Exception) -> None:
        self._reset_check_button()
        self._set_connection(ConnectionState.ERROR, detail=str(exc))

    def _reset_check_button(self) -> None:
        self.check_button.configure(state="normal", text="Check connection")

    def _set_connection(
        self,
        state: ConnectionState,
        detail: str = "",
        announce: bool = True,
    ) -> None:
        color, label, level = PILL_STATES[state]
        self.connection_state = state
        self.pill_dot.configure(text_color=color)
        self.pill_label.configure(text=label)

        if announce and detail:
            self.toast.show(detail, level)

    # -- the posting worker ------------------------------------------------
    def start_worker(self) -> None:
        """Start the one worker and begin draining its events.

        Started here rather than in __init__ so tests can construct an App
        without a background thread quietly posting to Facebook.
        """
        if self.worker is None:
            self.worker = PostingWorker(self.db)
        self.worker.start()
        self._pump_worker_events()
        self._refresh_worker_row()

    def toggle_worker(self) -> None:
        if self.worker is None:
            return
        if self.worker.paused:
            self.worker.resume()
            self.toast.show("Scheduler resumed.", "info")
        else:
            self.worker.pause()
            self.toast.show("Scheduler paused. Nothing will be posted.", "warning")
        self._refresh_worker_row()

    def _refresh_worker_row(self) -> None:
        label = getattr(self, "worker_label", None)
        if label is None:
            return

        if self.worker is None or not self.worker.running:
            text, colour, button = "Scheduler: off", theme.NEUTRAL, "Start"
        elif self.worker.paused:
            text, colour, button = "Scheduler: paused", theme.WARNING, "Resume"
        elif self.worker.state == "posting":
            text, colour, button = "Scheduler: posting…", theme.ACCENT, "Pause"
        else:
            text, colour, button = "Scheduler: running", theme.SUCCESS, "Pause"

        label.configure(text=text, text_color=colour)
        self.worker_button.configure(text=button)

    def _pump_worker_events(self) -> None:
        """Drain the worker's queue on the main thread.

        Same shape as BackgroundRunner: the worker never touches a widget, it
        only puts events on a queue that this after() loop reads.
        """
        if self.worker is not None:
            while True:
                try:
                    event = self.worker.events.get_nowait()
                except queue.Empty:
                    break
                self._handle_worker_event(event)

        self._refresh_worker_row()
        try:
            self._worker_after_id = self.after(WORKER_POLL_MS, self._pump_worker_events)
        except Exception:
            self._worker_after_id = None  # window is going away

    def _handle_worker_event(self, event) -> None:
        if event.kind in ("halted", "missed", "error"):
            self.toast.show(event.message, "error", duration_ms=15000)
        elif event.kind in ("skipped", "deferred", "recovered"):
            self.toast.show(event.message, "warning", duration_ms=9000)
        elif event.kind in ("posted", "batch_done"):
            self.toast.show(event.message, "success")

        # The queue view is the log of what happened, so keep it current.
        queue_view = self.views.get("queue")
        if queue_view is not None:
            queue_view.refresh()

    # -- lifecycle ---------------------------------------------------------
    def _on_close(self) -> None:
        self.destroy()

    def destroy(self) -> None:
        """Cancel our own pending timers before the widgets go away.

        An after() callback that fires against a destroyed widget makes Tk
        print 'invalid command name' to stderr, which is noise that hides real
        problems.
        """
        runner = getattr(self, "runner", None)
        if runner is not None:
            runner.stop()

        # Stop the worker before the database closes under it. A batch in
        # flight finishes its current group first; its outcome is already
        # committed either way.
        worker = getattr(self, "worker", None)
        if worker is not None:
            worker.stop()

        after_id = getattr(self, "_worker_after_id", None)
        if after_id is not None:
            try:
                self.after_cancel(after_id)
            except Exception:
                pass
            self._worker_after_id = None

        toast = getattr(self, "toast", None)
        if toast is not None:
            toast.clear()

        db = getattr(self, "db", None)
        if db is not None:
            db.close()

        super().destroy()


def run() -> int:
    """Launch the GUI. Returns an exit code."""
    # Set before the root exists so the first paint is already correct.
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")

    app = App()
    # Only the real GUI starts the scheduler; constructing an App does not.
    app.after(500, app.start_worker)
    app.mainloop()
    return 0
