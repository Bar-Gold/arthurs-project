"""Tests for reading a group's display name.

Cosmetic work, so the strongest requirement is that it never raises: failing to
find a name must not stop a group being added.
"""

from __future__ import annotations

from fbposter.automation.groupinfo import LiveGroupNamer, read_name
from fbposter.groups import clean_group_title

# The real titles observed on the live site.
REAL_TITLE = "(20+) bar-test | Facebook"
HEBREW_TITLE = "(20+) מוכרים-קונים כרטיסים להופעות | Facebook"


class TestCleanGroupTitle:
    def test_the_real_observed_title(self):
        assert clean_group_title(REAL_TITLE) == "bar-test"

    def test_a_hebrew_name_survives_intact(self):
        assert clean_group_title(HEBREW_TITLE) == "מוכרים-קונים כרטיסים להופעות"

    def test_an_exact_unread_count_is_stripped_too(self):
        assert clean_group_title("(3) Neighbourhood Sales | Facebook") == "Neighbourhood Sales"

    def test_no_unread_count_at_all(self):
        assert clean_group_title("Neighbourhood Sales | Facebook") == "Neighbourhood Sales"

    def test_a_name_containing_a_pipe_keeps_it(self):
        """Only the site suffix comes off, not every pipe in the name."""
        assert clean_group_title("(5) Bikes | Parts | Facebook") == "Bikes | Parts"

    def test_a_bare_site_title_yields_nothing(self):
        assert clean_group_title("Facebook") == ""
        assert clean_group_title("(20+) Facebook") == ""

    def test_empty_input(self):
        assert clean_group_title("") == ""
        assert clean_group_title("   ") == ""

    def test_whitespace_is_collapsed(self):
        assert clean_group_title("(1)   Spaced   Out   | Facebook") == "Spaced Out"


class FakePage:
    def __init__(self, heading="", title="", raises=False) -> None:
        self.heading = heading
        self.title = title
        self.raises = raises

    def evaluate(self, _script):
        if self.raises:
            raise RuntimeError("page went away")
        return {"heading": self.heading, "title": self.title}


class TestReadName:
    def test_the_heading_is_preferred(self):
        """The h1 is the name on its own, with none of the title's noise."""
        page = FakePage(heading="bar-test", title="(20+) something else | Facebook")
        assert read_name(page) == "bar-test"

    def test_it_falls_back_to_the_title(self):
        assert read_name(FakePage(heading="", title=REAL_TITLE)) == "bar-test"

    def test_a_hebrew_heading_survives(self):
        assert read_name(FakePage(heading="מוכרים-קונים")) == "מוכרים-קונים"

    def test_heading_whitespace_is_collapsed(self):
        assert read_name(FakePage(heading="  bar   test \n")) == "bar test"

    def test_nothing_usable_gives_an_empty_string(self):
        assert read_name(FakePage(heading="", title="Facebook")) == ""

    def test_a_page_that_blows_up_yields_nothing_rather_than_raising(self):
        """A missing name is cosmetic; it must never break adding a group."""
        assert read_name(FakePage(raises=True)) == ""


class TestLiveNamerIsSafe:
    def test_no_browser_means_empty_names_not_an_exception(self, monkeypatch):
        from fbposter.automation import groupinfo

        def no_session(*_a, **_k):
            raise RuntimeError("Chrome is not running")

        monkeypatch.setattr(groupinfo.session, "attach", no_session)

        namer = LiveGroupNamer()
        assert namer.names_for(["https://www.facebook.com/groups/1/"]) == {}
        assert namer.name_for("https://www.facebook.com/groups/1/") == ""


