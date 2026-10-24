"""The Create a post polish pass (2026-10-24), each change the user's choice.

Step 1 has no right-hand column: the templates are two buttons on the toggle's
row, and saving asks for a name in their place; the pictures are thumbnails
inside the box; "Remove this version" is on the versions' row.
Step 2 veils "Remove" until the pointer is on the row, and tints a ticked row.
Step 3 has its summary inside the When card, Back at the bottom left, days
written "Sat 24 Oct 2026", and no Remove on a lone time.
On a big screen the road stops at MAX_ROAD_WIDTH, centred.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from PySide6.QtCore import QEvent, QLocale, QPoint, QPointF
from PySide6.QtGui import QColor, QEnterEvent, QImage
from PySide6.QtWidgets import QApplication, QFrame, QPushButton

from fbposter.qtui import theme
from fbposter.qtui.views.compose import NO_TEMPLATES, Thumbnail
from fbposter.qtui.views.create import MAX_ROAD_WIDTH
from fbposter.qtui.views.publish import NOW, ONCE, REPEAT
from fbposter.qtui.widgets import row_tint

BODY = "Selling a road bike, 54cm frame."
DAY = r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2} (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4}"


@pytest.fixture
def groups(qt_app):
    return [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/polish{index}", name=f"Group {index}"
        )
        for index in range(3)
    ]


@pytest.fixture
def compose(qt_app):
    qt_app.show_view("compose")
    return qt_app.views["compose"]


@pytest.fixture
def groups_view(qt_app, groups):
    qt_app.show_view("groups")
    return qt_app.views["groups"]


@pytest.fixture
def publish(qt_app, groups):
    compose = qt_app.views["compose"]
    compose._show(BODY)
    compose.capture()
    for group in groups:
        qt_app.set_group_selected(group.id, True)
    qt_app.show_view("publish")
    return qt_app.views["publish"]


def laid_out(qt_app, size=theme.WINDOW_DEFAULT) -> None:
    """Lay the window out at a size without showing it."""
    qt_app.resize(*size)
    qt_app.grab()


def picture(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(QColor("#4A6E96"))
    assert image.save(str(path))
    return path


class TestStepOneHasNoEmptyColumn:
    def test_the_box_reaches_the_right_edge(self, qt_app, compose):
        """The templates' column stood mostly empty once "Sending to" went."""
        laid_out(qt_app)
        box = compose.editor_layout.parentWidget()
        assert box.mapTo(compose, QPoint(box.width(), 0)).x() == compose.width()

    def test_the_templates_sit_on_the_toggles_row(self, qt_app, compose):
        laid_out(qt_app)
        toggle = compose.write_button.parentWidget()
        tools = compose.template_tools
        toggle_middle = toggle.mapTo(compose, QPoint(0, toggle.height() // 2)).y()
        tools_middle = tools.mapTo(compose, QPoint(0, tools.height() // 2)).y()
        assert abs(toggle_middle - tools_middle) <= 2


class TestTemplates:
    def test_saving_asks_for_a_name_where_the_buttons_were(self, compose):
        compose.start_saving_template()
        assert compose.template_name.isVisibleTo(compose)
        assert not compose.load_template_button.isVisibleTo(compose)
        assert not compose.save_template_button.isVisibleTo(compose)

    def test_cancel_puts_the_buttons_back(self, compose):
        compose.start_saving_template()
        compose.template_name.setText("Half typed")
        compose.cancel_template_button.click()
        assert compose.template_name.text() == ""
        assert not compose.template_name.isVisibleTo(compose)
        assert compose.save_template_button.isVisibleTo(compose)

    def test_enter_saves_and_it_is_offered(self, compose):
        compose._show(BODY)
        compose.capture()
        compose.start_saving_template()
        compose.template_name.setText("Bike")
        compose.template_name.returnPressed.emit()
        assert compose.template_names() == ["Bike"]
        assert compose.save_template_button.isVisibleTo(compose)

    def test_a_name_is_still_asked_for(self, qt_app, compose):
        compose._show(BODY)
        compose.capture()
        compose.start_saving_template()
        assert compose.save_template() is False
        assert compose.template_name.isVisibleTo(compose)

    def test_none_saved_says_so(self, compose):
        compose.refresh_templates()
        (only,) = compose.template_menu.actions()
        assert only.text() == NO_TEMPLATES
        assert not only.isEnabled()

    def test_choosing_one_loads_it(self, qt_app, compose):
        qt_app.template_repo.save("Bike", BODY)
        compose.refresh_templates()
        (action,) = [a for a in compose.template_menu.actions() if a.isEnabled()]
        action.trigger()
        assert compose.base_body() == BODY

    def test_an_ampersand_in_a_name_is_shown_and_loads(self, qt_app, compose):
        """Qt reads "&" in a menu item as a shortcut marker (widgets.literal)."""
        qt_app.template_repo.save("Buy & sell", BODY)
        compose.refresh_templates()
        (action,) = [a for a in compose.template_menu.actions() if a.isEnabled()]
        assert action.text() == "Buy && sell"
        assert compose.template_names() == ["Buy & sell"]
        action.trigger()
        assert compose.base_body() == BODY

    def test_preview_stands_them_down_and_keeps_their_room(self, qt_app, compose):
        """The toggle beside them must not move when they go."""
        compose.set_mode("preview")
        assert not compose.template_tools.isVisibleTo(compose)
        assert compose.template_tools.sizePolicy().retainSizeWhenHidden()


class TestThumbnails:
    def test_one_per_attachment_inside_the_box(self, qt_app, compose, tmp_path):
        first, second = picture(tmp_path, "a.png"), picture(tmp_path, "b.png")
        compose.add_files([str(first), str(second)])
        shown = compose.findChildren(Thumbnail)
        assert [t.path for t in shown] == [first, second]
        assert all(t.readable for t in shown)
        assert compose.thumb_strip.isVisibleTo(compose)
        box = compose.editor_layout.parentWidget()
        assert box.isAncestorOf(compose.thumb_strip)

    def test_none_attached_shows_no_strip_and_no_words(self, compose):
        """"No pictures or videos attached." went: the button says it."""
        assert not compose.thumb_strip.isVisibleTo(compose)
        assert compose.findChildren(Thumbnail) == []

    def test_its_button_takes_it_off(self, qt_app, compose, tmp_path):
        first, second = picture(tmp_path, "a.png"), picture(tmp_path, "b.png")
        compose.add_files([str(first), str(second)])
        (thumb,) = [t for t in compose.findChildren(Thumbnail) if t.path == first]
        assert thumb.remove_button.accessibleName() == "Remove a.png"
        thumb.remove_button.click()
        assert compose.attachments == [second]
        assert [t.path for t in compose.findChildren(Thumbnail)] == [second]

    def test_the_last_one_off_hides_the_strip(self, qt_app, compose, tmp_path):
        only = picture(tmp_path, "a.png")
        compose.add_files([str(only)])
        compose.findChildren(Thumbnail)[0].remove_button.click()
        assert not compose.thumb_strip.isVisibleTo(compose)

    def test_a_picture_that_will_not_open_still_shows(self, compose):
        """It is still going to be uploaded."""
        missing = Path("C:/nowhere/gone.jpg")
        compose.attachments = [missing]
        compose._render_attachments()
        (thumb,) = compose.findChildren(Thumbnail)
        assert not thumb.readable
        assert thumb.toolTip() == "gone.jpg (no preview)"

    def test_preview_stands_the_strip_down(self, qt_app, compose, tmp_path):
        compose.add_files([str(picture(tmp_path, "a.png"))])
        compose.set_mode("preview")
        assert not compose.thumb_strip.isVisibleTo(compose)
        compose.set_mode("write")
        assert compose.thumb_strip.isVisibleTo(compose)


class TestRemovingAVersion:
    def test_it_is_on_the_versions_row(self, compose):
        compose.add_version()
        assert compose.remove_version_button.isVisibleTo(compose)
        assert not compose.editor_footer.isAncestorOf(compose.remove_version_button)

    def test_preview_stands_it_down(self, compose):
        compose._show(BODY)
        compose.capture()
        compose.add_version()
        compose.set_mode("preview")
        assert not compose.remove_version_button.isVisibleTo(compose)
        compose.set_mode("write")
        assert compose.remove_version_button.isVisibleTo(compose)


def enter(widget) -> None:
    point = QPointF(5, 5)
    QApplication.sendEvent(widget, QEnterEvent(point, point, point))


def leave(widget) -> None:
    QApplication.sendEvent(widget, QEvent(QEvent.Leave))


class TestStepTwoRows:
    def remove_of(self, row) -> QPushButton:
        (button,) = [b for b in row.findChildren(QPushButton) if b.text() == "Remove"]
        return button

    def test_remove_is_veiled_until_the_pointer_is_on_the_row(self, groups_view, groups):
        row = groups_view._rows[groups[0].id]
        remove = self.remove_of(row)
        assert remove.property("veiled") is True
        enter(row)
        assert remove.property("veiled") is False
        leave(row)
        assert remove.property("veiled") is True

    def test_remove_keeps_its_room_and_tab_reaches_it(self, groups_view, groups):
        """Veiled, not hidden: nothing shifts when it shows, and the keyboard
        still gets to it (theme.py shows it in focus)."""
        remove = self.remove_of(groups_view._rows[groups[0].id])
        assert not remove.isHidden()
        assert remove.accessibleName() == "Remove Group 0"
        assert '[veiled="true"]:focus' in theme.stylesheet()

    def test_a_ticked_row_is_tinted(self, groups_view, groups):
        row = groups_view._rows[groups[0].id]
        assert row_tint(row) is None
        groups_view._checkboxes[groups[0].id].setChecked(True)
        assert row_tint(row) == "ROW_PICKED"
        enter(row)
        assert row_tint(row) == "NAV_ACTIVE_BG"
        groups_view._checkboxes[groups[0].id].setChecked(False)
        assert row_tint(row) == "FIELD_HOVER"

    def test_select_all_tints_every_row(self, groups_view, groups):
        groups_view.set_all(True)
        assert {row_tint(r) for r in groups_view._rows.values()} == {"ROW_PICKED"}

    def test_a_row_that_ticks_nothing_is_never_tinted(self):
        assert row_tint(QFrame()) is None

    def test_the_tint_is_painted_inside_the_rounded_box(
        self, qt_application, qt_app, groups_view, groups
    ):
        """Painted by the list, clipped to its corners, under the rows."""
        groups_view._checkboxes[groups[0].id].setChecked(True)
        qt_app.resize(1000, 700)
        qt_app.show()
        qt_application.processEvents()
        try:
            listed = groups_view.group_list
            row = groups_view._rows[groups[0].id]
            image = listed.grab().toImage()
            # The middle of the row, clear of its name and its buttons.
            inside = row.geometry().center()
            assert image.pixelColor(inside) == QColor(theme.C["ROW_PICKED"])
            # The corner, outside the rounded border, is not tinted.
            assert image.pixelColor(QPoint(0, 0)) != QColor(theme.C["ROW_PICKED"])
        finally:
            qt_app.hide()


class TestStepThree:
    def test_the_summary_is_inside_the_when_card(self, publish):
        card = publish.summary.parentWidget()
        while card.objectName() != "Card":
            card = card.parentWidget()
        assert card.isAncestorOf(publish.mode_buttons[ONCE])
        assert not card.isAncestorOf(publish.go_button)

    def test_back_is_at_the_bottom_left_like_step_two(self, qt_app, publish):
        laid_out(qt_app)
        backs = {}
        for key in ("groups", "publish"):
            view = qt_app.views[key]
            qt_app.show_view(key)
            laid_out(qt_app)
            (back,) = [b for b in view.findChildren(QPushButton) if b.objectName() == "BackLink"]
            corner = back.mapTo(view, QPoint(0, back.height()))
            backs[key] = (corner.x(), view.height() - corner.y())
        assert backs["publish"] == backs["groups"]
        assert backs["publish"][0] == 0

    def test_the_day_reads_as_people_say_it(self, publish):
        publish.set_mode(ONCE)
        entry = publish.schedule_entry.date_entry
        assert re.fullmatch(DAY, entry.text()), entry.text()

    def test_it_is_in_english_whatever_the_machine(self, publish):
        entry = publish.schedule_entry.date_entry
        assert entry.locale().language() == QLocale.English

    def test_the_first_run_reads_the_same_way(self, publish):
        publish.set_mode(REPEAT)
        assert re.search(f"First run {DAY}, \\d\\d:\\d\\d", publish.summary.text())

    def test_a_lone_time_has_no_remove(self, publish):
        publish.set_mode(REPEAT)
        (remove,) = publish._time_removes.values()
        assert remove.isHidden()

    def test_two_times_each_have_one(self, publish):
        publish.add_time("18:00")
        assert len(publish._time_removes) == 2
        assert not any(r.isHidden() for r in publish._time_removes.values())

    def test_back_to_one_hides_it_again(self, publish):
        publish.add_time("18:00")
        entry = publish._time_rows[-1]
        publish._remove_time(entry.parentWidget(), entry)
        (remove,) = publish._time_removes.values()
        assert remove.isHidden()

    def test_a_fresh_post_starts_with_one_time_and_no_remove(self, publish):
        publish.add_time("18:00")
        publish.reset_form()
        (remove,) = publish._time_removes.values()
        assert remove.isHidden()
        assert publish.mode == NOW


class TestTheRoadOnABigScreen:
    def test_it_stops_at_its_widest_and_is_centred(self, qt_app, compose):
        laid_out(qt_app, (2400, 900))
        flow = qt_app.views["create"]
        pages = flow.pages
        assert pages.width() <= MAX_ROAD_WIDTH
        left = pages.mapTo(flow, QPoint(0, 0)).x()
        right = flow.width() - (left + pages.width())
        assert abs(left - right) <= 1

    def test_a_laptop_window_is_not_narrowed(self, qt_app, compose):
        laid_out(qt_app, (1366, 697))
        flow = qt_app.views["create"]
        assert flow.layout().contentsMargins().left() == 0
        assert flow.pages.width() == flow.width()
