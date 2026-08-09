"""The connection check behind the sidebar pill.

Kept apart from the widgets so the state mapping can be tested without a
window, and so the blocking Playwright call has an obvious home: this function
runs on the background thread, never on the UI thread.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .. import chrome, session
from ..errors import CheckpointError, FBPosterError


class ConnectionState(enum.Enum):
    UNKNOWN = "unknown"
    CHECKING = "checking"
    CONNECTED = "connected"
    CHROME_DOWN = "chrome_down"
    LOGGED_OUT = "logged_out"
    CHECKPOINT = "checkpoint"
    ERROR = "error"


@dataclass(frozen=True)
class ConnectionResult:
    state: ConnectionState
    detail: str


def check_connection() -> ConnectionResult:
    """Probe Chrome and the Facebook session. Blocking; call off the UI thread."""
    if chrome.probe() is None:
        return ConnectionResult(
            ConnectionState.CHROME_DOWN,
            "Chrome is not running. Start it with 'main.py launch'.",
        )

    try:
        with session.attach() as context:
            status = session.verify_session(context)
    except CheckpointError as exc:
        # Given its own state rather than folded into ERROR: a checkpoint means
        # stop and go look at the browser, not retry.
        return ConnectionResult(ConnectionState.CHECKPOINT, str(exc))
    except FBPosterError as exc:
        return ConnectionResult(ConnectionState.ERROR, str(exc))

    if status.logged_in:
        return ConnectionResult(
            ConnectionState.CONNECTED, f"Logged in as {status.user_id}"
        )
    return ConnectionResult(ConnectionState.LOGGED_OUT, status.detail)
