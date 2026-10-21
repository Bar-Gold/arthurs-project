"""Tests for the posting flow, driven against a fake page.

No browser, no Facebook account, no network. What is asserted here is the
behaviour that keeps the account safe: the order of steps, that anomalies halt
instead of pressing on, that a dry run never publishes, and that a draft is
never left behind.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from fbposter import strings
from fbposter.automation.humanize import HumanProfile, Humanizer
from fbposter.automation.poster import (
    GroupPoster,
    PostRequest,
    created_post_url,
    distinctive_snippet,
    read_created_post,
)
from fbposter.automation.poster import GROUP_UNAVAILABLE
from fbposter.errors import (
    AutomationHalted,
    ComposerNotFound,
    GroupUnavailable,
    PageLoadFailed,
    PostNotVerified,
)

from .fake_page import FakePage

GROUP_URL = "https://www.facebook.com/groups/testgroup/"
BODY = "Selling a barely used road bike, 54cm frame, recently serviced."

# Referenced through strings rather than hardcoded, so these tests do not have
# to be rewritten every time the interface language changes. The account's
# Facebook is Hebrew, so the first candidate is Hebrew.
POST_BUTTON = f"role=button:{strings.POST_BUTTONS[0]}"
PHOTO_BUTTON = strings.PHOTO_VIDEO_BUTTONS[0]
NO_POST_BUTTON = tuple(f"role=button:{name}" for name in strings.POST_BUTTONS)
NO_COMPOSER = tuple(strings.COMPOSER_TRIGGERS)


def quiet_humanizer() -> Humanizer:
    """Same logic as production, but instant and deterministic."""
    return Humanizer(
        profile=HumanProfile(word_pause_chance=0.0),
        rng=random.Random(1),
        sleep=lambda _seconds: None,
    )


def make_poster(page: FakePage, dry_run: bool = False) -> GroupPoster:
    return GroupPoster(page, humanizer=quiet_humanizer(), dry_run=dry_run)


def request(**overrides) -> PostRequest:
    values = {"group_url": GROUP_URL, "body": BODY}
    values.update(overrides)
    return PostRequest(**values)


class TestSnippet:
    def test_it_picks_the_longest_line(self):
        body = "Hi all\nSelling a barely used road bike in great condition\nThanks"
        assert distinctive_snippet(body).startswith("Selling a barely used road bike")

    def test_it_is_capped(self):
        assert len(distinctive_snippet("x" * 500)) <= 60

    def test_short_lines_are_joined_rather_than_used_alone(self):
        """A two-word line would match half the feed."""
        assert distinctive_snippet("Hi\nBye") == "Hi Bye"

    def test_empty_text_has_no_snippet(self):
        assert distinctive_snippet("   \n  ") == ""

    def test_whitespace_is_collapsed(self):
        assert "  " not in distinctive_snippet("a     lot     of      space here")


class TestHappyPath:
    def test_a_post_goes_through_every_step_in_order(self):
        page = FakePage()
        make_poster(page).post(request())

        names = page.call_names()
        first_type = names.index("type")
        assert names.index("goto") < names.index("wheel"), "should look around on arrival"
        assert names.index("wheel") < first_type, "should scroll before typing"
        # The composer trigger is hovered before typing, and the Post button
        # after it -- so there is a hover on each side.
        assert names.index("hover") < first_type, "composer trigger hovered first"
        assert len(names) - 1 - names[::-1].index("hover") > first_type, (
            "Post button hovered after the body was typed"
        )

    def test_the_body_is_typed_in_full(self):
        page = FakePage()
        make_poster(page).post(request())
        assert page.typed_text == BODY

    def test_the_field_is_cleared_before_typing(self):
        """Never type on top of a leftover draft -- that posts something the
        user did not write. Facebook currently drops the draft on close, but
        the flow must not depend on that continuing to be true."""
        page = FakePage()
        make_poster(page).post(request())

        names = page.call_names()
        presses = [i for i, c in enumerate(page.calls) if c[0] == "press"]
        first_type = names.index("type")
        assert any(page.calls[i][1] == "Control+A" for i in presses)
        assert any(page.calls[i][1] == "Delete" for i in presses)
        assert min(
            i for i in presses if page.calls[i][1] in ("Control+A", "Delete")
        ) < first_type

    def test_the_post_button_is_clicked(self):
        page = FakePage()
        make_poster(page).post(request())
        assert page.clicked(POST_BUTTON)

    def test_the_outcome_reports_a_verified_post(self):
        page = FakePage()
        outcome = make_poster(page).post(request())
        assert outcome.posted
        assert outcome.verified
        assert not outcome.dry_run

    def test_the_composer_is_never_brought_to_front(self):
        """Raising the window would break the whole non-interference promise."""
        page = FakePage()
        make_poster(page).post(request())
        assert not any("bring_to_front" in str(call) for call in page.calls)


class TestMedia:
    def test_all_images_go_in_one_call(self):
        page = FakePage()
        paths = (Path("a.png"), Path("b.png"), Path("c.png"))
        make_poster(page).post(request(media_paths=paths))

        uploads = [call for call in page.calls if call[0] == "set_input_files"]
        assert len(uploads) == 1
        assert uploads[0][2] == ("a.png", "b.png", "c.png")

    def test_the_photo_button_is_never_clicked(self):
        """Clicking it opens the native Windows file dialog -- modal, focus
        stealing, and observed being left on screen after a live run. The file
        input is already in the DOM, so the button is not needed at all.
        """
        page = FakePage()
        make_poster(page).post(request(media_paths=(Path("a.png"),)))
        assert not page.clicked(PHOTO_BUTTON)

    def test_no_upload_happens_without_media(self):
        page = FakePage()
        make_poster(page).post(request())
        assert not any(call[0] == "set_input_files" for call in page.calls)

    def test_the_os_file_picker_is_never_opened(self):
        """set_input_files only. A native dialog is modal and steals focus."""
        page = FakePage()
        make_poster(page).post(request(media_paths=(Path("a.png"),)))
        assert not any("file_chooser" in str(call) for call in page.calls)


class TestPicturesOnly:
    """A post with pictures and no words.

    Verification looks for the user's own words, and there are none, so what
    Facebook did with the composer is the evidence. Posted live to a real group
    on 2026-09-29: the composer closed and the picture was in the feed.
    """

    PICTURES = (Path("a.png"), Path("b.png"))

    def post(self, page: FakePage, body: str = ""):
        return make_poster(page).post(request(body=body, media_paths=self.PICTURES))

    def test_it_posts_without_typing_a_word(self):
        page = FakePage()
        outcome = self.post(page)

        assert outcome.posted
        assert not outcome.pending
        assert page.typed_text == ""
        assert page.clicked(POST_BUTTON)
        assert ("set_input_files", 'role=dialog:None >> input[type="file"]', ("a.png", "b.png")) in page.calls

    def test_it_is_not_claimed_as_seen_in_the_feed(self):
        """Without Facebook's reply nothing confirms it, so it says so."""
        outcome = self.post(FakePage())
        assert outcome.verified is False
        assert "could not check that it posted" in outcome.detail

    def test_a_leftover_draft_is_still_cleared_first(self):
        """No words to type is not a reason to post whatever was there before."""
        page = FakePage()
        self.post(page)
        assert ("press", "Control+A") in page.calls
        assert ("press", "Delete") in page.calls

    def test_invisible_characters_alone_are_no_words(self):
        """A bidi mark pasted on its own must not send it hunting for itself."""
        outcome = self.post(FakePage(), body="‏ \n")
        assert outcome.posted

    def test_it_looks_at_the_group_once_afterwards(self):
        page = FakePage()
        self.post(page)
        assert [c for c in page.calls if c[0] == "goto"] == [("goto", GROUP_URL)] * 2

    def test_a_checkpoint_on_that_look_halts_the_batch(self):
        page = FakePage()
        page.on_click[f"role=dialog:None >> {POST_BUTTON}"] = lambda: setattr(
            page, "redirect_to", "https://www.facebook.com/checkpoint/1/"
        )
        with pytest.raises(AutomationHalted):
            self.post(page)

    def test_a_composer_that_never_closes_halts_without_saying_it_is_safe(self):
        """With no words there is no second look, so it cannot say "did not go
        out" the way a text post that was searched for and missing can."""
        page = FakePage(never_detaches=True)
        with pytest.raises(PostNotVerified) as caught:
            self.post(page)

        message = str(caught.value)
        assert "cannot be checked" in message
        assert "safe to try again" not in message
        assert ("press", "Escape") in page.calls

    def test_a_group_holding_posts_reports_it_pending(self):
        page = FakePage(body_text=f"A group feed. {strings.PENDING_APPROVAL_MARKERS[0]}")
        outcome = self.post(page)
        assert outcome.pending is True
        assert outcome.posted is True

    def test_pictures_with_words_are_still_verified_by_the_words(self):
        page = FakePage()
        outcome = make_poster(page).post(request(media_paths=self.PICTURES))
        assert outcome.verified is True


