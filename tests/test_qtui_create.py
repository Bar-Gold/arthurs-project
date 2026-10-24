"""Create a post: the three steps as one road, in order (2026-10-19).

Compose, Groups and Publish are the same views as before, held as the pages of
one sidebar item. An earlier step can be clicked to go back; a later one only
through Next. A post that went out or was queued starts a fresh one and lands
on Home; a refused one keeps everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QPushButton

from fbposter.qtui import theme
from fbposter.qtui.app import FLOW_STEPS
from fbposter.qtui.views.create import STEP_KEYS
from fbposter.qtui.views.publish import NOW, ONCE, REPEAT

BODY = "Selling a road bike, 54cm frame."


@pytest.fixture
def flow(qt_app):
    for index in range(2):
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/demo{index}", name=f"Demo {index}"
        )
    qt_app.show_view("compose")
    return qt_app.views["create"]


def write(qt_app, text=BODY):
    compose = qt_app.views["compose"]
    compose._show(text)
    compose.capture()


def to_step_3(qt_app, text=BODY):
    """Write, pick every group, and arrive at step 3 the way a user does."""
    write(qt_app, text)
    qt_app.views["compose"].go_to_groups()
    qt_app.views["groups"].set_all(True)
    qt_app.views["groups"].next_button.click()


class TestTheSidebar:
    def test_one_item_for_the_three_steps(self, qt_app):
        assert qt_app.nav_buttons["create"].text() == "Create a post"
        for key in FLOW_STEPS:
            assert key not in qt_app.nav_buttons

    def test_every_step_lights_create_a_post(self, qt_app):
        for key in FLOW_STEPS:
            qt_app.show_view(key)
            assert qt_app.nav_buttons["create"].isChecked(), key
            assert qt_app.current_view == key

    def test_the_item_returns_to_the_step_you_were_at(self, qt_app, flow):
        to_step_3(qt_app)
        qt_app.show_view("dashboard")
        qt_app.nav_buttons["create"].click()
        assert qt_app.current_view == "publish"


class TestTheStepBar:
    def test_the_order(self, flow):
        assert STEP_KEYS == FLOW_STEPS
        texts = [flow.step_buttons[key].text() for key in STEP_KEYS]
        assert texts == ["1. Write the post", "2. Choose the groups", "3. Choose when"]

    def test_the_step_you_are_on_is_the_one_marked(self, qt_app, flow):
        qt_app.show_view("groups")
        checked = [key for key, button in flow.step_buttons.items() if button.isChecked()]
        assert checked == ["groups"]

    def test_a_later_step_cannot_be_clicked(self, flow):
        assert not flow.step_buttons["groups"].isEnabled()
        assert not flow.step_buttons["publish"].isEnabled()

    def test_an_earlier_step_can(self, qt_app, flow):
        to_step_3(qt_app)
        assert flow.step_buttons["compose"].isEnabled()
        flow.step_buttons["compose"].click()
        assert qt_app.current_view == "compose"
        # Going back does not let you skip ahead again.
        assert not flow.step_buttons["publish"].isEnabled()

    def test_forward_is_by_next(self, qt_app, flow):
        write(qt_app)
        qt_app.views["compose"].next_button.click()
        assert qt_app.current_view == "groups"

    def test_next_on_compose_needs_a_post(self, qt_app, flow):
        """An empty post went on to step 2 (the user's report, 2026-10-21)."""
        compose = qt_app.views["compose"]
        assert not compose.next_button.isEnabled()
        compose.next_button.click()
        assert qt_app.current_view == "compose"

    def test_typing_lets_you_on_and_clearing_greys_it_again(self, qt_app, flow):
        compose = qt_app.views["compose"]
        compose.editor.setPlainText(BODY)
        assert compose.next_button.isEnabled()
        compose.editor.setPlainText("  \n ")
        assert not compose.next_button.isEnabled()

    def test_a_picture_alone_is_a_post(self, qt_app, flow):
        compose = qt_app.views["compose"]
        compose.attachments = [Path("C:/pictures/bike.jpg")]
        compose._render_attachments()
        assert compose.next_button.isEnabled()
        compose._remove_attachment(Path("C:/pictures/bike.jpg"))
        assert not compose.next_button.isEnabled()

    def test_words_in_another_version_are_a_post(self, qt_app, flow):
        compose = qt_app.views["compose"]
        compose.add_version()
        compose.editor.setPlainText(BODY)
        compose.select_version(0)
        assert compose.next_button.isEnabled()

    def test_a_fresh_post_greys_it_again(self, qt_app, flow):
        compose = qt_app.views["compose"]
        compose.editor.setPlainText(BODY)
        flow.clear()
        assert not compose.next_button.isEnabled()

    def test_next_on_groups_needs_a_group(self, qt_app, flow):
        qt_app.show_view("groups")
        assert not qt_app.views["groups"].next_button.isEnabled()

    def test_a_click_on_the_current_step_leaves_it_marked(self, qt_app, flow):
        flow.step_buttons["compose"].click()
        assert flow.step_buttons["compose"].isChecked()

    def test_the_steps_own_titles_are_hidden(self, flow):
        for page in (flow.compose, flow.groups, flow.publish):
            assert page.heading.isHidden()

    def test_one_filled_button_per_step(self, qt_app, flow):
        for key in FLOW_STEPS:
            qt_app.show_view(key)
            page = qt_app.views[key]
            filled = [
                b for b in page.findChildren(QPushButton)
                if b.objectName() == "Primary" and b.isVisibleTo(page)
            ]
            assert len(filled) == 1, key


def laid_out(qt_app) -> None:
    """Lay the window out at its usual size without showing it."""
    qt_app.resize(*theme.WINDOW_DEFAULT)
    qt_app.grab()


def right_edge(widget, page) -> int:
    return widget.mapTo(page, QPoint(widget.width(), 0)).x()


class TestNothingMovesUnderThePointer:
    """Polished on 2026-10-21, each the user's choice."""

    def test_write_and_preview_stay_where_they_were_clicked(self, qt_app, flow):
        """At the right, the toggle jumped across the window when Preview
        hid the right column."""
        compose = qt_app.views["compose"]
        write(qt_app)
        toggle = compose.write_button.parentWidget()
        laid_out(qt_app)
        before = toggle.mapTo(qt_app, QPoint(0, 0))
        compose.preview_button.click()
        laid_out(qt_app)
        assert toggle.mapTo(qt_app, QPoint(0, 0)) == before

    def test_each_step_reaches_the_same_right_edge(self, qt_app, flow):
        """Qt's default margins inset the right column of steps 1 and 3, so
        their cards stopped short of the edge step 2 reaches."""
        compose = qt_app.views["compose"]
        write(qt_app)
        laid_out(qt_app)
        assert right_edge(compose.next_button, compose) == compose.width()
        to_step_3(qt_app)
        publish = qt_app.views["publish"]
        laid_out(qt_app)
        assert right_edge(publish.go_button, publish) == publish.width()

    def test_an_empty_box_says_what_to_do(self, qt_app, flow):
        assert qt_app.views["compose"].editor.placeholderText() == "Write your post here"


class TestAfterAPost:
    def assert_fresh(self, qt_app):
        compose = qt_app.views["compose"]
        publish = qt_app.views["publish"]
        assert qt_app.current_view == "home"
        assert compose.base_body() == ""
        assert compose.attachments == []
        assert qt_app.selected_groups == set()
        assert qt_app.selected_sets == set()
        assert publish.mode == NOW
        assert compose.version_count() == 1
        assert publish.times() == ["09:00"]
        assert len(publish.days()) == 7
        assert qt_app.views["create"].current_key == "compose"

    def test_post_now(self, qt_app, flow):
        to_step_3(qt_app)
        assert qt_app.views["publish"].publish() is True
        assert len(qt_app.task_repo.list_recent()) == 1
        self.assert_fresh(qt_app)

    def test_once(self, qt_app, flow):
        to_step_3(qt_app)
        publish = qt_app.views["publish"]
        publish.set_mode(ONCE)
        publish.schedule_entry.setDateTime(publish.schedule_entry.dateTime().addDays(1))
        assert publish.publish() is True
        self.assert_fresh(qt_app)

    def test_repeat(self, qt_app, flow):
        write(qt_app)
        qt_app.views["compose"].add_version()
        to_step_3(qt_app, "Another way of saying it")
        publish = qt_app.views["publish"]
        publish.set_mode(REPEAT)
        publish.add_time("18:00")
        publish.name_entry.setText("Bikes")
        assert publish.publish() is True
        assert len(qt_app.schedule_repo.list()) == 1
        self.assert_fresh(qt_app)
        assert publish.name_entry.text() == ""

    def test_pictures_go_too(self, qt_app, flow):
        write(qt_app, "")
        qt_app.views["compose"].attachments = [Path("C:/pictures/bike.jpg")]
        qt_app.views["compose"].go_to_groups()
        qt_app.views["groups"].set_all(True)
        qt_app.views["groups"].next_button.click()
        assert qt_app.views["publish"].publish() is True
        self.assert_fresh(qt_app)

    def test_the_message_stays_on_screen(self, qt_app, flow):
        to_step_3(qt_app)
        qt_app.views["publish"].publish()
        assert qt_app.toast_label.text() == "Queued for 2 groups."

    def test_a_refused_post_keeps_everything(self, qt_app, flow):
        """No groups: nothing is queued and nothing is cleared."""
        write(qt_app)
        qt_app.show_view("publish")
        assert qt_app.views["publish"].publish() is False
        assert qt_app.current_view == "publish"
        assert qt_app.views["compose"].base_body() == BODY
