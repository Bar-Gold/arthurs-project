"""Colour, type and spacing for the Qt UI.

Navy and gold (the user's choice, 2026-10-20, replacing the Facebook blue it
started from): a deep navy sidebar, warm ivory pages and navy buttons. Light
only: dark mode was removed at the user's request (2026-10-20), after its
Light/Dark switch could not be made smooth enough for them.
"""

from __future__ import annotations

from pathlib import Path

# Qt stylesheets take a file path, not a data URI. Forward slashes on Windows
# too -- a backslash is an escape character inside a stylesheet, so a native
# path silently fails to load and the tick just never appears.
ASSETS = Path(__file__).parent / "assets"
# The tick, white on the navy box.
CHECK_ICON = (ASSETS / "check.svg").as_posix()

# The arrows on steppers, drop-downs and the calendar. An SVG cannot take its
# colour from the palette, so the colour is in the file -- and the up/down pair
# exists again as "-off", for a stepper already at its limit. The files still
# end in "-light", from when there was a dark set too.
ICON_NAMES = (
    "chevron-up", "chevron-down", "chevron-up-off", "chevron-down-off",
    "chevron-left", "chevron-right",
)

FONT_FAMILY = "Segoe UI"
FONT_MONO = "Consolas"
# Page titles, the Home headline and the name in the sidebar, and nothing
# else: labels, buttons and numbers stay in the UI face. Bellefair is a
# high-contrast serif that also has Hebrew, which the Posting tips title needs
# (2026-10-20). It ships in assets/fonts with its licence (SIL OFL), and has
# one weight, so the rules that use it set 400: Qt would fake a bold.
FONT_DISPLAY = "Bellefair"
FONT_FILES = (ASSETS / "fonts" / "Bellefair-Regular.ttf",)

# Body was 13px, and run() then shrank the application font by another 2pt on
# top of it -- small enough to squint at, and below the 12px floor that counts
# as an anti-pattern for body text. Nothing here is decorative: this is the
# single biggest thing that made the window feel approachable.
SIZE_TITLE = 24
SIZE_HEADING = 17
SIZE_BODY = 15
SIZE_SMALL = 13
SIZE_STAT = 28
# A page title, in the serif. A serif at regular weight needs a few pixels
# more than the semibold sans did to carry the same weight.
SIZE_DISPLAY = 30
# The name beside the logo in the sidebar.
SIZE_BRAND = 22
# The Home page's one headline, the largest type in the window.
SIZE_HOME = 40

PAD_XS = 4
PAD_S = 8
PAD_M = 12
PAD_L = 16
PAD_XL = 24

# Between a screen's subtitle and the first thing on it. It was 12px, the
# same as the gap between two controls, so the header read as one more row of
# the form rather than as the name of the page.
HEADER_GAP = PAD_L

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
# A button that opens a list: the padding at its right. Qt keeps the
# chevron's own room out of the words by itself.
MENU_ROOM = 24
# The round button on an attached picture's corner, which takes it off.
THUMB_REMOVE = 22

RADIUS = 8
RADIUS_S = 6
# Cards and the banner: a softer corner than the controls inside them.
RADIUS_L = 12
SIDEBAR_WIDTH = 232
WINDOW_MIN = (980, 660)
WINDOW_DEFAULT = (1120, 760)

# The sidebar is deep navy, so it has colours of its own: the page's text
# colours are dark and would vanish on it. Every status shown there (the
# connection, "Posting is paused") has a sidebar version, light enough for
# navy. Gold marks the screen you are on.
SIDEBAR_LIGHT = {
    "SIDEBAR_BG": "#101B30",
    "SIDEBAR_BORDER": "#26324D",
    "SIDEBAR_TEXT": "#E8E3D9",
    "SIDEBAR_MUTED": "#A4ACBD",
    "SIDEBAR_HOVER": "#1A2640",
    "SIDEBAR_PRESSED": "#23304D",
    "SIDEBAR_ACTIVE_BG": "#1F2C48",
    "SIDEBAR_ACTIVE": "#E0C27F",
    "SIDEBAR_SUCCESS": "#6FD394",
    "SIDEBAR_WARNING": "#F3B860",
    "SIDEBAR_DANGER": "#FF8A97",
    "SIDEBAR_NEUTRAL": "#A4ACBD",
}

