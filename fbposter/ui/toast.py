"""Passive in-window notifications.

Deliberately not a Toplevel and not tkinter.messagebox. Both create a real OS
window that can take focus, and the whole point of this app is that it never
interrupts whatever the user is doing. A toast is just a frame placed over the
content area that fades itself out on a timer.
"""

from __future__ import annotations

import customtkinter as ctk

from . import theme

DEFAULT_DURATION_MS = 4500

_LEVEL_COLORS: dict[str, theme.Color] = {
    "info": theme.NEUTRAL,
    "success": theme.SUCCESS,
    "warning": theme.WARNING,
    "error": theme.DANGER,
}


class ToastHost:
    """Owns the toast area for one parent widget."""

    def __init__(self, parent: ctk.CTkBaseClass) -> None:
        self._parent = parent
        self._current: ctk.CTkFrame | None = None
        self._after_id: str | None = None

    def show(
        self,
        message: str,
        level: str = "info",
        duration_ms: int = DEFAULT_DURATION_MS,
    ) -> ctk.CTkFrame:
        """Display a message. A new toast replaces any toast already showing."""
        self.clear()

        accent = _LEVEL_COLORS.get(level, theme.NEUTRAL)
        frame = ctk.CTkFrame(
            self._parent,
            fg_color=theme.SURFACE,
            border_color=accent,
            border_width=1,
            corner_radius=theme.RADIUS,
        )
        # A coloured spine rather than a coloured background: it reads as
        # status without shouting, and stays legible in both themes.
        # height=1 for the same reason as the queue rows: CTkFrame's 200px
        # default would otherwise set the height of the whole toast.
        spine = ctk.CTkFrame(frame, fg_color=accent, width=4, height=1, corner_radius=2)
        spine.pack(side="left", fill="y", padx=(theme.PAD_S, 0), pady=theme.PAD_S)

        ctk.CTkLabel(
            frame,
            text=message,
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            justify="left",
            wraplength=380,
        ).pack(side="left", padx=theme.PAD_M, pady=theme.PAD_S)

        # Anchored inside the parent -- never raised, never focused.
        frame.place(relx=1.0, rely=1.0, anchor="se", x=-theme.PAD_L, y=-theme.PAD_L)

        self._current = frame
        if duration_ms > 0:
            self._after_id = self._parent.after(duration_ms, self.clear)
        return frame

    def clear(self) -> None:
        if self._after_id is not None:
            try:
                self._parent.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

        if self._current is not None:
            try:
                self._current.destroy()
            except Exception:
                pass
            self._current = None

    @property
    def visible(self) -> bool:
        return self._current is not None
