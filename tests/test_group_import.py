"""Reading the client's groups off Facebook, so nobody pastes links.

The user's choices (2026-10-13): the groups are read by themselves once,
after the first login, and by a button on Groups after that; they start
ticked; the ones the account manages are included; and a group the user
removed is never added back. Nothing here opens a browser: the page is a fake
and the finder a stub.
"""

from __future__ import annotations

import random

import pytest

from fbposter import strings
from fbposter.automation import groupfinder
from fbposter.automation.groupfinder import (
    FoundGroup,
    FoundGroups,
    expected_count,
    groups_from_links,
    read_your_groups,
)
from fbposter.automation.humanize import Humanizer
from fbposter.connection import ConnectionResult, ConnectionState
from fbposter.groups import parse_group_url
from fbposter.qtui.app import SETUP_COMPLETE_KEY
from fbposter.qtui.views.groups import IMPORT_LABEL, IMPORT_PENDING_KEY, IMPORTING_LABEL

G = strings.BASE_URL + "/groups/"


def card(identifier: str, name: str) -> list[dict]:
    """One group as the live page draws it: picture, name, "View group"."""
    url = f"{G}{identifier}/"
    return [
        {"href": url, "text": "", "labelled": False},
        {"href": url, "text": name, "labelled": False},
        {"href": url, "text": "View group", "labelled": True},
    ]


class TestReadingTheLinks:
    def test_each_group_once_with_its_name_not_the_button(self):
        links = card("1697911281266837", "TRY") + card("SneakerHeadsInIsrael", "SneakerHeads in Israel")
        found = groups_from_links(links)
        assert [(f.ref.identifier, f.name) for f in found] == [
            ("1697911281266837", "TRY"),
            ("SneakerHeadsInIsrael", "SneakerHeads in Israel"),
        ]

    def test_a_short_name_beats_the_longer_button_text(self):
        """Taking the longest text would have named the group "View group"."""
        assert groups_from_links(card("1", "TRY"))[0].name == "TRY"

    def test_the_button_names_nothing_in_any_language(self):
        links = [
            {"href": f"{G}5/", "text": "הצגת הקבוצה", "labelled": True},
            {"href": f"{G}5/", "text": "מוכרים-קונים כרטיסים", "labelled": False},
        ]
        assert groups_from_links(links)[0].name == "מוכרים-קונים כרטיסים"

    def test_facebooks_own_screens_are_not_groups(self):
        links = [
            {"href": "/groups/feed/", "text": "Your feed", "labelled": False},
            {"href": "/groups/joins/?nav_source=tab", "text": "See all", "labelled": False},
            {"href": "/groups/discover/", "text": "Discover", "labelled": False},
            {"href": "https://example.com/groups/7/", "text": "Elsewhere", "labelled": False},
        ]
        assert groups_from_links(links) == []

    def test_a_link_inside_a_group_names_that_group(self):
        links = [{"href": "/groups/42/permalink/99/", "text": "", "labelled": False}]
        (found,) = groups_from_links(links)
        assert found.ref == parse_group_url(f"{G}42/")

    def test_invisible_characters_and_extra_lines_are_dropped(self):
        links = [{"href": f"{G}9/", "text": "‏Bikes  TLV\nLast active 3 days ago",
                  "labelled": False}]
        assert groups_from_links(links)[0].name == "Bikes TLV"

    def test_a_group_with_no_readable_name_still_comes(self):
        (found,) = groups_from_links([{"href": f"{G}9/", "text": "", "labelled": False}])
        assert (found.ref.identifier, found.name) == ("9", "")

    @pytest.mark.parametrize(
        "heading,count",
        [
            ("All groups you've joined (12)", 12),
            ("כל הקבוצות שהצטרפת אליהן (1,204)", 1204),
            ("Все группы (3)", 3),
            ("All groups you've joined", None),
            ("", None),
        ],
    )
    def test_the_count_in_the_heading(self, heading, count):
        assert expected_count(heading) == count


