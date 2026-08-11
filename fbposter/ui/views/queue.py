"""Queue screen: what is queued, and what happened to it.

Reads real rows from the database. Nothing executes them yet -- the worker that
does is Phase 5 -- so everything here sits at "pending" until then.
"""

from __future__ import annotations

import customtkinter as ctk

from fbposter import clock
from fbposter.db.models import (
    TASK_CANCELLED,
    TASK_DONE,
    TASK_FAILED,
    TASK_HALTED,
    TASK_PENDING,
    TASK_RUNNING,
    Task,
)

from .. import theme
from .base import View, card, phase_note

STATE_COLORS: dict[str, theme.Color] = {
    "done": theme.SUCCESS,
    "running": theme.ACCENT,
    "waiting": theme.WARNING,
    "pending": theme.NEUTRAL,
    "failed": theme.DANGER,
    "skipped": theme.NEUTRAL,
    "cancelled": theme.NEUTRAL,
    "halted": theme.DANGER,
}

STATE_LABELS = {
    TASK_PENDING: "Pending",
    TASK_RUNNING: "Running",
    TASK_DONE: "Done",
    TASK_FAILED: "Failed",
    TASK_CANCELLED: "Cancelled",
    TASK_HALTED: "Halted",
    "waiting": "Waiting",
    "skipped": "Skipped",
}


def summarise(task: Task) -> str:
    """One line describing when a batch runs and what it says."""
    if task.scheduled_for is not None:
        when = f"Scheduled {clock.format_local(task.scheduled_for)}"
    else:
        when = "Post now"

    preview = " ".join(task.body.split())
    if len(preview) > 60:
        preview = preview[:57] + "…"
    return f"{when} — {preview}"


class QueueView(View):
    title = "Queue"
    subtitle = "One group at a time, with a randomised 10–25 minute gap between them."

    def build(self) -> None:
        self.rows_frame = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        self.note = phase_note(
            self.body,
            "Queued batches wait here — the worker that posts them arrives in Phase 5.",
        )

        self.note.pack(side="bottom", anchor="w", pady=(theme.PAD_S, 0))
        self.rows_frame.pack(side="top", fill="both", expand=True)

        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()

        tasks = self.app.task_repo.list_recent()
        if not tasks:
            phase_note(
                self.rows_frame,
                "Nothing queued. Write a post on the Compose screen and add it to the queue.",
            ).pack(anchor="w", pady=theme.PAD_S)
            return

        for task in tasks:
            self._render_task(task)

    def cancel_task(self, task: Task) -> None:
        self.app.task_repo.cancel(task.id)
        self.refresh()
        self.notify("Batch cancelled.", "info")

    # -- rendering ---------------------------------------------------------
    def _render_task(self, task: Task) -> None:
        container = card(self.rows_frame)
        container.pack(fill="x", pady=(0, theme.PAD_S))

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill="x", padx=theme.PAD_M, pady=(theme.PAD_M, theme.PAD_XS))

        ctk.CTkLabel(
            header,
            text=summarise(task),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
            justify="left",
        ).pack(side="left")

        ctk.CTkLabel(
            header,
            text=STATE_LABELS.get(task.state, task.state),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL, weight="bold"),
            text_color=STATE_COLORS.get(task.state, theme.NEUTRAL),
        ).pack(side="right")

        if task.state == TASK_PENDING:
            ctk.CTkButton(
                header,
                text="Cancel",
                width=70,
                height=24,
                fg_color="transparent",
                border_width=1,
                border_color=theme.DANGER,
                text_color=theme.DANGER,
                hover_color=theme.NAV_ACTIVE_BG,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                command=lambda t=task: self.cancel_task(t),
            ).pack(side="right", padx=theme.PAD_S)

        targets = self.app.task_repo.targets_for(task.id)
        for index, target in enumerate(targets):
            is_last = index == len(targets) - 1
            row = ctk.CTkFrame(container, fg_color="transparent")
            # Bottom padding comes from the last row rather than a spacer frame:
            # an empty CTkFrame defaults to 200px wide and draws as a stray line.
            row.pack(
                fill="x",
                padx=theme.PAD_M,
                pady=(0, theme.PAD_M if is_last else theme.PAD_XS),
            )

            spine = ctk.CTkFrame(
                row,
                fg_color=STATE_COLORS.get(target.state, theme.NEUTRAL),
                width=3,
                # CTkFrame defaults to 200px tall; without this the row balloons.
                height=1,
                corner_radius=2,
            )
            spine.pack(side="left", fill="y", padx=(0, theme.PAD_S))

            ctk.CTkLabel(
                row,
                text=target.group_label,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=theme.TEXT,
                anchor="w",
            ).pack(side="left")

            # Raw Playwright errors run to hundreds of characters of obfuscated
            # class names and swamped the row. Show a readable amount.
            detail = target.error or STATE_LABELS.get(target.state, target.state)
            if len(detail) > 160:
                detail = detail[:157].rstrip() + "…"

            ctk.CTkLabel(
                row,
                text=detail,
                font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
                text_color=STATE_COLORS.get(target.state, theme.NEUTRAL),
                anchor="e",
                justify="right",
                wraplength=430,
            ).pack(side="right")
