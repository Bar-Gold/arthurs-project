"""Keeping the machine awake while a batch is in flight -- and knowing when we cannot.

A batch legitimately takes one to three hours, almost all of it spent waiting
between groups. If Windows suspends halfway through, the remaining groups do
not go out and the schedule is silently wrong.

Only *system* sleep is held off, never the display: the user should still get
their screen turning off, and a lit monitor for three hours would be its own
kind of interference.

**What `SetThreadExecutionState` cannot do is the more important half.** It
suppresses the *idle timer* and nothing else. Closing a laptop lid, picking
Sleep from the Start menu, and shutting down all suspend the machine straight
through it, mid-batch and all. So on a laptop this API is not the answer --
the power plan is, and `scripts/setup_always_on.ps1` is what sets it. The job
left here is to stop an idle machine dozing off mid-batch, and to notice when
that plan does not apply because the thing is running on battery.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

# https://learn.microsoft.com/windows/win32/api/winbase/nf-winbase-setthreadexecutionstate
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

KEEP_AWAKE = ES_CONTINUOUS | ES_SYSTEM_REQUIRED
RELEASE = ES_CONTINUOUS

# GetSystemPowerStatus.ACLineStatus. 255 is "unknown" and is a real answer:
# desktops, virtual machines and drivers that decline to say all return it.
AC_OFFLINE = 0
AC_ONLINE = 1


def _default_setter():
    """The real Windows call, or None where it does not exist."""
    if os.name != "nt":
        return None
    try:
        return ctypes.windll.kernel32.SetThreadExecutionState
    except Exception:
        return None


class SYSTEM_POWER_STATUS(ctypes.Structure):
    """https://learn.microsoft.com/windows/win32/api/winbase/ns-winbase-system_power_status"""

    _fields_ = [
        ("ACLineStatus", ctypes.c_ubyte),
        ("BatteryFlag", ctypes.c_ubyte),
        ("BatteryLifePercent", ctypes.c_ubyte),
        ("SystemStatusFlag", ctypes.c_ubyte),
        ("BatteryLifeTime", wintypes.DWORD),
        ("BatteryFullLifeTime", wintypes.DWORD),
    ]


def read_ac_line_status() -> int | None:
    """Windows' own answer, or None where the question cannot be asked."""
    if os.name != "nt":
        return None
    try:
        status = SYSTEM_POWER_STATUS()
        if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(status)):
            return None
        return int(status.ACLineStatus)
    except Exception:
        return None


def on_battery(reader=read_ac_line_status) -> bool | None:
    """True on battery, False on mains, None when it cannot be established.

    Three states rather than two, and the third one is why this is not a plain
    bool. Warning someone about a battery they do not have would be worse than
    saying nothing, so an unknown answer stays unknown and nobody is told.
    """
    status = reader()
    if status == AC_OFFLINE:
        return True
    if status == AC_ONLINE:
        return False
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