class FakeListPage:
    """The "Your groups" page: a list that grows as it is scrolled."""

    def __init__(self, pages, heading="All groups you've joined (3)", url=None, text=""):
        self.pages = list(pages)  # the links visible after each scroll
        self.heading = heading
        self.url = url or strings.YOUR_GROUPS_URL
        self.text = text
        self.visits = []
        self.wheels = 0
        self.shown = 0
        self.mouse = self

    def goto(self, url, **_kw):
        self.visits.append(url)

    def wait_for_function(self, *_a, **_kw):
        return True

    def wheel(self, _x, _y):
        self.wheels += 1

    def evaluate(self, _script):
        if self.wheels:  # a scroll since the last look shows the next part
            self.shown += 1
            self.wheels = 0
        links = self.pages[min(self.shown, len(self.pages) - 1)]
        return {"links": links, "heading": self.heading, "text": self.text}


def instant():
    return Humanizer(rng=random.Random(1), sleep=lambda _s: None)


class TestReadingThePage:
    def test_opens_the_list_and_reads_it(self):
        page = FakeListPage([card("1", "A") + card("2", "B") + card("3", "C")])
        result = read_your_groups(page, instant())
        assert page.visits == [strings.YOUR_GROUPS_URL]
        assert result.ok and [f.name for f in result.groups] == ["A", "B", "C"]
        assert result.expected == 3
        assert page.wheels == 0, "scrolled although every group was there"

    def test_scrolls_until_it_has_the_count(self):
        page = FakeListPage([card("1", "A"), card("2", "B"), card("3", "C")])
        result = read_your_groups(page, instant())
        assert [f.name for f in result.groups] == ["A", "B", "C"]

    def test_stops_when_nothing_new_appears(self):
        page = FakeListPage([card("1", "A")], heading="All groups you've joined (50)")
        result = read_your_groups(page, instant())
        assert result.ok and len(result.groups) == 1 and result.expected == 50
        assert page.shown == groupfinder.QUIET_SCROLLS

    def test_stops_at_the_time_limit(self):
        ticks = iter(range(0, 10_000, 100))
        endless = [card(str(n), f"G{n}") for n in range(1, 500)]
        page = FakeListPage(endless, heading="All groups you've joined (999)")
        read_your_groups(page, instant(), clock=lambda: next(ticks))
        assert page.shown * 100 <= groupfinder.TIME_LIMIT_S + 100

    def test_never_more_than_the_scroll_cap(self):
        endless = [card(str(n), f"G{n}") for n in range(1, 500)]
        page = FakeListPage(endless, heading="All groups you've joined (999)")
        result = read_your_groups(page, instant(), clock=lambda: 0)
        assert page.shown == groupfinder.MAX_SCROLLS
        assert len(result.groups) == groupfinder.MAX_SCROLLS + 1

    def test_a_checkpoint_reads_nothing(self):
        page = FakeListPage([card("1", "A")], url=strings.BASE_URL + "/checkpoint/123/")
        result = read_your_groups(page, instant())
        assert not result.ok and result.groups == []
        assert "security check" in result.error

    def test_a_page_with_no_list_and_no_heading_is_not_called_empty(self):
        page = FakeListPage([[]], heading="")
        result = read_your_groups(page, instant())
        assert not result.ok


class TestStoringThem:
    def found(self, *pairs):
        return [(identifier, f"{G}{identifier}/", name) for identifier, name in pairs]

    def test_new_groups_are_added_with_their_names(self, qt_app):
        new = qt_app.group_repo.import_found(self.found(("1", "Bikes"), ("2", "אופניים")))
        assert [(g.identifier, g.name) for g in new] == [("1", "Bikes"), ("2", "אופניים")]
        assert qt_app.group_repo.missing_names() == []

    def test_a_group_already_listed_is_left_alone(self, qt_app):
        qt_app.group_repo.add_from_url(f"{G}1/", name="Mine")
        new = qt_app.group_repo.import_found(self.found(("1", "Theirs"), ("2", "B")))
        assert [g.identifier for g in new] == ["2"]
        assert qt_app.group_repo.get_by_identifier("1").name == "Mine"

    def test_a_removed_group_stays_removed(self, qt_app):
        gone = qt_app.group_repo.add_from_url(f"{G}SneakerHeads/")
        qt_app.group_repo.remove(gone.id)
        new = qt_app.group_repo.import_found(self.found(("sneakerheads", "SneakerHeads")))
        assert new == []
        assert qt_app.group_repo.list() == []


class StubFinder:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def find(self):
        self.calls += 1
        return self.result


def found(*pairs):
    return FoundGroups(
        groups=[FoundGroup(parse_group_url(f"{G}{i}/"), name) for i, name in pairs],
        expected=len(pairs),
    )


