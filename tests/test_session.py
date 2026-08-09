"""Tests for the session checks.

The two functions that decide "is this session usable?" are pure, so the whole
decision table can be tested without a browser.
"""

from __future__ import annotations

import pytest

from fbposter import session, strings
from fbposter.session import UrlVerdict


def cookie(name: str, value: str = "100012345678") -> dict[str, str]:
    return {"name": name, "value": value, "domain": ".facebook.com", "path": "/"}


class TestFindUserId:
    def test_returns_the_id_from_the_login_cookie(self):
        cookies = [cookie("datr", "abc"), cookie(strings.LOGIN_COOKIE, "100012345678")]
        assert session.find_user_id(cookies) == "100012345678"

    def test_none_when_the_login_cookie_is_absent(self):
        """A fresh profile has Facebook cookies but no c_user."""
        assert session.find_user_id([cookie("datr", "abc"), cookie("sb", "xyz")]) is None

    def test_none_for_no_cookies_at_all(self):
        assert session.find_user_id([]) is None

    def test_empty_value_counts_as_logged_out(self):
        """Facebook blanks c_user on logout rather than always deleting it."""
        assert session.find_user_id([cookie(strings.LOGIN_COOKIE, "")]) is None


class TestClassifyUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/",
            "https://www.facebook.com/groups/123456789",
            "https://www.facebook.com/groups/feed/",
        ],
    )
    def test_normal_urls_are_ok(self, url):
        assert session.classify_url(url) == UrlVerdict.OK

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/login/",
            "https://www.facebook.com/login.php?next=%2F",
            "https://www.facebook.com/recover/initiate/",
        ],
    )
    def test_login_redirects_are_detected(self, url):
        assert session.classify_url(url) == UrlVerdict.LOGIN

    @pytest.mark.parametrize(
        "url",
        [
            "https://www.facebook.com/checkpoint/1501092823525282/",
            "https://www.facebook.com/challenge/?next=x",
            "https://www.facebook.com/confirmemail.php",
        ],
    )
    def test_checkpoints_are_detected(self, url):
        assert session.classify_url(url) == UrlVerdict.CHECKPOINT

    def test_checkpoint_wins_over_login(self):
        """A checkpoint URL carrying a login next-param must not be read as a
        plain login redirect -- the two are handled very differently."""
        url = "https://www.facebook.com/checkpoint/?next=https%3A%2F%2Fwww.facebook.com%2Flogin"
        assert session.classify_url(url) == UrlVerdict.CHECKPOINT

    def test_matching_is_case_insensitive(self):
        assert session.classify_url("https://www.facebook.com/CHECKPOINT/123") == UrlVerdict.CHECKPOINT


class TestVerifySession:
    def test_no_cookie_short_circuits_without_navigating(self):
        """A fresh profile must be reported as logged out without opening a page."""

        class Context:
            def cookies(self, _url):
                return [cookie("datr", "abc")]

            def new_page(self):
                raise AssertionError("must not navigate when the login cookie is missing")

        status = session.verify_session(Context())
        assert status.logged_in is False
        assert status.user_id is None
