"""Colour, type and spacing for the Qt UI.

The same Facebook-derived palette as the Tk theme, resolved to one set of
values at startup rather than carried as (light, dark) pairs: Qt styles through
a stylesheet, and a stylesheet is a string, so the mode has to be chosen before
it is built.
"""

from __future__ import annotations

from pathlib import Path

# Qt stylesheets take a file path, not a data URI. Forward slashes on Windows
# too -- a backslash is an escape character inside a stylesheet, so a native
# path silently fails to load and the tick just never appears.
ASSETS = Path(__file__).parent / "assets"
CHECK_ICON = (ASSETS / "check.svg").as_posix()

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

# Body was 13px, and run() then shrank the application font by another 2pt on
# top of it -- small enough to squint at, and below the 12px floor that counts
# as an anti-pattern for body text. Nothing here is decorative: this is the
# single biggest thing that made the window feel approachable.
SIZE_TITLE = 24
SIZE_HEADING = 17
SIZE_BODY = 15
SIZE_SMALL = 13

PAD_XS = 4
PAD_S = 8
PAD_M = 12
PAD_L = 16
PAD_XL = 24

# Hit targets. A 30px button is fine to click and mean to aim at; the primary
# action on each screen gets the full 44.
CONTROL_HEIGHT = 36
PRIMARY_HEIGHT = 44

RADIUS = 8
SIDEBAR_WIDTH = 232
WINDOW_MIN = (980, 660)
WINDOW_DEFAULT = (1120, 760)

LIGHT = {
    "WINDOW_BG": "#F0F2F5",
    "SURFACE": "#FFFFFF",
    "SIDEBAR_BG": "#FFFFFF",
    "BORDER": "#DADDE1",
    "TEXT": "#1C1E21",
    "TEXT_MUTED": "#65676B",
    "TEXT_ON_ACCENT": "#FFFFFF",
    # Darker than Facebook's own #1877F2, which puts white text at 4.23:1 --
    # under the 4.5:1 floor. This one clears it at 5.18:1 and still reads as
    # the same blue.
    "ACCENT": "#0C68DE",
    "ACCENT_HOVER": "#0A57BA",
    # Accent used *as text*. Separate from the fill because the two have
    # opposite requirements in dark mode, where the fill must be dark enough
    # for white text and the text must be light enough for a dark surface.
    "ACCENT_TEXT": "#0C68DE",
    "NAV_ACTIVE_BG": "#E7F3FF",
    "SUCCESS": "#1B7F3B",
    "WARNING": "#9E5E00",
    "DANGER": "#D32F45",
    "NEUTRAL": "#6A6D71",
}

DARK = {
    "WINDOW_BG": "#18191A",
    "SURFACE": "#242526",
    "SIDEBAR_BG": "#1C1D1E",
    "BORDER": "#3A3B3C",
    "TEXT": "#E4E6EB",
    "TEXT_MUTED": "#B0B3B8",
    "TEXT_ON_ACCENT": "#FFFFFF",
    # The fill has to be dark enough to carry white text (4.64:1); hover goes
    # darker still rather than lighter, because lightening it drops white
    # below the floor.
    "ACCENT": "#006CFA",
    "ACCENT_HOVER": "#0059CC",
    # ...while accent-coloured *text* has to be light enough to sit on a dark
    # surface. One value cannot do both jobs here.
    "ACCENT_TEXT": "#5CA3FF",
    "NAV_ACTIVE_BG": "#263951",
    "SUCCESS": "#45BD62",
    "WARNING": "#FFC933",
    "DANGER": "#FF5C7C",
    "NEUTRAL": "#8A8C8F",
}

# Filled in by activate(); the module is imported for its names, so this has to
# exist before anyone reads it.
C = dict(LIGHT)


def system_is_dark() -> bool:
    """Follow the Windows app theme, the way the Tk build did."""
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        with key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return value == 0
    except Exception:
        return False


def activate(dark: bool | None = None) -> dict:
    """Choose the palette. Call once, before building the stylesheet."""
    global C
    C = dict(DARK if (system_is_dark() if dark is None else dark) else LIGHT)
    return C


