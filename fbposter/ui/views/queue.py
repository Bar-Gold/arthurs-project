"""Queue screen: what the worker is doing, and what it will do next.

Phase 2 renders the states with sample rows so the styling is real and
reviewable. The worker that fills them in is Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass

import customtkinter as ctk

from .. import theme
from .base import View, card, phase_note


@dataclass(frozen=True)
class QueueRow:
    group: str
    state: str
    detail: str


# The states the worker can put a group in, each shown once so the styling can
# be checked against every case rather than only the happy path.
SAMPLE_ROWS: tuple[QueueRow, ...] = (
    QueueRow("gardening.tlv", "done", "Posted 14:02 — verified"),
    QueueRow("secondhand-north", "done", "Posted 14:21 — verified"),
    QueueRow("142857291043", "running", "Typing post text…"),
    QueueRow("city-marketplace", "waiting", "Next in 18 min"),
    QueueRow("home-and-garden", "pending", "Queued"),
    QueueRow("bikes-for-sale", "failed", "Composer never appeared — batch halted"),
)

STATE_COLORS: dict[str, theme.Color] = {
    "done": theme.SUCCESS,
    "running": theme.ACCENT,
    "waiting": theme.WARNING,
    "pending": theme.NEUTRAL,
    "failed": theme.DANGER,
}

STATE_LABELS = {
    "done": "Done",
    "running": "Running",
    "waiting": "Waiting",
    "pending": "Pending",
    "failed": "Failed",
}


class QueueView(View):
    title = "Queue"
    subtitle = "One group at a time, with a randomised 10–25 minute gap between them."

    def build(self) -> None:
        banner = card(self.body)
        banner.pack(fill="x")

        inner = ctk.CTkFrame(banner, fg_color="transparent")
        inner.pack(fill="x", padx=theme.PAD_M, pady=theme.PAD_M)

        ctk.CTkLabel(
            inner,
            text="Next group in 18:24",
            font=ctk.CTkFont(
                family=theme.FONT_FAMILY, size=theme.SIZE_HEADING, weight="bold"
            ),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(side="left")

        ctk.CTkButton(
            inner,
            text="Cancel batch",
            width=110,
            height=30,
            fg_color="transparent",
            border_width=1,
            border_color=theme.DANGER,
            text_color=theme.DANGER,
            hover_color=theme.NAV_ACTIVE_BG,
            corner_radius=theme.RADIUS,
            command=lambda: self.notify("The worker arrives in Phase 5.", "info"),
        ).pack(side="right")

        self.rows_frame = ctk.CTkScrollableFrame(self.body, fg_color="transparent")
        self.note = phase_note(
            self.body,
            "Sample data — the queue is driven by the posting worker in Phase 5.",
        )

        # Fixed-height note first, so the expanding list cannot push it off-window.
        self.note.pack(side="bottom", anchor="w", pady=(theme.PAD_S, 0))
        self.rows_frame.pack(side="top", fill="both", expand=True, pady=(theme.PAD_M, 0))

        for row in SAMPLE_ROWS:
            self._render_row(row)

    def _render_row(self, row: QueueRow) -> None:
        color = STATE_COLORS.get(row.state, theme.NEUTRAL)

        container = card(self.rows_frame)
        container.pack(fill="x", pady=(0, theme.PAD_XS))

        # height=1 matters: CTkFrame defaults to 200px, and a spine that asks
        # for 200 drags the whole row to that height. fill="y" then stretches
        # it to whatever the text column actually needs.
        spine = ctk.CTkFrame(container, fg_color=color, width=4, height=1, corner_radius=2)
        spine.pack(side="left", fill="y", padx=(theme.PAD_S, 0), pady=theme.PAD_S)

        text_col = ctk.CTkFrame(container, fg_color="transparent")
        text_col.pack(side="left", fill="x", expand=True, padx=theme.PAD_M, pady=theme.PAD_S)

        ctk.CTkLabel(
            text_col,
            text=row.group,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_BODY, weight="bold"),
            text_color=theme.TEXT,
            anchor="w",
        ).pack(fill="x")

        ctk.CTkLabel(
            text_col,
            text=row.detail,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
            anchor="w",
        ).pack(fill="x")

        ctk.CTkLabel(
            container,
            text=STATE_LABELS.get(row.state, row.state),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.SIZE_SMALL, weight="bold"),
            text_color=color,
        ).pack(side="right", padx=theme.PAD_M)
