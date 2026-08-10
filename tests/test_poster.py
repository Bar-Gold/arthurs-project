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

    def test_the_photo_button_is_clicked_first(self):
        page = FakePage()
        make_poster(page).post(request(media_paths=(Path("a.png"),)))
        assert page.clicked(PHOTO_BUTTON)

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
        page = FakePage(wait_fails_for=("role=dialog:None",))
        with pytest.raises(TimeoutError):
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
