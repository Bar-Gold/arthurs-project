"""Groups screen: the list of Facebook groups available to post to."""

from __future__ import annotations

import customtkinter as ctk

from fbposter import clock
from fbposter.db.models import Group
from fbposter.errors import DuplicateGroup, InvalidGroupURL

from .. import theme
from .base import View, card, phase_note


def format_last_posted(group: Group) -> str:
    if group.last_posted_at is None:
        return "Last posted: never"
    return f"Last posted: {clock.format_local(group.last_posted_at)}"


class GroupsView(View):
    title = "Groups"
    subtitle = "Groups you can post to. Admin groups do not belong here — Facebook schedules those natively."

    def build(self) -> None:
        self.groups: list[Group] = []

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
            self.body,
            "Cooldown is the minimum gap before this group can be posted to again. "
            "Lower it for large, active groups — but the same text is never sent twice.",
        )

        self.note.pack(side="bottom", anchor="w", pady=(theme.PAD_S, 0))
        self.list_frame.pack(side="top", fill="both", expand=True, pady=(theme.PAD_M, 0))

        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    # -- data --------------------------------------------------------------
    @property
    def repo(self):
        return self.app.group_repo

    def refresh(self) -> None:
        self.groups = self.repo.list()
        self._render()

    def add_group(self, raw: str | None = None) -> bool:
        """Validate and store a group URL. Returns True if it was added."""
        text = raw if raw is not None else self.url_entry.get()
        try:
            group = self.repo.add_from_url(text)
        except InvalidGroupURL:
            self.notify("That is not a Facebook group URL.", "error")
            return False
        except DuplicateGroup:
            self.notify("That group is already in the list.", "warning")
            return False

        self.url_entry.delete(0, "end")
        self.refresh()
        self.notify(f"Added {group.identifier}.", "success")
        return True

    def remove_group(self, group: Group) -> None:
        self.repo.remove(group.id)
        self.refresh()

    def set_cooldown(self, group: Group, raw: str) -> None:
        try:
            hours = int(raw.strip())
        except (TypeError, ValueError):
            self.notify("Cooldown must be a whole number of hours.", "error")
            self.refresh()
            return

        if hours < 0:
            self.notify("Cooldown cannot be negative.", "error")
            self.refresh()
            return

        self.repo.set_cooldown(group.id, hours)
        self.refresh()
        self.notify(f"{group.display_name}: cooldown set to {hours}h.", "success")

    # -- rendering ---------------------------------------------------------
    def _render(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()

        if not self.groups:
            phase_note(self.list_frame, "No groups yet. Paste a group URL above.").pack(
                anchor="w", pady=theme.PAD_S
            )
            return

        for group in self.groups:
            self._render_row(group)

    def _render_row(self, group: Group) -> None:
        row = card(self.list_frame)
        row.pack(fill="x", pady=(0, theme.PAD_XS))

        text_col = ctk.CTkFrame(row, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, padx=theme.PAD_M, pady=theme.PAD_S)

        ctk.CTkLabel(
            text_col,
            text=group.display_name,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(fill="x")

        ctk.CTkLabel(
            text_col,
            text=format_last_posted(group),
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
            command=lambda g=group: self.remove_group(g),
        ).pack(side="right", padx=(0, theme.PAD_M))

        cooldown_box = ctk.CTkFrame(row, fg_color="transparent")
        cooldown_box.pack(side="right", padx=theme.PAD_S)

        ctk.CTkLabel(
            cooldown_box,
            text="cooldown h",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
        ).pack(side="left", padx=(0, theme.PAD_XS))

        entry = ctk.CTkEntry(
            cooldown_box,
            width=52,
            height=26,
            justify="center",
            border_color=theme.BORDER,
            fg_color=theme.SURFACE,
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
        )
        entry.insert(0, str(group.cooldown_hours))
        entry.pack(side="left")
        entry.bind("<Return>", lambda _e, g=group, w=entry: self.set_cooldown(g, w.get()))
        entry.bind("<FocusOut>", lambda _e, g=group, w=entry: self._maybe_save(g, w.get()))

    def _maybe_save(self, group: Group, raw: str) -> None:
        """Only write on focus-out if the value actually changed.

        Re-rendering on every focus change would destroy the widget the user is
        tabbing through.
        """
        if raw.strip() != str(group.cooldown_hours):
            self.set_cooldown(group, raw)
