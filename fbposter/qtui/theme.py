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

# The arrows on steppers, drop-downs and the calendar. An SVG cannot take its
# colour from the palette, so each one exists per mode -- and the up/down pair
# again as "-off", for a stepper already at its limit. One colour could not do
# for both modes: the dimmed arrow has to be *darker* than normal on a dark
# field and *lighter* on a light one.
ICON_NAMES = (
    "chevron-up", "chevron-down", "chevron-up-off", "chevron-down-off",
    "chevron-left", "chevron-right",
)

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

# The step buttons inside a time or number field: side by side and nearly the
# field's full height, the way Windows 11 draws its own number box. The stock
# ones were two 17px slivers, and the text box sat on top of half of one.
STEPPER_WIDTH = 28
STEPPER_HEIGHT = CONTROL_HEIGHT - 10
STEPPER_INSET = 4
# Room the text must leave for the buttons, or it lies on top of them and
# swallows their clicks -- which is exactly how "up" came to work only on its
# right-hand half.
STEPPER_ROOM = STEPPER_INSET + 2 * STEPPER_WIDTH + 2 + 6
DROPDOWN_ROOM = STEPPER_INSET + STEPPER_WIDTH + 6
# The chevron drawn in each; at 12px it was a 6px tick, too slight to aim at.
ARROW_SIZE = 16

RADIUS = 8
RADIUS_S = 6
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
    # Pressed goes darker again, so a click shows the moment it lands.
    "ACCENT_PRESSED": "#094A9E",
    # Accent used *as text*. Separate from the fill because the two have
    # opposite requirements in dark mode, where the fill must be dark enough
    # for white text and the text must be light enough for a dark surface.
    "ACCENT_TEXT": "#0C68DE",
    "NAV_ACTIVE_BG": "#E7F3FF",
    # Neutral states for everything that is not the accent. Hover used to be
    # the pale blue of the selected nav item, so hovering any button made it
    # look chosen.
    "HOVER_BG": "#F2F3F5",
    "PRESSED_BG": "#E4E6EB",
    "BORDER_STRONG": "#BCC0C4",
    "SUCCESS": "#1B7F3B",
    "WARNING": "#9E5E00",
    # Deeper than the #D32F45 it was, which read 4.39:1 on the window colour
    # -- where every error toast is written -- and could not sit on any tint.
    "DANGER": "#C0283C",
    # Behind a destructive action the moment you reach for it.
    "DANGER_SOFT": "#FDECEE",
    "DANGER_PRESSED_BG": "#FADCE0",
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
    "ACCENT_PRESSED": "#004AAD",
    # ...while accent-coloured *text* has to be light enough to sit on a dark
    # surface. One value cannot do both jobs here.
    "ACCENT_TEXT": "#5CA3FF",
    "NAV_ACTIVE_BG": "#263951",
    "HOVER_BG": "#2F3031",
    "PRESSED_BG": "#3A3B3C",
    "BORDER_STRONG": "#55575A",
    "SUCCESS": "#45BD62",
    "WARNING": "#FFC933",
    "DANGER": "#FF5C7C",
    "DANGER_SOFT": "#3B2227",
    "DANGER_PRESSED_BG": "#46252C",
    "NEUTRAL": "#8A8C8F",
}

# Filled in by activate(); the module is imported for its names, so this has to
# exist before anyone reads it.
C = dict(LIGHT)
MODE = "light"


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
    global C, MODE
    is_dark = system_is_dark() if dark is None else dark
    C = dict(DARK if is_dark else LIGHT)
    MODE = "dark" if is_dark else "light"
    return C


def icon_path(name: str, mode: str | None = None) -> Path:
    """The file for one of `ICON_NAMES` in the given (default: active) mode."""
    state = "-off" if name.endswith("-off") else ""
    return ASSETS / f"{name.removesuffix('-off')}-{mode or MODE}{state}.svg"


