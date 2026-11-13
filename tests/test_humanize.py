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

    def insert_text(self, text: str) -> None:
        self.chunks.append(text)

    def press(self, key: str) -> None:
        self.chunks.append(f"<{key}>")


class FakeMouse:
    def __init__(self) -> None:
        self.wheels: list[int] = []

    def wheel(self, x: int, y: int) -> None:
        self.wheels.append(y)


class FakePage:
    def __init__(self) -> None:
        self.mouse = FakeMouse()


class TestPasting:
    """Pasted, as an agent pastes a listing (the user's choice, 2026-11-13)."""

    def test_a_line_goes_in_at_once(self):
        human, _ = make()
        keyboard = FakeKeyboard()
        assert human.paste_text(keyboard, "hello world") == 11
        assert keyboard.chunks == ["hello world"]

    def test_lines_are_joined_by_enter_as_when_typed(self):
        human, _ = make()
        keyboard = FakeKeyboard()
        human.paste_text(keyboard, "Flat for sale\n\nCall Dana\r\n050")
        assert keyboard.chunks == [
            "Flat for sale", "<Enter>", "<Enter>", "Call Dana", "<Enter>", "050",
        ]

    def test_a_moment_to_reach_for_the_keys_first(self):
        human, recorder = make()
        human.paste_text(FakeKeyboard(), "hello")
        low, high = HumanProfile().paste_reach_ms
        assert len(recorder.waits) == 1
        assert low / 1000 <= recorder.waits[0] <= high / 1000

    def test_the_look_over_is_inside_the_profile(self):
        human, recorder = make()
        human.review_pause()
        low, high = HumanProfile().paste_review_ms
        assert low / 1000 <= recorder.waits[0] <= high / 1000

    def test_empty_text_pastes_nothing_and_waits_for_nothing(self):
        human, recorder = make()
        keyboard = FakeKeyboard()
        assert human.paste_text(keyboard, "") == 0
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
    def test_the_gap_is_inside_the_two_to_four_minute_band(self):
        """Lowered from 10-25 minutes on 2026-10-18, for one listing to 40-80
        groups. Guides put the floor at 2-3 minutes: closer reads as a bot."""
        human, _ = make()
        for _ in range(200):
            gap = human.gap_between_groups()
            assert 120 <= gap <= 240

    def test_gaps_vary(self):
        human, _ = make()
        gaps = {round(human.gap_between_groups()) for _ in range(20)}
        assert len(gaps) > 1

    def test_asking_for_the_gap_does_not_sleep(self):
        """The Phase 5 scheduler owns the wait so it can survive a restart."""
        human, recorder = make()
        human.gap_between_groups()
        assert recorder.waits == []
