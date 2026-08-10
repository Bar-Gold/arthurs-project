"""Making the automation behave like a person using the site.

This is a safety requirement rather than a nicety. Instant field fills, zero
hover time and machine-regular pacing are exactly the signals that separate a
script from a member, and the account is what is at stake.

`rng` and `sleep` are injected so tests are deterministic and instant while
production gets real randomness and real waiting.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


class _Typable(Protocol):  # the slice of Playwright's keyboard we use
    def type(self, text: str) -> None: ...


@dataclass(frozen=True)
class HumanProfile:
    """Timing ranges, all in milliseconds unless the name says seconds."""

    # Per keystroke. Real typing is uneven, so this is a wide range rather than
    # a constant delay -- a fixed 100ms per character is its own fingerprint.
    keystroke_ms: tuple[float, float] = (55, 185)
    # Now and then a person stops mid-sentence.
    word_pause_chance: float = 0.09
    word_pause_ms: tuple[float, float] = (220, 900)

    hover_ms: tuple[float, float] = (120, 420)
    click_settle_ms: tuple[float, float] = (350, 1100)

    # Arriving on a page and reading a little before doing anything.
    read_pause_ms: tuple[float, float] = (900, 2600)
    scroll_steps: tuple[int, int] = (2, 5)
    scroll_delta: tuple[int, int] = (240, 620)
    scroll_pause_ms: tuple[float, float] = (280, 950)

    # Between groups in a batch: 10-25 minutes, per README section 7. At 5-7
    # groups this makes a batch take 1-3 hours, which is the intended cost.
    group_gap_seconds: tuple[float, float] = (600, 1500)


@dataclass
class Humanizer:
    profile: HumanProfile = field(default_factory=HumanProfile)
    rng: random.Random = field(default_factory=random.Random)
    sleep: Callable[[float], None] = time.sleep

    # -- pacing ------------------------------------------------------------
    def _pause_ms(self, span: tuple[float, float]) -> float:
        low, high = span
        delay = self.rng.uniform(low, high)
        self.sleep(delay / 1000)
        return delay

    def read_pause(self) -> float:
        return self._pause_ms(self.profile.read_pause_ms)

    def settle(self) -> float:
        return self._pause_ms(self.profile.click_settle_ms)

    def gap_between_groups(self) -> float:
        """Seconds to wait before the next group. Not slept here -- the Phase 5
        scheduler owns that wait so it can be interrupted and survive a restart.
        """
        low, high = self.profile.group_gap_seconds
        return self.rng.uniform(low, high)

    # -- interaction -------------------------------------------------------
    def browse(self, page) -> int:
        """Scroll around a bit on arrival, the way a person skims a feed."""
        steps = self.rng.randint(*self.profile.scroll_steps)
        for _ in range(steps):
            delta = self.rng.randint(*self.profile.scroll_delta)
            page.mouse.wheel(0, delta)
            self._pause_ms(self.profile.scroll_pause_ms)
        return steps

    def hover_then_click(self, locator) -> None:
        """Never click something the mouse was never over."""
        locator.hover()
        self._pause_ms(self.profile.hover_ms)
        locator.click()
        self.settle()

    def type_text(self, keyboard: _Typable, text: str) -> int:
        """Type character by character with variance.

        Deliberately not `fill()` or a single `type(text)`: pasting a whole
        block in 0ms is the clearest automation tell there is. Returns the
        number of characters typed.
        """
        for index, character in enumerate(text):
            keyboard.type(character)
            self._pause_ms(self.profile.keystroke_ms)

            # A pause after a word, sometimes -- not after every one.
            following = text[index + 1] if index + 1 < len(text) else ""
            if following == " " and self.rng.random() < self.profile.word_pause_chance:
                self._pause_ms(self.profile.word_pause_ms)

        return len(text)
