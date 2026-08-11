"""Parsing and canonicalising Facebook group URLs.

Pure functions, no browser involved. Phase 4 navigates to the canonical URL
this produces, and the identifier is what makes "the same group added twice"
detectable regardless of how the user pasted it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

_FACEBOOK_HOSTS = frozenset(
    {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.com"}
)

# Paths under /groups/ that are Facebook's own screens rather than a group.
_RESERVED = frozenset({"feed", "discover", "create", "joins", "your_groups"})


@dataclass(frozen=True)
class GroupRef:
    """A Facebook group reduced to the two things the app needs."""

    identifier: str
    url: str

    @property
    def is_numeric(self) -> bool:
        return self.identifier.isdigit()


def parse_group_url(raw: str) -> GroupRef | None:
    """Return a GroupRef for a Facebook group URL, or None if it is not one.

    Accepts the numeric and slug forms, any Facebook host, missing scheme, and
    trailing junk such as /posts/123 or ?ref=share -- all of which turn up when
    a URL is copied out of the browser or shared from the app.
    """
    if not raw or not raw.strip():
        return None

    candidate = raw.strip()
    if "//" not in candidate:
        # Bare "facebook.com/groups/x" has no scheme for urlparse to find.
        candidate = f"https://{candidate}"

    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return None
    if parsed.hostname is None or parsed.hostname.lower() not in _FACEBOOK_HOSTS:
        return None

    segments = [segment for segment in parsed.path.split("/") if segment]
    if len(segments) < 2 or segments[0].lower() != "groups":
        return None

    identifier = segments[1]
    if identifier.lower() in _RESERVED:
        return None

    return GroupRef(
        identifier=identifier,
        url=f"https://www.facebook.com/groups/{identifier}/",
    )


def is_group_url(raw: str) -> bool:
    return parse_group_url(raw) is not None


# Facebook prefixes the tab title with the unread-notification count, e.g.
# "(20+) bar-test | Facebook".
_UNREAD_PREFIX = re.compile(r"^\(\d+\+?\)\s*")
_SITE_SUFFIX = " | Facebook"


def clean_group_title(title: str) -> str:
    """Pull the group's name out of a page title.

    A logged-in group page carries no og:title, so the tab title is the
    fallback source for a name -- but only after the unread count and the site
    suffix are stripped off it.

    Returns "" when nothing usable is left, which callers treat as "keep
    showing the identifier" rather than as an error.
    """
    if not title:
        return ""

    cleaned = _UNREAD_PREFIX.sub("", title.strip())
    if cleaned.endswith(_SITE_SUFFIX):
        cleaned = cleaned[: -len(_SITE_SUFFIX)]

    cleaned = " ".join(cleaned.split())
    # A bare "Facebook" is the site, not a group.
    return "" if cleaned.casefold() == "facebook" else cleaned
