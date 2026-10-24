"""Posting tips: advice for posting safely, in Hebrew, English or Russian.

Read-only, and it decides nothing: the app has no posting rules (the user's
choice, 2026-10-18), and this is where what used to be the rules is said as
advice. The words live in `fbposter.tips`, pure data; this file only draws
them.

The language switch is the page's own, not the app's: the rest of the window
stays in English. The choice is stored (`tips.LANGUAGE_KEY`) so the page opens
in the language last read, Hebrew to begin with. Hebrew turns the whole page
right to left with `setLayoutDirection`, which mirrors the layout: no
direction marks are added to any text, and Qt shapes the Hebrew itself.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fbposter import tips

from .. import theme
from ..widgets import card, clear, segmented

# Wide enough for "10" in the title serif, so every title starts in line.
TIP_NUMBER_WIDTH = 36
# Room either side of a tip's words for the end of a line. Qt lays a wrapped
# line out a few pixels narrower than it paints it, and a label clips what
# falls outside it: tip 2 in Hebrew lost half the "8" of "80" at the end of a
# line (the user's report, 2026-10-22).
SPILL = 6


class TipsView(QWidget):
    title = "Posting tips"

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # The language drawn; None until the first draw.
        self.shown: str | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        # The heading in the language chosen, and the switch at its far end.
        top = QHBoxLayout()
        self.heading = QLabel("")
        self.heading.setObjectName("Title")
        top.addWidget(self.heading, 1)
        toggle, buttons = segmented([name for _code, name in tips.LANGUAGES])
        group = QButtonGroup(self)
        self.language_buttons: dict[str, object] = {}
        for (code, _name), button in zip(tips.LANGUAGES, buttons):
            group.addButton(button)
            button.clicked.connect(lambda _c, c=code: self.choose(c))
            self.language_buttons[code] = button
        top.addWidget(toggle, 0, Qt.AlignVCenter)
        outer.addLayout(top)

        self.intro = QLabel("")
        self.intro.setObjectName("Subtitle")
        self.intro.setWordWrap(True)
        outer.addWidget(self.intro)
        outer.addSpacing(theme.HEADER_GAP)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.tip_box = QVBoxLayout(holder)
        self.tip_box.setContentsMargins(0, 0, 0, 0)
        self.tip_box.setSpacing(theme.PAD_S)
        area.setWidget(holder)
        outer.addWidget(area, 1)

        self.show_language(self.stored_language())

    # -- the language ------------------------------------------------------
    def stored_language(self) -> str:
        code = self.app.settings_repo.get(tips.LANGUAGE_KEY, tips.DEFAULT_LANGUAGE)
        return code if code in tips.PAGES else tips.DEFAULT_LANGUAGE

    def choose(self, code: str) -> None:
        """The switch: draw the page in `code`, and remember it."""
        if code not in tips.PAGES:
            return
        self.app.settings_repo.set(tips.LANGUAGE_KEY, code)
        self.show_language(code)

    def on_show(self) -> None:
        self.show_language(self.stored_language())

    # -- drawing -----------------------------------------------------------
    def show_language(self, code: str) -> None:
        # Restated every time, outside the guard: a click flips the button
        # before this runs, and an unchanged page must still light its own.
        for key, button in self.language_buttons.items():
            button.setChecked(key == code)
        if code == self.shown:
            return
        self.shown = code
        page = tips.page(code)

        self.setLayoutDirection(
            Qt.RightToLeft if code in tips.RIGHT_TO_LEFT else Qt.LeftToRight
        )
        self.heading.setText(page.title)
        self.intro.setText(page.intro)

        clear(self.tip_box)
        for number, tip in enumerate(page.tips, 1):
            self.tip_box.addWidget(self._card(number, tip, code in tips.RIGHT_TO_LEFT))
        sources = QLabel(page.sources)
        sources.setObjectName("Muted")
        sources.setWordWrap(True)
        self.tip_box.addWidget(sources)
        self.tip_box.addStretch(1)

    @staticmethod
    def _card(number: int, tip, right_to_left: bool = False) -> QWidget:
        """A tip: its number in gold at the leading edge (the right, in
        Hebrew), its title and its words beside it. The number had been typed
        into the title, where it read as one more word."""
        box = card()
        line = QHBoxLayout(box)
        # The side the words end on gives up the room they keep at their own
        # edges, and so does the gap after the number, so everything sits
        # where it did. A layout's margins are not mirrored right to left:
        # in Hebrew the words end on the left.
        if right_to_left:
            line.setContentsMargins(theme.PAD_M - SPILL, theme.PAD_S, theme.PAD_L, theme.PAD_M)
        else:
            line.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_L - SPILL, theme.PAD_M)
        line.setSpacing(theme.PAD_M - SPILL)
        figure = QLabel(str(number))
        figure.setObjectName("TipNumber")
        figure.setFixedWidth(TIP_NUMBER_WIDTH)
        figure.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        line.addWidget(figure, 0, Qt.AlignTop)
        words = QVBoxLayout()
        words.setContentsMargins(0, theme.PAD_S, 0, 0)
        words.setSpacing(theme.PAD_XS)
        title = QLabel(tip.title)
        title.setObjectName("SectionHeading")
        title.setWordWrap(True)
        title.setContentsMargins(SPILL, 0, SPILL, 0)
        words.addWidget(title)
        body = QLabel(tip.body)
        body.setWordWrap(True)
        body.setContentsMargins(SPILL, 0, SPILL, 0)
        words.addWidget(body)
        line.addLayout(words, 1)
        return box
