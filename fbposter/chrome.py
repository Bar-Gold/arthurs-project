"""Locating and launching the dedicated Chrome debug profile.

The app never launches a browser through Playwright. It starts real Chrome with
a debugging port and attaches to it, so the session keeps the user's genuine
cookies, IP and device fingerprint, and no automation flags are set on it.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Sequence

from . import config
from .errors import ChromeLaunchError, ChromeNotFoundError

# One launch at a time. Several things start Chrome -- the startup check, the
# wizard's button, the window noticing it has closed (see keepalive.py) -- and
# two of them racing would each see the port closed and each start a Chrome on
# the same profile. The second one then just opens another window in the
# first. Held for the whole launch, so the loser waits, finds the port open,
# and starts nothing.
_LAUNCH_LOCK = threading.Lock()


def find_chrome(candidates: Sequence[Path] | None = None) -> Path:
    """Return the path to chrome.exe, or raise ChromeNotFoundError."""
    searched = tuple(candidates) if candidates is not None else config.chrome_candidates()
    for path in searched:
        if Path(path).is_file():
            return Path(path)
    raise ChromeNotFoundError(
        "Could not find chrome.exe. Looked in:\n  "
        + "\n  ".join(str(p) for p in searched)
    )


def build_args(
    chrome: Path,
    profile_dir: Path,
    port: int = config.DEBUG_PORT,
    *,
    visible: bool,
) -> list[str]:
    """Build the Chrome command line.

    Pure function, so the flags that matter most can be asserted in tests
    without launching anything.
    """
    args = [
        str(chrome),
        f"--remote-debugging-port={port}",
        # Mandatory partner to the port above on Chrome 136+, not a preference.
        f"--user-data-dir={profile_dir}",
        # This window spends its whole life unfocused and off-screen. Without
        # these three flags Chrome throttles timers and backgrounds the renderer,
        # which makes pages behave differently from a focused window.
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if not visible:
        args.append(f"--window-position={config.OFFSCREEN_POSITION}")
    return args


def probe(port: int = config.DEBUG_PORT, timeout: float = config.PROBE_TIMEOUT_S) -> dict[str, Any] | None:
    """Return Chrome's /json/version payload, or None if nothing is listening.

    Doubles as the "is it already running?" check and as confirmation that
    whatever holds the port really is Chrome.
    """
    url = f"{config.cdp_endpoint(port)}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError):
        return None


def is_running(port: int = config.DEBUG_PORT) -> bool:
    return probe(port) is not None


# How long a Chrome that holds the profile but has not answered is given to
# answer, before launch() gives up on it rather than starting another.
BUSY_GRACE_S = 15.0


def profile_in_use(profile_dir: Path) -> bool:
    """Whether a Chrome is running on this profile, answering the port or not.

    Chrome holds `<profile>/lockfile` open for as long as it runs, created
    delete-on-close, so while it lives nobody else may open the file, and when
    it exits Windows deletes it. That tells the difference the debugging port
    cannot: "not answering" is either gone, or busy -- starting up, loading a
    heavy page, on a machine running flat out -- and the two need opposite
    handling. Launching Chrome on a profile a busy Chrome still holds hands the
    launch to it, and Chrome may shut the busy one down to take over, which is
    how a slow second once replaced the app's Chrome outright. Verified live
    on 2026-09-25: the file exists and refuses to open while Chrome runs.
    """
    lock = Path(profile_dir) / "lockfile"
    if not lock.exists():
        return False
    try:
        with open(lock, "rb"):
            return False  # it opened: nothing holds it; left over, not live
    except PermissionError:
        return True
    except OSError:
        return False


def wait_for_cdp(port: int = config.DEBUG_PORT, timeout: float = config.LAUNCH_TIMEOUT_S) -> dict[str, Any]:
    """Poll the debugging port until Chrome answers, or raise ChromeLaunchError."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        version = probe(port)
        if version is not None:
            return version
        time.sleep(config.POLL_INTERVAL_S)

    raise ChromeLaunchError(
        f"Chrome did not open a debugging port on {port} within {timeout:.0f}s.\n"
        "The usual cause is another Chrome already running with the same profile "
        "directory but without the debugging flag."
    )


def _creation_flags() -> int:
    """Detach the child so Chrome outlives this Python process.

    The user logs into Facebook once and that session has to survive every
    later run of the app.
    """
    if os.name != "nt":
        return 0
    detached = getattr(subprocess, "DETACHED_PROCESS", 0)
    new_group = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    return detached | new_group


def launch(
    profile_dir: Path,
    port: int = config.DEBUG_PORT,
    *,
    visible: bool,
) -> bool:
    """Start Chrome on the debugging port if it is not already up.

    Returns True if a new process was started, False if an existing one was
    reused. `visible` controls the one difference that matters: the initial
    Facebook login needs an on-screen window, and everything after it does not.
    """
    with _LAUNCH_LOCK:
        if is_running(port):
            return False
        if profile_in_use(profile_dir):
            # Running on this profile, just not answering yet: never start a
            # second one on top of it (see profile_in_use). Give it time to
            # answer; if it never does, wait_for_cdp says why -- most often a
            # Chrome opened on this profile without the debugging port.
            wait_for_cdp(port, timeout=BUSY_GRACE_S)
            return False

        chrome = find_chrome()
        profile_dir.mkdir(parents=True, exist_ok=True)
        args = build_args(chrome, profile_dir, port, visible=visible)

        subprocess.Popen(
            args,
            creationflags=_creation_flags(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
        wait_for_cdp(port)
        return True