class TestDryRun:
    def test_it_never_clicks_post(self):
        page = FakePage()
        outcome = make_poster(page, dry_run=True).post(request())

        assert not page.clicked(POST_BUTTON)
        assert outcome.posted is False
        assert outcome.dry_run is True

    def test_it_still_types_the_whole_body(self):
        """A rehearsal that skips typing would not rehearse much."""
        page = FakePage()
        make_poster(page, dry_run=True).post(request())
        assert page.typed_text == BODY

    def test_it_still_attaches_media(self):
        page = FakePage()
        make_poster(page, dry_run=True).post(request(media_paths=(Path("a.png"),)))
        assert any(call[0] == "set_input_files" for call in page.calls)

    def test_it_discards_the_draft(self):
        """Leaving a draft means the next run opens on top of it."""
        page = FakePage()
        make_poster(page, dry_run=True).post(request())
        assert ("press", "Escape") in page.calls


class TestAnomaliesHalt:
    def test_a_checkpoint_on_arrival_stops_everything(self):
        page = FakePage(redirect_to="https://www.facebook.com/checkpoint/1/")

        with pytest.raises(AutomationHalted) as caught:
            make_poster(page).post(request())

        assert caught.value.verdict.value == "checkpoint"
        assert page.typed_text == ""
        assert not page.clicked(POST_BUTTON)

    def test_a_rate_limit_warning_stops_everything(self):
        page = FakePage(body_text="You're Temporarily Blocked")

        with pytest.raises(AutomationHalted) as caught:
            make_poster(page).post(request())

        assert caught.value.verdict.value == "rate_limit"
        assert not page.clicked(POST_BUTTON)

    def test_an_expired_session_stops_everything(self):
        page = FakePage(redirect_to="https://www.facebook.com/login/")
        with pytest.raises(AutomationHalted):
            make_poster(page).post(request())

    def test_a_block_appearing_after_typing_prevents_the_post(self):
        """The page is re-checked between typing and publishing."""
        page = FakePage()

        original = page.inner_text

        def blocked_after_typing(selector: str) -> str:
            if page.keyboard.typed:
                return "you're posting too fast"
            return original(selector)

        page.inner_text = blocked_after_typing

        with pytest.raises(AutomationHalted):
            make_poster(page).post(request())

        assert not page.clicked(POST_BUTTON)

    def test_a_halt_still_discards_the_draft(self):
        page = FakePage()
        original = page.inner_text
        page.inner_text = lambda s: (
            "you're posting too fast" if page.keyboard.typed else original(s)
        )

        with pytest.raises(AutomationHalted):
            make_poster(page).post(request())

        assert ("press", "Escape") in page.calls

    def test_nothing_is_retried_after_a_halt(self):
        page = FakePage(body_text="You're Temporarily Blocked")
        with pytest.raises(AutomationHalted):
            make_poster(page).post(request())
        assert len([c for c in page.calls if c[0] == "goto"]) == 1


