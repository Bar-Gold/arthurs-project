"""Shared scaffolding for the content-area screens."""

from __future__ import annotations

import customtkinter as ctk

from .. import theme


class View(ctk.CTkFrame):
    """A screen with a heading, a subtitle and a body frame to fill in.

    Subclasses set `title`/`subtitle` and implement build().
    """

    title = ""
    subtitle = ""

    def __init__(self, master: ctk.CTkBaseClass, app=None, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self.app = app

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.PAD_XL, pady=(theme.PAD_XL, theme.PAD_M))

        ctk.CTkLabel(
            header,
            text=self.title,
            font=ctk.CTkFont(
                family=theme.FONT_FAMILY, size=theme.SIZE_TITLE, weight="bold"
            ),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(fill="x")

        if self.subtitle:
            ctk.CTkLabel(
                header,
                text=self.subtitle,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=theme.TEXT_MUTED,
                anchor="w",
                justify="left",
            ).pack(fill="x", pady=(theme.PAD_XS, 0))

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=theme.PAD_XL, pady=(0, theme.PAD_XL))

        self.build()

    def build(self) -> None:  # pragma: no cover - overridden by every subclass
        raise NotImplementedError

    def notify(self, message: str, level: str = "info") -> None:
        """Send a message to the app's toast area, if there is one."""
        if self.app is not None and hasattr(self.app, "toast"):
            self.app.toast.show(message, level)


def card(master: ctk.CTkBaseClass, **kwargs) -> ctk.CTkFrame:
    """A surface panel; the repeated container throughout the app."""
    return ctk.CTkFrame(
        master,
        fg_color=theme.SURFACE,
        corner_radius=theme.RADIUS,
        border_width=1,
        border_color=theme.BORDER,
        **kwargs,
    )


def phase_note(master: ctk.CTkBaseClass, text: str) -> ctk.CTkLabel:
    """A muted line marking something that a later phase delivers.

    Used instead of hiding the control, so the shell shows the shape of the
    finished app without pretending the feature already works.
    """
    return ctk.CTkLabel(
        master,
        text=text,
        font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL, slant="italic"),
        text_color=theme.TEXT_MUTED,
        anchor="w",
        justify="left",
    )