def stylesheet() -> str:
    """One stylesheet for the whole window.

    Widget-by-widget styling in Qt is possible but scatters colour around the
    codebase, which is the thing the Tk theme rules were written to prevent.
    """
    c = C
    return f"""
    QWidget {{
        background: {c["WINDOW_BG"]};
        color: {c["TEXT"]};
        font-family: "{FONT_FAMILY}";
        font-size: {SIZE_BODY}px;
    }}
    QFrame#Sidebar {{
        background: {c["SIDEBAR_BG"]};
        border-right: 1px solid {c["BORDER"]};
    }}
    QFrame#Card {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
    }}
    QFrame#Divider {{ background: {c["BORDER"]}; border: none; }}
    /* Without this a label inside a card paints the window colour behind
       itself and shows up as a grey band across the surface. QStackedWidget is
       here for the same reason and was missing: both mode switchers live
       inside a card, and the Publish one drew a grey slab across the white
       "When" panel wherever its page did not cover it. */
    QLabel, QCheckBox, QWidget#Row, QStackedWidget {{ background: transparent; }}
    QLabel#Title {{ font-size: {SIZE_TITLE}px; font-weight: 600; }}
    QLabel#Subtitle, QLabel#Muted {{ color: {c["TEXT_MUTED"]}; font-size: {SIZE_SMALL}px; }}
    QLabel#SectionHeading {{ font-size: {SIZE_SMALL}px; font-weight: 600; }}
    QLabel#Brand {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}

    QPushButton {{
        background: transparent;
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
        padding: 7px 14px;
        min-height: {CONTROL_HEIGHT - 16}px;
        color: {c["TEXT"]};
    }}
    QPushButton:hover {{ background: {c["NAV_ACTIVE_BG"]}; }}
    QPushButton:disabled {{ color: {c["TEXT_MUTED"]}; }}
    QPushButton#Primary {{
        background: {c["ACCENT"]};
        border: none;
        color: {c["TEXT_ON_ACCENT"]};
        font-weight: 600;
        padding: 11px 18px;
        min-height: {PRIMARY_HEIGHT - 22}px;
    }}
    QPushButton#Primary:hover {{ background: {c["ACCENT_HOVER"]}; }}
    QPushButton#Primary:disabled {{
        background: {c["BORDER"]};
        color: {c["TEXT_MUTED"]};
    }}
    QPushButton#Nav {{
        border: none;
        text-align: left;
        padding: 10px 12px;
        min-height: {CONTROL_HEIGHT - 14}px;
        color: {c["TEXT"]};
    }}
    QPushButton#Nav:checked {{
        background: {c["NAV_ACTIVE_BG"]};
        color: {c["ACCENT_TEXT"]};
        font-weight: 600;
    }}
    QPushButton#Tab {{ padding: 6px 12px; }}
    QPushButton#Tab:checked {{
        background: {c["ACCENT"]};
        border-color: {c["ACCENT"]};
        color: {c["TEXT_ON_ACCENT"]};
    }}
    /* Quiet until you reach for it. These are the destructive actions, and
       there is one on every row -- painted red by default, the most dangerous
       thing on the screen was also the first thing the eye landed on, four
       times over. Red on hover still says what it does, at the moment it
       matters. */
    QPushButton#Link {{ border: none; color: {c["TEXT_MUTED"]}; padding: 7px 10px; }}
    QPushButton#Link:hover {{ background: transparent; color: {c["DANGER"]}; }}
    QPushButton#Link:focus {{ border: 2px solid {c["ACCENT"]}; padding: 5px 8px; }}

    /* Keyboard focus has to be visible. A custom stylesheet replaces the
       platform focus rectangle, so without these rules tabbing through the
       window moves an invisible cursor -- the first anti-pattern in the
       accessibility list, and the easiest one to ship by accident. */
    QPushButton:focus {{
        border: 2px solid {c["ACCENT"]};
        padding: 6px 13px;
    }}
    /* A white ring inside the fill clears 3:1 against the fill, but from the
       outside it meets a near-white page and all but disappears. Darkening the
       fill as well means focus is legible from either side. */
    QPushButton#Primary:focus {{
        background: {c["ACCENT_HOVER"]};
        border: 2px solid {c["TEXT_ON_ACCENT"]};
        padding: 10px 17px;
    }}
    QPushButton#Nav:focus {{
        border: 2px solid {c["ACCENT"]};
        padding: 9px 11px;
    }}
    QCheckBox:focus {{ color: {c["ACCENT_TEXT"]}; font-weight: 600; }}

    QTextEdit, QLineEdit, QDateTimeEdit, QComboBox, QSpinBox {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
        padding: 8px;
        min-height: {CONTROL_HEIGHT - 18}px;
        selection-background-color: {c["ACCENT"]};
        selection-color: {c["TEXT_ON_ACCENT"]};
    }}
    QTextEdit:focus, QLineEdit:focus, QDateTimeEdit:focus,
    QComboBox:focus, QSpinBox:focus {{
        border: 2px solid {c["ACCENT"]};
        padding: 7px;
    }}
    QTextEdit#Editor {{ border: none; font-size: {SIZE_BODY + 1}px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {c["BORDER"]}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QCheckBox {{ spacing: 8px; }}
    /* Left to the platform, the two states did not look like one control:
       unchecked drew an empty rounded box, checked drew a bare grey tick with
       no box at all -- so the *selected* row was the fainter of the two, which
       is backwards on a screen whose whole job is picking groups. Filling the
       box with the accent puts the emphasis on chosen, where it belongs.

       If the SVG ever fails to load the box still fills with the accent, so
       the state stays unambiguous; only the tick is lost. */
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {c["BORDER"]};
        border-radius: 4px;
        background: {c["SURFACE"]};
    }}
    QCheckBox::indicator:hover {{ border-color: {c["ACCENT"]}; }}
    QCheckBox::indicator:checked {{
        background: {c["ACCENT"]};
        border-color: {c["ACCENT"]};
        image: url({CHECK_ICON});
    }}
    """