class TestUnavailableWordsInAGoodGroup:
    """A client's batch halted at a group that posted fine an hour later. The
    page text is the whole page, other people's posts included, and a shared
    post whose original was deleted reads "This content isn't available right
    now" in the middle of a perfectly good feed. The post box decides."""

    FEED = f"Someone shared this. {strings.UNAVAILABLE_MARKERS[1]} Another post."

    def test_a_group_with_a_post_box_is_posted_to(self):
        page = FakePage(body_text=self.FEED)
        outcome = make_poster(page).post(request(body="An ordinary post"))
        assert outcome.posted and outcome.verified
        assert page.clicked(POST_BUTTON)

    def test_the_words_in_our_own_post_do_not_stop_it(self):
        """Once typed, the post box's own text is page text too."""
        body = f"Flat for rent. {strings.UNAVAILABLE_MARKERS[0]} is not true here."
        page = FakePage()
        original = page.inner_text
        page.inner_text = lambda s: page.typed_text or original(s)

        outcome = make_poster(page).post(request(body=body))
        assert outcome.posted
        assert page.clicked(POST_BUTTON)

    def test_a_block_still_halts_however_the_feed_reads(self):
        page = FakePage(body_text=f"{self.FEED} You're Temporarily Blocked")
        with pytest.raises(AutomationHalted) as caught:
            make_poster(page).post(request())
        assert caught.value.verdict.value == "rate_limit"
        assert page.typed_text == ""

    def test_a_checkpoint_still_halts_on_arrival(self):
        page = FakePage(
            body_text=self.FEED, redirect_to="https://www.facebook.com/checkpoint/1/"
        )
        with pytest.raises(AutomationHalted):
            make_poster(page).post(request())


class TestAGroupThatIsReallyGone:
    def unavailable(self):
        return FakePage(body_text=strings.UNAVAILABLE_MARKERS[0], missing=NO_COMPOSER)

    def test_it_raises_group_unavailable_not_a_halt(self):
        page = self.unavailable()
        with pytest.raises(GroupUnavailable) as caught:
            make_poster(page).post(request())
        assert not isinstance(caught.value, AutomationHalted)
        assert str(caught.value) == GROUP_UNAVAILABLE

    def test_nothing_is_typed_or_clicked(self):
        page = self.unavailable()
        with pytest.raises(GroupUnavailable):
            make_poster(page).post(request())
        assert page.typed_text == ""
        assert not page.clicked(POST_BUTTON)

    def test_a_missing_post_box_without_those_words_is_still_an_error(self):
        page = FakePage(missing=NO_COMPOSER)
        with pytest.raises(ComposerNotFound):
            make_poster(page).post(request())

    def test_the_words_in_each_language_are_recognised(self):
        for marker in strings.UNAVAILABLE_MARKERS:
            page = FakePage(body_text=marker, missing=NO_COMPOSER)
            with pytest.raises(GroupUnavailable):
                make_poster(page).post(request())


class TestAPageThatWouldNotLoad:
    """A client's laptop woke from sleep and the page load came back
    net::ERR_NETWORK_IO_SUSPENDED. That is before any post box exists."""

    def test_it_raises_page_load_failed(self):
        page = FakePage(goto_error="net::ERR_NETWORK_IO_SUSPENDED")
        with pytest.raises(PageLoadFailed) as caught:
            make_poster(page).post(request())
        assert "ERR_NETWORK_IO_SUSPENDED" in str(caught.value)
        assert "Nothing was typed or posted" in str(caught.value)

    def test_nothing_is_typed_or_clicked(self):
        page = FakePage(goto_error="net::ERR_INTERNET_DISCONNECTED")
        with pytest.raises(PageLoadFailed):
            make_poster(page).post(request())
        assert page.typed_text == ""
        assert not page.clicked(POST_BUTTON)
        assert ("press", "Escape") not in page.calls, "there was no draft to clear"


class TestMissingElements:
    def test_a_missing_composer_is_an_error_not_a_guess(self):
        page = FakePage(missing=NO_COMPOSER)
        with pytest.raises(ComposerNotFound):
            make_poster(page).post(request())

    def test_a_missing_post_button_stops_before_publishing(self):
        page = FakePage(missing=NO_POST_BUTTON)
        with pytest.raises(ComposerNotFound):
            make_poster(page).post(request())
        assert ("press", "Escape") in page.calls

    def test_a_dialog_that_never_opens_is_an_error(self):
        # Distinct from never_detaches: this composer never appears at all,
        # which is a real failure rather than a slow publish.
        page = FakePage(wait_fails_for=("role=dialog:None",))
        with pytest.raises(TimeoutError):
            make_poster(page).post(request())


