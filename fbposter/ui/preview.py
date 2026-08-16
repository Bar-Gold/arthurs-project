"""A mock-up of the post as the group will see it.

Deliberately generic: a heading, the text, and the images. It is not a replica
of Facebook's chrome and does not pretend to know the account's avatar -- the
question it answers is "does my wording and my image look right", which needs
neither.

It matters most with per-group wording, where five groups get five different
posts and the only way to check one before it goes out is to read it back.
"""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk

from . import textdir, theme

# Images are capped rather than fitted to the panel: the left column is at
# least ~520px at the minimum window size, so a 400px thumbnail never overflows
# and never has to be re-scaled on resize.
IMAGE_MAX_WIDTH = 400
IMAGE_MAX_HEIGHT = 300

# Shown when an image cannot be read (or Pillow is missing).
PLACEHOLDER_SIZE = (220, 120)

# Used until the widget has been mapped and has a real width to measure.
FALLBACK_WRAP = 420
MIN_WRAP = 200
# Between the panel edge and the text sit the scrollbar, the card's margin and
# the card's inner padding, so the text wraps well short of the panel width.
WRAP_INSET = 2 * theme.PAD_M + 2 * theme.PAD_XS + 24

EMPTY_TEXT = "Nothing to preview yet — write something on the Write tab."
NO_TEXT = "(no text — the post would be images only)"

# Decoding a JPEG on every tab switch is wasteful; keyed on mtime so replacing
# a file on disk still shows the new one.
_THUMBNAIL_CACHE: dict[tuple[str, float, int, int], "ctk.CTkImage"] = {}
_CACHE_LIMIT = 24


def scaled_size(
    size: tuple[int, int], max_size: tuple[int, int] = (IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT)
) -> tuple[int, int]:
    """Fit `size` inside `max_size`, keeping the aspect ratio and never growing it."""
    width, height = size
    if width <= 0 or height <= 0:
        return (1, 1)
    factor = min(max_size[0] / width, max_size[1] / height, 1.0)
    return (max(1, round(width * factor)), max(1, round(height * factor)))


def wrap_to_width(line: str, font, width: int) -> list[str]:
    """Break one logical line into display lines that fit, greedy on spaces.

    Tk would do this itself, but only before the line is reordered for display,
    and reordered text cannot be wrapped correctly. So the wrap happens here
    while the text is still in logical order.

    A single word wider than the panel is left to overflow rather than being
    cut: the preview is for reading the wording back, and a chopped word would
    misrepresent it.
    """
    if not line:
        return [""]

    lines: list[str] = []
    current = ""
    for word in line.split(" "):
        candidate = f"{current} {word}" if current else word
        if current and font.measure(candidate) > width:
            lines.append(current)
            current = word
        else:
            current = candidate
    lines.append(current)
    return lines


