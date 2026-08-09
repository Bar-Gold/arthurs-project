"""Colour, type and spacing tokens.

Every colour is a (light, dark) pair because CustomTkinter accepts tuples
directly and picks the right one for the current appearance mode. Defining
both together is what makes "follow the Windows theme" work without a second
palette that quietly rots.

The greys are Facebook's own, so the app sits next to the browser window
without clashing.
"""

from __future__ import annotations

Color = tuple[str, str]

# --- surfaces -------------------------------------------------------------
WINDOW_BG: Color = ("#F0F2F5", "#18191A")
SURFACE: Color = ("#FFFFFF", "#242526")
SIDEBAR_BG: Color = ("#FFFFFF", "#1C1D1E")
BORDER: Color = ("#DADDE1", "#3A3B3C")

# --- text -----------------------------------------------------------------
TEXT: Color = ("#1C1E21", "#E4E6EB")
TEXT_MUTED: Color = ("#65676B", "#B0B3B8")
TEXT_ON_ACCENT: Color = ("#FFFFFF", "#FFFFFF")

# --- accent ---------------------------------------------------------------
ACCENT: Color = ("#1877F2", "#2D88FF")
ACCENT_HOVER: Color = ("#166FE5", "#4599FF")
NAV_ACTIVE_BG: Color = ("#E7F3FF", "#263951")

# --- status ---------------------------------------------------------------
# Amber differs sharply between modes: a light background needs a much darker
# amber than a dark one before the text is readable.
SUCCESS: Color = ("#1B7F3B", "#45BD62")
WARNING: Color = ("#B26A00", "#FFC933")
DANGER: Color = ("#D32F45", "#FF5C7C")
NEUTRAL: Color = ("#8A8D91", "#77797C")

# --- type -----------------------------------------------------------------
FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

SIZE_TITLE = 20
SIZE_HEADING = 15
SIZE_BODY = 13
SIZE_SMALL = 12

# --- spacing --------------------------------------------------------------
PAD_XS = 4
PAD_S = 8
PAD_M = 12
PAD_L = 16
PAD_XL = 24

RADIUS = 8
SIDEBAR_WIDTH = 210
WINDOW_MIN = (940, 620)
WINDOW_DEFAULT = "1040x700"
