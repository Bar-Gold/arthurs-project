"""Compose screen: what the post says. The first step of three.

Groups are chosen on the Groups screen and timing on Publish; this view reads
the chosen groups (to open a wording tab per group) but never edits them.

There is no direction handling anywhere in this file, and that is the point.
Qt shapes text itself, so Hebrew, English and digits on one line come out in
the right order and a Hebrew paragraph aligns itself to the right. The Tk build
needed a module of Unicode rules, an invisible embedding character and a
reordering library to get part of the way there.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock
from fbposter.db.models import utcnow
from fbposter.guards import PlannedTarget, evaluate_batch

from .. import theme
from ..widgets import card

IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.gif *.webp);;All files (*.*)"
RIGHT_COLUMN_WIDTH = 330
TAB_LABEL_CHARS = 16
PREVIEW_IMAGE_MAX = (400, 300)

ALL_GROUPS_TAB = "All groups"
REWORDED = "Just now · reworded for this group"
SHARED_WORDING = "Just now · the shared wording"


class ComposeView(QWidget):
    title = "Compose"
    subtitle = "Write the post and attach images. Groups and timing come next."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.attachments: list[Path] = []

        # The shared wording, and the per-group rewrites that override it.
        self._base_body = ""
        self._bodies: dict[int, str] = {}
        self._editing: int | None = None  # None == the "All groups" tab

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

        body = QHBoxLayout()
        body.setSpacing(theme.PAD_M)
        outer.addLayout(body, 1)
        self._build_editor(body)
        self._build_sidebar(body)

    def on_show(self) -> None:
        self.refresh_groups()
        self.refresh_templates()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    # -- left column -------------------------------------------------------
    def _build_editor(self, parent: QHBoxLayout) -> None:
        column = QVBoxLayout()
        column.setSpacing(theme.PAD_XS)
        parent.addLayout(column, 1)

        top = QHBoxLayout()
        self.tab_bar = QHBoxLayout()
        self.tab_bar.setSpacing(theme.PAD_XS)
        top.addLayout(self.tab_bar)
        top.addStretch(1)

        self.write_button = QPushButton("Write")
        self.preview_button = QPushButton("Preview")
        modes = QButtonGroup(self)
        for index, button in enumerate((self.write_button, self.preview_button)):
            button.setObjectName("Tab")
            button.setCheckable(True)
            modes.addButton(button, index)
            top.addWidget(button)
        self.write_button.setChecked(True)
        self.write_button.clicked.connect(lambda: self.set_mode("write"))
        self.preview_button.clicked.connect(lambda: self.set_mode("preview"))
        column.addLayout(top)

        holder = card()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)

        self.slot = QStackedWidget()
        self.editor = QTextEdit()
        self.editor.setObjectName("Editor")
        self.editor.setAcceptRichText(False)
        self.editor.textChanged.connect(self._on_text_changed)
        self.slot.addWidget(self.editor)

        self.preview = PostPreview()
        self.slot.addWidget(self.preview)
        holder_layout.addWidget(self.slot, 1)

        footer = QHBoxLayout()
        self.counter = QLabel("0 characters")
        self.counter.setObjectName("Muted")
        footer.addWidget(self.counter)
        footer.addStretch(1)
        self.reset_button = QPushButton("Reset to base text")
        self.reset_button.clicked.connect(self.reset_current)
        self.reset_button.hide()
        footer.addWidget(self.reset_button)
        self.variation_note = QLabel("Vary the wording between groups.")
        self.variation_note.setObjectName("Muted")
        footer.addWidget(self.variation_note)
        holder_layout.addLayout(footer)
        column.addWidget(holder, 1)

        attach = QHBoxLayout()
        button = QPushButton("Attach images")
        button.setObjectName("Primary")
        button.clicked.connect(self._pick_files)
        attach.addWidget(button)
        attach.addStretch(1)
        column.addLayout(attach)

        self.attachment_box = QVBoxLayout()
        self.attachment_box.setSpacing(theme.PAD_XS)
        column.addLayout(self.attachment_box)
        self._render_attachments()
        self.refresh_tabs()

    # -- right column ------------------------------------------------------
    def _build_sidebar(self, parent: QHBoxLayout) -> None:
        column = QVBoxLayout()
        column.setSpacing(theme.PAD_S)
        holder = QWidget()
        holder.setFixedWidth(RIGHT_COLUMN_WIDTH)
        holder.setLayout(column)
        parent.addWidget(holder)

        templates = card()
        inner = QVBoxLayout(templates)
        inner.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        label = QLabel("Templates")
        label.setObjectName("SectionHeading")
        inner.addWidget(label)
        row = QHBoxLayout()
        self.template_picker = QComboBox()
        row.addWidget(self.template_picker, 1)
        load = QPushButton("Load")
        load.clicked.connect(self.load_template)
        row.addWidget(load)
        inner.addLayout(row)
        row2 = QHBoxLayout()
        self.template_name = QLineEdit()
        self.template_name.setPlaceholderText("Name to save as")
        row2.addWidget(self.template_name, 1)
        save = QPushButton("Save")
        save.clicked.connect(self.save_template)
        row2.addWidget(save)
        inner.addLayout(row2)
        column.addWidget(templates)

        # A summary, not a picker: choosing groups is its own step, and having
        # the list in two places meant two things to keep in sync.
        recipients = card()
        recipients_layout = QVBoxLayout(recipients)
        recipients_layout.setContentsMargins(
            theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M
        )
        send_to = QLabel("Sending to")
        send_to.setObjectName("SectionHeading")
        recipients_layout.addWidget(send_to)
        self.recipient_summary = QLabel("")
        self.recipient_summary.setObjectName("Muted")
        self.recipient_summary.setWordWrap(True)
        recipients_layout.addWidget(self.recipient_summary)
        column.addWidget(recipients)

        column.addStretch(1)

        self.next_button = QPushButton("Next: pick groups →")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self.go_to_groups)
        column.addWidget(self.next_button)

    def go_to_groups(self) -> None:
        self.capture()
        self.app.show_view("groups")

    # -- text --------------------------------------------------------------
    def get_text(self) -> str:
        return self.editor.toPlainText()

    def _show(self, text: str) -> None:
        blocked = self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(blocked)
        self._update_counter()

    def _on_text_changed(self) -> None:
        self._update_counter()

    def _update_counter(self) -> None:
        count = len(self.get_text())
        self.counter.setText(f"{count} character{'s' if count != 1 else ''}")

    # -- per-group text ----------------------------------------------------
    def body_for(self, group_id: int) -> str:
        """The wording this group will actually receive. Committed state only."""
        return self._bodies.get(group_id, self._base_body)

    def base_body(self) -> str:
        """The shared wording, before any per-group rewrite. Committed state."""
        return self._base_body

    def capture(self) -> None:
        """Save what is in the editor into whichever tab is showing."""
        text = self.get_text()
        if self._editing is None:
            if text != self._base_body:
                had_rewrites = bool(self._bodies)
                self._base_body = text
                if had_rewrites:
                    self._bodies.clear()
                    self.notify(
                        "Base text changed, so the per-group versions were reset.",
                        "warning",
                    )
        else:
            self._bodies[self._editing] = text

    def select_tab(self, target: int | None) -> None:
        self.capture()
        text = self._base_body if target is None else self.body_for(target)
        self._editing = target
        self._show(text)
        self.refresh_tabs()

    def reset_current(self) -> None:
        if self._editing is None:
            return
        self._bodies.pop(self._editing, None)
        self._show(self._base_body)
        self.refresh_tabs()

    def has_rewrite(self, group_id: int) -> bool:
        return group_id in self._bodies and self._bodies[group_id] != self._base_body

    def selected_group_ids(self) -> list[int]:
        """Chosen on the Groups screen; this view only reads them."""
        return self.app.selected_group_ids()

    def refresh_tabs(self) -> None:
        while self.tab_bar.count():
            item = self.tab_bar.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        if self._editing is not None and self._editing not in self.selected_group_ids():
            self._editing = None
            self._show(self._base_body)

        self._add_tab(None, ALL_GROUPS_TAB)
        for group_id in self.selected_group_ids():
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            label = group.display_name
            if len(label) > TAB_LABEL_CHARS:
                label = label[: TAB_LABEL_CHARS - 1] + "…"
            if self.has_rewrite(group_id):
                label = f"● {label}"
            self._add_tab(group_id, label)

        showing = self._editing is not None and self.has_rewrite(self._editing)
        self.reset_button.setVisible(showing)
        self.refresh_preview()

    def _add_tab(self, target: int | None, label: str) -> None:
        button = QPushButton(label)
        button.setObjectName("Tab")
        button.setCheckable(True)
        button.setChecked(target == self._editing)
        button.clicked.connect(lambda _checked, t=target: self.select_tab(t))
        self.tab_bar.addWidget(button)

    # -- preview -----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        if mode == "preview":
            self.capture()
            self.slot.setCurrentWidget(self.preview)
            self.refresh_preview()
        else:
            self.slot.setCurrentWidget(self.editor)

    def preview_heading(self) -> tuple[str, str]:
        if self._editing is None:
            count = len(self.selected_group_ids())
            if count == 0:
                return ALL_GROUPS_TAB, "Just now · no groups selected yet"
            return ALL_GROUPS_TAB, f"Just now · {count} group{'s' if count != 1 else ''}"
        group = self.app.group_repo.get(self._editing)
        name = group.display_name if group else ALL_GROUPS_TAB
        return name, REWORDED if self.has_rewrite(self._editing) else SHARED_WORDING

    def refresh_preview(self) -> None:
        if self.slot.currentWidget() is not self.preview:
            return
        heading, subheading = self.preview_heading()
        text = self._base_body if self._editing is None else self.body_for(self._editing)
        self.preview.render(heading, subheading, text, list(self.attachments))

    # -- groups ------------------------------------------------------------
    def refresh_groups(self) -> None:
        """Restate the choice made on the Groups screen, and retab for it."""
        chosen = self.selected_group_ids()
        if not chosen:
            total = len(self.app.group_repo.list())
            self.recipient_summary.setText(
                "No groups picked yet."
                if total
                else "No groups yet — add one on the Groups screen."
            )
        else:
            names = []
            for group_id in chosen:
                group = self.app.group_repo.get(group_id)
                if group is not None:
                    names.append(group.display_name)
            self.recipient_summary.setText(
                f"{len(names)} group{'s' if len(names) != 1 else ''} · "
                + ", ".join(names)
            )
        self.refresh_tabs()

    # -- templates ---------------------------------------------------------
    def refresh_templates(self) -> None:
        names = [t.name for t in self.app.template_repo.list()]
        current = self.template_picker.currentText()
        self.template_picker.clear()
        self.template_picker.addItems(names or ["(none saved)"])
        if current in names:
            self.template_picker.setCurrentText(current)

    def save_template(self) -> bool:
        name = self.template_name.text().strip()
        if not name:
            self.notify("Give the template a name first.", "error")
            return False
        self.capture()
        body = self._base_body.strip()
        if not body:
            self.notify("Nothing to save — the post is empty.", "error")
            return False
        self.app.template_repo.save(name, body, [str(p) for p in self.attachments])
        self.template_name.clear()
        self.refresh_templates()
        self.notify(f"Saved template {name!r}.", "success")
        return True

    def load_template(self) -> bool:
        template = self.app.template_repo.get_by_name(self.template_picker.currentText())
        if template is None:
            self.notify("No template selected.", "warning")
            return False
        self._editing = None
        self._bodies.clear()
        self._base_body = template.body
        self._show(template.body)
        self.refresh_tabs()
        self.attachments = [Path(p) for p in template.media_paths]
        self._render_attachments()
        self.notify(f"Loaded {template.name!r}. Edit it before sending.", "info")
        return True

    # -- attachments -------------------------------------------------------
    def _pick_files(self) -> None:
        """The one modal dialog the app allows: the user asked for it, and it
        cannot appear while a batch is running."""
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, "Attach images", "", IMAGE_FILTER
        )
        added = 0
        for name in chosen:
            path = Path(name)
            if path not in self.attachments:
                self.attachments.append(path)
                added += 1
        if added:
            self._render_attachments()
            self.notify(f"Attached {added} file{'s' if added != 1 else ''}.", "success")

    def _remove_attachment(self, path: Path) -> None:
        if path in self.attachments:
            self.attachments.remove(path)
            self._render_attachments()

    def _render_attachments(self) -> None:
        while self.attachment_box.count():
            item = self.attachment_box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self.refresh_preview()
        if not self.attachments:
            note = QLabel("No images attached.")
            note.setObjectName("Muted")
            self.attachment_box.addWidget(note)
            return

        for path in self.attachments:
            row = card()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_S, theme.PAD_S)
            layout.addWidget(QLabel(path.name), 1)
            remove = QPushButton("Remove")
            remove.setObjectName("Link")
            remove.clicked.connect(lambda _c, p=path: self._remove_attachment(p))
            layout.addWidget(remove)
            self.attachment_box.addWidget(row)

    # -- queueing ----------------------------------------------------------
    def add_to_queue(self, when=None) -> bool:
        """Queue this post. `when` is a UTC instant, or None for "as soon as".

        Compose owns the content, so it owns the writing of the batch; the
        Publish screen supplies nothing but the moment.
        """
        self.capture()
        selected = self.selected_group_ids()
        if not selected:
            self.notify("Pick at least one group.", "error")
            return False
        if any(not self.body_for(gid).strip() for gid in selected):
            self.notify("Every selected group needs some text.", "error")
            return False

        now = utcnow()
        settings = self.app.settings_repo
        planned = []
        for group_id in selected:
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            planned.append(
                PlannedTarget(
                    group_id=group.id,
                    group_name=group.display_name,
                    body=self.body_for(group.id).strip(),
                    last_posted_at=group.last_posted_at,
                    cooldown_hours=group.cooldown_hours,
                    recent_bodies=tuple(self.app.group_repo.recent_bodies(group.id)),
                )
            )

        verdict = evaluate_batch(
            planned,
            now=now,
            when=when,
            daily_cap=settings.get_int("daily_cap", 25),
            posted_today=self.app.task_repo.posted_count_since(
                clock.start_of_local_day(now)
            ),
            window_start_hour=settings.get_int("posting_window_start_hour", 8),
            window_end_hour=settings.get_int("posting_window_end_hour", 23),
        )
        if not verdict.allowed:
            self.notify(verdict.blocked[0].message, "error")
            return False

        self.app.task_repo.create(
            self._base_body.strip(),
            [(target.group_id, target.body) for target in planned],
            media_paths=[str(p) for p in self.attachments],
            scheduled_for=when,
        )
        for warning in verdict.warnings:
            self.notify(warning, "warning")
        if not verdict.warnings:
            self.notify(f"Queued for {len(planned)} group(s).", "success")
        return True


class PostPreview(QWidget):
    """The post as the group will see it.

    A plain QLabel does the whole job: Qt orders mixed Hebrew and English
    correctly and aligns a Hebrew paragraph to the right by itself.
    """

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        layout.addWidget(self.area)
        self.holder = QWidget()
        self.body = QVBoxLayout(self.holder)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.area.setWidget(self.holder)
        self._body_text = ""
        self.render()

    def text_shown(self) -> str:
        return self._body_text

    def render(self, heading: str = "", subheading: str = "", text: str = "",
               media: list[Path] | None = None) -> None:
        media = media or []
        while self.body.count():
            item = self.body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._body_text = ""

        if not text.strip() and not media:
            note = QLabel("Nothing to preview yet — write something on the Write tab.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.body.addWidget(note)
            self.body.addStretch(1)
            return

        post = card()
        layout = QVBoxLayout(post)
        layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        layout.setSpacing(theme.PAD_XS)

        if heading:
            label = QLabel(heading)
            label.setStyleSheet("font-weight: 600;")
            layout.addWidget(label)
        if subheading:
            label = QLabel(subheading)
            label.setObjectName("Muted")
            layout.addWidget(label)

        body = text.rstrip()
        self._body_text = body if body.strip() else "(no text — the post would be images only)"
        label = QLabel(self._body_text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if not body.strip():
            label.setObjectName("Muted")
        layout.addWidget(label)

        for path in media:
            layout.addWidget(self._image(Path(path)))

        self.body.addWidget(post)
        self.body.addStretch(1)

    def _image(self, path: Path) -> QLabel:
        """A thumbnail, or a named tile when the file cannot be read.

        Showing nothing would be worse: the user would think the attachment had
        been dropped, when it is still going to be uploaded.
        """
        label = QLabel()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            label.setText(f"{path.name}\n(no preview)")
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(120)
            label.setStyleSheet(
                f"border: 1px solid {theme.C['BORDER']}; border-radius: {theme.RADIUS}px;"
            )
            return label
        label.setPixmap(
            pixmap.scaled(
                *PREVIEW_IMAGE_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        )
        return label
