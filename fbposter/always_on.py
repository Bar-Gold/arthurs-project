"""Starting with Windows, and keeping the PC awake.

The Settings side of `scripts/setup_always_on.ps1`, which the installer runs
once and which used to be the only way to change either. The script stays the
one implementation: it records every power value before changing it, verifies
what it set, and is what the uninstaller calls to put it all back. This module
only asks it to do one half or the other, and reads back where things stand.

What it reads, and why those are the right things to read:

* **Start with Windows** is the scheduled task the script registers. Asked of
  `schtasks`, which answers in tens of milliseconds and never raises a window.
* **Keep awake** is the script's own backup file. It is written before the
  first power setting is touched and deleted only by a clean revert, so it is
  present exactly when the script's power settings are in force.

Nothing here touches Facebook or Chrome. It does change the machine, which is
why `App` holds an inert `Inert` until `qtui.app.run()` swaps in the real one:
a test suite that toggled this would change the power plan of whoever ran it.

**Administrator rights are asked for only when Windows refuses without them.**
The installer runs elevated, and the logon task it registers can then be read
by the user but not deleted -- so unticking "Start with Windows" failed, and
the box ticked itself straight back. The script exits `NEEDS_ADMIN` for exactly
that refusal, and the change is run once more with administrator rights, which
Windows asks the user for. They just clicked the box, so the prompt answers
their own action.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

TASK_NAME = "FacebookLocalAutoPoster"
SCRIPT_NAME = "setup_always_on.ps1"
# Long enough for powercfg on a slow laptop; the script normally takes 2-4s.
SCRIPT_TIMEOUT_S = 120
QUERY_TIMEOUT_S = 15
# The script's exit code when the only thing refused was for want of
# administrator rights. 1 is every other failure.
NEEDS_ADMIN = 2
# Longer than SCRIPT_TIMEOUT_S: the user has to read and answer Windows' prompt.
ELEVATED_TIMEOUT_S = 300

# No console window flashing up behind the app. Windows only, and 0 elsewhere
# so importing this on another platform does not fail.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class AlwaysOnError(Exception):
    """The script ran and something it was asked to do did not happen."""


@dataclass(frozen=True)
class Status:
    # None when it could not be established -- schtasks missing or refusing.
    # Reported as "unknown" rather than guessed, like power.on_battery().
    autostart: bool | None
    keep_awake: bool


def backup_file() -> Path:
    """Where the script records the power values it replaced."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "FBAutomation" / "power-backup.json"


def script_path() -> Path | None:
    """The script, wherever this copy of the app keeps it.

    Packaged, the installer puts it in `scripts\\` beside the .exe. From a
    source checkout it is in the repository's own `scripts\\`.
    """
    if getattr(sys, "frozen", False):
        path = Path(sys.executable).parent / "scripts" / SCRIPT_NAME
    else:
        path = Path(__file__).resolve().parents[1] / "scripts" / SCRIPT_NAME
    return path if path.exists() else None


def app_path() -> str | None:
    """The .exe the logon task should start, or None for a source checkout.

    Without it the script builds the task around the checkout's pythonw.exe,
    which is right for a developer and wrong for a client.
    """
    return sys.executable if getattr(sys, "frozen", False) else None


def run_elevated(command: list[str], timeout: float) -> int:
    """Run `command` with administrator rights and return its exit code.

    Windows asks the user first. PermissionError if they said no, TimeoutError
    if the command has not finished within `timeout` seconds.

    ShellExecuteEx with "runas" rather than subprocess, because subprocess
    cannot elevate. The price is that the elevated process's output cannot be
    read, so only its exit code comes back.
    """
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_nocloseprocess = 0x00000040
    see_mask_noasync = 0x00000100
    see_mask_flag_no_ui = 0x00000400
    sw_hide = 0
    error_cancelled = 1223
    wait_timeout = 0x00000102

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess | see_mask_noasync | see_mask_flag_no_ui
    info.lpVerb = "runas"
    info.lpFile = command[0]
    info.lpParameters = subprocess.list2cmdline(command[1:])
    info.nShow = sw_hide
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error == error_cancelled:
            raise PermissionError("Administrator permission was not given.")
        raise ctypes.WinError(error)
    try:
        if kernel32.WaitForSingleObject(info.hProcess, int(timeout * 1000)) == wait_timeout:
            raise TimeoutError
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


class AlwaysOn:
    """The real thing. `run` and `elevate` are the seams, for the tests of
    this module."""

    def __init__(
        self,
        run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        elevate: Callable[[list[str], float], int] = run_elevated,
    ) -> None:
        self._run = run
        self._elevate = elevate

    def status(self) -> Status:
        return Status(self._task_exists(), backup_file().exists())

    def set_autostart(self, on: bool) -> None:
        if on:
            args = ["-SkipPower"]
            app = app_path()
            if app is not None:
                args += ["-AppPath", app]
        else:
            args = ["-Revert", "-SkipPower"]
        self._script(args)

    def set_keep_awake(self, on: bool) -> None:
        self._script(["-SkipTask"] if on else ["-Revert", "-SkipTask"])

    # -- helpers -----------------------------------------------------------
    def _task_exists(self) -> bool | None:
        try:
            result = self._run(
                ["schtasks", "/query", "/tn", TASK_NAME],
                capture_output=True, text=True, timeout=QUERY_TIMEOUT_S,
                creationflags=_NO_WINDOW,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return result.returncode == 0

    def _script(self, args: list[str]) -> None:
        script = script_path()
        if script is None:
            raise AlwaysOnError(
                "The setup script is missing from this installation. "
                "Reinstalling the app puts it back."
            )
        command = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-NoProfile",
                   "-NonInteractive", "-File", str(script), *args]
        try:
            result = self._run(
                command,
                capture_output=True, text=True, timeout=SCRIPT_TIMEOUT_S,
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise AlwaysOnError("Windows took too long to answer. Try again.") from exc
        except OSError as exc:
            raise AlwaysOnError(f"Windows would not run the setup: {exc}") from exc
        if result.returncode == NEEDS_ADMIN:
            self._script_elevated(command)
            return
        if result.returncode != 0:
            raise AlwaysOnError(_first_failure(result.stdout, result.stderr))

    def _script_elevated(self, command: list[str]) -> None:
        """The same run again, with administrator rights, which Windows asks for."""
        try:
            code = self._elevate(command, ELEVATED_TIMEOUT_S)
        except PermissionError as exc:
            raise AlwaysOnError(
                "This needs administrator permission, and Windows was not given it."
            ) from exc
        except TimeoutError as exc:
            raise AlwaysOnError("Windows took too long to answer. Try again.") from exc
        except OSError as exc:
            raise AlwaysOnError(f"Windows would not run the setup: {exc}") from exc
        if code != 0:
            raise AlwaysOnError(
                "Windows did not accept the change, even with administrator permission."
            )


def _first_failure(stdout: str | None, stderr: str | None) -> str:
    """The script's own words for what went wrong, or a plain fallback."""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith("[fail]"):
            return line[len("[fail]"):].strip()
    return "Windows did not accept the change."


class Inert:
    """What every window gets until `run()` wires up the real one.

    Reads as "not set up" and changes nothing, so no test can alter the power
    plan or the scheduled tasks of the machine it runs on.
    """

    def status(self) -> Status:
        return Status(autostart=False, keep_awake=False)

    def set_autostart(self, on: bool) -> None:
        pass

    def set_keep_awake(self, on: bool) -> None:
        pass
