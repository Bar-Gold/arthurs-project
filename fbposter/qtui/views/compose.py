"""Compose screen: what the post says. The first step of three.

Groups are chosen on the Groups screen and timing on Publish. The post can be
written as several versions, one tab each, with no groups picked yet; they
are shared out across the groups chosen next (the user's choice, 2026-10-23).
It used to be a tab per group, which could only be filled in after going on
to step 2 and coming back.

There is no direction handling anywhere in this file, and that is the point.
Qt shapes text itself, so Hebrew, English and digits on one line come out in
the right order and a Hebrew paragraph aligns itself to the right. The Tk build
needed a module of Unicode rules, an invisible embedding character and a
reordering library to get part of the way there.
"""

from __future__ import annotations

import html
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QIcon,
    QImageIOHandler,
    QImageReader,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QShortcut,
)
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fbposter import clock, media, recurrence
from fbposter.text import strip_invisible

from .. import icons, theme
from ..widgets import card, clear, literal, row, segmented

# Pictures and videos together (video since 2026-10-15). The video suffixes
# are the ones the group composer itself accepts, read live (media.py).
MEDIA_FILTER = (
    "Pictures and videos ("
    + " ".join(f"*{s}" for s in sorted(media.PICTURE_SUFFIXES | media.VIDEO_SUFFIXES))
    + ");;All files (*.*)"
)
ATTACH_LABEL = "Attach pictures or videos"
NO_ATTACHMENTS = "No pictures or videos attached."
LOAD_TEMPLATE = "Load template"
SAVE_TEMPLATE = "Save as template"
NO_TEMPLATES = "No templates saved yet"
TEMPLATE_NAME_WIDTH = 220
# An attached picture on step 1: a square, cropped to fill it.
THUMB_SIZE = 72

# -- preview geometry ------------------------------------------------------
# The post as a computer shows it and as a phone shows it, side by side (the
# user's choice, 2026-10-22). Facebook's feed column is around 500px and a
# phone around 375. Narrower than the floor and a post stops being readable.
MAX_POST_WIDTH = 500
MIN_POST_WIDTH = 280
# The narrowest the computer post is made to fit the box's height. Narrower,
# a post of pictures shrinks to a thumbnail; past here its pictures are made
# a little shorter instead (`MIN_SQUASH`).
FIT_FLOOR = 340
PHONE_WIDTH = 375
MIN_PHONE_WIDTH = 240
COLUMN_GAP = 24
# Re-laying out the collage on every pixel of a window drag would be a lot of
# rescaling for nothing.
RESIZE_SLACK = 12

AVATAR_SIZE = 40
TILE_GAP = 2
MAX_TILES = 5          # beyond this the last tile carries a "+N"
# A single picture keeps its own shape between these, as a share of the post's
# width: a panorama is not stretched, a tall photo is cut. Facebook shows a
# tall one up to 2:3 on a computer and 4:5 on a phone.
MIN_SINGLE_SHAPE = 0.36
MAX_SINGLE_SHAPE = {"computer": 1.5, "phone": 1.25}
# The big picture's share of a three or four picture layout: 327 of 492px.
BIG_SHARE = 0.665
# The shortest a picture may be made, against its height in the layout, to fit
# a post in a small window (PostPreview._fit). Shorter crops away too much.
MIN_SQUASH = 0.6
# Shorter still, only when a post cannot fit any other way: flatter pictures
# read better than a box that scrolls (the user's report, 2026-10-22).
LAST_SQUASH = 0.45
# Where Facebook folds long text behind "See more". It folds by lines on the
# screen, so these are near it, not exact: about 480 characters on a computer
# and 125 on a phone, and a few lines of short ones.
SEE_MORE_CHARS = {"computer": 480, "phone": 125}
SEE_MORE_LINES = {"computer": 5, "phone": 3}
SEE_MORE = "See more"

Rect = tuple[float, float, float, float]


def shape_of(path: Path) -> str:
    """"landscape", "portrait" or "square": what Facebook picks a layout by.

    A video is never opened to ask; it is drawn widescreen. A picture that
    will not open is taken as square.
    """
    if media.is_video(path):
        return "landscape"
    size = image_size(path)
    if size is None:
        return "square"
    ratio = size.width() / size.height()
    if ratio > 1.1:
        return "landscape"
    if ratio < 0.9:
        return "portrait"
    return "square"


def collage_layout(count: int, shape: str) -> tuple[float, list[Rect]]:
    """Where Facebook puts 2 to 5 pictures, as shares of the post's width.

    Returns the collage's height and one (x, y, width, height) per picture,
    in order. The first picture's shape picks the layout: a wide one goes on
    top with the rest under it, a tall one on the left with the rest stacked
    beside it, and square ones make a grid. Five or more are two above three,
    whatever their shape.
    """
    big, rest = BIG_SHARE, 1 - BIG_SHARE
    if count == 2:
        if shape == "landscape":
            return 1.0, [(0, 0, 1, 0.5), (0, 0.5, 1, 0.5)]
        if shape == "portrait":
            return 1.0, [(0, 0, 0.5, 1), (0.5, 0, 0.5, 1)]
        return 0.5, [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5)]
    if count == 3:
        if shape == "portrait":
            return 1.0, [(0, 0, big, 1), (big, 0, rest, 0.5), (big, 0.5, rest, 0.5)]
        top = big if shape == "landscape" else 0.5
        return 1.0, [(0, 0, 1, top), (0, top, 0.5, 1 - top), (0.5, top, 0.5, 1 - top)]
    if count == 4:
        third = 1 / 3
        if shape == "portrait":
            return 1.0, [(0, 0, big, 1)] + [(big, i * third, rest, third) for i in range(3)]
        if shape == "landscape":
            return 1.0, [(0, 0, 1, big)] + [(i * third, big, third, rest) for i in range(3)]
        return 1.0, [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5), (0, 0.5, 0.5, 0.5), (0.5, 0.5, 0.5, 0.5)]
    third = 1 / 3
    return 0.5 + third, (
        [(0, 0, 0.5, 0.5), (0.5, 0, 0.5, 0.5)]
        + [(i * third, 0.5, third, third) for i in range(3)]
    )


def see_more_cut(text: str, kind: str) -> str | None:
    """The part of `text` Facebook shows before "See more", or None for all."""
    limit, lines = SEE_MORE_CHARS[kind], SEE_MORE_LINES[kind]
    split = text.split("\n")
    if len(text) <= limit and len(split) <= lines:
        return None
    shown = "\n".join(split[:lines])[:limit]
    if len(shown) < len(text) and not text[len(shown)].isspace() and " " in shown:
        # Never mid-word.
        shown = shown[: shown.rindex(" ")]
    return shown.rstrip()


NO_TEXT = "(No text. The post would be pictures or videos only.)"


def check_attachments(names, already) -> tuple[list[Path], str, str]:
    """Which picked files to attach, and what to tell the user.

    Shared by Compose and the repeating-post edit screen. A video over
    Facebook's own limit is refused here rather than after an hour of
    uploading. A big one is attached with a note, because the batch waits for
    it to upload before the post goes out, and the posts behind it wait too.
    """
    accepted: list[Path] = []
    refused: list[str] = []
    slow = False
    for name in names:
        path = Path(name)
        if path in already or path in accepted:
            continue
        if media.too_large(path):
            refused.append(path.name)
            continue
        accepted.append(path)
        slow = slow or media.slow_to_upload(path)
    if refused:
        return (
            accepted,
            f"Not attached: {', '.join(refused)}. Facebook takes videos of up to "
            "10 GB.",
            "error",
        )
    if not accepted:
        return accepted, "", "info"
    message = f"Attached {len(accepted)} file{'s' if len(accepted) != 1 else ''}."
    if slow:
        return (
            accepted,
            message + " A large video takes a while to upload, and the post waits "
            "for it before it goes out.",
            "info",
        )
    return accepted, message, "success"