LIGHT = {
    **SIDEBAR_LIGHT,
    "WINDOW_BG": "#F6F3EE",
    # The light behind the middle of Home: WINDOW_BG raised by the same step in
    # every channel, so the gradient shows no coloured rings.
    "HOME_GLOW": "#FFFCF7",
    "SURFACE": "#FFFFFF",
    "BORDER": "#E3DCD0",
    "TEXT": "#161D2B",
    "TEXT_MUTED": "#5C6270",
    "TEXT_ON_ACCENT": "#FFFFFF",
    # Navy: white text on it is over 12:1.
    "ACCENT": "#1C2E52",
    "ACCENT_HOVER": "#2A406B",
    # Pressed goes darker again, so a click shows the moment it lands.
    "ACCENT_PRESSED": "#111E38",
    # Accent used *as text*: a touch bluer than the body text, so a chosen
    # tab still reads as chosen.
    "ACCENT_TEXT": "#1F3A6B",
    # The champagne tint behind a chosen tab or step.
    "NAV_ACTIVE_BG": "#F3ECDD",
    # The outline of a chosen tab or step. Deep enough for 3:1 on white.
    "GOLD": "#A88440",
    # The logo's own gold. Decoration only, never text.
    "BRAND": "#C9A45C",
    # Neutral states for everything that is not the accent. Hover used to be
    # the pale tint of the selected tab, so hovering any button made it look
    # chosen.
    "HOVER_BG": "#F2EEE7",
    "PRESSED_BG": "#E8E1D5",
    "BORDER_STRONG": "#C5BBAB",
    # The darker lower edge that lifts a box off what it sits on, the way
    # Windows 11 draws its own controls (2026-10-21). A typing box has the
    # firmest: its lower line is where the words go. Then a button, then a
    # panel, which only needs to read as one sheet laid on the page.
    "FIELD_BORDER": "#DCD4C7",
    "FIELD_STROKE": "#9E9483",
    "CONTROL_STROKE": "#CDC3B3",
    "CARD_STROKE": "#D9D0C1",
    # A typing box or a button under the pointer: warmer than white, quieter
    # than the hover of a list row.
    "FIELD_HOVER": "#FBF9F5",
    # A ticked group's row (2026-10-24): a faint champagne, so the picked ones
    # stand out down a list of 40-80. Under the pointer it deepens to
    # NAV_ACTIVE_BG, the chosen tab's tint.
    "ROW_PICKED": "#F8F2E4",
    # The hint in an empty typing box. Qt's own was 3.3:1 on white, too faint
    # to read; this is lighter than TEXT_MUTED, so it never looks typed.
    "PLACEHOLDER": "#6E7380",
    "SUCCESS": "#1D7A42",
    "WARNING": "#A0520C",
    "DANGER": "#B4283B",
    # Behind a destructive action the moment you reach for it.
    "DANGER_SOFT": "#FBEDEE",
    "DANGER_PRESSED_BG": "#F6DCDF",
    "NEUTRAL": "#676C77",
    # A post held for an admin, on the Dashboard's charts.
    "INFO": "#2E4D86",
    # The tints behind a status badge, each pale enough to keep its own colour
    # of text above 4.5:1. The accent's tint is NAV_ACTIVE_BG.
    "SUCCESS_SOFT": "#EAF4EC",
    "WARNING_SOFT": "#FBEEDF",
    "NEUTRAL_SOFT": "#EEEAE3",
    # A joined toggle: the track, and the raised segment that is chosen.
    "SEGMENT_TRACK": "#EDE8DF",
    "SEGMENT_ON": "#FFFFFF",
    # Facebook's own colours, for the preview only (2026-10-22): it is a
    # window onto Facebook, so it is drawn as Facebook draws, not in navy and
    # gold. The feed's grey ground, a post, its text and its grey words, the
    # line over Like/Comment/Share, a post's edge, and a group's picture tile.
    "FB_FEED": "#F0F2F5",
    "FB_CARD": "#FFFFFF",
    "FB_TEXT": "#050505",
    "FB_MUTED": "#65676B",
    "FB_DIVIDER": "#CED0D4",
    "FB_BORDER": "#DADDE1",
    "FB_GROUP_BG": "#E4E6EB",
}

# A status badge's text colour, by the colour token a status is given, and the
# tint behind it. Statuses keep naming their colour as before; the badge looks
# the tint up here.
BADGE_TINTS = {
    "SUCCESS": "SUCCESS_SOFT",
    "WARNING": "WARNING_SOFT",
    "DANGER": "DANGER_SOFT",
    "ACCENT_TEXT": "NAV_ACTIVE_BG",
    "TEXT_MUTED": "NEUTRAL_SOFT",
}

