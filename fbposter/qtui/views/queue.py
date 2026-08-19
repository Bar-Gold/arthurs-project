"""Queue screen: what is pending, running, done or failed."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock
from fbposter.db.models import (
    TARGET_AWAITING_APPROVAL,
    TARGET_DECLINED,
    TASK_PENDING,
    TASK_RUNNING,
    utcnow,
)

from .. import theme
from ..widgets import card, clear, replace_at

STATE_COLOURS = {
    "done": "SUCCESS",
    "posted": "SUCCESS",
    "failed": "DANGER",
    "halted": "DANGER",
    "cancelled": "TEXT_MUTED",
    "missed": "WARNING",
    "skipped": "WARNING",
    "running": "ACCENT_TEXT",
    TARGET_AWAITING_APPROVAL: "WARNING",
    TARGET_DECLINED: "TEXT_MUTED",
}

SNIPPET_CHARS = 60


class QueueView(QWidget):
    title = "Queue"
    subtitle = "One group at a time, with a randomised 10–25 minute gap between them."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.show_all = False
        # What is currently drawn. None means "nothing yet", so the first
        # refresh always builds.
        self._snapshot_shown = None
        # The same thing per card, parallel to the widgets in self.rows, so a
        # refresh can redraw only the batches that actually changed.
        self._cards_shown = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        heading = QLabel(self.title)
        heading.setObjectName("Title")
        outer.addWidget(heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        outer.addWidget(sub)
        outer.addSpacing(theme.PAD_S)

        tools = QHBoxLayout()
        self.hidden_label = QLabel("")
        self.hidden_label.setObjectName("Muted")
        tools.addWidget(self.hidden_label)
        tools.addStretch(1)
        self.recent_button = QPushButton("Recent")
        self.all_button = QPushButton("All")
        modes = QButtonGroup(self)
        for index, button in enumerate((self.recent_button, self.all_button)):
            button.setObjectName("Tab")
            button.setCheckable(True)
            modes.addButton(button, index)
            tools.addWidget(button)
        self.recent_button.setChecked(True)
        self.recent_button.clicked.connect(lambda: self.set_scope(False))
        self.all_button.clicked.connect(lambda: self.set_scope(True))
        outer.addLayout(tools)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.holder = QWidget()
        self.rows = QVBoxLayout(self.holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(theme.PAD_S)
        self.area.setWidget(self.holder)
        outer.addWidget(self.area, 1)

    def on_show(self) -> None:
        self.refresh()

    def cancel(self, task_id: int) -> None:
        self.app.task_repo.cancel(task_id)
        self.refresh()
        self.app.toast("Batch cancelled.", "info")

    def resolve_pending(self, target_id: int, approved: bool) -> None:
        """Record what the admin did with a post that was awaiting approval.

        Declining releases the wording: nothing was published, so the repeated
        -text guard should not go on treating it as sent. Without this the user
        could never send those words to that group again, over a post that
        never existed.
        """
        if not self.app.task_repo.resolve_pending(target_id, approved):
            self.app.toast("That post is no longer awaiting approval.", "warning")
        elif approved:
            self.app.toast("Marked as live.", "success")
        else:
            self.app.toast(
                "Marked as declined — that wording is free to send again.", "info"
            )
        self.refresh()

    def set_scope(self, show_all: bool) -> None:
        self.show_all = show_all
        self.recent_button.setChecked(not show_all)
        self.all_button.setChecked(show_all)
        self.refresh()

    def retention_hours(self) -> int:
        return self.app.settings_repo.get_int("queue_retention_hours", 24)

    def _schedule_name(self, schedule_id, names) -> str:
        if schedule_id is None:
            return ""
        return names.get(schedule_id) or "repeating post"

    def _card_snapshot(self, task, targets, schedule_names) -> tuple:
        """Everything one card draws, as one comparable value.

        Rebuilding 25 batch cards cost 127ms, and the screen paid it on every
        visit and again on every worker event -- so during a batch, which is
        exactly when someone is watching this screen, it was rebuilding
        several times a minute, usually to redraw 24 identical cards beside
        the one that had changed.

        A target's group name comes off the target row, which `targets_for`
        already joined. Looking it up through GroupRepo cost a query per group
        per refresh -- 150 of them here -- to recompute a string already held.
        """
        return (
            task.id,
            task.state,
            task.error,
            task.body,
            task.scheduled_for,
            self._schedule_name(task.schedule_id, schedule_names),
            tuple((t.id, t.state, t.error, t.group_label) for t in targets),
        )

    def refresh(self, force: bool = False) -> None:
        now = utcnow()
        hours = self.retention_hours()
        if self.show_all:
            tasks = self.app.task_repo.list_recent()
            hidden = 0
        else:
            tasks = self.app.task_repo.list_for_queue(now, hours)
            hidden = self.app.task_repo.count_older_than(now, hours)

        # Three queries for the whole screen rather than two per batch and one
        # per group. Still a fresh read every time -- nothing is remembered
        # between refreshes, because a stale read would be a wrong screen.
        targets = self.app.task_repo.targets_for_tasks([task.id for task in tasks])
        schedule_names = (
            self.app.schedule_repo.names_by_id()
            if any(task.schedule_id is not None for task in tasks)
            else {}
        )

        cards = [
            self._card_snapshot(task, targets.get(task.id, ()), schedule_names)
            for task in tasks
        ]
        # Nothing visible has changed, so nothing is redrawn. `force` is the
        # escape hatch for a repaint that the data cannot describe -- a theme
        # change, say.
        snapshot = (self.show_all, hidden, hours, tuple(cards))
        if not force and snapshot == self._snapshot_shown:
            return
        drawn = self._cards_shown
        self._snapshot_shown = snapshot
        self._cards_shown = list(cards)

        # Nothing is deleted -- this is a view filter. The bodies stay in the
        # database because the repeated-text guard reads them to refuse sending
        # the same words to a group twice.
        self.hidden_label.setText(
            f"{hidden} finished batch{'es' if hidden != 1 else ''} older than "
            f"{hours}h hidden"
            if hidden
            else ""
        )

        if not tasks:
            clear(self.rows)
            note = QLabel(
                "Nothing recent. Older batches are under “All”."
                if hidden
                else "Nothing queued yet — write a post on the Compose screen."
            )
            note.setObjectName("Muted")
            self.rows.addWidget(note)
            self.rows.addStretch(1)
            return

        # The same batches in the same order: swap out only the cards whose own
        # snapshot moved. During a batch that is one card per worker event
        # rather than all of them.
        same_batches = (
            not force
            and drawn is not None
            and len(drawn) == len(cards)
            and all(before[0] == after[0] for before, after in zip(drawn, cards))
        )
        if same_batches:
            for index, (before, after) in enumerate(zip(drawn, cards)):
                if before != after:
                    task = tasks[index]
                    replace_at(
                        self.rows,
                        index,
                        self._card(task, targets.get(task.id, ()), schedule_names),
                    )
            return

        clear(self.rows)
        for task in tasks:
            self.rows.addWidget(
                self._card(task, targets.get(task.id, ()), schedule_names)
            )
        self.rows.addStretch(1)

    def _card(self, task, targets, schedule_names) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)

        header = QHBoxLayout()
        when = (
            f"Scheduled {clock.format_local(task.scheduled_for)}"
            if task.scheduled_for
            else "Post now"
        )
        if task.schedule_id is not None:
            when = f"{when} · {self._schedule_name(task.schedule_id, schedule_names)}"
        snippet = " ".join(task.body.split())[:SNIPPET_CHARS]
        title = QLabel(f"{when} — {snippet}")
        title.setStyleSheet("font-weight: 600;")
        title.setWordWrap(True)
        header.addWidget(title, 1)

        state = QLabel(task.state.title())
        state.setStyleSheet(
            f"color: {theme.C[STATE_COLOURS.get(task.state, 'TEXT_MUTED')]};"
        )
        header.addWidget(state)

        if task.state in (TASK_PENDING, TASK_RUNNING):
            cancel = QPushButton("Cancel")
            cancel.setObjectName("Link")
            cancel.clicked.connect(lambda _c, tid=task.id: self.cancel(tid))
            header.addWidget(cancel)
        layout.addLayout(header)

        for target in targets:
            # The name is already on the row: targets_for joins groups.
            name = target.group_label
            row = QHBoxLayout()
            # Indented under the batch header, so a row reads as one of its
            # groups rather than as a sibling of it.
            row.setContentsMargins(theme.PAD_M, 0, 0, 0)
            row.setSpacing(theme.PAD_S)
            label = QLabel(name)
            # The status sits beside the name rather than at the far edge of a
            # full-width card. Stretching the name pushed the two roughly 600px
            # apart, and they stopped reading as one fact about one group.
            row.addWidget(label)
            label_text = (
                "Awaiting admin approval"
                if target.state == TARGET_AWAITING_APPROVAL
                else target.state.title()
            )
            outcome = QLabel(label_text)
            outcome.setStyleSheet(
                f"color: {theme.C[STATE_COLOURS.get(target.state, 'TEXT_MUTED')]};"
            )
            row.addWidget(outcome)
            row.addStretch(1)
            if target.state == TARGET_AWAITING_APPROVAL:
                # An override, not a chore: the worker follows these up on its
                # own every few hours. They are here for the post an admin
                # never gets round to, which the app stops chasing after a
                # month, and for a user who already knows the answer.
                for caption, approved in (("Went live", True), ("Declined", False)):
                    button = QPushButton(caption)
                    button.setObjectName("Tab")
                    button.clicked.connect(
                        lambda _c, tid=target.id, ok=approved:
                        self.resolve_pending(tid, ok)
                    )
                    row.addWidget(button)
            layout.addLayout(row)
            if target.error:
                error = QLabel(target.error)
                error.setObjectName("Muted")
                error.setWordWrap(True)
                layout.addWidget(error)

        if task.error:
            error = QLabel(task.error)
            error.setStyleSheet(f"color: {theme.C['DANGER']};")
            error.setWordWrap(True)
            layout.addWidget(error)
        return box
