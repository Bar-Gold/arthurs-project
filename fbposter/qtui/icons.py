"""The window's own icons, drawn from SVG written here rather than shipped as files.

Every one is a line drawing on a 24-unit grid, the way system icon sets are,
so they sit at the same weight as the text beside them. They are strings, not
files, for two reasons. An SVG file cannot take its colour from the palette, so
each icon would exist once per colour per mode, as the chevrons do. And
PyInstaller does not collect an `.svg` on its own: a missing file is a blank
space in a frozen build and nothing anywhere says why (packaging/CLAUDE.md).

They are drawn by Qt's SVG image plugin, the one the stylesheet already
needs for its ticks and arrows. `PySide6.QtSvg` is not imported, so nothing new
has to reach the packaged build.
"""

from __future__ import annotations

from PySide6.QtCore import QSize
from PySide6.QtGui import QGuiApplication, QIcon, QPixmap

from . import theme

# Inner SVG for each icon. `stroke` takes the colour; a shape that is filled
# says so itself.
SHAPES = {
    # A house: the page the app opens on.
    "home": (
        '<path d="M3.5 11 12 4l8.5 7"/>'
        '<path d="M5.5 9.5V20h13V9.5"/>'
        '<path d="M10 20v-5.5h4V20"/>'
    ),
    # A pen with a plus: making a new post, from the first word to the time.
    "create": (
        '<path d="M12 20h8"/>'
        '<path d="M16.4 3.6a2 2 0 0 1 2.9 2.9L7.5 18.3 3.5 19.5l1.2-4z"/>'
        '<path d="M6 3v5M3.5 5.5h5"/>'
    ),
    # A pen over a line: writing the post.
    "compose": (
        '<path d="M12 20h8"/>'
        '<path d="M16.4 3.6a2 2 0 0 1 2.9 2.9L7.5 18.3 3.5 19.5l1.2-4z"/>'
    ),
    # Two people: the groups it goes to.
    "groups": (
        '<circle cx="9" cy="8" r="3.5"/>'
        '<path d="M2.5 20v-1a5 5 0 0 1 5-5h3a5 5 0 0 1 5 5v1"/>'
        '<path d="M16 4.6a3.5 3.5 0 0 1 0 6.8"/>'
        '<path d="M18.5 14.2a5 5 0 0 1 3 4.8v1"/>'
    ),
    # A paper plane: sending it.
    "publish": (
        '<path d="M21 3 10.5 13.5"/>'
        '<path d="M21 3 14.5 21l-4-7.5L3 9.5z"/>'
    ),
    # Bars on a baseline: how the posting is going.
    "dashboard": (
        '<path d="M3.5 20.5h17"/>'
        '<path d="M7 17v-5M12 17V6M17 17v-8"/>'
    ),
    # Three dots in a box: groups kept together as one set.
    "group_sets": (
        '<rect x="3" y="3" width="18" height="18" rx="4"/>'
        '<circle cx="8.5" cy="9.5" r="2"/>'
        '<circle cx="15.5" cy="9.5" r="2"/>'
        '<circle cx="12" cy="15.5" r="2"/>'
    ),
    # Two arrows chasing each other round: a post that comes round again.
    "repeating": (
        '<path d="m17 2 4 4-4 4"/>'
        '<path d="M3 11v-1a4 4 0 0 1 4-4h14"/>'
        '<path d="m7 22-4-4 4-4"/>'
        '<path d="M21 13v1a4 4 0 0 1-4 4H3"/>'
    ),
    # A light bulb: advice for posting safely.
    "tips": (
        '<path d="M9.5 18h5M10.5 21h3"/>'
        '<path d="M12 3a6 6 0 0 0-3.4 10.9c.6.5.9 1.1.9 1.9v.2h5v-.2c0-.8.3-1.4.9-1.9A6 6 0 0 0 12 3z"/>'
    ),
    # Sliders: pausing, the account, this PC.
    "settings": (
        '<path d="M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1"/>'
        '<circle cx="15" cy="6" r="2"/>'
        '<circle cx="9" cy="12" r="2"/>'
        '<circle cx="17" cy="18" r="2"/>'
    ),
    # Setup checklist: done, the step you are on, and one still to come.
    "done": (
        '<circle cx="12" cy="12" r="10" fill="{colour}" stroke="none"/>'
        '<path d="m7.5 12.3 3 3 6-6.3" stroke="#FFFFFF" stroke-width="2.2"/>'
    ),
    "current": (
        '<circle cx="12" cy="12" r="9"/>'
        '<circle cx="12" cy="12" r="4" fill="{colour}" stroke="none"/>'
    ),
    "todo": '<circle cx="12" cy="12" r="9"/>',
    # The connection light.
    "dot": '<circle cx="12" cy="12" r="6" fill="{colour}" stroke="none"/>',
    # Inside a box or on a button (2026-10-21): what the box or button is for.
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    # A cross: taking an attached picture off the post (2026-10-24).
    "close": '<path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    # A picture: attaching pictures or videos.
    "picture": (
        '<rect x="3" y="4.5" width="18" height="15" rx="3"/>'
        '<circle cx="8.5" cy="9.5" r="1.5"/>'
        '<path d="m21 15.5-4.5-4.5L7 19.5"/>'
    ),
    # An arrow into a tray: getting the groups from Facebook.
    "download": '<path d="M12 4v11"/><path d="m7.5 10.5 4.5 4.5 4.5-4.5"/><path d="M5 20h14"/>',
    # The preview's Like, Comment and Share (2026-10-22), drawn the way
    # Facebook draws its own: a thumb, a round speech bubble, a curved arrow.
    "like": (
        '<path d="M7.5 10.5v9"/>'
        '<path d="M7.5 10.5 11 4.2c1.6-.5 3 .7 2.6 2.4L13 9.5h5.3c1.4 0 2.4 1.3 '
        '2 2.6l-1.9 6.2c-.3.9-1.1 1.5-2 1.5H4.5c-.8 0-1.5-.7-1.5-1.5v-6.3c0-.8.7-1.5 '
        '1.5-1.5z"/>'
    ),
    "comment": (
        '<path d="M12 3.5c4.7 0 8.5 3.4 8.5 7.6s-3.8 7.6-8.5 7.6c-1.1 0-2.2-.2-3.2-.5'
        'L4 19.8l1.3-3.9C4.2 14.6 3.5 13 3.5 11.1c0-4.2 3.8-7.6 8.5-7.6z"/>'
    ),
    "share": '<path d="M13.5 4.5 20.5 11l-7 6.5v-3.8C9 13.7 5.6 15.2 3.5 19c.6-5.5 3.9-9.4 10-10.2z"/>',
}

