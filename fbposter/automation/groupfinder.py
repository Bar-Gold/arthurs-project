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
* A group the account asked to join and is not in yet is on the same page,
  above the list, under a heading of its own: "Pending group requests (1)",
  then "All groups you've joined (46)" (read live on 2026-11-09). Its card is
  drawn like the others, "View group" and all. So a group is taken as joined
  only when a link to it sits below the last heading with a count, which
  needs no word of Facebook's (`pending` in `READ`, `joined_count`).
* All 12 were there on arrival. A longer list loads 20 at a time as it is
  scrolled, read live on 2026-11-03 with 46 groups in English, Hebrew and
  Russian: 20 on arrival, then 40, then 46, each batch a second or more after
  the bottom of the list is reached. So the reader scrolls to the bottom and
  waits there for the next batch, until it has the count from the heading, or
  waiting has stopped bringing any.

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
# A round scrolls to the bottom of the list and waits there for the next
# batch. The reader used to scroll a few hundred pixels and look at once, and
# gave up after three looks with nothing new: a client in about 100 groups got
# the first 20 (2026-11-03). Three rounds of reaching the bottom and waiting
# with nothing new is 45 seconds of patience: a slowed page (CPU x6, 1 Mbps)
# took 30 seconds for one batch live.
MAX_SCROLLS = 200
QUIET_SCROLLS = 3
# With the last round (up to a minute) and the page load, well under the daily
# look's 10-minute clearance (`GROUPS_CHECK_CLEARANCE`), so a
# read never shares Chrome with a post.
TIME_LIMIT_S = 420
# Scrolling to the bottom: at most this many skims (`Humanizer.browse`) a round.
STEPS_PER_ROUND = 15
# Waiting at the bottom for the next batch to arrive.
GROWTH_WAIT_S = 15.0
GROWTH_POLL_S = 0.5

HAS_LIST = """
() => !!document.querySelector('[role="main"] a[href*="/groups/"]')
"""

# `pending`: the link comes before the last heading that has a count in
# brackets, the joined list's, so it is in a section above it (the requests
# waiting for an admin). Invisible marks are dropped first, as `expected_count`
# drops them. Raw, so the backslashes reach the page as they are written.
READ = r"""
() => {
  const main = document.querySelector('[role="main"]');
  const marks = /[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]/g;
  const counted = h => /\(\d[\d,.\s]*\)/.test((h.innerText || '').replace(marks, ''));
  const heads = main ? Array.from(main.querySelectorAll('h1, h2, [role="heading"]')) : [];
  const last = heads.filter(counted).pop() || null;
  const links = main ? Array.from(main.querySelectorAll('a[href*="/groups/"]')).map(a => ({
    href: a.getAttribute('href') || '',
    text: (a.innerText || '').trim(),
    labelled: a.hasAttribute('aria-label'),
    pending: !!last && !!(a.compareDocumentPosition(last) & Node.DOCUMENT_POSITION_FOLLOWING),
  })) : [];
  const headings = heads.map(h => (h.innerText || '').trim());
  const root = document.documentElement;
  return {
    links,
    headings,
    at_bottom: window.scrollY + window.innerHeight >= root.scrollHeight - 300,
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

    @property
    def complete(self) -> bool:
        """Every group the heading counts was read. Without a count there is
        no knowing, so no: replacing the list on a partial read would remove
        groups the account is in."""
        return self.ok and self.expected is not None and len(self.groups) >= self.expected


class GroupFinder(Protocol):
    """One method, so the window can be tested without a browser."""

    def find(self) -> FoundGroups: ...


def _clean(text: str) -> str:
    first_line = strip_invisible(text).strip().split("\n")[0]
    return " ".join(first_line.split())


def groups_from_links(links: list[dict], skip_pending: bool = False) -> list[FoundGroup]:
    """Every group the links point at, once each, in the order they appear.

    A group's name is the text of a link to it that has no `aria-label`; the
    labelled one is the "View group" button, in whatever language the account
    uses. A link to a post or a member page inside a group still names that
    group, and Facebook's own screens (`joins`, `feed`, `discover`) are not
    groups at all (`groups.parse_group_url`).

    With `skip_pending`, a group whose every link is above the joined list
    (`pending`) is one the account asked to join and is not in: left out.
    One link in the list is enough, so a group shown in a section above it
    as well is still joined.
    """
    names: dict[str, str] = {}
    refs: dict[str, GroupRef] = {}
    joined: set[str] = set()
    for link in links:
        href = (link.get("href") or "").strip()
        if href.startswith("/"):
            href = strings.BASE_URL + href
        ref = parse_group_url(href)
        if ref is None:
            continue
        key = ref.identifier.lower()
        if not link.get("pending"):
            joined.add(key)
        refs.setdefault(key, ref)
        names.setdefault(key, "")
        if not names[key] and not link.get("labelled"):
            names[key] = _clean(link.get("text") or "")
    return [
        FoundGroup(refs[key], names[key])
        for key in refs
        if not skip_pending or key in joined
    ]


def expected_count(heading: str) -> int | None:
    """The number in brackets in the list's heading: "(12)", "(1,204)"."""
    match = re.search(r"\((\d[\d,.\s]*)\)", strip_invisible(heading or ""))
    if not match:
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def joined_count(headings: list[str]) -> int | None:
    """The count of the whole list of groups joined: the last number in
    brackets in any heading, the section for requests waiting for an admin
    being above it (2026-11-09). The first heading is not reliably the list's
    own.

    Only when it is also the largest. The list of every group joined counts at
    least as many as any part of it would, so a larger count after it means the
    page is laid out some other way, and then nothing is known: no count, which
    leaves the requests in (`skip_pending` off) and takes no group off the
    list (`FoundGroups.complete`)."""
    counts = [n for n in (expected_count(h) for h in headings) if n is not None]
    if not counts or counts[-1] < max(counts):
        return None
    return counts[-1]


