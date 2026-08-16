"""Shared widget helpers.

Separate from app.py so the views can use them without importing the window
that imports the views.
"""

from __future__ import annotations

from PySide6.QtWidgets import QFrame, QWidget


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
