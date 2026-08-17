"""Tests for invisible characters, and the two things they used to break.

Text pasted from Word, WhatsApp or a browser carries characters that print
nothing. The user cannot see them and did not type them, but they end up in the
string — and two of the app's load-bearing comparisons are string equality
against the user's own words.

The two that matter are:

* TestTheRepeatGuardIsNotFooled — an invisible character made an identical post
  compare as different, so the guard waved through the exact repeat it exists
  to stop. That is the app's main protection against a restriction.
* TestVerificationIsNotFooled — Facebook strips these when it renders, so a
  snippet still containing one is searched for and never found, reporting a
  post that went out fine as failed and halting the batch.
"""

from __future__ import annotations

import pytest

from fbposter import guards, text
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


class TestTheRepeatGuardIsNotFooled:
    """The live hole: paste the same ad twice and it went out twice."""

    @pytest.mark.parametrize("name,mark", list(MARKS.items()))
    def test_a_mark_only_difference_is_still_a_repeat(self, name, mark):
        already_sent = HEBREW
        pasted_again = f"{mark}{HEBREW}{mark}"
        violation = guards.check_repeat_text(pasted_again, [already_sent])
        assert violation is not None, f"{name} let a repeat through"

    def test_it_works_the_other_way_round_too(self):
        """History stored before the fix still has marks in it."""
        stored_with_marks = f"‫{HEBREW}‬"
        typed_clean = HEBREW
        assert guards.check_repeat_text(typed_clean, [stored_with_marks]) is not None

    def test_genuinely_different_wording_is_still_allowed(self):
        assert guards.check_repeat_text("A different ad entirely", [HEBREW]) is None

    def test_normalise_folds_marks_with_case_and_spacing(self):
        assert guards.normalise("  Hello​  World  ") == guards.normalise("hello world")

    def test_the_variation_warning_counts_them_as_the_same(self):
        bodies = [HEBREW, f"‏{HEBREW}", f"{HEBREW}​"]
        assert guards.variation_warning(bodies) is not None


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

    def test_alternate_wordings_are_cleaned_too(self, qt_app):
        """They become post bodies via a schedule, same as anything else."""
        publish = qt_app.views["publish"]
        publish.add_wording(f"‫{HEBREW}‬")
        assert all(not text.has_invisible(w) for w in publish.alternates())

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
