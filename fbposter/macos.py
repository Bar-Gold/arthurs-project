"""What the app needs from macOS itself, kept in one place.

Windows has its own answers spread through `single.py`, `tabs.py` and
`power.py`. On a Mac those modules ask here instead. Everything is read through
the standard library (`ctypes`, `subprocess`) so nothing new has to be
installed, and everything answers None or False rather than raising on another
system, so importing it on Windows costs nothing.

**Hiding Chrome instead of parking it off-screen.** On Windows the app's Chrome
lives at -32000,-32000. macOS does not allow that: verified live on 2026-11-12
against Chrome on macOS 13, a window asked for -32000,-32000 opens at 0,25, and
one moved there over CDP stops at -1160, its last 40 pixels still on screen.
So on a Mac the window is parked as far left as macOS lets it go and the app
is hidden, the way Cmd+H hides it. Verified the same day:

* A hidden Chrome keeps running its pages normally (`document.visibilityState`
  "visible", animation frames running), with the three no-throttling flags.
* Chrome un-hides itself when a tab is opened, without taking focus. So the
  app hides it again right after each tab it opens (`session.new_page`).
* A tab opened "in the background" (`Target.createTarget` with `background`)
  is worse, not better: Chrome still un-hides, and the page is "hidden" with
  its animation frames stopped.
* `open -j` (launch hidden) does not keep Chrome hidden. `open -g` does keep
  it from taking focus, which is what `chrome.launch` uses.

Hiding is not `bring_to_front()`'s opposite twin: it never raises anything
above what the user is doing. Showing does, and runs only from the login
window, because the user just pressed a button that says Chrome will open.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import socket
import subprocess
import sys
from pathlib import Path

IS_MAC = sys.platform == "darwin"

# Chrome keeps a symlink in the profile while it runs, pointing at
# "<hostname>-<pid>". Its Windows counterpart is the `lockfile` it holds open.
SINGLETON_LOCK = "SingletonLock"


# -- the profile lock -------------------------------------------------------
def lock_pid(profile_dir: Path) -> int | None:
    """The process id Chrome's profile lock names, alive or not.

    None when there is no lock, or when it was written on another computer (a
    profile copied across). Chrome leaves the link behind when it crashes, so
    the process it names may be gone, or be another program by now.
    """
    try:
        target = os.readlink(Path(profile_dir) / SINGLETON_LOCK)
    except OSError:
        return None
    host, _, pid_text = target.rpartition("-")
    if not pid_text.isdigit():
        return None
    if host and host != socket.gethostname():
        return None
    return int(pid_text)


def lock_owner(profile_dir: Path) -> int | None:
    """The live Chrome holding this profile: proven, not just named by the lock.

    Its own command line has to name the profile, as `tabs.started_on` judges
    it, so a number left behind by a crash and reused by another program is
    never taken for it. A process that has gone has no command line to read.
    """
    from . import tabs  # tabs imports chrome, which imports this lazily

    pid = lock_pid(profile_dir)
    if pid is None:
        return None
    argv = command_line(pid)
    if argv is None or not tabs.started_on(argv, Path(profile_dir)):
        return None
    return pid


# -- a process's command line -------------------------------------------------
CTL_KERN = 1
KERN_ARGMAX = 8
KERN_PROCARGS2 = 49


def _libc():
    return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def command_line(pid: int) -> list[str] | None:
    """The arguments a running process was started with, exactly as it got them.

    Read with sysctl(KERN_PROCARGS2), the call `ps` uses, which returns the
    count of arguments, the program's path, then each argument ending in a
    zero byte. Splitting `ps` output on spaces would not do: the app's profile
    is in "Application Support", which has a space in it. None when it cannot
    be read, which counts as "not the app's Chrome".
    """
    if not IS_MAC:
        return None
    try:
        libc = _libc()
        size = ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        argmax = ctypes.c_int(0)
        mib = (ctypes.c_int * 2)(CTL_KERN, KERN_ARGMAX)
        if libc.sysctl(mib, 2, ctypes.byref(argmax), ctypes.byref(size), None, 0) != 0:
            return None
        buffer = ctypes.create_string_buffer(argmax.value)
        size = ctypes.c_size_t(argmax.value)
        mib = (ctypes.c_int * 3)(CTL_KERN, KERN_PROCARGS2, pid)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
            return None
        return parse_procargs(buffer.raw[: size.value])
    except Exception:
        return None


def parse_procargs(raw: bytes) -> list[str] | None:
    """Split what KERN_PROCARGS2 returns into the argument list. Pure."""
    if len(raw) < 4:
        return None
    argc = int.from_bytes(raw[:4], sys.byteorder)
    rest = raw[4:]
    # The program's path, then zero bytes padding it out, then the arguments.
    end = rest.find(b"\0")
    if end < 0:
        return None
    rest = rest[end:].lstrip(b"\0")
    parts = rest.split(b"\0")
    if argc <= 0 or len(parts) < argc:
        return None
    return [part.decode("utf-8", "replace") for part in parts[:argc]]


# -- hiding and showing an app --------------------------------------------------
def _objc():
    """The Objective-C runtime with AppKit loaded, or None."""
    if not IS_MAC:
        return None
    try:
        objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
        ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    except OSError:
        return None
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = (ctypes.c_char_p,)
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = (ctypes.c_char_p,)
    return objc


def _send(objc, restype, receiver, selector: bytes, *args, argtypes=()):
    function = ctypes.CFUNCTYPE(restype, ctypes.c_void_p, ctypes.c_void_p, *argtypes)(
        ("objc_msgSend", objc)
    )
    return function(receiver, objc.sel_registerName(selector), *args)


def _with_app(pid: int, action) -> bool:
    """Run `action(objc, app)` on NSRunningApplication for `pid`. False on failure.

    Wrapped in an autorelease pool: this runs on the worker's thread, which has
    no pool of its own, and without one every call would leak a little.
    """
    objc = _objc()
    if objc is None:
        return False
    pool = None
    try:
        pool_class = objc.objc_getClass(b"NSAutoreleasePool")
        pool = _send(objc, ctypes.c_void_p, _send(objc, ctypes.c_void_p, pool_class, b"alloc"), b"init")
        app = _send(
            objc, ctypes.c_void_p, objc.objc_getClass(b"NSRunningApplication"),
            b"runningApplicationWithProcessIdentifier:", pid, argtypes=(ctypes.c_int,),
        )
        if not app:
            return False
        return bool(action(objc, app))
    except Exception:
        return False
    finally:
        if pool:
            try:
                _send(objc, None, pool, b"drain")
            except Exception:
                pass


def hide(pid: int) -> bool:
    """Hide every window of the app running as `pid`, as Cmd+H does."""
    return _with_app(pid, lambda objc, app: _send(objc, ctypes.c_bool, app, b"hide"))


def show(pid: int) -> bool:
    """Un-hide the app running as `pid` and put it in front.

    Only for when the user has just asked for that window (the login).
    """
    activate_all_windows = 1  # NSApplicationActivateAllWindows

    def action(objc, app):
        _send(objc, ctypes.c_bool, app, b"unhide")
        return _send(objc, ctypes.c_bool, app, b"activateWithOptions:",
                     activate_all_windows, argtypes=(ctypes.c_ulong,))

    return _with_app(pid, action)


def is_hidden(pid: int) -> bool | None:
    """Whether the app running as `pid` is hidden. None if it cannot be asked."""
    answer: list[bool] = []

    def action(objc, app):
        answer.append(bool(_send(objc, ctypes.c_bool, app, b"isHidden")))
        return True

    return answer[0] if _with_app(pid, action) and answer else None


# -- the app's own Chrome -----------------------------------------------------
def _app_chrome_pid(profile_dir: Path | None = None) -> int | None:
    from . import config

    return lock_owner(profile_dir or config.resolve_profile_dir())


def hide_app_chrome(profile_dir: Path | None = None) -> bool:
    """Hide the app's own Chrome, found by its profile lock. Never another one."""
    pid = _app_chrome_pid(profile_dir)
    return hide(pid) if pid is not None else False


