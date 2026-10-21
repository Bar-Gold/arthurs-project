"""The Posting tips screen: read-only advice in Hebrew, English or Russian.

Built 2026-10-18, when the posting rules went and became advice. It opens in
Hebrew, right to left, and remembers the language last chosen.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from fbposter import tips
from fbposter.qtui.app import FLOW_STEPS, NAV_ITEMS
from fbposter.qtui.views.tips import TipsView


@pytest.fixture
def screen(qt_app):
    qt_app.show_view("tips")
    return qt_app.views["tips"]


def labels(view) -> list[str]:
    return [label.text() for label in view.findChildren(QLabel)]


class TestWhereItIs:
    def test_it_is_in_the_sidebar_but_not_a_step(self, qt_app):
        keys = [key for key, _label, _view in NAV_ITEMS]
        # Beside the Dashboard: both are there to look at (2026-10-21).
        assert keys.index("tips") == keys.index("dashboard") + 1
        assert keys.index("tips") < keys.index("settings")
        assert "tips" not in FLOW_STEPS
        assert qt_app.nav_buttons["tips"].text() == "Posting tips"

    def test_it_lights_its_own_item(self, qt_app, screen):
        assert qt_app.nav_buttons["tips"].isChecked()


class TestTheLanguage:
    def test_it_opens_in_hebrew_right_to_left(self, screen):
        page = tips.PAGES["he"]
        assert screen.shown == "he"
        assert screen.layoutDirection() == Qt.RightToLeft
        assert screen.heading.text() == page.title
        assert screen.language_buttons["he"].isChecked()

    @pytest.mark.parametrize("code", ["en", "ru"])
    def test_switching_draws_it_left_to_right(self, screen, code):
        screen.choose(code)
        assert screen.layoutDirection() == Qt.LeftToRight
        assert screen.heading.text() == tips.PAGES[code].title
        assert screen.language_buttons[code].isChecked()
        assert not screen.language_buttons["he"].isChecked()

    def test_the_choice_is_remembered(self, qt_app, screen):
        screen.choose("ru")
        assert qt_app.settings_repo.get(tips.LANGUAGE_KEY) == "ru"
        again = TipsView(qt_app)
        assert again.shown == "ru"

    def test_a_clicked_language_is_drawn(self, screen):
        screen.language_buttons["en"].click()
        assert screen.shown == "en"

    def test_a_stored_nonsense_language_opens_in_hebrew(self, qt_app):
        qt_app.settings_repo.set(tips.LANGUAGE_KEY, "xx")
        assert TipsView(qt_app).shown == "he"

    def test_choosing_writes_nothing_else(self, qt_app, screen):
        before = qt_app.settings_repo.all()
        screen.choose("en")
        after = qt_app.settings_repo.all()
        assert {k: v for k, v in after.items() if k != tips.LANGUAGE_KEY} == {
            k: v for k, v in before.items() if k != tips.LANGUAGE_KEY
        }


class TestWhatItShows:
    @pytest.mark.parametrize("code", ["he", "en", "ru"])
    def test_every_tip_is_on_screen(self, screen, code):
        screen.choose(code)
        shown = labels(screen)
        page = tips.PAGES[code]
        for number, tip in enumerate(page.tips, 1):
            assert str(number) in shown
            assert tip.title in shown
            assert tip.body in shown
        assert page.intro in shown
        assert page.sources in shown

    def test_switching_back_and_forth_leaves_one_set_of_cards(self, screen):
        for code in ("en", "ru", "he", "en"):
            screen.choose(code)
        firsts = [tip.title for page in tips.PAGES.values() for tip in page.tips[:1]]
        titles = [text for text in labels(screen) if text in firsts]
        assert titles == [tips.PAGES["en"].tips[0].title]

    def test_each_number_is_its_own_gold_figure(self, screen):
        """In the title serif and gold, apart from the title, so the eye can
        run down the list by number."""
        screen.choose("en")
        figures = [label.text() for label in screen.findChildren(QLabel)
                   if label.objectName() == "TipNumber"]
        assert figures == [str(n) for n in range(1, len(tips.PAGES["en"].tips) + 1)]

    def test_the_screens_it_names_are_real(self, qt_app):
        """It tells the client to press Pause posting on Settings and to add
        wordings in Create a post, step 3, under Repeat, so those had better
        be what the window says."""
        from fbposter.qtui.views.publish import REPEAT

        assert qt_app.nav_buttons["settings"].text() == "Settings"
        assert qt_app.nav_buttons["create"].text() == "Create a post"
        assert qt_app.views["publish"].mode_buttons[REPEAT].text() == "Repeat"
        assert qt_app.views["settings"].pause_button.text() == "Pause posting"
        for page in tips.PAGES.values():
            text = " ".join(tip.body for tip in page.tips)
            for name in ("Pause posting", "Settings", "Create a post", "Repeat"):
                assert name in text
