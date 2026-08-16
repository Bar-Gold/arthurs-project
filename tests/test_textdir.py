"""Which way text reads.

Tk renders bidirectional text correctly by itself -- it reorders the runs and
resolves punctuation without help. Alignment is the one decision left to the
app, so this module is small and these cases are all about getting the base
direction right.

The rule is Unicode P2/P3, which is also what Facebook applies to a post, so
these double as a check that the Compose preview stays faithful to what the
group will see.
"""

from __future__ import annotations

import sys

import pytest

from fbposter.ui import textdir

HEBREW = "שלום עולם"
ENGLISH = "Hello world"
ARABIC = "مرحبا بالعالم"
RLM = "‏"
RLI, PDI = "⁧", "⁩"


class TestBaseDirection:
    def test_hebrew_reads_right_to_left(self):
        assert textdir.base_direction(HEBREW) == textdir.RTL

    def test_english_reads_left_to_right(self):
        assert textdir.base_direction(ENGLISH) == textdir.LTR

    def test_arabic_reads_right_to_left(self):
        """Arabic letters are category AL rather than R, and count the same."""
        assert textdir.base_direction(ARABIC) == textdir.RTL

    def test_empty_text_falls_back_to_left_to_right(self):
        assert textdir.base_direction("") == textdir.LTR

    @pytest.mark.parametrize("text", ["", "   ", "12345", "!?.,-", "50 60 70"])
    def test_text_with_no_strong_character_is_left_to_right(self, text):
        assert textdir.base_direction(text) == textdir.LTR

    def test_the_first_strong_character_wins_not_the_first_character(self):
        """A Hebrew line that opens with a number or a dash is still Hebrew."""
        assert textdir.base_direction("50 שקלים") == textdir.RTL
        assert textdir.base_direction("— שלום") == textdir.RTL
        assert textdir.base_direction('"שלום"') == textdir.RTL

    def test_a_hebrew_sentence_containing_english_is_still_hebrew(self):
        assert textdir.base_direction("קנה את Nike עכשיו") == textdir.RTL

    def test_an_english_sentence_containing_hebrew_is_still_english(self):
        assert textdir.base_direction("Buy שלום now") == textdir.LTR

    def test_the_first_line_decides_for_the_whole_post(self):
        """The editor aligns as one block, so the whole text gets one answer."""
        assert textdir.base_direction(f"{ENGLISH}\n{HEBREW}") == textdir.LTR
        assert textdir.base_direction(f"{HEBREW}\n{ENGLISH}") == textdir.RTL

    def test_a_bare_right_to_left_mark_counts_as_strong(self):
        assert textdir.base_direction(RLM + "50") == textdir.RTL

    def test_an_isolated_run_does_not_decide_the_direction(self):
        """That is what an isolate is for: a quoted phrase cannot flip the post."""
        assert textdir.base_direction(f"{RLI}{HEBREW}{PDI} and then english") == textdir.LTR

    def test_an_unterminated_isolate_does_not_swallow_the_rest(self):
        """Malformed input still has to produce an answer rather than hang."""
        assert textdir.base_direction(f"{RLI}{HEBREW}") == textdir.LTR


class TestIsRtl:
    def test_it_agrees_with_base_direction(self):
        assert textdir.is_rtl(HEBREW)
        assert not textdir.is_rtl(ENGLISH)


