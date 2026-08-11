"""The posting worker.

Exactly one of these runs, on one background thread, and it processes the queue
strictly serially -- one group at a time, globally. There is never a second
worker and batches never overlap: a "post now" raised mid-batch joins the queue
behind the one already running.

Three properties matter more than anything else here:

* **Every wait is an absolute instant, never a countdown.** The gap between
  groups is stored as `tasks.resume_at` and compared against the wall clock on
  each tick, so closing the app or suspending the machine cannot drift it.
* **Each group's outcome is committed the moment it happens**, so a crash
  resumes a batch instead of starting it again.
* **Nothing is ever retried automatically.** A duplicate post is the worst
  outcome this project has; an uncertain one is escalated to the user instead.

`now`, `sleep` and the poster are all injected, so the whole loop is testable
without a browser, a real clock, or a thread.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Protocol

from . import clock, session
from .automation import GroupPoster
from .automation.humanize import Humanizer
from .automation.poster import PostOutcome, PostRequest
from .db import Database
from .db.models import (
    TARGET_DONE,
    TARGET_FAILED,
    TARGET_PENDING,
    TARGET_RUNNING,
    TARGET_SKIPPED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_HALTED,
    TASK_MISSED,
    TASK_PENDING,
    TASK_RUNNING,
    Task,
    TaskTarget,
)
from .db.repo import GroupRepo, SettingsRepo, TaskRepo
from .errors import AutomationHalted, PostNotVerified
from .guards import check_cooldown
from .power import SleepBlocker

# A scheduled slot older than this was almost certainly missed while the
# machine was asleep. Firing a burst of late posts is exactly the behaviour the
# schedule exists to avoid, so it is reported instead.
MISSED_GRACE = timedelta(hours=2)

TICK_SECONDS = 5.0

FINISHED_STATES = {TASK_DONE, TASK_HALTED, TASK_CANCELLED, TASK_MISSED}


def _readable(exc: Exception) -> str:
    """Turn a raw Playwright failure into something worth reading.

    A locator timeout stringifies into a wall of obfuscated Facebook class
    names, which told the user nothing and buried the one useful fact: it timed
    out waiting for the page.
    """
    text = str(exc)
    if "Timeout" in text and "exceeded" in text:
        return (
            "Timed out waiting for Facebook to respond. Usually a slow or dropped "
            "connection. Nothing was posted; the batch stopped rather than guessing."
        )
    first_line = text.strip().splitlines()[0] if text.strip() else exc.__class__.__name__
    return f"Unexpected failure: {first_line[:300]}"


@dataclass(frozen=True)
class WorkerEvent:
    kind: str
    message: str
    task_id: int | None = None
    target_id: int | None = None


class Poster(Protocol):
    """The seam between the worker and a real browser."""

    def post(self, request: PostRequest) -> PostOutcome: ...

    def verify(self, group_url: str, body: str) -> bool: ...


class LivePoster:
    """Drives real Chrome. One page per group, opened and closed each time.

    Holding a CDP connection open across a multi-hour batch would mean a
    browser restart kills the run; reattaching per group costs a second and
    survives it.
    """

    def __init__(self, dry_run: bool = False) -> None:
        self.dry_run = dry_run

    def post(self, request: PostRequest) -> PostOutcome:
        with session.attach() as context:
            page = context.new_page()
            try:
                return GroupPoster(page, dry_run=self.dry_run).post(request)
            finally:
                page.close()

    def verify(self, group_url: str, body: str) -> bool:
        with session.attach() as context:
            page = context.new_page()
            try:
                poster = GroupPoster(page)
                page.goto(group_url, timeout=45_000, wait_until="domcontentloaded")
                return poster.verify(body, group_url)
            finally:
                page.close()


class PostingWorker:
    def __init__(
        self,
        db: Database,
        poster: Poster | None = None,
        *,
        events: "queue.Queue[WorkerEvent] | None" = None,
        now: Callable[[], Any] = clock.utcnow,
        sleep: Callable[[float], None] = time.sleep,
        humanizer: Humanizer | None = None,
        blocker: SleepBlocker | None = None,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.db = db
        self.tasks = TaskRepo(db)
        self.groups = GroupRepo(db)
        self.settings = SettingsRepo(db)
        self.poster = poster if poster is not None else LivePoster()
        self.events: "queue.Queue[WorkerEvent]" = events or queue.Queue()
        self._now = now
        self._sleep = sleep
        self.human = humanizer or Humanizer()
        self.blocker = blocker or SleepBlocker()
        self.tick_seconds = tick_seconds

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._busy = False

    # -- lifecycle ---------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def state(self) -> str:
        if not self.running:
            return "stopped"
        if self.paused:
            return "paused"
        return "posting" if self._busy else "idle"

    def start(self) -> None:
        """Start the one worker thread. Calling twice is a no-op, by design."""
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="poster", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        self.blocker.release()

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def emit(self, kind: str, message: str, task_id: int | None = None, target_id: int | None = None) -> None:
        self.events.put(WorkerEvent(kind, message, task_id, target_id))

    # -- the loop ----------------------------------------------------------
    def _loop(self) -> None:
        try:
            self.recover()
        except Exception as exc:  # never let the thread die silently
            self.emit("error", f"Recovery failed: {exc}")

        while not self._stop.is_set():
            if self._paused.is_set():
                self.blocker.release()
                self._stop.wait(self.tick_seconds)
                continue

            try:
                did_work = self.run_once()
            except Exception as exc:
                self.emit("error", f"Worker error: {exc}")
                did_work = False

            self._sync_power()
            if not did_work:
                self._stop.wait(self.tick_seconds)

        self.blocker.release()

    def _sync_power(self) -> None:
        """Hold off sleep only while a batch is genuinely under way.

        The gap between groups counts: suspending during it would strand the
        rest of the batch just as surely as suspending mid-post.
        """
        if self.tasks.active_batches() > 0:
            self.blocker.acquire()
        else:
            self.blocker.release()

    # -- one iteration -----------------------------------------------------
    def run_once(self) -> bool:
        """Advance the queue by at most one step. Returns True if it did."""
        now = self._now()
        for task in self.tasks.claimable(now):
            fresh = self.tasks.get(task.id)
            if fresh is None or fresh.state in FINISHED_STATES:
                continue

            if self._is_missed(fresh, now):
                self.tasks.mark_task(
                    fresh.id,
                    TASK_MISSED,
                    error="Scheduled slot passed while nothing was running.",
                    finished=True,
                )
                self.emit(
                    "missed",
                    "A scheduled batch was missed, probably while the machine was "
                    "asleep. It has not been fired late.",
                    fresh.id,
                )
                return True

            target = self.tasks.next_pending_target(fresh.id)
            if target is None:
                self._finish(fresh)
                return True

            return self._attempt(fresh, target, now)
        return False

    def _is_missed(self, task: Task, now) -> bool:
        # Only a batch that never started can be missed; one already in
        # progress simply carries on.
        if task.state != TASK_PENDING or task.started_at is not None:
            return False
        if task.scheduled_for is None:
            return False
        return now - task.scheduled_for > MISSED_GRACE

    def _finish(self, task: Task) -> None:
        self.tasks.set_resume_at(task.id, None)
        self.tasks.mark_task(task.id, TASK_DONE, finished=True)
        self.emit("batch_done", "Batch finished.", task.id)

    # -- guards, re-checked at the moment of posting -----------------------
    def _attempt(self, task: Task, target: TaskTarget, now) -> bool:
        group = self.groups.get(target.group_id)
        if group is None:
            self.tasks.mark_target(
                target.id, TARGET_FAILED, error="Group was removed.", attempted=True
            )
            return True

        start_hour = self.settings.get_int("posting_window_start_hour", 8)
        end_hour = self.settings.get_int("posting_window_end_hour", 23)
        cap = self.settings.get_int("daily_cap", 25)

        # The world moved since this was queued, so the rules are checked again
        # here rather than trusted from enqueue time.
        posted_today = self.tasks.posted_count_since(clock.start_of_local_day(now))
        if posted_today + 1 > cap:
            resume = clock.next_window_open(
                now + timedelta(days=1), start_hour, end_hour
            )
            self.tasks.set_resume_at(task.id, resume)
            self.emit(
                "deferred",
                f"Daily cap of {cap} reached; the rest of this batch waits until "
                f"{clock.format_local(resume)}.",
                task.id,
            )
            return True

        window_open = clock.next_window_open(now, start_hour, end_hour)
        if window_open > now:
            self.tasks.set_resume_at(task.id, window_open)
            self.emit(
                "deferred",
                f"Outside the {start_hour:02d}:00-{end_hour:02d}:00 window; resuming "
                f"{clock.format_local(window_open)}.",
                task.id,
            )
            return True

        cooldown = check_cooldown(
            group.last_posted_at, now, group.cooldown_hours, group.display_name
        )
        if cooldown is not None:
            # Skip this one group rather than stalling the whole batch behind it.
            self.tasks.mark_target(
                target.id, TARGET_SKIPPED, error=cooldown.message, attempted=True
            )
            self.emit("skipped", cooldown.message, task.id, target.id)
            return True

        return self._post(task, target, group)

    # -- the actual post ---------------------------------------------------
    def _post(self, task: Task, target: TaskTarget, group) -> bool:
        self.tasks.mark_task(task.id, TASK_RUNNING, started=True)
        self.tasks.mark_target(target.id, TARGET_RUNNING, attempted=True)
        self.blocker.acquire()
        self._busy = True
        self.emit("posting", f"Posting to {group.display_name}…", task.id, target.id)


        request = PostRequest(
            group_url=group.url,
            body=target.body,
            media_paths=tuple(Path(p) for p in task.media_paths),
        )

        try:
            outcome = self.poster.post(request)
        except AutomationHalted as exc:
            self._halt(task, target, str(exc))
            return True
        except PostNotVerified as exc:
            # Deliberately not a retry: it may well have posted.
            self._halt(task, target, str(exc))
            return True
        except Exception as exc:
            self._halt(task, target, _readable(exc))
            return True
        finally:
            self._busy = False

        if not outcome.posted:
            # A dry-run poster reaches here having published nothing. Recording
            # it as done would falsely start the group's cooldown and count
            # against the daily cap, so the outcome is believed rather than
            # assumed from the absence of an exception.
            self.tasks.mark_target(
                target.id, TARGET_SKIPPED, error=outcome.detail, attempted=True
            )
            self.emit(
                "skipped",
                f"{group.display_name}: {outcome.detail}",
                task.id,
                target.id,
            )
        else:
            self.tasks.mark_target(
                target.id, TARGET_DONE, posted=True, post_url=outcome.post_url
            )
            self.groups.mark_posted(group.id)
            self.emit("posted", f"Posted to {group.display_name}.", task.id, target.id)

        if self.tasks.remaining_targets(task.id) == 0:
            self._finish(task)
            return True

        gap = self.human.gap_between_groups()
        resume = self._now() + timedelta(seconds=gap)
        self.tasks.set_resume_at(task.id, resume)
        spacing = f"{gap / 60:.0f} min" if gap >= 60 else f"{gap:.0f}s"
        self.emit(
            "waiting",
            f"Next group at {clock.format_local(resume)} ({spacing} gap).",
            task.id,
        )
        return True

    def _halt(self, task: Task, target: TaskTarget, message: str) -> None:
        """Stop the whole batch. Never retry, never continue to the next group."""
        self.tasks.mark_target(target.id, TARGET_FAILED, error=message)
        self.tasks.set_resume_at(task.id, None)
        self.tasks.mark_task(task.id, TASK_HALTED, error=message, finished=True)
        self.blocker.release()
        self.emit("halted", message, task.id, target.id)

    # -- crash recovery ----------------------------------------------------
    def recover(self) -> None:
        """Resolve targets left mid-flight by a crash.

        Whether such a target posted is genuinely unknown. Guessing "no" would
        risk a duplicate, which is the worst thing this app can do; guessing
        "yes" would silently skip a group. So it looks.
        """
        for target in self.tasks.running_targets():
            group = self.groups.get(target.group_id)
            if group is None:
                self.tasks.mark_target(
                    target.id, TARGET_FAILED, error="Group was removed."
                )
                continue

            try:
                already_posted = self.poster.verify(group.url, target.body)
            except Exception as exc:
                self.tasks.mark_target(
                    target.id,
                    TARGET_FAILED,
                    error=(
                        "Interrupted mid-post and could not be checked "
                        f"({exc}). Look at the group before queueing this again — "
                        "posting it twice is worse than not posting it."
                    ),
                )
                self.tasks.mark_task(
                    target.task_id, TASK_HALTED, error="Interrupted; needs checking.",
                    finished=True,
                )
                self.emit("halted", "A batch was interrupted and needs checking.", target.task_id, target.id)
                continue

            if already_posted:
                self.tasks.mark_target(target.id, TARGET_DONE, posted=True)
                self.groups.mark_posted(group.id)
                self.emit(
                    "recovered",
                    f"{group.display_name} had already been posted to before the "
                    "interruption; not posting again.",
                    target.task_id,
                    target.id,
                )
            else:
                self.tasks.mark_target(target.id, TARGET_PENDING)
                self.emit(
                    "recovered",
                    f"{group.display_name} was interrupted before posting; requeued.",
                    target.task_id,
                    target.id,
                )
