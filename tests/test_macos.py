"""The Mac pieces: macos.py, and the Mac halves of chrome, power, single and always_on.

Nothing here starts Chrome, hides a window, or changes a login item of the
machine running it. The process tests start a throwaway Python process that
only *names* a profile on its command line, the way Chrome does.
"""

from __future__ import annotations

import os
import plistlib
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fbposter import always_on, chrome, config, macos, power, session

mac_only = pytest.mark.skipif(sys.platform != "darwin", reason="reads a Mac process")


# -- reading a process's arguments ---------------------------------------------
class TestProcargs:
    @staticmethod
    def raw(program: str, *args: str) -> bytes:
        argc = (1 + len(args)).to_bytes(4, sys.byteorder)
        body = program.encode() + b"\0\0\0" + b"\0".join(
            a.encode() for a in (program, *args)) + b"\0PATH=/bin\0"
        return argc + body

    def test_the_arguments_come_back_whole(self):
        raw = self.raw("/x/Google Chrome", "--user-data-dir=/a/Application Support/P", "--y")
        assert macos.parse_procargs(raw) == [
            "/x/Google Chrome", "--user-data-dir=/a/Application Support/P", "--y",
        ]

    def test_the_environment_after_them_is_left_out(self):
        assert macos.parse_procargs(self.raw("/bin/p", "a")) == ["/bin/p", "a"]

    @pytest.mark.parametrize("raw", [b"", b"\1\0\0", b"\5\0\0\0/bin/p\0\0a\0"])
    def test_anything_short_or_broken_is_none(self, raw):
        assert macos.parse_procargs(raw) is None


@pytest.fixture
def holder(tmp_path):
    """A live process whose command line names `tmp_path / "profile"`."""
    profile = tmp_path / "profile"
    profile.mkdir()
    process = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(30)",
        f"--user-data-dir={profile}",
    ])
    time.sleep(0.3)
    try:
        yield profile, process
    finally:
        process.kill()
        process.wait()


def lock(profile: Path, pid: int, host: str | None = None) -> None:
    os.symlink(f"{host or socket.gethostname()}-{pid}", profile / macos.SINGLETON_LOCK)


@mac_only
class TestTheProcessCommandLine:
    def test_it_is_read_exactly_even_with_a_space(self, holder):
        profile, process = holder
        argv = macos.command_line(process.pid)
        assert argv is not None and argv[-1] == f"--user-data-dir={profile}"

    def test_a_process_that_has_gone_has_none(self, holder):
        _profile, process = holder
        process.kill()
        process.wait()
        assert macos.command_line(process.pid) is None


# -- the profile lock ------------------------------------------------------------
class TestTheLockLink:
    def test_no_link_no_owner(self, tmp_path):
        assert macos.lock_pid(tmp_path) is None

    def test_the_number_is_read_off_the_link(self, tmp_path):
        lock(tmp_path, 4242)
        assert macos.lock_pid(tmp_path) == 4242

    def test_a_link_from_another_computer_is_ignored(self, tmp_path):
        lock(tmp_path, 4242, host="someone-elses-mac")
        assert macos.lock_pid(tmp_path) is None

    def test_a_link_without_a_number_is_ignored(self, tmp_path):
        os.symlink("nonsense", tmp_path / macos.SINGLETON_LOCK)
        assert macos.lock_pid(tmp_path) is None


@mac_only
class TestTheProfileInUseOnAMac:
    def test_a_live_chrome_on_this_profile_holds_it(self, holder):
        profile, process = holder
        lock(profile, process.pid)
        assert macos.lock_owner(profile) == process.pid
        assert chrome.profile_in_use(profile) is True

    def test_a_link_left_by_a_crash_does_not(self, holder):
        profile, process = holder
        lock(profile, process.pid)
        process.kill()
        process.wait()
        assert chrome.profile_in_use(profile) is False

    def test_a_reused_number_on_another_profile_does_not(self, holder, tmp_path):
        _profile, process = holder
        other = tmp_path / "other"
        other.mkdir()
        lock(other, process.pid)  # alive, but started on a different profile
        assert macos.lock_owner(other) is None
        assert chrome.profile_in_use(other) is False

    def test_hiding_needs_a_proven_owner(self, tmp_path):
        assert macos.hide_app_chrome(tmp_path) is False
        assert macos.show_app_chrome(tmp_path) is False


