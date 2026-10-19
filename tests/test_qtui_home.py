"""Home: where the app opens, and where a finished post lands (2026-10-19).

A calm page with one thing to do. It is the first sidebar item, and the logo at
the top of the sidebar leads to it too.
"""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from fbposter.qtui.app import NAV_ITEMS
from fbposter.qtui.views.home import CTA


def home(qt_app):
    qt_app.show_view("home")
    return qt_app.views["home"]


class TestWhereItIs:
    def test_it_is_the_first_sidebar_item(self, qt_app):
        assert NAV_ITEMS[0][0] == "home"
        assert list(qt_app.nav_buttons)[0] == "home"
        assert qt_app.nav_buttons["home"].text() == "Home"

    def test_the_logo_leads_home(self, qt_app):
        qt_app.show_view("settings")
        qt_app.brand_button.click()
        assert qt_app.current_view == "home"
        assert qt_app.nav_buttons["home"].isChecked()

    def test_the_logo_says_where_it_goes(self, qt_app):
        assert "Home" in qt_app.brand_button.accessibleName()
        assert qt_app.brand_button.toolTip() == "Home"


class TestWhatItOffers:
    def test_one_filled_button_and_it_creates_a_post(self, qt_app):
        page = home(qt_app)
        filled = [b for b in page.findChildren(QPushButton) if b.objectName() == "Primary"]
        assert filled == [page.create_button]
        assert page.create_button.text() == CTA

    def test_it_opens_step_1(self, qt_app):
        page = home(qt_app)
        page.create_button.click()
        assert qt_app.current_view == "compose"
        assert qt_app.nav_buttons["create"].isChecked()

    def test_it_says_nothing_but_what_to_do(self, qt_app):
        """No numbers and no lists: the Dashboard has those."""
        page = home(qt_app)
        assert page.headline.text()
        assert not any(ch.isdigit() for ch in page.line.text())
