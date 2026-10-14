"""A new group shows its name, never its number.

Reported: "when you add a new group it shows numbers, and only after about a
minute changes to the group's name". Three things added up to that:

* every lookup paused a flat four seconds *after* the page had loaded, though
  the heading is there the moment it does (see test_groupinfo.py);
* a lookup asked for while another was running was dropped, not queued, so the
  group waited until the Groups screen was next opened;
* a lookup made while Chrome was still starting was never retried until then.

Groups now come off Facebook with their names (the paste box went on
2026-10-14), so a nameless group is one whose name could not be read off the
list, or one added before that. The rules for looking one up are unchanged.

These tests hold the background work in a queue and release it by hand, so the
order of events is exact and nothing reaches a browser.
"""

from __future__ import annotations

import pytest

from fbposter import chrome
from fbposter.automation.groupinfo import GroupInfo
from fbposter.qtui.views.groups import LOOKING_UP
from fbposter.connection import ConnectionResult, ConnectionState

FIRST = "https://www.facebook.com/groups/1111111111/"
SECOND = "https://www.facebook.com/groups/2222222222/"


class Namer:
    """Answers from a table, and remembers every sweep it was asked for."""

    def __init__(self, names: dict[str, str]) -> None:
        self.names = names
        self.sweeps: list[list[str]] = []

    def names_for(self, urls):
        return {url: info.name for url, info in self.info_for(urls).items()}

    def name_for(self, url):
        return self.names_for([url]).get(url, "")

    def info_for(self, urls):
        self.sweeps.append(list(urls))
        return {url: GroupInfo(self.names.get(url, "")) for url in urls}


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
    """A nameless group arrives, and the screen is told to name it."""
    group = qt_app.group_repo.add_from_url(url)
    qt_app.views["groups"].look_up_missing_names()
    return group


def row_text(qt_app, group_id) -> str:
    return qt_app.views["groups"]._checkboxes[group_id].text()


class TestTheNumberIsNeverShown:
    def test_a_new_row_says_it_is_looking_up_its_name(self, qt_app, held):
        group = add(qt_app, FIRST)
        assert row_text(qt_app, group.id) == LOOKING_UP
        assert group.identifier not in row_text(qt_app, group.id)

    def test_the_name_replaces_the_placeholder(self, qt_app, held):
        group = add(qt_app, FIRST)
        held.release()
        assert row_text(qt_app, group.id) == "Bikes Tel Aviv"
        assert qt_app.group_repo.get(group.id).name == "Bikes Tel Aviv"


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

    def test_no_chrome_falls_back_to_the_number(self, qt_app, held, monkeypatch):
        monkeypatch.setattr(chrome, "probe", lambda *a, **k: None)
        group = add(qt_app, FIRST)
        held.release()
        assert row_text(qt_app, group.id) == group.identifier


class TestTheNameIsReadOnceChromeIsUp:
    def test_a_connection_looks_up_nameless_groups(self, qt_app, held):
        """A group added while Chrome was starting used to keep its number
        until the Groups screen was next opened."""
        group = qt_app.group_repo.add_from_url(FIRST)
        qt_app._on_check_result(ConnectionResult(ConnectionState.CONNECTED, ""))
        held.release()
        assert qt_app.group_repo.get(group.id).name == "Bikes Tel Aviv"

    def test_nothing_is_opened_when_every_name_and_count_is_known(self, qt_app, held):
        group = qt_app.group_repo.add_from_url(FIRST, name="Bikes Tel Aviv")
        qt_app.group_repo.set_members(group.id, "2 members")
        qt_app._on_check_result(ConnectionResult(ConnectionState.CONNECTED, ""))
        assert held.jobs == []

    def test_a_failed_check_does_not_look_anything_up(self, qt_app, held):
        qt_app.group_repo.add_from_url(FIRST)
        qt_app._on_check_result(ConnectionResult(ConnectionState.CHROME_DOWN, ""))
        assert held.jobs == []


class Counter(Namer):
    """Answers with a member count too; None for a page that never loaded."""

    def __init__(self, members: dict[str, str | None]) -> None:
        super().__init__({FIRST: "Read off the page"})
        self.members = members

    def info_for(self, urls):
        self.sweeps.append(list(urls))
        return {
            url: GroupInfo(self.names.get(url, ""), self.members[url])
            for url in urls
            if self.members.get(url) is not None
        }


def members_shown(qt_app, group_id) -> str:
    from PySide6.QtWidgets import QLabel

    row = qt_app.views["groups"]._rows[group_id]
    labels = [label.text() for label in row.findChildren(QLabel)]
    return " ".join(labels)


class TestTheMemberCount:
    """Read in the same visit as the name (2026-10-14), for groups already
    in the list as well as new ones."""

    def test_a_named_group_gets_its_count(self, qt_app, held):
        group = qt_app.group_repo.add_from_url(FIRST, name="Bikes Tel Aviv")
        qt_app.group_namer = Counter({FIRST: "12K members"})
        qt_app.views["groups"].look_up_missing_names()
        held.release()
        stored = qt_app.group_repo.get(group.id)
        assert stored.members == "12K members"
        assert stored.name == "Bikes Tel Aviv", "a known name is never replaced"
        assert "12K members" in members_shown(qt_app, group.id)

    def test_a_page_with_no_count_is_not_reopened_on_every_visit(self, qt_app, held):
        group = qt_app.group_repo.add_from_url(FIRST, name="Bikes Tel Aviv")
        qt_app.group_namer = Counter({FIRST: ""})
        view = qt_app.views["groups"]
        view.look_up_missing_names()
        held.release()
        assert qt_app.group_repo.get(group.id).members_read_at is not None
        view.on_show()
        assert held.jobs == []

    def test_a_page_that_never_loaded_is_tried_again(self, qt_app, held):
        group = qt_app.group_repo.add_from_url(FIRST, name="Bikes Tel Aviv")
        qt_app.group_namer = Counter({FIRST: None})
        view = qt_app.views["groups"]
        view.look_up_missing_names()
        held.release()
        assert qt_app.group_repo.get(group.id).members_read_at is None
        view.on_show()
        assert held.jobs, "a group whose page never loaded was given up on"

    def test_no_count_shows_nothing_rather_than_a_placeholder(self, qt_app, held):
        group = qt_app.group_repo.add_from_url(FIRST, name="Bikes Tel Aviv")
        qt_app.views["groups"].refresh()
        assert "member" not in members_shown(qt_app, group.id).lower()
