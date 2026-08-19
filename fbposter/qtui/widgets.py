"""Shared widget helpers.

Separate from app.py so the views can use them without importing the window
that imports the views.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QLayout, QWidget


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
