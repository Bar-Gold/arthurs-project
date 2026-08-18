"""Tests for the posting flow, driven against a fake page.

No browser, no Facebook account, no network. What is asserted here is the
behaviour that keeps the account safe: the order of steps, that anomalies halt
instead of pressing on, that a dry run never publishes, and that a draft is
never left behind.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from fbposter import strings
from fbposter.automation.humanize import HumanProfile, Humanizer
from fbposter.automation.poster import GroupPoster, PostRequest, distinctive_snippet
from fbposter.errors import AutomationHalted, ComposerNotFound, PostNotVerified

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

        # The post surfaces only once the feed has been nudged.
        original_wheel = page.mouse.wheel

        def wheel(x, y):
            appears["yet"] = True
            original_wheel(x, y)

        page.mouse.wheel = wheel

        outcome = make_poster(page).post(request())
        assert outcome.verified

    def test_it_finds_the_post_in_the_page_text_alone(self):
        """Even when the locator never matches, the text being on the page is
        proof enough that it published."""
        page = FakePage(missing=(f"text={distinctive_snippet(BODY)}",), body_text=BODY)
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
            body_text=f"{BODY} ... {self.BANNER}",
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

        page = Awkward(body_text=f"{BODY} ... {self.BANNER}")
        outcome = make_poster(page).post(request())
        assert outcome.verified is True
        assert outcome.pending is False

    def test_an_ordinary_group_with_no_banner_still_halts(self):
        """The safe default is unchanged: absent positive evidence of pending,
        an unverifiable post stops the batch."""
        page = self.moderated_page("A normal looking group feed")
        with pytest.raises(PostNotVerified):
            make_poster(page).post(request())

    @pytest.mark.parametrize(
        "banner",
        [
            "Pending admin approval",
            "ממתין לאישור מנהל",
            "Ожидает одобрения администратора",
        ],
    )
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
