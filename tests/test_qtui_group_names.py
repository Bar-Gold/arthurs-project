"""A new group shows its name, never its number.

Reported: "when you add a new group it shows numbers, and only after about a
minute changes to the group's name". Three things added up to that:

* every lookup paused a flat four seconds *after* the page had loaded, though
  the heading is there the moment it does (see test_groupinfo.py);
* a lookup asked for while another was running was dropped, not queued, so the
  group waited until the Groups screen was next opened;
* a lookup made while Chrome was still starting was never retried until then.

These tests hold the background work in a queue and release it by hand, so the
order of events is exact and nothing reaches a browser.
"""

from __future__ import annotations

import pytest

from fbposter import chrome
from fbposter.qtui.views.groups import LOOKING_UP
from fbposter.ui.connection import ConnectionResult, ConnectionState

FIRST = "https://www.facebook.com/groups/1111111111/"
SECOND = "https://www.facebook.com/groups/2222222222/"


class Namer:
    """Answers from a table, and remembers every sweep it was asked for."""

    def __init__(self, names: dict[str, str]) -> None:
        self.names = names
        self.sweeps: list[list[str]] = []

    def names_for(self, urls):
        self.sweeps.append(list(urls))
        return {url: self.names.get(url, "") for url in urls}

    def name_for(self, url):
        return self.names_for([url]).get(url, "")


class HeldBack:
    """Stands in for App.run_in_background: nothing runs until released."""

    def __init__(self) -> None:
        self.jobs = []

    def __call__(self, work, on_success=None, on_error=None) -> None:
        self.jobs.append((work, on_success, on_error))

    def release(self) -> None:
        work, on_success, _on_error = self.jobs.pop(0)
        on_success(work())


@pytest.fixture
def held(qt_app, monkeypatch):
    runner = HeldBack()
    monkeypatch.setattr(qt_app, "run_in_background", runner)
    monkeypatch.setattr(chrome, "probe", lambda *a, **k: object())
    qt_app.group_namer = Namer({FIRST: "Bikes Tel Aviv", SECOND: "יד שנייה חיפה"})
    qt_app.show_view("groups")
    return runner


def add(qt_app, url):
    view = qt_app.views["groups"]
    view.url_entry.setText(url)
    assert view.add_group()
    return qt_app.group_repo.list()[-1]


def row_text(qt_app, group_id) -> str:
    return qt_app.views["groups"]._checkboxes[group_id].text()


class TestTheNumberIsNeverShown:
    def test_a_new_row_says_it_is_looking_up_its_name(self, qt_app, held):
        group = add(qt_app, FIRST)
        assert row_text(qt_app, group.id) == LOOKING_UP
        assert group.identifier not in row_text(qt_app, group.id)

    def test_the_toast_does_not_announce_a_number(self, qt_app, held):
        group = add(qt_app, FIRST)
        assert group.identifier not in qt_app.toast_label.text()

    def test_the_name_replaces_the_placeholder(self, qt_app, held):
        group = add(qt_app, FIRST)
        held.release()
        assert row_text(qt_app, group.id) == "Bikes Tel Aviv"
        assert qt_app.group_repo.get(group.id).name == "Bikes Tel Aviv"

    def test_the_toast_names_it_when_the_name_arrives(self, qt_app, held):
        add(qt_app, FIRST)
        held.release()
        assert "Bikes Tel Aviv" in qt_app.toast_label.text()


class TestNoRequestIsDropped:
    def test_a_group_added_during_a_lookup_is_looked_up_next(self, qt_app, held):
        """It used to be dropped, and waited for the screen to be reopened."""
        add(qt_app, FIRST)
        second = add(qt_app, SECOND)
        assert row_text(qt_app, second.id) == LOOKING_UP
        held.release()
        assert held.jobs, "the second group's lookup was dropped"
        held.release()
        assert row_text(qt_app, second.id) == "יד שנייה חיפה"

    def test_the_group_just_added_goes_first(self, qt_app, held):
        """Ahead of an older group whose name could not be read before."""
        old = qt_app.group_repo.add_from_url(SECOND)  # nameless
        new = add(qt_app, FIRST)
        held.release()
        assert qt_app.group_namer.sweeps[0][0] == FIRST
        assert qt_app.group_repo.get(old.id).name
        assert qt_app.group_repo.get(new.id).name


class TestWhenTheNameCannotBeRead:
    def test_the_number_is_the_fallback(self, qt_app, held):
        qt_app.group_namer = Namer({})
        group = add(qt_app, FIRST)
        held.release()
        assert row_text(qt_app, group.id) == group.identifier

    def test_it_is_not_asked_again_in_a_loop(self, qt_app, held):
        """The re-run covers only what the last sweep did not try."""
        qt_app.group_namer = Namer({})
        add(qt_app, FIRST)
        add(qt_app, SECOND)
        held.release()
        held.release()
        assert held.jobs == []

    def test_no_chrome_says_why(self, qt_app, held, monkeypatch):
        monkeypatch.setattr(chrome, "probe", lambda *a, **k: None)
        add(qt_app, FIRST)
        held.release()
        assert "Chrome" in qt_app.toast_label.text()


class TestTheNameIsReadOnceChromeIsUp:
    def test_a_connection_looks_up_nameless_groups(self, qt_app, held):
        """A group added while Chrome was starting used to keep its number
        until the Groups screen was next opened."""
        group = qt_app.group_repo.add_from_url(FIRST)
        qt_app._on_check_result(ConnectionResult(ConnectionState.CONNECTED, ""))
        held.release()
        assert qt_app.group_repo.get(group.id).name == "Bikes Tel Aviv"

    def test_nothing_is_opened_when_every_name_is_known(self, qt_app, held):
        qt_app.group_repo.add_from_url(FIRST, name="Bikes Tel Aviv")
        qt_app._on_check_result(ConnectionResult(ConnectionState.CONNECTED, ""))
        assert held.jobs == []

    def test_a_failed_check_does_not_look_anything_up(self, qt_app, held):
        qt_app.group_repo.add_from_url(FIRST)
        qt_app._on_check_result(ConnectionResult(ConnectionState.CHROME_DOWN, ""))
        assert held.jobs == []