class TestSlowPublish:
    """The composer not closing is a hint, not a verdict.

    A hard 15s timeout on 'the dialog must detach' turned one slow moment into a
    halted batch, so the composer's state no longer decides the outcome --
    verification does.
    """

    def test_a_slow_post_that_did_go_out_still_succeeds(self):
        page = FakePage(never_detaches=True)
        # The dialog never detaches, but the text is findable in the feed.
        outcome = make_poster(page).post(request())

        assert outcome.posted
        assert outcome.verified

    def test_a_lingering_composer_with_no_post_says_it_is_safe_to_retry(self):
        snippet = distinctive_snippet(BODY)
        page = FakePage(never_detaches=True, missing=(f"text={snippet}",))

        with pytest.raises(PostNotVerified) as caught:
            make_poster(page).post(request())

        message = str(caught.value)
        assert "did not go out" in message
        assert "safe to try again" in message

    def test_a_lingering_composer_is_cleared_rather_than_left_as_a_draft(self):
        snippet = distinctive_snippet(BODY)
        page = FakePage(never_detaches=True, missing=(f"text={snippet}",))

        with pytest.raises(PostNotVerified):
            make_poster(page).post(request())

        assert ("press", "Escape") in page.calls

    def test_a_closed_composer_with_no_post_warns_against_reposting(self):
        """The genuinely ambiguous case: it may have gone out."""
        snippet = distinctive_snippet(BODY)
        page = FakePage(missing=(f"text={snippet}",))

        with pytest.raises(PostNotVerified) as caught:
            make_poster(page).post(request())

        assert "twice" in str(caught.value)


class TestVerificationIsPatient:
    """Facebook virtualises the group feed, so a freshly published post is not
    reliably in the DOM when first asked for. Two live posts were reported
    unverified this way and had gone out perfectly well."""

    def test_it_keeps_looking_and_scrolling(self):
        page = FakePage(missing=(f"text={distinctive_snippet(BODY)}",))
        appears = {"yet": False}

        original = page.inner_text
        page.inner_text = lambda s: (BODY if appears["yet"] else original(s))

        # The post surfaces only once the feed has been nudged after posting.
        # The scroll on arrival comes before it exists.
        original_wheel = page.mouse.wheel

        def wheel(x, y):
            if page.keyboard.typed:
                appears["yet"] = True
            original_wheel(x, y)

        page.mouse.wheel = wheel

        outcome = make_poster(page).post(request())
        assert outcome.verified

    def test_it_finds_the_post_in_the_page_text_alone(self):
        """Even when the locator never matches, the text being on the page is
        proof enough that it published."""
        page = FakePage(missing=(f"text={distinctive_snippet(BODY)}",), after_post_text=BODY)
        outcome = make_poster(page).post(request())
        assert outcome.verified

    def test_it_still_gives_up_when_the_post_really_is_absent(self):
        page = FakePage(missing=(f"text={distinctive_snippet(BODY)}",))
        with pytest.raises(PostNotVerified):
            make_poster(page).post(request())


class TestVerification:
    def test_an_unverifiable_post_raises_rather_than_reporting_success(self):
        """Never silently claim success -- a retry would double-post."""
        page = FakePage(missing=(f"text={distinctive_snippet(BODY)}",))

        with pytest.raises(PostNotVerified) as caught:
            make_poster(page).post(request())

        assert "twice" in str(caught.value)

    def test_it_reloads_the_feed_before_giving_up(self):
        """The first live post published and still failed verification: the
        feed we were already on had not picked it up. Reloading is what a
        person would do, and a false negative here is expensive -- a caller
        that retried on it would post twice.
        """
        page = FakePage()
        snippet = distinctive_snippet(BODY)
        appears_after_reload = {"done": False}

        original_resolve = page._resolve

        def resolve(description: str):
            if f"text={snippet}" in description and not appears_after_reload["done"]:
                located = original_resolve(description)
                located.matches = 0
                return located
            return original_resolve(description)

        original_goto = page.goto

        def goto(url, timeout=0, wait_until=""):
            # Anything after the first navigation is the verification reload.
            if page.keyboard.typed:
                appears_after_reload["done"] = True
            original_goto(url, timeout, wait_until)

        page._resolve = resolve
        page.goto = goto

        outcome = make_poster(page).post(request())

        assert outcome.verified
        assert len([c for c in page.calls if c[0] == "goto"]) == 2

    def test_verification_happens_after_the_post_click(self):
        page = FakePage()
        make_poster(page).post(request())

        clicks = [i for i, c in enumerate(page.calls) if c[0] == "click"]
        waits = [
            i
            for i, c in enumerate(page.calls)
            if c[0] == "wait_for" and "text=" in str(c[1])
        ]
        assert waits and clicks
        assert max(waits) > max(clicks)


class TestProbe:
    def test_a_clean_probe_finds_everything(self):
        page = FakePage()
        report = make_poster(page).probe(GROUP_URL)
        assert report.ok
        assert report.notes == []

    def test_a_probe_never_types_or_posts(self):
        """This is the read-only command; it must stay read-only."""
        page = FakePage()
        make_poster(page).probe(GROUP_URL)
        assert page.typed_text == ""
        assert not page.clicked(POST_BUTTON)

    def test_a_probe_reports_a_blocked_page_instead_of_continuing(self):
        page = FakePage(body_text="You're Temporarily Blocked")
        report = make_poster(page).probe(GROUP_URL)
        assert not report.ok
        assert not report.page_ok
        assert report.notes

    def test_a_probe_reports_a_missing_composer(self):
        page = FakePage(missing=NO_COMPOSER)
        report = make_poster(page).probe(GROUP_URL)
        assert not report.ok
        assert not report.composer_trigger

    def test_a_probe_closes_the_composer_afterwards(self):
        page = FakePage()
        make_poster(page).probe(GROUP_URL)
        assert ("press", "Escape") in page.calls

    def test_unavailable_words_with_a_post_box_are_reported_not_a_failure(self):
        """The probe judges a page exactly as posting does."""
        page = FakePage(body_text=strings.UNAVAILABLE_MARKERS[0])
        report = make_poster(page).probe(GROUP_URL)
        assert report.ok
        assert any("someone's post" in note for note in report.notes)

    def test_unavailable_words_and_no_post_box_fail_the_probe(self):
        page = FakePage(body_text=strings.UNAVAILABLE_MARKERS[0], missing=NO_COMPOSER)
        report = make_poster(page).probe(GROUP_URL)
        assert not report.ok
        assert not report.page_ok
        assert GROUP_UNAVAILABLE in report.notes


