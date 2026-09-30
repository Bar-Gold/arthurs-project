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

Saving judges the rules exactly as creating does (`publish.schedule_violations`),
but only a rule the post did not already have "Post anyway" for is offered
again. A rule it no longer breaks is dropped from the list, so the card never
claims an exception that is not in use.
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
from fbposter.guards import rule_labels
from fbposter.text import strip_invisible

from .. import theme
from ..widgets import card, clear, row
from .compose import IMAGE_FILTER
from .publish import (
    ANYWAY_EFFECT,
    ANYWAY_STILL,
    DEFAULT_TIMES,
    REPEAT,
    RIGHT_COLUMN_SCROLLBAR,
    RIGHT_COLUMN_WIDTH,
    TimeEntry,
    schedule_violations,
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
        self._time_rows: list[TimeEntry] = []
        self._day_boxes: list[QCheckBox] = []
        self._offered: frozenset[str] = frozenset()

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
        outer.addSpacing(theme.PAD_S)

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
        pictures_label = QLabel("Pictures")
        pictures_label.setObjectName("SectionHeading")
        pictures_header.addWidget(pictures_label)
        pictures_header.addStretch(1)
        self.attach_button = QPushButton("Attach images")
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
        # Four and three, as on Publish: seven across clips the labels.
        days_grid = QGridLayout()
        days_grid.setSpacing(theme.PAD_XS)
        for index, name in enumerate(recurrence.DAY_NAMES):
            box = QCheckBox(name)
            box.stateChanged.connect(lambda _s: self.refresh_summary())
            self._day_boxes.append(box)
            days_grid.addWidget(box, index // 4, index % 4)
        layout.addLayout(days_grid)
        column.addWidget(picker)

        self.summary = QLabel("")
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

        self._build_override(column)

        self.save_button = QPushButton("Save changes")
        self.save_button.setObjectName("Primary")
        # A lambda, because clicked() would pass its `checked` flag as `allow`.
        self.save_button.clicked.connect(lambda: self.save())
        column.addWidget(self.save_button)

        self.back_button = QPushButton("← Back without saving")
        self.back_button.clicked.connect(lambda: self.leave())
        column.addWidget(self.back_button)

        column.addStretch(1)

    def _build_override(self, column: QVBoxLayout) -> None:
        """The same panel Publish shows, in place of Save, for the same reason."""
        self.override_card = card()
        layout = QVBoxLayout(self.override_card)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        layout.setSpacing(theme.PAD_S)
        title = QLabel("This would break the posting rules")
        title.setObjectName("SectionHeading")
        title.setStyleSheet(f"color: {theme.C['WARNING']};")
        layout.addWidget(title)
        self.override_list = QLabel("")
        self.override_list.setWordWrap(True)
        layout.addWidget(self.override_list)
        self.override_effect = QLabel(ANYWAY_EFFECT[REPEAT] + ANYWAY_STILL)
        self.override_effect.setWordWrap(True)
        layout.addWidget(self.override_effect)
        why = QLabel(
            "The rules keep the account from looking automated, so break them "
            "only on purpose. Cancel saves nothing, so you can change it instead."
        )
        why.setObjectName("Muted")
        why.setWordWrap(True)
        layout.addWidget(why)
        buttons = QHBoxLayout()
        self.anyway_button = QPushButton("Post anyway")
        self.anyway_button.setObjectName("Danger")
        self.anyway_button.clicked.connect(lambda: self.post_anyway())
        buttons.addWidget(self.anyway_button, 1)
        self.keep_rules_button = QPushButton("Cancel")
        self.keep_rules_button.clicked.connect(lambda: self.dismiss_override())
        buttons.addWidget(self.keep_rules_button, 1)
        layout.addLayout(buttons)
        self.override_card.hide()
        column.addWidget(self.override_card)

    def offer_override(self, violations) -> None:
        self._offered = frozenset(v.rule for v in violations)
        self.override_list.setText("\n".join(f"•  {v.message}" for v in violations))
        self.override_card.show()
        self.save_button.hide()
        self.summary.hide()

    def dismiss_override(self) -> None:
        self._offered = frozenset()
        self.override_card.hide()
        self.save_button.show()
        self.summary.show()

    def post_anyway(self) -> bool:
        """Save again, allowed to break exactly what was on the panel."""
        allowed = self._offered
        self.dismiss_override()
        return self.save(allow=allowed)

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
            self.dismiss_override()
            self.heading.setText(f"{self.title}: {schedule.display_name}")
            self.name_entry.setText(schedule.name)

            clear(self.wording_box)
            self._wordings = []
            for body in schedule.bodies:
                self.add_wording(body)

            active = self.app.group_repo.list()
            clear(self.group_box)
            self._group_boxes = {}
            for group in active:
                box = QCheckBox(group.display_name)
                box.setChecked(group.id in schedule.group_ids)
                box.stateChanged.connect(lambda _s: self.refresh_summary())
                self.group_box.addWidget(box)
                self._group_boxes[group.id] = box
            if not active:
                note = QLabel("No groups yet — add them on the Groups screen.")
                note.setObjectName("Muted")
                self.group_box.addWidget(note)

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
        """Ticked groups from the current list, in the order it shows them."""
        return [gid for gid, box in self._group_boxes.items() if box.isChecked()]

    def group_ids_to_save(self) -> list[int]:
        """What the post will hold: its old order first, new groups after.

        Keeping the old order keeps the rotation where it was -- the position
        is part of which wording a group gets next. Only ticked groups are
        kept, and only groups on the list can be ticked, so a group removed
        from the list is never written back into the post.
        """
        ticked = self.ticked_group_ids()
        kept = [gid for gid in self._loaded.group_ids if gid in ticked]
        return kept + [gid for gid in ticked if gid not in kept]

    # -- pictures ----------------------------------------------------------
    def _pick_files(self) -> None:
        """The Compose picker, for the same reason it is allowed there: the
        user just pressed the button that asks for it."""
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "Attach images", "", IMAGE_FILTER
        )
        self.add_pictures(chosen)

    def add_pictures(self, paths) -> int:
        added = 0
        for name in paths:
            path = str(Path(name))
            if path not in self._pictures:
                self._pictures.append(path)
                added += 1
        if added:
            self._render_pictures()
            self.notify(f"Attached {added} file{'s' if added != 1 else ''}.", "success")
        return added

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
            note = QLabel("No images attached.")
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
        if len(self._time_rows) >= recurrence.MAX_TIMES_PER_DAY:
            self.notify(
                f"{recurrence.MAX_TIMES_PER_DAY} times a day is the ceiling — more "
                "than that stops looking like a person posting.",
                "warning",
            )
            return False
        if not text:
            text = DEFAULT_TIMES[min(len(self._time_rows), len(DEFAULT_TIMES) - 1)]
        hour, minute = recurrence.parse_hhmm(text)

        # row(), not a bare QWidget: it sits inside a card.
        line = row()
        layout = QHBoxLayout(line)
        layout.setContentsMargins(0, 0, 0, 0)
        entry = TimeEntry(hour, minute)
        entry.timeChanged.connect(lambda _t: self.refresh_summary())
        layout.addWidget(entry, 1)
        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, w=line, e=entry: self._remove_time(w, e))
        layout.addWidget(remove)

        self.time_box.addWidget(line)
        self._time_rows.append(entry)
        self.refresh_summary()
        return True

    def _remove_time(self, line: QWidget, entry: TimeEntry) -> None:
        if len(self._time_rows) == 1:
            self.notify("A repeating post needs at least one time of day.", "warning")
            return
        self._time_rows.remove(entry)
        self.time_box.removeWidget(line)
        line.deleteLater()
        self.refresh_summary()

    def times(self) -> list[str]:
        return [e.time().toString("HH:mm") for e in self._time_rows]

    def commit_typed_times(self) -> None:
        """See PublishView.commit_typed_times: a click no longer commits."""
        for entry in self._time_rows:
            entry.interpretText()

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
        # Anything edited and the offer on screen describes a post that no
        # longer exists; it is withdrawn, as on Publish.
        if not self.override_card.isHidden():
            self.dismiss_override()

        wordings = self.wordings()
        count = len(wordings)
        self.rotation_note.setText(
            f"{count} wording{'s' if count != 1 else ''} in rotation. Each run "
            "gives every group a wording it has not had yet, as long as one is left."
            if count > 1
            else "With one wording, each group gets it once, and every run after "
            "that would repeat it, which the rules refuse. Add one or two more."
        )

        rule = self.rule()
        if rule is None:
            self.summary.setText("Pick at least one day and one time.")
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
        found = schedule_violations(self.app, rule, groups, wordings, history=False)
        lines.extend(v.message for v in found)
        still = frozenset(v.rule for v in found) & self._loaded.overrides
        if still:
            lines.append(f"Post anyway: allowed to break the {rule_labels(still)}.")
        note = recurrence.variation_note(len(groups), count)
        if note is not None:
            lines.append(note)
        self.summary.setText("\n".join(lines))

    # -- doing it ----------------------------------------------------------
    def save(self, allow: frozenset[str] = frozenset()) -> bool:
        """`allow` is set only by post_anyway(): the rules the user accepted."""
        self.commit_typed_times()
        schedule = (
            self.app.schedule_repo.get(self.schedule_id)
            if self.schedule_id is not None else None
        )
        if schedule is None:
            self.notify("That repeating post no longer exists.", "error")
            self.leave()
            return False

        wordings = self.wordings()
        if not wordings and self._pictures:
            self.notify(
                "A repeating post needs some text as well as pictures — each run "
                "gives every group a wording it has not had yet.",
                "error",
            )
            return False
        if not wordings:
            self.notify("A repeating post needs at least one wording.", "error")
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

        # The rules creating it applied, judged again. Only what the post was
        # not already allowed to break is asked about.
        violations = schedule_violations(self.app, rule, ticked, wordings)
        already = schedule.overrides | allow
        new = [v for v in violations if v.rule not in already]
        if new:
            self.offer_override(new)
            return False
        overrides = frozenset(v.rule for v in violations)

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
                times=list(rule.times),
                days=list(rule.days),
                media_paths=self._pictures,
                overrides=overrides,
                next_run_at=next_run,
            )
        except ValueError as exc:
            self.notify(str(exc), "error")
            return False

        note = recurrence.variation_note(len(ticked), len(wordings))
        if note is not None:
            self.notify(f"{saved.display_name} saved. {note}", "warning")
        elif overrides:
            self.notify(
                f"{saved.display_name} saved: {recurrence.describe(rule)}, posting "
                f"anyway despite the {rule_labels(overrides)}.",
                "warning",
            )
        else:
            self.notify(
                f"{saved.display_name} saved: {recurrence.describe(rule)}.", "success"
            )
        self.leave()
        return True

    def leave(self) -> None:
        """Back to Publish. Nothing is written unless Save already did it."""
        self.dismiss_override()
        self.app.show_view("publish")
