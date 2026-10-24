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


class PageLoadFailed(FBPosterError):
    """The group page itself would not load: the internet dropped, or the
    machine had just woken up and its network was not back yet.

    Raised by the page load, before any post box exists, so nothing can have
    been typed or posted. That makes it the second failure, after
    ConnectionFailed, that is safe to wait out and try again.
    """


class GroupUnavailable(FBPosterError):
    """Facebook says this group is not available, and it has no post box.

    The account may have left it or been removed, or the group was deleted.
    Raised before anything is typed. Unlike a checkpoint or a rate-limit
    warning it says nothing about the account, so the batch skips this one
    group and carries on with the rest.
    """


class InvalidGroupURL(FBPosterError):
    """The text given is not a Facebook group URL."""


class DuplicateGroup(FBPosterError):
    """That group is already stored, possibly under a different URL form."""


class AutomationHalted(FBPosterError):
    """The page was not what we expected, so the batch stopped.

    Carries the verdict so the caller can tell a checkpoint from a rate limit.
    There is deliberately no retry path: retrying through a block is how an
    account gets restricted rather than warned.
    """

    def __init__(self, verdict, message: str, may_have_posted: bool = False) -> None:
        super().__init__(message)
        self.verdict = verdict
        # True when the page turned bad after Post was clicked: the batch
        # still stops, but the Queue must not say the post did not go out.
        self.may_have_posted = may_have_posted


class UploadStalled(FBPosterError):
    """A picture or video stopped uploading before Post was clicked.

    Raised before the click, so nothing was posted and saying so is true.
    """


class ComposerNotFound(FBPosterError):
    """A required composer element could not be located.

    Usually means Facebook changed its markup or the interface language is no
    longer English. Never treated as something to work around.
    """


class PostNotVerified(FBPosterError):
    """The post was submitted but could not be found afterwards.

    Not assumed to be a failure -- it may well have gone out -- so the caller
    must not simply retry it. `may_have_posted` is False only when the evidence
    says it did not: the post window never closed and the post is not there.
    """

    def __init__(self, message: str, may_have_posted: bool = True) -> None:
        super().__init__(message)
        self.may_have_posted = may_have_posted


class CheckpointError(FBPosterError):
    """Facebook served a checkpoint, challenge or verification screen.

    This is never handled automatically. Clicking through a checkpoint is one of
    the strongest signals that an account is automated, so the app always stops
    and hands control back to the user.
    """

    def __init__(self, url: str) -> None:
        super().__init__(
            "Facebook is asking you to confirm it is you. Press Show me the "
            "window and finish the check yourself. The app will never click "
            "through one of these."
        )
        self.url = url
