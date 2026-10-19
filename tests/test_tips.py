"""The Posting tips words, checked without a window.

The page is advice the user gives their clients, in three languages, and it
decides nothing. What can go wrong silently is a language falling out of step
with the others: a tip missing in one, or a number changed in English and not
in Hebrew. These pin both.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from fbposter import tips

HEBREW = re.compile(r"[֐-׿]")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")


def every_text(page: tips.Page) -> list[str]:
    return [page.title, page.intro, page.sources] + [
        text for tip in page.tips for text in (tip.title, tip.body)
    ]


class TestTheLanguages:
    def test_the_switch_offers_exactly_the_pages_there_are(self):
        assert [code for code, _name in tips.LANGUAGES] == ["he", "en", "ru"]
        assert set(tips.PAGES) == {"he", "en", "ru"}

    def test_it_opens_in_hebrew(self):
        """The user's choice, 2026-10-18: most clients write in Hebrew."""
        assert tips.DEFAULT_LANGUAGE == "he"
        assert "he" in tips.RIGHT_TO_LEFT
        assert "en" not in tips.RIGHT_TO_LEFT and "ru" not in tips.RIGHT_TO_LEFT

    def test_an_unknown_language_falls_back_to_the_default(self):
        assert tips.page("fr") is tips.PAGES[tips.DEFAULT_LANGUAGE]


class TestTheyStayInStep:
    def test_every_language_has_the_same_number_of_tips(self):
        counts = {code: len(page.tips) for code, page in tips.PAGES.items()}
        assert len(set(counts.values())) == 1, counts
        assert counts["en"] == 10

    def test_nothing_is_left_empty(self):
        for code, page in tips.PAGES.items():
            for text in every_text(page):
                assert text.strip(), code

    @pytest.mark.parametrize("index", range(10))
    def test_each_tip_has_the_same_numbers_in_every_language(self, index):
        """A number changed in one language and not the others is advice that
        depends on which language the client reads."""
        numbers = {
            code: re.findall(r"\d+", page.tips[index].title + " " + page.tips[index].body)
            for code, page in tips.PAGES.items()
        }
        assert numbers["he"] == numbers["en"] == numbers["ru"], numbers

    def test_each_language_is_written_in_its_own_script(self):
        for tip in tips.PAGES["he"].tips:
            assert HEBREW.search(tip.title) and HEBREW.search(tip.body)
        for tip in tips.PAGES["ru"].tips:
            assert CYRILLIC.search(tip.title) and CYRILLIC.search(tip.body)
        for text in every_text(tips.PAGES["en"]):
            assert not HEBREW.search(text) and not CYRILLIC.search(text)

    def test_no_text_carries_invisible_marks(self):
        """Qt shapes Hebrew itself; a direction mark here would be a bug."""
        from fbposter.text import has_invisible

        for code, page in tips.PAGES.items():
            for text in every_text(page):
                assert not has_invisible(text), code


class TestItDecidesNothing:
    @pytest.mark.parametrize(
        "module",
        ["worker.py", "qtui/views/compose.py", "qtui/views/publish.py", "recurrence.py"],
    )
    def test_nothing_that_posts_reads_it(self, module):
        source = (Path(tips.__file__).parent / module).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import) .*$", source, re.M)
        assert not any("tips" in line for line in imports), module
