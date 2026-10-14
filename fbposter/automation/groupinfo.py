"""Reading a group's display name and member count off its page.

Purely cosmetic: it turns "2509198906266893" in the Groups list into
"bar-test", with "2 members" beside it. Nothing here may raise into the
caller, because failing to look up a name must never stop a group being added
or a batch being queued.

A logged-in group page carries no og:title, so the name comes from the group
heading, with the tab title as a fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .. import session
from ..groups import clean_group_title
from ..text import strip_invisible

NAV_TIMEOUT_MS = 45_000

# The name is read the moment the group's own heading has text, not after a
# fixed pause. It used to wait a flat four seconds *after* the page's DOM had
# loaded; measured live on 2026-09-25, the heading is there at DOMContentLoaded
# (3.47s, heading at 3.50s), so every lookup spent more than half its time
# waiting for something that had already happened.
HEADING_TIMEOUT_MS = 12_000
# Polled on a timer, never on animation frames: this Chrome sits off-screen,
# and a window Chrome considers hidden may never paint a frame to poll on.
HEADING_POLL_MS = 100
# One more short look after the heading appears, so a heading that is still
# being filled in is not read half-way. Cheap next to the pause it replaces.
SETTLE_MS = 250

HAS_HEADING = """
() => {
  const h1 = document.querySelector('[role="main"] h1');
  return !!(h1 && (h1.innerText || '').trim());
}
"""

# The group's heading holds its name on its own, with none of the tab title's
# noise -- but it has to be the heading *inside the main landmark*.
#
# `document.querySelector('h1')` returns the first h1 in document order, and on
# a real group page that is not the group. Facebook's chat sidebar renders its
# own `h1` ("Chats", and whatever that word is in the account's language) and
# inserts it *ahead* of the group's heading about 1.75s into the load, so a
# group actually called "TRY" was stored as "Chats". Sampled live:
#
#     750ms   h1 count=1   first='TRY'     main='TRY'
#    1750ms   h1 count=2   first='Chats'   main='TRY'
#
# So this is not a race that waiting fixes -- waiting longer makes it worse,
# and the wrong name is stable from then on. It also only happens when the chat
# panel renders, which is why it survived to a client. Scope to [role="main"]
# and the group's own heading is the only one in view.
#
# Deliberately no "any h1" fallback: that is exactly the line that produced the
# bug, and the tab title is a better last resort than an arbitrary heading.
READ_NAME = """
() => {
  const h1 = document.querySelector('[role="main"] h1');
  return {
    heading: h1 ? (h1.innerText || '').trim() : '',
    title: document.title || '',
  };
}
"""


# The member count, as Facebook writes it ("2 members"), read live on
# 2026-10-14 off an English account. It sits in the line under the group's
# name, "Private group · 2 members", and is a link to the group's member list.
# The other link there, "See everyone", carries an aria-label; the count's
# link does not. So it is found by its address and the missing label, never by
# a word of Facebook's, and the text is kept as Facebook wrote it, in the
# account's own language. Only an English account and a two-member group were
# seen live; a large group ("12K members") is assumed to look the same.
READ_MEMBERS = """
() => {
  const main = document.querySelector('[role="main"]');
  if (!main) return '';
  for (const a of main.querySelectorAll('a[href*="/members"]')) {
    if (a.hasAttribute('aria-label')) continue;
    const text = (a.innerText || '').trim();
    if (text) return text;
  }
  return '';
}
"""
HAS_MEMBERS = "() => !!(" + READ_MEMBERS.strip() + ")()"
# It arrived with the heading when read live; this only covers a slow page.
MEMBERS_TIMEOUT_MS = 3_000


@dataclass(frozen=True)
class GroupInfo:
    """What one visit to a group's page found. Either may be empty."""

    name: str
    members: str = ""


class GroupNamer(Protocol):
    """So the UI can be tested without a browser."""

    def name_for(self, group_url: str) -> str: ...

    def info_for(self, group_urls: list[str]) -> dict[str, GroupInfo]: ...


def wait_for_heading(page: Any) -> bool:
    """Wait until the group's heading has text; False if it never did.

    Scoped like READ_NAME, which is what makes reading early safe: the heading
    that arrives late and wrong ("Chats") is outside [role="main"], so it can
    be neither what this waits for nor what READ_NAME then reads.
    """
    try:
        page.wait_for_function(
            HAS_HEADING, polling=HEADING_POLL_MS, timeout=HEADING_TIMEOUT_MS
        )
    except Exception:
        # No heading in time: read_name falls back to the tab title.
        return False
    page.wait_for_timeout(SETTLE_MS)
    return True


def read_members(page: Any) -> str:
    """The member count on an already-loaded group page, or "". Never raises."""
    try:
        page.wait_for_function(
            HAS_MEMBERS, polling=HEADING_POLL_MS, timeout=MEMBERS_TIMEOUT_MS
        )
    except Exception:
        pass  # read anyway: an empty answer is the honest one
    try:
        text = page.evaluate(READ_MEMBERS)
    except Exception:
        return ""
    if not isinstance(text, str):
        return ""
    return " ".join(strip_invisible(text).split())


def read_name(page: Any) -> str:
    """Extract a name from an already-loaded group page."""
    try:
        found = page.evaluate(READ_NAME)
    except Exception:
        return ""

    heading = " ".join((found.get("heading") or "").split())
    if heading:
        return heading
    return clean_group_title(found.get("title") or "")


class LiveGroupNamer:
    """Fetches names from real Chrome: one connection and one tab per sweep.

    The tab goes from group to group and is closed when the sweep ends (the
    user's request, 2026-10-14). It used to be a new tab for every group. One
    tab does not mean fewer visits to Facebook, since every group's count is
    on its own page, but it is how a person would go through them.
    """

    def name_for(self, group_url: str) -> str:
        return self.names_for([group_url]).get(group_url, "")

    def names_for(self, group_urls: list[str]) -> dict[str, str]:
        return {url: info.name for url, info in self.info_for(group_urls).items()}

    def info_for(self, group_urls: list[str]) -> dict[str, GroupInfo]:
        """Name and member count for each group whose page loaded.

        A group whose page could not be loaded is left out, so the caller can
        tell "not tried" from "tried, and the page had no count".
        """
        found: dict[str, GroupInfo] = {}
        try:
            with session.attach() as context:
                page = None
                try:
                    for url in group_urls:
                        if page is None:
                            page = context.new_page()
                        try:
                            # "commit", not "domcontentloaded": the heading is
                            # watched for from the first byte, and read as
                            # soon as it is there. A navigation that has
                            # committed has replaced the last group's page,
                            # so nothing of it can be read by mistake.
                            page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="commit")
                            wait_for_heading(page)
                            found[url] = GroupInfo(read_name(page), read_members(page))
                        except Exception:
                            # One unreachable group must not abandon the rest,
                            # and the tab it failed in may be in any state, so
                            # the next group gets a fresh one.
                            _close(page)
                            page = None
                finally:
                    if page is not None:
                        _close(page)
        except Exception:
            # No Chrome, no session: everything simply stays unknown.
            pass
        return found


def _close(page: Any) -> None:
    try:
        page.close()
    except Exception:
        pass
