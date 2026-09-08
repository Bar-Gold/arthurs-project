"""What the user still has to do before the app can post anything.

Pure functions and copy, deliberately kept apart from `login.py`, which is the
half that drives a browser. The split is the same one `guards.py` has against
`worker.py`: the decision of *what to tell the user next* is worth testing on
its own, without Chrome, a profile directory or a Facebook session.

This module exists because the app used to answer the question with terminal
commands. `ui/connection.py` told the user to "Start it with 'main.py launch'"
and `automation/detect.py` told them to "Run 'main.py setup' and sign in
again", both of which surface in the connection pill. That is fine for the
developer and useless to everybody else -- the app it is shipped to a client as
has no terminal in front of it, and the person reading those strings has no way
to act on them.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .ui.connection import ConnectionResult, ConnectionState


class SetupStep(enum.Enum):
    """Where the user is in getting the app ready, worst problem first."""

    CHROME_MISSING = "chrome_missing"
    CHROME_DOWN = "chrome_down"
    LOGGED_OUT = "logged_out"
    CHECKPOINT = "checkpoint"
    ERROR = "error"
    READY = "ready"


@dataclass(frozen=True)
class Guidance:
    """One step's worth of human-facing copy.

    `action` is the button label, or None when there is nothing the app can
    usefully do on the user's behalf -- installing Chrome and clearing a
    Facebook checkpoint are both jobs only they can do.
    """

    headline: str
    detail: str
    action: str | None
    done: bool


# Written for somebody who has never seen a terminal. No file paths, no command
# names, no mention of CDP or debugging ports: none of it is actionable, and
# all of it reads as an error even when the app is working perfectly.
GUIDANCE: dict[SetupStep, Guidance] = {
    SetupStep.CHROME_MISSING: Guidance(
        headline="Google Chrome is not installed",
        detail=(
            "This app posts through a real Chrome window, so Chrome has to be "
            "installed first. Install it from google.com/chrome, then come back "
            "here and press Check again."
        ),
        action=None,
        done=False,
    ),
    SetupStep.CHROME_DOWN: Guidance(
        headline="Chrome is not running yet",
        detail=(
            "The app uses its own separate Chrome window, kept off-screen so it "
            "never gets in your way."
        ),
        action="Start Chrome",
        done=False,
    ),
    SetupStep.LOGGED_OUT: Guidance(
        headline="You are not logged into Facebook",
        detail=(
            "A Chrome window will open. Log into Facebook in it exactly as you "
            "normally would, then come back here. You only have to do this once "
            "— the app never asks for your password and never logs in for you."
        ),
        action="Log in to Facebook",
        done=False,
    ),
    SetupStep.CHECKPOINT: Guidance(
        headline="Facebook is asking you to confirm it is you",
        detail=(
            "Facebook has put a security check in front of the account. Open the "
            "Chrome window, finish the check yourself, then press Check again. "
            "The app will never click through one of these for you."
        ),
        action="Show me the window",
        done=False,
    ),
    SetupStep.ERROR: Guidance(
        headline="Something went wrong checking the connection",
        detail="Press Check again. If it keeps happening, close the app and reopen it.",
        action="Check again",
        done=False,
    ),
    SetupStep.READY: Guidance(
        headline="You are all set",
        detail="Chrome is running and your Facebook account is logged in.",
        action=None,
        done=True,
    ),
}


# Changing which Facebook account the app posts as. Deliberately not a
# SetupStep and not part of `plan()`: it is not a step towards being ready, it
# is something you do once you already are, so a step of its own would have to
# be one nobody is ever "on". The copy lives here anyway, with the rest of the
# copy, so the same test greps it for terminal instructions.
SWITCH_ACTION = "Switch account"
# The card asks the question while it is waiting for an answer. Left saying
# "You are all set" above a warning about signing out, it read as though the
# app were congratulating the user and cautioning them in the same breath.
SWITCH_HEADLINE = "Switch to a different Facebook account?"
SWITCH_DETAIL = (
    "Posting as somebody else? This signs the current account out of the app's "
    "own Chrome window and opens the Facebook login, so you can sign in as "
    "another one. Your groups, templates, repeating posts and posting history "
    "all stay exactly as they are."
)
SWITCH_WARNING = (
    "You will need the other account's password to hand. Nothing is deleted, "
    "and the Chrome you browse in yourself is not touched — only the separate "
    "window this app posts through."
)
SWITCH_CONFIRM = "Yes, sign out and switch"
SWITCH_CANCEL = "Cancel"
# Refused rather than queued. Dropping the cookies with a post half-typed in
# the composer fails that post, and the batch then halts on a verification that
# could never have succeeded.
SWITCH_BUSY = (
    "A post is going out right now. Wait until it has finished, then switch."
)
# What the pill says the moment the cookies are gone, before any check has had
# time to come back and say the same thing.
SWITCH_DONE = "Signed out. Log in as the other account in the Chrome window."


def plan(chrome_installed: bool, result: ConnectionResult | None) -> SetupStep:
    """Decide the single next thing standing between the user and posting.

    One step at a time on purpose. A checklist that reports four problems at
    once is four times as intimidating and no more useful, because they have to
    be fixed in order anyway -- there is no point telling somebody they are
    logged out of Facebook when Chrome is not even installed.

    `result` is None before the first check has come back.
    """
    if not chrome_installed:
        return SetupStep.CHROME_MISSING
    if result is None:
        return SetupStep.CHROME_DOWN

    return {
        ConnectionState.CONNECTED: SetupStep.READY,
        ConnectionState.CHROME_DOWN: SetupStep.CHROME_DOWN,
        ConnectionState.LOGGED_OUT: SetupStep.LOGGED_OUT,
        ConnectionState.CHECKPOINT: SetupStep.CHECKPOINT,
        ConnectionState.ERROR: SetupStep.ERROR,
        # Neither is a finished answer, so neither should claim the user is
        # ready. Reporting the step they were most likely on keeps the wizard
        # from flickering back to the top while a check is in flight.
        ConnectionState.UNKNOWN: SetupStep.CHROME_DOWN,
        ConnectionState.CHECKING: SetupStep.CHROME_DOWN,
    }[result.state]


def guidance(step: SetupStep) -> Guidance:
    return GUIDANCE[step]


def needs_setup(step: SetupStep) -> bool:
    return step is not SetupStep.READY


class RowState(enum.Enum):
    DONE = "done"
    CURRENT = "current"
    TODO = "todo"


# The three things that have to be true before a post can go out, in the order
# they have to become true. Shown as a list so the user can see how far along
# they are; `plan()` still decides which single one they are being asked about.
CHECKLIST = (
    "Google Chrome installed",
    "Chrome running",
    "Logged into Facebook",
)

# How far down the list each step sits. READY is past the end, so everything
# reads as done.
_POSITION = {
    SetupStep.CHROME_MISSING: 0,
    SetupStep.CHROME_DOWN: 1,
    SetupStep.LOGGED_OUT: 2,
    SetupStep.CHECKPOINT: 2,
    # A failed check proves Chrome is installed and says nothing reliable about
    # the rest, so it points at the last row rather than claiming a login.
    SetupStep.ERROR: 2,
    SetupStep.READY: len(CHECKLIST),
}


def checklist(step: SetupStep) -> tuple[tuple[str, RowState], ...]:
    """The three-row progress list, with one row marked as the current one."""
    position = _POSITION[step]
    rows = []
    for index, label in enumerate(CHECKLIST):
        if index < position:
            state = RowState.DONE
        elif index == position:
            state = RowState.CURRENT
        else:
            state = RowState.TODO
        rows.append((label, state))
    return tuple(rows)
