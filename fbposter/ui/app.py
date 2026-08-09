"""The main window: sidebar navigation, content area and connection pill.

Nothing here ever raises the window, forces focus, or opens a modal dialog for
status. Feedback goes to the toast area; the window stays exactly where the
user left it.
"""

from __future__ import annotations

import customtkinter as ctk

from .. import config
from ..db import Database
from ..db.repo import GroupRepo, SettingsRepo, TaskRepo, TemplateRepo
from . import theme
from .background import BackgroundRunner
from .connection import ConnectionResult, ConnectionState, check_connection
from .toast import ToastHost
from .views.base import card
from .views.compose import ComposeView
from .views.groups import GroupsView
from .views.queue import QueueView

APP_TITLE = "Facebook Local Auto-Poster"

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
    def __init__(self, check_fn=check_connection, db: Database | None = None) -> None:
        super().__init__()
        # Both injectable so tests can drive every pill state without a browser
        # and run against a throwaway database instead of the user's real one.
        self._check_fn = check_fn
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

        self._build_pill(sidebar)

    def _build_pill(self, sidebar: ctk.CTkFrame) -> None:
        """The 'Test Connection' action from README section 4.

        It lives in the sidebar rather than a tab because the answer matters on
        every screen -- nothing else in the app works without it.
        """
        pill = card(sidebar)
        pill.grid(row=2, column=0, sticky="ew", padx=theme.PAD_S, pady=theme.PAD_M)

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
    app.mainloop()
    return 0
