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

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImageIOHandler, QImageReader, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
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
from fbposter.text import strip_invisible
from fbposter.db.models import utcnow
from fbposter.guards import PlannedTarget, evaluate_batch

from .. import theme
from ..widgets import card, clear, row

IMAGE_FILTER = "Images (*.jpg *.jpeg *.png *.gif *.webp);;All files (*.*)"
RIGHT_COLUMN_WIDTH = 330
TAB_LABEL_CHARS = 16

# -- preview geometry ------------------------------------------------------
# A feed column is around 500px. Wider than this and the fake post stops
# looking like a post; narrower than the floor and it stops being readable.
MAX_POST_WIDTH = 500
MIN_POST_WIDTH = 280
# Re-laying out the collage on every pixel of a window drag would be a lot of
# rescaling for nothing.
RESIZE_SLACK = 12

AVATAR_SIZE = 40
TILE_GAP = 2
MAX_TILES = 4          # beyond this the last tile carries a "+N"
PAIR_HEIGHT = 250
TRIPLE_TOP_HEIGHT = 260
TRIPLE_BOTTOM_HEIGHT = 170
QUAD_HEIGHT = 200
MIN_SINGLE_HEIGHT = 180
MAX_SINGLE_HEIGHT = 560

NO_TEXT = "(no text — the post would be images only)"
# Below this, a picture-less post is set large, the way a feed does it.
BIG_TEXT_CHARS = 85

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
        # The strip as currently drawn, so an unchanged one is not rebuilt.
        self._tabs_shown: list[tuple[int | None, str]] | None = None

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

        # Two rows rather than one. The mode toggle sits top-right, the wording
        # tabs get the full width underneath it: sharing a row, the tabs were
        # squeezed below their own text and group names clipped mid-word.
        top = QHBoxLayout()
        top.setSpacing(theme.PAD_S)
        top.addStretch(1)

        self.tab_scroll = QScrollArea()
        self.tab_scroll.setWidgetResizable(True)
        self.tab_scroll.setFrameShape(QFrame.NoFrame)
        self.tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tab_scroll.setFixedHeight(theme.CONTROL_HEIGHT + theme.PAD_M)
        tab_holder = QWidget()
        self.tab_bar = QHBoxLayout(tab_holder)
        self.tab_bar.setContentsMargins(0, 0, 0, 0)
        self.tab_bar.setSpacing(theme.PAD_XS)
        # Left alignment rather than a trailing stretch: refresh_tabs() clears
        # this layout by index and counts what is in it, so a spacer item would
        # be one more thing every caller has to know about.
        self.tab_bar.setAlignment(Qt.AlignLeft)
        self.tab_scroll.setWidget(tab_holder)

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
        column.addWidget(self.tab_scroll)

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

        # The attach button and the file list are one unit, so Preview can
        # stand them down together. In Preview the pictures are *in* the post
        # already -- listing them again underneath is a second telling, and it
        # was taking the height that stopped the post fitting on screen.
        self.attach_holder = row()
        attach_column = QVBoxLayout(self.attach_holder)
        attach_column.setContentsMargins(0, 0, 0, 0)
        attach_column.setSpacing(theme.PAD_XS)

        attach = QHBoxLayout()
        # Not #Primary: the accent on this screen belongs to "Next: pick
        # groups", and two filled buttons in one column read as two main
        # actions.
        button = QPushButton("Attach images")
        button.clicked.connect(self._pick_files)
        attach.addWidget(button)
        attach.addStretch(1)
        attach_column.addLayout(attach)

        self.attachment_box = QVBoxLayout()
        self.attachment_box.setSpacing(theme.PAD_XS)
        attach_column.addLayout(self.attachment_box)
        column.addWidget(self.attach_holder)
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
        # Hidden in Preview mode, which is why it is kept: templates and the
        # recipient summary are writing tools, and holding a third of the
        # window for them left the preview itself in half a screen.
        self.sidebar_holder = holder

        templates = card()
        inner = QVBoxLayout(templates)
        inner.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        label = QLabel("Templates")
        label.setObjectName("SectionHeading")
        inner.addWidget(label)
        reuse = QLabel("Reuse a saved post")
        reuse.setObjectName("Muted")
        inner.addWidget(reuse)
        row = QHBoxLayout()
        self.template_picker = QComboBox()
        self.template_picker.setAccessibleName("Saved templates")
        row.addWidget(self.template_picker, 1)
        load = QPushButton("Load")
        load.clicked.connect(self.load_template)
        row.addWidget(load)
        inner.addLayout(row)

        inner.addSpacing(theme.PAD_S)
        keep = QLabel("Save this one for later")
        keep.setObjectName("Muted")
        inner.addWidget(keep)
        row2 = QHBoxLayout()
        self.template_name = QLineEdit()
        self.template_name.setPlaceholderText("Give it a name")
        self.template_name.setAccessibleName("Template name")
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
        """The only read of the editor, and the only place text is cleaned.

        Qt needs no direction marks of its own, but a paste out of Word or
        WhatsApp brings its own invisible ones. Left in, they make an identical
        post compare as different and defeat the repeated-text guard.
        """
        return strip_invisible(self.editor.toPlainText())

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

    def _tab_plan(self) -> list[tuple[int | None, str]]:
        """The strip as (group id, label) pairs -- what to draw, not drawing.

        Which tab is *filled* is deliberately not in here; see
        `_mark_active_tab`, which is why it cannot be.
        """
        plan: list[tuple[int | None, str]] = [(None, ALL_GROUPS_TAB)]
        for group_id in self.selected_group_ids():
            group = self.app.group_repo.get(group_id)
            if group is None:
                continue
            label = group.display_name
            if len(label) > TAB_LABEL_CHARS:
                label = label[: TAB_LABEL_CHARS - 1] + "…"
            if self.has_rewrite(group_id):
                label = f"● {label}"
            plan.append((group_id, label))
        return plan

    def refresh_tabs(self) -> None:
        if self._editing is not None and self._editing not in self.selected_group_ids():
            self._editing = None
            self._show(self._base_body)

        # Ticking a box on the Groups screen calls this once per click, and
        # rebuilding the strip is ~9ms of widget construction. Skipping it when
        # the same tabs would come out is what keeps ticking through a long
        # list feeling instant. Everything after the guard still runs on every
        # call: marking the active tab (which the guard cannot govern -- see
        # below), a setVisible, and a no-op in Write mode.
        plan = self._tab_plan()
        if plan != self._tabs_shown:
            self._tabs_shown = plan
            clear(self.tab_bar)
            for target, label in plan:
                self._add_tab(target, label)
        self._mark_active_tab()

        showing = self._editing is not None and self.has_rewrite(self._editing)
        self.reset_button.setVisible(showing)
        self.refresh_preview()

    def _mark_active_tab(self) -> None:
        """Fill the tab whose wording the editor is holding. Always.

        Outside the rebuild guard, and it is the one thing on this strip that
        cannot live inside it. Switching tabs changes `_editing` without
        changing a single label, so a guard keyed on the labels skips the
        rebuild and the highlight stays on the tab the user has just left. And
        the buttons are checkable and in no exclusive group, so Qt toggles the
        one clicked *before* select_tab is reached: clicking a second tab lit
        two at once, and clicking the tab you were already on switched its own
        highlight off, leaving nothing lit at all. Neither touches the labels,
        so neither is a cache-key problem -- the fill simply is not something a
        guard on the contents can govern.

        It matters because the filled tab is the only thing on screen saying
        whose words are in the editor, and typing into the base text clears
        every per-group rewrite. A strip pointing at the wrong tab is how
        someone loses wording they had already written.
        """
        for index, (target, _label) in enumerate(self._tabs_shown or ()):
            item = self.tab_bar.itemAt(index)
            button = item.widget() if item is not None else None
            if button is not None:
                button.setChecked(target == self._editing)

    def _add_tab(self, target: int | None, label: str) -> None:
        button = QPushButton(label)
        button.setObjectName("Tab")
        button.setCheckable(True)
        button.setChecked(target == self._editing)
        button.clicked.connect(lambda _checked, t=target: self.select_tab(t))
        self.tab_bar.addWidget(button)

    # -- preview -----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Write and Preview are two different jobs, and want different room.

        Preview answers one question -- "will this read well when it lands?" --
        and it answers it better with the window to itself. The right rail is
        for writing, so it stands down; the post is then centred in the space
        the way a feed centres it, instead of being pinned into the left half.
        """
        preview = mode == "preview"
        if preview:
            self.capture()
        self.sidebar_holder.setVisible(not preview)
        self.attach_holder.setVisible(not preview)
        self.slot.setCurrentWidget(self.preview if preview else self.editor)
        if preview:
            self.refresh_preview()

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
        clear(self.attachment_box)
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


# Decoded tiles, keyed on the file and the box it was drawn for. Bounded,
# because a preview is redrawn on every resize and mode switch.
_TILE_CACHE: dict[tuple, QPixmap] = {}
_TILE_CACHE_LIMIT = 64


def _file_key(path: Path) -> tuple | None:
    """Identity of the file as it is right now, or None if it is not there."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _oriented_size(reader: QImageReader):
    """The size the picture is *shown* at, with its EXIF rotation applied.

    A phone photo is stored landscape with "rotate 90" in its header. The
    header size is therefore the wrong way round for anything that has to lay
    the picture out, so the axes are swapped for the quarter-turn cases.
    """
    size = reader.size()
    if not size.isValid() or size.width() <= 0 or size.height() <= 0:
        return None
    # The Transformation flags are a bitfield; bit 2 is the quarter turn.
    if reader.transformation().value & QImageIOHandler.TransformationRotate90.value:
        return QSize(size.height(), size.width())
    return size


