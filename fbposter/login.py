"""Getting the user logged into Facebook, once, by hand.

The login is never automated -- that rule is not negotiable and nothing here
bends it. What this module does is remove the terminal from the process: it
puts a real Chrome window in front of the user with Facebook open in it, and
puts that window back off-screen when they are done.

Two situations, and they need different handling:

* **Chrome is not running.** Launch it visible. This is the first run on a new
  machine, and it is the easy case.
* **Chrome is already running off-screen**, which is the normal state of this
  app and therefore what a *re-*login hits when a session expires. The window
  exists and is parked at -32000,-32000 where nobody can reach it. Restarting
  Chrome to fix that would be the obvious move and is the wrong one: there is
  no dependable way to close a window the user cannot see, and a restart during
  an active batch would strand it. So the window is moved on-screen over CDP
  instead, and moved back afterwards.

Moving a window is not `bring_to_front()`. The banned call raises a page above
whatever the user is working in, at a moment they did not ask for; this runs
only because they just pressed a button that says a Chrome window will open.
"""

from __future__ import annotations

from . import chrome, config, session, strings
from .errors import ChromeNotFoundError, FBPosterError


def chrome_installed() -> bool:
    """Whether chrome.exe exists anywhere the app knows to look."""
    try:
        chrome.find_chrome()
    except ChromeNotFoundError:
        return False
    return True


def start_chrome() -> bool:
    """Start the automation Chrome off-screen. True if a new one was started."""
    return chrome.launch(config.resolve_profile_dir(), visible=False)


def open_login_window() -> None:
    """Put a visible Chrome window in front of the user, showing Facebook.

    Raises FBPosterError with something the user can act on if that cannot be
    arranged -- the caller shows it as-is, so it must never mention a command.
    """
    if not chrome.is_running():
        # Nothing to move: launch it where they can see it in the first place.
        chrome.launch(config.resolve_profile_dir(), visible=True)
        _open_facebook(move_on_screen=False)
        return

    _open_facebook(move_on_screen=True)


def hide_login_window() -> None:
    """Park the window back off-screen once the login has been confirmed.

    Best-effort by design. A window left on screen is untidy; an exception here
    would undo a login that actually succeeded, which is far worse.
    """
    try:
        with session.attach() as context:
            page = context.new_page()
            try:
                _set_bounds(context, page, config.OFFSCREEN_BOUNDS)
            finally:
                page.close()
    except Exception:
        pass


# -- internals -------------------------------------------------------------
def _open_facebook(*, move_on_screen: bool) -> None:
    with session.attach() as context:
        # A page of our own. contexts[0].pages[0] may be the tab the user was
        # just looking at, and this one gets navigated.
        page = context.new_page()
        if move_on_screen and not _set_bounds(context, page, config.LOGIN_WINDOW_BOUNDS):
            page.close()
            raise FBPosterError(
                "The Chrome window this app uses is open but could not be brought "
                "on screen. Close every Chrome window, then press the button again."
            )
        page.goto(
            strings.HOME_URL,
            timeout=config.NAV_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        # Left open on purpose: it is what the user logs in through. Leaving
        # the `attach` block only drops our CDP connection -- the tab, and the
        # browser holding it, belong to the user and stay exactly as they are.


def _set_bounds(context, page, bounds: dict) -> bool:
    """Move the window this page lives in. False if Chrome would not do it.

    Swallowed rather than raised because the caller has a better answer than a
    stack trace: on the launch-visible path the window is already where it
    needs to be, so a failure here changes nothing at all.
    """
    try:
        cdp = context.new_cdp_session(page)
        window = cdp.send("Browser.getWindowForTarget")
        cdp.send(
            "Browser.setWindowBounds",
            {"windowId": window["windowId"], "bounds": dict(bounds)},
        )
    except Exception:
        return False
    return True
