"""First-run setup: the one screen that assumes nothing has been done yet.

Not a step in the flow and not in the sidebar. It is where the app opens when
it cannot post yet -- Chrome missing, Chrome not started, nobody logged into
Facebook -- and it is the only screen that offers to fix those things rather
than reporting them.

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
        button_row.addStretch(1)
        step_box.addWidget(buttons)
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

        if guide.done:
            # Nothing left to fix, so the button stops being a repair and
            # becomes the way into the app.
            self.action_button.setText("Start using the app  →")
            self.action_button.setVisible(True)
            self.recheck_button.setVisible(False)
        else:
            self.action_button.setVisible(guide.action is not None)
            self.action_button.setText(guide.action or "")
            self.recheck_button.setVisible(True)

        busy = self._busy
        self.action_button.setEnabled(not busy)
        self.recheck_button.setEnabled(not busy)
        if busy:
            self.action_button.setText("Working…")

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

    def _run(self, work) -> None:
        """Do the slow browser part off the drawing thread, then re-check."""
        self._busy = True
        self.refresh(force=True)
        self.app.run_in_background(work, self._on_done, self._on_failed)

    def _on_done(self, _result) -> None:
        self._busy = False
        if self.step is SetupStep.LOGGED_OUT:
            self.app.toast(
                "Chrome is open — log into Facebook there, then press Check again.",
                "info",
            )
        self.recheck()
        self.refresh(force=True)

    def _on_failed(self, exc: Exception) -> None:
        self._busy = False
        # Shown as-is. `login.py` raises text written for this person, so
        # dressing it up here would only bury it.
        self.app.toast(str(exc), "error")
        self.refresh(force=True)
