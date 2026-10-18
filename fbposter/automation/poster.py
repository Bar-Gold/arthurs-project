"""Posting to one group.

The flow is: arrive, look around, open the composer, type, attach, publish,
then confirm it actually appeared. Between every meaningful step the page is
re-classified, and anything unexpected halts the batch instead of pressing on.

Two rules from CLAUDE.md are load-bearing here:
  * never bring the window to the front -- the user is working
  * never open the OS file picker -- media goes in via set_input_files
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .. import media, strings
from ..errors import AutomationHalted, ComposerNotFound, PostNotVerified, UploadStalled
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
# How long to wait, once the post window has closed, for Facebook's reply to
# the Post click. It came about five seconds after the click live.
CREATION_WAIT_MS = 15_000

# Waiting for an attached video to finish uploading (see wait_for_uploads).
UPLOAD_POLL_MS = 2_000
# No movement for this long and the upload is taken to have stalled. There is
# no limit on the upload as a whole: the user chose none (2026-10-15), and a
# big video on a slow line legitimately takes many minutes -- a 58 MB one was
# a fifth of the way up after five minutes, live.
UPLOAD_STALL_MS = 5 * 60_000
# Consecutive looks with nothing uploading before it counts as finished, so a
# moment between one file's bar and the next is not mistaken for the end.
UPLOAD_QUIET_POLLS = 2
# How often the page is checked for a checkpoint during a long upload.
UPLOAD_GUARD_EVERY_MS = 30_000

# Long enough to be unique to this post, short enough to survive Facebook
# reflowing or truncating the text in the feed.
SNIPPET_MAX_CHARS = 60
SNIPPET_MIN_CHARS = 12

# Added to a halt that happens after Post was clicked. The page turning bad
# afterwards does not take the post back, so "nothing was posted" is not true.
SENT_BEFORE_STOP = (
    "Post had already been clicked, so this post may have gone out. Check the "
    "group before posting it again."
)


def _after_click(exc: Exception) -> str:
    """Why a post stopped after Post was clicked, without claiming it failed.

    Anything raised past that click used to reach the worker as a plain error,
    which reported "Nothing was posted" -- about a post that may well be live.
    """
    text = str(exc)
    if "Timeout" in text and "exceeded" in text:
        what = "Facebook stopped responding"
    else:
        what = "something unexpected went wrong"
    return (
        f"Clicked Post, but then {what} before the post could be checked. It may "
        "well have gone out. Check the group before queueing it again, because "
        "posting it twice is worse than not posting it."
    )


@dataclass(frozen=True)
class PostRequest:
    group_url: str
    body: str
    media_paths: tuple[Path, ...] = ()
    # These exact words have been sent to this group before. The same text may
    # go once a day, so an older post with it can sit in the feed, and finding
    # the words there is then no proof of this post.
    sent_before: bool = False


def _post_id_from_story_id(story_id: Any) -> str:
    """The post's number inside a story id, or "".

    A story id is base64 of "S:_I<author>:<kind>:<post number>". Read live on
    2026-10-15 off a video post, whose number opened that exact post.
    """
    if not isinstance(story_id, str) or not story_id:
        return ""
    try:
        decoded = base64.b64decode(story_id + "=" * (-len(story_id) % 4)).decode("ascii")
    except (ValueError, UnicodeDecodeError):
        return ""
    parts = decoded.split(":")
    if len(parts) < 3 or parts[0] != "S" or not parts[-1].isdigit():
        return ""
    return parts[-1]


def created_post_url(reply_text: str, group_url: str = "") -> str:
    """The new post's own link, from Facebook's reply to the Post click.

    "" when the reply does not describe a new post. The shape was read off the
    live site on 2026-10-07: data.story_create.story carries `url`
    (.../permalink/<id>/) and `legacy_story_hideable_id` (the <id>).

    **A video post replies differently** (read live 2026-10-15): `story` is
    null and `publishing_flow` is "FALLBACK", because Facebook publishes the
    post only once it has processed the video, a minute or two later. The
    post's number is then inside `story_create.story_id`, and the link built
    from it opened that exact post. So for a video the reply is still proof
    that this post was made, though it may not be in the feed yet.
    """
    for line in reply_text.splitlines():
        line = line.strip().removeprefix("for (;;);")
        if not line.startswith("{"):
            continue
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        data = reply.get("data") if isinstance(reply, dict) else None
        created = data.get("story_create") if isinstance(data, dict) else None
        if not isinstance(created, dict):
            continue
        story = created.get("story")
        if isinstance(story, dict):
            url = story.get("url")
            if isinstance(url, str) and url.startswith("http"):
                return url
            post_id = str(story.get("legacy_story_hideable_id") or "")
            if post_id.isdigit() and group_url:
                return group_url.rstrip("/") + f"/permalink/{post_id}/"
        post_id = _post_id_from_story_id(created.get("story_id"))
        if post_id and group_url:
            return group_url.rstrip("/") + f"/permalink/{post_id}/"
    return ""


def _call_name(request: Any) -> str:
    """Which of Facebook's calls a request is: its "friendly name"."""
    try:
        from urllib.parse import parse_qs

        name = parse_qs(request.post_data or "").get("fb_api_req_friendly_name")
        if name:
            return name[0]
    except Exception:
        pass
    try:
        return request.headers.get("x-fb-friendly-name", "")
    except Exception:
        return ""


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
        # Set the moment Post is clicked. From then on a failure cannot be
        # reported as "not posted", because it may have gone out.
        self.clicked_post = False
        self._creation_listener = None

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
                "Could not find where to write a post in this group. Facebook may "
                "have changed its page, or the account's language is not English, "
                "Hebrew or Russian."
            )

        self.human.hover_then_click(trigger)
        dialog = self._dialog()
        dialog.wait_for(state="visible", timeout=ACTION_TIMEOUT_MS)
        self.guard()
        return dialog

    def type_body(self, dialog: Any, body: str) -> None:
        textbox = self._find_textbox(dialog)
        if textbox is None:
            raise ComposerNotFound(
                "The post window opened, but had nowhere to type. Facebook may "
                "have changed its page."
            )

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
        """Attach pictures and videos by writing straight to the file input.

        **Do not click the Photo/video button.** Clicking it opens the native
        Windows "Open" dialog, which is modal, steals focus, and was observed
        being left on screen after a run -- breaking the one hard promise this
        app makes, that the user can keep working while it posts.

        The input element is already in the composer's DOM before that button
        is touched, so setting files on it directly is both safer and simpler.
        Everything goes in one call; the input carries `multiple`, and accepts
        pictures and videos together (read live 2026-10-15).
        """
        if not paths:
            return

        # An attribute selector on a standard element, not an obfuscated class
        # name -- this is the one place a CSS selector is the right tool.
        file_input = dialog.locator('input[type="file"]').first
        file_input.set_input_files([str(path) for path in paths])
        self.human.settle()
        self.wait_for_uploads(dialog)

    def _upload_progress(self, dialog: Any) -> list[float] | None:
        """How far each upload in the post window has got, or None if none is.

        Facebook draws a `progressbar` over a video while it uploads ("Uploading
        video", aria-valuenow 0 to 100) and removes it when the upload is done.
        Read by role and number only, so no language is involved.
        """
        bars = dialog.get_by_role("progressbar")
        count = bars.count()
        if not count:
            return None
        values = []
        for index in range(count):
            try:
                values.append(float(bars.nth(index).get_attribute("aria-valuenow") or 0))
            except Exception:
                values.append(0.0)  # no number, or a bar that went as it was read
        return values

    def _post_is_disabled(self, dialog: Any) -> bool:
        button = self._first_present(
            [dialog.get_by_role("button", name=name, exact=True) for name in strings.POST_BUTTONS]
        )
        if button is None:
            return False  # publish() reports a missing button in its own words
        try:
            return button.get_attribute("aria-disabled") == "true"
        except Exception:
            return False

    def wait_for_uploads(self, dialog: Any) -> None:
        """Wait until every attached file has finished uploading.

        **Facebook lets Post be clicked long before a video has uploaded.**
        Watched live on 2026-10-15: Post is disabled for about a second after
        the files are set, then enabled again while a 58 MB video sat at 1%.
        So Post being clickable says nothing, and the progress bar is what is
        watched: it goes when the upload is done (six seconds for a 1 MB
        video). What a click mid-upload does was not tested, on purpose.

        No overall limit: the wait lasts as long as the upload keeps moving.
        Five minutes without movement raises UploadStalled, before Post, so
        "nothing was posted" is true. The page is still checked for a
        checkpoint every half minute.
        """
        quiet = 0
        last: list[float] | None = None
        still_for = 0
        since_guard = 0
        while True:
            progress = self._upload_progress(dialog)
            if progress is None and not self._post_is_disabled(dialog):
                quiet += 1
                if quiet >= UPLOAD_QUIET_POLLS:
                    return
                last = None
            else:
                quiet = 0
                # Movement is any bar going up, or a bar coming or going: one
                # video finishing lowers the total while the next carries on.
                moved = progress is not None and (
                    last is None
                    or len(progress) != len(last)
                    or any(now > before for now, before in zip(progress, last))
                )
                last = progress
                if moved:
                    still_for = 0
                else:
                    still_for += UPLOAD_POLL_MS
                if still_for >= UPLOAD_STALL_MS:
                    raise UploadStalled(
                        "A video stopped uploading to Facebook: no progress for "
                        f"{UPLOAD_STALL_MS // 60_000} minutes. Post was not clicked, so "
                        "nothing was posted. Check the internet connection, then queue "
                        "it again."
                    )
            self.page.wait_for_timeout(UPLOAD_POLL_MS)
            since_guard += UPLOAD_POLL_MS
            if since_guard >= UPLOAD_GUARD_EVERY_MS:
                since_guard = 0
                self.guard()

    def publish(self, dialog: Any) -> bool:
        """Click Post. Returns whether the composer actually closed.

        A composer that has not closed is a hint, not a verdict. On a slow
        connection it can linger after a post that went out perfectly well, so
        this reports what it saw and lets verification decide. Treating the
        timeout as failure is what turned a slow moment into a halted batch.
        """
        button = self._find_post_button(dialog)
        if button is None:
            raise ComposerNotFound(
                "The post window had no Post button. Facebook may have changed "
                "its page."
            )

        self.clicked_post = True
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

    def _pending_outcome(self, post_url: str = "") -> PostOutcome:
        return PostOutcome(
            posted=True,
            verified=False,
            pending=True,
            post_url=post_url,
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
        """Post, and never let a failure after the Post click read as "not posted"."""
        self.clicked_post = False
        try:
            return self._post(request)
        except PostNotVerified:
            raise
        except AutomationHalted as exc:
            if not self.clicked_post:
                raise
            # Still a halt -- a checkpoint after the click stops the batch like
            # any other -- but one that says the post may be out there.
            raise AutomationHalted(
                exc.verdict, f"{exc} {SENT_BEFORE_STOP}", may_have_posted=True
            ) from exc
        except Exception as exc:
            if not self.clicked_post:
                raise
            raise PostNotVerified(_after_click(exc)) from exc

    def _post(self, request: PostRequest) -> PostOutcome:
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

            replies = self._listen_for_creation()
            try:
                closed = self.publish(dialog)
                # Read before anything navigates: once the page moves on,
                # Facebook's reply can no longer be read (seen live).
                created = self._created_post_url(replies, request.group_url)
            finally:
                self._stop_listening()
        except AutomationHalted:
            self.discard()
            raise
        except Exception:
            self.discard()
            raise

        if created:
            return self._created_outcome(request, created)

        # No reply to go on -- Facebook may have renamed the call. The older
        # checks follow, and they find a post by its words.
        if not distinctive_snippet(request.body):
            return self._pictures_only_outcome(request, closed)

        # Verification is the source of truth, not whether the dialog closed.
        if self.verify(request.body, request.group_url):
            if request.sent_before:
                # The same words were sent to this group before, so what was
                # found may be the older post. Saying "posted" on that is the
                # false confirmation this guards against.
                raise PostNotVerified(
                    "Clicked Post, and these words are in the group, but the same "
                    "text was posted there before, so the app cannot tell whether "
                    "this one went out. Check the group before posting it again."
                )
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
                "Clicked Post, but the post window never closed and the post is not "
                "in the group, so it almost certainly did not go out. Usually that is a "
                "slow or dropped connection. The draft was cleared, so it is safe "
                "to try again.",
                may_have_posted=False,
            )

        raise PostNotVerified(
            "Clicked Post, the post window closed, but the post could not be found "
            "in the group afterwards. It may well have gone out, so check the group "
            "before queueing it again, because posting it twice is worse than not "
            "posting it at all."
        )

    def _listen_for_creation(self) -> list:
        """Start keeping Facebook's reply to the Post click.

        Only listens: nothing about how the post is made changes. Returns the
        list the replies are kept in.
        """
        replies: list = []

        def keep(response: Any) -> None:
            try:
                if (
                    strings.GRAPHQL_PATH in response.url
                    and _call_name(response.request) == strings.POST_CREATED_CALL
                ):
                    replies.append(response)
            except Exception:
                pass  # listening must never be what breaks a post

        try:
            self.page.on("response", keep)
            self._creation_listener = keep
        except Exception:
            self._creation_listener = None
        return replies

    def _stop_listening(self) -> None:
        if self._creation_listener is None:
            return
        try:
            self.page.remove_listener("response", self._creation_listener)
        except Exception:
            pass
        self._creation_listener = None

    def _created_post_url(self, replies: list, group_url: str) -> str:
        """The new post's link from Facebook's reply, or "" if there is none."""
        if self._creation_listener is None:
            return ""
        waited = 0
        while not replies and waited < CREATION_WAIT_MS:
            self.page.wait_for_timeout(RENDER_POLL_MS)
            waited += RENDER_POLL_MS
        for reply in list(replies):
            try:
                url = created_post_url(reply.text(), group_url)
            except Exception:
                continue
            if url:
                return url
        return ""

    def _created_outcome(self, request: PostRequest, url: str) -> PostOutcome:
        """Facebook made the post. All that is left is whether a group holds it.

        The reply names this post and no other, which searching the feed for
        its words cannot do once the same words may go to a group every day --
        and it works for a post of pictures alone. One look at the group
        follows, as a person would take, for the "pending admin approval"
        banner; a checkpoint or a block shown there still halts the batch.
        """
        detail = "Posted. Facebook confirmed it and gave the post its own link."
        if any(media.is_video(path) for path in request.media_paths):
            # Facebook publishes a video post once it has processed the video,
            # a minute or two after the click (seen live, 2026-10-15).
            detail += (
                " A video can take a few minutes to show in the group while "
                "Facebook processes it."
            )
        confirmed = PostOutcome(posted=True, verified=True, post_url=url, detail=detail)
        if not request.group_url:
            return confirmed
        try:
            self.page.goto(
                request.group_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded"
            )
            self.page.wait_for_timeout(FEED_SETTLE_MS)
        except Exception:
            # The post is made; not being able to look again only means the
            # banner cannot be read.
            return confirmed
        self.guard()
        if self.awaiting_approval():
            # The banner shows while any post of ours is held. A text post is
            # looked up in the group's pending list; pictures cannot be, and a
            # group holding posts holds every member's.
            if not distinctive_snippet(request.body) or self._is_queued(request):
                return self._pending_outcome(url)
        return confirmed

    def _pictures_only_outcome(self, request: PostRequest, closed: bool) -> PostOutcome:
        """Judge a post that has pictures and no words.

        verify() finds a post by the user's own words, and there are none --
        nothing on the page ties a photo in the feed back to the file that was
        uploaded, because Facebook re-encodes it. So the evidence is what
        Facebook did with the composer: it closes when the post is accepted.

        Still open after PUBLISH_TIMEOUT_MS is not proof it did not go out, and
        with no words there is no second look to settle it, so this halts with
        the cautious message rather than the "safe to try again" one.
        """
        if not closed:
            self.discard()
            raise PostNotVerified(
                "Clicked Post, but the post window was still open a minute later. A "
                "post with only pictures or videos has no words to look for, so whether it "
                "went out cannot be checked. Look at the group before queueing it "
                "again, because posting it twice is worse than not posting it."
            )

        # One look at the group, as a person would after posting -- and the
        # same look verify() takes. A "posting too fast" warning or checkpoint
        # shown on arrival must still halt the batch.
        if request.group_url:
            self.page.goto(
                request.group_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded"
            )
            self.page.wait_for_timeout(FEED_SETTLE_MS)
            self.guard()

        # In a group that holds posts for an admin, the banner is the only sign
        # there is. It cannot be tied to this post, but a group holding posts
        # holds every member's, so it is read as this one waiting.
        if self.awaiting_approval():
            return self._pending_outcome()
        # Only reached without Facebook's reply to the Post click, which is what
        # confirms a post of pictures. The post window closing is a good sign
        # and no more, and the Queue says exactly that.
        return PostOutcome(
            posted=True,
            verified=False,
            detail=(
                "Only pictures or videos were uploaded, so the app could not check that it "
                "posted. Facebook closed the post window, which usually means it "
                "went out. Look at the group to be sure."
            ),
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
