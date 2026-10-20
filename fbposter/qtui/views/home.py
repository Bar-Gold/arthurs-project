"""Home: the page the app opens on, and where a finished post lands.

The user's request (2026-10-19): no numbers and no lists, just a calm page
with one clear thing to do. The Dashboard already answers "how is it going";
this answers "what now", and the answer is always the same button.

Polished at the user's request (2026-10-20), each piece their choice: a
greeting between two gold hairlines where a copy of the sidebar's logo used to
sit, a soft light behind the middle of the page, and one quiet line under the
button saying what the posting is doing, in words, never a number.

Its one accent-filled button is that call to action, as the visual rules ask
of every screen. It is reached from the first sidebar item and from the logo
at the top of the sidebar.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QRadialGradient
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ... import clock
from ...db.models import SCHEDULE_ACTIVE
from .. import icons, theme
from ..widgets import row

# Wide enough for each sentence of the line under the headline to sit on a
# line of its own, never a ragged third.
COLUMN_WIDTH = 460
RULE_WIDTH = 28
DOT_SIZE = 12

HEADLINE = "Ready for the next post?"
# Two sentences, one to a line.
LINE = "Write it, choose the groups, then choose when.<br>Then sit back, the app does the rest."
CTA = "Create a post"

# What the posting is doing, quietest last. Each is (words, colour token).
PAUSED = ("Posting is paused", "WARNING")
# The Dashboard's colour for "In progress", so the two screens agree.
GOING_OUT = ("A post is going out now", "ACCENT_TEXT")
WAITING_ONE = ("A post is waiting to go out", "TEXT_MUTED")
WAITING_MANY = ("Posts are waiting to go out", "TEXT_MUTED")
REPEATING_ONE = ("Your repeating post is on", "TEXT_MUTED")
REPEATING_MANY = ("Your repeating posts are on", "TEXT_MUTED")
NOTHING = ("Nothing waiting to go out", "BORDER_STRONG")


def greeting(moment: datetime) -> str:
    """Good morning, afternoon or evening, by the hour in Israel."""
    hour = clock.local_hour(moment)
    if 5 <= hour < 12:
        return "Good morning"
    if 12 <= hour < 18:
        return "Good afternoon"
    return "Good evening"


def status(paused: bool, going_out: int, waiting: int, repeating: int) -> tuple[str, str]:
    """The one line under the button. A pause outranks everything, because
    while it holds nothing else on the list is true."""
    if paused:
        return PAUSED
    if going_out:
        return GOING_OUT
    if waiting:
        return WAITING_ONE if waiting == 1 else WAITING_MANY
    if repeating:
        return REPEATING_ONE if repeating == 1 else REPEATING_MANY
    return NOTHING


class HomeView(QWidget):
    title = "Home"

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # What the status line shows: (words, colour), so a tick that changes
        # neither costs nothing.
        self._status_shown: tuple[str, str] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # A little above the middle, where the eye settles on an empty page.
        outer.addStretch(4)

        self.column = row()
        self.column.setFixedWidth(COLUMN_WIDTH)
        stack = QVBoxLayout(self.column)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        greeting_row = row()
        line = QHBoxLayout(greeting_row)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.PAD_M)
        self.greeting = QLabel()
        self.greeting.setObjectName("HomeGreeting")
        line.addStretch(1)
        line.addWidget(self._rule(), 0, Qt.AlignVCenter)
        line.addWidget(self.greeting)
        line.addWidget(self._rule(), 0, Qt.AlignVCenter)
        line.addStretch(1)
        stack.addWidget(greeting_row)
        stack.addSpacing(theme.PAD_L)

        self.headline = QLabel(HEADLINE)
        self.headline.setObjectName("HomeTitle")
        self.headline.setAlignment(Qt.AlignCenter)
        stack.addWidget(self.headline)
        stack.addSpacing(theme.PAD_M)

        self.line = QLabel(LINE)
        self.line.setObjectName("HomeLine")
        self.line.setAlignment(Qt.AlignCenter)
        self.line.setWordWrap(True)
        stack.addWidget(self.line)
        stack.addSpacing(theme.PAD_XL + theme.PAD_S)

        self.create_button = QPushButton(CTA)
        self.create_button.setObjectName("Primary")
        self.create_button.setProperty("hero", True)
        self.create_button.setCursor(Qt.PointingHandCursor)
        self.create_button.clicked.connect(self.create_post)
        stack.addWidget(self.create_button, 0, Qt.AlignHCenter)
        stack.addSpacing(theme.PAD_XL)

        status_row = row()
        status_line = QHBoxLayout(status_row)
        status_line.setContentsMargins(0, 0, 0, 0)
        status_line.setSpacing(theme.PAD_S)
        self.status_dot = QLabel()
        self.status_label = QLabel()
        self.status_label.setObjectName("HomeStatus")
        status_line.addStretch(1)
        status_line.addWidget(self.status_dot, 0, Qt.AlignVCenter)
        status_line.addWidget(self.status_label, 0, Qt.AlignVCenter)
        status_line.addStretch(1)
        stack.addWidget(status_row)

        outer.addWidget(self.column, 0, Qt.AlignHCenter)
        outer.addStretch(5)

        self.greeting.setText(greeting(datetime.now(timezone.utc)).upper())
        self._show_status(NOTHING)

    @staticmethod
    def _rule() -> QFrame:
        rule = QFrame()
        rule.setObjectName("HomeRule")
        rule.setFixedSize(RULE_WIDTH, 1)
        return rule

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        """The page colour, lit a little behind the column. It grows with the
        window, so a large one is not a small block on empty paper.

        HOME_GLOW is WINDOW_BG raised by the same step in red, green and blue,
        so the gradient's 8-bit steps fall at the same place in all three. Lifted
        unevenly, each channel stepped at its own radius and the light showed
        faint coloured rings.
        """
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(theme.C["WINDOW_BG"]))
        centre = QPointF(self.column.geometry().center())
        # An oval that fades out exactly at the nearest edges. A circle sized
        # to the page was still lit at the top, and this page sits inside the
        # window's margin, which is plain WINDOW_BG: the two met in a seam.
        reach_x = min(centre.x(), self.width() - centre.x())
        reach_y = min(centre.y(), self.height() - centre.y())
        if reach_x > 0 and reach_y > 0:
            painter.translate(centre)
            painter.scale(1.0, reach_y / reach_x)
            light = QRadialGradient(QPointF(0, 0), reach_x)
            light.setColorAt(0.0, QColor(theme.C["HOME_GLOW"]))
            light.setColorAt(1.0, QColor(theme.C["WINDOW_BG"]))
            painter.fillRect(QRectF(-reach_x, -reach_x, 2 * reach_x, 2 * reach_x), light)
        painter.end()

    def on_show(self) -> None:
        # The hour moves on while the app stays open.
        self.greeting.setText(greeting(datetime.now(timezone.utc)).upper())
        self.refresh_status()

    def refresh_status(self) -> None:
        """Say what the posting is doing. Runs on every worker tick, but asks
        the database only while Home is the screen in front of the user."""
        if self.app.current_view != "home":
            return
        worker = self.app.worker
        going_out = self.app.task_repo.active_batches()
        waiting = self.app.task_repo.unfinished_count() - going_out
        repeating = sum(1 for s in self.app.schedule_repo.list() if s.state == SCHEDULE_ACTIVE)
        self._show_status(status(
            worker is not None and worker.paused, going_out, waiting, repeating
        ))

    def _show_status(self, shown: tuple[str, str]) -> None:
        if shown == self._status_shown:
            return
        self._status_shown = shown
        words, colour = shown
        self.status_label.setText(words)
        self.status_dot.setPixmap(icons.pixmap("dot", theme.C[colour], DOT_SIZE))

    def create_post(self) -> None:
        """Step 1 of Create a post."""
        self.app.show_view("compose")
