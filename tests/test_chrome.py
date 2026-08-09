"""Tests for Chrome discovery and command-line construction.

None of these launch a browser; build_args is pure and find_chrome takes an
explicit candidate list.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fbposter import chrome, config
from fbposter.errors import ChromeLaunchError, ChromeNotFoundError

CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
PROFILE = Path(r"C:\FBAutomation\ChromeProfile")


def args(visible: bool) -> list[str]:
    return chrome.build_args(CHROME, PROFILE, 9222, visible=visible)


class TestBuildArgs:
    def test_debugging_port_is_always_paired_with_a_custom_profile(self):
        """Chrome 136+ ignores the port without it, so this pairing is the
        single most important thing in the command line."""
        for visible in (True, False):
            built = args(visible)
            assert "--remote-debugging-port=9222" in built
            assert f"--user-data-dir={PROFILE}" in built

    def test_offscreen_only_when_not_visible(self):
        offscreen = f"--window-position={config.OFFSCREEN_POSITION}"
        assert offscreen in args(visible=False)
        # The one-time Facebook login needs a window the user can actually see.
        assert offscreen not in args(visible=True)

    def test_background_throttling_is_disabled(self):
        """The window is always unfocused, so it must not be throttled."""
        built = args(visible=False)
        for flag in (
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
        ):
            assert flag in built

    def test_never_headless_and_never_flagged_as_automated(self):
        """Headless changes the fingerprint; --enable-automation announces itself."""
        for visible in (True, False):
            joined = " ".join(args(visible))
            assert "--headless" not in joined
            assert "--enable-automation" not in joined

    def test_chrome_path_is_argv0(self):
        assert args(visible=True)[0] == str(CHROME)


class TestFindChrome:
    def test_returns_first_existing_candidate(self, tmp_path):
        missing = tmp_path / "nope" / "chrome.exe"
        present = tmp_path / "chrome.exe"
        present.write_text("")
        assert chrome.find_chrome([missing, present]) == present

    def test_raises_when_nothing_found(self, tmp_path):
        with pytest.raises(ChromeNotFoundError):
            chrome.find_chrome([tmp_path / "chrome.exe"])

    def test_real_machine_has_chrome(self):
        """Sanity check against the actual install; skipped elsewhere."""
        try:
            found = chrome.find_chrome()
        except ChromeNotFoundError:
            pytest.skip("Chrome is not installed on this machine")
        assert found.is_file()


class TestProbe:
    def test_probe_returns_none_on_a_dead_port(self):
        # Port 1 is reserved and nothing will answer on it.
        assert chrome.probe(port=1, timeout=0.5) is None

    def test_is_running_is_false_on_a_dead_port(self):
        assert chrome.is_running(port=1) is False

    def test_wait_for_cdp_gives_up(self):
        with pytest.raises(ChromeLaunchError):
            chrome.wait_for_cdp(port=1, timeout=0.5)


class TestResolveProfileDir:
    def test_prefers_an_existing_directory_over_creating_one(self, tmp_path):
        """Switching profiles between runs would silently drop the login."""
        preferred = tmp_path / "preferred"
        fallback = tmp_path / "fallback"
        fallback.mkdir()

        assert config.resolve_profile_dir(preferred, fallback) == fallback
        assert not preferred.exists()

    def test_creates_the_preferred_directory_when_neither_exists(self, tmp_path):
        preferred = tmp_path / "preferred"
        fallback = tmp_path / "fallback"

        assert config.resolve_profile_dir(preferred, fallback) == preferred
        assert preferred.is_dir()

    def test_falls_back_when_preferred_cannot_be_created(self, tmp_path):
        # A file where the directory should go makes mkdir fail the same way an
        # unwritable C:\ root does.
        blocker = tmp_path / "blocked"
        blocker.write_text("")
        fallback = tmp_path / "fallback"

        assert config.resolve_profile_dir(blocker / "profile", fallback) == fallback

    def test_raises_when_both_are_impossible(self, tmp_path):
        blocker = tmp_path / "blocked"
        blocker.write_text("")
        with pytest.raises(ChromeLaunchError):
            config.resolve_profile_dir(blocker / "a", blocker / "b")
