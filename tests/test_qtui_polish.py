"""Visual rules that were broken in shipped code, and now cannot be again.

Every test here corresponds to something that was actually wrong on screen, not
to a preference. Two of them guard bugs that were invisible in the test suite
and obvious the moment the window was rendered.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

QTUI = Path(__file__).resolve().parent.parent / "fbposter" / "qtui"


class TestClearingALayoutReallyRemovesTheWidgets:
    """`takeAt()` does not unparent. A widget taken out of a layout stays a
    child of the same parent, goes on painting, and -- no longer managed by any
    layout -- reverts to Qt's default 640x480.

    That is what put a blue band across the Compose screen: the stale "All
    groups" tab was checked, so it was filled with the accent colour, and it
    painted 640x480 behind the tab strip. It looked so deliberate that it read
    as a design choice.
    """

    def layout_with_buttons(self, qt_application, count=3):
        from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

        holder = QWidget()
        layout = QHBoxLayout(holder)
        for index in range(count):
            layout.addWidget(QPushButton(f"button {index}"))
        return holder, layout

    def test_no_widget_is_left_parented(self, qt_application):
        from PySide6.QtWidgets import QPushButton

        from fbposter.qtui.widgets import clear

        holder, layout = self.layout_with_buttons(qt_application)
        assert len(holder.findChildren(QPushButton)) == 3

        clear(layout)
        assert holder.findChildren(QPushButton) == [], (
            "widgets survive as children and go on painting at 640x480"
        )

    def test_the_layout_is_empty(self, qt_application):
        from fbposter.qtui.widgets import clear

        _holder, layout = self.layout_with_buttons(qt_application)
        clear(layout)
        assert layout.count() == 0

    def test_nested_layouts_go_too(self, qt_application):
        from PySide6.QtWidgets import QLabel, QVBoxLayout

        from fbposter.qtui.widgets import clear

        holder, layout = self.layout_with_buttons(qt_application, count=1)
        inner = QVBoxLayout()
        inner.addWidget(QLabel("nested"))
        layout.addLayout(inner)

        clear(layout)
        assert holder.findChildren(QLabel) == []

    def test_clearing_an_empty_layout_is_harmless(self, qt_application):
        from PySide6.QtWidgets import QHBoxLayout

        from fbposter.qtui.widgets import clear

        clear(QHBoxLayout())

    def test_every_view_clears_through_the_helper(self):
        """The fix was learned once, for the preview collage, and then not
        applied to the five other places doing the same thing. A grep is what
        stops the sixth from being written."""
        offenders = []
        for path in sorted((QTUI / "views").glob("*.py")) + [QTUI / "widgets.py"]:
            source = path.read_text(encoding="utf-8")
            if "takeAt" in source and path.name != "widgets.py":
                offenders.append(path.name)
        assert offenders == [], (
            f"{offenders} clear a layout by hand; use widgets.clear() instead"
        )


class TestOneAccentButtonPerScreen:
    """The accent fill means "this is the next thing to do". Compose and Groups
    each carried three of them -- the step button, a secondary action beside it,
    and "Check connection" in the sidebar on every single screen -- so it meant
    nothing on any of them."""

    def primaries(self, widget) -> list[str]:
        from PySide6.QtWidgets import QPushButton

        return [
            button.text()
            for button in widget.findChildren(QPushButton)
            if button.objectName() == "Primary"
        ]

    @pytest.mark.parametrize("key", ["compose", "groups", "publish", "queue"])
    def test_at_most_one(self, qt_app, key):
        found = self.primaries(qt_app.views[key])
        assert len(found) <= 1, f"{key} has several filled buttons: {found}"

    @pytest.mark.parametrize("key", ["compose", "groups", "publish"])
    def test_each_step_has_one(self, qt_app, key):
        """A numbered step always offers the way onward."""
        assert len(self.primaries(qt_app.views[key])) == 1

    def test_the_connection_button_is_not_one(self, qt_app):
        """It sits on all four screens. Filled, it outranked whichever step the
        user was actually on."""
        assert qt_app.check_button.objectName() != "Primary"


class TestNothingInACardPaintsTheWindowColour:
    """The global rule gives every widget the window background, so an
    unstyled container inside a card draws a grey slab across it."""

    def test_the_transparent_list_covers_the_containers_used_in_cards(self):
        from fbposter.qtui import theme

        sheet = theme.stylesheet()
        transparent = [
            line for line in sheet.splitlines() if "background: transparent" in line
        ]
        joined = " ".join(transparent)
        for widget in ("QLabel", "QWidget#Row", "QStackedWidget"):
            assert widget in joined, f"{widget} will tint any card it sits in"


class TestTheCheckboxLooksLikeOneControl:
    """Unstyled, checked drew a bare grey tick with no box and unchecked drew
    an empty box -- so on the one screen whose job is picking groups, the
    picked ones were the fainter of the two."""

    def test_the_indicator_is_styled_in_both_states(self):
        from fbposter.qtui import theme

        sheet = theme.stylesheet()
        assert "QCheckBox::indicator" in sheet
        assert "QCheckBox::indicator:checked" in sheet

    def test_checked_is_the_accent(self):
        from fbposter.qtui import theme

        sheet = theme.stylesheet()
        checked = sheet.split("QCheckBox::indicator:checked")[1][:200]
        assert theme.C["ACCENT"] in checked

    def test_the_tick_exists_on_disk(self):
        """The stylesheet names a file. A missing one degrades to a plain
        accent square rather than breaking, but it should be there."""
        from fbposter.qtui import theme

        assert Path(theme.CHECK_ICON).is_file()

    def test_the_path_has_no_backslashes(self):
        """A backslash is an escape character inside a Qt stylesheet, so a
        native Windows path loads nothing at all -- silently."""
        from fbposter.qtui import theme

        assert "\\" not in theme.CHECK_ICON


class TestTheActionSitsWithWhatItActsOn:
    def test_publish_does_not_strand_its_button_at_the_bottom(self):
        """The stretch used to come before the summary and the Post button,
        which in Now and Once modes -- a one-line panel -- left a void most of
        the screen tall between the choice and the button carrying it out."""
        from fbposter.qtui.views import publish

        source = inspect.getsource(publish.PublishView._build_right)
        assert source.rindex("addStretch") > source.rindex("go_button"), (
            "the slack is above the action again"
        )


class TestPreviewGetsTheWholeWindow:
    """The preview answers one question -- "will this read well when it
    lands?" -- and it was answering it in half a screen: pinned into the left
    column with the writing rail holding a third of the window beside it, and
    the attachment list underneath repeating pictures the post already showed.
    """

    def test_write_mode_shows_the_writing_tools(self, qt_app):
        view = qt_app.views["compose"]
        view.set_mode("write")
        assert view.sidebar_holder.isVisibleTo(view)
        assert view.attach_holder.isVisibleTo(view)

    def test_preview_mode_stands_them_down(self, qt_app):
        view = qt_app.views["compose"]
        view.set_mode("preview")
        assert not view.sidebar_holder.isVisibleTo(view)
        assert not view.attach_holder.isVisibleTo(view)

    def test_going_back_restores_them(self, qt_app):
        view = qt_app.views["compose"]
        view.set_mode("preview")
        view.set_mode("write")
        assert view.sidebar_holder.isVisibleTo(view)
        assert view.attach_holder.isVisibleTo(view)

    def test_the_post_is_centred_not_pinned_left(self):
        """A feed centres its column. Against the left edge of a wide pane the
        post reads as a stray panel rather than as a post."""
        import inspect

        from fbposter.qtui.views import compose

        source = inspect.getsource(compose.PostPreview._redraw)
        block = source.split("row = QHBoxLayout()")[1]
        before, _, after = block.partition("addWidget")
        assert "addStretch" in before, "no stretch before the post"
        assert "addStretch" in after, "no stretch after the post"

    def test_the_post_still_keeps_a_feed_shaped_width(self):
        """Full width for the canvas, not for the post: stretched across the
        whole window it stops looking like a post at all."""
        from fbposter.qtui.views import compose

        assert compose.MAX_POST_WIDTH <= 640
        assert compose.MIN_POST_WIDTH < compose.MAX_POST_WIDTH

    def test_switching_modes_does_not_lose_the_text(self, qt_app):
        """set_mode captures on the way into preview. Losing a keystroke here
        would be worse than any layout problem."""
        view = qt_app.views["compose"]
        view.editor.setPlainText("a post worth keeping")
        view.set_mode("preview")
        view.set_mode("write")
        assert view.get_text() == "a post worth keeping"
        assert view.base_body() == "a post worth keeping"
