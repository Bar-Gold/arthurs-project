"""Create a post: write it, choose the groups, then choose when.

One sidebar item in place of three (the user's request, 2026-10-19). The three
screens are the same ComposeView, GroupsView and PublishView as before, held
here as the pages of one road, in order. Each is still registered in
`App.views` under its own key, so `show_view("groups")` and every other caller
keeps working: the window routes a step's key to this view.

The step bar at the top shows where you are. An earlier step can be clicked to
go back; a later one cannot, so the only way forward is each step's own Next
button, and the order is always kept (the user's choice). The steps' own big
titles are hidden here, under the one title, and their one-line instructions
stay.

A post that went out, or was queued, starts a fresh one: `start_over()` clears
the draft and lands on Home (the user's choice). A refused post keeps it all.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import icons, theme
from ..widgets import row
from .compose import ComposeView
from .groups import GroupsView
from .publish import PublishView

# The road, in order: each step's key in `App.views`, and its name on the bar.
STEPS = (
    ("compose", "Write the post"),
    ("groups", "Choose the groups"),
    ("publish", "Choose when"),
)
STEP_KEYS = tuple(key for key, _words in STEPS)

# The same drawn marks as the setup checklist (welcome.MARKS): done, here, and
# still to come.
DONE, CURRENT, TODO = "done", "current", "todo"
MARK_COLOURS = {DONE: "SUCCESS", CURRENT: "ACCENT_TEXT", TODO: "BORDER_STRONG"}
MARK_SIZE = 18
# The short rule between two steps.
JOIN_WIDTH = 28
# The widest the road is drawn (2026-10-24). On a big monitor it stretched
# edge to edge, a group's name a long way from its drop-down; past this it
# stays this wide, centred. A laptop window is narrower, so nothing changes
# there.
MAX_ROAD_WIDTH = 1200


class CreatePostView(QWidget):
    title = "Create a post"

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.index = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_S)

        heading = QLabel(self.title)
        heading.setObjectName("Title")
        outer.addWidget(heading)
        # A little more room round the step bar (2026-10-24): title, bar and
        # the step's instruction were packed together.
        outer.addSpacing(theme.PAD_XS)

        bar = row()
        line = QHBoxLayout(bar)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.PAD_S)
        self.step_buttons: dict[str, QPushButton] = {}
        for number, (key, words) in enumerate(STEPS, 1):
            if number > 1:
                join = QFrame()
                join.setObjectName("Divider")
                join.setFixedSize(JOIN_WIDTH, 1)
                line.addWidget(join, 0, Qt.AlignVCenter)
            button = QPushButton(f"{number}. {words}")
            button.setObjectName("Step")
            button.setCheckable(True)
            button.setIconSize(QSize(MARK_SIZE, MARK_SIZE))
            button.clicked.connect(lambda _c, k=key: self.app.show_view(k))
            line.addWidget(button)
            self.step_buttons[key] = button
        line.addStretch(1)
        outer.addWidget(bar)
        outer.addSpacing(theme.PAD_S)

        # Compose first: PublishView reads it while it builds.
        self.pages = QStackedWidget()
        self.compose = ComposeView(app)
        app.views["compose"] = self.compose
        self.groups = GroupsView(app)
        app.views["groups"] = self.groups
        self.publish = PublishView(app)
        app.views["publish"] = self.publish
        for page in (self.compose, self.groups, self.publish):
            # One title for the whole road; each step keeps its instruction.
            page.heading.hide()
            self.pages.addWidget(page)
        outer.addWidget(self.pages, 1)

        self._restate()

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        side = max(0, (self.width() - MAX_ROAD_WIDTH) // 2)
        self.layout().setContentsMargins(side, 0, side, 0)

    @property
    def current_key(self) -> str:
        return STEP_KEYS[self.index]

    def go_to(self, key: str) -> None:
        """Show step `key`. Called by the window, which owns navigation."""
        self.index = STEP_KEYS.index(key)
        self.pages.setCurrentIndex(self.index)
        self._restate()
        on_show = getattr(self.pages.currentWidget(), "on_show", None)
        if on_show is not None:
            on_show()

    def _restate(self) -> None:
        """Mark each step done, here or to come, and let only the earlier ones
        be clicked. Restated every time: a click flips the button it lands on
        before anything here runs."""
        for position, key in enumerate(STEP_KEYS):
            state = DONE if position < self.index else CURRENT if position == self.index else TODO
            button = self.step_buttons[key]
            button.setChecked(position == self.index)
            button.setEnabled(position <= self.index)
            button.setIcon(QIcon(icons.pixmap(state, theme.C[MARK_COLOURS[state]], MARK_SIZE)))

    def start_over(self) -> None:
        """A post went out or was queued: clear it all and go Home."""
        self.clear()
        self.app.show_view("home")

    def clear(self) -> None:
        """Empty the post being written and go back to step 1, staying put.

        Also what an account switch does (the user's choice, 2026-10-19): the
        new account starts with no draft of the old one's."""
        self.compose.clear_draft()
        self.app.selected_groups.clear()
        self.app.selected_sets.clear()
        self.publish.reset_form()
        self.index = 0
        self.pages.setCurrentIndex(0)
        self._restate()
