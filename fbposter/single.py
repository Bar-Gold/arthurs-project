"""Making sure only one copy of the app is running.

The architecture assumes exactly one posting worker, globally. A second copy of
the app is a second worker on the same database, and racing two of them
produced a duplicate post in 7 runs out of 40 -- the worst outcome this project
has.

`TaskRepo.claim_target` is the real defence and makes a duplicate impossible
even if two do run. This is the second layer, and it earns its place for the
things a database claim cannot help with: two workers both driving the same
Chrome session, both holding the machine awake, and two windows disagreeing
about what is queued.

A Windows named mutex rather than a lock file, because the kernel drops it when
the process ends however it ends. A lock file left behind by a crash would tell
the user their app is already running when it is not.

On a Mac the same promise comes from `flock` on a file in the app's data folder
(`~/Library/Application Support/FBAutomation/`): it is the lock, not the file,
that says "running", and macOS drops the lock when the process ends however it
ends. The file left behind holds nothing. It used to be in /tmp, which macOS
empties of files nobody has touched for three days (`periodic`), so a lock held
for longer could be deleted and a second copy start. One per account, as the
data folder is.
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from pathlib import Path

# One name per machine. Not per user: two accounts sharing C:\FBAutomation
# would share the database too.
MUTEX_NAME = "Global\\FacebookLocalAutoPoster.SingleInstance"

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    """Held for the life of the process; released by the OS when it exits.

    Falls open on anything unexpected. Refusing to start because a lock could
    not be taken would be a worse failure than the one being prevented, and the
    database claim still holds the line.
    """

    def __init__(self, name: str = MUTEX_NAME, directory: Path | None = None) -> None:
        self.name = name
        # Where a Mac keeps the lock file; the data folder unless a test says.
        self.directory = directory
        self._handle = None
        self.acquired = self._acquire()

    def _acquire(self) -> bool:
        if sys.platform == "darwin":
            return self._acquire_mac()
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateMutexW.argtypes = (
                wintypes.LPCVOID,
                wintypes.BOOL,
                wintypes.LPCWSTR,
            )
            kernel32.CreateMutexW.restype = wintypes.HANDLE

            handle = kernel32.CreateMutexW(None, True, self.name)
            last_error = ctypes.get_last_error()
            if not handle:
                return True  # could not lock at all; do not block the user
            self._handle = handle
            return last_error != ERROR_ALREADY_EXISTS
        except Exception:
            # Not Windows, or ctypes unavailable. The database claim still
            # prevents the duplicate that actually matters.
            return True

    def _acquire_mac(self) -> bool:
        import fcntl
        import os

        from . import config

        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", self.name)
        folder = self.directory or config.MAC_DATA_DIR
        try:
            folder.mkdir(parents=True, exist_ok=True)
            handle = os.open(folder / f"{safe}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return True  # could not lock at all; do not block the user
        try:
            # Its own open file, so a second holder in this same process is
            # refused too (flock is per open file, unlike fcntl locks).
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(handle)
            return False
        except OSError:
            os.close(handle)
            return True
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        if sys.platform == "darwin":
            import os

            try:
                os.close(self._handle)  # closing the file drops the lock
            except OSError:
                pass
            self._handle = None
            return
        try:
            ctypes.WinDLL("kernel32").CloseHandle(self._handle)
        except Exception:
            pass
        self._handle = None
