"""The window's look after the second design review (2026-10-12).

The user chose four changes, and each is pinned here:

* Statuses are badges. They were coloured words the size and weight of the
  grey "Cancel" and "Delete" beside them, so a status looked like a button.
* Two- and three-way choices are one joined toggle, not separate buttons.
* The sidebar has an icon per item in place of "1." to "3.".
* Spacing and type: a heading is never smaller than what it heads, the header
  has room under it, and the setup checklist is drawn, not typed.

They also asked for every long dash to go, from the app, the tests and the
docs. The last class here keeps them out.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fbposter.db.models import (
    TARGET_DONE,
    TARGET_UNCONFIRMED,
    TASK_DONE,
    TASK_HALTED,
)
from fbposter.qtui import icons, theme

ROOT = Path(__file__).resolve().parent.parent
BODY = "Selling a road bike, 54cm frame."

# Written as escapes, so this file does not contain what it forbids.
LONG_DASHES = ("\u2014", "\u2013")


@pytest.fixture
def groups(qt_app):
    return [
        qt_app.group_repo.add_from_url(
            f"https://www.facebook.com/groups/look{index}", name=f"Look {index}"
        )
        for index in range(2)
    ]


def badge_list(widget) -> list[tuple[str, str]]:
    """Every badge under a widget: its words, and the tone it is drawn in."""
    from PySide6.QtWidgets import QLabel

    return [
        (label.text(), label.property("tone"))
        for label in widget.findChildren(QLabel)
        if label.objectName() == "Badge"
    ]


def badges(widget) -> dict[str, str]:
    return dict(badge_list(widget))


class TestStatusesAreBadges:
    def test_each_group_and_the_batch_carry_one(self, qt_app, groups):
        task = qt_app.task_repo.create(BODY, [(g.id, BODY) for g in groups])
        first, second = qt_app.task_repo.targets_for(task.id)
        qt_app.task_repo.mark_target(first.id, TARGET_DONE, posted=True, post_url="u")
        # Tried, as the worker's claim records it: Recent activity lists only
        # groups whose turn came.
        qt_app.task_repo.mark_target(
            second.id, TARGET_UNCONFIRMED, error="Look at the group.", attempted=True
        )
        qt_app.task_repo.mark_task(task.id, TASK_HALTED, finished=True)
        qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        qt_app.show_view("dashboard")

        shown = badges(qt_app.views["dashboard"].holder)
        assert shown["Posted"] == "SUCCESS"
        assert shown["Might have posted"] == "DANGER"
        assert shown["Queued"] == "TEXT_MUTED"

    def test_a_finished_batch_reads_by_what_reached_the_groups(self, qt_app, groups):
        from fbposter.qtui.views.dashboard import task_label

        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        (target,) = qt_app.task_repo.targets_for(task.id)
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True, post_url="u")
        qt_app.task_repo.mark_task(task.id, TASK_DONE, finished=True)
        task = qt_app.task_repo.get(task.id)
        targets = qt_app.task_repo.targets_for(task.id)
        assert task_label(task, targets) == ("Posted", "SUCCESS")

    def test_the_time_is_its_own_line_under_the_words(self, qt_app, groups):
        """It shared one bold line with the words, joined by a dash."""
        from PySide6.QtWidgets import QLabel

        qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        qt_app.show_view("dashboard")
        texts = [
            label.text()
            for label in qt_app.views["dashboard"].holder.findChildren(QLabel)
        ]
        assert BODY in texts
        assert any(text.startswith("Right away") for text in texts)

    def test_a_repeating_post_says_whether_it_is_on(self, qt_app, groups):
        from fbposter import recurrence
        from fbposter.db.models import utcnow

        rule = recurrence.build(["09:00"])
        qt_app.schedule_repo.create(
            name="Daily", bodies=[BODY], group_ids=[groups[0].id],
            times=list(rule.times), days=list(rule.days), media_paths=[],
            next_run_at=recurrence.next_occurrence(rule, utcnow()),
        )
        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        publish.refresh_schedules()
        assert badges(publish)["Active"] == "SUCCESS"

    def test_the_scheduler_status_is_one_too(self, qt_app):
        settings = qt_app.views["settings"]
        qt_app.show_view("settings")
        assert settings.status_label.objectName() == "Badge"
        assert settings.status_label.text() == "Not running"
        assert settings.status_label.property("tone") == "TEXT_MUTED"

    def test_an_unknown_tone_falls_back_to_grey(self, qt_application):
        from fbposter.qtui.widgets import badge, set_badge

        label = badge("Hm", "PURPLE")
        assert label.property("tone") == "TEXT_MUTED"
        set_badge(label, "Fine", "SUCCESS")
        assert (label.text(), label.property("tone")) == ("Fine", "SUCCESS")

    def test_every_tone_has_a_tint(self):
        for tone, tint in theme.BADGE_TINTS.items():
            assert tone in theme.LIGHT and tint in theme.LIGHT


class TestChoicesAreJoinedToggles:
    @pytest.mark.parametrize(
        "view,names",
        [
            ("compose", ("write_button", "preview_button")),
        ],
    )
    def test_each_toggle_is_one_control(self, qt_app, view, names):
        buttons = [getattr(qt_app.views[view], name) for name in names]
        self.assert_joined(buttons)

    def test_publish_when(self, qt_app):
        self.assert_joined(list(qt_app.views["publish"].mode_buttons.values()))

    @staticmethod
    def assert_joined(buttons):
        parents = {button.parentWidget() for button in buttons}
        assert len(parents) == 1
        assert parents.pop().objectName() == "Segmented"
        for button in buttons:
            assert button.objectName() == "Segment"
            assert button.isCheckable()
        assert sum(button.isChecked() for button in buttons) == 1

    def test_choosing_one_lets_go_of_the_other(self, qt_app):
        from fbposter.qtui.views.publish import NOW, ONCE

        publish = qt_app.views["publish"]
        qt_app.show_view("publish")
        once, now = publish.mode_buttons[ONCE], publish.mode_buttons[NOW]
        once.click()
        assert once.isChecked() and not now.isChecked()
        now.click()
        assert now.isChecked() and not once.isChecked()

    def test_the_wording_tabs_are_not_filled_with_the_accent(self):
        """Only the screen's next step is filled with the accent."""
        theme.activate()
        sheet = theme.stylesheet()
        rule = re.search(r"QPushButton#Tab:checked \{([^}]*)\}", sheet).group(1)
        assert theme.C["NAV_ACTIVE_BG"] in rule
        assert f"background: {theme.C['ACCENT']}" not in rule


