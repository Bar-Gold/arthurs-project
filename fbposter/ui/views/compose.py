"""Compose screen: the post text, its attachments, and where it goes."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from fbposter import clock
from fbposter.db.models import utcnow
from fbposter.guards import PlannedTarget, evaluate_batch

from .. import textdir, theme
from ..preview import PostPreview
from .base import View, card, phase_note

IMAGE_TYPES = [("Images", "*.jpg *.jpeg *.png *.gif *.webp"), ("All files", "*.*")]
SCHEDULE_FORMAT = clock.LOCAL_FORMAT
RIGHT_COLUMN_WIDTH = 330
TAB_STRIP_HEIGHT = 42
# Group names can be long Hebrew or Russian strings; tabs stay readable.
TAB_LABEL_CHARS = 16

POST_NOW = "Post now"
SCHEDULE = "Schedule"

WRITE = "Write"
PREVIEW = "Preview"

ALL_GROUPS_TAB = "All groups"
REWORDED = "Just now · reworded for this group"
SHARED_WORDING = "Just now · the shared wording"

# Tk has no widget-level justify on a Text; it is a tag option. Left is the
# default, so only the right-to-left lines need marking.
RTL_TAG = "rtl"


def parse_schedule(raw: str) -> datetime:
    """Read an Israel-time 'YYYY-MM-DD HH:MM' and return it as UTC.

    The user types the time they mean locally; storage is always UTC.
    """
    return clock.parse_local(raw, SCHEDULE_FORMAT)


class ComposeView(View):
    title = "Compose"
    subtitle = "Write the post, attach images, then pick the groups to send it to."

    def build(self) -> None:
        self.attachments: list[Path] = []
        self._group_vars: dict[int, ctk.BooleanVar] = {}

        # The shared wording, and the per-group rewrites that override it.
        # Sending the same words to every group is the main ban vector at this
        # volume, so this is what the warning in guards.py exists to push
        # towards -- and until now there was no way to act on it.
        self._base_body = ""
        self._bodies: dict[int, str] = {}
        self._editing: int | None = None  # None == the "All groups" tab

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
        top_row = ctk.CTkFrame(parent, fg_color="transparent", height=TAB_STRIP_HEIGHT)

        # Packed before the tab strip so the strip, which expands, cannot push
        # it off the row.
        self.view_mode = ctk.CTkSegmentedButton(
            top_row,
            values=[WRITE, PREVIEW],
            width=150,
            height=26,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            command=lambda _choice: self.sync_mode(),
        )
        self.view_mode.set(WRITE)
        self.view_mode.pack(side="right", padx=(theme.PAD_S, 0))

        # Fixed height: the tab strip must never grow and squeeze the editor,
        # and CTkFrame would happily default to 200px if left alone.
        # Scrollable because 5-7 groups' tabs do not fit across the editor, but
        # the scrollbar is muted -- it is a rarely-used affordance and a heavy
        # grey bar under the tabs dominated the screen.
        self.tab_strip = ctk.CTkScrollableFrame(
            top_row,
            orientation="horizontal",
            height=TAB_STRIP_HEIGHT,
            fg_color="transparent",
            scrollbar_fg_color="transparent",
            scrollbar_button_color=theme.BORDER,
            scrollbar_button_hover_color=theme.TEXT_MUTED,
        )
        self.tab_strip.pack(side="left", fill="x", expand=True)

        editor = card(parent)
        self.editor = editor
        # Shares the editor's slot: exactly one of the two is packed.
        self.preview = PostPreview(parent)

        self.textbox = ctk.CTkTextbox(
            editor,
            wrap="word",
            fg_color="transparent",
            text_color=theme.TEXT,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY),
            border_width=0,
        )
        self.textbox.pack(fill="both", expand=True, padx=theme.PAD_M, pady=theme.PAD_M)
        self.textbox.bind("<KeyRelease>", lambda _event: self._on_text_changed())
        self.textbox.tag_config(RTL_TAG, justify="right")
        # What is currently tagged, so a keystroke that changes nothing visible
        # costs nothing. See _apply_direction.
        self._line_directions: list[str] = []
        self._apply_direction()

        footer = ctk.CTkFrame(editor, fg_color="transparent")
        footer.pack(fill="x", padx=theme.PAD_M, pady=(0, theme.PAD_M))

        self.counter = ctk.CTkLabel(
            footer,
            text="0 characters",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
        )
        self.counter.pack(side="left")

        self.reset_button = ctk.CTkButton(
            footer,
            text="Reset to base text",
            width=140,
            height=24,
            fg_color="transparent",
            border_width=1,
            border_color=theme.BORDER,
            text_color=theme.TEXT_MUTED,
            hover_color=theme.NAV_ACTIVE_BG,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            command=self.reset_current,
        )

        # Content variation is the highest-value protection in the app
        # (README section 7), so the reminder sits beside the text.
        self.variation_note = phase_note(footer, "Vary the wording between groups.")
        self.variation_note.pack(side="right")

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
        # Tabs first, then the editor -- the expanding widget is packed last so
        # it cannot shove the fixed controls off the window.
        top_row.pack(side="top", fill="x", pady=(0, theme.PAD_XS))
        editor.pack(side="top", fill="both", expand=True)
        self.refresh_tabs()

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
            0, (clock.local_now() + timedelta(hours=1)).strftime(SCHEDULE_FORMAT)
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

        # A template is the shared starting point, so it stores the base text
        # rather than whichever group happens to be open.
        self.capture()
        body = self._base_body.strip()
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

        # A template replaces the shared wording, so any per-group rewrites of
        # the previous text no longer belong to anything.
        self._editing = None
        self._bodies.clear()
        self._base_body = template.body
        self._show(template.body)
        self.refresh_tabs()

        self.attachments = [Path(p) for p in template.media_paths]
        self._render_attachments()
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
                # Ticking a group gives it a tab; unticking takes it away.
                command=self.refresh_tabs,
            ).pack(anchor="w", padx=theme.PAD_XS, pady=2)

        self.refresh_tabs()

    def selected_group_ids(self) -> list[int]:
        return [group_id for group_id, var in self._group_vars.items() if var.get()]

    # -- queueing ----------------------------------------------------------
    def scheduled_for(self) -> datetime | None:
        """The UTC moment this batch should run, or None to run immediately."""
        if self.schedule_mode.get() != SCHEDULE:
            return None
        return parse_schedule(self.schedule_entry.get())

    def add_to_queue(self) -> bool:
        # Whatever is on screen belongs to a tab; commit it before reading.
        self.capture()

        selected = self.selected_group_ids()
        if not selected:
            self.notify("Pick at least one group.", "error")
            return False

        if any(not self.body_for(group_id).strip() for group_id in selected):
            self.notify("Every selected group needs some text.", "error")
            return False

        body = self._base_body.strip()

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
                    # This group's own wording, which is the whole point: the
                    # variation warning counts distinct bodies, so rewriting
                    # them silences it honestly rather than by ignoring it.
                    body=self.body_for(group.id).strip(),
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
            posted_today=self.app.task_repo.posted_count_since(clock.start_of_local_day(now)),
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

        self.refresh_preview()

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

    # -- per-group text ----------------------------------------------------
    def body_for(self, group_id: int) -> str:
        """The wording this group will actually receive.

        Reads committed state only, so callers must `capture()` first if the
        editor might hold unsaved text. An earlier version returned the live
        editor contents when that group was the active tab, which quietly
        handed back the wrong text the moment `_editing` was assigned before
        the read.
        """
        return self._bodies.get(group_id, self._base_body)

    def capture(self) -> None:
        """Save what is in the editor into whichever tab is showing.

        Editing the base wipes the per-group rewrites, which is what the user
        asked for. It is announced rather than silent, and only fires when the
        base text genuinely changed -- switching tabs must never cost someone
        their wording.
        """
        text = self.get_text()
        if self._editing is None:
            if text != self._base_body:
                had_rewrites = bool(self._bodies)
                self._base_body = text
                if had_rewrites:
                    self._bodies.clear()
                    self.notify(
                        "Base text changed, so the per-group versions were reset.",
                        "warning",
                    )
        else:
            self._bodies[self._editing] = text

    def select_tab(self, target: int | None) -> None:
        """Switch the editor between the base text and a group's version."""
        self.capture()
        # Read the target's text before reassigning _editing: the two are
        # independent and mixing the order is what broke this once already.
        text = self._base_body if target is None else self.body_for(target)
        self._editing = target
        self._show(text)
        self.refresh_tabs()

    def reset_current(self) -> None:
        """Drop this group's rewrite and go back to the shared wording."""
        if self._editing is None:
            return
        self._bodies.pop(self._editing, None)
        self._show(self._base_body)
        self.refresh_tabs()

    def has_rewrite(self, group_id: int) -> bool:
        return group_id in self._bodies and self._bodies[group_id] != self._base_body

    def _show(self, text: str) -> None:
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", text)
        self._on_text_changed()

    # -- preview -----------------------------------------------------------
    def sync_mode(self) -> None:
        """Swap the editor and the preview in and out of the same slot."""
        if self.view_mode.get() == PREVIEW:
            # The preview must show what would be sent, so commit the editor
            # first -- exactly as switching tabs does.
            self.capture()
            self.editor.pack_forget()
            self.preview.pack(side="top", fill="both", expand=True)
            # Marks a rewrite that was typed a moment ago, and redraws the
            # preview on its way through.
            self.refresh_tabs()
        else:
            self.preview.pack_forget()
            self.editor.pack(side="top", fill="both", expand=True)

    def preview_heading(self) -> tuple[str, str]:
        """The name and meta line above the previewed post."""
        if self._editing is None:
            count = len(self.selected_group_ids())
            if count == 0:
                return ALL_GROUPS_TAB, "Just now · no groups selected yet"
            return ALL_GROUPS_TAB, f"Just now · {count} group{'s' if count != 1 else ''}"

        group = self.app.group_repo.get(self._editing)
        name = group.display_name if group else ALL_GROUPS_TAB
        return name, REWORDED if self.has_rewrite(self._editing) else SHARED_WORDING

    def refresh_preview(self) -> None:
        """Redraw the preview, if it is the pane on screen.

        Cheap to call from anywhere: in Write mode it does nothing at all,
        which is why every path that changes the post can just call it.
        """
        if getattr(self, "view_mode", None) is None or self.view_mode.get() != PREVIEW:
            return

        heading, subheading = self.preview_heading()
        # Committed state, never the live editor -- same rule as body_for().
        text = self._base_body if self._editing is None else self.body_for(self._editing)
        self.preview.render(
            heading=heading, subheading=subheading, text=text, media=list(self.attachments)
        )

    def refresh_tabs(self) -> None:
        """Rebuild the tab strip from the current selection."""
        strip = getattr(self, "tab_strip", None)
        if strip is None:
            return

        # An edited group that is no longer selected must not stay on screen --
        # the editor would be showing text nobody is going to receive.
        if self._editing is not None and self._editing not in self.selected_group_ids():
            self._editing = None
            self._show(self._base_body)

        # Every path that changes the post ends up here, so this is the one
        # place the preview has to be kept in step. It is a no-op in Write
        # mode, which is the mode it is called in almost every time.
        self.refresh_preview()

        # Destroying and recreating buttons inside a CTkScrollableFrame is
        # surprisingly expensive -- doing it unconditionally on every refresh
        # doubled the test suite's runtime. Skip when nothing visible changed.
        signature = (
            self._editing,
            tuple(
                (group_id, self.has_rewrite(group_id))
                for group_id in self.selected_group_ids()
            ),
        )
        if signature == getattr(self, "_tab_signature", None):
            return
        self._tab_signature = signature

        for child in strip.winfo_children():
            child.destroy()

        self._tab_buttons: dict[int | None, ctk.CTkButton] = {}
        self._add_tab(None, ALL_GROUPS_TAB)

        for group_id in self.selected_group_ids():
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            label = group.display_name
            if len(label) > TAB_LABEL_CHARS:
                label = label[: TAB_LABEL_CHARS - 1] + "…"
            if self.has_rewrite(group_id):
                label = f"● {label}"
            self._add_tab(group_id, label)

        showing_group = self._editing is not None
        if showing_group and self.has_rewrite(self._editing):
            self.reset_button.pack(side="right", padx=(0, theme.PAD_S))
        else:
            self.reset_button.pack_forget()

    def _add_tab(self, target: int | None, label: str) -> None:
        active = target == self._editing
        button = ctk.CTkButton(
            self.tab_strip,
            # A CTkButton is 140px wide unless told otherwise, which fits about
            # two tabs before the strip starts scrolling. width=10 makes it
            # shrink to its text instead; the spaces are the breathing room
            # that a fixed width used to provide.
            text=f" {label} ",
            width=10,
            height=26,
            corner_radius=theme.RADIUS,
            fg_color=theme.ACCENT if active else "transparent",
            text_color=theme.TEXT_ON_ACCENT if active else theme.TEXT,
            border_width=0 if active else 1,
            border_color=theme.BORDER,
            hover_color=theme.ACCENT_HOVER if active else theme.NAV_ACTIVE_BG,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            command=lambda t=target: self.select_tab(t),
        )
        button.pack(side="left", padx=(0, theme.PAD_XS))
        self._tab_buttons[target] = button

    # -- text --------------------------------------------------------------
    def get_text(self) -> str:
        """What the user actually wrote.

        The editor holds invisible direction marks so Windows draws Hebrew the
        right way round; they are scaffolding, not content. Stripping here — the
        one place the box is ever read — is what keeps them out of the post, the
        templates and the database.
        """
        return textdir.strip_controls(self.textbox.get("1.0", "end-1c"))

    def _on_text_changed(self) -> None:
        """Everything that has to follow the text. The one hook for edits.

        Runs on every keystroke, so it reads the box once and hands the text to
        both jobs rather than fetching it twice.
        """
        text = self.get_text()
        self._update_counter(text)
        self._apply_direction(text)

    def _has_mark(self, line: int) -> bool:
        return self.textbox.get(f"{line}.0", f"{line}.1") == textdir.RLE_MARK

    def _apply_direction(self, text: str | None = None) -> None:
        """Make each line read and sit the way it should.

        Two separate things, both per line. **Order** comes from an invisible
        RLE at the start of a right-to-left line: Tk lays characters out in
        logical order and leaves the reordering to Windows, which only manages
        it one run of one script at a time, so a line mixing Hebrew with
        English or digits comes out mirrored. The RLE gives Windows a
        right-to-left base for the whole line and it comes out right, English
        and numbers included. **Alignment** is the justify tag.

        The mark lives in the widget and never in the post: `get_text` strips
        it, and it is the only way anything reads this box.

        Touching the widget forces a full relayout, felt as typing lag, so this
        does nothing at all unless a line's direction or its mark is actually
        wrong -- which is rare, and never during ordinary typing.
        """
        text = self.get_text() if text is None else text
        directions = textdir.line_directions(text)
        marks = [
            self._has_mark(number) for number in range(1, len(directions) + 1)
        ]
        wanted = [direction == textdir.RTL for direction in directions]
        if directions == self._line_directions and marks == wanted:
            return
        self._line_directions = directions

        # Marks first: they shift columns, and the tags are placed by line.
        for number, (needs_mark, has_mark) in enumerate(zip(wanted, marks), start=1):
            if needs_mark and not has_mark:
                self.textbox.insert(f"{number}.0", textdir.RLE_MARK)
            elif has_mark and not needs_mark:
                self.textbox.delete(f"{number}.0", f"{number}.1")

        self.textbox.tag_remove(RTL_TAG, "1.0", "end")
        for number, direction in enumerate(directions, start=1):
            if direction == textdir.RTL:
                # Through the newline, so a character typed at the end of the
                # line inherits the tag instead of falling out of it.
                self.textbox.tag_add(RTL_TAG, f"{number}.0", f"{number + 1}.0")

    def _update_counter(self, text: str | None = None) -> None:
        count = len(self.get_text() if text is None else text)
        self.counter.configure(text=f"{count} character{'s' if count != 1 else ''}")