@pytest.fixture
def window(qt_app, monkeypatch):
    """The window with a stub finder, Chrome "up", and background work run inline."""
    monkeypatch.setattr("fbposter.qtui.views.groups.chrome.probe", lambda: {"Browser": "x"})

    def inline(fn, ok=None, err=None):
        try:
            result = fn()
        except Exception as exc:
            if err:
                err(exc)
            return
        if ok:
            ok(result)

    monkeypatch.setattr(qt_app, "run_in_background", inline)
    qt_app.group_finder = StubFinder(found(("1", "TRY"), ("2", "Bikes")))
    return qt_app


def connect(app):
    app._on_check_result(ConnectionResult(ConnectionState.CONNECTED, ""))


class TestTheFirstLogin:
    def test_the_groups_arrive_by_themselves_ticked(self, window):
        connect(window)
        names = [g.display_name for g in window.group_repo.list()]
        assert names == ["TRY", "Bikes"]
        assert window.selected_groups == {g.id for g in window.group_repo.list()}
        assert "Added 2 of your Facebook groups" in window.toast_label.text()

    def test_only_once(self, window):
        connect(window)
        connect(window)
        assert window.group_finder.calls == 1
        assert window.settings_repo.get(IMPORT_PENDING_KEY) == "0"

    def test_a_client_already_set_up_is_not_given_one(self, window):
        """Upgrading from an older version: they have their list already."""
        window.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        connect(window)
        assert window.group_finder.calls == 0
        assert window.group_repo.list() == []

    def test_a_failed_read_is_tried_at_the_next_connection(self, window):
        window.group_finder = StubFinder(FoundGroups(error="Facebook said no."))
        connect(window)
        assert window.settings_repo.get(IMPORT_PENDING_KEY) == "1"
        assert window.toast_label.text() != "Facebook said no.", "the automatic run told nobody"
        window.group_finder = StubFinder(found(("1", "TRY")))
        connect(window)
        assert [g.name for g in window.group_repo.list()] == ["TRY"]

    def test_nothing_happens_without_a_finder(self, qt_app):
        """Every window the suite builds has it switched off."""
        assert qt_app.group_finder is None
        connect(qt_app)
        assert qt_app.group_repo.list() == []


class TestTheButton:
    def test_it_waits_for_a_connection(self, window):
        groups = window.views["groups"]
        window.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        window.show_view("groups")
        assert not groups.import_button.isEnabled()
        connect(window)
        assert groups.import_button.isEnabled()
        assert groups.import_button.text() == IMPORT_LABEL

    def test_it_adds_only_what_is_new(self, window):
        window.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        connect(window)
        window.group_repo.add_from_url(f"{G}1/", name="TRY")
        window.views["groups"].import_from_facebook()
        assert [g.name for g in window.group_repo.list()] == ["TRY", "Bikes"]
        assert "Added 1 of your Facebook group," in window.toast_label.text()

    def test_it_says_when_there_is_nothing_new(self, window):
        window.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        connect(window)
        window.views["groups"].import_from_facebook()
        window.views["groups"].import_from_facebook()
        assert window.toast_label.text().startswith("No new groups found.")

    def test_it_says_why_it_could_not(self, window):
        window.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        window.group_finder = StubFinder(FoundGroups(error="Facebook said no."))
        window.views["groups"].import_from_facebook()
        assert window.toast_label.text() == "Facebook said no."
        assert window.banner.level == "error"

    def test_it_says_when_some_could_not_be_read(self, window):
        window.group_finder = StubFinder(
            FoundGroups(groups=found(("1", "A")).groups, expected=4)
        )
        window.views["groups"].import_from_facebook()
        assert "Facebook lists 4 groups and 1 could be read" in window.toast_label.text()
        assert window.banner.level == "warning"

    def test_it_reads_busy_while_it_runs(self, window, monkeypatch):
        held = []
        monkeypatch.setattr(window, "run_in_background", lambda *a, **k: held.append(a))
        window.settings_repo.set(SETUP_COMPLETE_KEY, "1")
        connect(window)
        groups = window.views["groups"]
        groups.import_from_facebook()
        assert groups.import_button.text() == IMPORTING_LABEL
        assert not groups.import_button.isEnabled()
        groups.import_from_facebook()
        assert len(held) == 1, "a second read started while the first was running"