class TestIcons:
    @pytest.mark.parametrize("name", sorted(icons.SHAPES))
    def test_every_icon_draws(self, qt_application, name):
        """A shape the SVG reader cannot parse is a blank space, not an error."""
        drawn = icons.pixmap(name, theme.C["TEXT"], 18)
        assert not drawn.isNull()
        image = drawn.toImage()
        assert any(
            image.pixelColor(x, y).alpha()
            for x in range(image.width())
            for y in range(image.height())
        ), f"{name} drew nothing"

    def test_the_brand_draws(self, qt_application):
        assert not icons.brand(24).isNull()

    def test_a_sidebar_icon_leaves_room_before_its_label(self, qt_application):
        """Qt allows 4px between an icon and its text, and a stylesheet
        cannot change it, so the room is drawn into the icon."""
        assert icons.NAV_ICON_BOX.width() > icons.NAV_ICON_SIZE
        drawn = icons.pixmap("compose", "#000000", 18, icons.NAV_ICON_BOX.width())
        image = drawn.toImage()
        right = range(round(image.width() * 0.75), image.width())
        assert not any(
            image.pixelColor(x, y).alpha() for x in right for y in range(image.height())
        )

    def test_the_connection_light_is_drawn_not_typed(self, qt_app):
        assert "\u25cf" not in qt_app.pill_label.text()
        assert not qt_app.pill_dot.pixmap().isNull()

    def test_the_setup_checklist_is_drawn_not_typed(self, qt_app):
        from PySide6.QtWidgets import QLabel

        welcome = qt_app.views["welcome"]
        qt_app.show_view("welcome")
        welcome.refresh(force=True)
        labels = welcome.checklist_card.findChildren(QLabel)
        texts = " ".join(label.text() for label in labels)
        for glyph in ("\u2713", "\u25b6", "\u25cb"):
            assert glyph not in texts
        assert sum(1 for label in labels if not label.pixmap().isNull()) == 3


class TestSpacingAndType:
    def test_a_heading_is_not_smaller_than_body_text(self):
        theme.activate()
        rule = re.search(r"QLabel#SectionHeading \{([^}]*)\}", theme.stylesheet()).group(1)
        size = int(re.search(r"font-size: (\d+)px", rule).group(1))
        assert size >= theme.SIZE_BODY

    def test_the_header_has_more_room_under_it_than_two_controls(self):
        assert theme.HEADER_GAP > theme.PAD_S

    def test_every_screen_uses_the_header_gap(self):
        views = ROOT / "fbposter" / "qtui" / "views"
        for path in views.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            if '"Subtitle"' in source:
                assert "theme.HEADER_GAP" in source, path.name