class TestAGroupThatHoldsPostsForApproval:
    """Verified against a live moderated group on 2026-08-17.

    The post is submitted, the composer closes, and it is NOT in the feed --
    the group shows the author a "Pending admin approval" banner instead.
    The app used to guess, and which way it guessed depended on timing.
    """

    BANNER = "Pending admin approval 1 post"
    CONTENT_PAGE = "Your content Manage and view your posts"

    def moderated_page(self, body_text: str) -> FakePage:
        """A group where the post is genuinely nowhere to be found.

        `missing` kills the text locator and body_text carries no snippet, so
        both halves of _snippet_visible fail -- which is what really happens
        when the post is sitting in the admin queue.
        """
        return FakePage(
            body_text=body_text,
            missing=(f"text={distinctive_snippet(BODY)}",),
        )

    def test_it_is_reported_as_pending_not_failed(self):
        page = self.moderated_page(f"A normal group feed. {self.BANNER}")
        outcome = make_poster(page).post(request())
        assert outcome.pending is True
        assert outcome.posted is True
        assert outcome.verified is False

    def test_the_detail_says_it_may_still_be_declined(self):
        page = self.moderated_page(f"A normal group feed. {self.BANNER}")
        outcome = make_poster(page).post(request())
        assert "approve" in outcome.detail.lower()
        assert "declined" in outcome.detail.lower()

    def test_a_post_that_is_actually_visible_is_not_called_pending(self):
        """The banner persists while any of our posts is queued, so it must
        never override a post that really did appear.

        The group's pending list is what settles it: this post is not on it, so
        the banner is about some other post of ours.
        """
        page = FakePage(
            body_text=f"A group feed. {self.BANNER}",
            after_post_text=BODY,
            pending_page_text=f"{self.CONTENT_PAGE} some other post of ours",
        )
        outcome = make_poster(page).post(request())
        assert outcome.pending is False
        assert outcome.verified is True

    def test_seeing_your_own_queued_post_is_not_publication(self):
        """Caught live in Hebrew on 2026-08-18, and it had shipped.

        Facebook shows the author their own queued post in the feed, so in a
        moderated group verify() succeeds on a post nobody else can see -- and
        the banner check never ran, because it only ran when the post was
        absent. The app recorded a confident "done" for a post the same page
        said was awaiting an admin: the cooldown and the wording were right by
        luck, but the follow-up never looked at it again, so an admin declining
        it would have locked those words to that group for ever.
        """
        page = FakePage(
            # Visible in the feed, banner up, AND on the group's pending list.
            body_text=f"{BODY} ... {self.BANNER}",
            pending_page_text=f"{self.CONTENT_PAGE} {BODY}",
        )
        outcome = make_poster(page).post(request())
        assert outcome.pending is True
        assert outcome.verified is False

    def test_a_broken_pending_list_leaves_a_visible_post_alone(self):
        """The extra check is a refinement, not a new way to fail: the post was
        seen, so anything unexpected keeps the verdict it already had."""

        class Awkward(FakePage):
            def goto(self, url, **kwargs):
                if strings.MY_CONTENT_PATH in url:
                    raise RuntimeError("that page would not load")
                return super().goto(url, **kwargs)

        page = Awkward(body_text=f"A group feed. {self.BANNER}", after_post_text=BODY)
        outcome = make_poster(page).post(request())
        assert outcome.verified is True
        assert outcome.pending is False

    def test_an_ordinary_group_with_no_banner_still_halts(self):
        """The safe default is unchanged: absent positive evidence of pending,
        an unverifiable post stops the batch."""
        page = self.moderated_page("A normal looking group feed")
        with pytest.raises(PostNotVerified):
            make_poster(page).post(request())

    # Exactly what each language renders, read off the live site rather than
    # translated -- 2026-08-17 (en) and 2026-08-18 (he, ru). Two of the three
    # differ from the obvious translation: Hebrew says בהמתנה, not ממתין, and
    # Russian says подтверждения, not одобрения.
    LIVE_BANNERS = [
        "Pending admin approval 1 post Learn more Manage post",
        "בהמתנה לאישור מנהל פוסט אחד לקבלת מידע נוסף ניהול הפוסט",
        "Ожидает подтверждения администратора 1 публикация Подробнее "
        "Управление публикацией",
    ]

    @pytest.mark.parametrize("banner", LIVE_BANNERS, ids=["en", "he", "ru"])
    def test_the_banner_is_recognised_in_each_language(self, banner):
        page = self.moderated_page(f"A normal looking group feed. {banner}")
        outcome = make_poster(page).post(request())
        assert outcome.pending is True


