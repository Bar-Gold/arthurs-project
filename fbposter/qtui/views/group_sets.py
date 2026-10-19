"""Group sets: groups kept together under one name, picked as one item.

The user's request (2026-10-15). Sets are made and changed here, on a screen of
their own, and picked whole on the Groups screen and on a repeating post's edit
screen: a set is never picked group by group.

A repeating post linked to a set follows it, so saving a set changes where
those posts go from their next run. Saving never asks first, and judges
nothing: there are no posting rules (the user's choice, 2026-10-18).

Deleting a set leaves the repeating posts that used it with its groups as
ordinary groups, and Undo puts the set back.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


from .. import theme
from ..widgets import card, clear, row
from .groups import GroupRow, matches

# How many group names a set's card spells out before "and N more".
NAMES_SHOWN = 6
NAME_PLACEHOLDER = "e.g. Tel Aviv"
SEARCH_PLACEHOLDER = "Search your groups"


def group_count(count: int) -> str:
    return f"{count} group{'s' if count != 1 else ''}"


def name_list(names: list[str], shown: int = NAMES_SHOWN) -> str:
    """The first few names, and how many more."""
    if len(names) <= shown:
        return ", ".join(names)
    return f"{', '.join(names[:shown])} and {len(names) - shown} more"


class GroupSetsView(QWidget):
    title = "Group sets"
    subtitle = (
        "Keep groups that go together as one set, then pick the whole set in "
        "Create a post, step 2. A repeating post that uses a set follows it: a "
        "group added here gets that post from its next run."
    )

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # The set being edited, None for a new one. Only meaningful while the
        # editor is open.
        self.editing_id: int | None = None
        self._editor_open = False
        self._list_shown = None
        self._boxes: dict[int, QCheckBox] = {}
        self._editor_rows: dict[int, QWidget] = {}
        self._editor_groups: dict[int, object] = {}

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

        self.list_page = row()
        self._build_list(self.list_page)
        outer.addWidget(self.list_page, 1)

        self.editor_page = row()
        self._build_editor(self.editor_page)
        self.editor_page.setVisible(False)
        outer.addWidget(self.editor_page, 1)

    def on_show(self) -> None:
        # An open editor is left as it is: walking to another screen and back
        # must not cost the user what they have ticked so far.
        if not self._editor_open:
            self.refresh()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    # -- the list of sets --------------------------------------------------
    def _build_list(self, page: QWidget) -> None:
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_S)

        tools = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("SectionHeading")
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        # Not #Primary: this screen is not a step in the flow.
        self.new_button = QPushButton("New set")
        self.new_button.clicked.connect(lambda: self.open_editor())
        tools.addWidget(self.new_button)
        column.addLayout(tools)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.set_rows = QVBoxLayout(holder)
        self.set_rows.setContentsMargins(0, 0, 0, 0)
        self.set_rows.setSpacing(theme.PAD_XS)
        area.setWidget(holder)
        column.addWidget(area, 1)

    def _list_snapshot(self, sets, names, used) -> tuple:
        """Everything a card draws: the set, its groups' names, its posts."""
        return tuple(
            (
                s.id,
                s.name,
                tuple(names.get(gid, "") for gid in s.group_ids),
                tuple(used.get(s.id, ())),
            )
            for s in sets
        )

    def refresh(self, force: bool = False) -> None:
        sets = self.app.group_set_repo.list()
        names = {g.id: g.display_name for g in self.app.group_repo.list()}
        schedule_names = self.app.schedule_repo.names_by_id()
        used = {
            s.id: [schedule_names[sid] for sid in self.app.group_set_repo.used_by(s.id)
                   if sid in schedule_names]
            for s in sets
        }
        self.count_label.setText(
            f"{len(sets)} set{'s' if len(sets) != 1 else ''}" if sets else ""
        )
        snapshot = self._list_snapshot(sets, names, used)
        if not force and snapshot == self._list_shown:
            return
        self._list_shown = snapshot

        clear(self.set_rows)
        if not sets:
            note = QLabel("No group sets yet. Press New set to make one.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.set_rows.addWidget(note)
        for group_set in sets:
            self.set_rows.addWidget(
                self._card(group_set, [names.get(gid, "") for gid in group_set.group_ids],
                           used.get(group_set.id, []))
            )
        self.set_rows.addStretch(1)

    def _card(self, group_set, group_names: list[str], used: list[str]) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        layout.setSpacing(theme.PAD_XS)

        header = QHBoxLayout()
        header.setSpacing(theme.PAD_S)
        title = QLabel(group_set.name)
        title.setStyleSheet("font-weight: 600;")
        header.addWidget(title)
        count = QLabel(group_count(len(group_set.group_ids)))
        count.setObjectName("Muted")
        header.addWidget(count)
        header.addStretch(1)
        edit = QPushButton("Edit")
        edit.clicked.connect(lambda _c, s=group_set.id: self.open_editor(s))
        header.addWidget(edit)
        remove = QPushButton("Delete")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, s=group_set.id: self.delete_set(s))
        header.addWidget(remove)
        layout.addLayout(header)

        members = QLabel(
            name_list(group_names) if group_names
            else "No groups left: every one was removed from the list."
        )
        members.setWordWrap(True)
        layout.addWidget(members)

        if used:
            posts = QLabel(f"Used by {', '.join(used)}.")
            posts.setObjectName("Muted")
            posts.setWordWrap(True)
            layout.addWidget(posts)
        return box

    # -- deleting ------------------------------------------------------------
    def delete_set(self, set_id: int) -> None:
        deletion = self.app.group_set_repo.delete(set_id)
        self.app.selected_sets.discard(set_id)
        self.refresh()
        if deletion is None:
            return
        name = deletion.group_set.name
        message = f"{name} deleted."
        posts = len(deletion.links)
        if posts:
            # The user's choice: nothing stops posting because a set went.
            message += (
                f" The {posts} repeating post{'s' if posts != 1 else ''} that used it "
                f"{'keep' if posts != 1 else 'keeps'} its groups."
            )
        self.app.toast(message, "info", undo=lambda: self._restore(deletion))

    def _restore(self, deletion) -> str | None:
        if not self.app.group_set_repo.restore(deletion):
            return None
        self.refresh()
        return f"{deletion.group_set.name} is back."

    # -- the editor ----------------------------------------------------------
    def _build_editor(self, page: QWidget) -> None:
        column = QVBoxLayout(page)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(theme.PAD_XS)

        self.editor_heading = QLabel("New set")
        self.editor_heading.setObjectName("SectionHeading")
        column.addWidget(self.editor_heading)

        name_card = card()
        name_layout = QVBoxLayout(name_card)
        name_layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        name_layout.setSpacing(theme.PAD_XS)
        name_label = QLabel("Name")
        name_layout.addWidget(name_label)
        self.name_entry = QLineEdit()
        self.name_entry.setPlaceholderText(NAME_PLACEHOLDER)
        self.name_entry.setAccessibleName("Name of the group set")
        name_layout.addWidget(self.name_entry)
        column.addWidget(name_card)
        column.addSpacing(theme.PAD_S)

        tools = QHBoxLayout()
        self.ticked_label = QLabel("")
        self.ticked_label.setObjectName("SectionHeading")
        tools.addWidget(self.ticked_label)
        tools.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search.setAccessibleName(SEARCH_PLACEHOLDER)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.apply_search())
        tools.addWidget(self.search, 1)
        column.addLayout(tools)

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.editor_rows = QVBoxLayout(holder)
        self.editor_rows.setContentsMargins(0, 0, 0, 0)
        self.editor_rows.setSpacing(theme.PAD_XS)
        area.setWidget(holder)
        column.addWidget(area, 1)

        footer = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(lambda: self.close_editor())
        footer.addWidget(self.cancel_button)
        footer.addStretch(1)
        self.save_button = QPushButton("Save set")
        self.save_button.setObjectName("Primary")
        self.save_button.clicked.connect(lambda: self.save())
        footer.addWidget(self.save_button)
        column.addLayout(footer)

    def open_editor(self, set_id: int | None = None) -> bool:
        """New set, or Edit on a set's card. False if that set has gone."""
        group_set = None
        if set_id is not None:
            group_set = self.app.group_set_repo.get(set_id)
            if group_set is None:
                self.notify("That group set no longer exists.", "error")
                self.refresh()
                return False
        self.editing_id = set_id
        self.editor_heading.setText(f"Edit {group_set.name}" if group_set else "New set")
        self.name_entry.setText(group_set.name if group_set else "")
        self.search.clear()
        ticked = set(group_set.group_ids) if group_set else set()

        clear(self.editor_rows)
        self._boxes = {}
        self._editor_rows = {}
        groups = self.app.group_repo.list()
        self._editor_groups = {group.id: group for group in groups}
        self.empty_note = QLabel("No groups yet. Get them in Create a post, step 2.")
        self.empty_note.setObjectName("Muted")
        self.empty_note.setWordWrap(True)
        self.editor_rows.addWidget(self.empty_note)
        for group in groups:
            box = QCheckBox(group.display_name)
            box.setStyleSheet("font-weight: 600;")
            box.setChecked(group.id in ticked)
            box.toggled.connect(lambda _on: self.refresh_ticked())
            holder = GroupRow(box)
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_S, theme.PAD_S)
            layout.addWidget(box, 1)
            if group.members:
                members = QLabel(group.members)
                members.setObjectName("Muted")
                layout.addWidget(members)
            self.editor_rows.addWidget(holder)
            self._boxes[group.id] = box
            self._editor_rows[group.id] = holder
        self.editor_rows.addStretch(1)
        self.apply_search()
        self.refresh_ticked()

        self._editor_open = True
        self.list_page.setVisible(False)
        self.editor_page.setVisible(True)
        self.name_entry.setFocus()
        return True

    def close_editor(self) -> None:
        """Back to the list. Nothing is written unless Save already did it."""
        self._editor_open = False
        self.editing_id = None
        self.editor_page.setVisible(False)
        self.list_page.setVisible(True)
        self.refresh()

    def ticked_group_ids(self) -> list[int]:
        """Ticked groups, in the order the list shows them."""
        return [gid for gid, box in self._boxes.items() if box.isChecked()]

    def tick(self, group_ids, on: bool = True) -> None:
        """Tick groups by id, as a click on their rows would."""
        for group_id in group_ids:
            box = self._boxes.get(group_id)
            if box is not None:
                box.setChecked(on)

    def refresh_ticked(self) -> None:
        self.ticked_label.setText(f"{group_count(len(self.ticked_group_ids()))} ticked")

    def apply_search(self) -> None:
        """Hide the rows that do not match. A hidden ticked group stays in."""
        query = self.search.text()
        shown = 0
        for group_id, holder in self._editor_rows.items():
            visible = matches(self._editor_groups[group_id], query)
            holder.setVisible(visible)
            shown += visible
        if not self._editor_rows:
            self.empty_note.setText("No groups yet. Get them in Create a post, step 2.")
        elif not shown:
            self.empty_note.setText(f"No group matches \"{query.strip()}\".")
        self.empty_note.setVisible(not shown)

    # -- saving --------------------------------------------------------------
    def save(self) -> bool:
        name = self.name_entry.text()
        group_ids = self.ticked_group_ids()
        repo = self.app.group_set_repo
        set_id = self.editing_id
        if set_id is not None and repo.get(set_id) is None:
            self.notify("That group set no longer exists.", "error")
            self.close_editor()
            return False
        try:
            saved = (
                repo.update(set_id, name, group_ids) if set_id is not None
                else repo.create(name, group_ids)
            )
        except ValueError as exc:
            self.notify(str(exc), "error")
            return False

        self.notify(f"{saved.name} saved: {group_count(len(saved.group_ids))}.", "success")
        self.close_editor()
        return True
