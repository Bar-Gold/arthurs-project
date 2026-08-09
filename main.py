"""Command line entry point.

    python main.py gui       open the desktop app
    python main.py setup     launch Chrome on-screen for the one-time Facebook login
    python main.py launch    launch Chrome off-screen, ready for automation
    python main.py status    attach over CDP and report on the Facebook session
"""

from __future__ import annotations

import argparse
import sys

from fbposter import chrome, config, session
from fbposter.errors import ChromeNotRunningError, FBPosterError

SETUP_INSTRUCTIONS = """
Chrome is open with the automation profile.

  1. Log into Facebook in that window, and tick "remember me" if offered.
  2. Leave it logged in. The profile keeps the session between runs, so this
     is a one-time step.
  3. Run:  python main.py status

This is the only part of the app that expects you to touch the browser, and it
is deliberate -- the login is never automated.
""".strip()


def cmd_setup(_: argparse.Namespace) -> int:
    profile_dir = config.resolve_profile_dir()
    print(f"Profile directory: {profile_dir}")

    started = chrome.launch(profile_dir, visible=True)
    if started:
        print(f"Started Chrome on debugging port {config.DEBUG_PORT}.")
    else:
        print(
            f"Chrome is already listening on port {config.DEBUG_PORT}; reusing it.\n"
            "If you cannot see its window it was launched off-screen -- close it "
            "and run setup again."
        )
    print()
    print(SETUP_INSTRUCTIONS)
    return 0


def cmd_launch(_: argparse.Namespace) -> int:
    profile_dir = config.resolve_profile_dir()
    print(f"Profile directory: {profile_dir}")

    started = chrome.launch(profile_dir, visible=False)
    if started:
        print(
            f"Started Chrome off-screen on debugging port {config.DEBUG_PORT}.\n"
            "It has no visible window by design, so it cannot take focus while you work."
        )
    else:
        print(f"Chrome is already listening on port {config.DEBUG_PORT}; nothing to do.")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    version = chrome.probe()
    if version is None:
        raise ChromeNotRunningError(
            f"Nothing is listening on port {config.DEBUG_PORT}.\n"
            "Run 'python main.py launch' first, or 'python main.py setup' if you "
            "have not logged into Facebook yet."
        )

    print(f"Connected to: {version.get('Browser', 'unknown build')}")
    print(f"Profile directory: {config.resolve_profile_dir()}")

    with session.attach() as context:
        status = session.verify_session(context)

    if status.logged_in:
        print(f"Facebook session: LOGGED IN ({status.detail})")
        return 0

    print(f"Facebook session: NOT LOGGED IN\n  {status.detail}")
    print("\nRun 'python main.py setup' and log in manually.")
    return 1


def cmd_gui(_: argparse.Namespace) -> int:
    # Imported here so the CLI commands stay usable on a machine where the GUI
    # dependencies are missing or there is no display.
    from fbposter.ui.app import run

    return run()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Facebook Local Auto-Poster.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("gui", help="open the desktop app").set_defaults(func=cmd_gui)
    subparsers.add_parser(
        "setup", help="launch Chrome on-screen for the one-time Facebook login"
    ).set_defaults(func=cmd_setup)
    subparsers.add_parser(
        "launch", help="launch Chrome off-screen, ready for automation"
    ).set_defaults(func=cmd_launch)
    subparsers.add_parser(
        "status", help="attach over CDP and report on the Facebook session"
    ).set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except FBPosterError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