# -- starting Chrome -------------------------------------------------------------
class TestStartingChromeOnAMac:
    PROGRAM = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")

    def args(self, profile: Path) -> list[str]:
        return chrome.build_args(self.PROGRAM, profile, visible=False)

    def test_open_starts_a_new_one_in_the_background(self, tmp_path):
        command = chrome.mac_launch_command(self.args(tmp_path), visible=False)
        assert command[:3] == ["open", "-n", "-g"]
        assert command[command.index("-a") + 1] == "/Applications/Google Chrome.app"
        flags = command[command.index("--args") + 1:]
        assert f"--user-data-dir={tmp_path}" in flags
        assert "--remote-debugging-port=9222" in flags
        assert str(self.PROGRAM) not in flags

    def test_the_login_window_may_come_to_the_front(self, tmp_path):
        command = chrome.mac_launch_command(self.args(tmp_path), visible=True)
        assert "-g" not in command and command[:2] == ["open", "-n"]

    def test_chrome_is_looked_for_in_both_applications_folders(self, monkeypatch):
        monkeypatch.setattr(config, "IS_MAC", True)
        found = config.chrome_candidates()
        assert found[0] == Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        assert found[1] == Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"

    @mac_only
    def test_the_profile_lives_in_application_support(self):
        assert config.PREFERRED_PROFILE_DIR == (
            Path.home() / "Library" / "Application Support" / "FBAutomation" / "ChromeProfile"
        )


# -- new tabs keep Chrome hidden ---------------------------------------------------
class FakeContext:
    def __init__(self):
        self.opened = 0

    def new_page(self):
        self.opened += 1
        return f"page {self.opened}"


class TestNewTabsHideChromeAgain:
    def test_inert_until_the_app_wires_it(self):
        assert session.after_new_page is None
        assert session.new_page(FakeContext()) == "page 1"

    def test_each_tab_is_followed_by_a_hide(self, monkeypatch):
        hidden = []
        monkeypatch.setattr(session, "after_new_page", lambda: hidden.append(1))
        context = FakeContext()
        session.new_page(context)
        session.new_page(context)
        assert hidden == [1, 1]

    def test_a_hide_that_fails_never_stops_the_tab(self, monkeypatch):
        def broken():
            raise RuntimeError("no AppKit")

        monkeypatch.setattr(session, "after_new_page", broken)
        assert session.new_page(FakeContext()) == "page 1"

    def test_the_login_window_is_left_showing(self):
        from fbposter import login

        assert login.show_window is None  # inert until run() wires it


# -- power -------------------------------------------------------------------------
class TestPowerOnAMac:
    @pytest.mark.parametrize("text, source", [
        ("Now drawing from 'AC Power'\n -InternalBattery-0 100%", "AC Power"),
        ("Now drawing from 'Battery Power'\n -InternalBattery-0 80%", "Battery Power"),
        ("", None),
        ("something else", None),
    ])
    def test_the_source_is_read_off_pmset(self, text, source):
        assert macos.parse_power_source(text) == source

    @mac_only
    @pytest.mark.parametrize("source, battery", [
        ("AC Power", False), ("Battery Power", True), (None, None), ("UPS Power", None),
    ])
    def test_it_answers_in_windows_terms(self, monkeypatch, source, battery):
        monkeypatch.setattr(macos, "power_source", lambda: source)
        assert power.on_battery() is battery

    @mac_only
    def test_caffeinate_holds_off_idle_sleep_only_and_ends_with_the_app(self):
        started = []
        macos.keep_awake_while_running(1234, popen=lambda cmd, **k: started.append(cmd))
        assert started == [["caffeinate", "-i", "-w", "1234"]]

    @mac_only
    def test_the_per_batch_blocker_holds_nothing_on_a_mac(self):
        assert power.SleepBlocker().available is False


# -- start at login ------------------------------------------------------------------
class TestStartAtLoginOnAMac:
    def test_the_login_item_starts_the_app_once(self):
        data = plistlib.loads(always_on.launch_agent_plist(["/x/app", "start"]))
        assert data["Label"] == always_on.LAUNCH_AGENT_LABEL
        assert data["ProgramArguments"] == ["/x/app", "start"]
        assert data["RunAtLoad"] is True and data["KeepAlive"] is False

    def test_on_and_off(self, tmp_path):
        agent = tmp_path / "LaunchAgents" / "x.plist"
        mac = always_on.MacAlwaysOn(agent_file=lambda: agent)
        assert mac.status().autostart is False
        mac.set_autostart(True)
        assert agent.exists() and mac.status().autostart is True
        mac.set_autostart(False)
        assert not agent.exists() and mac.status().autostart is False

    def test_keep_awake_is_always_on_and_has_no_switch(self, tmp_path):
        mac = always_on.MacAlwaysOn(agent_file=lambda: tmp_path / "x.plist")
        mac.set_keep_awake(False)
        assert mac.status().keep_awake is True

    def test_the_right_one_is_picked_for_this_computer(self):
        expected = always_on.MacAlwaysOn if sys.platform == "darwin" else always_on.AlwaysOn
        assert isinstance(always_on.for_this_computer(), expected)