class TestFindingOutWhatBecameOfAPendingPost:
    """`pending_verdict` reads the group's "Your content" page, whose default
    tab is Pending.

    Everything here turns on one asymmetry: a plain decline leaves no positive
    trace, so "declined" is an absence -- and an absence is also what a page
    that failed to load looks like. Only "unknown" is safe to guess.
    """

    PAGE = "Your content Manage and view your posts"

    def test_still_listed_means_still_waiting(self):
        page = FakePage(body_text=f"{self.PAGE} {BODY}")
        assert make_poster(page).pending_verdict(GROUP_URL, BODY) == "pending"

    def test_gone_from_pending_but_in_the_feed_means_approved(self):
        page = FakePage(body_text=self.PAGE)
        # goto() to the group leaves the same body text, so the snippet has to
        # come from somewhere: verify() finds it through the text locator.
        assert make_poster(page).pending_verdict(GROUP_URL, BODY) == "approved"

    def test_gone_from_both_means_declined(self):
        page = FakePage(
            body_text=self.PAGE, missing=(f"text={distinctive_snippet(BODY)}",)
        )
        assert make_poster(page).pending_verdict(GROUP_URL, BODY) == "declined"

    def test_a_page_that_never_rendered_is_unknown_not_declined(self):
        """The whole reason MY_CONTENT_PAGE_MARKERS exists. Reading "declined"
        off a blank page releases the wording while the post is still queued."""
        page = FakePage(
            body_text="", missing=(f"text={distinctive_snippet(BODY)}",)
        )
        assert make_poster(page).pending_verdict(GROUP_URL, BODY) == "unknown"

    @pytest.mark.parametrize(
        "page",
        [
            FakePage(
                redirect_to=f"https://www.facebook.com{strings.CHECKPOINT_MARKERS[0]}"
            ),
            FakePage(body_text=strings.RATE_LIMIT_MARKERS[0]),
        ],
        ids=["checkpoint", "rate limit"],
    )
    def test_an_anomaly_raises_rather_than_reading_as_unknown(self, page):
        """Neither carries the page markers, so both would come back as a
        polite "unknown" and be retried twice a day while the user was never
        told -- and the rate-limit one would go on opening group pages straight
        through a block."""
        with pytest.raises(AutomationHalted):
            make_poster(page).pending_verdict(GROUP_URL, BODY)

    def test_an_empty_body_asks_nothing(self):
        page = FakePage(body_text=self.PAGE)
        assert make_poster(page).pending_verdict(GROUP_URL, "   ") == "unknown"
        assert page.call_names() == []

    # The header and blurb each language actually renders, read off the live
    # page on the dates above. The tabs are not here on purpose: the app relies
    # on Pending being the default rather than clicking a translated name.
    LIVE_PAGES = [
        "SneakerHeads in Israel › Your content Manage and view your posts",
        "SneakerHeads in Israel › התוכן שלך "
        "באפשרותך לנהל את הפוסטים שלך ולעיין בהם בקבוצה",
        "SneakerHeads in Israel › Ваш контент "
        "Управление публикациями в группе и их просмотр",
    ]

    @pytest.mark.parametrize("page_text", LIVE_PAGES, ids=["en", "he", "ru"])
    def test_the_page_is_recognised_in_each_language(self, page_text):
        """If this misses, the follow-up answers "unknown" for ever and a
        pending post is never resolved on its own."""
        page = FakePage(body_text=f"{page_text} {BODY}")
        assert make_poster(page).pending_verdict(GROUP_URL, BODY) == "pending"


class TestAFailureAfterThePostClick:
    """Once Post is clicked, the post may be live whatever happens next.

    A timeout while checking used to reach the worker as a plain error, which
    recorded "Nothing was posted" -- about a post that may well have gone out.
    """

    POST_CLICK = f"role=dialog:None >> {POST_BUTTON}"

    def test_a_timeout_while_checking_says_it_may_have_gone_out(self):
        page = FakePage(missing=(f"text={distinctive_snippet(BODY)}",))

        def stalls(url, timeout=0, wait_until=""):
            raise TimeoutError("Timeout 45000ms exceeded.")

        page.on_click[self.POST_CLICK] = lambda: setattr(page, "goto", stalls)
        with pytest.raises(PostNotVerified) as caught:
            make_poster(page).post(request())

        assert caught.value.may_have_posted is True
        assert "may well have gone out" in str(caught.value)
        assert "Nothing was posted" not in str(caught.value)

    def test_a_block_after_the_click_still_halts_and_says_it_may_be_out(self):
        page = FakePage()
        page.on_click[self.POST_CLICK] = lambda: setattr(
            page, "url", "https://www.facebook.com/checkpoint/1/"
        )
        with pytest.raises(AutomationHalted) as caught:
            make_poster(page).post(request())

        assert caught.value.may_have_posted is True
        assert "may have gone out" in str(caught.value)

    def test_a_block_before_the_click_is_not_marked_that_way(self):
        page = FakePage(redirect_to="https://www.facebook.com/checkpoint/1/")
        with pytest.raises(AutomationHalted) as caught:
            make_poster(page).post(request())
        assert caught.value.may_have_posted is False

    def test_a_failure_before_the_click_is_left_alone(self):
        with pytest.raises(ComposerNotFound):
            make_poster(FakePage(missing=NO_COMPOSER)).post(request())

    def test_a_post_window_that_never_closed_is_not_marked_maybe(self):
        """The post is not in the group and the window never closed: that one
        really did not go out, and the Queue may say so."""
        snippet = distinctive_snippet(BODY)
        page = FakePage(never_detaches=True, missing=(f"text={snippet}",))
        with pytest.raises(PostNotVerified) as caught:
            make_poster(page).post(request())
        assert caught.value.may_have_posted is False