def image_size(path: Path):
    """The picture's size as displayed, read from the header.

    `QPixmap(path)` decodes the entire image. A phone photo is 24 megapixels
    and takes **~600ms** to decode, and the preview was paying that merely to
    ask how tall the picture is. QImageReader reads the header instead.
    """
    try:
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        return _oriented_size(reader)
    except Exception:
        return None


def load_tile(path: Path, width: int, height: int) -> QPixmap:
    """The picture, decoded at roughly the size it will be drawn at.

    Three things were wrong with `QPixmap(str(path))` here, and one 8MB holiday
    photo made all three obvious: it decoded 24 megapixels to fill a 480x270
    box, it did that again for every redraw -- a resize, a switch to Preview,
    a walk to another screen and back -- and `_single_height` did it a second
    time per render just to read the aspect ratio.

    JPEG decoders can scale while decoding, which is far cheaper than decoding
    full size and scaling afterwards, so the reader is told the size wanted.
    The result is cached against the file's mtime, so an edited picture is
    still picked up. Never raises: an unreadable file returns a null pixmap and
    the caller draws a named tile, because the file is still going to be
    uploaded and showing nothing would suggest it had been dropped.
    """
    key = _file_key(path)
    if key is None:
        return QPixmap()
    cache_key = key + (width, height)
    cached = _TILE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    try:
        reader = QImageReader(str(path))
        # Facebook applies the EXIF rotation when it renders, so a preview that
        # did not would be answering the wrong question. It also means the size
        # to scale against is the rotated one.
        reader.setAutoTransform(True)
        natural = _oriented_size(reader)
        if natural is not None:
            # Enough to fill the box; cover() crops the overflow afterwards,
            # which is cheap once the decode is already this small.
            scale = max(width / natural.width(), height / natural.height())
            if scale < 1.0:
                # setScaledSize is in stored orientation, so swap back for a
                # quarter turn -- otherwise a portrait photo decodes to the
                # wrong shape and cover() has to crop most of it away.
                target = QSize(round(natural.width() * scale), round(natural.height() * scale))
                if reader.transformation().value & QImageIOHandler.TransformationRotate90.value:
                    target = QSize(target.height(), target.width())
                reader.setScaledSize(target)
        image = reader.read()
        pixmap = QPixmap.fromImage(image) if not image.isNull() else QPixmap()
    except Exception:
        pixmap = QPixmap()

    if len(_TILE_CACHE) >= _TILE_CACHE_LIMIT:
        _TILE_CACHE.clear()
    _TILE_CACHE[cache_key] = pixmap
    return pixmap


