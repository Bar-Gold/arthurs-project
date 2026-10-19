"""Groups screen: who the post is going to, and the list it is chosen from.

The middle step of the flow: Compose says what, this says where, Publish says
when. Picking recipients and managing the list are the same screen on purpose:
the moment you notice a group is missing is the moment you are choosing groups,
so "Get my groups from Facebook" is right here rather than somewhere else.

There is no box for pasting a group's link any more (removed 2026-10-14): every
group comes off Facebook. A search box narrows the list instead, and a removed
group comes back from the removed groups under the list.

The ticks themselves live on the window (`App.selected_groups`), because
Compose opens a wording tab per chosen group and Publish sends to them; three
screens reading one set is the only way they cannot disagree.

Group sets are picked here too, each as one item (`App.selected_sets`); they
are made on their own screen. A group a picked set brings shows ticked and
greyed with the set's name beside it, and cannot be unticked on its own: the
user's choice (2026-10-15) was that a set is picked whole or not at all.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from fbposter import chrome
from fbposter.connection import ConnectionState
from fbposter.text import strip_invisible

from .. import theme
from ..widgets import card, clear, row

# What a row says while its group's name is being fetched. It used to show the
# group's number instead, which is meaningless to the person who pasted a link.
LOOKING_UP = "Looking up the group's name…"

# Set at the first login and cleared once the groups have been read off
# Facebook. A setting, not memory, so a read that fails is tried again at the
# next connection, including after a restart.
IMPORT_PENDING_KEY = "groups_import_pending"
# Set when the user switches Facebook account, and cleared once the list has
# been replaced with the new account's groups (`GroupRepo.replace_with`). While
# it is set, a read replaces the list instead of adding to it.
REPLACE_PENDING_KEY = "groups_replace_pending"
IMPORT_LABEL = "Get my groups from Facebook"
IMPORTING_LABEL = "Getting your groups…"
SEARCH_PLACEHOLDER = "Search your groups"
SHOW_REMOVED = "Show removed groups ({count})"
HIDE_REMOVED = "Hide removed groups"
# Group sets offered per line above the list.
SET_COLUMNS = 3


def set_label(group_set) -> str:
    """A set as it is offered for picking: its name and how many groups."""
    return f"{group_set.name} ({len(group_set.group_ids)})"


def in_sets(names) -> str:
    """Beside a group a picked set brings: which set it comes from."""
    return "in " + ", ".join(names)


def taken_out_of(schedules: int, batches: int, sets: int) -> str:
    """"2 repeating posts, 1 queued post and 1 group set", or "" for none."""
    parts = [
        f"{count} {noun}{'s' if count != 1 else ''}"
        for count, noun in (
            (schedules, "repeating post"), (batches, "queued post"), (sets, "group set")
        )
        if count
    ]
    if len(parts) > 1:
        return f"{', '.join(parts[:-1])} and {parts[-1]}"
    return parts[0] if parts else ""


def matches(group, query: str) -> bool:
    """Whether a row matches the search: the name it shows, ignoring case.

    A group with no name yet shows its number, so the number matches then.
    Invisible marks are dropped from both sides, because a name read off
    Facebook can carry them and the user never types them.
    """
    wanted = strip_invisible(query).strip().casefold()
    return not wanted or wanted in strip_invisible(group.display_name).casefold()


class GroupRow(QFrame):
    """A group's card, which ticks and unticks it wherever it is clicked.

    The user's request (2026-10-14): the little box alone was too small a
    target. A click on the box itself is the box's own, and one on "Remove" is
    the button's; Qt passes every other click on the card up to here. The box
    still takes the keyboard focus, so Tab and Space work as before.
    """

    def __init__(self, box: QCheckBox) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.box = box
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt's name
        # Accepted, so the release comes here too.
        if event.button() == Qt.LeftButton:
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt's name
        # Only a release still over the card, as for any button: dragging off
        # it is how a person takes a click back. `toggle()` ignores a box being
        # disabled, so that is checked here: a group a picked set brings is
        # not ticked or unticked on its own.
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            if self.box.isEnabled():
                self.box.toggle()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class GroupsView(QWidget):
    title = "Groups"
    # The admin advice is gone: the user chose (2026-10-13) to have every
    # group added, the ones they manage included.
    subtitle = "Tick the groups this post goes to."

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
        self._checkboxes: dict[int, QCheckBox] = {}
        # Every row as drawn, so a search hides and shows them rather than
        # rebuilding fifty cards on every keystroke.
        self._rows: dict[int, QWidget] = {}
        self._groups: dict[int, object] = {}
        self._removed_rows: dict[int, QWidget] = {}
        self._removed_groups: dict[int, object] = {}
        self._show_removed = False
        self.removed_heading: QLabel | None = None
        # Made by refresh(); None until the list is first drawn.
        self.empty_note: QLabel | None = None
        # True while the groups are being read off Facebook.
        self._importing = False
        # The group set boxes, and the sets they were drawn from. Picking one
        # does not rebuild them (the box clicked would be destroyed under the
        # click); only a set being made, renamed, refilled or deleted does.
        self._set_boxes: dict[int, QCheckBox] = {}
        self._sets_shown = None
        # Each group a picked set brings, with the sets' names.
        self._covered: dict[int, list[str]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        # Hidden inside Create a post, which has one title for all three steps.
        self.heading = QLabel(self.title)
        self.heading.setObjectName("Title")
        outer.addWidget(self.heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        sub.setWordWrap(True)
        outer.addWidget(sub)
        outer.addSpacing(theme.HEADER_GAP)

        top = QHBoxLayout()
        # A placeholder is enough here, unlike the old link box: what is
        # typed into a search box explains itself.
        self.search = QLineEdit()
        self.search.setPlaceholderText(SEARCH_PLACEHOLDER)
        self.search.setAccessibleName(SEARCH_PLACEHOLDER)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(lambda _text: self.apply_search())
        top.addWidget(self.search, 1)
        # Every group the account is in, read off Facebook. It also runs by
        # itself once, after the first login (App._on_check_result). Not
        # #Primary: the accent here belongs to "Next: when to send".
        self.import_button = QPushButton(IMPORT_LABEL)
        self.import_button.clicked.connect(lambda: self.import_from_facebook())
        top.addWidget(self.import_button)
        outer.addLayout(top)
        outer.addSpacing(theme.PAD_M)

        # Group sets, each picked as one item. They are made on the Group sets
        # screen, so this is hidden until one exists.
        self.sets_section = row()
        sets_column = QVBoxLayout(self.sets_section)
        sets_column.setContentsMargins(0, 0, 0, theme.PAD_M)
        sets_column.setSpacing(theme.PAD_XS)
        sets_heading = QLabel("Group sets")
        sets_heading.setObjectName("SectionHeading")
        sets_column.addWidget(sets_heading)
        sets_card = card()
        self.set_grid = QGridLayout(sets_card)
        self.set_grid.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        self.set_grid.setHorizontalSpacing(theme.PAD_L)
        self.set_grid.setVerticalSpacing(theme.PAD_XS)
        sets_column.addWidget(sets_card)
        self.sets_section.setVisible(False)
        outer.addWidget(self.sets_section)

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

        # Under the list, apart from it: a removed group is not one you are
        # choosing from, and fifty of them in view would bury the ones you are.
        self.removed_toggle = QPushButton("")
        self.removed_toggle.setObjectName("Link")
        self.removed_toggle.setVisible(False)
        self.removed_toggle.clicked.connect(lambda: self.toggle_removed())
        removed_line = QHBoxLayout()
        removed_line.addWidget(self.removed_toggle)
        removed_line.addStretch(1)
        outer.addLayout(removed_line)

        footer = QHBoxLayout()
        back = QPushButton("← Back: write the post")
        back.clicked.connect(lambda: self.app.show_view("compose"))
        footer.addWidget(back)
        footer.addStretch(1)
        self.next_button = QPushButton("Next: choose when →")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(lambda: self.app.show_view("publish"))
        footer.addWidget(self.next_button)
        outer.addLayout(footer)

    def on_show(self) -> None:
        self.refresh()
        self.refresh_import_button()
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
        """Tick or clear the groups on show: only the matches while a search
        is typed (the user's choice, 2026-10-14).

        Clear also unpicks every group set: they are always on show, and a
        "Clear" that left greyed ticks behind would not look cleared.
        """
        if not selected and self.app.selected_sets:
            for group_id in self.shown_group_ids():
                self.app.set_group_selected(group_id, False)
            self.app.selected_sets.clear()
            # The greyed rows go back to plain ones, so they are redrawn.
            self.refresh()
            return
        shown = set(self.shown_group_ids())
        for group_id, box in self._checkboxes.items():
            if group_id not in shown:
                continue
            self.app.set_group_selected(group_id, selected)
            if not box.isEnabled():
                continue  # a set's group stays as the set has it
            box.blockSignals(True)
            box.setChecked(selected)
            box.blockSignals(False)
        self.refresh_count()

    def toggle_set(self, set_id: int, selected: bool) -> None:
        """Pick or unpick a group set. Its groups' rows change, so they are
        redrawn; the set boxes themselves are not."""
        self.app.set_set_selected(set_id, selected)
        self.refresh()

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
    def remove_group(self, group_id: int) -> None:
        was_selected = group_id in self.app.selected_groups
        removal = self.app.group_repo.remove(group_id)
        self.app.set_group_selected(group_id, False)
        self.refresh()
        # "Remove" is on every row, one click from the tick beside it. Undo
        # puts the group back into exactly what it was taken out of; "Bring
        # back" later does not.
        undo = lambda: self._restore_group(group_id, removal, was_selected)  # noqa: E731
        listed = taken_out_of(removal.schedules, removal.batches, removal.sets)
        if listed:
            # Said here because bringing the group back does not undo it.
            self.app.toast(
                f"Group removed, and taken out of {listed}. It can "
                "be brought back from the removed groups under the list.",
                "info",
                undo=undo,
            )
        else:
            self.app.toast(
                "Group removed. It can be brought back from the removed groups "
                "under the list.",
                "info",
                undo=undo,
            )

    def _restore_group(self, group_id: int, removal, was_selected: bool) -> str | None:
        if not self.app.group_repo.restore(group_id, removal):
            return None
        if was_selected:
            self.app.set_group_selected(group_id, True)
        self.refresh()
        group = self.app.group_repo.get(group_id)
        name = group.display_name if group is not None else "The group"
        return f"{name} is back, in everything it was taken out of."

    def bring_back(self, group_id: int) -> None:
        """Put a removed group back in the list, from the removed groups.

        It starts ticked, as a group added by pasting its link used to: it was
        brought back in order to be posted to. Its history comes with it, and
        the toast says so, because the repeat guard already knows what it was
        sent. It is not put back in any repeating or queued post.
        """
        group = self.app.group_repo.bring_back(group_id)
        if group is None:
            self.refresh()
            return
        self.app.set_group_selected(group.id, True)
        if self.app.group_repo.recent_bodies(group.id, limit=1):
            message = f"Brought {group.display_name} back, along with its posting history."
        else:
            message = f"Brought {group.display_name} back."
        self.notify(message, "success")
        self.refresh()
        self._fetch_missing_names()

    def toggle_removed(self) -> None:
        self._show_removed = not self._show_removed
        self.refresh()

    # -- reading the groups off Facebook -------------------------------------
    def refresh_import_button(self) -> None:
        """Usable only while connected, and not while a read is under way."""
        connected = self.app.connection_state is ConnectionState.CONNECTED
        self.import_button.setText(IMPORTING_LABEL if self._importing else IMPORT_LABEL)
        self.import_button.setEnabled(
            connected and not self._importing and self.app.group_finder is not None
        )
        self.import_button.setToolTip(
            "" if connected else "Available once the app is connected to Facebook."
        )

    def import_from_facebook(self, automatic: bool = False) -> None:
        """Read every group the account is in, and add the ones not in the list.

        New groups start ticked (the user's choice). A group the user removed
        is never added back (`GroupRepo.import_found`). The automatic run after
        the first login says nothing when it fails: it is tried again at the
        next connection, and the client never asked for it.

        After an account switch (`REPLACE_PENDING_KEY`) the read replaces the
        list instead (`_replace_with_found`), and a failure is said even on the
        automatic run: the user switched, and is looking at the old account's
        groups with no other way to know why.
        """
        finder = self.app.group_finder
        if self._importing or finder is None:
            return
        self._importing = True
        self.refresh_import_button()

        def work():
            # Off the drawing thread, like the name lookups: the probe can
            # take a second, and the read itself takes several.
            if chrome.probe() is None:
                return None
            return finder.find()

        def done(result):
            self._importing = False
            replacing = self.app.settings_repo.get(REPLACE_PENDING_KEY) == "1"
            if result is None:
                if not automatic or replacing:
                    self.notify(
                        "Chrome isn't connected, so your groups could not be read.",
                        "warning",
                    )
            elif not result.ok:
                if not automatic or replacing:
                    self.notify(result.error, "error")
            elif replacing:
                self._replace_with_found(result)
            else:
                self._add_found(result, automatic)
            self.refresh_import_button()

        def failed(exc):
            self._importing = False
            if not automatic:
                self.notify(f"Your groups could not be read: {exc}", "error")
            self.refresh_import_button()

        self.app.run_in_background(work, done, failed)

    def _add_found(self, result, automatic: bool) -> None:
        new = self.app.group_repo.import_found(
            (found.ref.identifier, found.ref.url, found.name) for found in result.groups
        )
        self.app.settings_repo.set(IMPORT_PENDING_KEY, "0")
        for group in new:
            self.app.set_group_selected(group.id, True)
        self.refresh()
        count = len(new)
        if count:
            message = (
                f"Added {count} of your Facebook group{'s' if count != 1 else ''}, "
                "all ticked. Untick any you do not want to post to."
            )
        elif automatic:
            return  # nothing new, and nobody asked
        else:
            message = "No new groups found. Every group you are in is already in the list."
        short = result.expected is not None and len(result.groups) < result.expected
        if short:
            message += (
                f" Facebook lists {result.expected} groups and {len(result.groups)} "
                f"could be read; press {IMPORT_LABEL} again for the rest."
            )
        self.notify(message, "warning" if short else "success")
        # A group whose name could not be read off the list gets the usual
        # lookup, one at a time.
        self._fetch_missing_names()

    def _replace_with_found(self, result) -> None:
        """After an account switch: the list becomes the new account's groups.

        All or nothing. Only a read that got every group the page counts may
        remove anything, because a group missed by the read would be removed
        from a list it belongs on. A short read changes nothing and leaves the
        switch pending, so the next connection or the button tries again.
        """
        if not result.complete:
            if result.expected is None:
                why = "Facebook did not say how many groups this account is in"
            else:
                why = (
                    f"Facebook lists {result.expected} groups and only "
                    f"{len(result.groups)} could be read"
                )
            self.notify(
                f"{why}, so your groups were not changed yet. "
                f"Press {IMPORT_LABEL} to try again.",
                "warning",
            )
            return
        replacement = self.app.group_repo.replace_with(
            (found.ref.identifier, found.ref.url, found.name) for found in result.groups
        )
        self.app.settings_repo.set(REPLACE_PENDING_KEY, "0")
        self.app.settings_repo.set(IMPORT_PENDING_KEY, "0")
        for group in replacement.added + replacement.brought_back:
            self.app.set_group_selected(group.id, True)
        for group_id in replacement.removals:
            self.app.set_group_selected(group_id, False)
        self.refresh()

        came = len(replacement.added) + len(replacement.brought_back)
        went = len(replacement.removals)
        if not came and not went:
            message = "Your groups already match the Facebook account you are logged in as."
        else:
            message = (
                "Your groups now match the Facebook account you are logged in as: "
                f"{came} added, {went} removed."
            )
        removals = replacement.removals.values()
        listed = taken_out_of(
            len({row[0] for r in removals for row in r.schedule_rows}),
            len({task_id for r in removals for task_id in r.batch_ids}),
            len({row[0] for r in removals for row in r.set_rows}),
        )
        if listed:
            # Only what was made since the switch: it deleted the rest.
            message += f" The removed ones were taken out of {listed}."
        self.notify(message, "success")
        self._fetch_missing_names()

    # -- names and member counts --------------------------------------------
    def look_up_missing_names(self) -> None:
        """Called when Chrome becomes reachable, which is the first moment a
        group added while it was still starting can have its name read."""
        self._fetch_missing_names()

    def _fetch_missing_names(self, skip: frozenset[int] = frozenset()) -> None:
        """Look up display names and member counts, but never at the cost of
        blocking the window. One visit to a group's page reads both.

        Guarded on the debug port being open, because the namer is injectable
        precisely so tests never reach the live site.

        `skip` is what an earlier sweep in the same chain already tried -- a
        name that could not be read a moment ago will not be read by asking
        again straight away.
        """
        missing = [g for g in self.app.group_repo.missing_details() if g.id not in skip]
        if self._naming:
            # Queued, not dropped; finish() runs it. The rows say so now,
            # rather than showing a number until the running sweep ends.
            if missing:
                self._lookup_again = True
                self._looking_up.update(group.id for group in missing)
                self.refresh()
            return
        if not missing:
            return

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
            return self.app.group_namer.info_for(urls)

        def done(found):
            for group in missing:
                info = (found or {}).get(group.url)
                if info is None:
                    continue  # its page never loaded: not tried, so not recorded
                # A name already known came off Facebook's own list; it stays.
                if info.name and not group.name:
                    self.app.group_repo.set_name(group.id, info.name)
                if not group.members:
                    self.app.group_repo.set_members(group.id, info.members)
            finish()

        def failed(_exc):
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
    def _snapshot(self, groups, removed) -> tuple:
        """Everything a row draws. Rebuilding twelve of them cost 30ms on
        every visit to this screen, to redraw the identical list.

        The search is not in it: typing hides and shows rows that are already
        drawn (`apply_search`), it never rebuilds them."""
        selected = self.app.selected_groups
        covered = self._covered
        return (
            tuple(
                (g.id, self._label(g), g.members, g.id in selected,
                 tuple(covered.get(g.id, ())))
                for g in groups
            ),
            tuple((g.id, g.display_name) for g in removed) if self._show_removed else (),
            len(removed),
            self._show_removed,
        )

    def _label(self, group) -> str:
        if not group.name and group.id in self._looking_up:
            return LOOKING_UP
        return group.display_name

    def refresh_sets(self, sets) -> None:
        """The group set boxes: rebuilt only when the sets themselves change.

        Whether each one is picked is restated every time, outside the guard,
        because a click changes it without changing anything the guard is
        keyed on, and Clear changes it without a click.
        """
        names = {g.id: g.display_name for g in self._groups.values()}
        # The tooltip names the groups, so their names are in it too.
        tips = {
            s.id: ", ".join(names.get(gid, "") for gid in s.group_ids) for s in sets
        }
        shown = tuple((s.id, set_label(s), tips[s.id]) for s in sets)
        if shown != self._sets_shown:
            self._sets_shown = shown
            clear(self.set_grid)
            self._set_boxes = {}
            for index, group_set in enumerate(sets):
                box = QCheckBox(set_label(group_set))
                box.setToolTip(tips[group_set.id])
                box.toggled.connect(lambda on, sid=group_set.id: self.toggle_set(sid, on))
                self.set_grid.addWidget(box, index // SET_COLUMNS, index % SET_COLUMNS)
                self._set_boxes[group_set.id] = box
            self.sets_section.setVisible(bool(sets))
        for set_id, box in self._set_boxes.items():
            box.blockSignals(True)
            box.setChecked(set_id in self.app.selected_sets)
            box.blockSignals(False)

    def refresh(self, force: bool = False) -> None:
        groups = self.app.group_repo.list()
        removed = self.app.group_repo.removed()
        sets = self.app.group_set_repo.list()
        # A set deleted while picked is no longer picked.
        self.app.selected_sets &= {s.id for s in sets}
        self._covered = {}
        for group_set in sets:
            if group_set.id in self.app.selected_sets:
                for group_id in group_set.group_ids:
                    self._covered.setdefault(group_id, []).append(group_set.name)
        self._groups = {group.id: group for group in groups}
        self.refresh_sets(sets)
        snapshot = self._snapshot(groups, removed)
        if not force and snapshot == self._snapshot_shown:
            # The count still gets restated: it is one setText, and it also
            # reaches Compose, which needs to hear about the selection.
            self.refresh_count()
            return
        self._snapshot_shown = snapshot

        clear(self.rows)
        self._checkboxes = {}
        self._rows = {}
        self._removed_rows = {}
        self._removed_groups = {group.id: group for group in removed}
        self.removed_heading = None

        # One note for both empty cases, worded by apply_search.
        self.empty_note = QLabel("")
        self.empty_note.setObjectName("Muted")
        self.empty_note.setWordWrap(True)
        self.rows.addWidget(self.empty_note)
        for group in groups:
            row = self._row(group)
            self._rows[group.id] = row
            self.rows.addWidget(row)

        self.removed_toggle.setVisible(bool(removed))
        self.removed_toggle.setText(
            HIDE_REMOVED if self._show_removed else SHOW_REMOVED.format(count=len(removed))
        )
        if removed and self._show_removed:
            self.removed_heading = QLabel("Removed groups")
            self.removed_heading.setObjectName("SectionHeading")
            self.rows.addSpacing(theme.PAD_M)
            self.rows.addWidget(self.removed_heading)
            for group in removed:
                row = self._removed_row(group)
                self._removed_rows[group.id] = row
                self.rows.addWidget(row)

        self.rows.addStretch(1)
        self.apply_search()
        self.refresh_count()

    # -- search ------------------------------------------------------------
    def shown_group_ids(self) -> list[int]:
        """The groups in the list that match the search, in list order."""
        query = self.search.text()
        return [gid for gid, group in self._groups.items() if matches(group, query)]

    def apply_search(self) -> None:
        """Hide the rows that do not match. Nothing is rebuilt, and nothing
        is unticked: a hidden group that is ticked still gets the post."""
        if self.empty_note is None:
            return  # nothing drawn yet; refresh() calls this when it is
        query = self.search.text()
        shown = set(self.shown_group_ids())
        for group_id, row in self._rows.items():
            row.setVisible(group_id in shown)
        any_removed = False
        for group_id, row in self._removed_rows.items():
            visible = matches(self._removed_groups[group_id], query)
            row.setVisible(visible)
            any_removed = any_removed or visible
        if self.removed_heading is not None:
            self.removed_heading.setVisible(any_removed)

        if not self._groups:
            self.empty_note.setText(f"No groups yet. Press {IMPORT_LABEL}.")
        elif not shown:
            self.empty_note.setText(f"No group matches \"{query.strip()}\".")
        self.empty_note.setVisible(not shown)

    def _row(self, group) -> QWidget:
        label = self._label(group)
        covered = self._covered.get(group.id)
        box = QCheckBox(label)
        box.setChecked(group.id in self.app.selected_groups or bool(covered))
        box.setStyleSheet(
            f"color: {theme.C['TEXT_MUTED']};" if label == LOOKING_UP
            else "font-weight: 600;"
        )
        box.toggled.connect(lambda on, gid=group.id: self.toggle(gid, on))
        self._checkboxes[group.id] = box

        holder = GroupRow(box)
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_S, theme.PAD_S)
        layout.addWidget(box, 1)

        if covered:
            # The set picks it, so the set is what unpicks it.
            box.setEnabled(False)
            holder.setCursor(Qt.ArrowCursor)
            source = QLabel(in_sets(covered))
            source.setObjectName("Muted")
            layout.addWidget(source)
            # Apart from the member count, or the two read as one phrase.
            layout.addSpacing(theme.PAD_M)

        # As Facebook writes it, in the account's language: "12K members".
        if group.members:
            members = QLabel(group.members)
            members.setObjectName("Muted")
            layout.addWidget(members)

        # No cooldown control. There is one rule for every group -- the
        # default gap, set on the Settings screen -- and breaking it is a
        # choice made per post, with "Post anyway" on the Publish screen, not
        # a setting buried in a row.

        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, gid=group.id: self.remove_group(gid))
        layout.addWidget(remove)
        return holder

    def _removed_row(self, group) -> QWidget:
        holder = card()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_S, theme.PAD_S)
        name = QLabel(group.display_name)
        name.setObjectName("Muted")
        layout.addWidget(name, 1)
        back = QPushButton("Bring back")
        back.setObjectName("Link")
        back.clicked.connect(lambda _c, gid=group.id: self.bring_back(gid))
        layout.addWidget(back)
        return holder
