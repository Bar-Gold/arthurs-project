"""The app's log file: what the posting did, for the support call.

The packaged app has no console, so until this existed a client's app kept no
record beyond the one line per group on the Dashboard, and a client whose
"post to several groups at once" reached one group in most posts could not be
asked why (2026-11-12). The log says, in plain lines with a time on each:
every worker event, which groups each post tried to tick and why one was not,
what each "Your content" check saw, and every unexpected error with its
traceback.

It is capped so it can never fill a client's disk: `MAX_BYTES` a file, and
`BACKUPS` older files kept beside it, the oldest dropped as a new one starts.
That is 8 MB at most, in `C:\\FBAutomation\\logs`, beside the database.

Only `run()` turns it on. A window the suite builds writes no log, and nothing
is printed either: the package's logger has a `NullHandler` (`__init__.py`),
so a warning logged with no file attached goes nowhere.

A log that cannot be written never stops the app: setting it up never raises,
and the logging module swallows a failed write.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import threading
from datetime import datetime
from pathlib import Path

LOGGER_NAME = "fbposter"
LOG_DIRNAME = "logs"
LOG_FILENAME = "fbposter.log"
# 2 MB a file and three older ones: 8 MB in all, a few weeks of posting at
# 100 groups a day.
MAX_BYTES = 2_000_000
BACKUPS = 3
FORMAT = "%(asctime)s %(levelname)-7s %(threadName)s %(name)s: %(message)s"

_handler: logging.Handler | None = None
# The error hooks there were before `start`, put back by `stop`.
_previous_hooks: tuple | None = None


def log_dir() -> Path:
    """Beside the database, where the client's other data is."""
    from . import config

    return config.database_path().parent / LOG_DIRNAME


def start(directory: Path | None = None) -> Path | None:
    """Start writing the log. Returns the file, or None when it cannot be.

    Calling it again does nothing. Never raises: the app runs without a log
    rather than not at all.
    """
    global _handler
    if _handler is not None:
        return Path(_handler.baseFilename)
    try:
        folder = directory or log_dir()
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            folder / LOG_FILENAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUPS,
            encoding="utf-8",
        )
    except Exception:
        return None
    handler.setFormatter(logging.Formatter(FORMAT))
    logger = logging.getLogger(LOGGER_NAME)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    _handler = handler
    _catch_crashes(logger)
    logger.info(
        "App started (%s), Windows %s.",
        _build(),
        platform.version(),
    )
    return Path(handler.baseFilename)


def stop() -> None:
    """Close the file. For tests, and for a clean exit."""
    global _handler, _previous_hooks
    if _handler is None:
        return
    logger = logging.getLogger(LOGGER_NAME)
    logger.removeHandler(_handler)
    _handler.close()
    _handler = None
    if _previous_hooks is not None:
        sys.excepthook, threading.excepthook = _previous_hooks
        _previous_hooks = None


def _catch_crashes(logger: logging.Logger) -> None:
    """An error nothing caught goes in the log too, on any thread, and then
    on to whatever handled it before."""
    global _previous_hooks
    previous = sys.excepthook
    _previous_hooks = (sys.excepthook, threading.excepthook)

    def on_main(kind, value, trace):
        logger.error("Uncaught error", exc_info=(kind, value, trace))
        previous(kind, value, trace)

    sys.excepthook = on_main

    previous_thread = threading.excepthook

    def on_thread(args):
        logger.error(
            "Uncaught error in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        previous_thread(args)

    threading.excepthook = on_thread


def _build() -> str:
    """The date of the program file, which says which release the client has:
    the packaged app does not know its own version number."""
    if not getattr(sys, "frozen", False):
        return "from source"
    try:
        stamp = Path(sys.executable).stat().st_mtime
        return "built " + datetime.fromtimestamp(stamp).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "build unknown"
