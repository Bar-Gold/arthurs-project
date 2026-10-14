"""Finding every group the logged-in account belongs to, so nobody pastes links.

Read off Facebook's own "Your groups" page (`strings.YOUR_GROUPS_URL`). Read
only: it loads that one page, scrolls it the way a person would, and reads the
links. It clicks nothing and types nothing.

What the page looks like, read live on 2026-10-13 (an English account in 12
groups, one of them managed):

* Inside `[role="main"]`, one heading with the count in brackets, "All groups
  you've joined (12)", and every group under it, the managed one included.
* Each group has three links to the same address: its picture (no text), its
  name, and a "View group" button. The button carries an `aria-label` and the
  name link does not, which tells them apart in any language without a single
  word of Facebook's.
* The address is `https://www.facebook.com/groups/<number>/`, or the group's
  short name for one that has one (`.../groups/SneakerHeadsInIsrael/`).
* All 12 were there on arrival. A longer list is assumed to load as it is
  scrolled, so the reader scrolls until it has the count from the heading, or
  nothing new appears.

Nothing here may raise into the caller. A failure comes back as a
`FoundGroups` with `error` set, and nothing is added.
"""

from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .. import session, strings
from ..groups import GroupRef, parse_group_url
from ..text import strip_invisible
from . import detect
from .humanize import Humanizer

NAV_TIMEOUT_MS = 45_000
# Waiting for the list to appear at all.
LIST_TIMEOUT_MS = 15_000
LIST_POLL_MS = 200
# Bounds on the scrolling, so a client in hundreds of groups, or a page that
# never stops growing, costs minutes at most and never an open-ended session.
MAX_SCROLLS = 60
QUIET_SCROLLS = 3
TIME_LIMIT_S = 180

HAS_LIST = """
() => !!document.querySelector('[role="main"] a[href*="/groups/"]')
"""

READ = """
() => {
  const main = document.querySelector('[role="main"]');
  const links = main ? Array.from(main.querySelectorAll('a[href*="/groups/"]')).map(a => ({
    href: a.getAttribute('href') || '',
    text: (a.innerText || '').trim(),
    labelled: a.hasAttribute('aria-label'),
  })) : [];
  const heading = main ? main.querySelector('h1, h2, [role="heading"]') : null;
  return {
    links,
    heading: heading ? (heading.innerText || '').trim() : '',
    text: document.body ? (document.body.innerText || '').slice(0, 5000) : '',
  };
}
"""

# Said instead of halting a batch: nothing was posted, nothing was added.
PAGE_PROBLEMS = {
    detect.PageVerdict.CHECKPOINT: (
        "Facebook is showing a security check, so your groups were not read. "
        "Press Check connection, then Show me the window, and finish the check "
        "yourself. The app will never click through one."
    ),
    detect.PageVerdict.LOGIN: (
        "Facebook showed its login page, so your groups were not read. Press "
        "Check connection, then Log in to Facebook."
    ),
    detect.PageVerdict.RATE_LIMIT: (
        "Facebook is showing a temporary-block warning, so your groups were not "
        "read. Leave it a while before trying again."
    ),
    detect.PageVerdict.UNAVAILABLE: (
        "Facebook's list of your groups did not load as expected, so no groups "
        "were added. Try again in a while."
    ),
}


@dataclass(frozen=True)
class FoundGroup:
    ref: GroupRef
    name: str


@dataclass(frozen=True)
class FoundGroups:
    """What one look at the page found. `error` set means nothing usable."""

    groups: list[FoundGroup] = field(default_factory=list)
    # The count from the page's heading, when there was one to read.
    expected: int | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


class GroupFinder(Protocol):
    """One method, so the window can be tested without a browser."""

    def find(self) -> FoundGroups: ...


def _clean(text: str) -> str:
    first_line = strip_invisible(text).strip().split("\n")[0]
    return " ".join(first_line.split())


def groups_from_links(links: list[dict]) -> list[FoundGroup]:
    """Every group the links point at, once each, in the order they appear.

    A group's name is the text of a link to it that has no `aria-label`; the
    labelled one is the "View group" button, in whatever language the account
    uses. A link to a post or a member page inside a group still names that
    group, and Facebook's own screens (`joins`, `feed`, `discover`) are not
    groups at all (`groups.parse_group_url`).
    """
    names: dict[str, str] = {}
    refs: dict[str, GroupRef] = {}
    for link in links:
        href = (link.get("href") or "").strip()
        if href.startswith("/"):
            href = strings.BASE_URL + href
        ref = parse_group_url(href)
        if ref is None:
            continue
        key = ref.identifier.lower()
        refs.setdefault(key, ref)
        names.setdefault(key, "")
        if not names[key] and not link.get("labelled"):
            names[key] = _clean(link.get("text") or "")
    return [FoundGroup(refs[key], names[key]) for key in refs]


def expected_count(heading: str) -> int | None:
    """The number in brackets in the list's heading: "(12)", "(1,204)"."""
    match = re.search(r"\((\d[\d,.\s]*)\)", heading or "")
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def read_your_groups(
    page: Any,
    humanizer: Humanizer | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> FoundGroups:
    """Load the page and read every group off it. The page is the caller's."""
    humanizer = humanizer or Humanizer(rng=random.Random())
    page.goto(strings.YOUR_GROUPS_URL, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
    try:
        page.wait_for_function(HAS_LIST, polling=LIST_POLL_MS, timeout=LIST_TIMEOUT_MS)
    except Exception:
        pass  # judged below: a page with no list is either empty or wrong
    seen = page.evaluate(READ)
    verdict = detect.classify(page.url, seen.get("text", ""))
    if verdict is not detect.PageVerdict.OK:
        return FoundGroups(error=PAGE_PROBLEMS.get(verdict, detect.halt_message(verdict)))

    links = list(seen.get("links") or [])
    found = groups_from_links(links)
    expected = expected_count(seen.get("heading", ""))
    if not found and expected is None:
        # No list and no heading: not the page it should be. Adding nothing
        # is safe; calling it "no groups" would not be.
        return FoundGroups(error=PAGE_PROBLEMS[detect.PageVerdict.UNAVAILABLE])

    started = clock()
    quiet = 0
    for _ in range(MAX_SCROLLS):
        if expected is not None and len(found) >= expected:
            break
        if quiet >= QUIET_SCROLLS or clock() - started > TIME_LIMIT_S:
            break
        humanizer.browse(page)
        # Kept across rounds: a long list may drop what has scrolled away.
        links += page.evaluate(READ).get("links") or []
        more = groups_from_links(links)
        quiet = quiet + 1 if len(more) == len(found) else 0
        found = more
    return FoundGroups(groups=found, expected=expected)


class LiveGroupFinder:
    """Reads the groups through the app's own Chrome, on one tab it closes."""

    def find(self) -> FoundGroups:
        try:
            with session.attach() as context:
                page = context.new_page()
                try:
                    return read_your_groups(page)
                finally:
                    try:
                        page.close()
                    except Exception:
                        pass
        except Exception as exc:  # never into the window
            return FoundGroups(error=f"Your groups could not be read from Facebook: {exc}")
