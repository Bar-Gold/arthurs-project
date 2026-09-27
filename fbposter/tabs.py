"""A tab that has stopped answering, in the app's own Chrome: noticing it and
closing it.

A client installed the app, logged in, saw the light go green -- and then the
group names never loaded, and Check connection sat on "Checking..." for good.
Closing the background Chrome by hand put it right. What had happened was
reproduced exactly, live, on 2026-09-27 against Chrome 153, on a throwaway
Chrome of its own (its own profile, port 9333, no Facebook):

* **While any tab has a JavaScript dialog open** -- an alert, a confirm, a
  "Leave site?" -- **or its page is stuck in a loop, Playwright's
  `connect_over_cdp` hangs for ever.** Its own 30-second timeout does not
  fire; it was still waiting at 60 and at 90. Every connection check, every
  name lookup and every post goes through that connect, so all of them hang
  with it.
* **The debugging port goes on answering `/json/version` throughout**, so the
  keep-alive watcher (`keepalive.py`) sees a perfectly healthy Chrome.
* **`Runtime.getIsolateId` tells the two apart.** A healthy tab answers it
  instantly; a stuck one never does. It runs nothing in the page.
* **Closing just the stuck tab (`Target.closeTarget`) is enough**, and a
  connect already hung on it then finishes by itself -- the hung one in the
  test completed 0.6s after the tab went. No restart needed.
* **`Browser.close` shuts Chrome down cleanly even with stuck tabs open**, the
  profile released in 1.4s. That is the last resort, `restart_chrome`, used
  only when a check has gone unanswered past its deadline anyway.

**Only the app's own Chrome is ever touched, and that is proven rather than
assumed.** The process behind the debugging port must have been started with
`--user-data-dir=` the app's own profile, read from Windows itself. The
client's everyday Chrome cannot even open the port (Chrome 136+ refuses it on
the default profile), and anything else found answering there is asked which
process it is and nothing more: no tab of it is looked at, and it is never
closed.

**Nothing is closed from under a post.** A page the worker is posting through
can be busy for a while on a slow machine, and closing it mid-post loses the
post. `posting()` marks those pages, and while one is open nothing here looks
at a single tab or restarts anything.
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from . import chrome, config
from .cdp import CdpClient, CdpError

# How long a tab has to answer. A healthy one takes about a millisecond.
ANSWER_S = 3.0
# A tab that did not answer gets a second chance this much later, so a page
# merely busy loading -- Facebook on a slow laptop -- is not taken for stuck.
# Only a tab silent on both looks, more than ten seconds in all, is closed.
SECOND_LOOK_S = 5.0
# How long Chrome gets to close before a restart gives up on it.
CLOSE_WAIT_S = 20.0

# What restart_chrome reports.
RESTARTED = "restarted"
NOT_OURS = "not_ours"
WOULD_NOT_CLOSE = "would_not_close"
POSTING = "posting"
GONE = "gone"

_LOCK = threading.Lock()
_posting = 0


@contextmanager
def posting() -> Iterator[None]:
    """Held by the worker while a page of its own is open in Chrome."""
    global _posting
    with _LOCK:
        _posting += 1
    try:
        yield
    finally:
        with _LOCK:
            _posting -= 1


def posting_now() -> bool:
    return _posting > 0


def clear_stuck_tabs(
    port: int = config.DEBUG_PORT,
    profile_dir: Path | None = None,
    *,
    open_client: Callable[[str], CdpClient] = CdpClient.open,
    command_line_of: Callable[[int], list[str] | None] | None = None,
    probe: Callable[[], dict | None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    """Close every tab of the app's Chrome that has stopped answering.

    Returns the titles of what was closed -- usually nothing. Never raises:
    it runs in front of every connection, and a failure to look must never be
    the thing that stops one.
    """
    with _LOCK:
        if _posting:
            return []
        try:
            version = (probe or (lambda: chrome.probe(port)))()
            client = _owned_client(version, profile_dir, open_client, command_line_of)
            if client is None:
                return []
            with client:
                return _close_silent(client, sleep)
        except Exception:
            return []


def restart_chrome(
    port: int = config.DEBUG_PORT,
    profile_dir: Path | None = None,
    *,
    launch: Callable[[], object] | None = None,
    open_client: Callable[[str], CdpClient] = CdpClient.open,
    command_line_of: Callable[[int], list[str] | None] | None = None,
    probe: Callable[[], dict | None] | None = None,
    profile_busy: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str:
    """Close the app's own Chrome and start it again. The last resort.

    Used only when a connection check has gone unanswered past its deadline
    even so, and never while a post is in progress. `Browser.close` is Chrome
    shutting itself down, the same as closing its window: the Facebook login
    is on disk in the profile and comes back with it, exactly as it did when
    the client closed the window by hand.
    """
    profile = profile_dir or config.resolve_profile_dir()
    launch = launch or (lambda: chrome.launch(profile, port, visible=False))
    probe = probe or (lambda: chrome.probe(port))
    profile_busy = profile_busy or (lambda: chrome.profile_in_use(profile))
    with _LOCK:
        if _posting:
            return POSTING
        version = probe()
        if version is None:
            return GONE
        client = _owned_client(version, profile, open_client, command_line_of)
        if client is None:
            return NOT_OURS
        with client:
            try:
                client.call("Browser.close")
            except (CdpError, TimeoutError, OSError):
                # The reply may never come: Chrome is closing the very
                # connection it would arrive on. Whether it went is judged
                # below, by the port and the profile lock.
                pass
        deadline = monotonic() + CLOSE_WAIT_S
        while probe() is not None or profile_busy():
            if monotonic() >= deadline:
                return WOULD_NOT_CLOSE
            sleep(0.25)
        launch()
    return RESTARTED


# -- whose Chrome is this? ---------------------------------------------------
def started_on(argv: list[str], profile_dir: Path) -> bool:
    """Whether a Chrome command line names this profile as its user data dir."""
    for arg in argv:
        if arg.lower().startswith("--user-data-dir="):
            value = arg.split("=", 1)[1].strip().strip('"')
            return _same_path(value, profile_dir)
    return False


def _same_path(a: str, b: Path) -> bool:
    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p)).rstrip("\\/")

    return bool(a) and norm(a) == norm(str(b))


def _owned_client(version, profile_dir, open_client, command_line_of) -> CdpClient | None:
    """A connection to the browser `version` describes -- only if it is the app's.

    `version` is the port's /json/version answer. The one question put before
    the proof is which process the browser is; nothing about its tabs is
    asked, and nothing is changed, until that process has been shown to run
    on the app's own profile.
    """
    if not version or not version.get("webSocketDebuggerUrl"):
        return None
    client = open_client(version["webSocketDebuggerUrl"])
    try:
        processes = client.call("SystemInfo.getProcessInfo").get("processInfo", [])
        pids = [p.get("id") for p in processes if p.get("type") == "browser"]
        argv = (command_line_of or command_line)(pids[0]) if len(pids) == 1 else None
        if argv is None or not started_on(argv, profile_dir or config.resolve_profile_dir()):
            client.close()
            return None
    except BaseException:
        client.close()
        raise
    return client


def command_line(pid: int) -> list[str] | None:
    """The command line a running process was started with, split into args.

    Read from Windows itself: NtQueryInformationProcess for the text, then
    CommandLineToArgvW to split it exactly as the process saw it. None when it
    cannot be read -- which counts as "not the app's Chrome".
    """
    if os.name != "nt":
        return None
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.LocalFree.argtypes = (ctypes.c_void_p,)
    shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))

    class UnicodeString(ctypes.Structure):
        _fields_ = [("Length", wintypes.USHORT), ("MaximumLength", wintypes.USHORT),
                    ("Buffer", ctypes.c_void_p)]

    process_query_limited_information = 0x1000
    process_command_line_information = 60

    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        size = wintypes.ULONG(0)
        ntdll.NtQueryInformationProcess(handle, process_command_line_information,
                                        None, 0, ctypes.byref(size))
        if not size.value:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        status = ntdll.NtQueryInformationProcess(handle, process_command_line_information,
                                                 buffer, size, ctypes.byref(size))
        if status != 0:
            return None
        text = UnicodeString.from_buffer(buffer)
        if not text.Buffer:
            return None
        line = ctypes.wstring_at(text.Buffer, text.Length // 2)
    finally:
        kernel32.CloseHandle(handle)

    count = ctypes.c_int(0)
    args = shell32.CommandLineToArgvW(line, ctypes.byref(count))
    if not args:
        return None
    try:
        return [args[i] for i in range(count.value)]
    finally:
        kernel32.LocalFree(args)


# -- finding the stuck ones --------------------------------------------------
def _close_silent(client: CdpClient, sleep: Callable[[float], None]) -> list[str]:
    targets = client.call("Target.getTargets").get("targetInfos", [])
    pages = [t for t in targets if t.get("type") == "page"]
    silent = _silent(client, pages)
    if not silent:
        return []
    sleep(SECOND_LOOK_S)
    silent = _silent(client, silent)
    if not silent:
        return []
    if len(silent) == len(pages):
        # Never close the last tab: Chrome quits with it. A blank one first.
        client.call("Target.createTarget", {"url": "about:blank"})
    closed = []
    for target in silent:
        try:
            client.call("Target.closeTarget", {"targetId": target["targetId"]})
        except (CdpError, TimeoutError):
            continue  # gone already -- closed by the user in the meantime
        closed.append(target.get("title") or target.get("url") or "a tab")
    return closed


def _silent(client: CdpClient, pages: list[dict]) -> list[dict]:
    """The pages that did not answer within ANSWER_S."""
    sessions: dict[str, str] = {}
    for page in pages:
        try:
            attached = client.call(
                "Target.attachToTarget", {"targetId": page["targetId"], "flatten": True}
            )
        except (CdpError, TimeoutError):
            continue  # the tab closed between the listing and now
        sessions[page["targetId"]] = attached["sessionId"]

    asked = {client.post("Runtime.getIsolateId", session=session): target
             for target, session in sessions.items()}
    answered = {asked[i] for i in client.collect(list(asked), ANSWER_S)}

    for session in sessions.values():
        try:
            client.call("Target.detachFromTarget", {"sessionId": session})
        except (CdpError, TimeoutError):
            pass
    return [p for p in pages if p["targetId"] in sessions and p["targetId"] not in answered]
