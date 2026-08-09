"""Groups screen: the list of Facebook groups available to post to."""

from __future__ import annotations

import customtkinter as ctk

from fbposter.groups import GroupRef, parse_group_url

from .. import theme
from .base import View, card, phase_note


class GroupsView(View):
    title = "Groups"
    subtitle = "Groups you can post to. Admin groups do not belong here — Facebook schedules those natively."

    def build(self) -> None:
        self.groups: list[GroupRef] = []

        entry_row = ctk.CTkFrame(self.body, fg_color="transparent")
        entry_row.pack(fill="x")

        self.url_entry = ctk.CTkEntry(
            entry_row,
            placeholder_text="https://www.facebook.com/groups/...",
            height=36,
            corner_radius=theme.RADIUS,
            border_color=theme.BORDER,
            fg_color=theme.SURFACE,
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY),
        )
        self.url_entry.pack(side="left", fill="x", expand=True)
        self.url_entry.bind("<Return>", lambda _event: self.add_group())

        ctk.CTkButton(
            entry_row,
            text="Add",
            width=90,
            height=36,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT_ON_ACCENT,
            corner_radius=theme.RADIUS,
            command=self.add_group,
        ).pack(side="left", padx=(theme.PAD_S, 0))

        self.list_frame = ctk.CTkScrollableFrame(
            self.body, fg_color="transparent", label_text=""
        )
        self.note = phase_note(
            self.body, "Groups are held in memory for now; SQLite storage arrives in Phase 3."
        )

        # Fixed-height note first, so the expanding list cannot push it off-window.
        self.note.pack(side="bottom", anchor="w", pady=(theme.PAD_S, 0))
        self.list_frame.pack(side="top", fill="both", expand=True, pady=(theme.PAD_M, 0))

        self._render()

    # -- data --------------------------------------------------------------
    def add_group(self, raw: str | None = None) -> bool:
        """Validate and add a group URL. Returns True if it was added."""
        text = raw if raw is not None else self.url_entry.get()
        ref = parse_group_url(text)

        if ref is None:
            self.notify("That is not a Facebook group URL.", "error")
            return False

        if any(existing.identifier == ref.identifier for existing in self.groups):
            # Parsing to an identifier first is what makes this work even when
            # the same group is pasted in two different URL forms.
            self.notify("That group is already in the list.", "warning")
            return False

        self.groups.append(ref)
        self.url_entry.delete(0, "end")
        self._render()
        self.notify(f"Added {ref.identifier}.", "success")
        return True

    def remove_group(self, ref: GroupRef) -> None:
        self.groups = [g for g in self.groups if g.identifier != ref.identifier]
        self._render()

    # -- rendering ---------------------------------------------------------
    def _render(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()

        if not self.groups:
            phase_note(self.list_frame, "No groups yet. Paste a group URL above.").pack(
                anchor="w", pady=theme.PAD_S
            )
            return

        for ref in self.groups:
            row = card(self.list_frame)
            row.pack(fill="x", pady=(0, theme.PAD_XS))

            text_col = ctk.CTkFrame(row, fg_color="transparent")
            text_col.pack(side="left", fill="x", expand=True, padx=theme.PAD_M, pady=theme.PAD_S)

            ctk.CTkLabel(
                text_col,
                text=ref.identifier,
                font=ctk.CTkFont(
                    family=theme.FONT_FAMILY, size=theme.SIZE_BODY, weight="bold"
                ),
                text_color=theme.TEXT,
                anchor="w",
            ).pack(fill="x")

            ctk.CTkLabel(
                text_col,
                text="Last posted: never",
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=theme.TEXT_MUTED,
                anchor="w",
            ).pack(fill="x")

            ctk.CTkButton(
                row,
                text="Remove",
                width=70,
                height=26,
                fg_color="transparent",
                text_color=theme.DANGER,
                hover_color=theme.NAV_ACTIVE_BG,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                command=lambda r=ref: self.remove_group(r),
            ).pack(side="right", padx=theme.PAD_M)
