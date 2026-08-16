"""Colour, type and spacing for the Qt UI.

The same Facebook-derived palette as the Tk theme, resolved to one set of
values at startup rather than carried as (light, dark) pairs: Qt styles through
a stylesheet, and a stylesheet is a string, so the mode has to be chosen before
it is built.
"""

from __future__ import annotations

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"

SIZE_TITLE = 20
SIZE_HEADING = 15
SIZE_BODY = 13
SIZE_SMALL = 12

PAD_XS = 4
PAD_S = 8
PAD_M = 12
PAD_L = 16
PAD_XL = 24

RADIUS = 8
SIDEBAR_WIDTH = 210
WINDOW_MIN = (940, 620)
WINDOW_DEFAULT = (1040, 700)

LIGHT = {
    "WINDOW_BG": "#F0F2F5",
    "SURFACE": "#FFFFFF",
    "SIDEBAR_BG": "#FFFFFF",
    "BORDER": "#DADDE1",
    "TEXT": "#1C1E21",
    "TEXT_MUTED": "#65676B",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "ACCENT": "#1877F2",
    "ACCENT_HOVER": "#166FE5",
    "NAV_ACTIVE_BG": "#E7F3FF",
    "SUCCESS": "#1B7F3B",
    "WARNING": "#B26A00",
    "DANGER": "#D32F45",
    "NEUTRAL": "#8A8D91",
}

DARK = {
    "WINDOW_BG": "#18191A",
    "SURFACE": "#242526",
    "SIDEBAR_BG": "#1C1D1E",
    "BORDER": "#3A3B3C",
    "TEXT": "#E4E6EB",
    "TEXT_MUTED": "#B0B3B8",
    "TEXT_ON_ACCENT": "#FFFFFF",
    "ACCENT": "#2D88FF",
    "ACCENT_HOVER": "#4599FF",
    "NAV_ACTIVE_BG": "#263951",
    "SUCCESS": "#45BD62",
    "WARNING": "#FFC933",
    "DANGER": "#FF5C7C",
    "NEUTRAL": "#77797C",
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
    /* Without this a label inside a card paints the window colour behind
       itself and shows up as a grey band across the surface. */
    QLabel, QCheckBox, QWidget#Row {{ background: transparent; }}
    QLabel#Title {{ font-size: {SIZE_TITLE}px; font-weight: 600; }}
    QLabel#Subtitle, QLabel#Muted {{ color: {c["TEXT_MUTED"]}; font-size: {SIZE_SMALL}px; }}
    QLabel#SectionHeading {{ font-size: {SIZE_SMALL}px; font-weight: 600; }}
    QLabel#Brand {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}

    QPushButton {{
        background: transparent;
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
        padding: 6px 12px;
        color: {c["TEXT"]};
    }}
    QPushButton:hover {{ background: {c["NAV_ACTIVE_BG"]}; }}
    QPushButton:disabled {{ color: {c["TEXT_MUTED"]}; }}
    QPushButton#Primary {{
        background: {c["ACCENT"]};
        border: none;
        color: {c["TEXT_ON_ACCENT"]};
        font-weight: 600;
        padding: 9px 14px;
    }}
    QPushButton#Primary:hover {{ background: {c["ACCENT_HOVER"]}; }}
    QPushButton#Nav {{
        border: none;
        text-align: left;
        padding: 9px 12px;
        color: {c["TEXT"]};
    }}
    QPushButton#Nav:checked {{
        background: {c["NAV_ACTIVE_BG"]};
        color: {c["ACCENT"]};
        font-weight: 600;
    }}
    QPushButton#Tab {{ padding: 4px 10px; }}
    QPushButton#Tab:checked {{
        background: {c["ACCENT"]};
        border-color: {c["ACCENT"]};
        color: {c["TEXT_ON_ACCENT"]};
    }}
    QPushButton#Link {{ border: none; color: {c["DANGER"]}; }}

    QTextEdit, QLineEdit, QDateTimeEdit, QComboBox, QSpinBox {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
        padding: 6px;
        selection-background-color: {c["ACCENT"]};
        selection-color: {c["TEXT_ON_ACCENT"]};
    }}
    QTextEdit#Editor {{ border: none; font-size: {SIZE_BODY + 1}px; }}
    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {c["BORDER"]}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QCheckBox {{ spacing: 8px; }}
    """