class _Links:
    """Every link read so far, each once, in the order first seen. Kept across
    rounds, because a long list may drop what has scrolled away."""

    def add(self, links: list[dict] | None) -> None:
        for link in links or []:
            key = (link.get("href"), link.get("text"), bool(link.get("labelled")),
                   bool(link.get("pending")))
            self._seen.setdefault(key, link)

    def __init__(self, skip_pending: bool = False) -> None:
        self._seen: dict[tuple, dict] = {}
        self.skip_pending = skip_pending

    def groups(self) -> list[FoundGroup]:
        return groups_from_links(list(self._seen.values()), self.skip_pending)


def _to_the_bottom(page: Any, humanizer: Humanizer, links: _Links) -> None:
    """Skim down until the end of the list is on screen, the way a person
    scrolls to see more. Bounded, for a page whose end never comes."""
    for _ in range(STEPS_PER_ROUND):
        humanizer.browse(page)
        seen = page.evaluate(READ)
        links.add(seen.get("links"))
        if seen.get("at_bottom", True):
            return


def _wait_for_more(page: Any, humanizer: Humanizer, links: _Links, known: int) -> int:
    """At the bottom, give the next batch time to arrive. Returns how many
    groups there are now."""
    count = len(links.groups())
    for _ in range(int(GROWTH_WAIT_S / GROWTH_POLL_S)):
        if count > known:
            break
        humanizer.sleep(GROWTH_POLL_S)
        links.add(page.evaluate(READ).get("links"))
        count = len(links.groups())
    return count


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

    expected = joined_count(seen.get("headings") or [])
    # Requests are told apart by where the joined list's heading is, so only
    # when that heading was found.
    links = _Links(skip_pending=expected is not None)
    links.add(seen.get("links"))
    found = links.groups()
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
        known = len(found)
        _to_the_bottom(page, humanizer, links)
        if _wait_for_more(page, humanizer, links, known) > known:
            quiet = 0
        else:
            quiet += 1
            # A little way back up, as a person does when nothing loads; the
            # next round comes down to the bottom again.
            page.mouse.wheel(0, -humanizer.rng.randint(*humanizer.profile.scroll_delta))
        found = links.groups()
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