def creation_reply(
    post_id: str = "1746810129710285", url: bool = True, held: bool = False
) -> str:
    """Shaped like Facebook's reply read live on 2026-10-07, trimmed.

    `held` is a group holding the post for an admin, read live on 2026-10-21:
    the same reply, with a link of its own shape.
    """
    story = {
        "id": "UzpfSTEwMDAxMDIyMDcwNzIyNTpWSzoxNzQ2ODEwMTI5NzEwMjg1",
        "legacy_story_hideable_id": post_id,
    }
    if held:
        story["url"] = f"{GROUP_URL}pending_posts/{post_id}/"
        story["if_viewer_can_learn_more_about_pending_post"] = {"__typename": "Story"}
    elif url:
        story["url"] = f"{GROUP_URL}permalink/{post_id}/"
    return json.dumps(
        {
            "data": {
                "story_create": {
                    "story_id": None,
                    "post_id": None,
                    "publishing_flow": "FALLBACK",
                    "story": story,
                }
            },
            "extensions": {},
        }
    )


class TestReadingFacebooksReply:
    def test_the_live_shape_gives_the_post_its_link(self):
        assert created_post_url(creation_reply()) == (
            f"{GROUP_URL}permalink/1746810129710285/"
        )

    def test_the_link_is_built_from_the_id_when_there_is_no_url(self):
        reply = creation_reply("42", url=False)
        assert created_post_url(reply, GROUP_URL) == f"{GROUP_URL}permalink/42/"

    def test_a_reply_with_no_post_is_nothing(self):
        refused = json.dumps({"errors": [{"message": "nope"}], "data": None})
        empty = json.dumps({"data": {"story_create": {"story": None}}})
        for reply in (refused, empty, "", "not json at all"):
            assert created_post_url(reply, GROUP_URL) == ""

    def test_the_old_json_guard_prefix_is_read_through(self):
        assert created_post_url("for (;;);" + creation_reply()).endswith("/1746810129710285/")

    def test_facebooks_own_link_says_live(self):
        created = read_created_post(creation_reply())
        assert created.from_facebook and created.live and not created.held

    def test_facebooks_own_link_says_held(self):
        """Read live on 2026-10-21 in a group with post approval."""
        created = read_created_post(creation_reply("42", held=True))
        assert created.url == f"{GROUP_URL}pending_posts/42/"
        assert created.held and not created.live

    def test_a_link_built_from_the_number_says_neither(self):
        created = read_created_post(creation_reply("42", url=False), GROUP_URL)
        assert created.url == f"{GROUP_URL}permalink/42/"
        assert not created.from_facebook
        assert not created.held and not created.live


class TestFacebooksReplyConfirmsThePost:
    """Every post gets its own id in Facebook's reply to the Post click, so it
    confirms *this* post -- which finding its words in the feed cannot, once
    the same words may go to a group every day. Read live on 2026-10-07."""

    def test_a_text_post_is_confirmed_with_its_link(self):
        page = FakePage(created_reply=creation_reply())
        outcome = make_poster(page).post(request())
        assert outcome.posted and outcome.verified
        assert outcome.post_url.endswith("/permalink/1746810129710285/")

    def test_it_does_not_depend_on_finding_the_words(self):
        snippet = distinctive_snippet(BODY)
        page = FakePage(created_reply=creation_reply(), missing=(f"text={snippet}",))
        outcome = make_poster(page).post(request())
        assert outcome.verified

    def test_text_sent_before_is_confirmed_by_the_reply(self):
        page = FakePage(created_reply=creation_reply())
        outcome = make_poster(page).post(request(sent_before=True))
        assert outcome.verified

    def test_pictures_are_confirmed_too(self):
        page = FakePage(created_reply=creation_reply())
        outcome = make_poster(page).post(
            request(body="", media_paths=(Path("a.png"),))
        )
        assert outcome.verified is True
        assert outcome.post_url

    def test_a_reply_read_after_navigating_is_gone(self):
        """Live, the reply could not be read once the page had moved on. The
        fake refuses the same way, which is what makes the tests above fail
        if the poster ever reads it after looking at the group."""
        page = FakePage(created_reply=creation_reply())
        seen = []
        page.on("response", seen.append)
        page._clicked(f"role=dialog:None >> {POST_BUTTON}")
        page.goto(GROUP_URL)
        with pytest.raises(RuntimeError):
            seen[0].text()

    def test_a_held_post_is_pending_by_its_own_link(self):
        """No banner and no words needed: the reply says so (2026-10-21)."""
        page = FakePage(created_reply=creation_reply("42", held=True))
        outcome = make_poster(page).post(
            request(body="", media_paths=(Path("a.png"),))
        )
        assert outcome.pending is True
        assert outcome.post_url == f"{GROUP_URL}pending_posts/42/"

    def test_a_held_text_post_needs_no_look_at_the_pending_list(self):
        page = FakePage(created_reply=creation_reply("42", held=True))
        outcome = make_poster(page).post(request())
        assert outcome.pending is True
        assert not any(
            strings.MY_CONTENT_PATH in str(call[1]) for call in page.calls if call[0] == "goto"
        )

    def test_a_live_link_is_live_whatever_the_banner_says(self):
        """The banner shows while any post of ours is held. Facebook's own
        link says this one was published."""
        page = FakePage(
            created_reply=creation_reply(),
            body_text=f"A group feed. {strings.PENDING_APPROVAL_MARKERS[0]}",
        )
        outcome = make_poster(page).post(
            request(body="", media_paths=(Path("a.png"),))
        )
        assert outcome.pending is False and outcome.verified is True

    def test_a_link_built_from_the_number_leaves_it_to_the_banner(self):
        page = FakePage(
            created_reply=creation_reply(url=False),
            body_text=f"A group feed. {strings.PENDING_APPROVAL_MARKERS[0]}",
        )
        outcome = make_poster(page).post(
            request(body="", media_paths=(Path("a.png"),))
        )
        assert outcome.pending is True
        assert outcome.post_url

    def test_a_block_after_the_post_still_halts(self):
        page = FakePage(created_reply=creation_reply())
        page.on_click[f"role=dialog:None >> {POST_BUTTON}"] = lambda: setattr(
            page, "redirect_to", "https://www.facebook.com/checkpoint/1/"
        )
        with pytest.raises(AutomationHalted):
            make_poster(page).post(request())

    def test_the_listener_is_removed_afterwards(self):
        page = FakePage(created_reply=creation_reply())
        make_poster(page).post(request())
        assert page.listeners.get("response") == []


