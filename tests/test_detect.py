"""Tests for page classification.

Anything other than OK halts the whole batch, so both false negatives (missing
a block) and false positives (halting on a normal feed) matter.
"""

from __future__ import annotations

import pytest

from fbposter.automation.detect import PageVerdict, classify, halt_message

GROUP_URL = "https://www.facebook.com/groups/123456789/"


class TestNormalPages:
    @pytest.mark.parametrize(
        "text",
        [
            "A normal group feed",
            "Write something...",
            "Members  Discussion  Featured",
            "",
        ],
    )
    def test_a_normal_group_page_is_ok(self, text):
        assert classify(GROUP_URL, text) is PageVerdict.OK

    def test_ordinary_words_do_not_trip_the_detector(self):
        """Halting on a normal feed would be as bad as missing a real block."""
        text = "Selling a bike. Try again later if it is gone. Blocked drain fixed."
        # "try again later" is a real marker, so this one SHOULD trip -- the
        # point of the test is that it is a deliberate, known tradeoff.
        assert classify(GROUP_URL, text) is PageVerdict.RATE_LIMIT


class TestCheckpoints:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/checkpoint/1234/",
            "https://www.facebook.com/challenge/?next=x",
        ],
    )
    def test_checkpoint_urls(self, url):
        assert classify(url, "") is PageVerdict.CHECKPOINT

    def test_a_checkpoint_outranks_everything_else(self):
        """The most serious signal must win, whatever else is on the page."""
        verdict = classify(
            "https://www.facebook.com/checkpoint/1/", "you're temporarily blocked"
        )
        assert verdict is PageVerdict.CHECKPOINT


class TestRateLimits:
    @pytest.mark.parametrize(
        "text",
        [
            "You're Temporarily Blocked",
            "It looks like you were posting too fast.",
            "Please try again later",
            "We limit how often you can post",
            "This feature isn't available right now",
        ],
    )
    def test_rate_limit_wording(self, text):
        assert classify(GROUP_URL, text) is PageVerdict.RATE_LIMIT

    def test_matching_ignores_case(self):
        assert classify(GROUP_URL, "YOU'RE TEMPORARILY BLOCKED") is PageVerdict.RATE_LIMIT

    def test_a_rate_limit_outranks_a_login_redirect(self):
        """What the account's standing is matters more than where we landed."""
        verdict = classify(
            "https://www.facebook.com/login/", "you're temporarily blocked"
        )
        assert verdict is PageVerdict.RATE_LIMIT


class TestOtherStates:
    def test_login_redirect(self):
        assert classify("https://www.facebook.com/login/", "") is PageVerdict.LOGIN

    def test_unavailable_group(self):
        assert (
            classify(GROUP_URL, "This content isn't available right now")
            is PageVerdict.UNAVAILABLE
        )

    def test_non_member(self):
        assert classify(GROUP_URL, "You must be a member to see this") is PageVerdict.UNAVAILABLE


class TestHaltMessages:
    @pytest.mark.parametrize("verdict", [v for v in PageVerdict if v is not PageVerdict.OK])
    def test_every_bad_verdict_explains_itself(self, verdict):
        message = halt_message(verdict)
        assert message and len(message) > 20

    def test_the_checkpoint_message_says_we_will_not_click_through(self):
        assert "never click through" in halt_message(PageVerdict.CHECKPOINT)

    def test_the_rate_limit_message_says_to_stop_posting(self):
        assert "do not post again" in halt_message(PageVerdict.RATE_LIMIT).lower()


class TestVerdictHelper:
    def test_only_ok_is_ok(self):
        assert PageVerdict.OK.is_ok
        for verdict in PageVerdict:
            if verdict is not PageVerdict.OK:
                assert not verdict.is_ok
