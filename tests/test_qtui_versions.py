"""Versions of a post, written in step 1 and shared out across the groups.

Step 1 had a tab per group, which could only be filled in after picking the
groups in step 2 and coming back (the user's report, 2026-10-23). Now the post
is written as Version 1, 2, 3 with no groups needed; the app shares them out,
step 3 shows which group gets which and lets any one be changed, and a
repeating post rotates the same versions.
"""

from __future__ import annotations

import pytest

from fbposter.db.models import TARGET_DONE
from fbposter.qtui.views.publish import NOW, ONCE, REPEAT

FIRST = "Selling a road bike, 54cm frame."
SECOND = "Road bike for sale, barely ridden."
THIRD = "54cm road bike, going cheap."


@pytest.fixture
def compose(qt_app):
    qt_app.show_view("compose")
    return qt_app.views["compose"]


def write(compose, text):
    compose._show(text)
    compose.capture()


def three_versions(compose):
    write(compose, FIRST)
    compose.add_version()
    write(compose, SECOND)
    compose.add_version()
    write(compose, THIRD)


@pytest.fixture
def groups(qt_app):
    made = []
    for index in range(5):
        made.append(qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/versions{index}", name=f"Group {index}"
        ))
    return made


def pick(qt_app, groups):
    for group in groups:
        qt_app.set_group_selected(group.id, True)


class TestWritingVersions:
    def test_there_is_one_to_start_with(self, compose):
        assert compose.version_count() == 1
        assert compose.tab_bar.count() == 1
        assert compose.remove_version_button.isHidden()

    def test_versions_need_no_groups(self, qt_app, compose):
        assert qt_app.selected_group_ids() == []
        three_versions(compose)
        assert compose.versions() == [FIRST, SECOND, THIRD]
        assert compose.tab_bar.count() == 3

    def test_a_new_version_opens_empty(self, compose):
        write(compose, FIRST)
        compose.add_version()
        assert compose._editing == 1
        assert compose.get_text() == ""

    def test_each_tab_shows_its_own_words(self, compose):
        three_versions(compose)
        compose.select_version(1)
        assert compose.get_text() == SECOND
        compose.select_version(0)
        assert compose.get_text() == FIRST

    def test_changing_one_leaves_the_others_alone(self, qt_app, compose):
        """Changing the shared text used to clear every per-group rewrite,
        with a warning."""
        three_versions(compose)
        compose.select_version(0)
        write(compose, "A new first version")
        assert compose.versions() == ["A new first version", SECOND, THIRD]
        assert "cleared" not in qt_app.toast_label.text()

    def test_the_footer_counts_them(self, compose):
        three_versions(compose)
        assert compose.variation_note.text().startswith("3 versions")
        assert not compose.remove_version_button.isHidden()

    def test_an_empty_version_is_not_a_version(self, compose):
        write(compose, FIRST)
        compose.add_version()
        assert compose.versions() == [FIRST]

    def test_the_first_with_words_is_the_post(self, compose):
        compose.add_version()
        write(compose, SECOND)
        assert compose.base_body() == SECOND

    def test_preview_says_which_version(self, compose):
        three_versions(compose)
        compose.select_version(1)
        assert compose.preview_heading()[1] == "Version 2 of 3"

    def test_preview_says_nothing_with_one_version(self, qt_app, compose, groups):
        """Nor how many groups, with or without any picked (the user's
        request, 2026-10-24)."""
        from PySide6.QtWidgets import QLabel

        pick(qt_app, groups)
        write(compose, FIRST)
        compose.set_mode("preview")
        assert compose.preview_heading()[1] == ""
        assert compose.preview.findChildren(QLabel, "PreviewNote") == []


class TestRemovingAVersion:
    def test_it_goes(self, compose):
        three_versions(compose)
        compose.select_version(1)
        compose.remove_version()
        assert compose.versions() == [FIRST, THIRD]
        assert compose.get_text() == THIRD

    def test_the_last_one_stays(self, compose):
        write(compose, FIRST)
        compose.remove_version()
        assert compose.versions() == [FIRST]

    def test_undo_puts_it_back_in_its_place(self, qt_app, compose):
        three_versions(compose)
        compose.select_version(1)
        compose.remove_version()
        assert qt_app.banner.undo_button.isVisibleTo(qt_app.banner)
        qt_app.banner.undo_button.click()
        assert compose.versions() == [FIRST, SECOND, THIRD]
        assert compose.get_text() == SECOND


