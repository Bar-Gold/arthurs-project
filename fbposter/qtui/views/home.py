"""Home: the page the app opens on, and where a finished post lands.

The user's request (2026-10-19): no numbers and no lists, just a calm page
with one clear thing to do. The Dashboard already answers "how is it going";
this answers "what now", and the answer is always the same button.

Its one accent-filled button is that call to action, as the visual rules ask
of every screen. It is reached from the first sidebar item and from the logo
at the top of the sidebar.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from .. import icons, theme

MARK_SIZE = 88
# Wide enough for the line under the headline to sit on one or two lines,
# never a ragged three.
COLUMN_WIDTH = 460

HEADLINE = "Ready for the next post?"
LINE = (
    "Write it, choose the groups, then choose when. The app posts it to one "
    "group at a time."
)
CTA = "Create a post"


class HomeView(QWidget):
    title = "Home"

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # A little above the middle, where the eye settles on an empty page.
        outer.addStretch(4)

        column = QWidget()
        column.setObjectName("Row")
        column.setFixedWidth(COLUMN_WIDTH)
        stack = QVBoxLayout(column)
        stack.setContentsMargins(0, 0, 0, 0)
        stack.setSpacing(0)

        # A fixed size: left to the layout, the label was squeezed and the
        # tile drawn with its top and bottom cut off.
        mark = QLabel()
        mark.setPixmap(icons.brand(MARK_SIZE))
        mark.setFixedSize(MARK_SIZE, MARK_SIZE)
        stack.addWidget(mark, 0, Qt.AlignHCenter)
        stack.addSpacing(theme.PAD_XL)

        self.headline = QLabel(HEADLINE)
        self.headline.setObjectName("HomeTitle")
        self.headline.setAlignment(Qt.AlignCenter)
        stack.addWidget(self.headline)
        stack.addSpacing(theme.PAD_S)

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

        outer.addWidget(column, 0, Qt.AlignHCenter)
        outer.addStretch(5)

    def create_post(self) -> None:
        """Step 1 of Create a post."""
        self.app.show_view("compose")
