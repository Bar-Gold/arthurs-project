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

    @pytest.mark.parametrize(
        "text",
        [
            "Вы временно заблокированы",
            "Вы публикуете слишком часто",
            "Повторите попытку позже",
            "Эта функция сейчас недоступна",
        ],
    )
    def test_russian_rate_limit_wording(self, text):
        assert classify(GROUP_URL, text) is PageVerdict.RATE_LIMIT

    @pytest.mark.parametrize(
        "text",
        ["נחסמת באופן זמני", "נסה שוב מאוחר יותר"],
    )
    def test_hebrew_rate_limit_wording(self, text):
        assert classify(GROUP_URL, text) is PageVerdict.RATE_LIMIT

    def test_russian_matching_ignores_case(self):
        assert classify(GROUP_URL, "ВЫ ВРЕМЕННО ЗАБЛОКИРОВАНЫ") is PageVerdict.RATE_LIMIT

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

    def test_russian_unavailable(self):
        assert classify(GROUP_URL, "Материал недоступен") is PageVerdict.UNAVAILABLE

    def test_hebrew_unavailable(self):
        assert classify(GROUP_URL, "התוכן הזה לא זמין") is PageVerdict.UNAVAILABLE


class TestEveryLanguageIsCovered:
    """Each supported language must contribute to each composer lookup.

    The bug this guards against is subtle: a language can look supported
    because the composer opens, then fail at the Post click -- which is the one
    step that cannot be safely retried.
    """

    ALPHABETS = {
        "english": lambda s: any(c.isascii() and c.isalpha() for c in s),
        "hebrew": lambda s: any("֐" <= c <= "׿" for c in s),
        "russian": lambda s: any("Ѐ" <= c <= "ӿ" for c in s),
    }

    # Every language-dependent table, not just the composer ones. The account
    # language is the only thing that decides what Facebook renders -- the app
    # never asks for a locale -- so each of these has to match whichever of the
    # three it turns out to be.
    LANGUAGE_DEPENDENT = [
        "COMPOSER_TRIGGERS",
        "COMPOSER_TEXTBOX",
        "POST_BUTTONS",
        "PHOTO_VIDEO_BUTTONS",
        "CLOSE_BUTTONS",
        "DISCARD_PROMPT_BUTTONS",
        "RATE_LIMIT_MARKERS",
        "UNAVAILABLE_MARKERS",
        "PENDING_APPROVAL_MARKERS",
        "MY_CONTENT_PAGE_MARKERS",
    ]

    @pytest.mark.parametrize("language", list(ALPHABETS))
    @pytest.mark.parametrize("field", LANGUAGE_DEPENDENT)
    def test_the_lookup_has_a_candidate_in_each_language(self, language, field):
        from fbposter import strings

        has = self.ALPHABETS[language]
        candidates = getattr(strings, field)
        assert any(has(c) for c in candidates), f"{field} has no {language} candidate"

    def test_no_language_dependent_table_is_left_unguarded(self):
        """A new table added to strings.py must be listed above.

        Otherwise it can ship covering one language, and nothing notices until
        the account is switched.
        """
        from fbposter import strings

        tables = {
            name
            for name in dir(strings)
            if name.isupper() and isinstance(getattr(strings, name), tuple)
        }
        # URL fragments carry no language at all.
        tables -= {"CHECKPOINT_MARKERS", "LOGIN_MARKERS"}
        assert tables == set(self.LANGUAGE_DEPENDENT), (
            "strings.py gained or lost a language-dependent table; "
            f"unlisted: {sorted(tables - set(self.LANGUAGE_DEPENDENT))}, "
            f"stale: {sorted(set(self.LANGUAGE_DEPENDENT) - tables)}"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "You're temporarily blocked from posting",   # English
            "נחסמת באופן זמני",                          # Hebrew
            "Вы временно заблокированы",                 # Russian
        ],
    )
    def test_a_block_is_recognised_whatever_language_it_arrives_in(self, text):
        """The worst failure available: not noticing a block and posting on.

        Data coverage is not enough on its own -- this drives classify(), so
        case folding and substring matching are exercised in every script.
        """
        assert classify(GROUP_URL, text) is PageVerdict.RATE_LIMIT

    @pytest.mark.parametrize(
        "text",
        [
            "This content isn't available right now",
            "התוכן הזה לא זמין",
            "Материал недоступен",
        ],
    )
    def test_an_unavailable_group_is_recognised_in_each_language(self, text):
        assert classify(GROUP_URL, text) is PageVerdict.UNAVAILABLE

    def test_the_russian_post_button_is_the_real_one(self):
        """Read off the live composer. The plausible translation,
        "Опубликовать", is not what Facebook uses -- it is "Отправить"."""
        from fbposter import strings

        assert "Отправить" in strings.POST_BUTTONS
        assert "Опубликовать" not in strings.POST_BUTTONS


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
