"""Compose screen: the post text and its attachments."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from .. import theme
from .base import View, card, phase_note

IMAGE_TYPES = [("Images", "*.jpg *.jpeg *.png *.gif *.webp"), ("All files", "*.*")]


class ComposeView(View):
    title = "Compose"
    subtitle = "Write the post, attach images, then pick the groups to send it to."

    def build(self) -> None:
        self.attachments: list[Path] = []

        editor = card(self.body)

        self.textbox = ctk.CTkTextbox(
            editor,
            wrap="word",
            fg_color="transparent",
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY),
            border_width=0,
        )
        self.textbox.pack(fill="both", expand=True, padx=theme.PAD_M, pady=theme.PAD_M)
        self.textbox.bind("<KeyRelease>", lambda _event: self._update_counter())

        footer = ctk.CTkFrame(editor, fg_color="transparent")
        footer.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

        self.counter = ctk.CTkLabel(
            footer,
            text="0 characters",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
        )
        self.counter.pack(side="left")

        # Content variation is the single highest-value protection in the app
        # (README section 7), so the reminder lives beside the text rather than
        # in a settings screen nobody opens.
        phase_note(
            footer,
            "Vary the wording between groups — repeated text is the main ban signal.",
        ).pack(side="right")

        # --- attachments ---
        attach_row = ctk.CTkFrame(self.body, fg_color="transparent")

        ctk.CTkButton(
            attach_row,
            text="Attach images",
            width=130,
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT_ON_ACCENT,
            corner_radius=theme.RADIUS,
            command=self._pick_files,
        ).pack(side="left")

        ctk.CTkButton(
            attach_row,
            text="Save as template",
            width=140,
            fg_color="transparent",
            border_width=1,
            border_color=theme.BORDER,
            text_color=theme.TEXT,
            hover_color=theme.NAV_ACTIVE_BG,
            corner_radius=theme.RADIUS,
            command=lambda: self.notify(
                "Templates are stored in SQLite, which arrives in Phase 3.", "info"
            ),
        ).pack(side="left", padx=(theme.PAD_S, 0))

        self.attachment_list = ctk.CTkFrame(self.body, fg_color="transparent")

        # Pack the fixed-height controls against the bottom edge first. If the
        # expanding editor is packed first it claims the whole frame and pushes
        # everything below it off the window.
        self.attachment_list.pack(side="bottom", fill="x", pady=(theme.PAD_S, 0))
        attach_row.pack(side="bottom", fill="x", pady=(theme.PAD_M, 0))
        editor.pack(side="top", fill="both", expand=True)

        self._render_attachments()

    # -- attachments -------------------------------------------------------
    def _pick_files(self) -> None:
        """Open the OS picker.

        This is the one modal dialog the app is allowed to open: it is started
        by the user clicking a button and can never appear while a batch is
        running. The rule it sits under bans the app *interrupting* the user,
        not the user choosing a file.
        """
        chosen = filedialog.askopenfilenames(title="Attach images", filetypes=IMAGE_TYPES)
        added = 0
        for name in chosen:
            path = Path(name)
            if path not in self.attachments:
                self.attachments.append(path)
                added += 1
        if added:
            self._render_attachments()
            self.notify(f"Attached {added} file{'s' if added != 1 else ''}.", "success")

    def _remove_attachment(self, path: Path) -> None:
        if path in self.attachments:
            self.attachments.remove(path)
            self._render_attachments()

    def _render_attachments(self) -> None:
        for child in self.attachment_list.winfo_children():
            child.destroy()

        if not self.attachments:
            phase_note(self.attachment_list, "No images attached.").pack(anchor="w")
            return

        for path in self.attachments:
            row = card(self.attachment_list)
            row.pack(fill="x", pady=(0, theme.PAD_XS))

            ctk.CTkLabel(
                row,
                text=path.name,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=theme.TEXT,
                anchor="w",
            ).pack(side="left", padx=theme.PAD_M, pady=theme.PAD_S)

            ctk.CTkButton(
                row,
                text="Remove",
                width=70,
                height=24,
                fg_color="transparent",
                text_color=theme.DANGER,
                hover_color=theme.NAV_ACTIVE_BG,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                command=lambda p=path: self._remove_attachment(p),
            ).pack(side="right", padx=theme.PAD_S)

    # -- text --------------------------------------------------------------
    def get_text(self) -> str:
        return self.textbox.get("1.0", "end-1c")

    def _update_counter(self) -> None:
        count = len(self.get_text())
        self.counter.configure(text=f"{count} character{'s' if count != 1 else ''}")
