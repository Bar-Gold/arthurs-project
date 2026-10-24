"""Repeating posts: every post that goes out by itself, in one list.

The user's request (2026-10-21). The list used to sit under Create a post,
step 3, in Repeat mode only, so a repeating post could be paused, edited or
deleted only from inside the road to a new post. It is a sidebar item now,
beside Group sets. New repeating posts are still made in Create a post: a post
needs its words, its groups and its times, and that road already asks for
them in order. There is no "New" button here (the user's choice).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock, recurrence
from fbposter.db.models import SCHEDULE_ACTIVE, SCHEDULE_PAUSED, utcnow

from .. import theme
from ..widgets import badge, card, clear

SNIPPET_CHARS = 90
# Left, whatever the language: see `_card`.
START = Qt.AlignLeft | Qt.AlignAbsolute | Qt.AlignVCenter


def describe(schedule) -> str:
    """Its rule, as words, or why it has none."""
    try:
        return recurrence.describe(recurrence.build(schedule.times, schedule.days))
    except recurrence.InvalidRecurrence as exc:
        return f"Unusable rule: {exc}"


class RepeatingPostsView(QWidget):
    title = "Repeating posts"
    subtitle = (
        "Posts that go out by themselves on the days and times you set. Make a "
        "new one in Create a post, step 3."
    )

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # What the list shows now. None means "nothing yet", so the first
        # refresh always builds.
        self._shown = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        heading = QLabel(self.title)
        heading.setObjectName("Title")
        outer.addWidget(heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        outer.addSpacing(theme.HEADER_GAP)

        self.count_label = QLabel("")
        self.count_label.setObjectName("SectionHeading")
        outer.addWidget(self.count_label)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        # Never sideways: a card too wide for the column was cut off at its
        # right edge, Edit half shown and Delete out of sight.
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.schedule_box = QVBoxLayout(holder)
        self.schedule_box.setContentsMargins(0, 0, 0, 0)
        self.schedule_box.setSpacing(theme.PAD_XS)
        self.area.setWidget(holder)
        outer.addWidget(self.area, 1)

    def on_show(self) -> None:
        self.refresh()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    # -- the list ------------------------------------------------------------
    @staticmethod
    def _snapshot(schedules) -> tuple:
        """Everything a card draws. The rule's words come from the times and
        days, and the counts from the lists, so those stand in for them."""
        return tuple(
            (
                s.id, s.display_name, s.state, tuple(s.times), tuple(s.days),
                s.next_run_at, len(s.recipient_ids), len(s.set_ids), tuple(s.bodies),
            )
            for s in schedules
        )

    def refresh(self, force: bool = False) -> None:
        """Re-read the posts, and redraw only when what they show changed.

        A fresh read every time: a post can be paused, edited or deleted from
        here, and the worker moves its next run on.
        """
        schedules = self.app.schedule_repo.list()
        self.count_label.setText(
            f"{len(schedules)} repeating post{'s' if len(schedules) != 1 else ''}"
            if schedules else ""
        )
        snapshot = self._snapshot(schedules)
        if not force and snapshot == self._shown:
            return
        self._shown = snapshot

        clear(self.schedule_box)
        if not schedules:
            note = QLabel(
                "Nothing repeating yet. Make one in Create a post: write it, "
                "choose the groups, then Repeat."
            )
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.schedule_box.addWidget(note)
        for schedule in schedules:
            self.schedule_box.addWidget(self._card(schedule))
        self.schedule_box.addStretch(1)

    def _card(self, schedule) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)

        # The name and whether it is on, then what it does, then what can be
        # done to it. The state used to sit among the buttons, in coloured
        # words the same size as "Delete" beside it; and the buttons shared
        # the name's line, which ran the card off the column's right edge in
        # a narrow window.
        header = QHBoxLayout()
        header.setSpacing(theme.PAD_S)
        title = QLabel(schedule.display_name)
        title.setObjectName("Strong")
        title.setWordWrap(True)
        # At the card's start, as names are in every list. Left to itself a
        # Hebrew name went to the far end, beside its badge. Alignment, not
        # direction code: the words still read right to left.
        title.setAlignment(START)
        header.addWidget(title, 1)
        header.addWidget(
            badge(
                "Active" if schedule.active else "Paused",
                "SUCCESS" if schedule.active else "TEXT_MUTED",
            ),
            0,
            Qt.AlignTop,
        )
        layout.addLayout(header)

        # recipient_ids: the groups its next run goes to, sets included.
        groups = len(schedule.recipient_ids)
        sets = len(schedule.set_ids)
        detail = QLabel(
            f"{describe(schedule)} · "
            + (f"{sets} group set{'s' if sets != 1 else ''} · " if sets else "")
            + f"{groups} group{'s' if groups != 1 else ''} · "
            f"{len(schedule.bodies)} version{'s' if len(schedule.bodies) != 1 else ''}"
        )
        detail.setObjectName("Muted")
        detail.setWordWrap(True)
        layout.addWidget(detail)

        when = QLabel(
            f"Next {clock.format_local(schedule.next_run_at)}"
            if schedule.active and schedule.next_run_at
            else "Not scheduled"
        )
        when.setObjectName("Muted")
        layout.addWidget(when)

        if schedule.bodies:
            flat = " ".join(schedule.bodies[0].split())
            snippet = flat[:SNIPPET_CHARS] + ("…" if len(flat) > SNIPPET_CHARS else "")
            first = QLabel(snippet)
            first.setWordWrap(True)
            first.setAlignment(START)
            layout.addWidget(first)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, theme.PAD_XS, 0, 0)
        actions.setSpacing(theme.PAD_S)
        toggle = QPushButton("Pause" if schedule.active else "Resume")
        toggle.clicked.connect(lambda _c, s=schedule.id: self.toggle_schedule(s))
        actions.addWidget(toggle)
        edit = QPushButton("Edit")
        edit.clicked.connect(lambda _c, s=schedule.id: self.edit_schedule(s))
        actions.addWidget(edit)
        actions.addStretch(1)
        remove = QPushButton("Delete")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, s=schedule.id: self.delete_schedule(s))
        actions.addWidget(remove)
        layout.addLayout(actions)
        return box

    # -- what can be done to one -------------------------------------------
    def toggle_schedule(self, schedule_id: int) -> None:
        schedule = self.app.schedule_repo.get(schedule_id)
        if schedule is None:
            return
        if schedule.active:
            self.app.schedule_repo.set_state(schedule_id, SCHEDULE_PAUSED)
            self.notify(f"{schedule.display_name} paused.", "info")
        else:
            # Resuming recomputes the next slot rather than firing the one that
            # went by while it was paused.
            rule = recurrence.build(schedule.times, schedule.days)
            self.app.schedule_repo.set_next_run(
                schedule_id, recurrence.next_occurrence(rule, utcnow())
            )
            self.app.schedule_repo.set_state(schedule_id, SCHEDULE_ACTIVE)
            self.notify(f"{schedule.display_name} resumed.", "success")
        self.refresh()

    def edit_schedule(self, schedule_id: int) -> bool:
        """Open the repeating post on its own screen, where all of it can change."""
        editor = self.app.views["schedule_edit"]
        if not editor.load(schedule_id):
            self.notify("That repeating post no longer exists.", "error")
            self.refresh()
            return False
        self.app.show_view("schedule_edit")
        return True

    def delete_schedule(self, schedule_id: int) -> None:
        schedule = self.app.schedule_repo.get(schedule_id)
        self.app.schedule_repo.delete(schedule_id)
        self.refresh()
        if schedule is not None:
            # "Delete" sits beside "Edit" on every card. A slip takes the
            # wordings with it, so it is one click to take back.
            self.app.toast(
                f"{schedule.display_name} deleted.",
                "info",
                undo=lambda: self._restore_schedule(schedule),
            )

    def _restore_schedule(self, schedule) -> str | None:
        if not self.app.schedule_repo.restore(schedule):
            return None
        self.refresh()
        return f"{schedule.display_name} is back."
