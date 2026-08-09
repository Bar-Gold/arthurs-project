"""Exception types shared across the app.

Everything raised deliberately by this package derives from FBPosterError, so
callers can distinguish an expected failure (Chrome is not running) from a bug.
"""

from __future__ import annotations


class FBPosterError(Exception):
    """Base class for every error this application raises on purpose."""


class ChromeNotFoundError(FBPosterError):
    """chrome.exe could not be located in any of the known install paths."""


class ChromeLaunchError(FBPosterError):
    """Chrome was started but never exposed a usable debugging port."""


class ChromeNotRunningError(FBPosterError):
    """Nothing is listening on the debugging port, so there is nothing to attach to."""


class ConnectionFailed(FBPosterError):
    """The debugging port answered but a CDP session could not be established."""


class CheckpointError(FBPosterError):
    """Facebook served a checkpoint, challenge or verification screen.

    This is never handled automatically. Clicking through a checkpoint is one of
    the strongest signals that an account is automated, so the app always stops
    and hands control back to the user.
    """

    def __init__(self, url: str) -> None:
        super().__init__(
            f"Facebook served a checkpoint or verification screen: {url}\n"
            "Stopping. Open Chrome and resolve it yourself -- the app will never "
            "click through one of these."
        )
        self.url = url
