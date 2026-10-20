"""Editing a repeating post that already exists.

Its own screen, opened from the post's card on Publish, rather than loading the
post back into Compose, Groups and Publish: those three hold whatever the user
is writing next, and editing a repeating post must not cost them that draft.

Everything a repeating post is made of can change here -- name, wordings,
groups, pictures, times and days. Two things deliberately do not:

* **Paused or active.** Saving never pauses or resumes; that stays the card's
  Pause/Resume button.
* **A batch it has already queued.** That is an ordinary task with its own text
  and groups, possibly half posted, so it goes out as it was. Changes apply
  from the next run.

A group removed from the Groups list is not here at all: removing it took it
out of every repeating post (`GroupRepo.remove`).

Group sets are picked here whole, as on the Groups screen: a group a ticked
set brings shows ticked and greyed, with the set's name beside it. The post
stores the set, not its groups, so it follows the set from its next run.

Saving judges nothing: there are no posting rules (the user's choice,
2026-10-18). As many times a day as wanted, and pictures or a video alone.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock, recurrence
from fbposter.db.models import utcnow
from fbposter.db.repo import KEEP
from fbposter.text import strip_invisible

from .. import theme
from ..widgets import card, clear, literal, row, set_note_lines
from .compose import ATTACH_LABEL, MEDIA_FILTER, NO_ATTACHMENTS, check_attachments
from .groups import SET_COLUMNS, in_sets, set_label
from .publish import (
    DEFAULT_TIMES,
    RIGHT_COLUMN_SCROLLBAR,
    RIGHT_COLUMN_WIDTH,
    TimePicker,
    day_boxes,
    following,
    next_time,
)


# Room for about four lines of a wording.
WORDING_HEIGHT = 104


class ScheduleEditView(QWidget):
    title = "Edit repeating post"
    subtitle = (
        "Changes apply from its next run. A batch it has already queued goes "
        "out as it was."
    )

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.schedule_id: int | None = None
        # The post as it was loaded: its groups' order and the rule it had, so
        # Save can tell what moved.
        self._loaded = None
        self._loading = False
        self._wordings: list[QTextEdit] = []
        self._pictures: list[str] = []
        self._group_boxes: dict[int, QCheckBox] = {}
        # Beside each group: which ticked set brings it, when one does.
        self._group_notes: dict[int, QLabel] = {}
        # The groups ticked by hand. A box a set has ticked shows the set's
        # tick, so this is what it goes back to when the set is unticked.
        self._own: set[int] = set()
        self._set_boxes: dict[int, QCheckBox] = {}
        self._sets: dict[int, object] = {}
        self._time_rows: list[TimePicker] = []
        self._day_boxes: list[QCheckBox] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        self.heading = QLabel(self.title)
        self.heading.setObjectName("Title")
        outer.addWidget(self.heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        outer.addSpacing(theme.HEADER_GAP)

        body = QHBoxLayout()
        body.setSpacing(theme.PAD_M)
        outer.addLayout(body, 1)
        self._build_left(body)
        self._build_right(body)

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    # -- left column: what goes out, and where -----------------------------
    def _build_left(self, parent: QHBoxLayout) -> None:
        # One scroll for the whole column: every section keeps its natural
        # height rather than being squashed to fit the window.
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_XS)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        area.setWidget(holder)
        parent.addWidget(area, 1)

        header = QHBoxLayout()
        label = QLabel("Wordings")
        label.setObjectName("SectionHeading")
        header.addWidget(label)
        header.addStretch(1)
        self.add_wording_button = QPushButton("Add wording")
        self.add_wording_button.clicked.connect(lambda: self.add_wording())
        header.addWidget(self.add_wording_button)
        column.addLayout(header)

        self.wording_box = QVBoxLayout()
        self.wording_box.setSpacing(theme.PAD_S)
        column.addLayout(self.wording_box)

        self.rotation_note = QLabel("")
        self.rotation_note.setObjectName("Muted")
        self.rotation_note.setWordWrap(True)
        column.addWidget(self.rotation_note)
        column.addSpacing(theme.PAD_S)

        # Hidden while there are no sets to pick.
        self.sets_section = row()
        sets_column = QVBoxLayout(self.sets_section)
        sets_column.setContentsMargins(0, 0, 0, theme.PAD_S)
        sets_column.setSpacing(theme.PAD_XS)
        sets_label = QLabel("Group sets")
        sets_label.setObjectName("SectionHeading")
        sets_column.addWidget(sets_label)
        sets_card = card()
        self.set_grid = QGridLayout(sets_card)
        self.set_grid.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        self.set_grid.setHorizontalSpacing(theme.PAD_L)
        self.set_grid.setVerticalSpacing(theme.PAD_XS)
        sets_column.addWidget(sets_card)
        self.sets_section.setVisible(False)
        column.addWidget(self.sets_section)

        groups_label = QLabel("Groups")
        groups_label.setObjectName("SectionHeading")
        column.addWidget(groups_label)
        groups_card = card()
        groups_layout = QVBoxLayout(groups_card)
        groups_layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        groups_layout.setSpacing(theme.PAD_XS)
        self.group_box = QVBoxLayout()
        self.group_box.setSpacing(theme.PAD_XS)
        groups_layout.addLayout(self.group_box)
        column.addWidget(groups_card)
        column.addSpacing(theme.PAD_S)

        pictures_header = QHBoxLayout()
        pictures_label = QLabel("Pictures and videos")
        pictures_label.setObjectName("SectionHeading")
        pictures_header.addWidget(pictures_label)
        pictures_header.addStretch(1)
        self.attach_button = QPushButton(ATTACH_LABEL)
        self.attach_button.clicked.connect(self._pick_files)
        pictures_header.addWidget(self.attach_button)
        column.addLayout(pictures_header)

        self.picture_box = QVBoxLayout()
        self.picture_box.setSpacing(theme.PAD_XS)
        column.addLayout(self.picture_box)

        column.addStretch(1)

    # -- right column: when, and the buttons -------------------------------
    def _build_right(self, parent: QHBoxLayout) -> None:
        column = QVBoxLayout()
        column.setSpacing(theme.PAD_S)
        holder = QWidget()
        holder.setLayout(column)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFixedWidth(RIGHT_COLUMN_WIDTH + RIGHT_COLUMN_SCROLLBAR)
        scroll.setWidget(holder)
        parent.addWidget(scroll)

        picker = card()
        layout = QVBoxLayout(picker)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        layout.setSpacing(theme.PAD_XS)

        name_label = QLabel("Name")
        name_label.setObjectName("SectionHeading")
        layout.addWidget(name_label)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText("e.g. Bikes for sale")
        layout.addWidget(self.name_entry)

        header = QHBoxLayout()
        times_label = QLabel("Times of day")
        times_label.setObjectName("SectionHeading")
        header.addWidget(times_label)
        header.addStretch(1)
        self.add_time_button = QPushButton("Add a time")
        self.add_time_button.clicked.connect(lambda: self.add_time())
        header.addWidget(self.add_time_button)
        layout.addLayout(header)

        self.time_box = QVBoxLayout()
        self.time_box.setSpacing(theme.PAD_XS)
        layout.addLayout(self.time_box)

        days_label = QLabel("Days")
        days_label.setObjectName("SectionHeading")
        layout.addWidget(days_label)
        # Sunday first, four and three, as on Publish.
        days_grid = QGridLayout()
        days_grid.setSpacing(theme.PAD_XS)
        self._day_boxes = day_boxes(days_grid, self.refresh_summary)
        layout.addLayout(days_grid)
        column.addWidget(picker)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

        self.save_button = QPushButton("Save changes")
        self.save_button.setObjectName("Primary")
        # A lambda, so clicked() cannot pass its `checked` flag into save().
        self.save_button.clicked.connect(lambda: self.save())
        column.addWidget(self.save_button)

        self.back_button = QPushButton("← Back without saving")
        self.back_button.clicked.connect(lambda: self.leave())
        column.addWidget(self.back_button)

        column.addStretch(1)

    # -- loading -----------------------------------------------------------
    def load(self, schedule_id: int) -> bool:
        """Fill the screen from the stored post. False if it has gone."""
        schedule = self.app.schedule_repo.get(schedule_id)
        if schedule is None:
            return False
        self._loading = True
        try:
            self.schedule_id = schedule_id
            self._loaded = schedule
            self.heading.setText(f"{self.title}: {schedule.display_name}")
            self.name_entry.setText(schedule.name)

            clear(self.wording_box)
            self._wordings = []
            for body in schedule.bodies:
                self.add_wording(body)

            sets = self.app.group_set_repo.list()
            self._sets = {s.id: s for s in sets}
            clear(self.set_grid)
            self._set_boxes = {}
            for index, group_set in enumerate(sets):
                box = QCheckBox(literal(set_label(group_set)))
                box.setChecked(group_set.id in schedule.set_ids)
                box.toggled.connect(lambda _on: self.refresh_covered())
                self.set_grid.addWidget(box, index // SET_COLUMNS, index % SET_COLUMNS)
                self._set_boxes[group_set.id] = box
            self.sets_section.setVisible(bool(sets))

            active = self.app.group_repo.list()
            clear(self.group_box)
            self._group_boxes = {}
            self._group_notes = {}
            self._own = set(schedule.group_ids)
            for group in active:
                line = row()
                layout = QHBoxLayout(line)
                layout.setContentsMargins(0, 0, 0, 0)
                box = QCheckBox(literal(group.display_name))
                box.setChecked(group.id in schedule.group_ids)
                box.toggled.connect(lambda on, gid=group.id: self._toggle_own(gid, on))
                layout.addWidget(box, 1)
                note = QLabel("")
                note.setObjectName("Muted")
                note.setVisible(False)
                layout.addWidget(note)
                self.group_box.addWidget(line)
                self._group_boxes[group.id] = box
                self._group_notes[group.id] = note
            if not active:
                note = QLabel("No groups yet. Get them in Create a post, step 2.")
                note.setObjectName("Muted")
                self.group_box.addWidget(note)
            self.refresh_covered()

            self._pictures = list(schedule.media_paths)
            self._render_pictures()

            clear(self.time_box)
            self._time_rows = []
            for text in schedule.times or [DEFAULT_TIMES[0]]:
                self.add_time(text)

            every_day = not schedule.days
            for index, box in enumerate(self._day_boxes):
                box.setChecked(every_day or index in schedule.days)
        finally:
            self._loading = False
        self.refresh_summary()
        return True

    # -- wordings ----------------------------------------------------------
    def add_wording(self, text: str = "") -> QTextEdit:
        holder = card()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(theme.PAD_S, theme.PAD_S, theme.PAD_S, theme.PAD_S)

        editor = QTextEdit()
        editor.setAcceptRichText(False)
        editor.setPlainText(text)
        # Fixed rather than expanding: in one scrolling column, expanding
        # editors shared out the whole window and pushed Groups and Pictures
        # out of sight. A longer wording scrolls inside its own box.
        editor.setFixedHeight(WORDING_HEIGHT)
        editor.setPlaceholderText("Another way of saying the same thing…")
        editor.textChanged.connect(self.refresh_summary)
        layout.addWidget(editor, 1)

        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=holder, e=editor: self._remove_wording(w, e))
        layout.addWidget(remove, 0, Qt.AlignTop)

        self.wording_box.addWidget(holder)
        self._wordings.append(editor)
        self.refresh_summary()
        return editor

    def _remove_wording(self, holder: QWidget, editor: QTextEdit) -> None:
        self._wordings.remove(editor)
        self.wording_box.removeWidget(holder)
        holder.deleteLater()
        self.refresh_summary()

    def wordings(self) -> list[str]:
        """Cleaned on read, like every other editor: these become post bodies."""
        cleaned = (strip_invisible(e.toPlainText()).strip() for e in self._wordings)
        return [text for text in cleaned if text]

    # -- groups ------------------------------------------------------------
    def ticked_group_ids(self) -> list[int]:
        """Every group the post goes to, in the order the list shows them:
        the ones ticked by hand and the ones its ticked sets bring."""
        return [gid for gid, box in self._group_boxes.items() if box.isChecked()]

    def ticked_set_ids(self) -> list[int]:
        return [sid for sid, box in self._set_boxes.items() if box.isChecked()]

    def covered(self) -> dict[int, list[str]]:
        """Each group a ticked set brings, with the sets' names."""
        covered: dict[int, list[str]] = {}
        for set_id in self.ticked_set_ids():
            group_set = self._sets.get(set_id)
            if group_set is not None:
                for group_id in group_set.group_ids:
                    covered.setdefault(group_id, []).append(group_set.name)
        return covered

    def _toggle_own(self, group_id: int, on: bool) -> None:
        # refresh_covered() sets a box with its signals blocked, so only a
        # tick by hand (or a test standing in for one) reaches here.
        if on:
            self._own.add(group_id)
        else:
            self._own.discard(group_id)
        self.refresh_summary()

    def refresh_covered(self) -> None:
        """Grey the groups a ticked set brings; give the rest their own tick."""
        covered = self.covered()
        for group_id, box in self._group_boxes.items():
            names = covered.get(group_id)
            box.blockSignals(True)
            box.setChecked(bool(names) or group_id in self._own)
            box.blockSignals(False)
            box.setEnabled(not names)
            note = self._group_notes.get(group_id)
            if note is not None:
                note.setText(in_sets(names) if names else "")
                note.setVisible(bool(names))
        self.refresh_summary()

    def plain_group_ids(self) -> list[int]:
        """The groups ticked by hand that no ticked set brings."""
        covered = self.covered()
        return [gid for gid in self._group_boxes if gid in self._own and gid not in covered]

    def group_ids_to_save(self) -> list[int]:
        """What the post will hold: its old order first, new groups after.

        Keeping the old order keeps the rotation where it was -- the position
        is part of which wording a group gets next. Only ticked groups are
        kept, and only groups on the list can be ticked, so a group removed
        from the list is never written back into the post. A group a ticked
        set brings is the set's, so it is stored through the set.
        """
        plain = self.plain_group_ids()
        kept = [gid for gid in self._loaded.group_ids if gid in plain]
        return kept + [gid for gid in plain if gid not in kept]

    def set_ids_to_save(self) -> list[int]:
        """The sets it follows, old order first, for the same reason."""
        ticked = self.ticked_set_ids()
        kept = [sid for sid in self._loaded.set_ids if sid in ticked]
        return kept + [sid for sid in ticked if sid not in kept]

    # -- pictures ----------------------------------------------------------
    def _pick_files(self) -> None:
        """The Compose picker, for the same reason it is allowed there: the
        user just pressed the button that asks for it."""
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, ATTACH_LABEL, "", MEDIA_FILTER
        )
        self.add_pictures(chosen)

    def add_pictures(self, paths) -> int:
        """Pictures and videos, judged exactly as Compose judges them."""
        accepted, message, level = check_attachments(
            paths, [Path(p) for p in self._pictures]
        )
        self._pictures.extend(str(path) for path in accepted)
        if accepted:
            self._render_pictures()
        if message:
            self.notify(message, level)
        return len(accepted)

    def remove_picture(self, path: str) -> None:
        if path in self._pictures:
            self._pictures.remove(path)
            self._render_pictures()

    def pictures(self) -> list[str]:
        return list(self._pictures)

    def _render_pictures(self) -> None:
        clear(self.picture_box)
        self.refresh_summary()
        if not self._pictures:
            note = QLabel(NO_ATTACHMENTS)
            note.setObjectName("Muted")
            self.picture_box.addWidget(note)
            return
        for path in self._pictures:
            holder = card()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_S, theme.PAD_S)
            name = Path(path).name
            if not Path(path).exists():
                name += "  (file not found)"
            layout.addWidget(QLabel(name), 1)
            remove = QPushButton("Remove")
            remove.setObjectName("Link")
            remove.clicked.connect(lambda _c, p=path: self.remove_picture(p))
            layout.addWidget(remove)
            self.picture_box.addWidget(holder)

    # -- times and days ----------------------------------------------------
    def add_time(self, text: str = "") -> bool:
        if not text:
            text = next_time(self.times())
        hour, minute = recurrence.parse_hhmm(text)

        # row(), not a bare QWidget: it sits inside a card.
        line = row()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        entry = TimePicker(hour, minute)
        entry.timeChanged.connect(lambda _t: self.refresh_summary())
        layout.addWidget(entry)
        layout.addStretch(1)
        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=line, e=entry: self._remove_time(w, e))
        layout.addWidget(remove)

        self.time_box.addWidget(line)
        self._time_rows.append(entry)
        self.refresh_summary()
        return True

    def _remove_time(self, line: QWidget, entry: TimePicker) -> None:
        if len(self._time_rows) == 1:
            self.notify("A repeating post needs at least one time of day.", "warning")
            return
        self._time_rows.remove(entry)
        self.time_box.removeWidget(line)
        line.deleteLater()
        self.refresh_summary()

    def times(self) -> list[str]:
        return [e.time().toString("HH:mm") for e in self._time_rows]

    def days(self) -> list[int]:
        return [i for i, box in enumerate(self._day_boxes) if box.isChecked()]

    def rule(self):
        """The rule as set on screen, or None if it cannot be built.

        No days ticked is None rather than "every day": to `recurrence` an
        empty list means daily, the opposite of what seven empty boxes say.
        """
        if not self.days():
            return None
        try:
            return recurrence.build(self.times(), self.days())
        except recurrence.InvalidRecurrence:
            return None

    def _when_changed(self, rule) -> bool:
        try:
            before = recurrence.build(self._loaded.times, self._loaded.days)
        except recurrence.InvalidRecurrence:
            return True
        return rule != before

    # -- what is about to be saved -----------------------------------------
    def refresh_summary(self) -> None:
        if self._loading or self._loaded is None:
            return

        wordings = self.wordings()
        count = len(wordings)
        if count > 1:
            note = (
                f"{count} wordings in rotation. Each run shares them out across "
                "the groups, a different one to each in turn."
            )
        elif count == 1:
            note = "One wording: every run sends it to every group."
        else:
            note = "No wording: every run posts the pictures or video on their own."
        self.rotation_note.setText(note)

        rule = self.rule()
        if rule is None:
            set_note_lines(self.summary, [], ["Pick at least one day and one time."])
            return
        groups = self.ticked_group_ids()
        lines = [
            f"{recurrence.describe(rule)} · {len(groups)} "
            f"group{'s' if len(groups) != 1 else ''}"
        ]
        if not self._loaded.active:
            lines.append("Paused. Saving keeps it paused.")
        elif self._when_changed(rule):
            lines.append(
                f"Next run {clock.format_local(recurrence.next_occurrence(rule, utcnow()))}"
            )
        elif self._loaded.next_run_at is not None:
            lines.append(f"Next run {clock.format_local(self._loaded.next_run_at)}")
        follows = following(
            [self._sets[sid].name for sid in self.ticked_set_ids() if sid in self._sets]
        )
        if follows:
            lines.append(follows)
        set_note_lines(self.summary, lines)

    # -- doing it ----------------------------------------------------------
    def save(self) -> bool:
        schedule = (
            self.app.schedule_repo.get(self.schedule_id)
            if self.schedule_id is not None else None
        )
        if schedule is None:
            self.notify("That repeating post no longer exists.", "error")
            self.leave()
            return False

        wordings = self.wordings()
        # Pictures or a video alone may repeat (the user's choice, 2026-10-18).
        if not wordings and not self._pictures:
            self.notify(
                "A repeating post needs some text or a picture or video.", "error"
            )
            return False

        ticked = self.ticked_group_ids()
        if not ticked:
            self.notify("Tick at least one group.", "error")
            return False

        if not self.days():
            self.notify("Tick at least one day of the week.", "error")
            return False
        try:
            rule = recurrence.build(self.times(), self.days())
        except recurrence.InvalidRecurrence as exc:
            self.notify(str(exc), "error")
            return False

        # A new time is a new slot, found the way Resume finds one. Unchanged
        # times keep the slot already set, which the worker may have moved on
        # since this screen opened. A paused post gets its slot on Resume.
        next_run = KEEP
        if schedule.active and self._when_changed(rule):
            next_run = recurrence.next_occurrence(rule, utcnow())

        try:
            saved = self.app.schedule_repo.update(
                schedule.id,
                name=self.name_entry.text().strip(),
                bodies=wordings,
                group_ids=self.group_ids_to_save(),
                set_ids=self.set_ids_to_save(),
                times=list(rule.times),
                days=list(rule.days),
                media_paths=self._pictures,
                next_run_at=next_run,
            )
        except ValueError as exc:
            self.notify(str(exc), "error")
            return False

        self.notify(
            f"{saved.display_name} saved: {recurrence.describe(rule)}.", "success"
        )
        self.leave()
        return True

    def leave(self) -> None:
        """Back to Publish. Nothing is written unless Save already did it."""
        self.app.show_view("publish")
