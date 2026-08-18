"""Posting to one group.

The flow is: arrive, look around, open the composer, type, attach, publish,
then confirm it actually appeared. Between every meaningful step the page is
re-classified, and anything unexpected halts the batch instead of pressing on.

Two rules from CLAUDE.md are load-bearing here:
  * never bring the window to the front -- the user is working
  * never open the OS file picker -- media goes in via set_input_files
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .. import strings
from ..errors import AutomationHalted, ComposerNotFound, PostNotVerified
from ..text import strip_invisible
from .detect import PageVerdict, classify, halt_message
from .humanize import Humanizer

NAV_TIMEOUT_MS = 45_000
# Generous on purpose. These were 15s and a slow connection blew straight
# through them on a post that was otherwise fine.
ACTION_TIMEOUT_MS = 30_000
# Publishing is the slowest step: Facebook has to accept the text and any
# uploads before it closes the composer.
PUBLISH_TIMEOUT_MS = 60_000
VERIFY_TIMEOUT_MS = 20_000
# A brief look at the feed we are already on before bothering to reload it.
FIRST_LOOK_TIMEOUT_MS = 8_000
# Time for the reloaded feed to render before searching it.
FEED_SETTLE_MS = 6_000
# The feed is virtualised, so look several times, scrolling between tries.
VERIFY_ATTEMPTS = 5
VERIFY_POLL_MS = 3_000
VERIFY_SCROLL_PX = 700
# How often to re-check while waiting for the single-page app to render.
RENDER_POLL_MS = 500

# Long enough to be unique to this post, short enough to survive Facebook
# reflowing or truncating the text in the feed.
SNIPPET_MAX_CHARS = 60
SNIPPET_MIN_CHARS = 12


@dataclass(frozen=True)
class PostRequest:
    group_url: str
    body: str
    media_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class PostOutcome:
    posted: bool
    detail: str
    post_url: str = ""
    dry_run: bool = False
    verified: bool = False
    # Submitted, but the group holds posts for an admin to approve, so it is
    # not in the feed yet. Neither a success to celebrate nor a failure.
    pending: bool = False


@dataclass
class ProbeReport:
    """What a read-only selector check found. Nothing is typed or submitted."""

    group_url: str
    page_ok: bool = False
    composer_trigger: bool = False
    dialog: bool = False
    textbox: bool = False
    photo_button: bool = False
    post_button: bool = False
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(
            (
                self.page_ok,
                self.composer_trigger,
                self.dialog,
                self.textbox,
                self.photo_button,
                self.post_button,
            )
        )


def distinctive_snippet(body: str) -> str:
    """A slice of the post to look for when verifying it appeared.

    Uses the user's own words rather than any Facebook string, so verification
    does not depend on the interface language. Prefers the longest line: a post
    often opens with a short greeting that would match half the feed.
    """
    # Facebook drops invisible characters when it renders the post, so a
    # snippet that still contains one is searched for and never found -- which
    # reports a post that went out fine as failed, and halts the batch.
    body = strip_invisible(body)
    lines = [" ".join(line.split()) for line in body.splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return ""

    candidate = max(lines, key=len)
    if len(candidate) < SNIPPET_MIN_CHARS:
        candidate = " ".join(lines)
    return candidate[:SNIPPET_MAX_CHARS].strip()


class GroupPoster:
    def __init__(
        self,
        page: Any,
        humanizer: Humanizer | None = None,
        *,
        dry_run: bool = False,
    ) -> None:
        self.page = page
        self.human = humanizer or Humanizer()
        self.dry_run = dry_run

    # -- guards ------------------------------------------------------------
    def _page_text(self) -> str:
        try:
            return self.page.inner_text("body")
        except Exception:
            # A page mid-navigation can refuse; an empty string simply means
            # "no anomaly markers seen", and the next guard will catch it.
            return ""

    def guard(self) -> None:
        """Halt unless the page is what we expect. Called between every step."""
        verdict = classify(self.page.url, self._page_text())
        if not verdict.is_ok:
            raise AutomationHalted(verdict, halt_message(verdict))

    # -- element lookup ----------------------------------------------------
    def _first_present(self, candidates: Sequence[Any]) -> Any | None:
        """Return the first locator that actually matches something.

        Facebook words the composer differently between builds and group types,
        so each element is tried against a list of accessible names rather than
        one hardcoded string.
        """
        for locator in candidates:
            try:
                if locator.count() > 0:
                    return locator.first
            except Exception:
                continue
        return None

    def _wait_for_any(self, candidates: Sequence[Any], timeout_ms: int = ACTION_TIMEOUT_MS) -> Any | None:
        """Poll a set of candidates until one exists.

        Facebook is a single-page app: the group feed and its composer render
        well after DOMContentLoaded, so a one-shot `count()` finds nothing on a
        page that is perfectly fine a second later.
        """
        waited = 0
        while True:
            found = self._first_present(candidates)
            if found is not None:
                return found
            if waited >= timeout_ms:
                return None
            self.page.wait_for_timeout(RENDER_POLL_MS)
            waited += RENDER_POLL_MS

    def _composer_trigger_candidates(self) -> list[Any]:
        candidates = []
        for name in strings.COMPOSER_TRIGGERS:
            candidates.append(self.page.get_by_role("button", name=name, exact=False))
        # The group composer trigger carries no aria-label, so its visible text
        # is the only thing to match on.
        for name in strings.COMPOSER_TRIGGERS:
            candidates.append(self.page.get_by_text(name, exact=False))
        return candidates

    def _find_composer_trigger(self) -> Any | None:
        return self._wait_for_any(self._composer_trigger_candidates())

    def _dialog(self) -> Any:
        return self.page.get_by_role("dialog").first

    def _find_textbox(self, dialog: Any) -> Any | None:
        # The field exposes only an aria-placeholder, not an accessible name,
        # so the reliable handle is "the dialog's textbox".
        candidates = [dialog.get_by_role("textbox")]
        for name in strings.COMPOSER_TEXTBOX:
            candidates.append(dialog.get_by_role("textbox", name=name, exact=False))
        return self._wait_for_any(candidates)

    def _find_photo_button(self, dialog: Any) -> Any | None:
        return self._wait_for_any(
            [
                dialog.get_by_role("button", name=name, exact=False)
                for name in strings.PHOTO_VIDEO_BUTTONS
            ]
        )

    def _find_post_button(self, dialog: Any) -> Any | None:
        return self._wait_for_any(
            [
                dialog.get_by_role("button", name=name, exact=True)
                for name in strings.POST_BUTTONS
            ]
        )

    # -- steps -------------------------------------------------------------
    def open_group(self, url: str) -> None:
        self.page.goto(url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        self.guard()
        self.human.browse(self.page)
        self.human.read_pause()

    def open_composer(self) -> Any:
        trigger = self._find_composer_trigger()
        if trigger is None:
            raise ComposerNotFound(
                "Could not find the group composer. Facebook may have changed its "
                "markup, or the interface language may no longer be English."
            )

        self.human.hover_then_click(trigger)
        dialog = self._dialog()
        dialog.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
        self.guard()
        return dialog

    def type_body(self, dialog: Any, body: str) -> None:
        textbox = self._find_textbox(dialog)
        if textbox is None:
            raise ComposerNotFound("The composer opened but has no text field.")

        textbox.click()

        # Clear whatever is there before typing. Facebook was observed to drop
        # the draft when the composer is closed, but that is its behaviour
        # today for short text -- it may keep one for a longer post or when
        # media was attached. Typing on top of a leftover draft would post
        # something the user never wrote, so this does not rely on it.
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Delete")

        self.human.type_text(self.page.keyboard, body)

    def attach_media(self, dialog: Any, paths: Sequence[Path]) -> None:
        """Attach images by writing straight to the file input.

        **Do not click the Photo/video button.** Clicking it opens the native
        Windows "Open" dialog, which is modal, steals focus, and was observed
        being left on screen after a run -- breaking the one hard promise this
        app makes, that the user can keep working while it posts.

        The input element is already in the composer's DOM before that button
        is touched, so setting files on it directly is both safer and simpler.
        All images go in one call; the input carries `multiple`.
        """
        if not paths:
            return

        # An attribute selector on a standard element, not an obfuscated class
        # name -- this is the one place a CSS selector is the right tool.
        file_input = dialog.locator('input[type="file"]').first
        file_input.set_input_files([str(path) for path in paths])
        self.human.settle()

    def publish(self, dialog: Any) -> bool:
        """Click Post. Returns whether the composer actually closed.

        A composer that has not closed is a hint, not a verdict. On a slow
        connection it can linger after a post that went out perfectly well, so
        this reports what it saw and lets verification decide. Treating the
        timeout as failure is what turned a slow moment into a halted batch.
        """
        button = self._find_post_button(dialog)
        if button is None:
            raise ComposerNotFound("The composer has no Post button.")

        self.human.hover_then_click(button)
        try:
            dialog.wait_for(state="detached", timeout=PUBLISH_TIMEOUT_MS)
            closed = True
        except Exception:
            closed = False

        self.guard()
        return closed

    def _snippet_visible(self, snippet: str, timeout_ms: int) -> bool:
        try:
            self.page.get_by_text(snippet, exact=False).first.wait_for(
                state="visible", timeout=timeout_ms
            )
            return True
        except Exception:
            pass

        # Fall back to the page's own text. The locator needs the post to be
        # mounted as an element; this catches it wherever it sits.
        try:
            return snippet.casefold() in self.page.inner_text("body").casefold()
        except Exception:
            return False

    def _hunt_for_snippet(self, snippet: str) -> bool:
        """Look for the post while nudging the feed into rendering more of it.

        Facebook virtualises the group feed: which posts exist in the DOM at
        any moment varies, and a freshly published one is not reliably among
        them. Two live posts were reported unverified this way and turned out
        to have gone out perfectly well, so this scrolls and re-checks instead
        of asking once.
        """
        for attempt in range(VERIFY_ATTEMPTS):
            if self._snippet_visible(snippet, VERIFY_POLL_MS):
                return True
            try:
                self.page.mouse.wheel(0, VERIFY_SCROLL_PX)
            except Exception:
                pass
            self.page.wait_for_timeout(VERIFY_POLL_MS)
        return False

    def verify(self, body: str, group_url: str = "") -> bool:
        """Confirm the post actually appeared rather than trusting the click.

        Facebook does not reliably drop a new post into the feed you are
        already looking at -- the first live run published successfully and
        still failed this check, because the feed had not updated. So if the
        post is not visible, reload the group and look again, which is what a
        person would do.

        A false negative here is expensive: it raises PostNotVerified, and a
        caller that retried on that would post twice.
        """
        snippet = distinctive_snippet(body)
        if not snippet:
            return False

        if self._snippet_visible(snippet, FIRST_LOOK_TIMEOUT_MS):
            return True

        if not group_url:
            return False

        self.page.goto(group_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        self.page.wait_for_timeout(FEED_SETTLE_MS)
        return self._hunt_for_snippet(snippet)

    def awaiting_approval(self) -> bool:
        """Whether this group is holding a post of ours for an admin.

        Read off the group page, which shows the author a "Pending admin
        approval" banner. The page has already been reloaded by verify(), so
        this reads what is on screen rather than navigating again.
        """
        text = self._page_text().lower()
        return any(marker in text for marker in strings.PENDING_APPROVAL_MARKERS)

    def _pending_outcome(self) -> PostOutcome:
        return PostOutcome(
            posted=True,
            verified=False,
            pending=True,
            detail=(
                "Submitted, and the group is holding it for an admin to "
                "approve. It is not visible yet and may still be declined."
            ),
        )

    def _is_queued(self, request: PostRequest) -> bool:
        """Is *this* post the one the group is holding?

        Asked only when the banner is up and the post appeared to be visible.
        The group's own pending list is the one place that distinguishes "your
        queued post, shown to you" from "published", which the feed does not.

        Anything unexpected answers no: the post was seen, so the existing
        verdict already errs the right way, and turning a good post into a
        halted batch over a failed extra check would be a poor trade.
        """
        try:
            return self.pending_verdict(request.group_url, request.body) == "pending"
        except Exception:
            return False

    def pending_verdict(self, group_url: str, body: str) -> str:
        """What became of a post that was awaiting approval.

        Returns "pending", "approved", "declined" or "unknown".

        A plain decline leaves no positive trace anywhere -- "Declined with
        Feedback" only lists the ones an admin wrote a reason for -- so
        "declined" has to be reached by elimination. That makes proving the
        page actually rendered essential: a blank "Your content" page looks
        exactly like an empty Pending tab, and treating that as declined would
        release the wording while the post is still queued.

        "unknown" is the safe answer and the caller is expected to try again
        another day rather than act on it. A checkpoint or a rate-limit warning
        is not one of those answers -- it raises, like every other step here.
        """
        snippet = distinctive_snippet(body)
        if not snippet:
            return "unknown"

        try:
            self.page.goto(
                strings.my_content_url(group_url),
                timeout=NAV_TIMEOUT_MS,
                wait_until="domcontentloaded",
            )
            self.page.wait_for_timeout(FEED_SETTLE_MS)
        except Exception:
            return "unknown"

        page_text = self._page_text()

        # Classified before anything is read off it. A checkpoint, a login
        # screen or a "posting too fast" warning carries none of the markers
        # below, so without this it would come back as a polite "unknown" and
        # be retried twice a day while the user was never told. The rate-limit
        # case is worse than silent: the caller would go on opening group pages
        # straight through a block.
        verdict = classify(self.page.url, page_text)
        if not verdict.is_ok:
            raise AutomationHalted(verdict, halt_message(verdict))

        lowered = page_text.lower()
        if not any(m in lowered for m in strings.MY_CONTENT_PAGE_MARKERS):
            return "unknown"  # never rendered; do not read anything into it

        # The page opens on Pending, so finding it here means still waiting.
        if snippet.casefold() in lowered:
            return "pending"

        # Not pending any more. Live, or gone.
        return "approved" if self.verify(body, group_url) else "declined"

    def discard(self) -> None:
        """Close the composer and throw away the draft.

        Facebook asks what to do with unsent text; leaving a draft means the
        next run opens on top of it and could post the wrong thing.
        """
        try:
            self.page.keyboard.press("Escape")
            self.human.settle()
            button = self._first_present(
                [
                    self.page.get_by_role("button", name=name, exact=True)
                    for name in strings.DISCARD_PROMPT_BUTTONS
                ]
            )
            if button is not None:
                button.click()
                self.human.settle()
        except Exception:
            # Discarding is cleanup. Failing here must not mask the real error
            # that sent us down this path.
            pass

    # -- the whole flow ----------------------------------------------------
    def post(self, request: PostRequest) -> PostOutcome:
        self.open_group(request.group_url)
        dialog = self.open_composer()

        try:
            self.type_body(dialog, request.body)
            self.attach_media(dialog, request.media_paths)
            self.guard()

            if self.dry_run:
                self.discard()
                return PostOutcome(
                    posted=False,
                    dry_run=True,
                    detail=(
                        "Dry run: composed and attached everything, stopped before "
                        "clicking Post, and discarded the draft."
                    ),
                )

            closed = self.publish(dialog)
        except AutomationHalted:
            self.discard()
            raise
        except Exception:
            self.discard()
            raise

        # Verification is the source of truth, not whether the dialog closed.
        if self.verify(request.body, request.group_url):
            # Seeing it is not the same as it being published. Facebook shows
            # the author their own queued post in the feed, so in a moderated
            # group verify() succeeds on a post nobody else can see -- observed
            # live in Hebrew on 2026-08-18, where this returned a confident
            # "done" for a post the same page said was awaiting an admin.
            #
            # The banner alone cannot settle it either, because it persists
            # while ANY post of ours is queued. So when it is showing, the
            # question "is it this post?" goes to the one page that answers it.
            if self.awaiting_approval() and self._is_queued(request):
                return self._pending_outcome()
            return PostOutcome(
                posted=True,
                verified=True,
                detail="Posted and confirmed visible in the group.",
            )

        # Not in the feed at all. Before calling that a failure, ask whether the
        # group is simply holding it for an admin -- which is a success of a
        # different shape, and the batch should carry on rather than stop.
        if self.awaiting_approval():
            return self._pending_outcome()

        if not closed:
            # Still sitting in an open composer, so nothing was published --
            # clear it rather than leaving a draft for the next run.
            self.discard()
            raise PostNotVerified(
                "Clicked Post but the composer never closed and the post is not in "
                "the group, so it almost certainly did not go out. Often a slow or "
                "dropped connection. Nothing was posted and the draft was cleared, "
                "so it is safe to try again."
            )

        raise PostNotVerified(
            "Clicked Post, the composer closed, but the post could not be found in "
            "the group afterwards. It may well have gone out -- check the group "
            "before queueing it again, because posting it twice is worse than not "
            "posting it at all."
        )

    # -- read-only ---------------------------------------------------------
    def probe(self, group_url: str) -> ProbeReport:
        """Check every selector resolves, without typing or submitting anything."""
        report = ProbeReport(group_url=group_url)

        self.page.goto(group_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        verdict = classify(self.page.url, self._page_text())
        report.page_ok = verdict.is_ok
        if not verdict.is_ok:
            report.notes.append(halt_message(verdict))
            return report

        self.human.read_pause()

        trigger = self._find_composer_trigger()
        report.composer_trigger = trigger is not None
        if trigger is None:
            report.notes.append("No composer trigger matched any known wording.")
            return report

        self.human.hover_then_click(trigger)
        dialog = self._dialog()
        try:
            dialog.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
            report.dialog = True
        except Exception:
            report.notes.append("The composer dialog never appeared.")
            return report

        report.textbox = self._find_textbox(dialog) is not None
        report.photo_button = self._find_photo_button(dialog) is not None
        report.post_button = self._find_post_button(dialog) is not None

        # Nothing was typed, so this should close cleanly with no draft prompt.
        self.discard()
        return report
