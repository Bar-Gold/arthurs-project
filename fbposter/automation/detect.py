"""Working out what page we are actually looking at.

Called before and after every meaningful step. Anything other than OK stops the
whole batch -- the app never waits out a block, never retries through one, and
never clicks a verification screen away.
"""

from __future__ import annotations

import enum

from .. import strings


class PageVerdict(enum.Enum):
    OK = "ok"
    CHECKPOINT = "checkpoint"
    LOGIN = "login"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"

    @property
    def is_ok(self) -> bool:
        return self is PageVerdict.OK


# Ordered by how badly we want to know: a checkpoint matters more than a group
# simply being unavailable, and a rate limit matters more than a login screen
# because it says something about the account's standing.
def classify(url: str, page_text: str = "") -> PageVerdict:
    """Judge a page from its URL and visible text."""
    lowered_url = (url or "").lower()
    if any(marker in lowered_url for marker in strings.CHECKPOINT_MARKERS):
        return PageVerdict.CHECKPOINT

    lowered_text = (page_text or "").lower()
    if any(marker in lowered_text for marker in strings.RATE_LIMIT_MARKERS):
        return PageVerdict.RATE_LIMIT

    if any(marker in lowered_url for marker in strings.LOGIN_MARKERS):
        return PageVerdict.LOGIN

    if any(marker in lowered_text for marker in strings.UNAVAILABLE_MARKERS):
        return PageVerdict.UNAVAILABLE

    return PageVerdict.OK


HALT_MESSAGES = {
    # The connection light does not change when a batch halts, so both of
    # these start at "Check connection": that is what turns the button under
    # it into the one that fixes this.
    PageVerdict.CHECKPOINT: (
        "Facebook is showing a security check, so the batch has stopped. Press "
        "Check connection, then Show me the window, and finish the check "
        "yourself — the app will never click through one."
    ),
    PageVerdict.LOGIN: (
        "Facebook showed its login page: the session has expired, so the batch "
        "has stopped. Press Check connection, then Log in to Facebook, to sign "
        "in again."
    ),
    PageVerdict.RATE_LIMIT: (
        "Facebook is showing a rate-limit or temporary-block warning. Stopping the "
        "batch immediately. Do not post again today, and leave it a while."
    ),
    PageVerdict.UNAVAILABLE: (
        "The group page did not load as expected — it may be unavailable, or "
        "membership may have changed. Stopping rather than guessing."
    ),
}


def halt_message(verdict: PageVerdict) -> str:
    return HALT_MESSAGES.get(verdict, f"Unexpected page state: {verdict.value}")
