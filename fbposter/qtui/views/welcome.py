"""First-run setup: the one screen that assumes nothing has been done yet.

Not a step in the flow and not in the sidebar. It is where the app opens when
it cannot post yet -- Chrome missing, Chrome not started, nobody logged into
Facebook -- and it is the only screen that offers to fix those things rather
than reporting them.

It also carries out a change of Facebook account. The button and the "are you
sure?" live on the Settings screen, where somebody looks for them; once they
confirm, Settings calls `start_switch()` and brings them here, because what
follows is a login -- and this is the one screen that already owns "put a
Chrome window in front of the user" and puts it back afterwards.

It replaces instructions the app used to give and nobody outside this project
could follow: "Start it with 'main.py launch'" and "Run 'main.py setup' and
sign in again", both of which reached the user through the connection pill.

The decisions live in `fbposter/onboarding.py` and the browser work lives in
`fbposter/login.py`; this file draws the result and nothing more. Everything
slow -- probing the debug port is up to a full second, and checking the
Facebook session is a real page load -- goes through `App.run_in_background`,
because this is the thread painting the window.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fbposter import login, onboarding
from fbposter.onboarding import RowState, SetupStep
from fbposter.connection import ConnectionResult, ConnectionState
from fbposter.db.repo import start_new_account

from .. import icons, theme
from ..widgets import RowList, badge, card, card_title, clear, row
from .groups import IMPORT_PENDING_KEY, REPLACE_PENDING_KEY

# Drawn, not typed: a filled tick for done, a ringed dot for the step you are
# on, an empty ring for one still to come (icons.py). They were the characters
# "✓", "▶" and "○", which Segoe UI draws at three different sizes
# and heights, so the list looked hand-assembled.
MARKS = {
    RowState.DONE: ("done", "SUCCESS"),
    RowState.CURRENT: ("current", "ACCENT_TEXT"),
    RowState.TODO: ("todo", "BORDER_STRONG"),
}
MARK_SIZE = 20
# The screen is a centred column, as Home is (2026-10-20): it is the first
# thing a client sees, and it was a form pinned to the top left.
COLUMN_WIDTH = 560
LOGO_SIZE = 64


class WelcomeView(QWidget):
    title = "Welcome"
    subtitle = (
        "Three things have to be true before this app can post for you. "
        "It will walk you through them."
    )

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.step = SetupStep.CHROME_DOWN
        self._busy = False
        # True once this session has moved the automation Chrome on screen for
        # a login. It is what decides whether there is a window to put back.
        self._opened_login = False
        # True between an account switch starting and the sign-out landing.
        # Until it lands the app still believes it is connected, and the READY
        # branch of refresh() would park the very window the login form is
        # about to appear in.
        self._switching = False
        # What is drawn. The screen redraws on a real state change and not on
        # every visit, the same guard every other view uses.
        self._shown = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # A little above the middle, where the eye settles, as on Home.
        outer.addStretch(3)

        column = row()
        column.setFixedWidth(COLUMN_WIDTH)
        stack = QVBoxLayout(column)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        logo = QLabel()
        logo.setPixmap(icons.brand(LOGO_SIZE))
        logo.setFixedSize(LOGO_SIZE, LOGO_SIZE)
        stack.addWidget(logo, 0, Qt.AlignHCenter)
        stack.addSpacing(theme.PAD_L)

        heading = QLabel(self.title)
        heading.setObjectName("HomeTitle")
        heading.setAlignment(Qt.AlignCenter)
        stack.addWidget(heading)
        stack.addSpacing(theme.PAD_S)
        sub = QLabel(self.subtitle)
        sub.setObjectName("HomeLine")
        sub.setAlignment(Qt.AlignCenter)
        sub.setWordWrap(True)
        stack.addWidget(sub)
        stack.addSpacing(theme.HEADER_GAP + theme.PAD_S)

        # The three steps, numbered, in one box with a hairline between them.
        self.checklist_card = RowList()
        self.checklist_box = self.checklist_card.box
        stack.addWidget(self.checklist_card)
        stack.addSpacing(theme.PAD_M)

        self.step_card = card()
        step_box = QVBoxLayout(self.step_card)
        step_box.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        step_box.setSpacing(theme.PAD_S)

        self.step_headline = card_title("")
        self.step_headline.setWordWrap(True)
        step_box.addWidget(self.step_headline)

        self.step_detail = QLabel()
        self.step_detail.setObjectName("Subtitle")
        self.step_detail.setWordWrap(True)
        step_box.addWidget(self.step_detail)

        buttons = row()
        button_row = QHBoxLayout(buttons)
        button_row.setContentsMargins(0, theme.PAD_S, 0, 0)
        button_row.setSpacing(theme.PAD_S)

        # The one accent button on this screen: whatever the user is being
        # asked to do right now.
        self.action_button = QPushButton()
        self.action_button.setObjectName("Primary")
        self.action_button.clicked.connect(self.do_action)
        button_row.addWidget(self.action_button)

        self.recheck_button = QPushButton("Check again")
        self.recheck_button.clicked.connect(self.recheck)
        button_row.addWidget(self.recheck_button)

        button_row.addStretch(1)
        step_box.addWidget(buttons)

        stack.addWidget(self.step_card)
        # Centred by stretches either side rather than an alignment flag: an
        # aligned item is given its size hint, worked out before the wrapped
        # text in the step card had its width, and the card was cut short
        # through its buttons.
        middle = QHBoxLayout()
        middle.addStretch(1)
        middle.addWidget(column)
        middle.addStretch(1)
        outer.addLayout(middle)
        outer.addStretch(4)

        self.refresh(force=True)

    # -- state -------------------------------------------------------------
    def on_show(self) -> None:
        """Redraw from what is already known. Deliberately does not check.

        Navigating to a screen should not cause network activity: a check is a
        real page load against Facebook, and arriving here twice would run two.
        Startup has its own check (`App.begin_startup_checks`) and the user has
        an explicit button, so the only thing left to do on arrival is draw.
        """
        self.refresh()

    def recheck(self) -> None:
        """Re-run the connection check. Cheap to press, safe to press twice."""
        self.app.check_connection()
        self.refresh()

    def current_step(self) -> SetupStep:
        return onboarding.plan(login.chrome_installed(), self.app.connection_result)

    def refresh(self, force: bool = False) -> None:
        self.step = self.current_step()
        snapshot = (self.step, self._busy)
        if not force and snapshot == self._shown:
            return
        self._shown = snapshot
        self._draw()

        if self.step is SetupStep.READY and self._opened_login:
            # The login worked, so the window it was done in goes back where
            # the rest of the app expects it: off-screen, out of the way. Only
            # after READY -- parking it the moment the button was pressed would
            # take the login form away mid-typing.
            self._opened_login = False
            self.app.run_in_background(login.hide_login_window)

    def _draw(self) -> None:
        clear(self.checklist_box)
        for number, (label, state) in enumerate(onboarding.checklist(self.step), 1):
            mark, colour = MARKS[state]
            holder = row()
            line = QHBoxLayout(holder)
            line.setContentsMargins(theme.PAD_L, theme.PAD_M, theme.PAD_L, theme.PAD_M)
            line.setSpacing(theme.PAD_M)
            icon = QLabel()
            icon.setPixmap(icons.pixmap(mark, theme.C[colour], MARK_SIZE))
            line.addWidget(icon)
            figure = QLabel(str(number))
            figure.setObjectName("StepNumber")
            line.addWidget(figure)
            words = QLabel(label)
            # Drawn by its state (theme.py): the one being asked about in the
            # accent, a step still to come greyed. Not "Muted", which would
            # also drop it to the small size.
            words.setObjectName("StepWords")
            words.setProperty("state", state.value)
            line.addWidget(words, 1)
            if state is RowState.CURRENT:
                line.addWidget(badge("Now", "ACCENT_TEXT"))
            self.checklist_box.addWidget(holder)

        guide = onboarding.guidance(self.step)
        self.step_headline.setText(guide.headline)
        self.step_detail.setText(guide.detail)

        # Both shapes state every button. Working out only what changed is how
        # a stale button gets left on screen offering something the card no
        # longer says.
        if guide.done:
            # Nothing left to fix, so the button stops being a repair and
            # becomes the way into the app.
            self.action_button.setText("Start using the app  →")
            self.action_button.setVisible(True)
            self.recheck_button.setVisible(False)
        else:
            self.action_button.setText(guide.action or "")
            self.action_button.setVisible(guide.action is not None)
            self.recheck_button.setVisible(True)

        busy = self._busy
        for button in (self.action_button, self.recheck_button):
            button.setEnabled(not busy)
        if busy:
            # Whatever is running, it is the action button that says so.
            self.action_button.setText("Working…")
            self.action_button.setVisible(True)

    # -- the one button ----------------------------------------------------
    def do_action(self) -> None:
        if self._busy:
            return
        if self.step is SetupStep.READY:
            self.app.show_view("home")
            return
        if self.step is SetupStep.ERROR:
            self.recheck()
            return
        if self.step is SetupStep.CHROME_MISSING:
            return  # no action offered; the button is hidden

        if self.step is SetupStep.CHROME_DOWN:
            self._run(login.start_chrome)
            return

        self._opened_login = True
        self._run(login.open_login_window)

    # -- changing account --------------------------------------------------
    def start_switch(self) -> bool:
        """Sign the current account out and open the login. Already confirmed.

        Called by the Settings screen, which asks first and refuses while a
        post is going out; this does the part that follows, because it is a
        login and the login lives here. False when something is already
        running here and nothing was started.
        """
        if self._busy:
            return False
        self._switching = True
        self._run(login.switch_account)
        return True

    def _run(self, work) -> None:
        """Do the slow browser part off the drawing thread, then re-check."""
        self._busy = True
        self.refresh(force=True)
        self.app.run_in_background(work, self._on_done, self._on_failed)

    def _on_done(self, _result) -> None:
        self._busy = False
        cleared = ""
        if self._switching:
            self._switching = False
            # Only once the sign-out has worked: a failed one changes nothing.
            # A new account starts with nothing of the old one's but the
            # templates (the user's choices, 2026-10-19): no posts waiting or
            # repeating, no group sets, no history, no draft. With nothing
            # queued, nothing goes out as the new account to the old groups.
            reset = start_new_account(self.app.db)
            self.app.views["create"].clear()
            self.app.views["group_sets"].close_editor()
            cleared = onboarding.switch_cleared(reset.waiting, reset.repeating, reset.sets)
            # The old account is signed out, so the list is replaced with the
            # next account's groups the first time a connection comes back
            # (the user's choice, 2026-10-19). Stored, so it survives a restart
            # before they log in.
            self.app.settings_repo.set(REPLACE_PENDING_KEY, "1")
            self.app.settings_repo.set(IMPORT_PENDING_KEY, "1")
            # The cookies are gone, so what the last check said is now false.
            # Recording it here rather than waiting for the round trip keeps
            # the pill and this screen honest while the login form is already
            # on screen -- and it has to happen before _opened_login is set,
            # because a refresh that still believed we were connected would
            # park the very window the user is about to type into.
            self.app.note_connection(
                ConnectionResult(ConnectionState.LOGGED_OUT, onboarding.SWITCH_DONE)
            )
            self._opened_login = True
        if self.step is SetupStep.LOGGED_OUT:
            self.app.toast(
                f"{cleared}Chrome is open. Log into Facebook there, then press Check again.",
                "info",
            )
        self.recheck()
        self.refresh(force=True)

    def _on_failed(self, exc: Exception) -> None:
        self._busy = False
        # Nothing was signed out, so the screen goes back to what it was
        # showing rather than to the half-switched state.
        self._switching = False
        # Shown as-is. `login.py` raises text written for this person, so
        # dressing it up here would only bury it.
        self.app.toast(str(exc), "error")
        self.refresh(force=True)