def load_thumbnail(
    path: Path, max_size: tuple[int, int] = (IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT)
) -> ctk.CTkImage | None:
    """A scaled-down copy of an image, or None if it cannot be shown.

    Never raises. A deleted, corrupt or unsupported attachment is a preview
    inconvenience -- the compose screen must not fall over for it, and neither
    must a machine that installed the app without Pillow.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None

    key = (str(path), stamp, *max_size)
    cached = _THUMBNAIL_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        with Image.open(path) as image:
            image.load()
            size = scaled_size(image.size, max_size)
            thumbnail = image.convert("RGBA").resize(size, Image.LANCZOS)
    except (OSError, ValueError, MemoryError):
        return None

    result = ctk.CTkImage(light_image=thumbnail, dark_image=thumbnail, size=size)
    if len(_THUMBNAIL_CACHE) >= _CACHE_LIMIT:
        _THUMBNAIL_CACHE.clear()
    _THUMBNAIL_CACHE[key] = result
    return result


class PostPreview(ctk.CTkFrame):
    """Draws one post: heading, meta line, body text, attached images."""

    def __init__(self, master: ctk.CTkBaseClass, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)

        self._scroll = ctk.CTkScrollableFrame(
            self,
            fg_color="transparent",
            scrollbar_fg_color="transparent",
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.TEXT_MUTED,
        )
        self._scroll.pack(fill="both", expand=True)

        # CTkImages must outlive the render call or Tk drops the pixels.
        self._images: list[ctk.CTkImage] = []
        self._placeholders: list[Path] = []
        self._wrapped: list[ctk.CTkLabel] = []
        self._body_text = ""
        self._last_render: tuple | None = None
        self._wrap_width = FALLBACK_WRAP

        # The panel's width is set by its parent, so re-wrapping the text can
        # never feed back into another resize.
        self.bind("<Configure>", self._on_resize)

        self.render()

    # -- public ------------------------------------------------------------
    def render(
        self,
        heading: str = "",
        subheading: str = "",
        text: str = "",
        media: list[Path] | tuple[Path, ...] = (),
    ) -> None:
        for child in self._scroll.winfo_children():
            child.destroy()
        self._images = []
        self._placeholders = []
        self._wrapped = []
        self._body_text = ""
        # Kept so a resize can lay the post out again: the wrapping is done by
        # hand now, so it cannot be fixed up by reconfiguring a widget.
        self._last_render = (heading, subheading, text, list(media))
        self._wrap_width = self._available_width()

        if not text.strip() and not media:
            self._empty_note(EMPTY_TEXT)
            return

        post = ctk.CTkFrame(
            self._scroll,
            fg_color=theme.SURFACE,
            corner_radius=theme.RADIUS,
            border_width=1,
            border_color=theme.BORDER,
        )
        post.pack(fill="x", padx=theme.PAD_XS, pady=(0, theme.PAD_S))

        self._header(post, heading, subheading)
        self._body(post, text)
        for path in media:
            self._image(post, Path(path))

    @property
    def images(self) -> list[ctk.CTkImage]:
        """The thumbnails currently on screen; empty when none could be read."""
        return list(self._images)

    @property
    def placeholders(self) -> list[Path]:
        """Attachments drawn as a tile because they could not be read."""
        return list(self._placeholders)

    def text_shown(self) -> str:
        """The body text this is a preview of, for tests and for debugging.

        The post as written, not what the labels hold: those carry the
        reordered, wrapped form, which is a drawing detail and would be
        misleading to read back.
        """
        return self._body_text

    # -- pieces ------------------------------------------------------------
    def _header(self, post: ctk.CTkFrame, heading: str, subheading: str) -> None:
        if not heading and not subheading:
            return

        block = ctk.CTkFrame(post, fg_color="transparent")
        block.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, 0))

        if heading:
            ctk.CTkLabel(
                block,
                text=heading,
                font=ctk.CTkFont(
                    family=theme.FONT_FAMILY, size=theme.SIZE_BODY, weight="bold"
                ),
                text_color=theme.TEXT,
                anchor="w",
            ).pack(fill="x")

        if subheading:
            ctk.CTkLabel(
                block,
                text=subheading,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=theme.TEXT_MUTED,
                anchor="w",
            ).pack(fill="x")

    def _body(self, post: ctk.CTkFrame, text: str) -> None:
        body = text.rstrip()
        muted = not body.strip()

        block = ctk.CTkFrame(post, fg_color="transparent")
        block.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_S, theme.PAD_M))

        if muted:
            self._body_text = NO_TEXT
            self._line(block, NO_TEXT, textdir.LTR, muted=True)
            return

        self._body_text = body
        font = ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY)

        # Wrapping is done here rather than by Tk, because each display line is
        # reordered before it is drawn and Tk would otherwise be wrapping text
        # that had already been rearranged. Wrap in logical order, reorder each
        # resulting line: that is the order the two steps have to happen in.
        for line, direction in zip(body.split("\n"), textdir.line_directions(body)):
            for display_line in wrap_to_width(line, font, self._wrap_width):
                self._line(block, display_line, direction)

    def _line(
        self, block: ctk.CTkFrame, text: str, direction: str, muted: bool = False
    ) -> None:
        label = ctk.CTkLabel(
            block,
            # Reordered for display. Tk hands mixed Hebrew and English to
            # Windows a run at a time and gets the runs back in the wrong
            # order; this is the only place in the app that can put them right,
            # because it is the only place the text is not being edited.
            text=textdir.to_visual(text),
            font=ctk.CTkFont(
                family=theme.FONT_FAMILY,
                size=theme.SIZE_BODY,
                slant="italic" if muted else "roman",
            ),
            text_color=theme.TEXT_MUTED if muted else theme.TEXT,
            anchor=textdir.anchor_for(direction),
            justify=textdir.justify_for(direction),
            # Already wrapped by hand; letting Tk wrap again would break lines
            # in the middle of the reordered text.
            wraplength=0,
        )
        label.pack(fill="x")
        self._wrapped.append(label)

    def _image(self, post: ctk.CTkFrame, path: Path) -> None:
        thumbnail = load_thumbnail(path)
        if thumbnail is None:
            self._image_placeholder(post, path)
            return

        self._images.append(thumbnail)
        ctk.CTkLabel(post, text="", image=thumbnail).pack(
            padx=theme.PAD_M, pady=(0, theme.PAD_M)
        )

    def _image_placeholder(self, post: ctk.CTkFrame, path: Path) -> None:
        """Stand-in for an image that could not be read.

        Showing nothing would be worse: the user would think the attachment had
        been dropped, when in fact it is still going to be uploaded.
        """
        self._placeholders.append(path)
        width, height = PLACEHOLDER_SIZE
        tile = ctk.CTkFrame(
            post,
            width=width,
            height=height,
            fg_color=theme.WINDOW_BG,
            border_width=1,
            border_color=theme.BORDER,
            corner_radius=theme.RADIUS,
        )
        tile.pack(padx=theme.PAD_M, pady=(0, theme.PAD_M))
        tile.pack_propagate(False)

        ctk.CTkLabel(
            tile,
            text=f"{path.name}\n(no preview)",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
            justify="center",
            wraplength=width - theme.PAD_M,
        ).pack(expand=True)

    def _empty_note(self, message: str) -> None:
        ctk.CTkLabel(
            self._scroll,
            text=message,
            font=ctk.CTkFont(
                family=theme.FONT_FAMILY, size=theme.SIZE_SMALL, slant="italic"
            ),
            text_color=theme.TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=self._wrap_width,
        ).pack(anchor="w", padx=theme.PAD_XS, pady=theme.PAD_XS)

    # -- sizing ------------------------------------------------------------
    def _available_width(self) -> int:
        width = self.winfo_width()
        if width <= 1:  # not mapped yet
            width = FALLBACK_WRAP + WRAP_INSET
        return max(MIN_WRAP, width - WRAP_INSET)

    def _on_resize(self, _event=None) -> None:
        """Lay the post out again at the new width.

        A full re-render rather than a reconfigure, because the line breaks are
        chosen here rather than by Tk. The panel's width comes from its parent,
        so this cannot feed back into another resize, and the threshold keeps it
        off the path of the small jitter that arrives during layout.
        """
        width = self._available_width()
        if abs(width - self._wrap_width) < 8:
            return
        self._wrap_width = width
        if self._last_render is not None:
            self.render(*self._last_render)