# Filled in by activate(); the module is imported for its names, so this has to
# exist before anyone reads it.
C = dict(LIGHT)


def activate() -> dict:
    """Fill C with the palette, before building the stylesheet from it."""
    global C
    C = dict(LIGHT)
    return C


def icon_path(name: str) -> Path:
    """The file for one of `ICON_NAMES`."""
    state = "-off" if name.endswith("-off") else ""
    return ASSETS / f"{name.removesuffix('-off')}-light{state}.svg"


def icon(name: str) -> str:
    """An icon as a stylesheet URL: forward slashes, for the reason above."""
    return icon_path(name).as_posix()


def check_icon() -> str:
    """The checkbox tick."""
    return CHECK_ICON


def load_fonts() -> list[str]:
    """Register the bundled fonts with Qt, and return the families loaded.

    Needs a QApplication. A font that will not load is left out rather than
    raised: Qt then draws the titles in the UI face, which is plainer, not
    broken.
    """
    from PySide6.QtGui import QFontDatabase

    families: list[str] = []
    for path in FONT_FILES:
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id != -1:
            families.extend(QFontDatabase.applicationFontFamilies(font_id))
    return families


def stylesheet() -> str:
    """One stylesheet for the whole window.

    Widget-by-widget styling in Qt is possible but scatters colour around the
    codebase, which is the thing the Tk theme rules were written to prevent.
    """
    c = C
    badge_tones = "\n    ".join(
        f'QLabel#Badge[tone="{tone}"] {{ color: {c[tone]}; background: {c[tint]}; }}'
        for tone, tint in BADGE_TINTS.items()
    )
    return f"""
    QWidget {{
        background: {c["WINDOW_BG"]};
        color: {c["TEXT"]};
        font-family: "{FONT_FAMILY}";
        font-size: {SIZE_BODY}px;
    }}
    QFrame#Sidebar {{
        background: {c["SIDEBAR_BG"]};
        border-right: 1px solid {c["SIDEBAR_BORDER"]};
    }}
    /* A list (widgets.RowList) is drawn as a card: one box, its rows inside
       with a hairline between them. Every list of groups uses it. */
    QFrame#Card, QFrame#List {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-bottom-color: {c["CARD_STROKE"]};
        border-radius: {RADIUS_L}px;
    }}
    QFrame#Divider {{ background: {c["BORDER"]}; border: none; }}
    /* Without this a label inside a card paints the window colour behind
       itself and shows up as a grey band across the surface. QStackedWidget is
       here for the same reason and was missing: both mode switchers live
       inside a card, and the Publish one drew a grey slab across the white
       "When" panel wherever its page did not cover it. A list's rows are
       here too, or each would paint over the box it sits in. */
    QLabel, QCheckBox, QWidget#Row, QStackedWidget {{ background: transparent; }}
    QFrame#ListRow {{ background: transparent; border: none; }}
    /* A name that leads its card (a set, a repeating post, a batch). One
       rule, where each screen used to set its own bold. */
    QLabel#Strong {{ font-weight: 600; }}
    /* A group whose name is still being looked up. */
    QCheckBox#Pending {{ color: {c["TEXT_MUTED"]}; }}
    QLabel#Title {{
        font-family: "{FONT_DISPLAY}";
        font-size: {SIZE_DISPLAY}px;
        font-weight: 400;
    }}
    QLabel#Subtitle, QLabel#Muted {{ color: {c["TEXT_MUTED"]}; font-size: {SIZE_SMALL}px; }}
    /* A heading is never smaller than what it heads. At SIZE_SMALL it was,
       and "Going out" sat above a list of names two pixels larger than itself,
       the same size as the grey hints around it. */
    QLabel#SectionHeading {{ font-size: {SIZE_BODY}px; font-weight: 600; }}
    /* The name of a card that holds settings of its own (Settings), one step
       up from the setting names inside it, which are body text. */
    QLabel#CardTitle {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}
    QLabel#HomeTitle {{
        font-family: "{FONT_DISPLAY}";
        font-size: {SIZE_HOME}px;
        font-weight: 400;
    }}
    QLabel#HomeLine {{ color: {c["TEXT_MUTED"]}; font-size: {SIZE_BODY}px; }}
    /* Home's greeting, in capitals and tracked wide (widgets.TRACKING). The
       words are muted, not gold: gold text is 3.1:1 on the cream page. The
       gold is the two hairlines either side, which are detail. */
    QLabel#HomeGreeting {{
        color: {c["TEXT_MUTED"]};
        font-size: {SIZE_SMALL}px;
        font-weight: 600;
    }}
    QFrame#HomeRule {{ background: {c["GOLD"]}; border: none; }}
    QLabel#HomeStatus {{ color: {c["TEXT_MUTED"]}; }}
    /* A Dashboard number, and a Dashboard tile that holds words instead. */
    QLabel#StatValue {{ font-size: {SIZE_STAT}px; font-weight: 600; }}
    QLabel#StatText {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}
    /* The Dashboard's second row of numbers: one strip, a step down. */
    QLabel#StatSmall {{ font-size: {SIZE_HEADING}px; font-weight: 600; }}
    /* A tip's number on Posting tips, in the title serif and gold: detail
       that lets the eye run down the list, never a status. */
    QLabel#TipNumber {{
        font-family: "{FONT_DISPLAY}";
        font-size: {SIZE_DISPLAY}px;
        font-weight: 400;
        color: {c["GOLD"]};
    }}
    /* A setup step on Welcome: its number, and its words by its state. The
       ring of a step still to come is fainter than its words, which still
       have to be read. */
    QLabel#StepNumber {{ color: {c["TEXT_MUTED"]}; font-weight: 600; }}
    QLabel#StepWords[state="current"] {{ color: {c["ACCENT_TEXT"]}; font-weight: 600; }}
    QLabel#StepWords[state="todo"] {{ color: {c["TEXT_MUTED"]}; }}
    /* Preview is a window onto Facebook, so everything in it is drawn in
       Facebook's own colours: the feed's grey ground filling the whole box,
       and the post on it, as a computer and as a phone show it. */
    QWidget#PreviewCanvas {{
        background: {c["FB_FEED"]};
        border-radius: {RADIUS_L - 1}px;
    }}
    QLabel#PreviewNote, QLabel#PreviewCaption, QLabel#FbMeta, QLabel#FbMuted {{
        color: {c["FB_MUTED"]};
    }}
    QLabel#PreviewNote, QLabel#PreviewCaption, QLabel#FbMeta {{
        font-size: {SIZE_SMALL}px;
    }}
    QLabel#PreviewCaption {{ font-weight: 600; }}
    QFrame#FbPost {{
        background: {c["FB_CARD"]};
        border: 1px solid {c["FB_BORDER"]};
        border-radius: {RADIUS}px;
    }}
    QFrame#FbPost[phone="true"] {{ border-radius: 0px; }}
    QLabel#FbText, QLabel#FbName {{ color: {c["FB_TEXT"]}; }}
    QLabel#FbName {{ font-weight: 600; }}
    QLabel#FbMore {{ color: {c["FB_MUTED"]}; font-size: {SIZE_HEADING}px; }}
    QLabel#FbAction {{ color: {c["FB_MUTED"]}; font-weight: 600; }}
    QLabel#FbAction[phone="true"] {{ font-size: {SIZE_SMALL}px; }}
    QFrame#FbDivider {{ background: {c["FB_DIVIDER"]}; border: none; }}

    /* A status: what happened to a post, or whether a repeating post is on.
       It used to be coloured words, the same size and weight as the grey
       "Cancel" and "Delete" beside it, so a status and a button looked alike.
       A tinted capsule is plainly a label, and nothing to click. */
    QLabel#Badge {{
        font-size: {SIZE_SMALL}px;
        font-weight: 600;
        padding: 2px 8px;
        border-radius: 10px;
        color: {c["TEXT_MUTED"]};
        background: {c["NEUTRAL_SOFT"]};
    }}
    {badge_tones}

    /* Buttons. Every kind states each moment it can be in -- rest, hover,
       pressed, disabled -- because a button that does not change as it is
       pressed feels as if the click did not land. The :pressed rules sit at
       the end of the block so they win over :hover and :focus, which are
       both true at the moment of a click. */
    /* A plain button is a white tile with a firmer lower edge, so it stands
       a little off the page like a key, not a flat outline (2026-10-21). */
    QPushButton {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-bottom-color: {c["CONTROL_STROKE"]};
        border-radius: {RADIUS_S}px;
        padding: 7px 14px;
        min-height: {CONTROL_HEIGHT - 16}px;
        color: {c["TEXT"]};
    }}
    QPushButton:hover {{
        background: {c["FIELD_HOVER"]};
        border-color: {c["BORDER_STRONG"]};
        border-bottom-color: {c["FIELD_STROKE"]};
    }}
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
    /* The sidebar is navy, so everything on it takes the
       SIDEBAR_ colours, and the screen you are on is lit in gold. */
    QFrame#Sidebar QFrame#Divider {{ background: {c["SIDEBAR_BORDER"]}; }}
    QPushButton#Nav {{
        background: transparent;
        border: none;
        text-align: left;
        padding: 10px 12px;
        min-height: {CONTROL_HEIGHT - 14}px;
        color: {c["SIDEBAR_TEXT"]};
    }}
    QPushButton#Nav:hover {{ background: {c["SIDEBAR_HOVER"]}; }}
    QPushButton#Nav:checked {{
        background: {c["SIDEBAR_ACTIVE_BG"]};
        color: {c["SIDEBAR_ACTIVE"]};
        font-weight: 600;
    }}
    /* "Check connection" and the repair button under it: outlined in the
       sidebar's own border, never filled, so they stay quieter than the
       screen's one filled button. */
    QPushButton#SidebarButton {{
        background: transparent;
        border: 1px solid {c["SIDEBAR_BORDER"]};
        color: {c["SIDEBAR_TEXT"]};
    }}
    QPushButton#SidebarButton:hover {{
        background: {c["SIDEBAR_HOVER"]};
        border-color: {c["SIDEBAR_MUTED"]};
    }}
    QPushButton#SidebarButton:disabled {{
        background: transparent;
        border-color: {c["SIDEBAR_BORDER"]};
        color: {c["SIDEBAR_MUTED"]};
    }}
    /* "Posting is paused": shown only while it is true. Warning-coloured
       text on the sidebar, laid out like a nav item so it reads as somewhere
       to go (Settings) rather than as a button that acts. */
    QPushButton#PausedNotice {{
        background: transparent;
        border: none;
        text-align: left;
        padding: 10px 12px;
        color: {c["SIDEBAR_WARNING"]};
        font-weight: 600;
    }}
    QPushButton#PausedNotice:hover {{ background: {c["SIDEBAR_HOVER"]}; }}
    /* The logo at the top of the sidebar leads Home, the way a website's
       does. Drawn like the label it replaced: no border, no fill at rest.
       The name is set in the title serif, like a wordmark. */
    QPushButton#Brand {{
        background: transparent;
        border: none;
        text-align: left;
        padding: 4px;
        color: {c["SIDEBAR_TEXT"]};
        font-family: "{FONT_DISPLAY}";
        font-size: {SIZE_BRAND}px;
        font-weight: 400;
    }}
    QPushButton#Brand:hover {{ background: {c["SIDEBAR_HOVER"]}; }}
    /* The wording tabs on Compose, one per group. The chosen one is tinted
       champagne and outlined in gold rather than filled: a filled one stood
       beside the screen's filled "Next" button as a second call to action,
       and only "Next" is one. */
    QPushButton#Tab {{ padding: 6px 12px; }}
    QPushButton#Tab:checked {{
        background: {c["NAV_ACTIVE_BG"]};
        border-color: {c["GOLD"]};
        color: {c["ACCENT_TEXT"]};
        font-weight: 600;
    }}
    QPushButton#Tab:checked:hover {{ border-color: {c["ACCENT"]}; }}
    /* The step bar of Create a post. Drawn like a tab, but the step you are
       on is not set bold: bold is wider than the width the button was sized
       for, and cut "Choose the groups" short. */
    QPushButton#Step {{ padding: 6px 12px; }}
    QPushButton#Step:checked {{
        background: {c["NAV_ACTIVE_BG"]};
        border-color: {c["GOLD"]};
        color: {c["ACCENT_TEXT"]};
    }}

    /* A joined toggle (widgets.segmented): one choice out of two or three,
       drawn as one control the way phones and Windows draw it. The chosen
       segment is raised out of a sunken track. The weight does not change
       with the choice, because bold text is wider and the segments would
       shift sideways under the pointer. */
    QFrame#Segmented {{
        background: {c["SEGMENT_TRACK"]};
        border: none;
        border-radius: {RADIUS}px;
    }}
    QPushButton#Segment {{
        background: transparent;
        border: 1px solid transparent;
        border-radius: {RADIUS_S}px;
        padding: 5px 14px;
        min-height: {CONTROL_HEIGHT - 6 - 12}px;
        color: {c["TEXT_MUTED"]};
    }}
    QPushButton#Segment:hover {{ color: {c["TEXT"]}; }}
    QPushButton#Segment:checked {{
        background: {c["SEGMENT_ON"]};
        border-color: {c["BORDER"]};
        border-bottom-color: {c["CONTROL_STROKE"]};
        color: {c["TEXT"]};
    }}
    QPushButton#Segment:focus {{ border: 2px solid {c["ACCENT"]}; padding: 4px 13px; }}
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
    /* "Remove" on a group's row is veiled until the pointer is on the row
       (groups.GroupRow, 2026-10-24): fifty of them at once were the loudest
       thing in the list. Tab still reaches it, and focus shows it. Before
       :hover, so reaching for the button itself still turns it red. */
    QPushButton#Link[veiled="true"] {{ color: transparent; }}
    QPushButton#Link:hover {{ background: {c["DANGER_SOFT"]}; color: {c["DANGER"]}; }}
    QPushButton#Link:focus {{ border: 2px solid {c["ACCENT"]}; padding: 5px 8px; }}
    QPushButton#Link[veiled="true"]:focus {{ color: {c["TEXT_MUTED"]}; }}
    /* "Back" in the flow: words, no box, so the one filled button beside or
       above it stands alone (the user's choice, 2026-10-20). Grey like a
       caption until the pointer reaches it. */
    QPushButton#BackLink {{
        background: transparent;
        border: none;
        color: {c["TEXT_MUTED"]};
        padding: 7px 10px;
    }}
    QPushButton#BackLink:hover {{ background: {c["HOVER_BG"]}; color: {c["TEXT"]}; }}
    QPushButton#BackLink:focus {{ border: 2px solid {c["ACCENT"]}; padding: 5px 8px; }}
    /* A button that opens a short list: "Load template" on step 1
       (2026-10-24). The chevron is the drop-down's, so it reads as one. */
    QPushButton#MenuButton {{ padding: 7px {MENU_ROOM}px 7px 14px; }}
    QPushButton#MenuButton:focus {{ padding: 6px {MENU_ROOM - 1}px 6px 13px; }}
    QPushButton#MenuButton::menu-indicator {{
        image: url({icon("chevron-down")});
        subcontrol-origin: padding;
        subcontrol-position: center right;
        right: 10px;
        width: {ARROW_SIZE}px;
        height: {ARROW_SIZE}px;
    }}
    QMenu {{
        background: {c["SURFACE"]};
        color: {c["TEXT"]};
        border: 1px solid {c["BORDER"]};
        padding: 4px;
    }}
    QMenu::item {{ padding: 8px 24px 8px 12px; border-radius: {RADIUS_S}px; }}
    QMenu::item:selected {{ background: {c["NAV_ACTIVE_BG"]}; color: {c["TEXT"]}; }}
    QMenu::item:disabled {{ color: {c["TEXT_MUTED"]}; }}
    /* An attached picture on step 1, and the round button that takes it off
       (compose.Thumbnail, 2026-10-24). Dark on the picture, as a photo
       app's own controls are, whatever the picture's colours. */
    QPushButton#ThumbRemove {{
        background: rgba(22, 29, 43, 190);
        border: none;
        border-radius: {THUMB_REMOVE // 2}px;
        padding: 0px;
        min-height: 0px;
    }}
    QPushButton#ThumbRemove:hover {{ background: rgba(22, 29, 43, 235); }}
    QPushButton#ThumbRemove:focus {{ border: 2px solid {c["TEXT_ON_ACCENT"]}; padding: 0px; }}
    QPushButton#ThumbRemove:pressed {{ background: {c["ACCENT_PRESSED"]}; }}
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
    /* On the navy sidebar the ring is gold: navy on navy is no ring. */
    QPushButton#Nav:focus, QPushButton#PausedNotice:focus {{
        border: 2px solid {c["SIDEBAR_ACTIVE"]};
        padding: 9px 11px;
    }}
    QPushButton#Brand:focus {{ border: 2px solid {c["SIDEBAR_ACTIVE"]}; padding: 2px; }}
    QPushButton#SidebarButton:focus {{
        border: 2px solid {c["SIDEBAR_ACTIVE"]};
        padding: 6px 13px;
    }}
    /* Home's one call to action, a size up from every other Primary. */
    QPushButton#Primary[hero="true"] {{ font-size: {SIZE_HEADING}px; padding: 14px 40px; }}
    QPushButton#Primary[hero="true"]:focus {{ padding: 13px 39px; }}
    QCheckBox:focus {{ color: {c["ACCENT_TEXT"]}; font-weight: 600; }}

    QPushButton:pressed {{ background: {c["PRESSED_BG"]}; border-color: {c["BORDER_STRONG"]}; }}
    QPushButton#Primary:pressed {{ background: {c["ACCENT_PRESSED"]}; }}
    QPushButton#Nav:pressed {{ background: {c["SIDEBAR_PRESSED"]}; }}
    QPushButton#Brand:pressed {{ background: {c["SIDEBAR_PRESSED"]}; }}
    QPushButton#SidebarButton:pressed {{
        background: {c["SIDEBAR_PRESSED"]};
        border-color: {c["SIDEBAR_MUTED"]};
    }}
    /* For the moment of the press the warning goes plain, as it had to on
       the old pressed grey. */
    QPushButton#PausedNotice:pressed {{
        background: {c["SIDEBAR_PRESSED"]};
        color: {c["SIDEBAR_TEXT"]};
    }}
    QPushButton#Step:checked:pressed, QPushButton#Tab:checked:pressed {{
        background: {c["PRESSED_BG"]};
        border-color: {c["ACCENT_PRESSED"]};
    }}
    QPushButton#Segment:pressed {{ background: {c["PRESSED_BG"]}; color: {c["TEXT"]}; }}
    QPushButton#Link:pressed {{ background: {c["DANGER_PRESSED_BG"]}; color: {c["DANGER"]}; }}
    QPushButton#BackLink:pressed {{ background: {c["PRESSED_BG"]}; color: {c["TEXT"]}; }}
    QPushButton#Danger:pressed {{ background: {c["DANGER_PRESSED_BG"]}; }}

    /* The status banner under the screen. The bar at its edge says what kind
       of message it is; the words stay in the body colour, because a warning
       written in orange or an error in red is harder to read, not easier.
       The level is a dynamic property, re-polished whenever it changes. */
    QFrame#Toast {{
        background: {c["SURFACE"]};
        border: 1px solid {c["BORDER"]};
        border-radius: {RADIUS_L}px;
    }}
    QFrame#ToastBar {{ background: {c["ACCENT"]}; border: none; border-radius: 2px; }}
    QFrame#ToastBar[level="success"] {{ background: {c["SUCCESS"]}; }}
    QFrame#ToastBar[level="warning"] {{ background: {c["WARNING"]}; }}
    QFrame#ToastBar[level="error"] {{ background: {c["DANGER"]}; }}
    QLabel#ToastText {{ color: {c["TEXT"]}; }}
    QPushButton#ToastAction {{
        background: transparent;
        border: none;
        color: {c["ACCENT_TEXT"]};
        font-weight: 600;
        padding: 7px 12px;
    }}
    QPushButton#ToastAction:hover {{ background: {c["HOVER_BG"]}; }}
    QPushButton#ToastAction:focus {{ border: 2px solid {c["ACCENT"]}; padding: 5px 10px; }}
    QPushButton#ToastAction:pressed {{ background: {c["PRESSED_BG"]}; }}
    QPushButton#ToastClose {{
        background: transparent;
        border: none;
        color: {c["TEXT_MUTED"]};
        font-size: {SIZE_HEADING}px;
        padding: 4px 10px;
        min-height: 0px;
    }}
    QPushButton#ToastClose:hover {{ background: {c["HOVER_BG"]}; color: {c["TEXT"]}; }}
    QPushButton#ToastClose:focus {{ border: 2px solid {c["ACCENT"]}; padding: 2px 8px; }}
    QPushButton#ToastClose:pressed {{ background: {c["PRESSED_BG"]}; color: {c["TEXT"]}; }}

    /* A typing box or a list to pick from, drawn as Windows 11 draws its own
       (2026-10-21): faint sides and a firm line along the bottom, where the
       words sit. In focus that line turns navy and thickens, and the sides
       stay put. They were a pale outline the same as every panel's, so a box
       inside a panel was hard to find at all. */
    QTextEdit, QLineEdit, QDateTimeEdit, QComboBox, QSpinBox {{
        background: {c["SURFACE"]};
        border: 1px solid {c["FIELD_BORDER"]};
        border-bottom-color: {c["FIELD_STROKE"]};
        border-radius: {RADIUS_S}px;
        padding: 8px 10px;
        min-height: {CONTROL_HEIGHT - 18}px;
        selection-background-color: {c["ACCENT"]};
        selection-color: {c["TEXT_ON_ACCENT"]};
        placeholder-text-color: {c["PLACEHOLDER"]};
    }}
    QTextEdit:hover, QLineEdit:hover, QDateTimeEdit:hover, QComboBox:hover, QSpinBox:hover {{
        background: {c["FIELD_HOVER"]};
    }}
    QTextEdit:focus, QLineEdit:focus, QDateTimeEdit:focus,
    QComboBox:focus, QSpinBox:focus {{
        background: {c["SURFACE"]};
        border-bottom: 2px solid {c["ACCENT"]};
        padding-bottom: 7px;
    }}
    QTextEdit#Editor, QTextEdit#Editor:hover, QTextEdit#Editor:focus {{
        background: {c["SURFACE"]};
        border: none;
        padding: 8px;
        font-size: {SIZE_BODY + 1}px;
    }}

    /* Step buttons. Left to the platform they were two small arrows, and the
       text box -- sized by the padding above, which knew nothing of them --
       lay across the left half of "up", so half of it did nothing at all.
       The right padding now reserves their room, and they are drawn here:
       side by side, nearly full height, decrease then increase. */
    QAbstractSpinBox {{ padding-right: {STEPPER_ROOM}px; }}
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
    /* A time of day: the hour and minute lists in one box, drawn like any
       typing box (publish.TimePicker). Each half is tinted under the pointer
       and in keyboard focus, since it has no border of its own to show it. */
    QFrame#TimeField {{
        background: {c["SURFACE"]};
        border: 1px solid {c["FIELD_BORDER"]};
        border-bottom-color: {c["FIELD_STROKE"]};
        border-radius: {RADIUS_S}px;
    }}
    QComboBox#TimePart {{
        background: transparent;
        border: none;
        border-radius: {RADIUS_S - 1}px;
        padding: 7px {DROPDOWN_ROOM}px 7px 10px;
    }}
    QComboBox#TimePart:hover {{ background: {c["FIELD_HOVER"]}; }}
    QComboBox#TimePart:focus {{ background: {c["NAV_ACTIVE_BG"]}; border: none; padding-bottom: 7px; }}
    QLabel#TimeColon {{ color: {c["TEXT_MUTED"]}; font-weight: 600; padding: 0 2px; }}
    /* A time already gone, in the one-off picker's hour and minute lists:
       greyed like a past day in the calendar, and not choosable. */
    QComboBox QAbstractItemView::item:disabled {{ color: {c["BORDER_STRONG"]}; }}

    /* The date picker's calendar. Unstyled it was the platform's own: red
       weekends (Saturday and Sunday, which is not even Israel's weekend), a
       grey header, and a next-month arrow that could vanish. */
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
       get the same colour from DateEntry.dim_past_days. */
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
    /* The strip of wording tabs on Compose scrolls sideways once there are
       more groups than fit. Its bar is a thin line: at full size it read as
       a beige slab under the tabs. */
    QScrollArea#TabStrip QScrollBar:horizontal, QScrollArea#ThumbStrip QScrollBar:horizontal {{
        height: 4px;
    }}
    QScrollArea#TabStrip QScrollBar::handle:horizontal,
    QScrollArea#ThumbStrip QScrollBar::handle:horizontal {{ border-radius: 2px; }}
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
        image: url({check_icon()});
    }}
    QCheckBox::indicator:checked:hover {{
        background: {c["ACCENT_HOVER"]};
        border-color: {c["ACCENT_HOVER"]};
    }}
    QCheckBox::indicator:checked:pressed {{
        background: {c["ACCENT_PRESSED"]};
        border-color: {c["ACCENT_PRESSED"]};
    }}
    /* Ticked by a group set, not by hand: greyed, because it is not this
       box's to untick. Unpicking the set is. After :hover, so it wins. */
    QCheckBox::indicator:checked:disabled {{
        background: {c["TEXT_MUTED"]};
        border-color: {c["TEXT_MUTED"]};
    }}
    """
