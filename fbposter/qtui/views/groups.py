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
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from fbposter import chrome
from fbposter.errors import FBPosterError

from .. import theme
from ..widgets import card


class GroupsView(QWidget):
    title = "Groups"
    subtitle = (
        "Tick the groups this post goes to. Only groups you can post in — "
        "admin groups belong in Facebook's own scheduler."
    )

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self._naming = False
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
        add = QPushButton("Add group")
        add.setObjectName("Primary")
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
        clear = QPushButton("Clear")
        clear.clicked.connect(lambda: self.set_all(False))
        tools.addWidget(clear)
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

        self.url_entry.clear()
        # A group added here was added to be posted to, so it starts ticked.
        self.app.set_group_selected(group.id, True)
        self.refresh()
        self.notify(f"Added {group.display_name}.", "success")
        self._fetch_missing_names()
        return True

    def remove_group(self, group_id: int) -> None:
        self.app.group_repo.remove(group_id)
        self.app.set_group_selected(group_id, False)
        self.refresh()
        self.notify("Group removed.", "info")

    def set_cooldown(self, group_id: int, hours: int) -> None:
        self.app.group_repo.set_cooldown(group_id, hours)
        self.notify("Cooldown updated.", "success")

    # -- names -------------------------------------------------------------
    def _fetch_missing_names(self) -> None:
        """Look up display names, but never at the cost of blocking the window.

        Guarded on the debug port being open, because the namer is injectable
        precisely so tests never reach the live site.
        """
        if self._naming:
            return
        missing = self.app.group_repo.missing_names()
        if not missing or chrome.probe() is None:
            return

        self._naming = True
        urls = [group.url for group in missing]

        def work():
            return self.app.group_namer.names_for(urls)

        def done(found):
            self._naming = False
            changed = False
            for group in missing:
                name = (found or {}).get(group.url, "")
                if name:
                    self.app.group_repo.set_name(group.id, name)
                    changed = True
            if changed:
                self.refresh()

        def failed(_exc):
            self._naming = False

        self.app.run_in_background(work, done, failed)

    # -- rendering ---------------------------------------------------------
    def refresh(self) -> None:
        while self.rows.count():
            item = self.rows.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._checkboxes = {}

        groups = self.app.group_repo.list()
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

        box = QCheckBox(group.display_name)
        box.setChecked(group.id in self.app.selected_groups)
        box.setStyleSheet("font-weight: 600;")
        box.toggled.connect(lambda on, gid=group.id: self.toggle(gid, on))
        self._checkboxes[group.id] = box
        layout.addWidget(box, 1)

        layout.addWidget(QLabel("Cooldown"))
        spin = QSpinBox()
        spin.setRange(0, 720)
        spin.setValue(group.cooldown_hours)
        spin.setSuffix(" h")
        spin.valueChanged.connect(lambda hours, gid=group.id: self.set_cooldown(gid, hours))
        layout.addWidget(spin)

        remove = QPushButton("Remove")
        remove.setObjectName("Link")
        remove.clicked.connect(lambda _c, gid=group.id: self.remove_group(gid))
        layout.addWidget(remove)
        return holder