def icon(name: str) -> str:
    """An icon as a stylesheet URL: forward slashes, for the reason above."""
    return icon_path(name).as_posix()


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

    /* Buttons. Every kind states each moment it can be in -- rest, hover,
       pressed, disabled -- because a button that does not change as it is
       pressed feels as if the click did not land. The :pressed rules sit at
       the end of the block so they win over :hover and :focus, which are
       both true at the moment of a click. */
    QPushButton {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
        padding: 7px 14px;
        min-height: {CONTROL_HEIGHT - 16}px;
        color: {c["TEXT"]};
    }}
    QPushButton:hover {{ background: {c["HOVER_BG"]}; border-color: {c["BORDER_STRONG"]}; }}
    QPushButton:disabled {{
        background: transparent;
        border-color: {c["BORDER"]};
        color: {c["TEXT_MUTED"]};
    }}
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
        background: transparent;
        border: none;
        text-align: left;
        padding: 10px 12px;
        min-height: {CONTROL_HEIGHT - 14}px;
        color: {c["TEXT"]};
    }}
    QPushButton#Nav:hover {{ background: {c["HOVER_BG"]}; }}
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
    QPushButton#Tab:checked:hover {{
        background: {c["ACCENT_HOVER"]};
        border-color: {c["ACCENT_HOVER"]};
    }}
    /* Quiet until you reach for it. These are the destructive actions, and
       there is one on every row -- painted red by default, the most dangerous
       thing on the screen was also the first thing the eye landed on, four
       times over. Red on hover still says what it does, at the moment it
       matters, and the tint behind it says it is a button, not a caption. */
    QPushButton#Link {{
        background: transparent;
        border: none;
        color: {c["TEXT_MUTED"]};
        padding: 7px 10px;
    }}
    QPushButton#Link:hover {{ background: {c["DANGER_SOFT"]}; color: {c["DANGER"]}; }}
    QPushButton#Link:focus {{ border: 2px solid {c["ACCENT"]}; padding: 5px 8px; }}
    /* "Post anyway": a deliberate step outside the rules. Outlined rather than
       filled -- the one filled button on a screen is the next step in the
       flow, and this is the opposite of that. Its own :focus rule, because the
       id beats the plain one and the ring would otherwise never show. */
    QPushButton#Danger {{
        border: 1px solid {c["DANGER"]};
        color: {c["DANGER"]};
        font-weight: 600;
    }}
    QPushButton#Danger:hover {{ background: {c["DANGER_SOFT"]}; }}
    QPushButton#Danger:focus {{ border: 2px solid {c["ACCENT"]}; padding: 6px 13px; }}

    /* Keyboard focus has to be visible. A custom stylesheet replaces the
       platform focus rectangle, so without these rules tabbing through the
       window moves an invisible cursor -- the first anti-pattern in the
       accessibility list, and the easiest one to ship by accident. A mouse
       click does not focus a button (widgets.AppStyle), so the ring means
       "the keyboard is here" and never lingers on the last thing clicked. */
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

    QPushButton:pressed {{ background: {c["PRESSED_BG"]}; border-color: {c["BORDER_STRONG"]}; }}
    QPushButton#Primary:pressed {{ background: {c["ACCENT_PRESSED"]}; }}
    QPushButton#Nav:pressed {{ background: {c["PRESSED_BG"]}; }}
    QPushButton#Tab:checked:pressed {{
        background: {c["ACCENT_PRESSED"]};
        border-color: {c["ACCENT_PRESSED"]};
    }}
    QPushButton#Link:pressed {{ background: {c["DANGER_PRESSED_BG"]}; color: {c["DANGER"]}; }}
    QPushButton#Danger:pressed {{ background: {c["DANGER_PRESSED_BG"]}; }}

    QTextEdit, QLineEdit, QDateTimeEdit, QComboBox, QSpinBox {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS}px;
        padding: 8px;
        min-height: {CONTROL_HEIGHT - 18}px;
        selection-background-color: {c["ACCENT"]};
        selection-color: {c["TEXT_ON_ACCENT"]};
    }}
    QLineEdit:hover, QDateTimeEdit:hover, QComboBox:hover, QSpinBox:hover {{
        border-color: {c["BORDER_STRONG"]};
    }}
    QTextEdit:focus, QLineEdit:focus, QDateTimeEdit:focus,
    QComboBox:focus, QSpinBox:focus {{
        border: 2px solid {c["ACCENT"]};
        padding: 7px;
    }}
    QTextEdit#Editor {{ border: none; font-size: {SIZE_BODY + 1}px; }}

    /* Step buttons. Left to the platform they were two small arrows, and the
       text box -- sized by the padding above, which knew nothing of them --
       lay across the left half of "up", so half of it did nothing at all.
       The right padding now reserves their room, and they are drawn here:
       side by side, nearly full height, decrease then increase. */
    QAbstractSpinBox {{ padding-right: {STEPPER_ROOM}px; }}
    QAbstractSpinBox:focus {{ padding-right: {STEPPER_ROOM - 1}px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
        subcontrol-origin: border;
        subcontrol-position: center right;
        width: {STEPPER_WIDTH}px;
        height: {STEPPER_HEIGHT}px;
        border: none;
        border-radius: {RADIUS_S}px;
        background: transparent;
    }}
    QAbstractSpinBox::up-button {{ right: {STEPPER_INSET}px; }}
    QAbstractSpinBox::down-button {{ right: {STEPPER_INSET + STEPPER_WIDTH + 2}px; }}
    QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{
        background: {c["HOVER_BG"]};
    }}
    QAbstractSpinBox::up-button:pressed, QAbstractSpinBox::down-button:pressed {{
        background: {c["PRESSED_BG"]};
    }}
    QAbstractSpinBox::up-arrow {{ image: url({icon("chevron-up")}); width: {ARROW_SIZE}px; height: {ARROW_SIZE}px; }}
    QAbstractSpinBox::down-arrow {{ image: url({icon("chevron-down")}); width: {ARROW_SIZE}px; height: {ARROW_SIZE}px; }}
    /* "off" is a stepper already at its limit. It dims, rather than looking
       ready and then ignoring the click -- which is what read as broken. */
    QAbstractSpinBox::up-arrow:off, QAbstractSpinBox::up-arrow:disabled {{
        image: url({icon("chevron-up-off")});
    }}
    QAbstractSpinBox::down-arrow:off, QAbstractSpinBox::down-arrow:disabled {{
        image: url({icon("chevron-down-off")});
    }}

    /* One drop-down look for the template list and the date picker. */
    QComboBox, QDateTimeEdit#DateEntry {{ padding-right: {DROPDOWN_ROOM}px; }}
    QComboBox:focus, QDateTimeEdit#DateEntry:focus {{ padding-right: {DROPDOWN_ROOM - 1}px; }}
    QComboBox::drop-down, QDateTimeEdit::drop-down {{
        subcontrol-origin: border;
        subcontrol-position: center right;
        right: {STEPPER_INSET}px;
        width: {STEPPER_WIDTH}px;
        height: {STEPPER_HEIGHT}px;
        border: none;
        border-radius: {RADIUS_S}px;
        background: transparent;
    }}
    QComboBox::drop-down:hover, QDateTimeEdit::drop-down:hover {{ background: {c["HOVER_BG"]}; }}
    QComboBox::drop-down:pressed, QDateTimeEdit::drop-down:pressed {{
        background: {c["PRESSED_BG"]};
    }}
    QComboBox::down-arrow {{ image: url({icon("chevron-down")}); width: {ARROW_SIZE}px; height: {ARROW_SIZE}px; }}
    QComboBox::down-arrow:on {{ image: url({icon("chevron-up")}); }}
    QComboBox QAbstractItemView {{
        background: {c["SURFACE"]};
        color: {c["TEXT"]};
        border: 1px solid {c["BORDER"]};
        padding: 4px;
        selection-background-color: {c["NAV_ACTIVE_BG"]};
        selection-color: {c["TEXT"]};
    }}
    QComboBox QAbstractItemView::item {{
        min-height: {CONTROL_HEIGHT - 8}px;
        padding: 0 8px;
        border-radius: {RADIUS_S}px;
    }}
    QComboBox QAbstractItemView::item:hover {{ background: {c["HOVER_BG"]}; }}
    QComboBox QAbstractItemView::item:selected {{
        background: {c["NAV_ACTIVE_BG"]};
        color: {c["TEXT"]};
    }}

    /* The date picker's calendar. Unstyled it was the platform's own: red
       weekends (Saturday and Sunday, which is not even Israel's weekend), a
       grey header, and a next-month arrow that vanished in dark mode. */
    QCalendarWidget QWidget {{ background: {c["SURFACE"]}; }}
    QCalendarWidget QWidget#qt_calendar_navigationbar {{
        background: {c["SURFACE"]};
        border-bottom: 1px solid {c["BORDER"]};
        padding: 4px;
    }}
    QCalendarWidget QToolButton {{
        background: transparent;
        color: {c["TEXT"]};
        border: none;
        border-radius: {RADIUS_S}px;
        padding: 4px 10px;
        font-weight: 600;
    }}
    QCalendarWidget QToolButton:hover {{ background: {c["HOVER_BG"]}; }}
    QCalendarWidget QToolButton:pressed {{ background: {c["PRESSED_BG"]}; }}
    QCalendarWidget QToolButton::menu-indicator {{ image: none; width: 0; }}
    QCalendarWidget QToolButton#qt_calendar_prevmonth {{
        qproperty-icon: url({icon("chevron-left")});
    }}
    QCalendarWidget QToolButton#qt_calendar_nextmonth {{
        qproperty-icon: url({icon("chevron-right")});
    }}
    /* The box that opens when the year is clicked. It is a spin box, so the
       step-button rules above reached it, and Qt sizes it from its hint: with
       68px of room for buttons it ran off the calendar's right edge, over the
       next-month arrow, with its own arrows cut off. Compact and buttonless
       instead -- typed, or stepped with the arrow keys, like the rest of the
       date. */
    QCalendarWidget QSpinBox {{
        padding: 2px 6px;
        min-height: 0;
        /* Qt sizes it from the platform's buttons, which are hidden here, and
           it then covered the next-month arrow. */
        max-width: 72px;
        border-radius: {RADIUS_S}px;
        font-weight: 600;
    }}
    QCalendarWidget QSpinBox:focus {{ padding: 1px 5px; }}
    QCalendarWidget QSpinBox::up-button, QCalendarWidget QSpinBox::down-button {{
        width: 0;
        border: none;
    }}
    QCalendarWidget QSpinBox::up-arrow, QCalendarWidget QSpinBox::down-arrow {{
        image: none;
        width: 0;
        height: 0;
    }}
    QCalendarWidget QMenu {{
        background: {c["SURFACE"]};
        color: {c["TEXT"]};
        border: 1px solid {c["BORDER"]};
    }}
    QCalendarWidget QMenu::item {{ padding: 6px 16px; }}
    QCalendarWidget QMenu::item:selected {{ background: {c["NAV_ACTIVE_BG"]}; color: {c["TEXT"]}; }}
    QCalendarWidget QAbstractItemView {{
        background: {c["SURFACE"]};
        color: {c["TEXT"]};
        border: none;
        selection-background-color: {c["ACCENT"]};
        selection-color: {c["TEXT_ON_ACCENT"]};
    }}
    /* Not about a disabled calendar: Qt draws the neighbouring months' days
       in the palette's *disabled* text colour, and this is what sets it.
       Without it they came out as dark as the month being shown. Past days
       get the same colour from ScheduleEntry._dim_past_days. */
    QCalendarWidget QAbstractItemView:disabled {{ color: {c["BORDER_STRONG"]}; }}

    QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle:vertical {{
        background: {c["BORDER"]}; border-radius: 5px; min-height: 30px;
    }}
    QScrollBar::handle:horizontal {{
        background: {c["BORDER"]}; border-radius: 5px; min-width: 30px;
    }}
    QScrollBar::handle:hover {{ background: {c["BORDER_STRONG"]}; }}
    QScrollBar::handle:pressed {{ background: {c["TEXT_MUTED"]}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
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
        border: 1px solid {c["BORDER_STRONG"]};
        border-radius: 4px;
        background: {c["SURFACE"]};
    }}
    QCheckBox::indicator:hover {{ border-color: {c["ACCENT"]}; }}
    QCheckBox::indicator:pressed {{ background: {c["NAV_ACTIVE_BG"]}; border-color: {c["ACCENT"]}; }}
    QCheckBox::indicator:checked {{
        background: {c["ACCENT"]};
        border-color: {c["ACCENT"]};
        image: url({CHECK_ICON});
    }}
    QCheckBox::indicator:checked:hover {{
        background: {c["ACCENT_HOVER"]};
        border-color: {c["ACCENT_HOVER"]};
    }}
    QCheckBox::indicator:checked:pressed {{
        background: {c["ACCENT_PRESSED"]};
        border-color: {c["ACCENT_PRESSED"]};
    }}
    """