def media_only(paths) -> str:
    """How a post with pictures or videos and no words is named on Publish
    and the Dashboard: "Pictures only (2 pictures, no text)"."""
    paths = list(paths)
    return f"{media.kind(paths)} only ({media.describe(paths)}, no text)"

# Below this, a picture-less post is set large, the way a feed does it.
BIG_TEXT_CHARS = 85

# The group's name on the preview's post, which is not one group's.
ALL_GROUPS = "All groups"
# In the empty writing box, until the first letter.
EDITOR_PLACEHOLDER = "Write your post here"
ADD_VERSION = "Add version"
REMOVE_VERSION = "Remove this version"
ONE_VERSION_HINT = "Add a version to vary the words between groups."


def version_label(index: int) -> str:
    """A version's name on screen, counted from 1."""
    return f"Version {index + 1}"


class ComposeView(QWidget):
    title = "Compose"
    subtitle = "Write the post and attach pictures or videos. Groups and timing come next."

    def __init__(self, app) -> None:
        super().__init__()
        self.app = app
        self.attachments: list[Path] = []

        # The post's versions, in order, and the one in the editor. There is
        # always at least one.
        self._versions: list[str] = [""]
        self._editing = 0
        # The version a group was given by hand on step 3, by group id. The
        # rest are shared out by `assignment`.
        self._chosen: dict[int, int] = {}
        # The strip as currently drawn, so an unchanged one is not rebuilt.
        self._tabs_shown: list[tuple[int, str]] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(theme.PAD_XS)

        # Hidden inside Create a post, which has one title for all three steps.
        self.heading = QLabel(self.title)
        self.heading.setObjectName("Title")
        outer.addWidget(self.heading)
        sub = QLabel(self.subtitle)
        sub.setObjectName("Subtitle")
        outer.addWidget(sub)
        outer.addSpacing(theme.HEADER_GAP)

        self._build_editor(outer)
        # After the whole page: each of these restates the Next button.
        self._render_attachments()
        self.refresh_tabs()
        self.refresh_templates()

    def on_show(self) -> None:
        self.refresh_tabs()
        self.refresh_templates()

    def notify(self, message: str, level: str = "info") -> None:
        self.app.toast(message, level)

    # -- the page --------------------------------------------------------------
    def _build_editor(self, column: QVBoxLayout) -> None:
        """One column (2026-10-24). The templates were a card of their own in
        a right-hand column, which stood mostly empty once "Sending to" went;
        they are two buttons on the toggle's row now, and the box has the
        width. The pictures are inside the box, under the words, as they are
        in the post itself."""
        # Two rows rather than one. The mode toggle sits top-left, the version
        # tabs get the full width underneath it: sharing a row, the tabs were
        # squeezed below their own text and clipped mid-word. Left, because
        # Preview stands the writing tools down: at the right the toggle
        # jumped across the window, away from the pointer that had just
        # clicked it (2026-10-21).
        top = QHBoxLayout()
        top.setSpacing(theme.PAD_S)

        self.tab_scroll = QScrollArea()
        # Its sideways bar is drawn as a thin line (theme.py, #TabStrip).
        self.tab_scroll.setObjectName("TabStrip")
        self.tab_scroll.setWidgetResizable(True)
        self.tab_scroll.setFrameShape(QFrame.NoFrame)
        self.tab_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tab_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.tab_scroll.setFixedHeight(theme.CONTROL_HEIGHT + theme.PAD_S)
        tab_holder = QWidget()
        strip = QHBoxLayout(tab_holder)
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(theme.PAD_XS)
        # The version tabs alone, in a layout of their own: refresh_tabs()
        # clears it by index and counts what is in it, so the add button and
        # the stretch live beside it rather than in it.
        self.tab_bar = QHBoxLayout()
        self.tab_bar.setContentsMargins(0, 0, 0, 0)
        self.tab_bar.setSpacing(theme.PAD_XS)
        strip.addLayout(self.tab_bar)
        self.add_version_button = QPushButton(ADD_VERSION)
        icons.set_button_icon(self.add_version_button, "plus")
        self.add_version_button.clicked.connect(lambda: self.add_version())
        strip.addWidget(self.add_version_button)
        strip.addStretch(1)
        self.tab_scroll.setWidget(tab_holder)

        toggle, (self.write_button, self.preview_button) = segmented(("Write", "Preview"))
        modes = QButtonGroup(self)
        for index, button in enumerate((self.write_button, self.preview_button)):
            modes.addButton(button, index)
        top.addWidget(toggle)
        top.addStretch(1)
        self.template_tools = self._build_templates()
        # Its room is kept while Preview hides it, so the row, and the toggle
        # in it, stay exactly where they were clicked.
        policy = self.template_tools.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.template_tools.setSizePolicy(policy)
        top.addWidget(self.template_tools)
        self.write_button.setChecked(True)
        self.write_button.clicked.connect(lambda: self.set_mode("write"))
        self.preview_button.clicked.connect(lambda: self.set_mode("preview"))
        column.addLayout(top)

        # The versions, and taking out the one open, on one row: it was down
        # in the box's footer, in the middle of nowhere (2026-10-24).
        versions = QHBoxLayout()
        versions.setSpacing(theme.PAD_S)
        versions.addWidget(self.tab_scroll, 1)
        self.remove_version_button = QPushButton(REMOVE_VERSION)
        self.remove_version_button.setObjectName("Link")
        self.remove_version_button.clicked.connect(lambda: self.remove_version())
        self.remove_version_button.hide()
        versions.addWidget(self.remove_version_button)
        column.addLayout(versions)

        holder = card()
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(theme.PAD_M, theme.PAD_M, theme.PAD_M, theme.PAD_M)
        holder_layout.setSpacing(theme.PAD_S)
        # Preview takes the box edge to edge; see set_mode.
        self.editor_layout = holder_layout

        self.slot = QStackedWidget()
        self.editor = QTextEdit()
        self.editor.setObjectName("Editor")
        self.editor.setAcceptRichText(False)
        self.editor.setPlaceholderText(EDITOR_PLACEHOLDER)
        self.editor.textChanged.connect(self._on_text_changed)
        self.slot.addWidget(self.editor)

        self.preview = PostPreview()
        self.slot.addWidget(self.preview)
        holder_layout.addWidget(self.slot, 1)

        # The pictures and videos, under the words and inside the box, as they
        # sit in the post itself: a square each, with a button on its corner
        # that takes it off (2026-10-24). They were a white bar per file name
        # under the box. It scrolls sideways once there are more than fit.
        self.thumb_strip = QScrollArea()
        self.thumb_strip.setObjectName("ThumbStrip")
        self.thumb_strip.setWidgetResizable(True)
        self.thumb_strip.setFrameShape(QFrame.NoFrame)
        self.thumb_strip.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.thumb_strip.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.thumb_strip.setFixedHeight(THUMB_SIZE + theme.PAD_S)
        thumbs = row()
        self.attachment_box = QHBoxLayout(thumbs)
        self.attachment_box.setContentsMargins(0, 0, 0, 0)
        self.attachment_box.setSpacing(theme.PAD_S)
        self.thumb_strip.setWidget(thumbs)
        holder_layout.addWidget(self.thumb_strip)

        # Writing aids, so Preview stands them down with the rest: attaching
        # at the left, as a composer has it, and the count and the hint about
        # versions at the right.
        self.editor_footer = row()
        footer = QHBoxLayout(self.editor_footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(theme.PAD_L)
        # Not #Primary: the accent on this screen belongs to "Next: choose the
        # groups", and two filled buttons read as two main actions.
        self.attach_button = QPushButton(ATTACH_LABEL)
        icons.set_button_icon(self.attach_button, "picture")
        self.attach_button.clicked.connect(self._pick_files)
        footer.addWidget(self.attach_button)
        footer.addStretch(1)
        self.variation_note = QLabel(ONE_VERSION_HINT)
        self.variation_note.setObjectName("Muted")
        footer.addWidget(self.variation_note)
        self.counter = QLabel("0 characters")
        self.counter.setObjectName("Muted")
        footer.addWidget(self.counter)
        holder_layout.addWidget(self.editor_footer)
        column.addWidget(holder, 1)

        # Next at the bottom right, where step 2 has its own. Preview stands
        # it down with the writing tools: its row is height the post needs.
        self.next_row = row()
        bottom = QHBoxLayout(self.next_row)
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addStretch(1)
        self.next_button = QPushButton("Next: choose the groups →")
        self.next_button.setObjectName("Primary")
        self.next_button.clicked.connect(self.go_to_groups)
        bottom.addWidget(self.next_button)
        column.addWidget(self.next_row)

    def _build_templates(self) -> QWidget:
        """Load template and Save as template, at the right of the toggle's
        row. Saving asks for a name in the same place, in a box that opens
        where the two buttons were: no dialog, and nothing moves elsewhere."""
        tools = row()
        line = QHBoxLayout(tools)
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(theme.PAD_S)

        self.load_template_button = QPushButton(LOAD_TEMPLATE)
        self.load_template_button.setObjectName("MenuButton")
        self.template_menu = QMenu(self.load_template_button)
        self.load_template_button.setMenu(self.template_menu)
        line.addWidget(self.load_template_button)
        self.save_template_button = QPushButton(SAVE_TEMPLATE)
        self.save_template_button.clicked.connect(lambda: self.start_saving_template())
        line.addWidget(self.save_template_button)

        self.template_name = QLineEdit()
        self.template_name.setPlaceholderText("Give it a name")
        self.template_name.setAccessibleName("Template name")
        self.template_name.setFixedWidth(TEMPLATE_NAME_WIDTH)
        self.template_name.returnPressed.connect(lambda: self.save_template())
        cancel_key = QShortcut(QKeySequence(Qt.Key_Escape), self.template_name)
        cancel_key.setContext(Qt.WidgetShortcut)
        cancel_key.activated.connect(self.cancel_saving_template)
        line.addWidget(self.template_name)
        self.confirm_template_button = QPushButton("Save")
        self.confirm_template_button.clicked.connect(lambda: self.save_template())
        line.addWidget(self.confirm_template_button)
        self.cancel_template_button = QPushButton("Cancel")
        self.cancel_template_button.setObjectName("BackLink")
        self.cancel_template_button.clicked.connect(self.cancel_saving_template)
        line.addWidget(self.cancel_template_button)
        self._show_template_name(False)
        return tools

    def _show_template_name(self, naming: bool) -> None:
        for widget in (self.load_template_button, self.save_template_button):
            widget.setVisible(not naming)
        for widget in (self.template_name, self.confirm_template_button,
                       self.cancel_template_button):
            widget.setVisible(naming)

    def start_saving_template(self) -> None:
        """Ask for the template's name where the buttons were."""
        self._show_template_name(True)
        self.template_name.setFocus(Qt.OtherFocusReason)

    def cancel_saving_template(self) -> None:
        self.template_name.clear()
        self._show_template_name(False)

    def go_to_groups(self) -> None:
        self.capture()
        self.app.show_view("groups")

    def refresh_next(self) -> None:
        """Next is greyed out while the post has nothing in it (the user's
        choice, 2026-10-21), as Next on Groups is while no group is picked.
        An empty post used to go on to step 2 and was only refused at step 3."""
        self.next_button.setEnabled(self.has_content())

    def has_content(self) -> bool:
        """Some text in any version, or a picture or video: what Next would
        leave committed, so the half-typed editor counts too."""
        if self.attachments or self.get_text().strip():
            return True
        return any(
            text.strip() for index, text in enumerate(self._versions) if index != self._editing
        )

    def clear_draft(self) -> None:
        """A blank post: one empty version, no pictures, back on Write.

        Called when a post has gone out or been queued (the user's choice,
        2026-10-19), never on a refusal."""
        self._versions = [""]
        self._editing = 0
        self._chosen.clear()
        self._show("")
        self.attachments = []
        self._render_attachments()
        self.write_button.setChecked(True)
        self.set_mode("write")
        self.refresh_tabs()

    # -- text --------------------------------------------------------------
    def get_text(self) -> str:
        """The only read of the editor, and the only place text is cleaned.

        Qt needs no direction marks of its own, but a paste out of Word or
        WhatsApp brings its own invisible ones. Left in, they make an identical
        post compare as different, and an old post in the feed is then taken
        for this one (`PostRequest.sent_before`).
        """
        return strip_invisible(self.editor.toPlainText())

    def _show(self, text: str) -> None:
        blocked = self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(blocked)
        self._update_counter()
        self.refresh_next()

    def _on_text_changed(self) -> None:
        self._update_counter()
        self.refresh_next()

    def _update_counter(self) -> None:
        count = len(self.get_text())
        self.counter.setText(f"{count} character{'s' if count != 1 else ''}")

    # -- versions ----------------------------------------------------------
    def capture(self) -> None:
        """Save what is in the editor into the version it is showing. Every
        version stands on its own: changing one never touches another."""
        self._versions[self._editing] = self.get_text()

    def versions(self) -> list[str]:
        """The versions with words in, in order: what a repeating post
        rotates through. Committed state only."""
        return [text.strip() for text in self._versions if text.strip()]

    def filled_versions(self) -> list[int]:
        """The places of the versions with words in: what a group can be
        given on step 3."""
        return [index for index, text in enumerate(self._versions) if text.strip()]

    def version_count(self) -> int:
        """Every version, empty ones too: what the tabs show."""
        return len(self._versions)

    def base_body(self) -> str:
        """The first version with words in, or "". A batch keeps it as its
        own text, and step 3 shows it as the post. Committed state."""
        return next((text for text in self._versions if text.strip()), "")

    def select_version(self, index: int) -> None:
        self.capture()
        self._editing = index
        self._show(self._versions[index])
        self.refresh_tabs()

    def add_version(self) -> None:
        """A new, empty version, open in the editor ready to type."""
        self.capture()
        self._versions.append("")
        self._editing = len(self._versions) - 1
        self._show("")
        self.refresh_tabs()
        self.editor.setFocus(Qt.OtherFocusReason)

    def remove_version(self) -> None:
        """Take out the version being edited, with an Undo if it had words.

        A group given it by hand on step 3 goes back to being given one by
        the app. The last version cannot go: there is always one.
        """
        if len(self._versions) == 1:
            return
        self.capture()
        index = self._editing
        text = self._versions.pop(index)
        dropped = [gid for gid, chosen in self._chosen.items() if chosen == index]
        self._chosen = {
            gid: chosen - 1 if chosen > index else chosen
            for gid, chosen in self._chosen.items()
            if chosen != index
        }
        self._editing = min(index, len(self._versions) - 1)
        self._show(self._versions[self._editing])
        self.refresh_tabs()
        if not text.strip():
            return

        def undo() -> str:
            self.capture()
            # Back in its place, or last if versions were removed since.
            at = min(index, len(self._versions))
            self._versions.insert(at, text)
            self._chosen = {
                gid: chosen + 1 if chosen >= at else chosen
                for gid, chosen in self._chosen.items()
            }
            self._chosen.update({gid: at for gid in dropped})
            self._editing = at
            self._show(text)
            self.refresh_tabs()
            return f"{version_label(at)} is back."

        self.app.toast(f"Removed {version_label(index).lower()}.", "info", undo=undo)

    def selected_group_ids(self) -> list[int]:
        """Chosen on the Groups screen; this view only reads them."""
        return self.app.selected_group_ids()

    # -- which group gets which version --------------------------------------
    def assignment(self) -> dict[int, int | None]:
        """Each chosen group's version, as an index into the versions.

        Shared out by `recurrence.pick_index`, the rule repeating posts use:
        the group at place n starts at version n, and gets one it has not had
        today when there is one, so the same listing posted twice in a day
        reaches each group in other words the second time. A version left
        empty is passed over. A group given one by hand on step 3 keeps it.
        None for every group when no version has words: pictures alone.

        One query for the whole list: step 3 asks on the thread drawing the
        window, for as many as 80 groups.
        """
        selected = self.selected_group_ids()
        today = self.app.group_repo.sent_on_day_many(selected, clock.utcnow())
        shared: dict[int, int | None] = {}
        for position, group_id in enumerate(selected):
            chosen = self._chosen.get(group_id)
            if chosen is not None and 0 <= chosen < len(self._versions) \
                    and self._versions[chosen].strip():
                shared[group_id] = chosen
            else:
                shared[group_id] = recurrence.pick_index(
                    self._versions, position, today.get(group_id, ())
                )
        return shared

    def bodies(self) -> dict[int, str]:
        """The words each chosen group will receive. Committed state only."""
        return {
            group_id: "" if index is None else self._versions[index].strip()
            for group_id, index in self.assignment().items()
        }

    def body_for(self, group_id: int) -> str:
        """The words this group will receive, if it is chosen. The only read
        of one group's text. Committed state only."""
        return self.bodies().get(group_id, self.base_body())

    def choose_version(self, group_id: int, index: int) -> None:
        """Give one group a version by hand, on step 3."""
        self._chosen[group_id] = index

    # -- the strip ---------------------------------------------------------
    def _tab_plan(self) -> list[tuple[int, str]]:
        """The strip as (version index, label) pairs: what to draw, not drawing.

        Which tab is *filled* is deliberately not in here; see
        `_mark_active_tab`, which is why it cannot be.
        """
        return [(index, version_label(index)) for index in range(len(self._versions))]

    def refresh_tabs(self) -> None:
        # Rebuilding the strip is ~9ms of widget construction, so it happens
        # only when the versions change. Everything after the guard still
        # runs on every call: marking the active tab (which the guard cannot
        # govern, see below), the footer and a no-op in Write mode.
        plan = self._tab_plan()
        if plan != self._tabs_shown:
            self._tabs_shown = plan
            clear(self.tab_bar)
            for target, label in plan:
                self._add_tab(target, label)
        self._mark_active_tab()

        several = len(self._versions) > 1
        self._restate_writing_tools()
        self.variation_note.setText(
            f"{len(self._versions)} versions, shared out across the groups."
            if several else ONE_VERSION_HINT
        )
        self.refresh_preview()
        self.refresh_next()

    def _mark_active_tab(self) -> None:
        """Fill the tab whose version the editor is holding. Always.

        Outside the rebuild guard, and it is the one thing on this strip that
        cannot live inside it. Switching tabs changes `_editing` without
        changing a single label, so a guard keyed on the labels skips the
        rebuild and the highlight stays on the tab the user has just left. And
        the buttons are checkable and in no exclusive group, so Qt toggles the
        one clicked *before* select_version is reached: clicking a second tab
        lit two at once, and clicking the tab you were already on switched
        its own highlight off, leaving nothing lit at all. Neither touches the
        labels, so neither is a cache-key problem: the fill simply is not
        something a guard on the contents can govern.

        It matters because the filled tab is the only thing on screen saying
        which version is in the editor. A strip pointing at the wrong tab is
        how someone types into the version they did not mean to.
        """
        for index, (target, _label) in enumerate(self._tabs_shown or ()):
            item = self.tab_bar.itemAt(index)
            button = item.widget() if item is not None else None
            if button is not None:
                button.setChecked(target == self._editing)

    def _add_tab(self, target: int, label: str) -> None:
        button = QPushButton(literal(label))
        button.setObjectName("Tab")
        button.setCheckable(True)
        button.setChecked(target == self._editing)
        button.clicked.connect(lambda _checked, t=target: self.select_version(t))
        self.tab_bar.addWidget(button)

    # -- preview -----------------------------------------------------------
    def set_mode(self, mode: str) -> None:
        """Write and Preview are two different jobs, and want different room.

        Preview answers one question -- "will this read well when it lands?" --
        and it answers it better with the box to itself. The writing tools
        stand down; the post is then centred in the space the way a feed
        centres it, instead of being pinned into the left half. The version
        tabs stay, to pick the version shown.
        """
        preview = mode == "preview"
        if preview:
            self.capture()
        else:
            # The preview goes first, so it is not redrawn for room it is about
            # to give up as each writing tool comes back.
            self.slot.setCurrentWidget(self.editor)
        self._restate_writing_tools(preview)
        # The preview's ground fills the box to its border, rather than sitting
        # in it as a band with white all round.
        edge = 1 if preview else theme.PAD_M
        self.editor_layout.setContentsMargins(edge, edge, edge, edge)
        self.slot.setCurrentWidget(self.preview if preview else self.editor)
        if preview:
            self.refresh_preview()

    def _in_preview(self) -> bool:
        return self.slot.currentWidget() is self.preview

    def _restate_writing_tools(self, preview: bool | None = None) -> None:
        """Show the tools for writing in Write, and stand them down in
        Preview, where the pictures are in the post already."""
        if preview is None:
            preview = self._in_preview()
        self.template_tools.setVisible(not preview)
        self.editor_footer.setVisible(not preview)
        self.next_row.setVisible(not preview)
        self.thumb_strip.setVisible(not preview and bool(self.attachments))
        self.remove_version_button.setVisible(not preview and len(self._versions) > 1)

    def preview_heading(self) -> tuple[str, str]:
        """The name on the preview's post, and the note above it: which
        version, when there are several, else nothing. Never how many groups
        (the user's request, 2026-10-24)."""
        if len(self._versions) > 1:
            return ALL_GROUPS, f"{version_label(self._editing)} of {len(self._versions)}"
        return ALL_GROUPS, ""

    def refresh_preview(self) -> None:
        if self.slot.currentWidget() is not self.preview:
            return
        heading, subheading = self.preview_heading()
        text = self._versions[self._editing]
        self.preview.render(heading, subheading, text, list(self.attachments))

    # -- templates ---------------------------------------------------------
    def refresh_templates(self) -> None:
        """The names under Load template, newest list each time it is shown."""
        self.template_menu.clear()
        names = [t.name for t in self.app.template_repo.list()]
        if not names:
            self.template_menu.addAction(NO_TEMPLATES).setEnabled(False)
        for name in names:
            # literal(): Qt reads "&" in an item as a shortcut marker. The
            # name itself rides along as the item's data.
            action = self.template_menu.addAction(literal(name))
            action.setData(name)
            action.triggered.connect(lambda _checked=False, n=name: self.load_template(n))

    def template_names(self) -> list[str]:
        """The templates Load template offers, by name, in its order."""
        return [a.data() for a in self.template_menu.actions() if a.isEnabled()]

    def save_template(self) -> bool:
        name = self.template_name.text().strip()
        if not name:
            self.notify("Give the template a name first.", "error")
            return False
        self.capture()
        versions = self.versions()
        if not versions:
            self.notify("The post is empty, so there is nothing to save.", "error")
            return False
        self.app.template_repo.save(
            name, versions[0], [str(p) for p in self.attachments], versions=versions
        )
        self.cancel_saving_template()
        self.refresh_templates()
        self.notify(f"Saved template “{name}”.", "success")
        return True

    def load_template(self, name: str) -> bool:
        template = self.app.template_repo.get_by_name(name)
        if template is None:
            self.notify("No template selected.", "warning")
            return False
        self._versions = list(template.versions) or [""]
        self._editing = 0
        self._chosen.clear()
        self._show(self._versions[0])
        self.refresh_tabs()
        self.attachments = [Path(p) for p in template.media_paths]
        self._render_attachments()
        self.notify(f"Loaded “{template.name}”.", "info")
        return True

    # -- attachments -------------------------------------------------------
    def _pick_files(self) -> None:
        """The one modal dialog the app allows: the user asked for it, and it
        cannot appear while a batch is running."""
        chosen, _filter = QFileDialog.getOpenFileNames(
            self, ATTACH_LABEL, "", MEDIA_FILTER
        )
        self.add_files(chosen)

    def add_files(self, names) -> int:
        """Attach files as the picker would. Returns how many were added."""
        accepted, message, level = check_attachments(names, self.attachments)
        self.attachments.extend(accepted)
        if accepted:
            self._render_attachments()
        if message:
            self.notify(message, level)
        return len(accepted)

    def _remove_attachment(self, path: Path) -> None:
        if path in self.attachments:
            self.attachments.remove(path)
            self._render_attachments()

    def _render_attachments(self) -> None:
        clear(self.attachment_box)
        self.refresh_preview()
        self.refresh_next()
        for path in self.attachments:
            self.attachment_box.addWidget(Thumbnail(path, self._remove_attachment))
        self.attachment_box.addStretch(1)
        self._restate_writing_tools()

    # -- queueing ----------------------------------------------------------
    def add_to_queue(self, when=None) -> bool:
        """Queue this post. `when` is a UTC instant, or None for "as soon as".

        Compose owns the content, so it owns the writing of the batch; the
        Publish screen supplies nothing but the moment. Nothing about it is
        judged: there are no posting rules (the user's choice, 2026-10-18).
        """
        self.capture()
        selected = self.selected_group_ids()
        if not selected:
            self.notify("Pick at least one group.", "error")
            return False
        # Pictures go to every group in the batch, so with any attached a group
        # left without words still has something to post: a pictures-only post.
        bodies = self.bodies()
        if not self.attachments and any(not bodies.get(gid, "") for gid in selected):
            self.notify("Every selected group needs some text or a picture or video.", "error")
            return False

        targets = [
            (group_id, bodies.get(group_id, ""))
            for group_id in selected
            if self.app.group_repo.get(group_id) is not None
        ]
        if not targets:
            self.notify("Pick at least one group.", "error")
            return False
        self.app.task_repo.create(
            self.base_body().strip(),
            targets,
            media_paths=[str(p) for p in self.attachments],
            scheduled_for=when,
        )
        count = f"{len(targets)} group{'s' if len(targets) != 1 else ''}"
        self.notify(f"Queued for {count}.", "success")
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


def video_tile(name: str, width: int, height: int) -> QPixmap:
    """A video in the preview: dark, a play button, and the file's name.

    Its colours are fixed, like a video player's, in both themes.
    """
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#18191A"))
    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        radius = max(14, min(width, height) // 8)
        centre_x, centre_y = width // 2, height // 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 60))
        painter.drawEllipse(centre_x - radius, centre_y - radius, radius * 2, radius * 2)
        painter.setBrush(QColor("#FFFFFF"))
        side = radius * 0.9
        painter.drawPolygon(
            QPolygonF(
                [
                    QPointF(centre_x - side * 0.35, centre_y - side * 0.5),
                    QPointF(centre_x - side * 0.35, centre_y + side * 0.5),
                    QPointF(centre_x + side * 0.55, centre_y),
                ]
            )
        )
        painter.setPen(QColor("#E4E6EB"))
        font = painter.font()
        font.setPixelSize(theme.SIZE_SMALL)
        painter.setFont(font)
        painter.drawText(
            pixmap.rect().adjusted(theme.PAD_S, 0, -theme.PAD_S, -theme.PAD_S),
            Qt.AlignBottom | Qt.AlignHCenter,
            painter.fontMetrics().elidedText(name, Qt.ElideMiddle, width - 2 * theme.PAD_S),
        )
    finally:
        painter.end()
    return pixmap


def _screen_scale() -> float:
    screen = QGuiApplication.primaryScreen()
    return screen.devicePixelRatio() if screen is not None else 1.0


def thumbnail_pixmap(path: Path, size: int) -> tuple[QPixmap, bool]:
    """An attachment as a rounded square `size` points across, and whether
    it could be read.

    Decoded at that size, at the screen's own scale, through `load_tile`. A
    video is the preview's player tile, unnamed at this size (the tooltip
    names it). A picture that will not open is a plain tile with the picture
    icon, because it is still going to be uploaded.
    """
    scale = _screen_scale()
    pixels = round(size * scale)
    readable = True
    if media.is_video(path):
        drawn = video_tile("", pixels, pixels)
    else:
        loaded = load_tile(path, pixels, pixels)
        readable = not loaded.isNull()
        if readable:
            drawn = cover(loaded, pixels, pixels)
        else:
            drawn = QPixmap(pixels, pixels)
            drawn.setDevicePixelRatio(scale)
            drawn.fill(QColor(theme.C["NEUTRAL_SOFT"]))
            mark_size = size // 3
            mark = icons.pixmap("picture", theme.C["TEXT_MUTED"], mark_size)
            painter = QPainter(drawn)
            offset = (size - mark_size) / 2
            painter.drawPixmap(QPointF(offset, offset), mark)
            painter.end()
    drawn.setDevicePixelRatio(scale)

    shaped = QPixmap(pixels, pixels)
    shaped.setDevicePixelRatio(scale)
    shaped.fill(Qt.transparent)
    painter = QPainter(shaped)
    try:
        painter.setRenderHint(QPainter.Antialiasing)
        edge = QPainterPath()
        edge.addRoundedRect(QRectF(0, 0, size, size), theme.RADIUS, theme.RADIUS)
        painter.setClipPath(edge)
        painter.drawPixmap(0, 0, drawn)
        painter.setClipping(False)
        # A hairline round it, or a white picture has no edge on the white box.
        painter.setPen(QPen(QColor(theme.C["BORDER"]), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, size - 1, size - 1), theme.RADIUS, theme.RADIUS)
    finally:
        painter.end()
    return shaped, readable


class Thumbnail(QFrame):
    """An attached picture or video on step 1, with a round button on its
    corner that takes it off (2026-10-24).

    It replaced a white bar per file name, which said what was attached
    without showing it.
    """

    def __init__(self, path: Path, on_remove) -> None:
        super().__init__()
        self.path = Path(path)
        self.setObjectName("Row")
        self.setFixedSize(THUMB_SIZE, THUMB_SIZE)
        self.picture = QLabel(self)
        self.picture.setGeometry(0, 0, THUMB_SIZE, THUMB_SIZE)
        shown, self.readable = thumbnail_pixmap(self.path, THUMB_SIZE)
        self.picture.setPixmap(shown)
        name = self.path.name
        self.setToolTip(name if self.readable else f"{name} (no preview)")

        self.remove_button = QPushButton(self)
        self.remove_button.setObjectName("ThumbRemove")
        self.remove_button.setFixedSize(theme.THUMB_REMOVE, theme.THUMB_REMOVE)
        mark = theme.THUMB_REMOVE - 8
        self.remove_button.setIcon(QIcon(icons.pixmap("close", theme.C["TEXT_ON_ACCENT"], mark)))
        self.remove_button.setIconSize(QSize(mark, mark))
        self.remove_button.setToolTip(f"Remove {name}")
        self.remove_button.setAccessibleName(f"Remove {name}")
        inset = theme.PAD_XS
        self.remove_button.move(THUMB_SIZE - theme.THUMB_REMOVE - inset, inset)
        self.remove_button.clicked.connect(lambda: on_remove(self.path))


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
        self.is_video = media.is_video(self.path)

        if self.is_video:
            # Never decoded: Qt's image readers cannot open a video, and
            # QtMultimedia is not in the packaged build. A player-shaped tile
            # with the file's name says what will be uploaded.
            self.readable = True
            shown = video_tile(self.path.name, width, height)
            if extra > 0:
                shown = self._with_overlay(shown, f"+{extra}")
            self.setPixmap(shown)
            return

        pixmap = load_tile(self.path, width, height)
        self.readable = not pixmap.isNull()
        if not self.readable:
            self.setText(f"{self.path.name}\n(no preview)")
            self.setWordWrap(True)
            self.setStyleSheet(
                f"background: {theme.C['FB_GROUP_BG']};"
                f" color: {theme.C['FB_MUTED']};"
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
    """The post as the group will see it, on a computer and on a phone.

    Shaped like a Facebook post and drawn in Facebook's own colours (the
    user's request, 2026-10-22): the group's picture and name, the time, the
    text folded behind "See more" where Facebook folds it, the pictures
    full-bleed in Facebook's layouts, then Like, Comment and Share. A preview
    that looks nothing like the destination cannot answer "will this read well
    when it lands?", which is the only question it exists to answer. The two
    are side by side because a post folds and fits differently on each.

    There is still no direction handling anywhere in here. Qt orders mixed
    Hebrew and English correctly and right-aligns a Hebrew paragraph by itself.
    """

    KINDS = ("computer", "phone")
    CAPTIONS = {"computer": "Computer", "phone": "Phone"}

    def __init__(self) -> None:
        super().__init__()
        # Facebook's grey ground fills the box, the way the feed sets its
        # posts on it.
        self.setObjectName("PreviewCanvas")
        self.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, theme.PAD_M, 0, theme.PAD_M)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.area)
        self.holder = QWidget()
        self.body = QVBoxLayout(self.holder)
        self.body.setContentsMargins(0, 0, 0, 0)
        # Between the note (which version, said above the posts so they carry
        # only what Facebook shows) and the posts.
        self.body.setSpacing(theme.PAD_S)
        self.area.setWidget(self.holder)

        self._body_text = ""
        self._last = ("", "", "", [])
        self._rendered_width = 0
        self._rendered_height = 0
        # The posts opened with "See more", until the text changes.
        self._expanded: set[str] = set()
        # Each post as drawn, and its text, by kind.
        self.posts: dict[str, QFrame] = {}
        self.messages: dict[str, QLabel] = {}
        # The computer post's text, for tests and for the size bump.
        self.message_label: QLabel | None = None
        self.render()

    # -- what the caller asks about -----------------------------------------
    def text_shown(self) -> str:
        """The logical post text, not whatever the labels happen to hold."""
        return self._body_text

    @property
    def tiles(self) -> list[MediaTile]:
        """Every picture tile of the computer post, in order."""
        return self.tiles_of("computer")

    def tiles_of(self, kind: str) -> list[MediaTile]:
        post = self.posts.get(kind)
        return post.findChildren(MediaTile) if post is not None else []

    def post_width(self) -> int:
        """The widest the computer post may be, clamped to something feed-shaped."""
        return self._widths()["computer"]

    def _widths(self) -> dict[str, int]:
        """The widest each post may be: the box, shared in their proportions.

        Recomputed on resize rather than fixed, so the box can be any width
        without a horizontal scrollbar appearing. A post with pictures may be
        drawn narrower, to fit the box's height (`_fit`).
        """
        across = self._across()
        if self._stacked():
            # Each post has the box's width to itself.
            return {
                "computer": max(MIN_POST_WIDTH, min(MAX_POST_WIDTH, across)),
                "phone": max(MIN_PHONE_WIDTH, min(PHONE_WIDTH, across)),
            }
        share = (across - COLUMN_GAP) / (MAX_POST_WIDTH + PHONE_WIDTH)
        return {
            "computer": max(MIN_POST_WIDTH, min(MAX_POST_WIDTH, int(MAX_POST_WIDTH * share))),
            "phone": max(MIN_PHONE_WIDTH, min(PHONE_WIDTH, int(PHONE_WIDTH * share))),
        }

    def _stacked(self) -> bool:
        """The phone post under the computer post, when the box has no room
        for the two side by side: the repeating-post editor's column is too
        narrow, and side by side the phone post was cut off at the edge."""
        smallest = MIN_POST_WIDTH + round(MIN_POST_WIDTH * PHONE_WIDTH / MAX_POST_WIDTH)
        return self._across() < smallest + COLUMN_GAP

    def _across(self) -> int:
        """The width the posts may share: the box less room for a scroll bar,
        whether one shows or not. Measured from the viewport, it shrank when
        a long post's scroll bar appeared, and the phone post was cut off."""
        area = self.area
        inside = area.width() - 2 * area.frameWidth()
        return inside - area.verticalScrollBar().sizeHint().width() - theme.PAD_S

    # -- rendering -----------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().resizeEvent(event)
        # Not while hidden: the next show catches up. Redrawn while Write's
        # columns came back, three times in a row, a post built and cleared
        # before Qt had shown it flashed up as a small window of its own.
        if self.isVisible() and self._stale():
            self._redraw()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt's name
        super().showEvent(event)
        if self._stale():
            self._redraw()

    def _stale(self) -> bool:
        """Whether the box has changed enough since the last drawing to redraw.

        Only when it actually matters: re-laying out the collage on every
        pixel of a window drag would be a lot of scaling for nothing. The
        height matters only to a post with pictures, which is fitted to it.
        """
        taller = abs(self.area.viewport().height() - self._rendered_height)
        return (abs(self.post_width() - self._rendered_width) > RESIZE_SLACK
                or bool(self._last[3]) and taller > RESIZE_SLACK)

    def render(self, heading: str = "", note: str = "", text: str = "",
               media: list[Path] | None = None) -> None:
        """`note` is said above the posts: which version, or nothing."""
        if text != self._last[2]:
            self._expanded = set()
        self._last = (heading, note, text, list(media or []))
        self._redraw()

    def _see_more(self, kind: str) -> None:
        """Open the whole text, as Facebook does. After the click, not in it:
        the label clicked is cleared by the redraw."""
        self._expanded.add(kind)
        QTimer.singleShot(0, self._redraw)

    def _redraw(self) -> None:
        heading, note, text, media = self._last
        clear(self.body)
        self.posts = {}
        self.messages = {}
        self.message_label = None
        self._body_text = ""
        widest = self._widths()
        self._rendered_width = widest["computer"]
        self._rendered_height = self.area.viewport().height()
        # Height the posts cannot have: the note and the captions above them.
        above = 0

        if note:
            label = QLabel(note)
            label.setObjectName("PreviewNote")
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            self.body.addWidget(label)
            label.ensurePolished()
            above += label.heightForWidth(self.area.viewport().width()) + theme.PAD_S

        if not text.strip() and not media:
            empty = QLabel("Nothing to preview yet. Write something on the Write tab.")
            empty.setObjectName("PreviewNote")
            empty.setWordWrap(True)
            empty.setAlignment(Qt.AlignCenter)
            self.body.addWidget(empty)
            self.body.addStretch(1)
            return

        body = text.rstrip()
        self._body_text = body if body.strip() else NO_TEXT
        # Each post is finished at its size before it goes into a layout, so
        # Qt lays the screen out once. Sized afterwards, the posts one above
        # the other overlapped until a second layout pass.
        captions = {}
        for kind in self.KINDS:
            caption = QLabel(self.CAPTIONS[kind])
            caption.setObjectName("PreviewCaption")
            caption.setAlignment(Qt.AlignCenter)
            caption.ensurePolished()
            captions[kind] = caption
            self._post(kind, heading, body, media)
        captioned = captions["computer"].sizeHint().height() + theme.PAD_S
        sizes = self._fit(media, widest, self._rendered_height - above - captioned)
        for kind, (width, squash) in sizes.items():
            post = self.posts[kind]
            post.setFixedWidth(width)
            fit_actions(post, width)
            self._fold(kind, body, width - 2 * post.frameWidth())
            if media:
                # Between the gap under the text and the footer, inside the
                # post's border.
                layout = post.layout()
                inner = width - 2 * post.frameWidth()
                layout.insertWidget(layout.count() - 1, self._collage(kind, media, inner, squash))
            # Its height for its own width, fixed: centred in a column, Qt
            # measured it as wide as the box and drew it a line short, with
            # Like/Comment/Share over the pictures.
            post.setFixedHeight(post.heightForWidth(width))

        stacked = self._stacked()
        line = QVBoxLayout() if stacked else QHBoxLayout()
        line.setContentsMargins(0, 0, 0, 0)
        line.setSpacing(COLUMN_GAP)
        # A stretch on both sides: a feed centres its column.
        if not stacked:
            line.addStretch(1)
        for kind in self.KINDS:
            column = row()
            lines = QVBoxLayout(column)
            lines.setContentsMargins(0, 0, 0, 0)
            lines.setSpacing(theme.PAD_S)
            lines.addWidget(captions[kind])
            lines.addWidget(self.posts[kind])
            if not stacked:
                line.addWidget(column, 0, Qt.AlignTop)
                continue
            # Centred between stretches, not by an alignment flag: aligned,
            # the first layout pass gave each column half the box, and the
            # phone post was drawn over the computer post for a moment.
            centred = row()
            across = QHBoxLayout(centred)
            across.setContentsMargins(0, 0, 0, 0)
            across.addStretch(1)
            across.addWidget(column)
            across.addStretch(1)
            line.addWidget(centred)
        if not stacked:
            line.addStretch(1)
        holder = QWidget()
        # Transparent like row(), so the canvas shows through.
        holder.setObjectName("Row")
        holder.setLayout(line)
        self.body.addWidget(holder)
        self.body.addStretch(1)
        if self.isVisible():
            # Shown and laid out now rather than over the next two passes: the
            # scroll area grows its page only on the second, and until then
            # the phone post one below the computer post was drawn over it.
            for index in range(self.body.count()):
                shown = self.body.itemAt(index).widget()
                if shown is not None:
                    shown.show()
            QCoreApplication.sendPostedEvents(None, QEvent.LayoutRequest)

    def _post(self, kind: str, heading: str, body: str, media: list[Path]) -> QWidget:
        """The post without its pictures, which wait for its width (`_fit`)."""
        post = QFrame()
        post.setObjectName("FbPost")
        # A phone draws its posts edge to edge, with square corners.
        post.setProperty("phone", kind == "phone")
        layout = QVBoxLayout(post)
        # No horizontal padding on the post itself: the pictures run edge to
        # edge the way they do in a feed, so the text gets its own inset.
        layout.setContentsMargins(0, theme.PAD_M, 0, 0)
        layout.setSpacing(0)

        layout.addWidget(self._header(heading))
        layout.addSpacing(theme.PAD_S)
        message = self._message(kind, body, media)
        layout.addWidget(message)
        layout.addSpacing(theme.PAD_M if media else theme.PAD_S)
        layout.addWidget(self._footer(kind))
        self.posts[kind] = post
        if kind == "computer":
            self.message_label = message
        return post

    def _message(self, kind: str, body: str, media: list[Path]) -> QLabel:
        message = QLabel()
        message.setObjectName("FbText")
        message.setWordWrap(True)
        message.setContentsMargins(theme.PAD_M, 0, theme.PAD_M, 0)
        self.messages[kind] = message
        if not body.strip():
            message.setObjectName("FbMuted")
            message.setText(NO_TEXT)
            return message
        # Whole for now: where it folds depends on the post's width, which
        # `_fit` decides, and `_fold` folds it there.
        message.setTextFormat(Qt.PlainText)
        message.setText(body)
        message.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.LinksAccessibleByMouse)
        message.linkActivated.connect(lambda _link, k=kind: self._see_more(k))
        if len(body) <= BIG_TEXT_CHARS and not media and "\n" not in body:
            # A short post with no picture is set large in the feed. Copying it
            # is most of why a preview reads as "a post" rather than "a label".
            message.setStyleSheet(f"font-size: {theme.SIZE_TITLE}px;")
        return message

    def _folds(self, kind: str) -> bool:
        """Whether this post's text may be folded behind "See more"."""
        return kind not in self._expanded and bool(self._last[2].strip())

    def _text_cap(self, kind: str) -> int:
        """How tall the text may be before Facebook folds it: its lines, as
        the folded text sets them."""
        message = self.messages[kind]
        message.ensurePolished()
        kept = message.textFormat(), message.text()
        message.setTextFormat(Qt.RichText)
        message.setText('<p style="margin: 0;">X</p>' * SEE_MORE_LINES[kind])
        cap = message.heightForWidth(MAX_POST_WIDTH)
        message.setTextFormat(kept[0])
        message.setText(kept[1])
        return cap

    def _fold(self, kind: str, body: str, width: int) -> None:
        """Fold the text where Facebook would in a post `width` wide inside its
        border, with "See more" ending the last line.

        Facebook folds by the lines a post takes on the screen, not the lines
        typed: folded at five typed lines, one long line ran to eight in a
        narrow post, and a long post with pictures could not fit the box.
        """
        if not self._folds(kind):
            return
        message = self.messages[kind]
        cap = self._text_cap(kind)
        typed = see_more_cut(body, kind)
        if typed is None and message.heightForWidth(width) <= cap:
            return
        limit = typed or body
        # Never mid-word: only where a space or a line break follows.
        stops = [end for end in range(1, len(limit) + 1)
                 if end == len(limit) or limit[end] in " \n"]
        message.setTextFormat(Qt.RichText)

        def fits(stop: int) -> bool:
            message.setText(folded_html(limit[:stop].rstrip()))
            return message.heightForWidth(width) <= cap

        best, low, high = stops[0], 0, len(stops) - 1
        while low <= high:
            middle = (low + high) // 2
            if fits(stops[middle]):
                best, low = stops[middle], middle + 1
            else:
                high = middle - 1
        message.setText(folded_html(limit[:best].rstrip()))

    def _fit(self, media: list[Path], widest: dict[str, int],
             room: int) -> dict[str, tuple[int, float]]:
        """How wide to draw each post, and how much shorter its pictures, so
        both fit the box without scrolling.

        Reported 2026-10-22: with three pictures the box scrolled, and the
        Like/Comment/Share row was out of sight at the window's usual size.
        Narrowing a post first shrinks its pictures with it, so it gets smaller
        and keeps the shape a feed gives it. The two shrink together, so the
        phone stays as much narrower than the computer as it really is. Only
        once the computer post is down to `FIT_FLOOR` are the pictures made
        shorter than that shape, and no shorter than `MIN_SQUASH` of it. The
        text is measured as `_fold` will leave it. A post opened with "See
        more" that still cannot fit is drawn full width and scrolls: shrinking
        it would only make it harder to read. Any other is drawn as small as
        it may be, so as little as possible scrolls.
        """
        full = {kind: (widest[kind], 1.0) for kind in self.KINDS}
        if not media:
            return full
        for post in self.posts.values():
            post.ensurePolished()
        ratio = PHONE_WIDTH / MAX_POST_WIDTH

        def widths_for(computer: int) -> dict[str, int]:
            return {"computer": computer,
                    "phone": min(widest["phone"], round(computer * ratio))}

        caps = {kind: self._text_cap(kind) for kind in self.KINDS if self._folds(kind)}

        def rest_at(kind: str, width: int) -> int:
            post = self.posts[kind]
            rest = post.heightForWidth(width)
            rest = rest if rest >= 0 else post.sizeHint().height()
            if kind in caps:
                # Less what `_fold` will put behind "See more".
                text = self.messages[kind].heightForWidth(width - 2 * post.frameWidth())
                rest -= max(0, text - caps[kind])
            return rest

        def pictures_at(kind: str, width: int) -> int:
            return self._collage_height(kind, media, width - 2 * self.posts[kind].frameWidth())

        def tall_at(kind: str, width: int) -> int:
            return rest_at(kind, width) + pictures_at(kind, width)

        def fits(computer: int) -> bool:
            return all(tall_at(kind, width) <= room
                       for kind, width in widths_for(computer).items())

        top = widest["computer"]
        floor = min(top, FIT_FLOOR)
        if fits(top):
            return {kind: (width, 1.0) for kind, width in widths_for(top).items()}
        def squashed(computer: int) -> dict[str, tuple[int, float]] | None:
            sizes = {}
            for kind, width in widths_for(computer).items():
                if tall_at(kind, width) <= room:
                    sizes[kind] = (width, 1.0)
                    continue
                pictures = pictures_at(kind, width)
                # Less the gaps between rows, which do not shrink.
                squash = (room - rest_at(kind, width) - 2 * TILE_GAP) / pictures
                if squash < MIN_SQUASH:
                    return None
                sizes[kind] = (width, squash)
            return sizes

        if not fits(floor):
            # In a small window, narrower still, down to the readable floor.
            for narrowest in (floor, min(top, MIN_POST_WIDTH)):
                sizes = squashed(narrowest)
                if sizes is not None:
                    return sizes
            if self._expanded:
                # Opened with "See more": read at full width, and scrolled.
                return full
            # As small as a post may be drawn, its pictures down to
            # `LAST_SQUASH`, so as little as possible is scrolled. Drawn full
            # size instead, a long post with pictures scrolled by half the box
            # (2026-10-22).
            sizes = {}
            for kind, width in widths_for(min(top, MIN_POST_WIDTH)).items():
                needed = (room - rest_at(kind, width) - 2 * TILE_GAP) / pictures_at(kind, width)
                sizes[kind] = (width, min(1.0, max(LAST_SQUASH, needed)))
            return sizes
        fitting, too_wide = floor, top
        while too_wide - fitting > 1:
            middle = (fitting + too_wide) // 2
            if fits(middle):
                fitting = middle
            else:
                too_wide = middle
        return {kind: (width, 1.0) for kind, width in widths_for(fitting).items()}

    def _header(self, heading: str) -> QWidget:
        """The group's picture and name, and the time, as a group post has."""
        holder = row()
        line = QHBoxLayout(holder)
        line.setContentsMargins(theme.PAD_M, 0, theme.PAD_M, 0)
        line.setSpacing(theme.PAD_S)

        picture = QLabel()
        picture.setPixmap(icons.group_picture(AVATAR_SIZE))
        picture.setFixedSize(AVATAR_SIZE, AVATAR_SIZE)
        line.addWidget(picture, 0, Qt.AlignTop)

        names = QVBoxLayout()
        names.setContentsMargins(0, 0, 0, 0)
        names.setSpacing(0)
        # Wraps rather than runs under the "···": a post fitted to a small
        # window is narrow, and a group's name can be long.
        name = QLabel(heading or ALL_GROUPS)
        name.setObjectName("FbName")
        name.setWordWrap(True)
        names.addWidget(name)
        when = QLabel("Just now")
        when.setObjectName("FbMeta")
        names.addWidget(when)
        line.addLayout(names, 1)

        more = QLabel("···")
        more.setObjectName("FbMore")
        line.addWidget(more, 0, Qt.AlignTop)
        return holder

    def _collage(self, kind: str, media: list[Path], width: int,
                 squash: float = 1.0) -> QWidget:
        """Facebook's layouts for 1 to 5 pictures, more behind a "+N"."""
        holder = row()
        shown = media[:MAX_TILES]
        if len(shown) == 1:
            height = self._single_height(kind, shown[0], width, squash)
            holder.setFixedSize(width, height)
            MediaTile(shown[0], width, height).setParent(holder)
            return holder

        extra = len(media) - len(shown)
        tall, places = collage_layout(len(shown), shape_of(shown[0]))
        # Each picture owns its share of the collage plus one gap, so the gap
        # falls between neighbours and never along the post's edge. A size
        # comes from its share alone, so equal shares are equal to the pixel.
        across = width + TILE_GAP
        down = round(tall * width * squash) + TILE_GAP
        bottom = 0
        for index, (path, (x, y, wide, high)) in enumerate(zip(shown, places)):
            more = extra if index == len(shown) - 1 else 0
            tile = MediaTile(
                path,
                round(wide * across) - TILE_GAP,
                round(high / tall * down) - TILE_GAP,
                extra=more,
            )
            tile.setParent(holder)
            tile.move(round(x * across), round(y / tall * down))
            bottom = max(bottom, tile.y() + tile.height())
        holder.setFixedSize(width, bottom)
        return holder

    def _collage_height(self, kind: str, media: list[Path], width: int) -> int:
        """How tall `_collage` draws these pictures for a post `width` wide."""
        shown = media[:MAX_TILES]
        if len(shown) == 1:
            return self._single_height(kind, shown[0], width)
        tall, _places = collage_layout(len(shown), shape_of(shown[0]))
        return round(tall * width)

    @staticmethod
    def _single_height(kind: str, path: Path, width: int, squash: float = 1.0) -> int:
        """One picture keeps its own shape, within reason.

        A single photo is shown at its natural aspect ratio (cropping the only
        picture in a post to a fixed box would misrepresent it) but a very tall
        one is cut where Facebook cuts it. A video is drawn widescreen.
        """
        low, high = MIN_SINGLE_SHAPE * width, MAX_SINGLE_SHAPE[kind] * width
        if media.is_video(path):
            natural = width * 9 / 16
        else:
            size = image_size(path)
            natural = width / 2 if size is None else width * size.height() / size.width()
        return int(max(low, min(high, natural)) * squash)

    def _footer(self, kind: str) -> QWidget:
        """Like, Comment and Share. Structural chrome, and deliberately inert.

        No counts: inventing "12 likes" on a post that has not been published
        would be showing the user something untrue.
        """
        holder = row()
        outer = QVBoxLayout(holder)
        outer.setContentsMargins(theme.PAD_M, theme.PAD_S, theme.PAD_M, theme.PAD_S)
        outer.setSpacing(theme.PAD_S)

        divider = QFrame()
        divider.setObjectName("FbDivider")
        divider.setFixedHeight(1)
        outer.addWidget(divider)

        # Spread across the row, as Facebook spreads them, rather than a third
        # each: in a narrow phone post a third was too small for "Comment".
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(0)
        actions.addStretch(1)
        for name, icon in (("Like", "like"), ("Comment", "comment"), ("Share", "share")):
            glyph = QLabel()
            glyph.setPixmap(icons.pixmap(icon, theme.C["FB_MUTED"], FB_ICON_SIZE))
            actions.addWidget(glyph)
            actions.addSpacing(FB_ICON_GAP)
            label = QLabel(name)
            label.setObjectName("FbAction")
            # The phone app sets them smaller than a computer does.
            label.setProperty("phone", kind == "phone")
            actions.addWidget(label)
            actions.addStretch(1)
        outer.addLayout(actions)
        return holder


FB_ICON_SIZE = 18
FB_ICON_GAP = 6


def fit_actions(post: QWidget, width: int) -> None:
    """Like, Comment and Share as icons alone when their words do not fit the
    post's width: cut short, "Comment" read as "Comm"."""
    labels = post.findChildren(QLabel, "FbAction")
    for label in labels:
        label.ensurePolished()
    needed = sum(label.sizeHint().width() + FB_ICON_SIZE + FB_ICON_GAP for label in labels)
    for label in labels:
        label.setVisible(needed <= width - 2 * theme.PAD_M)


def folded_html(shown: str) -> str:
    """The text Facebook shows before "See more", and the link that opens it.

    One paragraph per line, so each finds its own direction as the plain text
    does: a Hebrew line still sits on the right with no direction code.
    """
    lines = [html.escape(line) for line in shown.split("\n")]
    link = (
        f'<a href="more" style="color: {theme.C["FB_TEXT"]}; '
        f'text-decoration: none; font-weight: 600;">{SEE_MORE.replace(" ", "&nbsp;")}</a>'
    )
    lines[-1] += f"… {link}"
    return "".join(
        f'<p style="margin: 0; white-space: pre-wrap;">{line or "&nbsp;"}</p>'
        for line in lines
    )