class TestSharingThemOut:
    def test_each_group_starts_one_further_along(self, qt_app, compose, groups):
        three_versions(compose)
        pick(qt_app, groups)
        bodies = compose.bodies()
        assert [bodies[g.id] for g in groups] == [FIRST, SECOND, THIRD, FIRST, SECOND]

    def test_an_empty_version_is_passed_over(self, qt_app, compose, groups):
        write(compose, FIRST)
        compose.add_version()
        compose.add_version()
        write(compose, THIRD)
        pick(qt_app, groups[:3])
        assert set(compose.bodies().values()) == {FIRST, THIRD}

    def test_a_group_gets_one_it_has_not_had_today(self, qt_app, compose, groups):
        """The same listing posted twice in a day reaches each group in other
        words the second time."""
        task = qt_app.task_repo.create(FIRST, [(groups[0].id, FIRST)])
        (target,) = qt_app.task_repo.targets_for(task.id)
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True)
        three_versions(compose)
        pick(qt_app, groups[:1])
        assert compose.body_for(groups[0].id) == SECOND

    def test_post_now_sends_them(self, qt_app, compose, groups):
        three_versions(compose)
        pick(qt_app, groups[:3])
        assert compose.add_to_queue() is True
        (task,) = qt_app.task_repo.list_recent()
        sent = [t.body for t in qt_app.task_repo.targets_for(task.id)]
        assert sent == [FIRST, SECOND, THIRD]


class TestChoosingOnStep3:
    @pytest.fixture
    def publish(self, qt_app, compose, groups):
        three_versions(compose)
        pick(qt_app, groups[:3])
        qt_app.show_view("publish")
        return qt_app.views["publish"]

    def test_each_row_shows_its_version(self, qt_app, publish, groups):
        shown = [publish._version_lists[g.id].currentText() for g in groups[:3]]
        assert shown == ["Version 1", "Version 2", "Version 3"]

    def test_a_row_can_be_changed(self, qt_app, publish, compose, groups):
        picker = publish._version_lists[groups[0].id]
        picker.setCurrentIndex(2)
        picker.activated.emit(2)
        assert compose.body_for(groups[0].id) == THIRD
        assert publish.publish() is True
        (task,) = qt_app.task_repo.list_recent()
        assert qt_app.task_repo.targets_for(task.id)[0].body == THIRD

    def test_the_choice_survives_going_back_and_forth(self, qt_app, publish, groups):
        picker = publish._version_lists[groups[0].id]
        picker.setCurrentIndex(2)
        picker.activated.emit(2)
        qt_app.show_view("compose")
        qt_app.show_view("publish")
        assert publish._version_lists[groups[0].id].currentText() == "Version 3"

    def test_removing_the_version_gives_the_group_another(
        self, qt_app, publish, compose, groups
    ):
        picker = publish._version_lists[groups[0].id]
        picker.setCurrentIndex(2)
        picker.activated.emit(2)
        compose.select_version(2)
        compose.remove_version()
        assert compose.body_for(groups[0].id) == FIRST
        assert compose._chosen == {}

    def test_once_offers_them_too(self, publish, groups):
        publish.set_mode(ONCE)
        assert publish._version_lists

    def test_repeat_does_not(self, publish):
        """A repeating post's versions take turns, run after run."""
        publish.set_mode(REPEAT)
        assert publish._version_lists == {}
        publish.set_mode(NOW)
        assert publish._version_lists

    def test_one_version_needs_no_choice(self, qt_app, compose, groups):
        write(compose, FIRST)
        pick(qt_app, groups[:2])
        qt_app.show_view("publish")
        assert qt_app.views["publish"]._version_lists == {}

    def test_the_wheel_changes_nothing(self, publish, groups):
        from PySide6.QtCore import QPoint, QPointF, Qt
        from PySide6.QtGui import QWheelEvent

        picker = publish._version_lists[groups[0].id]
        before = picker.currentIndex()
        event = QWheelEvent(
            QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, -120),
            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
        )
        picker.wheelEvent(event)
        assert picker.currentIndex() == before


class TestTemplates:
    def test_every_version_is_kept(self, qt_app, compose):
        three_versions(compose)
        compose.template_name.setText("Bike")
        assert compose.save_template() is True
        compose.clear_draft()
        compose.refresh_templates()
        assert compose.template_names() == ["Bike"]
        assert compose.load_template("Bike") is True
        assert compose.versions() == [FIRST, SECOND, THIRD]
        assert compose.get_text() == FIRST

    def test_an_old_template_is_one_version(self, qt_app, compose):
        """Saved before versions, it has only its body."""
        qt_app.template_repo.save("Old", FIRST)
        qt_app.db.write("UPDATE templates SET versions = '[]' WHERE name = 'Old'")
        compose.refresh_templates()
        assert compose.load_template("Old") is True
        assert compose.versions() == [FIRST]