class TestTheHeadingIsTheGroupsOwn:
    """A group called "TRY" was stored as "Chats".

    `document.querySelector('h1')` returns the first h1 in *document order*,
    and Facebook's chat sidebar inserts its own h1 ahead of the group's about
    1.75 seconds into the load. Sampled live against a real group:

        750ms   h1 count=1   first='TRY'     main='TRY'
       1750ms   h1 count=2   first='Chats'   main='TRY'

    So it is not a race that waiting fixes -- the app already waited four
    seconds, and by then the wrong answer was stable. The heading has to be
    scoped to the main landmark.

    The failure lives in a JavaScript string that no Python test can execute,
    so what is pinned here is the selector itself.
    """

    def test_the_heading_is_read_from_inside_the_main_landmark(self):
        from fbposter.automation.groupinfo import READ_NAME

        assert '[role="main"] h1' in READ_NAME

    def test_no_unscoped_h1_lookup_survives(self):
        """The exact line that produced the bug."""
        from fbposter.automation.groupinfo import READ_NAME

        assert "querySelector('h1')" not in READ_NAME
        assert 'querySelector("h1")' not in READ_NAME
        assert "querySelectorAll('h1')" not in READ_NAME

    def test_furniture_is_never_preferred_to_the_group(self):
        """The shape of the real page: two headings, the group's second."""
        page = FakePage(heading="TRY", title="(20+) TRY | Facebook")
        assert read_name(page) == "TRY"

    def test_the_title_is_the_fallback_not_some_other_heading(self):
        """With no heading in main, the tab title is a better answer than an
        arbitrary h1 -- an arbitrary h1 is how this bug happened."""
        assert read_name(FakePage(heading="", title="(20+) TRY | Facebook")) == "TRY"


class RecordingPage(FakePage):
    """A page that records the waits, for the timing tests below."""

    def __init__(self, heading="TRY", title="(20+) TRY | Facebook", appears=True) -> None:
        super().__init__(heading=heading, title=title)
        self.appears = appears
        self.calls = []

    def goto(self, url, **kwargs):
        self.calls.append(("goto", url, kwargs))

    def wait_for_function(self, script, **kwargs):
        self.calls.append(("wait_for_function", script, kwargs))
        if not self.appears:
            raise TimeoutError("no heading")

    def wait_for_timeout(self, ms):
        self.calls.append(("wait_for_timeout", ms))

    def close(self):
        self.calls.append(("close",))


class TestTheNameIsReadAsSoonAsItIsThere:
    """A lookup used to pause a flat four seconds after the page loaded.

    Measured live on 2026-09-25: DOMContentLoaded at 3.47s, the group's
    heading at 3.50s. The pause was more than half of every lookup, spent
    waiting for something that had already happened.
    """

    def test_it_waits_for_the_heading_not_for_a_clock(self):
        from fbposter.automation.groupinfo import wait_for_heading

        page = RecordingPage()
        assert wait_for_heading(page) is True
        pauses = [call[1] for call in page.calls if call[0] == "wait_for_timeout"]
        assert all(ms < 1000 for ms in pauses), pauses

    def test_it_waits_for_the_groups_own_heading(self):
        """Scoped like READ_NAME, or the chat panel's "Chats" would do."""
        from fbposter.automation.groupinfo import HAS_HEADING

        assert '[role="main"] h1' in HAS_HEADING
        assert "querySelector('h1')" not in HAS_HEADING

    def test_it_polls_on_a_timer_not_on_animation_frames(self):
        """The Chrome it reads from is off-screen and may never paint."""
        from fbposter.automation.groupinfo import wait_for_heading

        page = RecordingPage()
        wait_for_heading(page)
        _name, _script, kwargs = page.calls[0]
        assert isinstance(kwargs["polling"], int)
        assert kwargs["timeout"] > 0

    def test_no_heading_falls_back_to_the_title_without_raising(self):
        from fbposter.automation.groupinfo import wait_for_heading

        page = RecordingPage(heading="", appears=False)
        assert wait_for_heading(page) is False
        assert read_name(page) == "TRY"

    def test_the_live_namer_navigates_and_reads_without_a_fixed_pause(self, monkeypatch):
        from contextlib import contextmanager

        from fbposter.automation import groupinfo

        page = RecordingPage()

        class Context:
            def new_page(self):
                return page

        @contextmanager
        def attach(*_a, **_k):
            yield Context()

        monkeypatch.setattr(groupinfo.session, "attach", attach)
        url = "https://www.facebook.com/groups/1/"
        assert LiveGroupNamer().names_for([url]) == {url: "TRY"}
        kinds = [call[0] for call in page.calls]
        assert kinds[:2] == ["goto", "wait_for_function"]
        assert kinds[-1] == "close"
        assert all(call[1] < 1000 for call in page.calls if call[0] == "wait_for_timeout")


class MembersPage(FakePage):
    """Answers READ_MEMBERS with `members`, and READ_NAME as FakePage does."""

    def __init__(self, members="", heading="TRY", fails_on=None) -> None:
        super().__init__(heading=heading)
        self.members = members
        self.fails_on = fails_on
        self.calls = []

    def goto(self, url, **kwargs):
        self.calls.append(("goto", url))
        if url == self.fails_on:
            raise RuntimeError("page did not load")

    def wait_for_function(self, script, **kwargs):
        self.calls.append(("wait_for_function", script))

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, script):
        from fbposter.automation.groupinfo import READ_MEMBERS

        if script == READ_MEMBERS:
            return self.members
        return super().evaluate(script)

    def close(self):
        self.calls.append(("close",))