def show_app_chrome(profile_dir: Path | None = None) -> bool:
    """Un-hide the app's own Chrome and put it in front, for the login."""
    pid = _app_chrome_pid(profile_dir)
    return show(pid) if pid is not None else False


# -- power ----------------------------------------------------------------------
def power_source(run=subprocess.run) -> str | None:
    """'AC Power', 'Battery Power', or None when macOS does not say.

    Read from `pmset -g batt`, whose first line is
    "Now drawing from 'AC Power'" (or 'Battery Power').
    """
    if not IS_MAC:
        return None
    try:
        result = run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_power_source(result.stdout or "")


def parse_power_source(text: str) -> str | None:
    """The source named on pmset's first line. Pure."""
    first = text.splitlines()[0] if text else ""
    if "'" not in first:
        return None
    parts = first.split("'")
    return parts[1] if len(parts) >= 3 else None


def keep_awake_while_running(pid: int | None = None, popen=subprocess.Popen):
    """Stop the Mac idling to sleep for as long as process `pid` runs.

    `caffeinate -i -w <pid>` is built into macOS and needs no password. `-i`
    holds off idle sleep only: the screen still turns off, and closing the lid
    still sleeps the Mac (the user's choice, 2026-11-12). `-w` makes it end by
    itself when the app ends, however the app ends, so nothing can be left
    holding the Mac awake. Returns the process, or None if it could not start.
    """
    if not IS_MAC:
        return None
    try:
        return popen(
            ["caffeinate", "-i", "-w", str(pid or os.getpid())],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        return None


# Everything NSActivityUserInitiated holds except idle system sleep, which is
# caffeinate's job above (so the lid and the user's own choice still decide).
NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SYSTEM_SLEEP = 0x00EFFFFF
KEEP_RUNNING_REASON = b"Posting to Facebook groups on a schedule"

# The activity macOS handed back, held for the life of the app: letting it go
# ends it, and macOS may then nap the app again.
_activity = None


def keep_running(objc_loader=None, send=None):
    """Stop macOS putting the app to sleep (App Nap) while its window is hidden.

    App Nap slows the timers and threads of an app nobody is looking at, which
    is this app's normal state on a Mac: the window closed to the Dock, posting
    going on. `caffeinate` does not prevent it; it only keeps the Mac awake.
    `[NSProcessInfo beginActivityWithOptions:reason:]` is Apple's way, called
    through the Objective-C runtime like `hide`. The bundle also says so in its
    Info.plist (`LSAppNapIsDisabled`); this covers running from a checkout too.
    Returns the activity, or None when it could not be had. Never raises.
    """
    global _activity
    if _activity is not None:
        return _activity
    objc = (objc_loader or _objc)()
    if objc is None:
        return None
    send = send or _send
    pool = None
    try:
        pool_class = objc.objc_getClass(b"NSAutoreleasePool")
        pool = send(objc, ctypes.c_void_p, send(objc, ctypes.c_void_p, pool_class, b"alloc"), b"init")
        info = send(objc, ctypes.c_void_p, objc.objc_getClass(b"NSProcessInfo"), b"processInfo")
        reason = send(
            objc, ctypes.c_void_p, objc.objc_getClass(b"NSString"),
            b"stringWithUTF8String:", KEEP_RUNNING_REASON, argtypes=(ctypes.c_char_p,),
        )
        activity = send(
            objc, ctypes.c_void_p, info, b"beginActivityWithOptions:reason:",
            NS_ACTIVITY_USER_INITIATED_ALLOWING_IDLE_SYSTEM_SLEEP, reason,
            argtypes=(ctypes.c_uint64, ctypes.c_void_p),
        )
        if not activity:
            return None
        # Kept past the pool below, which would otherwise free it.
        _activity = send(objc, ctypes.c_void_p, activity, b"retain")
        return _activity
    except Exception:
        return None
    finally:
        if pool:
            try:
                send(objc, None, pool, b"drain")
            except Exception:
                pass
