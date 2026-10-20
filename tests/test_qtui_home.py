"""Home: where the app opens, and where a finished post lands (2026-10-19).

A calm page with one thing to do. It is the first sidebar item, and the logo at
the top of the sidebar leads to it too.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtWidgets import QFrame, QLabel, QPushButton

from fbposter.db.models import TASK_RUNNING
from fbposter.qtui.app import NAV_ITEMS
from fbposter.qtui.views.home import CTA, greeting, status

ISRAEL = ZoneInfo("Asia/Jerusalem")


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


@pytest.fixture
def group(qt_app):
    return qt_app.group_repo.add_from_url("https://www.facebook.com/groups/demo0", name="Demo")


def waiting_post(qt_app, group):
    return qt_app.task_repo.create("Hi", [(group.id, "Hi")])


class TestTheGreeting:
    """Polish, 2026-10-20 (the user's choice): a greeting between two gold
    hairlines, where a copy of the sidebar's logo used to sit."""

    @pytest.mark.parametrize(
        "hour,words",
        [
            (5, "Good morning"),
            (11, "Good morning"),
            (12, "Good afternoon"),
            (17, "Good afternoon"),
            (18, "Good evening"),
            (2, "Good evening"),
        ],
    )
    def test_it_follows_the_hour(self, hour, words):
        assert greeting(datetime(2026, 10, 20, hour, 30, tzinfo=ISRAEL)) == words

    def test_the_hour_is_israels_not_utcs(self):
        # 03:30 UTC is 06:30 in Israel: morning there, still night in UTC.
        assert greeting(datetime(2026, 10, 20, 3, 30, tzinfo=timezone.utc)) == "Good morning"

    def test_it_is_in_capitals_between_two_gold_rules(self, qt_app):
        page = home(qt_app)
        assert page.greeting.text() in {"GOOD MORNING", "GOOD AFTERNOON", "GOOD EVENING"}
        assert len(page.findChildren(QFrame, "HomeRule")) == 2

    def test_the_logo_is_not_repeated(self, qt_app):
        """The sidebar already carries it, top left."""
        page = home(qt_app)
        assert not any(label.pixmap() and label.pixmap().width() > 40
                       for label in page.findChildren(QLabel))


class TestTheStatusLine:
    """One line under the button, in words, saying what the posting is doing."""

    def test_nothing_waiting(self, qt_app):
        assert home(qt_app).status_label.text() == "Nothing waiting to go out"

    def test_a_post_waiting(self, qt_app, group):
        waiting_post(qt_app, group)
        assert home(qt_app).status_label.text() == "A post is waiting to go out"

    def test_posts_waiting(self, qt_app, group):
        waiting_post(qt_app, group)
        waiting_post(qt_app, group)
        assert home(qt_app).status_label.text() == "Posts are waiting to go out"

    def test_a_post_going_out(self, qt_app, group):
        task = waiting_post(qt_app, group)
        qt_app.task_repo.mark_task(task.id, TASK_RUNNING, started=True)
        assert home(qt_app).status_label.text() == "A post is going out now"

    def test_a_repeating_post(self, qt_app, group):
        qt_app.schedule_repo.create(
            name="Daily", bodies=["Hi"], group_ids=[group.id], times=["09:00"]
        )
        assert home(qt_app).status_label.text() == "Your repeating post is on"

    def test_a_pause_outranks_everything(self, qt_app, group):
        task = waiting_post(qt_app, group)
        qt_app.task_repo.mark_task(task.id, TASK_RUNNING, started=True)
        page = home(qt_app)
        qt_app.worker = SimpleNamespace(paused=True, state="idle")
        try:
            qt_app._refresh_scheduler()
            assert page.status_label.text() == "Posting is paused"
        finally:
            # A worker left attached with a batch waiting makes closing the
            # window raise the real close dialog, which hangs the run.
            qt_app.worker = None

    def test_it_follows_the_worker_tick(self, qt_app, group):
        page = home(qt_app)
        waiting_post(qt_app, group)
        qt_app._refresh_scheduler()
        assert page.status_label.text() == "A post is waiting to go out"

    def test_it_asks_nothing_while_another_screen_is_open(self, qt_app, group):
        page = home(qt_app)
        qt_app.show_view("dashboard")
        waiting_post(qt_app, group)
        qt_app._refresh_scheduler()
        assert page.status_label.text() == "Nothing waiting to go out"

    def test_it_never_shows_a_number(self):
        lines = {
            status(paused, going, waiting, repeating)[0]
            for paused in (False, True)
            for going in (0, 1)
            for waiting in (0, 1, 2)
            for repeating in (0, 1, 2)
        }
        assert len(lines) == 7
        assert not any(ch.isdigit() for line in lines for ch in line)
