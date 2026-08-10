"""Keeping the machine awake while a batch is in flight.

A batch legitimately takes one to three hours, almost all of it spent waiting
between groups. If Windows suspends halfway through, the remaining groups do
not go out and the schedule is silently wrong.

Only *system* sleep is held off, never the display: the user should still get
their screen turning off, and a lit monitor for three hours would be its own
kind of interference.
"""

from __future__ import annotations

import ctypes
import os

# https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

KEEP_AWAKE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
RELEASE = ES_CONTINUOUS


def _default_setter():
    """The real Windows call, or None where it does not exist."""
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.kernel32.SetThreadExecutionState
    except Exception:
        return None


class SleepBlocker:
    """Holds off system sleep for as long as it is held.

    The execution state is per-thread, so this must be used on the same thread
    that is doing the work -- which is the posting worker, not the UI.
    """

    def __init__(self, setter=None) -> None:
        self._setter = setter if setter is not None else _default_setter()
        self._held = False

    @property
    def held(self) -> bool:
        return self._held

    @property
    def available(self) -> bool:
        return self._setter is not None

    def acquire(self) -> bool:
        """Ask Windows not to sleep. Idempotent."""
        if self._held or self._setter is None:
            return self._held
        self._setter(KEEP_AWAKE)
        self._held = True
        return True

    def release(self) -> None:
        """Let the machine sleep normally again. Idempotent."""
        if not self._held or self._setter is None:
            self._held = False
            return
        self._setter(RELEASE)
        self._held = False

    def __enter__(self) -> "SleepBlocker":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> None:
        self.release()