# On a plain button, beside its words. Drawn wider than tall, with the
# drawing at its left, for the same reason as NAV_ICON_GAP: Qt's fixed 4px
# between an icon and its words crowded the two.
BUTTON_ICON_SIZE = 16
BUTTON_ICON_GAP = 4
BUTTON_ICON_BOX = QSize(BUTTON_ICON_SIZE + BUTTON_ICON_GAP, BUTTON_ICON_SIZE)

# The app's own icon: a navy paper plane on a gold tile (2026-10-20; it was
# white on blue, and the installer and taskbar icon still are).
BRAND = (
    '<rect x="1" y="1" width="22" height="22" rx="6" fill="{accent}"/>'
    '<path d="M18.2 6 5.6 10.7l4.7 1.6z" fill="{mark}"/>'
    '<path d="M18.2 6 11.1 13.1l1.9 5.2z" fill="{mark}" fill-opacity="0.85"/>'
)

NAV_ICON_SIZE = 18
# Room between a sidebar icon and its label. Qt puts a fixed 4px there, which
# crowded the two together, and a stylesheet cannot change it. So the icon is
# drawn this much wider than it is tall, with the drawing at its left edge.
NAV_ICON_GAP = 8
NAV_ICON_BOX = QSize(NAV_ICON_SIZE + NAV_ICON_GAP, NAV_ICON_SIZE)