class TestWithoutFacebooksReply:
    """If the reply never comes -- Facebook renamed the call, say -- the older
    checks decide, and they must not be fooled by an older post."""

    def test_text_never_sent_before_is_found_by_its_words(self):
        outcome = make_poster(FakePage(after_post_text=BODY)).post(request())
        assert outcome.verified
        assert not outcome.unconfirmed

    def test_text_sent_before_might_have_posted(self):
        """The words may belong to yesterday's post. Not a halt: the batch
        carries on (the user's choice, 2026-10-20)."""
        outcome = make_poster(FakePage()).post(request(sent_before=True))
        assert outcome.posted and outcome.unconfirmed
        assert not outcome.verified
        assert "same words were there before" in outcome.detail

    def test_words_already_in_the_group_might_have_posted(self):
        """A colleague's identical listing, or a post made by hand: the app's
        own history knows about neither, the look on arrival does."""
        page = FakePage(body_text=f"A group feed. Someone else wrote: {BODY}")
        outcome = make_poster(page).post(request())
        assert outcome.posted and outcome.unconfirmed
        assert not outcome.verified

    def test_words_shown_by_the_scroll_on_arrival_count_too(self):
        page = FakePage()
        original_wheel = page.mouse.wheel

        def wheel(x, y):
            # Further down the feed, before anything has been typed.
            if not page.keyboard.typed:
                page.body_text = f"An older post: {BODY}"
            original_wheel(x, y)

        page.mouse.wheel = wheel
        outcome = make_poster(page).post(request())
        assert outcome.unconfirmed

    def test_the_words_are_matched_as_a_person_reads_them(self):
        """Case, spacing and marks that print nothing do not hide them."""
        shown = "‏" + "  ".join(BODY.upper().split())
        outcome = make_poster(FakePage(body_text=shown)).post(request())
        assert outcome.unconfirmed

    def test_the_look_on_arrival_adds_no_waiting(self):
        """It reads the text the page already shows (the user's choice,
        2026-10-20): no extra pause and no extra scrolling."""
        page = FakePage(after_post_text=BODY)
        make_poster(page).post(request())
        names = page.call_names()
        assert set(names[: names.index("hover")]) == {"goto", "wheel"}

    def test_the_reply_decides_even_when_the_words_were_there(self):
        page = FakePage(created_reply=creation_reply(), body_text=f"Old: {BODY}")
        outcome = make_poster(page).post(request(sent_before=True))
        assert outcome.verified and not outcome.unconfirmed
        assert outcome.post_url

    def test_a_group_holding_the_post_is_still_pending(self):
        """Seen in the feed, the banner up, and on the group's pending list:
        held for an admin, whatever was in the group before."""
        page = FakePage(
            body_text=f"Old: {BODY} {strings.PENDING_APPROVAL_MARKERS[0]}",
            pending_page_text=f"Your content {BODY}",
        )
        outcome = make_poster(page).post(request(sent_before=True))
        assert outcome.pending and not outcome.unconfirmed

    def test_pictures_say_they_could_not_be_checked(self):
        outcome = make_poster(FakePage()).post(
            request(body="", media_paths=(Path("a.png"),))
        )
        assert outcome.posted and not outcome.verified
        assert "could not check that it posted" in outcome.detail


class TestAHeldPostsOwnLink:
    """Once an admin approves, a held post's own link opens the live post at
    .../posts/<id>/; held or declined, it stays put. Read live on 2026-10-21."""

    HELD = f"{GROUP_URL}pending_posts/42/"

    def test_approved_it_leads_to_the_live_post(self):
        page = FakePage(redirect_to=f"{GROUP_URL}posts/42/?ref=x")
        assert make_poster(page).live_link(self.HELD) == f"{GROUP_URL}posts/42/"

    def test_held_or_declined_it_stays_put(self):
        assert make_poster(FakePage()).live_link(self.HELD) == ""

    def test_a_page_that_would_not_load_is_unknown(self):
        page = FakePage(goto_error="net::ERR_NETWORK_IO_SUSPENDED")
        assert make_poster(page).live_link(self.HELD) is None

    def test_somewhere_else_is_unknown(self):
        page = FakePage(redirect_to=GROUP_URL)
        assert make_poster(page).live_link(self.HELD) is None

    def test_another_posts_link_is_unknown(self):
        page = FakePage(redirect_to=f"{GROUP_URL}posts/43/")
        assert make_poster(page).live_link(self.HELD) is None

    def test_a_checkpoint_halts(self):
        page = FakePage(redirect_to="https://www.facebook.com/checkpoint/1/")
        with pytest.raises(AutomationHalted):
            make_poster(page).live_link(self.HELD)

    def test_unavailable_words_in_the_feed_do_not_hide_it(self):
        """The live post opens in the group's feed, where someone's shared
        post can read "content isn't available"."""
        page = FakePage(
            redirect_to=f"{GROUP_URL}posts/42/",
            body_text=f"A feed. {strings.UNAVAILABLE_MARKERS[0]}",
        )
        assert make_poster(page).live_link(self.HELD) == f"{GROUP_URL}posts/42/"
