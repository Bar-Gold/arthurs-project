"""Making the automation behave like a person using the site.

This is a safety requirement rather than a nicety. Zero hover time and
machine-regular pacing are signals that separate a script from a member, and
the account is what is at stake. Text is pasted, as an agent pastes a listing
(the user's choice, 2026-11-13); it used to be typed key by key.

`rng` and `sleep` are injected so tests are deterministic and instant while
production gets real randomness and real waiting.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


class _Typable(Protocol):  # the slice of Playwright's keyboard we use
    def insert_text(self, text: str) -> None: ...

    def press(self, key: str) -> None: ...


@dataclass(frozen=True)
class HumanProfile:
    """Timing ranges, all in milliseconds unless the name says seconds."""

    # Pasting, not typing (the user's choice, 2026-11-13): an agent copies the
    # listing from somewhere and pastes it. A moment to reach for Ctrl+V, and
    # after a whole post, a moment to look it over.
    paste_reach_ms: tuple[float, float] = (600, 1800)
    paste_review_ms: tuple[float, float] = (2000, 5000)

    hover_ms: tuple[float, float] = (120, 420)
    click_settle_ms: tuple[float, float] = (350, 1100)

    # Arriving on a page and reading a little before doing anything.
    read_pause_ms: tuple[float, float] = (900, 2600)
    scroll_steps: tuple[int, int] = (2, 5)
    scroll_delta: tuple[int, int] = (240, 620)
    scroll_pause_ms: tuple[float, float] = (280, 950)

    # From the start of one post to the start of the next: 2-4 minutes (the
    # user's choice, 2026-10-18; it was 10-25 between posts). Since 2026-11-13
    # the post's own time is inside it, not added to it. Guides put the floor
    # at 2-3 minutes: closer than that reads as automation. Since 2026-11-03
    # the client picks the speed on Settings (`fbposter/pace.py`), and the
    # worker passes it in; this is the range when nothing is passed.
    group_gap_seconds: tuple[float, float] = (120, 240)


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

    def gap_between_groups(self, span: tuple[float, float] | None = None) -> float:
        """Seconds to wait before the next group. Not slept here -- the Phase 5
        scheduler owns that wait so it can be interrupted and survive a restart.

        `span` is the client's speed from Settings (`pace.gap_span`); without
        one, the profile's own range.
        """
        low, high = span or self.profile.group_gap_seconds
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

    def paste_text(self, keyboard: _Typable, text: str) -> int:
        """Put the text in the way a person pastes it: all at once, after a
        moment to reach for the keys (the user's choice, 2026-11-13).

        Each line goes in whole and Enter starts the next, which is how the
        post box got a line break when the text was typed key by key. Never
        the real clipboard: that would overwrite what the user has copied
        while they work, and a copy made in between would be posted.
        Returns the number of characters put in.
        """
        if not text:
            return 0
        self._pause_ms(self.profile.paste_reach_ms)
        for index, line in enumerate(text.replace("\r\n", "\n").split("\n")):
            if index:
                keyboard.press("Enter")
            if line:
                keyboard.insert_text(line)
        return len(text)

    def review_pause(self) -> float:
        """Looking the post over once it is in."""
        return self._pause_ms(self.profile.paste_review_ms)
