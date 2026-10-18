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
    # Sliders: the rules it posts by.
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
}

# The app's own icon, the one on the installer and the taskbar: a white paper
# plane on a blue tile. Its colours are fixed, like any logo.
BRAND = (
    '<rect x="1" y="1" width="22" height="22" rx="6" fill="{accent}"/>'
    '<path d="M18.2 6 5.6 10.7l4.7 1.6z" fill="#FFFFFF"/>'
    '<path d="M18.2 6 11.1 13.1l1.9 5.2z" fill="#FFFFFF" fill-opacity="0.85"/>'
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


def brand(size: int = 24) -> QPixmap:
    accent = theme.C["ACCENT"]

    def document(px: int, _width: int) -> bytes:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{px}" height="{px}" '
            f'viewBox="0 0 24 24">{BRAND.replace("{accent}", accent)}</svg>'
        ).encode()

    return _render(document, size)


def nav_icon(name: str) -> QIcon:
    """A sidebar icon: muted at rest, the accent while its screen is open.

    A checkable button draws its icon's On state while checked, so the icon
    changes colour with the label and nothing has to keep the two in step.
    """
    icon = QIcon()
    for colour, state in (("TEXT_MUTED", QIcon.Off), ("ACCENT_TEXT", QIcon.On)):
        drawn = pixmap(name, theme.C[colour], NAV_ICON_SIZE, NAV_ICON_BOX.width())
        icon.addPixmap(drawn, QIcon.Normal, state)
    return icon
