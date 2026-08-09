"""Compose screen: the post text, its attachments, and where it goes."""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from fbposter.db.models import utcnow
from fbposter.guards import PlannedTarget, evaluate_batch

from .. import theme
from .base import View, card, phase_note

IMAGE_TYPES = [("Images", "*.jpg *.jpeg *.png *.gif *.webp"), ("All files", "*.*")]
SCHEDULE_FORMAT = "%Y-%m-%d %H:%M"
RIGHT_COLUMN_WIDTH = 330

POST_NOW = "Post now"
SCHEDULE = "Schedule"


def start_of_today_utc(now: datetime | None = None) -> datetime:
    """Midnight local time, expressed in UTC.

    The daily cap is a human "today", not a UTC one -- posts at 01:00 local
    belong to the day the user thinks they do.
    """
    local_now = (now or utcnow()).astimezone()
    midnight = datetime.combine(local_now.date(), time.min, tzinfo=local_now.tzinfo)
    return midnight.astimezone(timezone.utc)


def parse_schedule(raw: str) -> datetime:
    """Read a local 'YYYY-MM-DD HH:MM' and return it as UTC."""
    naive = datetime.strptime(raw.strip(), SCHEDULE_FORMAT)
    return naive.astimezone().astimezone(timezone.utc)


class ComposeView(View):
    title = "Compose"
    subtitle = "Write the post, attach images, then pick the groups to send it to."

    def build(self) -> None:
        self.attachments: list[Path] = []
        self._group_vars: dict[int, ctk.BooleanVar] = {}

        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_columnconfigure(1, minsize=RIGHT_COLUMN_WIDTH)
        self.body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(self.body, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.PAD_M))
        right = ctk.CTkFrame(self.body, fg_color="transparent", width=RIGHT_COLUMN_WIDTH)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_propagate(False)

        self._build_editor(left)
        self._build_sidebar(right)
        self._render_attachments()

    def on_show(self) -> None:
        self.refresh_groups()
        self.refresh_templates()

    # -- left column -------------------------------------------------------
    def _build_editor(self, parent: ctk.CTkFrame) -> None:
        editor = card(parent)

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

        # Content variation is the highest-value protection in the app
        # (README section 7), so the reminder sits beside the text.
        phase_note(footer, "Vary the wording between groups.").pack(side="right")

        attach_row = ctk.CTkFrame(parent, fg_color="transparent")
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

        self.attachment_list = ctk.CTkFrame(parent, fg_color="transparent")

        # Fixed-height controls first so the expanding editor cannot push them
        # off the bottom of the window.
        self.attachment_list.pack(side="bottom", fill="x", pady=(theme.PAD_S, 0))
        attach_row.pack(side="bottom", fill="x", pady=(theme.PAD_M, 0))
        editor.pack(side="top", fill="both", expand=True)

    # -- right column ------------------------------------------------------
    def _build_sidebar(self, parent: ctk.CTkFrame) -> None:
        small = ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL)

        # --- templates ---
        templates = card(parent)
        templates.pack(fill="x")
        ctk.CTkLabel(
            templates,
            text="Templates",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, theme.PAD_XS))

        picker_row = ctk.CTkFrame(templates, fg_color="transparent")
        picker_row.pack(fill="x", padx=theme.PAD_M)
        self.template_picker = ctk.CTkOptionMenu(
            picker_row, values=["(none saved)"], width=180, height=28, font=small,
            command=lambda _choice: None,
        )
        self.template_picker.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            picker_row, text="Load", width=60, height=28, font=small,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT, hover_color=theme.NAV_ACTIVE_BG,
            command=self.load_template,
        ).pack(side="left", padx=(theme.PAD_XS, 0))

        save_row = ctk.CTkFrame(templates, fg_color="transparent")
        save_row.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_XS, theme.PAD_M))
        self.template_name = ctk.CTkEntry(
            save_row, placeholder_text="Name to save as", height=28, font=small,
            border_color=theme.BORDER, fg_color=theme.SURFACE, text_color=theme.TEXT,
        )
        self.template_name.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(
            save_row, text="Save", width=60, height=28, font=small,
            fg_color="transparent", border_width=1, border_color=theme.BORDER,
            text_color=theme.TEXT, hover_color=theme.NAV_ACTIVE_BG,
            command=self.save_template,
        ).pack(side="left", padx=(theme.PAD_XS, 0))

        # --- schedule ---
        schedule = card(parent)
        schedule.pack(fill="x", pady=(theme.PAD_S, 0))
        self.schedule_mode = ctk.CTkSegmentedButton(
            schedule, values=[POST_NOW, SCHEDULE], font=small,
            command=lambda _v: self._sync_schedule_entry(),
        )
        self.schedule_mode.set(POST_NOW)
        self.schedule_mode.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, theme.PAD_XS))

        self.schedule_entry = ctk.CTkEntry(
            schedule, placeholder_text=SCHEDULE_FORMAT.replace("%", ""), height=28,
            font=small, border_color=theme.BORDER, fg_color=theme.SURFACE,
            text_color=theme.TEXT,
        )
        self.schedule_entry.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))
        self.schedule_entry.insert(
            0, (datetime.now() + timedelta(hours=1)).strftime(SCHEDULE_FORMAT)
        )
        self._sync_schedule_entry()

        # --- queue button, pinned to the bottom ---
        self.queue_button = ctk.CTkButton(
            parent, text="Add to queue", height=38, corner_radius=theme.RADIUS,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            text_color=theme.TEXT_ON_ACCENT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY, weight="bold"),
            command=self.add_to_queue,
        )
        self.queue_button.pack(side="bottom", fill="x", pady=(theme.PAD_S, 0))

        # --- groups (expands into whatever is left) ---
        groups_card = card(parent)
        ctk.CTkLabel(
            groups_card,
            text="Send to",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, 0))
        self.group_list = ctk.CTkScrollableFrame(groups_card, fg_color="transparent")
        self.group_list.pack(fill="both", expand=True, padx=theme.PAD_S, pady=theme.PAD_S)
        groups_card.pack(side="top", fill="both", expand=True, pady=(theme.PAD_S, 0))

    def _sync_schedule_entry(self) -> None:
        state = "normal" if self.schedule_mode.get() == SCHEDULE else "disabled"
        self.schedule_entry.configure(state=state)

    # -- templates ---------------------------------------------------------
    def refresh_templates(self) -> None:
        names = [t.name for t in self.app.template_repo.list()]
        self.template_picker.configure(values=names or ["(none saved)"])
        if names and self.template_picker.get() not in names:
            self.template_picker.set(names[0])
        elif not names:
            self.template_picker.set("(none saved)")

    def save_template(self) -> bool:
        name = self.template_name.get().strip()
        if not name:
            self.notify("Give the template a name first.", "error")
            return False

        body = self.get_text().strip()
        if not body:
            self.notify("Nothing to save — the post is empty.", "error")
            return False

        self.app.template_repo.save(name, body, [str(p) for p in self.attachments])
        self.template_name.delete(0, "end")
        self.refresh_templates()
        self.notify(f"Saved template {name!r}.", "success")
        return True

    def load_template(self) -> bool:
        template = self.app.template_repo.get_by_name(self.template_picker.get())
        if template is None:
            self.notify("No template selected.", "warning")
            return False

        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", template.body)
        self.attachments = [Path(p) for p in template.media_paths]
        self._render_attachments()
        self._update_counter()
        self.notify(f"Loaded {template.name!r}. Edit it before sending.", "info")
        return True

    # -- groups ------------------------------------------------------------
    def refresh_groups(self) -> None:
        previously_selected = self.selected_group_ids()
        for child in self.group_list.winfo_children():
            child.destroy()
        self._group_vars = {}

        groups = self.app.group_repo.list()
        if not groups:
            phase_note(self.group_list, "No groups yet — add some on the Groups screen.").pack(
                anchor="w", padx=theme.PAD_XS, pady=theme.PAD_XS
            )
            return

        for group in groups:
            var = ctk.BooleanVar(value=group.id in previously_selected)
            self._group_vars[group.id] = var
            ctk.CTkCheckBox(
                self.group_list,
                text=group.display_name,
                variable=var,
                checkbox_width=18,
                checkbox_height=18,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=theme.TEXT,
                fg_color=theme.ACCENT,
                hover_color=theme.ACCENT_HOVER,
            ).pack(anchor="w", padx=theme.PAD_XS, pady=2)

    def selected_group_ids(self) -> list[int]:
        return [group_id for group_id, var in self._group_vars.items() if var.get()]

    # -- queueing ----------------------------------------------------------
    def scheduled_for(self) -> datetime | None:
        """The UTC moment this batch should run, or None to run immediately."""
        if self.schedule_mode.get() != SCHEDULE:
            return None
        return parse_schedule(self.schedule_entry.get())

    def add_to_queue(self) -> bool:
        body = self.get_text().strip()
        if not body:
            self.notify("Write something first.", "error")
            return False

        selected = self.selected_group_ids()
        if not selected:
            self.notify("Pick at least one group.", "error")
            return False

        try:
            when = self.scheduled_for()
        except ValueError:
            self.notify(f"Schedule must look like {SCHEDULE_FORMAT}.", "error")
            return False

        now = utcnow()
        settings = self.app.settings_repo
        planned = []
        for group_id in selected:
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            planned.append(
                PlannedTarget(
                    group_id=group.id,
                    group_name=group.display_name,
                    body=body,
                    last_posted_at=group.last_posted_at,
                    cooldown_hours=group.cooldown_hours,
                    recent_bodies=tuple(self.app.group_repo.recent_bodies(group.id)),
                )
            )

        verdict = evaluate_batch(
            planned,
            now=now,
            when=when,
            daily_cap=settings.get_int("daily_cap", 25),
            posted_today=self.app.task_repo.posted_count_since(start_of_today_utc(now)),
            window_start_hour=settings.get_int("posting_window_start_hour", 8),
            window_end_hour=settings.get_int("posting_window_end_hour", 23),
        )

        if not verdict.allowed:
            # Show the first refusal; fixing it usually reveals the rest.
            self.notify(verdict.blocked[0].message, "error")
            return False

        self.app.task_repo.create(
            body,
            [(group.group_id, group.body) for group in planned],
            media_paths=[str(p) for p in self.attachments],
            scheduled_for=when,
        )

        for warning in verdict.warnings:
            self.notify(warning, "warning")
        if not verdict.warnings:
            self.notify(f"Queued for {len(planned)} group(s).", "success")
        return True

    # -- attachments -------------------------------------------------------
    def _pick_files(self) -> None:
        """Open the OS picker.

        The one modal dialog the app is allowed to open: the user clicked a
        button, and it can never appear while a batch is running. The rule it
        sits under bans the app interrupting the user, not the user choosing a
        file.
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
