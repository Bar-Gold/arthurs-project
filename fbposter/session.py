"""Attaching to the running Chrome and checking the Facebook session.

Nothing here logs in, and nothing here clicks through a verification screen.
The only question this module answers is "is there a usable Facebook session in
that browser right now?"
"""

from __future__ import annotations

import enum
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Iterator, Mapping, Sequence

from . import config, strings
from .errors import CheckpointError, ConnectionFailed

if TYPE_CHECKING:  # annotations only -- `from __future__ import annotations`
    from playwright.sync_api import BrowserContext

# Playwright is imported inside `attach()`, not here.
#
# It is the single most expensive import in the app at ~830ms, and every path
# that reaches it -- the worker thread and the background group-name lookup --
# is already off the thread drawing the window. Imported at module level it was
# paid at launch instead, by the UI, before the window appeared; deferred, the
# first attach carries it on a thread where nothing is waiting on it and the
# next action is a post with a ten-minute gap in front of it.
#
# Keep it that way: importing `playwright` anywhere at module scope in this
# package puts the cost straight back on startup, wherever it is written.
# tests/test_qtui_performance.py pins that nothing does.


class UrlVerdict(enum.Enum):
    """What the URL Facebook actually served tells us about the session."""

    OK = "ok"
    LOGIN = "login"
    CHECKPOINT = "checkpoint"


@dataclass(frozen=True)
class SessionStatus:
    logged_in: bool
    user_id: str | None
    detail: str


def find_user_id(cookies: Sequence[Mapping[str, Any]]) -> str | None:
    """Return the Facebook user id from the login cookie, or None.

    Preferred over any DOM check: it needs no navigation, survives Facebook's
    obfuscated markup, and does not depend on the interface language.
    """
    for cookie in cookies:
        if cookie.get("name") == strings.LOGIN_COOKIE:
            value = cookie.get("value")
            if value:
                return str(value)
    return None


def classify_url(url: str) -> UrlVerdict:
    """Classify the URL Facebook landed on. Checkpoints are checked first."""
    lowered = url.lower()
    if any(marker in lowered for marker in strings.CHECKPOINT_MARKERS):
        return UrlVerdict.CHECKPOINT
    if any(marker in lowered for marker in strings.LOGIN_MARKERS):
        return UrlVerdict.LOGIN
    return UrlVerdict.OK


@contextmanager
def attach(endpoint: str | None = None) -> Iterator[BrowserContext]:
    """Yield the browser context of the already-running Chrome."""
    endpoint = endpoint or config.cdp_endpoint()
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(endpoint)
        except PlaywrightError as exc:
            raise ConnectionFailed(
                f"Could not open a CDP session at {endpoint}: {exc}"
            ) from exc

        if not browser.contexts:
            raise ConnectionFailed("Chrome accepted the connection but exposed no context.")

        yield browser.contexts[0]

        # Deliberately no browser.close(). This Chrome belongs to the user and
        # holds the Facebook login; leaving the sync_playwright block drops our
        # CDP connection and leaves the browser running.


def verify_session(context: BrowserContext) -> SessionStatus:
    """Report whether the attached browser holds a usable Facebook session."""
    user_id = find_user_id(context.cookies(strings.BASE_URL))
    if user_id is None:
        return SessionStatus(
            logged_in=False,
            user_id=None,
            detail="No Facebook login cookie in this profile -- nobody has logged in yet.",
        )

    # A cookie only proves someone logged in at some point, so confirm the
    # session is still good server-side. Uses its own page rather than
    # contexts[0].pages[0], which may be something the user has open, and never
    # calls bring_to_front -- the window must stay in the background.
    page = context.new_page()
    try:
        page.goto(
            strings.HOME_URL,
            timeout=config.NAV_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        verdict = classify_url(page.url)
        landed = page.url
    finally:
        page.close()

    if verdict is UrlVerdict.CHECKPOINT:
        raise CheckpointError(landed)

    if verdict is UrlVerdict.LOGIN:
        return SessionStatus(
            logged_in=False,
            user_id=user_id,
            detail="Login cookie is present but Facebook redirected to the login page; "
            "the session has expired and needs a manual sign-in.",
        )

    return SessionStatus(
        logged_in=True,
        user_id=user_id,
        detail=f"Logged in as user id {user_id}.",
    )
