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
