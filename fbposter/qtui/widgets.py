"""Shared widget helpers.

Separate from app.py so the views can use them without importing the window
that imports the views.
"""

from __future__ import annotations

from html import escape
from typing import Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QProxyStyle,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme

# How long a message stays. Reading takes time in proportion to length, and
# many of these are two or three sentences that a fixed 4.5 seconds cut off
# half-read.
MESSAGE_MIN_MS = 4_500
MESSAGE_MS_PER_CHAR = 60  # about 16 characters a second, unhurried
MESSAGE_MAX_MS = 15_000
# A message offering Undo stays at least this long, so there is time to reach
# the button.
UNDO_MIN_MS = 10_000


# Letter spacing for large type, in px, by object name. Large text reads too
# loose at the spacing that suits body text, so headings are drawn a touch
# tighter -- about -0.02em, the way system fonts track their display sizes.
# A stylesheet cannot say this (Qt has no letter-spacing property), so
# AppStyle sets it as each label is polished.
TRACKING = {"Title": -0.5, "Brand": -0.3, "CardTitle": -0.2}


def literal(text: str) -> str:
    """Text for a button or checkbox, with every "&" shown as itself.

    Qt reads "&" in a button's label as "underline the next letter for a
    keyboard shortcut" and draws no "&" at all, so a group called "buy & sell"
    showed as "buy  sell". "&&" is how Qt spells one "&". Anything the user or
    Facebook named (a group, a set) goes through this.
    """
    return text.replace("&", "&&")


def set_note_lines(
    label: QLabel, facts: Sequence[str], warnings: Sequence[str] = ()
) -> None:
    """Facts in the label's own colour, warnings in the warning colour.

    A warning set in the same grey as the facts around it read as one more
    fact -- "the same text reaches 3 groups" looked exactly like "First run
    09:00". One block per line, so each keeps its own text direction, as the
    lines of plain text did.
    """
    label.setTextFormat(Qt.RichText)
    colour = theme.C["WARNING"]
    blocks = [f"<div>{escape(line, quote=False)}</div>" for line in facts]
    blocks += [
        f'<div style="color: {colour};">{escape(line, quote=False)}</div>'
        for line in warnings
    ]
    label.setText("".join(blocks))


def message_duration(text: str, level: str, can_undo: bool = False) -> int | None:
    """How long a message stays, in ms. None: until it is closed or replaced.

    An error stays: it is the one a person must not miss, and the batch that
    stopped at 3am is not waiting for anyone to be looking.
    """
    if level == "error":
        return None
    ms = min(MESSAGE_MAX_MS, max(MESSAGE_MIN_MS, len(text) * MESSAGE_MS_PER_CHAR))
    if can_undo:
        ms = max(ms, UNDO_MIN_MS)
    return ms


class AppStyle(QProxyStyle):
    """What a stylesheet cannot say about a control, said once for all of them.

    Qt polishes every widget through the style as it first appears, including
    the rows a screen rebuilds later, so this reaches every button without a
    line in any view.

    * **A hand cursor on everything clickable.** Only the sidebar had one, so
      the pointer stayed an arrow over every other button in the window.
    * **A click does not give a button keyboard focus; Tab still does.** Focus
      has to be visible, so the stylesheet rings it -- and with click focus the
      ring then sat on whatever was clicked last, and a clicked group's name
      turned bold blue. Tab-only focus is the desktop version of the web's
      `:focus-visible`: the ring means "the keyboard is here", nothing else.
      Values typed into a field are not stranded by it: the fields read them
      with `interpretText()` rather than relying on losing focus to a button.
    """

    def polish(self, target):  # noqa: D401 - Qt's name, several overloads
        if isinstance(target, (QAbstractButton, QComboBox)):
            target.setCursor(Qt.PointingHandCursor)
        if isinstance(target, QAbstractButton):
            target.setFocusPolicy(Qt.TabFocus)
        if isinstance(target, QLabel) and target.objectName() in TRACKING:
            font = target.font()
            font.setLetterSpacing(QFont.AbsoluteSpacing, TRACKING[target.objectName()])
            target.setFont(font)
        return super().polish(target)


