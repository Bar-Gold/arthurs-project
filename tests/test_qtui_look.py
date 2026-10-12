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
        qt_app.task_repo.mark_target(
            second.id, TARGET_UNCONFIRMED, error="Look at the group."
        )
        qt_app.task_repo.mark_task(task.id, TASK_HALTED, finished=True)
        qt_app.show_view("queue")

        shown = badges(qt_app.views["queue"].holder)
        assert shown["Posted"] == "SUCCESS"
        assert shown["Might have posted"] == "DANGER"
        assert shown["Stopped"] == "DANGER"

    def test_a_finished_batch_reads_by_what_reached_the_groups(self, qt_app, groups):
        task = qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        (target,) = qt_app.task_repo.targets_for(task.id)
        qt_app.task_repo.mark_target(target.id, TARGET_DONE, posted=True, post_url="u")
        qt_app.task_repo.mark_task(task.id, TASK_DONE, finished=True)
        qt_app.show_view("queue")
        assert badge_list(qt_app.views["queue"].holder) == [
            ("Posted", "SUCCESS"),
            ("Posted", "SUCCESS"),
        ]

    def test_the_time_is_its_own_line_under_the_words(self, qt_app, groups):
        """It shared one bold line with the words, joined by a dash."""
        from PySide6.QtWidgets import QLabel

        qt_app.task_repo.create(BODY, [(groups[0].id, BODY)])
        qt_app.show_view("queue")
        texts = [label.text() for label in qt_app.views["queue"].holder.findChildren(QLabel)]
        assert BODY in texts
        assert "Right away" in texts

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

    def test_every_tone_has_a_tint_in_both_modes(self):
        for tone, tint in theme.BADGE_TINTS.items():
            for palette in (theme.LIGHT, theme.DARK):
                assert tone in palette and tint in palette


class TestChoicesAreJoinedToggles:
    @pytest.mark.parametrize(
        "view,names",
        [
            ("compose", ("write_button", "preview_button")),
            ("queue", ("recent_button", "all_button")),
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
        queue = qt_app.views["queue"]
        qt_app.show_view("queue")
        queue.all_button.click()
        assert queue.all_button.isChecked() and not queue.recent_button.isChecked()
        queue.recent_button.click()
        assert queue.recent_button.isChecked() and not queue.all_button.isChecked()

    def test_the_wording_tabs_are_not_filled_with_the_accent(self):
        """Only the screen's next step is filled with the accent."""
        theme.activate(dark=False)
        sheet = theme.stylesheet()
        rule = re.search(r"QPushButton#Tab:checked \{([^}]*)\}", sheet).group(1)
        assert theme.C["NAV_ACTIVE_BG"] in rule
        assert f"background: {theme.C['ACCENT']}" not in rule


class TestIcons:
    @pytest.mark.parametrize("name", sorted(icons.SHAPES))
    @pytest.mark.parametrize("dark", [False, True])
    def test_every_icon_draws(self, qt_application, name, dark):
        """A shape the SVG reader cannot parse is a blank space, not an error."""
        theme.activate(dark=dark)
        try:
            drawn = icons.pixmap(name, theme.C["TEXT"], 18)
            assert not drawn.isNull()
            image = drawn.toImage()
            assert any(
                image.pixelColor(x, y).alpha()
                for x in range(image.width())
                for y in range(image.height())
            ), f"{name} drew nothing"
        finally:
            theme.activate(dark=False)

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
        theme.activate(dark=False)
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
        for control in (settings.cap_entry, settings.cooldown_entry, settings.awake_box):
            line = control.parentWidget().layout()
            last = line.itemAt(line.count() - 1)
            assert last.widget() is control, control.accessibleName()
            assert last.alignment() & Qt.AlignRight

    def test_the_card_titles_are_a_step_above_the_setting_names(self):
        theme.activate(dark=False)
        sheet = theme.stylesheet()
        rule = re.search(r"QLabel#CardTitle \{([^}]*)\}", sheet).group(1)
        assert f"{theme.SIZE_HEADING}px" in rule and "600" in rule

    def test_no_empty_note_leaves_a_gap(self, qt_app):
        settings = qt_app.views["settings"]
        qt_app.show_view("settings")
        assert settings.pc_note.isHidden()
        assert settings.confirm_row.isHidden()


class TestNoLongDashes:
    """The user asked for every long dash to go (2026-10-12): they read as
    machine-written. A sentence that wants one wants a full stop, a comma, a
    colon or brackets instead."""

    @staticmethod
    def files():
        patterns = ("fbposter/**/*.py", "tests/*.py", "*.py", "**/*.md", "packaging/*.*",
                    "scripts/*.*")
        skip = {".venv", ".git", "build", "dist", ".agents", ".claude", "__pycache__"}
        seen = set()
        for pattern in patterns:
            for path in ROOT.glob(pattern):
                if path in seen or not path.is_file() or skip & set(path.parts):
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
                         "packaging/CLAUDE.md", "fbposter/worker.py", "main.py"):
            assert expected in names
