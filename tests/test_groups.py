"""Tests for Facebook group URL parsing."""

from __future__ import annotations

import pytest

from fbposter.groups import is_group_url, parse_group_url


class TestAccepted:
    @pytest.mark.parametrize(
        "url,identifier",
        [
            ("https://www.facebook.com/groups/123456789", "123456789"),
            ("https://www.facebook.com/groups/123456789/", "123456789"),
            ("https://facebook.com/groups/my.group.name/", "my.group.name"),
            ("https://m.facebook.com/groups/mobilegroup", "mobilegroup"),
            ("https://web.facebook.com/groups/webgroup", "webgroup"),
            ("http://www.facebook.com/groups/insecure", "insecure"),
            # Copied straight out of the address bar, junk and all.
            ("https://www.facebook.com/groups/123/posts/456/", "123"),
            ("https://www.facebook.com/groups/123?ref=share", "123"),
            ("  https://www.facebook.com/groups/spaced/  ", "spaced"),
            # Pasted without a scheme.
            ("www.facebook.com/groups/noscheme", "noscheme"),
            ("facebook.com/groups/bare", "bare"),
        ],
    )
    def test_identifier_is_extracted(self, url, identifier):
        ref = parse_group_url(url)
        assert ref is not None
        assert ref.identifier == identifier

    def test_url_is_canonicalised(self):
        """Different forms of the same group must converge, otherwise the same
        group could be added twice and posted to twice in one batch."""
        forms = [
            "https://m.facebook.com/groups/123456789",
            "https://www.facebook.com/groups/123456789/posts/999",
            "facebook.com/groups/123456789?ref=bookmarks",
        ]
        canonical = {parse_group_url(form).url for form in forms}
        assert canonical == {"https://www.facebook.com/groups/123456789/"}

    def test_numeric_flag(self):
        assert parse_group_url("https://www.facebook.com/groups/123").is_numeric
        assert not parse_group_url("https://www.facebook.com/groups/slug").is_numeric


class TestRejected:
    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "https://www.facebook.com/",
            "https://www.facebook.com/groups/",
            "https://www.facebook.com/someone.page",
            "https://www.facebook.com/profile.php?id=123",
            "https://www.instagram.com/groups/123",
            "https://evil.com/groups/123",
            # A lookalike host must not pass.
            "https://facebook.com.evil.com/groups/123",
            "ftp://www.facebook.com/groups/123",
            "not a url at all",
        ],
    )
    def test_non_group_urls_are_rejected(self, url):
        assert parse_group_url(url) is None
        assert is_group_url(url) is False

    @pytest.mark.parametrize("reserved", ["feed", "discover", "create", "your_groups"])
    def test_facebooks_own_group_screens_are_not_groups(self, reserved):
        assert parse_group_url(f"https://www.facebook.com/groups/{reserved}/") is None
