"""First-run setup: the one screen that assumes nothing has been done yet.

Not a step in the flow and not in the sidebar. It is where the app opens when
it cannot post yet -- Chrome missing, Chrome not started, nobody logged into
Facebook -- and it is the only screen that offers to fix those things rather
than reporting them.

It is also the only way to change which Facebook account the app posts as.
That is not a setup step -- it is something you do once setup is finished --
but it belongs on the one screen that already owns the login, so there is a
single implementation of "put a Chrome window in front of the user". The pill's
button is what leads here once the wizard has been retired.

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

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from fbposter import login, onboarding
from fbposter.onboarding import RowState, SetupStep
from fbposter.ui.connection import ConnectionResult, ConnectionState

from .. import theme
from ..widgets import card, clear, row

# Drawn rather than spelled out: a tick, the arrow for what you are being asked
# to do now, and a dot for what comes after.
MARKS = {
    RowState.DONE: ("✓", "SUCCESS"),
    RowState.CURRENT: ("▶", "ACCENT_TEXT"),
    RowState.TODO: ("○", "TEXT_MUTED"),
}


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
        # True while the user is being asked to confirm an account switch, and
        # True between pressing confirm and the sign-out landing. The second
        # one exists because until it lands the app still believes it is
        # connected, and the READY branch of refresh() would park the very
        # window the login form is about to appear in.
        self._confirming = False
        self._switching = False
        # What is drawn. The screen redraws on a real state change and not on
        # every visit, the same guard every other view uses.
        self._shown = None

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
        outer.addSpacing(theme.PAD_M)

        self.checklist_card = card()
        self.checklist_box = QVBoxLayout(self.checklist_card)
        self.checklist_box.setContentsMargins(
            theme.PAD_L, theme.PAD_M, theme.PAD_L, theme.PAD_M
        )
        self.checklist_box.setSpacing(theme.PAD_S)
        outer.addWidget(self.checklist_card)
        outer.addSpacing(theme.PAD_M)

        self.step_card = card()
        step_box = QVBoxLayout(self.step_card)
        step_box.setContentsMargins(theme.PAD_L, theme.PAD_L, theme.PAD_L, theme.PAD_L)
        step_box.setSpacing(theme.PAD_S)

        self.step_headline = QLabel()
        self.step_headline.setObjectName("SectionHeading")
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

        # Plain, never #Primary. It is an escape hatch offered on the one
        # screen where nothing is wrong, and filled it would outrank the button
        # that actually takes the user into the app.
        self.switch_button = QPushButton(onboarding.SWITCH_ACTION)
        self.switch_button.clicked.connect(self.begin_switch)
        button_row.addWidget(self.switch_button)

        self.confirm_button = QPushButton(onboarding.SWITCH_CONFIRM)
        self.confirm_button.clicked.connect(self.confirm_switch)
        button_row.addWidget(self.confirm_button)

        self.cancel_button = QPushButton(onboarding.SWITCH_CANCEL)
        self.cancel_button.clicked.connect(self.cancel_switch)
        button_row.addWidget(self.cancel_button)

        button_row.addStretch(1)
        step_box.addWidget(buttons)

        # Under the buttons, because it explains one of them rather than the
        # card. Hidden entirely until the card is the "all set" one.
        self.switch_note = QLabel()
        self.switch_note.setObjectName("Muted")
        self.switch_note.setWordWrap(True)
        step_box.addWidget(self.switch_note)

        outer.addWidget(self.step_card)

        # Slack below everything, never above: anchoring the card to the bottom
        # of the window puts a void between what you read and what you press.
        outer.addStretch(1)

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
        if self.step is not SetupStep.READY and self._confirming:
            # The question stops making sense the moment the answer changes --
            # there is nothing left to sign out of. Settled before the snapshot
            # is taken, so what is recorded as drawn is what was drawn.
            self._confirming = False

        snapshot = (self.step, self._busy, self._confirming)
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
        for label, state in onboarding.checklist(self.step):
            mark, colour = MARKS[state]
            line = QLabel(f"{mark}   {label}")
            # Colour only. The "Muted" object name would have done the greying
            # for free, but it also drops the font to SIZE_SMALL, which would
            # have made the three rows of one list different sizes.
            line.setStyleSheet(f"color: {theme.C[colour]};")
            self.checklist_box.addWidget(line)

        guide = onboarding.guidance(self.step)
        self.step_headline.setText(guide.headline)
        self.step_detail.setText(guide.detail)

        # Three shapes, and every button is stated in each of them. Working out
        # only what changed is how a stale button gets left on screen offering
        # something the card no longer says.
        confirming = guide.done and self._confirming
        if confirming:
            # The question replaces the card rather than opening a dialog over
            # it: "no modal dialogs" is the rule and this earns no exception.
            # It reads better here anyway, where the warning has room to be a
            # sentence rather than a line in a box.
            self.step_headline.setText(onboarding.SWITCH_HEADLINE)
            self.step_detail.setText(onboarding.SWITCH_WARNING)
            self.action_button.setVisible(False)
            self.recheck_button.setVisible(False)
            self.switch_button.setVisible(False)
            self.confirm_button.setVisible(True)
            self.cancel_button.setVisible(True)
            self.switch_note.setVisible(False)
        elif guide.done:
            # Nothing left to fix, so the button stops being a repair and
            # becomes the way into the app -- and the one thing a finished
            # setup might still want changing gets offered beside it.
            self.action_button.setText("Start using the app  →")
            self.action_button.setVisible(True)
            self.recheck_button.setVisible(False)
            self.switch_button.setVisible(True)
            self.confirm_button.setVisible(False)
            self.cancel_button.setVisible(False)
            self.switch_note.setText(onboarding.SWITCH_DETAIL)
            self.switch_note.setVisible(True)
        else:
            self.action_button.setText(guide.action or "")
            self.action_button.setVisible(guide.action is not None)
            self.recheck_button.setVisible(True)
            self.switch_button.setVisible(False)
            self.confirm_button.setVisible(False)
            self.cancel_button.setVisible(False)
            self.switch_note.setVisible(False)

        busy = self._busy
        for button in (
            self.action_button, self.recheck_button, self.switch_button,
            self.confirm_button, self.cancel_button,
        ):
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
            self.app.show_view("compose")
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
    def begin_switch(self) -> None:
        """Ask before signing anybody out. It is not a stray-click action.

        The button sits beside the connection light on every screen, so the
        second press is what separates "I want to change account" from "I was
        aiming for Check connection".
        """
        if self._busy:
            return
        worker = self.app.worker
        if worker is not None and worker.state == "posting":
            # Dropping the cookies with a post half-typed into the composer
            # fails that post, and the batch then halts on a verification that
            # never could have succeeded. It is a wait, not a refusal.
            self.app.toast(onboarding.SWITCH_BUSY, "warning")
            return
        self._confirming = True
        self.refresh(force=True)

    def cancel_switch(self) -> None:
        self._confirming = False
        self.refresh(force=True)

    def confirm_switch(self) -> None:
        if self._busy:
            return
        self._confirming = False
        self._switching = True
        self._run(login.switch_account)

    def _run(self, work) -> None:
        """Do the slow browser part off the drawing thread, then re-check."""
        self._busy = True
        self.refresh(force=True)
        self.app.run_in_background(work, self._on_done, self._on_failed)

    def _on_done(self, _result) -> None:
        self._busy = False
        if self._switching:
            self._switching = False
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
                "Chrome is open — log into Facebook there, then press Check again.",
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