def clear(layout: QLayout) -> None:
    """Empty a layout, properly.

    **`takeAt()` does not unparent a widget.** Taking one out of a layout only
    stops the layout managing its geometry; it stays a child of the same parent
    and goes on painting until the event loop turns and `deleteLater()` fires.
    Worse, an unmanaged widget reverts to Qt's default 640x480, so it paints at
    a size it never had on screen.

    That is not theoretical. The Compose wording tabs cleared themselves this
    way, and the stale "All groups" tab -- checked, therefore filled with the
    accent colour -- sat behind the strip as a 640x480 blue rectangle, clipped
    by the scroll area into what looked like a deliberate blue band across the
    screen. The same bug produced the "duplicate row" and "stale collage"
    scares. `setParent(None)` is what actually removes it.

    Every layout in this package is cleared through here so the fix cannot be
    learned once and forgotten in the next view.
    """
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
            continue
        child = item.layout()
        if child is not None:
            clear(child)


def card(parent: QWidget | None = None) -> QFrame:
    """A surface panel. The one place the card look is defined."""
    frame = QFrame(parent)
    frame.setObjectName("Card")
    return frame


class RowList(QFrame):
    """A list in one box, with a hairline between its rows.

    Every list of groups is drawn this way (the user's choice, 2026-10-20):
    Groups, the Group sets editor, the repeating-post editor and "Going out"
    used to be four different looks, two of them a separate box per row.

    The hairlines are painted here, between whichever rows are showing, rather
    than being widgets of their own: a search hides rows, and a line that
    belonged to a hidden row would otherwise be left on screen, or sit along
    the top of the box above the first row still shown.

    `framed=False` draws the lines only, for a list that already sits inside a
    card. `inset` keeps them clear of the box's edges, in line with the text.
    """

    def __init__(self, framed: bool = True, inset: int = theme.PAD_M,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("List" if framed else "Row")
        self.inset = inset
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(0)

    def add(self, widget: QWidget) -> QWidget:
        self.box.addWidget(widget)
        return widget

    def rows(self) -> list[QWidget]:
        """Every row, shown or not, in order."""
        found = (self.box.itemAt(index).widget() for index in range(self.box.count()))
        return [widget for widget in found if widget is not None]

    def shown_rows(self) -> list[QWidget]:
        return [widget for widget in self.rows() if not widget.isHidden()]

    def lines(self) -> int:
        """How many hairlines are drawn: one between each two rows shown."""
        return max(0, len(self.shown_rows()) - 1)

    def clear(self) -> None:
        clear(self.box)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().paintEvent(event)
        shown = self.shown_rows()
        if len(shown) < 2:
            return
        painter = QPainter(self)
        painter.setPen(QPen(QColor(theme.C["BORDER"]), 1))
        for below in shown[1:]:
            top = below.geometry().top()
            painter.drawLine(below.geometry().left() + self.inset, top,
                             below.geometry().right() - self.inset, top)
        painter.end()


def back_link(text: str) -> QPushButton:
    """"Back" in the flow: words with no box (the user's choice, 2026-10-20),
    so the filled button for the next step stands alone."""
    button = QPushButton(text)
    button.setObjectName("BackLink")
    return button


def card_title(text: str) -> QLabel:
    """The name of a card, one step above what is in it. One size for every
    card in the window: Templates and When were body size, Settings' titles a
    step up, so the same kind of heading came in two sizes."""
    label = QLabel(text)
    label.setObjectName("CardTitle")
    return label


def badge(text: str, tone: str) -> QLabel:
    """A status in a tinted capsule. `tone` is a colour token: SUCCESS,
    WARNING, DANGER, ACCENT_TEXT or TEXT_MUTED (theme.BADGE_TINTS).

    Its own size, never stretched: in a row it would otherwise grow to the
    row's height, and a capsule as tall as the buttons beside it reads as one
    of them.
    """
    label = QLabel(text)
    label.setObjectName("Badge")
    label.setProperty("tone", tone if tone in theme.BADGE_TINTS else "TEXT_MUTED")
    label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return label


class Switch(QCheckBox):
    """An on/off switch, drawn the way Windows 11 draws one.

    For a setting that takes effect the moment it is flipped, which is what a
    switch promises and a checkbox does not. Off is an outlined track with a
    small grey knob, on is the accent with a white knob, so the two states
    differ in shape as well as colour. It is a QCheckBox underneath, so
    `setChecked`, `isChecked`, `clicked` and the space bar all behave as one.
    It has no text of its own: the setting's name is the label beside it, and
    `name` is what a screen reader says.
    """

    WIDTH = 44
    HEIGHT = 24
    # Room around the track for the focus ring.
    RING = 2

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(name)
        self.setFixedSize(self.sizeHint())

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's name
        return QSize(self.WIDTH + 2 * self.RING, self.HEIGHT + 2 * self.RING)

    def hitButton(self, pos) -> bool:  # noqa: N802 - Qt's name
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt's name
        c = theme.C
        on = self.isChecked()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            painter.setOpacity(0.45)
        track = QRectF(self.RING, self.RING, self.WIDTH, self.HEIGHT)
        radius = self.HEIGHT / 2
        if on:
            fill = c["ACCENT_PRESSED" if self.isDown() else "ACCENT"]
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(fill))
            painter.drawRoundedRect(track, radius, radius)
            knob, colour = 16, c["TEXT_ON_ACCENT"]
            x = track.right() - 4 - knob
        else:
            painter.setPen(QPen(QColor(c["TEXT_MUTED"]), 1))
            painter.setBrush(QColor(c["PRESSED_BG" if self.isDown() else "SURFACE"]))
            painter.drawRoundedRect(track.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
            knob, colour = 12, c["TEXT_MUTED"]
            x = track.left() + 6
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(colour))
        painter.drawEllipse(QRectF(x, track.center().y() - knob / 2, knob, knob))
        if self.hasFocus():
            # Outside the track, so it shows on the accent fill as well.
            painter.setOpacity(1)
            painter.setPen(QPen(QColor(c["ACCENT"]), 2))
            painter.setBrush(Qt.NoBrush)
            ring = QRectF(self.rect()).adjusted(1, 1, -1, -1)
            painter.drawRoundedRect(ring, ring.height() / 2, ring.height() / 2)
        painter.end()


