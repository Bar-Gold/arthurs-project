---
paths:
  - "tests/**"
---

# Tests

Moved out of the root `CLAUDE.md`, which still applies. This file loads by itself when Claude reads or edits a file listed above.

## How the tests avoid a browser and a real clock

Nothing in the suite opens Chrome, hits Facebook, or waits out a real delay. Keep it that way. Every seam already exists:

- **`tests/fake_page.py`** stands in for a Playwright `Page`, recording every call into `page.calls`. It implements only the surface `GroupPoster` actually uses, so a poster that starts calling something new fails loudly instead of quietly passing. Its knobs (`missing`, `redirect_to`, `body_text`, `wait_fails_for`, `never_detaches`) are how the halt paths, the slow-publish path and the dry-run boundary get exercised.
- **`Humanizer(rng=, sleep=)`**: pass a seeded `Random` and a no-op sleep and the human pacing is deterministic and instant.
- **`PostingWorker(poster=, now=, sleep=, blocker=, tick_seconds=)`**: the whole loop, including the inter-group gap and crash recovery, runs without a thread, a browser or the wall clock.
- **`App(check_fn=, db=, group_namer=)`**: a temporary database and `SilentNamer`. Constructing an `App` deliberately does not start the worker. `App.group_finder` is `None` (switched off) unless a test passes a stub; `run()` switches the live one on.
- **Looking inside Chrome is inert until `qtui.app.run()` switches it on.** `session.before_attach` is `None`, and a window's `_clear_stuck_tabs` and `_restart_chrome` do nothing, unless `run()` wires up `tabs.py`. The suite never does, so no test can look inside, let alone close a tab in, the app's Chrome running on the developer's machine. `tests/test_tabs.py` tests the DevTools client against a WebSocket server on a thread of its own.
- **Qt tests run offscreen.** `tests/conftest.py` has a session-scoped `qt_application` (one `QApplication`, `QT_QPA_PLATFORM=offscreen`) and a per-test `qt_app` window on a temporary database. Offscreen is not tidiness: this app's central promise is that it never takes focus, and a suite that popped real windows would break that on the developer's own machine every time it ran. Drive views through their own methods rather than synthesised clicks. Note that `deleteLater()` widgets keep painting until the event loop turns, so anything that reads pixels needs a real loop turn first. Two "duplicate row" and "giant blue rectangle" scares came from screenshotting without one.

**`QPixmap` cannot be constructed before a `QApplication` exists**: Qt aborts the process (`STATUS_STACK_BUFFER_OVERRUN`), so pytest reports nothing at all rather than a failure. Any test touching `QPixmap`, `cover()` or `avatar()` must depend on the `qt_application` fixture even if it never builds a widget.
