"""Running blocking work without freezing the window.

Tkinter is single-threaded: only the thread that created a widget may touch it.
Playwright calls block for seconds at a time, so they run on a worker thread and
their results come back through a queue that is drained by a widget.after()
pump on the main thread.

This is a deliberately small version of the architecture in CLAUDE.md -- the
Phase 5 posting worker is a separate, strictly serial thread, but it reports
progress to the UI through exactly this mechanism.
"""

from __future__ import annotations

import queue
import threading
import traceback
from typing import Any, Callable

Callback = Callable[[Any], None]


class BackgroundRunner:
    """Runs callables off the main thread and delivers results back onto it."""

    def __init__(self, widget: Any, poll_ms: int = 80) -> None:
        self._widget = widget
        self._poll_ms = poll_ms
        self._results: queue.Queue[tuple[Callback | None, Any]] = queue.Queue()
        self._pending = 0
        self._stopped = False
        self._after_id: str | None = None
        self._schedule()

    @property
    def pending(self) -> int:
        """How many jobs are in flight. Used to keep buttons disabled."""
        return self._pending

    def submit(
        self,
        fn: Callable[[], Any],
        on_success: Callback | None = None,
        on_error: Callback | None = None,
    ) -> threading.Thread:
        """Run fn on a worker thread; fire the matching callback on the main one."""
        self._pending += 1
        thread = threading.Thread(
            target=self._run, args=(fn, on_success, on_error), daemon=True
        )
        thread.start()
        return thread

    def stop(self) -> None:
        """Stop the pump. Worker threads are daemons and die with the process."""
        self._stopped = True
        if self._after_id is not None:
            try:
                self._widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _run(self, fn: Callable[[], Any], on_success: Callback | None, on_error: Callback | None) -> None:
        try:
            result = fn()
        except Exception as exc:
            # A worker that dies silently is worse than one that reports; route
            # the exception to on_error rather than letting the thread vanish.
            traceback.print_exc()
            self._results.put((on_error, exc))
        else:
            self._results.put((on_success, result))

    def _schedule(self) -> None:
        if self._stopped:
            return
        try:
            self._after_id = self._widget.after(self._poll_ms, self._drain)
        except Exception:
            # The widget is gone (window closed mid-flight); nothing left to pump.
            self._stopped = True

    def _drain(self) -> None:
        try:
            while True:
                try:
                    callback, payload = self._results.get_nowait()
                except queue.Empty:
                    break
                self._pending -= 1
                if callback is None:
                    continue
                try:
                    callback(payload)
                except Exception:
                    # One bad callback must not stop the pump for everything else.
                    traceback.print_exc()
        finally:
            self._schedule()
