"""Tests for the human-like pacing.

`rng` and `sleep` are injected, so these run instantly and deterministically
while asserting the properties that actually matter for not looking automated.
"""

from __future__ import annotations

import random

from fbposter.automation.humanize import HumanProfile, Humanizer


class Recorder:
    """Stands in for time.sleep and remembers every wait."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)

    @property
    def total_ms(self) -> float:
        return sum(self.waits) * 1000


def make(seed: int = 7, profile: HumanProfile | None = None) -> tuple[Humanizer, Recorder]:
    recorder = Recorder()
    human = Humanizer(
        profile=profile or HumanProfile(),
        rng=random.Random(seed),
        sleep=recorder,
    )
    return human, recorder


class FakeKeyboard:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    def type(self, text: str) -> None:
        self.chunks.append(text)


class FakeMouse:
    def __init__(self) -> None:
        self.wheels: list[int] = []

    def wheel(self, x: int, y: int) -> None:
        self.wheels.append(y)


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()


class TestTyping:
    def test_text_is_typed_one_character_at_a_time(self):
        """A whole block pasted in 0ms is the clearest automation tell there is."""
        human, _ = make()
        keyboard = FakeKeyboard()

        human.type_text(keyboard, "hello world")

        assert keyboard.chunks == list("hello world")
        assert "".join(keyboard.chunks) == "hello world"

    def test_every_character_is_followed_by_a_wait(self):
        human, recorder = make()
        keyboard = FakeKeyboard()

        typed = human.type_text(keyboard, "abcdef")

        assert typed == 6
        assert len(recorder.waits) >= 6

    def test_keystroke_delays_vary(self):
        """Constant timing is its own fingerprint."""
        human, recorder = make()
        human.type_text(FakeKeyboard(), "abcdefghijklmnop")
        assert len(set(recorder.waits)) > 1

    def test_keystroke_delays_stay_inside_the_profile(self):
        profile = HumanProfile(keystroke_ms=(50, 150), word_pause_chance=0.0)
        human, recorder = make(profile=profile)

        human.type_text(FakeKeyboard(), "abcdefghij")

        for wait in recorder.waits:
            assert 0.050 <= wait <= 0.150

    def test_word_pauses_can_be_turned_off_and_on(self):
        never = HumanProfile(word_pause_chance=0.0)
        always = HumanProfile(word_pause_chance=1.0)

        human_a, rec_a = make(profile=never)
        human_a.type_text(FakeKeyboard(), "one two three four")

        human_b, rec_b = make(profile=always)
        human_b.type_text(FakeKeyboard(), "one two three four")

        assert len(rec_b.waits) > len(rec_a.waits)

    def test_typing_is_reproducible_for_a_given_seed(self):
        human_a, rec_a = make(seed=99)
        human_a.type_text(FakeKeyboard(), "reproducible")
        human_b, rec_b = make(seed=99)
        human_b.type_text(FakeKeyboard(), "reproducible")
        assert rec_a.waits == rec_b.waits

    def test_empty_text_types_nothing(self):
        human, recorder = make()
        keyboard = FakeKeyboard()
        assert human.type_text(keyboard, "") == 0
        assert keyboard.chunks == []
        assert recorder.waits == []


class TestBrowsing:
    def test_arriving_scrolls_a_random_number_of_times(self):
        human, _ = make()
        page = FakePage()
        steps = human.browse(page)
        low, high = human.profile.scroll_steps
        assert low <= steps <= high
        assert len(page.mouse.wheels) == steps

    def test_scroll_distances_are_inside_the_profile(self):
        human, _ = make()
        page = FakePage()
        human.browse(page)
        low, high = human.profile.scroll_delta
        for delta in page.mouse.wheels:
            assert low <= delta <= high


class TestClicking:
    def test_hover_always_precedes_click(self):
        """Clicking something the mouse was never over is not human."""
        events: list[str] = []

        class Locator:
            def hover(self):
                events.append("hover")

            def click(self):
                events.append("click")

        human, _ = make()
        human.hover_then_click(Locator())

        assert events == ["hover", "click"]

    def test_there_is_a_pause_between_hover_and_click(self):
        class Locator:
            def hover(self):
                pass

            def click(self):
                pass

        human, recorder = make()
        human.hover_then_click(Locator())
        assert len(recorder.waits) >= 2  # hover dwell, then settle


class TestGroupGap:
    def test_the_gap_is_inside_the_ten_to_twentyfive_minute_band(self):
        """README section 7: this pacing is the point, not a performance bug."""
        human, _ = make()
        for _ in range(200):
            gap = human.gap_between_groups()
            assert 600 <= gap <= 1500

    def test_gaps_vary(self):
        human, _ = make()
        gaps = {round(human.gap_between_groups()) for _ in range(20)}
        assert len(gaps) > 1

    def test_asking_for_the_gap_does_not_sleep(self):
        """The Phase 5 scheduler owns the wait so it can survive a restart."""
        human, recorder = make()
        human.gap_between_groups()
        assert recorder.waits == []
