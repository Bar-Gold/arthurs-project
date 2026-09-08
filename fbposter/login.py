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

Switching to a *different* Facebook account is the same journey with one step
in front of it: the session already there has to end first, or Facebook simply
shows the account that is logged in. `switch_account()` does that by dropping
the profile's cookies rather than by driving Facebook's own Log out menu -- see
`_sign_out`.

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


def switch_account() -> None:
    """End the current Facebook session and offer the login form again.

    The client keeps everything except who they post as. The groups, the
    templates, the schedules and the whole posting history live in the
    database, and nothing in it is tied to an account -- the login cookie is
    only ever read as a yes/no and never stored -- so switching account is
    exactly this and nothing more.

    Raises FBPosterError with something the user can act on if it cannot be
    arranged. The caller shows it as-is.
    """
    if not chrome.is_running():
        # No window to move, and the launch below produces the only session
        # there is to end: start it where they can see it, then clear and go.
        chrome.launch(config.resolve_profile_dir(), visible=True)
        _open_facebook(move_on_screen=False, sign_out_first=True)
        return

    _open_facebook(move_on_screen=True, sign_out_first=True)


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
def _open_facebook(*, move_on_screen: bool, sign_out_first: bool = False) -> None:
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
        if sign_out_first:
            # Before the navigation, never after. Cleared afterwards, the page
            # in front of the user is still the old account's feed, and the
            # login form appears only if they think to reload it themselves.
            _sign_out(context)
        page.goto(
            strings.HOME_URL,
            timeout=config.NAV_TIMEOUT_MS,
            wait_until="domcontentloaded",
        )
        # Left open on purpose: it is what the user logs in through. Leaving
        # the `attach` block only drops our CDP connection -- the tab, and the
        # browser holding it, belong to the user and stay exactly as they are.


def _sign_out(context) -> None:
    """End whatever Facebook session this profile holds, by dropping its cookies.

    Facebook's session *is* its cookies, so removing them ends it. Done this
    way rather than by driving Facebook's own "Log out" menu, which would be a
    language-dependent click on obfuscated markup, in the account menu of all
    places, where a mis-resolved selector could press something else entirely.
    It also leaves nothing for the next account to inherit: the "recently
    logged in" chooser is a cookie too.

    Only the profile this app made is touched. The user's everyday Chrome has a
    different profile directory and never sees this.

    Raised, never swallowed. Silently leaving the session up would put the old
    account's Facebook in front of somebody who has just been told they were
    signed out, and the next thing they do is post as them.
    """
    try:
        context.clear_cookies()
    except Exception as exc:
        raise FBPosterError(
            "Could not sign the current account out of the app's Chrome window. "
            "Close every Chrome window, then press the button again."
        ) from exc


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
