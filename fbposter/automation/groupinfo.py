"""Reading a group's display name off its page.

Purely cosmetic: it turns "2509198906266893" in the Groups list into
"bar-test". Nothing here may raise into the caller, because failing to look up
a name must never stop a group being added or a batch being queued.

A logged-in group page carries no og:title, so the name comes from the page's
h1, with the tab title as a fallback.
"""

from __future__ import annotations

from typing import Any, Protocol

from .. import session
from ..groups import clean_group_title

NAV_TIMEOUT_MS = 45_000
RENDER_MS = 4_000

# The h1 holds the group's name on its own, with none of the tab title's noise.
READ_NAME = """
() => {
  const h1 = document.querySelector('h1');
  return {
    heading: h1 ? (h1.innerText || '').trim() : '',
    title: document.title || '',
  };
}
"""


class GroupNamer(Protocol):
    """One method, so the UI can be tested without a browser."""

    def name_for(self, group_url: str) -> str: ...


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
    """Fetches names from real Chrome, reusing one attachment for a whole sweep.

    Opening a session per group would mean a burst of tabs the moment the
    Groups screen is opened; `names_for` walks them on a single connection
    instead.
    """

    def name_for(self, group_url: str) -> str:
        return self.names_for([group_url]).get(group_url, "")

    def names_for(self, group_urls: list[str]) -> dict[str, str]:
        names: dict[str, str] = {}
        try:
            with session.attach() as context:
                for url in group_urls:
                    page = context.new_page()
                    try:
                        page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
                        page.wait_for_timeout(RENDER_MS)
                        names[url] = read_name(page)
                    except Exception:
                        # One unreachable group must not abandon the rest.
                        names[url] = ""
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
        except Exception:
            # No Chrome, no session: every name simply stays unknown.
            pass
        return names
