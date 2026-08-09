"""Paths, ports and Chrome flags.

Values here are constants; the one function that touches the filesystem is
resolve_profile_dir, which has to because it picks between two candidate
locations.
"""

from __future__ import annotations

import os
from pathlib import Path

from .errors import ChromeLaunchError

# Chrome 136+ ignores --remote-debugging-port unless it is paired with a
# --user-data-dir pointing somewhere other than the default profile, so the app
# gets its own profile directory and the user logs into Facebook there once.
PREFERRED_PROFILE_DIR = Path(r"C:\FBAutomation\ChromeProfile")
FALLBACK_PROFILE_DIR = (
    Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FBAutomation" / "ChromeProfile"
)

DEBUG_PORT = 9222
# 127.0.0.1 rather than localhost: Chrome validates the Host header on the
# debugging port and rejects names it does not recognise.
CDP_HOST = "127.0.0.1"

# Far enough off-screen to be invisible on any monitor arrangement. Off-screen
# rather than minimised: Chrome throttles minimised windows harder and can skip
# rendering entirely, which breaks element interaction.
OFFSCREEN_POSITION = "-32000,-32000"

LAUNCH_TIMEOUT_S = 30.0
POLL_INTERVAL_S = 0.25
PROBE_TIMEOUT_S = 1.0
NAV_TIMEOUT_MS = 30_000


def cdp_endpoint(port: int = DEBUG_PORT) -> str:
    """The HTTP endpoint Playwright's connect_over_cdp accepts."""
    return f"http://{CDP_HOST}:{port}"


def chrome_candidates() -> tuple[Path, ...]:
    """Known chrome.exe locations, in the order they should be tried."""
    roots = (
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    )
    return tuple(
        Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe"
        for root in roots
        if root
    )


def resolve_profile_dir(
    preferred: Path = PREFERRED_PROFILE_DIR,
    fallback: Path = FALLBACK_PROFILE_DIR,
) -> Path:
    """Return the profile directory to use, creating it if necessary.

    An existing directory always wins over creating a new one. Silently
    switching profiles between runs would look to the user like their Facebook
    login had spontaneously disappeared, since the session lives in the profile.

    Creating a directory at the C:\\ root can require elevation, so the
    LOCALAPPDATA fallback exists for machines where the preferred path fails.
    """
    for candidate in (preferred, fallback):
        if candidate.is_dir():
            return candidate

    for candidate in (preferred, fallback):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        except OSError:
            continue

    raise ChromeLaunchError(
        f"Could not create a Chrome profile directory at {preferred} or {fallback}."
    )
