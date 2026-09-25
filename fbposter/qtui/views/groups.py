"""Groups screen: who the post is going to, and the list it is chosen from.

The middle step of the flow — Compose says what, this says where, Publish says
when. Picking recipients and managing the list are the same screen on purpose:
the moment you notice a group is missing is the moment you are choosing groups,
so the "add a group" box is right here rather than somewhere else.

The ticks themselves live on the window (`App.selected_groups`), because
Compose opens a wording tab per chosen group and Publish sends to them; three
screens reading one set is the only way they cannot disagree.
"""

from __future__ import annotations

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

from fbposter import chrome
from fbposter.errors import FBPosterError

from .. import theme
from ..widgets import card, clear

# What a row says while its group's name is being fetched. It used to show the
# group's number instead, which is meaningless to the person who pasted a link.
LOOKING_UP = "Looking up the group's name…"


class GroupsView(QWidget):
    title = "Groups"
    subtitle = (
        "Tick the groups this post goes to. Only groups you can post in — "
        "admin groups belong in Facebook's own scheduler."
    )

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        # What the rows currently show; None means nothing has been drawn yet.
        self._snapshot_shown = None
        self._naming = False
        # Groups whose name is being fetched right now, or is queued to be.
        self._looking_up: set[int] = set()
        # A lookup asked for while one was already running. It used to be
        # dropped, and the group it was for then showed its number until this
        # screen was next opened -- which could be minutes.
        self._lookup_again = False
        # Added on this screen and still nameless: the toast names them when
        # the name arrives, rather than announcing a number.
        self._announce: set[int] = set()
        self._checkboxes: dict[int, QCheckBox] = {}

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
        outer.addSpacing(theme.PAD_S)

        # A visible label, not just a placeholder: placeholder text vanishes the
        # moment you type, taking the only explanation of the field with it.
        add_label = QLabel("Add a group")
        add_label.setObjectName("SectionHeading")
        outer.addWidget(add_label)

        entry = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("https://www.facebook.com/groups/…")
        self.url_entry.setAccessibleName("Facebook group URL")
        self.url_entry.returnPressed.connect(self.add_group)
        entry.addWidget(self.url_entry, 1)
        # Not #Primary -- see the note on the Compose attach button. The accent
        # here belongs to "Next: when to send".
        add = QPushButton("Add group")
        add.clicked.connect(self.add_group)
        entry.addWidget(add)
        outer.addLayout(entry)
        outer.addSpacing(theme.PAD_M)

        tools = QHBoxLayout()
        self.count_label = QLabel("")
        self.count_label.setObjectName("SectionHeading")
        tools.addWidget(self.count_label)
        tools.addStretch(1)
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self.set_all(True))
        tools.addWidget(select_all)
        # Not named `clear`: that shadows widgets.clear, the helper every
        # rebuild in this file depends on.
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(lambda: self.set_all(False))
        tools.addWidget(clear_button)
        outer.addLayout(tools)

        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.holder = QWidget()
        self.rows = QVBoxLayout(self.holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(theme.PAD_XS)
        self.area.setWidget(self.holder)
        outer.addWidget(self.area, 1)

        footer = QHBoxLayout()
        back = QPushButton("← Back to Compose")
        back.clicked.connect(lambda: self.app.show_view("compose"))
        footer.addWidget(back)
        footer.addStretch(1)
        self.next_button = QPushButton("Next: when to send →")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(lambda: self.app.show_view("publish"))
        footer.addWidget(self.next_button)
        outer.addLayout(footer)

    def on_show(self) -> None:
        self.refresh()
        self._fetch_missing_names()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    # -- selection ---------------------------------------------------------
    def selected_group_ids(self) -> list[int]:
        return self.app.selected_group_ids()

    def toggle(self, group_id: int, selected: bool) -> None:
        self.app.set_group_selected(group_id, selected)
        self.refresh_count()

    def set_all(self, selected: bool) -> None:
        for group_id, box in self._checkboxes.items():
            box.blockSignals(True)
            box.setChecked(selected)
            box.blockSignals(False)
            self.app.set_group_selected(group_id, selected)
        self.refresh_count()

    def refresh_count(self) -> None:
        chosen = len(self.selected_group_ids())
        total = len(self._checkboxes)
        self.count_label.setText(f"{chosen} of {total} selected")
        self.next_button.setEnabled(chosen > 0)
        # Compose shows one wording tab per chosen group, so it has to hear
        # about this even though it is not the screen being looked at.
        compose = self.app.views.get("compose")
        if compose is not None:
            compose.refresh_tabs()

    # -- actions -----------------------------------------------------------
    def add_group(self) -> bool:
        raw = self.url_entry.text().strip()
        if not raw:
            return False
        try:
            group = self.app.group_repo.add_from_url(raw)
        except FBPosterError as exc:
            self.notify(str(exc), "error")
            return False

        # Pasting the link of a removed group brings it back, history and all.
        # Saying so matters: the user is about to wonder why the repeat guard
        # already knows what this group has been sent.
        restored = bool(self.app.group_repo.recent_bodies(group.id, limit=1))

        self.url_entry.clear()
        # A group added here was added to be posted to, so it starts ticked.
        self.app.set_group_selected(group.id, True)
        if restored:
            self.notify(
                f"Brought {group.display_name} back, with what it has already been sent.",
                "success",
            )
        elif group.name:
            self.notify(f"Added {group.name}.", "success")
        else:
            # Never "Added 1697911281266837." -- the name follows in seconds.
            self._announce.add(group.id)
            self.notify("Group added. Looking up its name…", "success")
        # Before the refresh, so the new row is drawn saying it is looking up
        # its name, and never flashes the number first.
        self._fetch_missing_names(first=group.id)
        self.refresh()
        return True

    def remove_group(self, group_id: int) -> None:
        self.app.group_repo.remove(group_id)
        self.app.set_group_selected(group_id, False)
        self.refresh()
        self.notify("Group removed. Paste its link again to bring it back.", "info")

    # -- names -------------------------------------------------------------
    def look_up_missing_names(self) -> None:
        """Called when Chrome becomes reachable, which is the first moment a
        group added while it was still starting can have its name read."""
        self._fetch_missing_names()

    def _fetch_missing_names(
        self, first: int | None = None, skip: frozenset[int] = frozenset()
    ) -> None:
        """Look up display names, but never at the cost of blocking the window.

        Guarded on the debug port being open, because the namer is injectable
        precisely so tests never reach the live site.

        `first` is a group just added: it goes to the front, ahead of any group
        whose name could not be read before. `skip` is what an earlier sweep in
        the same chain already tried -- a name that could not be read a moment
        ago will not be read by asking again straight away.
        """
        missing = [g for g in self.app.group_repo.missing_names() if g.id not in skip]
        if self._naming:
            # Queued, not dropped; finish() runs it.
            if missing:
                self._lookup_again = True
                if first is not None:
                    self._looking_up.add(first)
            return
        if not missing:
            return

        missing.sort(key=lambda group: group.id != first)
        self._naming = True
        self._looking_up = {group.id for group in missing}
        self.refresh()
        urls = [group.url for group in missing]
        attempted = skip | {group.id for group in missing}

        def work():
            # The probe belongs on this thread, not the one drawing the window.
            # It is a synchronous HTTP call to the debug port -- 14ms when
            # Chrome answers, and up to PROBE_TIMEOUT_S (a full second) of a
            # frozen window when something holds the port without replying.
            # This screen ran it on every single show.
            if chrome.probe() is None:
                return None  # not "no names": nothing could be asked at all
            return self.app.group_namer.names_for(urls)

        def done(found):
            for group in missing:
                name = (found or {}).get(group.url, "")
                if name:
                    self.app.group_repo.set_name(group.id, name)
                    if group.id in self._announce:
                        self.notify(f"Added {name}.", "success")
                elif group.id in self._announce and found is None:
                    self.notify(
                        "Chrome isn't connected yet, so the new group shows its "
                        "number until its name can be read.",
                        "warning",
                    )
                self._announce.discard(group.id)
            finish()

        def failed(_exc):
            self._announce.difference_update(group.id for group in missing)
            finish()

        def finish():
            self._naming = False
            self._looking_up.difference_update(attempted)
            if self._lookup_again:
                self._lookup_again = False
                self._fetch_missing_names(skip=attempted)
            if not self._naming:
                self._looking_up.clear()
            self.refresh()

        self.app.run_in_background(work, done, failed)

    # -- rendering ---------------------------------------------------------
    def _snapshot(self, groups) -> tuple:
        """Everything a row draws. Rebuilding twelve of them cost 30ms on
        every visit to this screen, to redraw the identical list."""
        selected = self.app.selected_groups
        return tuple(
            (g.id, self._label(g), g.id in selected)
            for g in groups
        )

    def _label(self, group) -> str:
        if not group.name and group.id in self._looking_up:
            return LOOKING_UP
        return group.display_name

    def refresh(self, force: bool = False) -> None:
        groups = self.app.group_repo.list()
        snapshot = self._snapshot(groups)
        if not force and snapshot == self._snapshot_shown and self._checkboxes:
            # The count still gets restated: it is one setText, and it also
            # reaches Compose, which needs to hear about the selection.
            self.refresh_count()
            return
        self._snapshot_shown = snapshot

        clear(self.rows)
        self._checkboxes = {}
        if not groups:
            note = QLabel("No groups yet. Paste a group URL above.")
            note.setObjectName("Muted")
            self.rows.addWidget(note)
            self.rows.addStretch(1)
            self.refresh_count()
            return

        for group in groups:
            self.rows.addWidget(self._row(group))
        self.rows.addStretch(1)
        self.refresh_count()

    def _row(self, group) -> QWidget:
        holder = card()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_S, theme.PAD_S)

        label = self._label(group)
        box = QCheckBox(label)
        box.setChecked(group.id in self.app.selected_groups)
        box.setStyleSheet(
            f"color: {theme.C['TEXT_MUTED']};" if label == LOOKING_UP
            else "font-weight: 600;"
        )
        box.toggled.connect(lambda on, gid=group.id: self.toggle(gid, on))
        self._checkboxes[group.id] = box
        layout.addWidget(box, 1)

        # No cooldown control. There is one rule for every group -- the
        # default gap, 8h -- and breaking it is a choice made per post, with
        # "Post anyway" on the Publish screen, not a setting buried in a row.

        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, gid=group.id: self.remove_group(gid))
        layout.addWidget(remove)
        return holder
