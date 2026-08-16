"""Command line entry point.

    python main.py start     launch Chrome if needed, then open the app  <- everyday use
    python main.py gui       open the app without touching Chrome
    python main.py setup     launch Chrome on-screen for the one-time Facebook login
    python main.py launch    launch Chrome off-screen, ready for automation
    python main.py status    attach over CDP and report on the Facebook session
    python main.py probe     check the composer selectors resolve (types nothing)
    python main.py dry-run   full rehearsal that never clicks Post
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from fbposter import chrome, config, session
from fbposter.automation import GroupPoster
from fbposter.automation.poster import PostRequest
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


def cmd_gui(args: argparse.Namespace) -> int:
    # Imported here so the CLI commands stay usable on a machine where the GUI
    # dependencies are missing or there is no display.
    #
    # Qt by default: Tk cannot render Hebrew mixed with English correctly, which
    # is most of what this app is used to write. --tk keeps the old window
    # available while the Qt one settles in.
    if getattr(args, "tk", False):
        from fbposter.ui.app import run
    else:
        from fbposter.qtui.app import run

    return run()


def cmd_start(args: argparse.Namespace) -> int:
    """The everyday entry point: make sure Chrome is up, then open the app.

    Chrome already running is the normal case, not an error -- chrome.launch()
    reuses it. And a Chrome that cannot be started is not a reason to refuse to
    open the window: groups and templates are still editable, and the
    connection pill will say what is wrong. So this warns and carries on rather
    than dying at the terminal.
    """
    try:
        profile_dir = config.resolve_profile_dir()
        if chrome.launch(profile_dir, visible=False):
            print(f"Started Chrome off-screen on port {config.DEBUG_PORT}.")
        else:
            print(f"Chrome is already running on port {config.DEBUG_PORT}; reusing it.")
    except FBPosterError as exc:
        print(f"\nCould not start Chrome:\n{exc}\n", file=sys.stderr)
        print(
            "Opening the app anyway — you can still edit groups and templates, but "
            "nothing will post until Chrome is up. Try 'main.py setup' if you have "
            "never logged in.\n",
            file=sys.stderr,
        )

    print("Opening the app…")
    return cmd_gui(args)


def _require_chrome() -> None:
    if chrome.probe() is None:
        raise ChromeNotRunningError(
            f"Nothing is listening on port {config.DEBUG_PORT}.\n"
            "Run 'python main.py launch' first."
        )


def _new_page(context):
    """A page of our own.

    Never reuses contexts[0].pages[0] -- that is very likely a tab the user has
    open, and this one gets navigated and closed.
    """
    return context.new_page()


def cmd_probe(args: argparse.Namespace) -> int:
    """Read-only: confirm the composer selectors resolve. Types nothing."""
    _require_chrome()
    print(f"Probing {args.group_url}\nRead-only — nothing will be typed or posted.\n")

    with session.attach() as context:
        page = _new_page(context)
        try:
            report = GroupPoster(page).probe(args.group_url)
        finally:
            page.close()

    checks = [
        ("page loaded cleanly", report.page_ok),
        ("composer trigger", report.composer_trigger),
        ("composer dialog", report.dialog),
        ("text field", report.textbox),
        ("Photo/video button", report.photo_button),
        ("Post button", report.post_button),
    ]
    for label, found in checks:
        print(f"  [{'ok' if found else 'MISSING'}] {label}")

    for note in report.notes:
        print(f"\n  {note}")

    print("\nAll selectors resolved." if report.ok else "\nSome selectors did not resolve.")
    return 0 if report.ok else 1


def cmd_dry_run(args: argparse.Namespace) -> int:
    """Full rehearsal against a real group that never clicks Post."""
    _require_chrome()
    media = tuple(Path(p) for p in (args.image or []))
    for path in media:
        if not path.is_file():
            raise FBPosterError(f"No such image: {path}")

    print(
        f"Dry run against {args.group_url}\n"
        "Everything except the Post click. The draft is discarded afterwards.\n"
    )

    request = PostRequest(group_url=args.group_url, body=args.text, media_paths=media)
    with session.attach() as context:
        page = _new_page(context)
        try:
            outcome = GroupPoster(page, dry_run=True).post(request)
        finally:
            page.close()

    print(outcome.detail)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Facebook Local Auto-Poster.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser(
        "start", help="launch Chrome if it is not up, then open the app (use this one)"
    )
    start.add_argument(
        "--tk", action="store_true", help="use the old Tkinter window instead of Qt"
    )
    start.set_defaults(func=cmd_start)

    gui = subparsers.add_parser("gui", help="open the app without touching Chrome")
    gui.add_argument(
        "--tk", action="store_true", help="use the old Tkinter window instead of Qt"
    )
    gui.set_defaults(func=cmd_gui)
    subparsers.add_parser(
        "setup", help="launch Chrome on-screen for the one-time Facebook login"
    ).set_defaults(func=cmd_setup)
    subparsers.add_parser(
        "launch", help="launch Chrome off-screen, ready for automation"
    ).set_defaults(func=cmd_launch)
    subparsers.add_parser(
        "status", help="attach over CDP and report on the Facebook session"
    ).set_defaults(func=cmd_status)

    probe = subparsers.add_parser(
        "probe", help="check the composer selectors resolve (read-only, types nothing)"
    )
    probe.add_argument("group_url", help="a Facebook group URL")
    probe.set_defaults(func=cmd_probe)

    dry_run = subparsers.add_parser(
        "dry-run", help="full rehearsal against a real group that never clicks Post"
    )
    dry_run.add_argument("group_url", help="a Facebook group URL")
    dry_run.add_argument("--text", required=True, help="the post body to rehearse")
    dry_run.add_argument(
        "--image", action="append", help="image to attach; repeat for several"
    )
    dry_run.set_defaults(func=cmd_dry_run)

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
