"""Keeping the background Chrome running: the decisions, not the doing.

The app posts through a Chrome it starts itself, off-screen. That Chrome can go
away while the app stays open -- the user closes it from the taskbar, it
crashes, Windows Update restarts it -- and until now nothing noticed: the pill
kept whatever it last said, and every post waited on a connection that was
never coming back until someone pressed Start Chrome.

The window now looks every `WATCH_EVERY` and starts it again. This module is
the policy for that, pure like `guards.py` and `onboarding.py`: `qtui/app.py`
does the looking and the launching, and asks this what to do.

The one thing the policy exists to prevent is a restart loop. When Chrome will
not come back -- most often because a Chrome window is already open on the same
profile *without* the debugging port, so every launch just opens another window
in it -- restarting every twenty seconds would pile up windows for ever. So a
failed restart backs off, and after `MAX_ATTEMPTS` the app stops trying and
says so; the user's own Start Chrome press, or Chrome being seen alive again,
starts the count afresh.
"""

from __future__ import annotations

from dataclasses import dataclass

# How often the window asks whether Chrome is still there. The question is one
# HTTP request to the local debugging port -- ~14ms, and never a Facebook page
# -- so it is cheap to ask often, and the sooner it is asked the less a post
# is kept waiting.
WATCH_EVERY_S = 20

# How long to wait before trying again after each failed restart. The first
# attempt is immediate: Chrome having closed is the common case, and it comes
# straight back.
BACKOFF_S = (60, 5 * 60)
MAX_ATTEMPTS = len(BACKOFF_S) + 1


@dataclass
class ChromeKeeper:
    """Whether to restart Chrome now. Times are `time.monotonic()` seconds."""

    failures: int = 0
    retry_at: float | None = None

    @property
    def gave_up(self) -> bool:
        return self.failures >= MAX_ATTEMPTS

    def may_restart(self, now: float) -> bool:
        if self.gave_up:
            return False
        return self.retry_at is None or now >= self.retry_at

    def seen_running(self) -> None:
        """Chrome is up, whoever started it: any earlier failure is history."""
        self.failures = 0
        self.retry_at = None

    def restarted(self) -> None:
        self.seen_running()

    def failed(self, now: float) -> None:
        self.failures += 1
        if not self.gave_up:
            self.retry_at = now + BACKOFF_S[self.failures - 1]

    def wait_text(self, now: float) -> str:
        """ "in a minute" / "in 5 minutes" -- for the pill, while backing off."""
        if self.retry_at is None:
            return "shortly"
        minutes = max(1, round((self.retry_at - now) / 60))
        return "in a minute" if minutes == 1 else f"in {minutes} minutes"