class TestTheSettingsPage:
    """Settings read as a form: controls in a column of their own, each hint
    under the control instead of the name it explained, and checkboxes for
    changes that happen the moment they are clicked."""

    def test_the_pc_settings_are_switches(self, qt_app):
        from fbposter.qtui.widgets import Switch

        settings = qt_app.views["settings"]
        for box in (settings.autostart_box, settings.awake_box):
            assert isinstance(box, Switch)
            assert box.accessibleName()
            assert box.text() == ""

    def test_a_switch_flips_and_reports_it(self, qt_application):
        from fbposter.qtui.widgets import Switch

        switch = Switch("Test")
        flipped = []
        switch.clicked.connect(lambda checked: flipped.append(checked))
        switch.click()
        assert switch.isChecked() and flipped == [True]
        switch.click()
        assert not switch.isChecked() and flipped == [True, False]

    def test_a_switch_draws_both_states(self, qt_application):
        from fbposter.qtui.widgets import Switch

        switch = Switch("Test")
        off = switch.grab().toImage()
        switch.setChecked(True)
        on = switch.grab().toImage()
        assert off != on

    def test_each_control_sits_at_the_right_of_its_row(self, qt_app):
        from PySide6.QtCore import Qt

        settings = qt_app.views["settings"]
        for control in (settings.autostart_box, settings.awake_box):
            line = control.parentWidget().layout()
            last = line.itemAt(line.count() - 1)
            assert last.widget() is control, control.accessibleName()
            assert last.alignment() & Qt.AlignRight

    def test_the_card_titles_are_a_step_above_the_setting_names(self):
        theme.activate()
        sheet = theme.stylesheet()
        rule = re.search(r"QLabel#CardTitle \{([^}]*)\}", sheet).group(1)
        assert f"{theme.SIZE_HEADING}px" in rule and "600" in rule

    def test_no_empty_note_leaves_a_gap(self, qt_app):
        settings = qt_app.views["settings"]
        qt_app.show_view("settings")
        assert settings.pc_note.isHidden()
        assert settings.confirm_row.isHidden()


class TestThePolishPass:
    """The polish pass of 2026-10-20: the user chose one look for every list
    of groups, quiet Back links, and a Dashboard led by four numbers."""

    def test_a_list_draws_a_line_only_between_rows_shown(self, qt_application):
        from PySide6.QtWidgets import QLabel

        from fbposter.qtui.widgets import RowList

        rows = RowList()
        labels = [rows.add(QLabel(f"Row {n}")) for n in range(4)]
        assert rows.lines() == 3
        labels[0].hide()
        labels[2].hide()
        assert rows.lines() == 1, "a hidden row takes its line with it"

    def test_every_group_list_is_one_box(self, qt_app, groups):
        from fbposter.qtui.widgets import RowList

        view = qt_app.views["groups"]
        view.refresh(force=True)
        assert isinstance(view.group_list, RowList)
        assert all(row.parentWidget() is view.group_list for row in view._rows.values())
        assert {row.objectName() for row in view._rows.values()} == {"ListRow"}

        sets = qt_app.views["group_sets"]
        sets.open_editor()
        assert all(row.parentWidget() is sets.editor_list
                   for row in sets._editor_rows.values())

    def test_a_search_that_matches_nothing_draws_no_empty_box(self, qt_app, groups):
        view = qt_app.views["groups"]
        view.refresh(force=True)
        view.search.setText("nothing is called this")
        assert view.group_list.isHidden()
        view.search.clear()
        assert not view.group_list.isHidden()

    def test_back_is_a_link_not_a_button(self, qt_app):
        from PySide6.QtWidgets import QPushButton

        for key in ("groups", "publish", "schedule_edit"):
            backs = [b for b in qt_app.views[key].findChildren(QPushButton)
                     if b.text().startswith("←")]
            assert backs, key
            assert {b.objectName() for b in backs} == {"BackLink"}, key

    def test_the_dashboard_leads_with_four_numbers(self, qt_app):
        from fbposter.qtui.views.dashboard import LEAD_TILES, STRIP_TILES

        tiles = qt_app.views["dashboard"].tiles
        assert set(LEAD_TILES) | set(STRIP_TILES) == set(tiles)
        assert all(tiles[key].frame.objectName() == "Card" for key in LEAD_TILES)
        assert all(tiles[key].value.objectName() == "StatSmall" for key in STRIP_TILES)

    def test_no_screen_sets_its_own_bold(self):
        views = ROOT / "fbposter" / "qtui" / "views"
        for path in views.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            assert 'setStyleSheet("font-weight: 600;")' not in source, path.name


class TestNoLongDashes:
    """The user asked for every long dash to go (2026-10-12): they read as
    machine-written. A sentence that wants one wants a full stop, a comma, a
    colon or brackets instead."""

    @staticmethod
    def files():
        patterns = ("fbposter/**/*.py", "tests/*.py", "*.py", "**/*.md", "packaging/*.*",
                    "scripts/*.*", ".claude/rules/**/*.md")
        skip = {".venv", ".git", "build", "dist", ".agents", ".claude", "__pycache__"}
        rules = ROOT / ".claude" / "rules"
        seen = set()
        for pattern in patterns:
            for path in ROOT.glob(pattern):
                if path in seen or not path.is_file():
                    continue
                if skip & set(path.relative_to(ROOT).parts) and rules not in path.parents:
                    continue
                if path.suffix.lower() in (".ico", ".png", ".exe"):
                    continue
                seen.add(path)
                yield path

    def test_none_anywhere(self):
        found = []
        for path in self.files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), 1):
                if any(dash in line for dash in LONG_DASHES):
                    found.append(f"{path.relative_to(ROOT)}:{number}")
        assert not found, "long dashes at " + ", ".join(found[:20])

    def test_the_scan_covers_the_app_and_the_docs(self):
        names = {path.relative_to(ROOT).as_posix() for path in self.files()}
        for expected in ("CLAUDE.md", "README.md", "fbposter/qtui/CLAUDE.md",
                         "packaging/CLAUDE.md", ".claude/rules/worker.md",
                         "fbposter/worker.py", "main.py"):
            assert expected in names
