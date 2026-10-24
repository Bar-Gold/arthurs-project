"""Tests for invisible characters, and the two things they used to break.

Text pasted from Word, WhatsApp or a browser carries characters that print
nothing. The user cannot see them and did not type them, but they end up in the
string, and two of the app's load-bearing comparisons are string equality
against the user's own words.

The two that matter are:

* TestTheSameWordsCompareTheSame: an invisible character made an identical
  post compare as different. That once let the repeated-text rule wave a
  repeat through; there are no rules now, but `normalise` still decides
  whether a group was sent these words before (`sent_before`), and so whether
  finding them in its feed proves anything about this post.
* TestVerificationIsNotFooled: Facebook strips these when it renders, so a
  snippet still containing one is searched for and never found, reporting a
  post that went out fine as failed and halting the batch.
"""

from __future__ import annotations

import pytest

from fbposter import text
from fbposter.automation.poster import distinctive_snippet

HEBREW = 'מוכר אופניים חשמליים במחיר 1800 ש"ח'

# Every invisible character, by the name a person would search for.
MARKS = {
    "LRM": "‎",
    "RLM": "‏",
    "LRE": "‪",
    "RLE": "‫",
    "PDF": "‬",
    "LRO": "‭",
    "RLO": "‮",
    "LRI": "⁦",
    "RLI": "⁧",
    "FSI": "⁨",
    "PDI": "⁩",
    "soft hyphen": "­",
    "zero width space": "​",
    "zero width non-joiner": "‌",
    "zero width joiner": "‍",
    "word joiner": "⁠",
    "BOM": "﻿",
}


class TestStripping:
    @pytest.mark.parametrize("name,mark", list(MARKS.items()))
    def test_every_known_mark_is_removed(self, name, mark):
        assert text.strip_invisible(f"a{mark}b") == "ab", name

    @pytest.mark.parametrize("name,mark", list(MARKS.items()))
    def test_has_invisible_spots_it(self, name, mark):
        assert text.has_invisible(f"a{mark}b"), name

    def test_ordinary_text_is_untouched(self):
        for sample in (HEBREW, "Selling a bike", "1800", "", "a\nb\tc"):
            assert text.strip_invisible(sample) == sample

    def test_a_visible_space_is_left_alone(self):
        """Only zero-width characters go. Removing anything with width would
        change what the reader sees."""
        assert text.strip_invisible("a b") == "a b"
        assert text.strip_invisible("a b") == "a b"  # non-breaking space

    def test_emoji_survive(self):
        assert text.strip_invisible("🚲 1800") == "🚲 1800"

    def test_an_emoji_built_from_a_joiner_is_left_recognisable(self):
        """A ZWJ sequence loses its joiner, which is a real trade-off: the
        family emoji splits into its parts rather than vanishing."""
        assert text.strip_invisible("👨‍👩‍👧") == "👨👩👧"

    def test_none_of_it_raises_on_odd_input(self):
        for sample in ("", " ", "\x00", "\U0010FFFF"):
            text.strip_invisible(sample)
            text.has_invisible(sample)


class TestTheSameWordsCompareTheSame:
    """The live hole: the same ad pasted twice compared as two different ads."""

    @pytest.mark.parametrize("name,mark", list(MARKS.items()))
    def test_a_mark_only_difference_is_the_same_text(self, name, mark):
        pasted_again = f"{mark}{HEBREW}{mark}"
        assert text.normalise(pasted_again) == text.normalise(HEBREW), name

    def test_it_works_the_other_way_round_too(self):
        """History stored before the fix still has marks in it."""
        stored_with_marks = f"‫{HEBREW}‬"
        assert text.normalise(HEBREW) == text.normalise(stored_with_marks)

    def test_genuinely_different_wording_is_different(self):
        assert text.normalise("A different ad entirely") != text.normalise(HEBREW)

    def test_normalise_folds_marks_with_case_and_spacing(self):
        assert text.normalise("  Hello​  World  ") == text.normalise("hello world")

    @pytest.mark.parametrize(
        "variant",
        ["selling a bike", "Selling  a   bike", "  Selling a bike\n", "Selling a bike\n\n"],
    )
    def test_trivial_differences_are_the_same_text(self, variant):
        """Whitespace and capitals are not new content."""
        assert text.normalise(variant) == text.normalise("Selling a bike")

    def test_reworded_text_is_different(self):
        assert text.normalise("Road bike for sale") != text.normalise("Selling a bike")


class TestVerificationIsNotFooled:
    """A false 'not verified' halts a batch that actually posted."""

    def test_the_snippet_carries_no_invisible_characters(self):
        snippet = distinctive_snippet(f"‫{HEBREW}‬")
        assert not text.has_invisible(snippet)

    def test_it_matches_what_facebook_would_render(self):
        rendered = HEBREW  # Facebook drops the marks
        assert distinctive_snippet(f"‏{HEBREW}") in rendered

    def test_a_body_of_nothing_but_marks_yields_no_snippet(self):
        assert distinctive_snippet("​‏‬") == ""


class TestTheComposerCleansOnTheWayIn:
    """Defence in depth: the guard folds them, and they never get stored."""

    def test_qt_compose_strips_pasted_marks(self, qt_app):
        compose = qt_app.views["compose"]
        compose._show(f"‫{HEBREW}‬")
        assert not text.has_invisible(compose.get_text())

    def test_the_captured_body_is_clean(self, qt_app):
        compose = qt_app.views["compose"]
        compose._show(f"‏{HEBREW}​")
        compose.capture()
        assert not text.has_invisible(compose.base_body())

    def test_every_version_is_cleaned_too(self, qt_app):
        """They become post bodies, same as the first."""
        compose = qt_app.views["compose"]
        compose._show(HEBREW)
        compose.add_version()
        compose._show(f"‫{HEBREW}‬")
        compose.capture()
        assert len(compose.versions()) == 2
        assert all(not text.has_invisible(w) for w in compose.versions())

    def test_the_character_count_reflects_what_will_be_sent(self, qt_app):
        compose = qt_app.views["compose"]
        compose._show(f"‏‏‏abc")
        assert compose.get_text() == "abc"


class TestSingleInstance:
    """Second layer. The database claim is what makes a duplicate impossible;
    this stops two apps fighting over Chrome and the keep-awake request."""

    def test_the_first_holder_gets_it(self):
        from fbposter.single import SingleInstance

        first = SingleInstance(r"Global\FBPosterTest.First")
        try:
            assert first.acquired
        finally:
            first.release()

    def test_a_second_holder_of_the_same_name_is_refused(self):
        from fbposter.single import SingleInstance

        first = SingleInstance(r"Global\FBPosterTest.Contended")
        try:
            assert first.acquired
            second = SingleInstance(r"Global\FBPosterTest.Contended")
            try:
                assert not second.acquired, "two instances both think they are alone"
            finally:
                second.release()
        finally:
            first.release()

    def test_releasing_lets_the_next_one_in(self):
        from fbposter.single import SingleInstance

        first = SingleInstance(r"Global\FBPosterTest.Recycled")
        assert first.acquired
        first.release()
        second = SingleInstance(r"Global\FBPosterTest.Recycled")
        try:
            assert second.acquired
        finally:
            second.release()

    def test_release_is_safe_to_call_twice(self):
        from fbposter.single import SingleInstance

        lock = SingleInstance(r"Global\FBPosterTest.DoubleRelease")
        lock.release()
        lock.release()
