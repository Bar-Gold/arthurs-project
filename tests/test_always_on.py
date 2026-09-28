"""Starting with Windows and keeping the PC awake, from the Settings screen.

Nothing here runs PowerShell or schtasks: `AlwaysOn(run=)` is handed a fake,
and the backup file is pointed at a temporary directory. A suite that really
toggled these would change the power plan of whoever ran it.
"""

from __future__ import annotations

import subprocess

import pytest

from fbposter import always_on
from fbposter.always_on import AlwaysOn, AlwaysOnError, Inert


class FakeRun:
    def __init__(self, returncode=0, stdout="", raises=None) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.raises = raises
        self.calls: list[list[str]] = []

    def __call__(self, args, **kwargs):
        self.calls.append(list(args))
        # Never a console window flashing up behind the app.
        assert kwargs.get("creationflags") == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(args, self.returncode, self.stdout, "")

    def script_args(self) -> list[str]:
        call = self.calls[-1]
        return call[call.index("-File") + 2:]


@pytest.fixture(autouse=True)
def private_appdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))


class TestReadingWhereThingsStand:
    def test_start_with_windows_is_the_scheduled_task(self):
        run = FakeRun(returncode=0)
        assert AlwaysOn(run).status().autostart is True
        assert run.calls[0][:4] == ["schtasks", "/query", "/tn", always_on.TASK_NAME]

    def test_no_task_is_off(self):
        assert AlwaysOn(FakeRun(returncode=1)).status().autostart is False

    def test_a_windows_that_will_not_say_is_unknown_not_off(self):
        """Like power.on_battery(): an answer we do not have is not a no."""
        status = AlwaysOn(FakeRun(raises=OSError("no schtasks"))).status()
        assert status.autostart is None

    def test_keep_awake_is_the_scripts_own_backup_file(self):
        """Written before the first power setting is touched, deleted only by
        a clean revert: present exactly while the settings are in force."""
        run = FakeRun()
        assert AlwaysOn(run).status().keep_awake is False
        always_on.backup_file().parent.mkdir(parents=True)
        always_on.backup_file().write_text("{}", encoding="utf-8")
        assert AlwaysOn(run).status().keep_awake is True


class TestEachHalfOnItsOwn:
    """The installer runs both halves at once. Settings turns each on and
    off separately, so each call must leave the other half alone."""

    def test_start_with_windows_leaves_the_power_plan_alone(self):
        run = FakeRun()
        AlwaysOn(run).set_autostart(True)
        assert "-SkipPower" in run.script_args()
        assert "-Revert" not in run.script_args()

    def test_turning_it_off_removes_only_the_task(self):
        run = FakeRun()
        AlwaysOn(run).set_autostart(False)
        assert run.script_args() == ["-Revert", "-SkipPower"]

    def test_keep_awake_leaves_the_task_alone(self):
        run = FakeRun()
        AlwaysOn(run).set_keep_awake(True)
        assert run.script_args() == ["-SkipTask"]

    def test_turning_it_off_restores_only_the_power_plan(self):
        run = FakeRun()
        AlwaysOn(run).set_keep_awake(False)
        assert run.script_args() == ["-Revert", "-SkipTask"]

    def test_a_packaged_app_names_its_own_exe(self, monkeypatch):
        """Without -AppPath the script builds the task around a source
        checkout's Python, which a client does not have."""
        monkeypatch.setattr(always_on, "app_path", lambda: r"C:\Program Files\X\X.exe")
        run = FakeRun()
        AlwaysOn(run).set_autostart(True)
        args = run.script_args()
        assert args[args.index("-AppPath") + 1] == r"C:\Program Files\X\X.exe"

    def test_a_source_checkout_does_not(self, monkeypatch):
        monkeypatch.setattr(always_on, "app_path", lambda: None)
        run = FakeRun()
        AlwaysOn(run).set_autostart(True)
        assert "-AppPath" not in run.script_args()

    def test_it_runs_the_script_this_copy_of_the_app_ships(self):
        run = FakeRun()
        AlwaysOn(run).set_keep_awake(True)
        call = run.calls[-1]
        assert call[0] == "powershell.exe"
        assert call[call.index("-File") + 1].endswith(always_on.SCRIPT_NAME)


class TestFailuresAreSaid:
    def test_the_scripts_own_words_are_used(self):
        run = FakeRun(returncode=1, stdout="  [ok]   fine\n  [fail] Lid close action could not be set\n")
        with pytest.raises(AlwaysOnError, match="Lid close action could not be set"):
            AlwaysOn(run).set_keep_awake(True)

    def test_a_failure_with_no_words_still_says_something(self):
        with pytest.raises(AlwaysOnError, match="did not accept"):
            AlwaysOn(FakeRun(returncode=1)).set_autostart(True)

    def test_a_hung_windows_is_a_failure_not_a_hang(self):
        run = FakeRun(raises=subprocess.TimeoutExpired("powershell.exe", 1))
        with pytest.raises(AlwaysOnError, match="too long"):
            AlwaysOn(run).set_autostart(True)

    def test_a_missing_script_is_said_before_anything_runs(self, monkeypatch):
        monkeypatch.setattr(always_on, "script_path", lambda: None)
        run = FakeRun()
        with pytest.raises(AlwaysOnError, match="missing"):
            AlwaysOn(run).set_autostart(True)
        assert run.calls == []


class TestTheScript:
    def test_it_ships_where_this_checkout_looks(self):
        assert always_on.script_path() is not None

    def test_each_half_can_be_reverted_alone(self):
        """The uninstaller passes -Revert alone and must still get both."""
        text = always_on.script_path().read_text(encoding="utf-8")
        revert = text[text.index("if ($Revert) {"):text.index("# --- apply")]
        assert "if ($SkipPower)" in revert
        assert "if ($SkipTask)" in revert

    def test_it_says_when_it_failed(self):
        """The app reads the exit code; the installer ignores it."""
        text = always_on.script_path().read_text(encoding="utf-8")
        assert "exit 1" in text


def test_the_inert_one_changes_nothing_and_reads_as_off():
    inert = Inert()
    inert.set_autostart(True)
    inert.set_keep_awake(True)
    assert inert.status() == always_on.Status(autostart=False, keep_awake=False)