class TestLineDirections:
    """Each line is aligned on its own.

    One direction for the whole post dragged an English paragraph over to the
    right-hand edge, which was reported as tangled and hard to read.
    """

    def test_each_line_follows_its_own_language(self):
        assert textdir.line_directions(f"{HEBREW}\n{ENGLISH}") == [
            textdir.RTL,
            textdir.LTR,
        ]
        assert textdir.line_directions(f"{ENGLISH}\n{HEBREW}") == [
            textdir.LTR,
            textdir.RTL,
        ]

    def test_a_line_with_no_direction_inherits_from_the_one_above(self):
        """A phone number under a Hebrew line belongs with the Hebrew."""
        assert textdir.line_directions(f"{HEBREW}\n054-1234567") == [
            textdir.RTL,
            textdir.RTL,
        ]

    def test_a_neutral_first_line_takes_the_direction_of_the_post(self):
        """Nothing above it to inherit from, so the post as a whole decides."""
        assert textdir.line_directions(f"1,800\n{HEBREW}")[0] == textdir.RTL
        assert textdir.line_directions(f"1,800\n{ENGLISH}")[0] == textdir.LTR

    def test_blank_lines_inherit_rather_than_snapping_back(self):
        assert textdir.line_directions(f"{HEBREW}\n\n{HEBREW}") == [textdir.RTL] * 3

    def test_a_neutral_post_is_left_to_right_throughout(self):
        assert textdir.line_directions("123\n\n456") == [textdir.LTR] * 3

    def test_there_is_one_direction_per_line(self):
        text = "\n".join([HEBREW, ENGLISH, "", "054-1", HEBREW])
        assert len(textdir.line_directions(text)) == len(text.split("\n"))

    def test_empty_text_still_describes_its_single_line(self):
        assert textdir.line_directions("") == [textdir.LTR]


class TestStrongDirection:
    """The tri-state the line rules are built on."""

    def test_it_reports_no_opinion_when_there_is_none(self):
        assert textdir.strong_direction("054-1234567") is None
        assert textdir.strong_direction("") is None

    def test_it_reports_a_direction_when_there_is_one(self):
        assert textdir.strong_direction(HEBREW) == textdir.RTL
        assert textdir.strong_direction(ENGLISH) == textdir.LTR


class TestToVisual:
    """Reordering for display, which is what makes the preview truthful.

    Tk hands Windows one run at a time, so a line mixing Hebrew with English or
    digits is drawn mirrored. These check the reordering puts it right without
    disturbing the text itself.
    """

    def strip_marks(self, text):
        """Drop the override characters so the visible content can be compared."""
        return text.replace("‭", "").replace("‬", "")

    def test_the_hebrew_is_reversed_for_display(self):
        visual = self.strip_marks(textdir.to_visual(HEBREW))
        assert visual == HEBREW[::-1]

    def test_english_is_left_alone(self):
        assert self.strip_marks(textdir.to_visual(ENGLISH)) == ENGLISH

    def test_an_embedded_english_word_keeps_its_own_order(self):
        """The bug in the naive fix: reversing everything spelt it backwards."""
        visual = self.strip_marks(textdir.to_visual("אני מוכר kalofan היום"))
        assert "kalofan" in visual
        assert "nafolak" not in visual

    def test_digits_keep_their_own_order(self):
        visual = self.strip_marks(textdir.to_visual("המחיר הוא 1000 שקל"))
        assert "1000" in visual
        assert "0001" not in visual

    def test_the_override_is_applied_so_windows_does_not_reorder_again(self):
        """Without it the reordering is undone as the text is drawn."""
        visual = textdir.to_visual(HEBREW)
        assert visual.startswith("‭") and visual.endswith("‬")

    def test_empty_text_is_left_alone(self):
        assert textdir.to_visual("") == ""

    def test_it_never_raises_on_odd_input(self):
        for text in ("‭‬", "🙂", "⁧abc", "1234", " "):
            textdir.to_visual(text)

    def test_it_degrades_instead_of_failing_without_the_library(self, monkeypatch):
        """Optional at runtime, exactly like Pillow."""
        monkeypatch.setitem(sys.modules, "bidi.algorithm", None)
        monkeypatch.setitem(sys.modules, "bidi", None)
        assert textdir.to_visual(HEBREW) == HEBREW


class TestTkOptions:
    """The two Tk words the rest of the UI is allowed to know about."""

    def test_hebrew_is_right_aligned(self):
        assert textdir.justify(HEBREW) == "right"
        assert textdir.anchor(HEBREW) == "e"

    def test_english_is_left_aligned(self):
        assert textdir.justify(ENGLISH) == "left"
        assert textdir.anchor(ENGLISH) == "w"

    def test_the_direction_forms_agree_with_the_text_forms(self):
        assert textdir.justify_for(textdir.RTL) == textdir.justify(HEBREW)
        assert textdir.anchor_for(textdir.LTR) == textdir.anchor(ENGLISH)