def svg(name: str, colour: str, size: int, width: int | None = None) -> bytes:
    """One icon as an SVG document `size` pixels tall.

    `width`, when wider than `size`, leaves empty room to the right of it.
    """
    width = width or size
    inner = SHAPES[name].replace("{colour}", colour)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{size}" '
        f'viewBox="0 0 {24 * width / size:g} 24" fill="none" stroke="{colour}" '
        f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
        f'{inner}</svg>'
    ).encode()


def _scale() -> float:
    """The screen's pixels per point, so an icon is drawn sharp at 125% or 150%."""
    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen is not None else 1.0


def _render(document_for, size: int, width: int | None = None) -> QPixmap:
    """`document_for(height, width)` is drawn at the screen's own scale."""
    scale = _scale()
    pixmap = QPixmap()
    pixmap.loadFromData(
        document_for(round(size * scale), round((width or size) * scale)), "SVG"
    )
    pixmap.setDevicePixelRatio(scale)
    return pixmap


def pixmap(name: str, colour: str, size: int, width: int | None = None) -> QPixmap:
    """An icon in one colour, for a label."""
    return _render(lambda px, wide: svg(name, colour, px, wide), size, width)


def brand(size: int = 24, width: int | None = None) -> QPixmap:
    """The app's mark. `width`, when wider than `size`, leaves empty room to
    its right: the sidebar's logo button uses it for the gap before its name,
    which Qt otherwise fixes at 4px (see NAV_ICON_GAP)."""
    body = BRAND.replace("{accent}", theme.C["BRAND"]).replace("{mark}", theme.C["SIDEBAR_BG"])

    def document(px: int, wide: int) -> bytes:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{wide}" height="{px}" '
            f'viewBox="0 0 {24 * wide / px:g} 24">{body}</svg>'
        ).encode()

    return _render(document, size, width)


def group_picture(size: int = 40) -> QPixmap:
    """A group's picture in the preview: Facebook's grey tile with two people,
    which is what a group with no cover photo shows. The real one is not
    known to the app, and a made-up one would say something untrue."""
    body = (
        f'<rect x="0" y="0" width="24" height="24" rx="5" '
        f'fill="{theme.C["FB_GROUP_BG"]}" stroke="none"/>'
        f'<g transform="translate(5.5 5.5) scale(0.54)" stroke-width="2.2">'
        f'{SHAPES["groups"]}</g>'
    )

    def document(px: int, wide: int) -> bytes:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{wide}" height="{px}" '
            f'viewBox="0 0 24 24" fill="none" stroke="{theme.C["FB_MUTED"]}" '
            f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
        ).encode()

    return _render(document, size)


def set_button_icon(button, name: str) -> None:
    """Put an icon before a plain button's words: in the button's own text
    colour, and muted while the button is greyed out."""
    icon = QIcon()
    for colour, mode in (("TEXT", QIcon.Normal), ("TEXT_MUTED", QIcon.Disabled)):
        drawn = pixmap(name, theme.C[colour], BUTTON_ICON_SIZE, BUTTON_ICON_BOX.width())
        icon.addPixmap(drawn, mode)
    button.setIcon(icon)
    button.setIconSize(BUTTON_ICON_BOX)


def nav_icon(name: str) -> QIcon:
    """A sidebar icon: muted at rest, gold while its screen is open.

    A checkable button draws its icon's On state while checked, so the icon
    changes colour with the label and nothing has to keep the two in step.
    """
    icon = QIcon()
    for colour, state in (("SIDEBAR_MUTED", QIcon.Off), ("SIDEBAR_ACTIVE", QIcon.On)):
        drawn = pixmap(name, theme.C[colour], NAV_ICON_SIZE, NAV_ICON_BOX.width())
        icon.addPixmap(drawn, QIcon.Normal, state)
    return icon
