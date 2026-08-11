"""Tests for the command line entry points.

`start` is the one the user actually types every day, so its two paths both
matter: Chrome already running, and Chrome refusing to start.
"""

from __future__ import annotations

import argparse

import pytest

import main as cli
from fbposter.errors import ChromeNotFoundError


@pytest.fixture
def stub(monkeypatch, tmp_path):
    """Neutralise everything that would touch Chrome or open a window."""
    calls: dict[str, object] = {"gui": 0, "launch": []}

    def fake_gui(_args):
        calls["gui"] += 1
        return 0

    def fake_resolve(*_a, **_k):
        return tmp_path / "profile"

    monkeypatch.setattr(cli, "cmd_gui", fake_gui)
    monkeypatch.setattr(cli.config, "resolve_profile_dir", fake_resolve)
    return calls


class TestStart:
    def test_it_launches_chrome_then_opens_the_app(self, stub, monkeypatch):
        monkeypatch.setattr(cli.chrome, "launch", lambda *a, **k: True)
        assert cli.cmd_start(argparse.Namespace()) == 0
        assert stub["gui"] == 1

    def test_chrome_already_running_is_not_an_error(self, stub, monkeypatch, capsys):
        """The normal case. It must reuse the browser, not complain."""
        monkeypatch.setattr(cli.chrome, "launch", lambda *a, **k: False)

        assert cli.cmd_start(argparse.Namespace()) == 0
        assert stub["gui"] == 1
        assert "already running" in capsys.readouterr().out

    def test_chrome_launched_off_screen_never_visible(self, stub, monkeypatch):
        """start is for everyday use, so the window must not steal focus."""
        seen: dict[str, object] = {}

        def record(profile_dir, port=None, *, visible):
            seen["visible"] = visible
            return True

        monkeypatch.setattr(cli.chrome, "launch", record)
        cli.cmd_start(argparse.Namespace())
        assert seen["visible"] is False

    def test_it_still_opens_the_app_when_chrome_cannot_start(self, stub, monkeypatch, capsys):
        """Groups and templates are still editable without a browser, and the
        connection pill explains the problem. Refusing to open the window would
        be less useful, not safer."""

        def boom(*_a, **_k):
            raise ChromeNotFoundError("no chrome.exe anywhere")

        monkeypatch.setattr(cli.chrome, "launch", boom)

        assert cli.cmd_start(argparse.Namespace()) == 0
        assert stub["gui"] == 1, "gave up instead of opening the app"
        assert "Could not start Chrome" in capsys.readouterr().err


class TestParser:
    def test_start_is_a_command(self):
        args = cli.build_parser().parse_args(["start"])
        assert args.func is cli.cmd_start

    @pytest.mark.parametrize(
        "name", ["start", "gui", "setup", "launch", "status", "probe", "dry-run"]
    )
    def test_every_command_is_wired_to_a_function(self, name):
        extra = {
            "probe": ["https://www.facebook.com/groups/1"],
            "dry-run": ["https://www.facebook.com/groups/1", "--text", "x"],
        }
        args = cli.build_parser().parse_args([name] + extra.get(name, []))
        assert callable(args.func)

    def test_a_missing_command_is_rejected(self):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args([])