def set_badge(label: QLabel, text: str, tone: str) -> None:
    """Change a badge already on screen."""
    label.setText(text)
    tone = tone if tone in theme.BADGE_TINTS else "TEXT_MUTED"
    if label.property("tone") != tone:
        label.setProperty("tone", tone)
        # A dynamic property is only read when the widget is polished.
        label.style().unpolish(label)
        label.style().polish(label)


def segmented(
    labels: Sequence[str], expand: bool = False
) -> tuple[QFrame, list[QPushButton]]:
    """A joined toggle: two or three choices drawn as one control.

    Returns the control and its buttons, checkable and in order. The caller
    puts them in its own exclusive QButtonGroup and wires them, as it did when
    they were separate buttons. `expand` shares the width out equally, for a
    toggle that fills its card.
    """
    frame = QFrame()
    frame.setObjectName("Segmented")
    layout = QHBoxLayout(frame)
    layout.setContentsMargins(3, 3, 3, 3)
    layout.setSpacing(2)
    buttons = []
    for text in labels:
        button = QPushButton(text)
        button.setObjectName("Segment")
        button.setCheckable(True)
        layout.addWidget(button, 1 if expand else 0)
        buttons.append(button)
    if not expand:
        frame.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return frame, buttons


class StatusBanner(QFrame):
    """Where the app says what just happened, under the screen. Never a dialog.

    It used to be one line of small grey text that went after 4.5 seconds,
    errors included, so a batch stopping or a post refused was easy to miss
    entirely. Now the kind of message is a coloured bar at its edge -- the
    words stay in the body colour, readable whatever the kind -- it stays for
    as long as it takes to read, an error stays until it is closed, and it can
    carry an Undo for a slip the user can take back.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Toast")
        self.setProperty("level", "info")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(theme.PAD_S, theme.PAD_S, theme.PAD_XS, theme.PAD_S)
        layout.setSpacing(theme.PAD_S)

        # The kind of message, as a bar of its own: drawn as the frame's left
        # border it bent round the rounded corner and read as a bracket.
        self.bar = QFrame()
        self.bar.setObjectName("ToastBar")
        self.bar.setProperty("level", "info")
        self.bar.setFixedWidth(4)
        layout.addWidget(self.bar)

        self.text = QLabel("")
        self.text.setObjectName("ToastText")
        self.text.setWordWrap(True)
        # So an error can be copied into a message to whoever looks after the
        # app, word for word.
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.text, 1)

        self.undo_button = QPushButton("Undo")
        self.undo_button.setObjectName("ToastAction")
        self.undo_button.hide()
        self.undo_button.clicked.connect(self._run_undo)
        layout.addWidget(self.undo_button)

        self.close_button = QPushButton("\u00d7")
        self.close_button.setObjectName("ToastClose")
        self.close_button.setToolTip("Close")
        self.close_button.setAccessibleName("Close this message")
        self.close_button.clicked.connect(self.dismiss)
        layout.addWidget(self.close_button)

        self._undo: Callable[[], None] | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.dismiss)
        self.hide()

    @property
    def level(self) -> str:
        return str(self.property("level"))

    def show_message(
        self, text: str, level: str = "info", undo: Callable[[], None] | None = None
    ) -> None:
        """Replace whatever is showing. An Undo goes with the message it came with."""
        self.text.setText(text)
        if self.property("level") != level:
            # A dynamic property is only read when the widget is polished.
            for widget in (self, self.bar):
                widget.setProperty("level", level)
                widget.style().unpolish(widget)
                widget.style().polish(widget)
        self._undo = undo
        self.undo_button.setVisible(undo is not None)
        self.show()
        duration = message_duration(text, level, undo is not None)
        if duration is None:
            self.timer.stop()
        else:
            self.timer.start(duration)

    def dismiss(self) -> None:
        self.timer.stop()
        self._undo = None
        self.undo_button.hide()
        self.text.setText("")
        self.hide()

    def _run_undo(self) -> None:
        undo, self._undo = self._undo, None
        self.dismiss()
        if undo is not None:
            undo()


def row(parent: QWidget | None = None) -> QWidget:
    """A plain container that sits inside a card without tinting it.

    A bare QWidget picks up the window background from the global rule and
    draws a grey band across the card behind it; this one is styled transparent
    in theme.py so the card shows through. Use it for anything grouped inside a
    card -- a single line, or a whole panel.
    """
    holder = QWidget(parent)
    holder.setObjectName("Row")
    return holder


def replace_at(layout: QLayout, index: int, widget: QWidget) -> None:
    """Swap one widget in a layout for another, removing the old one properly.

    Same hazard as `clear()`: `takeAt()` alone leaves the old widget a child of
    the same parent, still painting, at Qt's default 640x480 because no layout
    manages it any more. Every removal in this package goes through this module
    so that mistake cannot be made a seventh time.

    The Queue uses this to redraw only the batches that actually changed. It
    rebuilt all 25 cards whenever any one target changed state -- 127ms on the
    thread drawing the window, on every worker event, which is precisely when
    someone is watching that screen.
    """
    item = layout.takeAt(index)
    if item is not None:
        old = item.widget()
        if old is not None:
            old.setParent(None)
            old.deleteLater()
    layout.insertWidget(index, widget)