class TestTheMemberCount:
    """Read live on 2026-10-14: "Private group · 2 members" under the name,
    the count a link to the member list, with no aria-label."""

    def test_it_is_kept_as_facebook_wrote_it(self):
        from fbposter.automation.groupinfo import read_members

        assert read_members(MembersPage("12K members")) == "12K members"
        assert read_members(MembersPage("\u200f12 אלף חברים")) == "12 אלף חברים"

    def test_no_count_on_the_page_reads_as_empty(self):
        from fbposter.automation.groupinfo import read_members

        assert read_members(MembersPage("")) == ""

    def test_a_page_that_blows_up_yields_nothing_rather_than_raising(self):
        from fbposter.automation.groupinfo import read_members

        assert read_members(FakePage(raises=True)) == ""

    def test_it_is_found_by_its_address_not_by_a_word(self):
        """Any language: the member-list link, inside the group's own main
        landmark, skipping the labelled "See everyone" beside it."""
        from fbposter.automation.groupinfo import READ_MEMBERS

        assert '[role="main"]' in READ_MEMBERS
        assert 'a[href*="/members"]' in READ_MEMBERS
        assert "hasAttribute('aria-label')" in READ_MEMBERS
        assert "members" not in READ_MEMBERS.replace('"/members"', "")


class TestOneVisitReadsBoth:
    def attach(self, monkeypatch, page):
        from contextlib import contextmanager

        from fbposter.automation import groupinfo

        class Context:
            def new_page(self):
                return page

        @contextmanager
        def attach(*_a, **_k):
            yield Context()

        monkeypatch.setattr(groupinfo.session, "attach", attach)

    def test_name_and_count_come_from_the_same_page(self, monkeypatch):
        from fbposter.automation.groupinfo import GroupInfo

        page = MembersPage("2 members")
        self.attach(monkeypatch, page)
        url = "https://www.facebook.com/groups/1/"
        assert LiveGroupNamer().info_for([url]) == {url: GroupInfo("TRY", "2 members")}
        assert [c[0] for c in page.calls].count("goto") == 1
        assert page.calls[-1] == ("close",)

    def test_a_page_that_did_not_load_is_left_out_and_still_closed(self, monkeypatch):
        """Left out, not empty: the screen tells "not tried" from "no count"."""
        one, two = "https://www.facebook.com/groups/1/", "https://www.facebook.com/groups/2/"
        page = MembersPage("2 members", fails_on=one)
        self.attach(monkeypatch, page)
        assert list(LiveGroupNamer().info_for([one, two])) == [two]
        assert [c for c in page.calls if c[0] == "close"] == [("close",), ("close",)]


class TestOneTabForTheWholeSweep:
    """The user's request (2026-10-14): one tab, going from group to group,
    rather than a new tab for each."""

    URLS = [f"https://www.facebook.com/groups/{n}/" for n in (1, 2, 3)]

    def run(self, monkeypatch, page):
        from contextlib import contextmanager

        from fbposter.automation import groupinfo

        opened = []

        class Context:
            def new_page(self):
                opened.append(1)
                return page

        @contextmanager
        def attach(*_a, **_k):
            yield Context()

        monkeypatch.setattr(groupinfo.session, "attach", attach)
        return LiveGroupNamer().info_for(self.URLS), opened

    def test_every_group_is_read_in_one_tab_closed_at_the_end(self, monkeypatch):
        page = MembersPage("2 members")
        found, opened = self.run(monkeypatch, page)
        assert list(found) == self.URLS
        assert len(opened) == 1
        assert [c[1] for c in page.calls if c[0] == "goto"] == self.URLS
        assert [c for c in page.calls if c[0] == "close"] == [("close",)]
        assert page.calls[-1] == ("close",)

    def test_a_failed_group_gets_the_rest_a_fresh_tab(self, monkeypatch):
        page = MembersPage("2 members", fails_on=self.URLS[1])
        found, opened = self.run(monkeypatch, page)
        assert list(found) == [self.URLS[0], self.URLS[2]]
        assert len(opened) == 2
        assert page.calls[-1] == ("close",)
