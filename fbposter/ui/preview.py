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

from . import theme

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
        """The body text as rendered, for tests and for debugging."""
        return self._wrapped[0].cget("text") if self._wrapped else ""

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

        label = ctk.CTkLabel(
            post,
            text=body if not muted else NO_TEXT,
            font=ctk.CTkFont(
                family=theme.FONT_FAMILY,
                size=theme.SIZE_BODY,
                slant="italic" if muted else "roman",
            ),
            text_color=theme.TEXT_MUTED if muted else theme.TEXT,
            anchor="w",
            justify="left",
            wraplength=self._wrap_width,
        )
        label.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_S, theme.PAD_M))
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
        width = self._available_width()
        if abs(width - self._wrap_width) < 8:
            return
        self._wrap_width = width
        for label in self._wrapped:
            label.configure(wraplength=width)