def cover(pixmap: QPixmap, width: int, height: int) -> QPixmap:
    """Scale to fill the box and crop the overflow, centred.

    What every feed does with a photo. Fitting inside the box instead leaves
    letterbox bars and makes a row of tiles look ragged; stretching to the box
    distorts faces. Filling and cropping is the only option that keeps a grid
    tidy and the picture honest.
    """
    if pixmap.isNull() or width <= 0 or height <= 0:
        return pixmap
    scaled = pixmap.scaled(
        width, height, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
    )
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, min(width, scaled.width()), min(height, scaled.height()))


def avatar(letter: str, size: int = AVATAR_SIZE) -> QPixmap:
    """A round monogram standing in for the group's picture.

    Drawn rather than shipped as an asset, and a letter rather than an emoji --
    emoji as iconography renders differently on every machine and reads as a
    placeholder that was never finished.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(theme.C["ACCENT"]))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(0, 0, size, size)
        painter.setPen(QColor(theme.C["TEXT_ON_ACCENT"]))
        font = painter.font()
        font.setPixelSize(int(size * 0.45))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pixmap.rect(), Qt.AlignCenter, (letter or "?")[:1].upper())
    finally:
        painter.end()
    return pixmap


class MediaTile(QLabel):
    """One picture in the collage, cropped to fill its slot.

    Never raises on a bad file: a missing or corrupt image draws a named tile
    instead, because the file is still going to be uploaded and showing nothing
    would suggest it had been dropped.
    """

    def __init__(self, path: Path, width: int, height: int, extra: int = 0) -> None:
        super().__init__()
        self.path = Path(path)
        self.setFixedSize(width, height)
        self.setAlignment(Qt.AlignCenter)

        pixmap = load_tile(self.path, width, height)
        self.readable = not pixmap.isNull()
        if not self.readable:
            self.setText(f"{self.path.name}\n(no preview)")
            self.setWordWrap(True)
            self.setStyleSheet(
                f"background: {theme.C['WINDOW_BG']};"
                f" color: {theme.C['TEXT_MUTED']};"
                f" font-size: {theme.SIZE_SMALL}px;"
            )
            return

        shown = cover(pixmap, width, height)
        if extra > 0:
            shown = self._with_overlay(shown, f"+{extra}")
        self.setPixmap(shown)

    @staticmethod
    def _with_overlay(pixmap: QPixmap, text: str) -> QPixmap:
        """The "+3" tile: how a feed says there are more pictures than shown."""
        stamped = QPixmap(pixmap)
        painter = QPainter(stamped)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(stamped.rect(), QColor(0, 0, 0, 140))
            painter.setPen(QColor("#FFFFFF"))
            font = painter.font()
            font.setPixelSize(max(20, stamped.height() // 5))
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(stamped.rect(), Qt.AlignCenter, text)
        finally:
            painter.end()
        return stamped


class PostPreview(QWidget):
    """The post as the group will actually see it.

    Shaped like a real feed post -- monogram, name, meta line, then the text,
    then the pictures full-bleed to the card edges, then the action row. The
    point is not decoration: a preview that looks nothing like the destination
    cannot answer "will this read well when it lands?", which is the only
    question it exists to answer. The collage in particular is what tells the
    user that four photos become a 2x2 grid rather than four stacked strips.

    There is still no direction handling anywhere in here. Qt orders mixed
    Hebrew and English correctly and right-aligns a Hebrew paragraph by itself.
    """

    def __init__(self) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.area)
        self.holder = QWidget()
        self.body = QVBoxLayout(self.holder)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.area.setWidget(self.holder)

        self._body_text = ""
        self._last = ("", "", "", [])
        self._rendered_width = 0
        # The label carrying the post text, for tests and for the size bump.
        self.message_label: QLabel | None = None
        self.render()

    # -- what the caller asks about -----------------------------------------
    def text_shown(self) -> str:
        """The logical post text, not whatever the labels happen to hold."""
        return self._body_text

    @property
    def tiles(self) -> list[MediaTile]:
        """Every picture tile currently drawn, for tests and for counting."""
        return self.holder.findChildren(MediaTile)

    def post_width(self) -> int:
        """How wide the fake post is, clamped to something feed-shaped.

        A post stretched across a wide pane stops looking like a post; one
        squeezed below the floor stops being readable. Recomputed on resize
        rather than fixed, so the pane can be any width without a horizontal
        scrollbar appearing.
        """
        available = self.area.viewport().width() - theme.PAD_S
        return max(MIN_POST_WIDTH, min(MAX_POST_WIDTH, available))

    # -- rendering -----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        # Only when it actually matters: re-laying out the collage on every
        # pixel of a window drag would be a lot of scaling for nothing.
        if abs(self.post_width() - self._rendered_width) > RESIZE_SLACK:
            self._redraw()

    def render(self, heading: str = "", subheading: str = "", text: str = "",
               media: list[Path] | None = None) -> None:
        self._last = (heading, subheading, text, list(media or []))
        self._redraw()

    def _redraw(self) -> None:
        heading, subheading, text, media = self._last
        clear(self.body)
        self._body_text = ""
        self._rendered_width = self.post_width()

        self.message_label = None
        if not text.strip() and not media:
            note = QLabel("Nothing to preview yet — write something on the Write tab.")
            note.setObjectName("Muted")
            note.setWordWrap(True)
            self.body.addWidget(note)
            self.body.addStretch(1)
            return

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        # A stretch on both sides. A feed centres its column; pinned to the
        # left edge of a wide pane the post reads as a stray panel.
        row.addStretch(1)
        row.addWidget(self._post(heading, subheading, text, media))
        row.addStretch(1)
        holder = QWidget()
        holder.setLayout(row)
        self.body.addWidget(holder)
        self.body.addStretch(1)

    def _post(self, heading: str, subheading: str, text: str,
              media: list[Path]) -> QWidget:
        width = self._rendered_width
        post = card()
        post.setFixedWidth(width)
        layout = QVBoxLayout(post)
        # No horizontal padding on the card itself: the pictures run edge to
        # edge the way they do in a feed, so the text gets its own inset.
        layout.setContentsMargins(0, theme.PAD_M, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._header(heading, subheading))
        layout.addSpacing(theme.PAD_S)

        body = text.rstrip()
        self._body_text = body if body.strip() else NO_TEXT
        message = QLabel(self._body_text)
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse)
        message.setContentsMargins(theme.PAD_M, 0, theme.PAD_M, 0)
        if not body.strip():
            message.setObjectName("Muted")
        elif len(body) <= BIG_TEXT_CHARS and not media and "\n" not in body:
            # A short post with no picture is set large in the feed. Copying it
            # is most of why a preview reads as "a post" rather than "a label".
            message.setStyleSheet(f"font-size: {theme.SIZE_TITLE}px;")
        self.message_label = message
        layout.addWidget(message)

        if media:
            layout.addSpacing(theme.PAD_S)
            layout.addWidget(self._collage(media, width))
        else:
            layout.addSpacing(theme.PAD_S)

        layout.addWidget(self._footer())
        return post

    def _header(self, heading: str, subheading: str) -> QWidget:
        holder = row()
        line = QHBoxLayout(holder)
        line.setContentsMargins(theme.PAD_M, 0, theme.PAD_M, 0)
        line.setSpacing(theme.PAD_S)

        picture = QLabel()
        picture.setPixmap(avatar(heading))
        picture.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        line.addWidget(picture)

        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        name = QLabel(heading or ALL_GROUPS_TAB)
        name.setStyleSheet(f"font-weight: 600; font-size: {theme.SIZE_BODY}px;")
        names.addWidget(name)
        meta = QLabel(subheading)
        meta.setObjectName("Muted")
        names.addWidget(meta)
        line.addLayout(names, 1)

        more = QLabel("···")
        more.setObjectName("Muted")
        more.setStyleSheet(f"font-size: {theme.SIZE_HEADING}px;")
        line.addWidget(more, 0, Qt.AlignTop)
        return holder

    def _collage(self, media: list[Path], width: int) -> QWidget:
        """The 1 / 2 / 3 / 4 / more layouts a feed uses, in that order."""
        holder = row()
        grid = QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(TILE_GAP)

        shown = media[:MAX_TILES]
        extra = len(media) - len(shown)
        half = (width - TILE_GAP) // 2

        if len(shown) == 1:
            grid.addWidget(MediaTile(shown[0], width, self._single_height(shown[0], width)), 0, 0)
        elif len(shown) == 2:
            for column, path in enumerate(shown):
                grid.addWidget(MediaTile(path, half, PAIR_HEIGHT), 0, column)
        elif len(shown) == 3:
            grid.addWidget(MediaTile(shown[0], width, TRIPLE_TOP_HEIGHT), 0, 0, 1, 2)
            for column, path in enumerate(shown[1:]):
                grid.addWidget(MediaTile(path, half, TRIPLE_BOTTOM_HEIGHT), 1, column)
        else:
            for index, path in enumerate(shown):
                # The last tile carries the "+N" when more were attached.
                more = extra if (index == MAX_TILES - 1 and extra) else 0
                grid.addWidget(
                    MediaTile(path, half, QUAD_HEIGHT, extra=more),
                    index // 2,
                    index % 2,
                )
        return holder

    @staticmethod
    def _single_height(path: Path, width: int) -> int:
        """One picture keeps its own shape, within reason.

        A single photo is shown at its natural aspect ratio -- cropping the
        only picture in a post to a fixed box would misrepresent it -- but a
        very tall one is capped so it does not push everything else off screen.
        """
        size = image_size(path)
        if size is None:
            return PAIR_HEIGHT
        natural = int(width * size.height() / size.width())
        return max(MIN_SINGLE_HEIGHT, min(MAX_SINGLE_HEIGHT, natural))

    def _footer(self) -> QWidget:
        """Like / Comment / Share. Structural chrome, and deliberately inert.

        No counts: inventing "12 likes" on a post that has not been published
        would be showing the user something untrue.
        """
        holder = row()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        outer.setSpacing(theme.PAD_S)

        divider = QFrame()
        divider.setObjectName("Divider")
        divider.setFixedHeight(1)
        outer.addWidget(divider)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        for name in ("Like", "Comment", "Share"):
            label = QLabel(name)
            label.setObjectName("Muted")
            label.setAlignment(Qt.AlignCenter)
            actions.addWidget(label, 1)
        outer.addLayout(actions)
        return holder
