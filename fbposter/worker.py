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

from . import clock, recurrence, session
from .automation import GroupPoster
from .automation.humanize import Humanizer
from .automation.poster import PostOutcome, PostRequest
from .db import Database
from .db.models import (
    SCHEDULE_PAUSED,
    TARGET_AWAITING_APPROVAL,
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
    Schedule,
    Task,
    TaskTarget,
    from_iso,
    to_iso,
)
from .db.repo import GroupRepo, ScheduleRepo, SettingsRepo, TaskRepo
from .errors import AutomationHalted, ConnectionFailed, PostNotVerified
from .guards import check_cooldown
from .power import SleepBlocker

# A scheduled slot older than this was almost certainly missed while the
# machine was asleep. Firing a burst of late posts is exactly the behaviour the
# schedule exists to avoid, so it is reported instead.
MISSED_GRACE = timedelta(hours=2)

TICK_SECONDS = 5.0

# How far ahead a batch has to be doing something for the machine to be held
# awake for it. Comfortably past the longest inter-group gap (25 minutes), so
# every real gap is covered -- but a batch deferred to tomorrow morning because
# the window closed is not, and the machine is free to sleep through the night.
KEEP_AWAKE_HORIZON = timedelta(minutes=30)

# Chrome is not reachable over CDP. Nothing has been typed when this happens,
# so unlike every other failure it is safe to wait and try again: after a
# Windows Update restart, a Chrome crash, or simply the first minute after
# logging in, the browser is legitimately absent for a while. Giving up
# eventually, rather than waiting for ever, keeps a dead batch from sitting in
# the queue looking alive.
CONNECTION_RETRY = timedelta(minutes=5)
CONNECTION_GIVE_UP = timedelta(hours=2)

# How often the finished-batch history is pruned. Housekeeping, not a deadline.
PRUNE_EVERY = timedelta(days=1)

# How often posts awaiting an admin are followed up, and how long to leave one
# alone first -- an admin who has just been sent something needs a moment.
FOLLOW_UP_EVERY = timedelta(hours=6)
FOLLOW_UP_AFTER = timedelta(minutes=30)
# Groups checked in one sweep. Every check drives a real browser, so an
# unbounded loop would both stall the queue for minutes and produce a burst of
# page loads no member would make. The rest come round on the next sweep.
FOLLOW_UP_PER_SWEEP = 3
# Stop chasing a post an admin has simply never got to. It stays awaiting --
# the wording remains claimed, which is the cautious direction, and the Queue
# screen goes on showing it with its two buttons -- but the app stops opening a
# group page about it twice a day for ever.
FOLLOW_UP_GIVE_UP = timedelta(days=30)
# Consecutive checks that must fail to find it before it counts as declined.
# One is not enough: a page that half-renders looks like an empty Pending tab.
MISSES_BEFORE_DECLINED = 2

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

    def pending_verdict(self, group_url: str, body: str) -> str: ...


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

    def pending_verdict(self, group_url: str, body: str) -> str:
        with session.attach() as context:
            page = context.new_page()
            try:
                return GroupPoster(page).pending_verdict(group_url, body)
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
        on_battery: Callable[[], bool | None] = lambda: None,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self.db = db
        self.tasks = TaskRepo(db)
        self.groups = GroupRepo(db)
        self.settings = SettingsRepo(db)
        self.schedules = ScheduleRepo(db)
        self.poster = poster if poster is not None else LivePoster()
        self.events: "queue.Queue[WorkerEvent]" = events or queue.Queue()
        self._now = now
        self._sleep = sleep
        self.human = humanizer or Humanizer()
        self.blocker = blocker or SleepBlocker()
        # Inert by default and wired up by the app, like every other seam here.
        # A default that read the real battery would make the suite's answer
        # depend on whether the developer's machine happened to be plugged in.
        self._on_battery = on_battery
        self.tick_seconds = tick_seconds

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._busy = False
        self._battery_warned = False
        # When Chrome first went missing, per batch. In memory rather than in
        # the database on purpose: a restart is itself a fresh chance for the
        # browser to be there, so the patience starting over is the right
        # behaviour rather than a lost detail.
        self._connection_trouble: dict[int, Any] = {}
        self._connection_announced: set[int] = set()
        self._recovery_deferred = False
        self._recovery_announced = False
        self._next_recovery_at = None
        self._recovery_trouble = None

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
        # Belt and braces. The execution state is per *thread*, so this call --
        # made from the UI thread -- cannot clear the worker thread's request;
        # what clears it is `_loop` releasing on its way out, and Windows
        # dropping it when the thread ends. This keeps the object's own idea of
        # itself honest for the case where the join above timed out.
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
        rest of the batch just as surely as suspending mid-post. A batch
        deferred to tomorrow morning does not -- holding a machine awake for
        nine hours of deliberate waiting is a cost with nothing bought by it.
        """
        if self.tasks.active_batches(self._now() + KEEP_AWAKE_HORIZON) > 0:
            self.blocker.acquire()
            self._warn_if_on_battery()
        else:
            self.blocker.release()
            self._battery_warned = False

    def _warn_if_on_battery(self) -> None:
        """Say so once per batch, because the keep-awake request is not enough.

        SetThreadExecutionState suppresses the idle timer and nothing else, and
        the power plan that stops a closed lid suspending the machine is set
        for mains only -- deliberately, because nobody wants a laptop that
        refuses to sleep in a bag. Unplugged, the batch can therefore be cut
        off with the app none the wiser, and the only useful thing to do is say
        so while it can still be fixed by reaching for the charger.
        """
        if self._battery_warned or self._on_battery() is not True:
            return
        self._battery_warned = True
        self.emit(
            "power",
            "Posting on battery — Windows can still suspend the machine and cut "
            "the batch short. Plug in to be sure it finishes.",
        )

    # -- one iteration -----------------------------------------------------
    def run_once(self) -> bool:
        """Advance the queue by at most one step. Returns True if it did."""
        now = self._now()
        self._retry_recovery(now)
        self._maybe_prune(now)
        self._follow_up_pending(now)
        if self._materialise_schedules(now):
            return True

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
        # A batch the worker deliberately deferred -- the window had closed, or
        # the daily cap was full -- is waiting on purpose, not because the
        # machine was asleep. Without this, a 23:30 slot deferred to 08:00 came
        # back nine hours "late" and was thrown away at the moment it was
        # finally allowed to run.
        if task.resume_at is not None:
            return False
        return now - task.scheduled_for > MISSED_GRACE

    def _retry_recovery(self, now) -> None:
        """Another go at a recovery Chrome was not up for.

        Only ever runs when a previous attempt deferred, so the ordinary path
        -- recovery once, at start-up -- is untouched.
        """
        if not self._recovery_deferred:
            return
        if self._next_recovery_at is not None and now < self._next_recovery_at:
            return
        self._recovery_deferred = False
        try:
            self.recover()
        except Exception as exc:
            self.emit("error", f"Recovery failed: {exc}")

    def _finish(self, task: Task) -> None:
        self._forget_connection_trouble(task.id)
        self.tasks.set_resume_at(task.id, None)
        self.tasks.mark_task(task.id, TASK_DONE, finished=True)
        self.emit("batch_done", "Batch finished.", task.id)

    # -- housekeeping --------------------------------------------------------
    def _maybe_prune(self, now) -> bool:
        """Delete finished batches older than the retention window.

        Once a day at most, tracked in the database rather than in memory so
        that opening and closing the app repeatedly does not re-run it, and so
        that an app left open for weeks still gets round to it.

        Never lets a failure reach the queue: housekeeping must not be able to
        stop posting.
        """
        keep_days = self.settings.get_int("history_retention_days", 90)
        if keep_days <= 0:
            return False

        last = from_iso(self.settings.get("last_prune_at", ""))
        if last is not None and now - last < PRUNE_EVERY:
            return False

        try:
            removed = self.tasks.prune_history(now, keep_days)
            self.settings.set("last_prune_at", to_iso(now) or "")
            if removed:
                self.tasks.reclaim_space()
                self.emit(
                    "pruned",
                    f"Cleared {removed} finished batch{'es' if removed != 1 else ''} "
                    f"older than {keep_days} days.",
                )
        except Exception as exc:  # tidying up is never worth a stalled queue
            self.emit("error", f"History cleanup skipped: {exc}")
            return False
        return bool(removed)

    def _follow_up_pending(self, now) -> bool:
        """Find out what admins did with posts awaiting approval.

        The alternative was asking the user, which is not something this app
        should need. Runs on its own slow clock and only when there is
        something to check, so a user with no moderated groups never pays for
        it -- and never outside the posting window, because a group page being
        opened at 4am is the same signal as a post at 4am.

        Wrapped the same way as the pruner, and for the same reason: this runs
        at the top of every tick, and optional work must never be able to take
        the tick that posts down with it.
        """
        try:
            return self._sweep_pending(now)
        except Exception as exc:
            self.emit("error", f"Checking pending posts failed: {exc}")
            return False

    def _sweep_pending(self, now) -> bool:
        waiting = self.tasks.awaiting_approval_targets()
        if not waiting:
            return False

        start_hour = self.settings.get_int("posting_window_start_hour", 8)
        end_hour = self.settings.get_int("posting_window_end_hour", 23)
        if clock.next_window_open(now, start_hour, end_hour) > now:
            return False

        last = from_iso(self.settings.get("last_follow_up_at", ""))
        if last is not None and now - last < FOLLOW_UP_EVERY:
            return False
        self.settings.set("last_follow_up_at", to_iso(now) or "")

        checked = 0
        for target in self._follow_up_order(waiting):
            if checked >= FOLLOW_UP_PER_SWEEP:
                break

            age = None if target.posted_at is None else now - target.posted_at
            if age is not None and (age < FOLLOW_UP_AFTER or age > FOLLOW_UP_GIVE_UP):
                continue

            group = self.groups.get(target.group_id)
            if group is None:
                # Only reachable if the group is removed between the query above
                # and this line. Nothing is stranded by skipping it: task_targets
                # cascade with the group, so removing one takes its awaiting
                # posts out of every future sweep too.
                continue

            if checked:
                self.human.read_pause()
            checked += 1
            # Recorded as attempted before the call, so one target that always
            # fails cannot sit at the front of the rotation starving the rest.
            self.settings.set("follow_up_cursor", target.id)

            try:
                verdict = self.poster.pending_verdict(group.url, target.body)
            except AutomationHalted as exc:
                # A checkpoint, a login screen or a rate-limit warning. Opening
                # more group pages into that is precisely the wrong move, so the
                # sweep stops and the user is told. Nothing about the post is
                # changed -- it is still awaiting, and still will be next time.
                self.emit("halted", f"Stopped checking pending posts: {exc}")
                break
            except ConnectionFailed as exc:
                # No browser to ask, so the other groups would fail the same way
                # -- one message rather than one per group.
                self.emit("error", f"Could not check pending posts: {exc}")
                break
            except Exception as exc:
                # Checking is optional; never let it disturb the queue.
                self.emit("error", f"Could not check {group.display_name}: {exc}")
                continue

            if verdict == "pending":
                self.tasks.clear_resolve_misses(target.id)
            elif verdict == "approved":
                self.tasks.resolve_pending(
                    target.id,
                    approved=True,
                    note="An admin approved it; confirmed visible in the group.",
                )
                self.emit(
                    "posted",
                    f"{group.display_name} approved the post — it is live now.",
                    target.task_id,
                    target.id,
                )
            elif verdict == "declined":
                misses = self.tasks.record_resolve_miss(target.id)
                if misses >= MISSES_BEFORE_DECLINED:
                    self.tasks.resolve_pending(
                        target.id,
                        approved=False,
                        note=(
                            f"Gone from the group's pending list on "
                            f"{MISSES_BEFORE_DECLINED} checks running and never "
                            "published, so an admin declined it."
                        ),
                    )
                    self.emit(
                        "declined",
                        f"{group.display_name} declined the post. That wording is "
                        "free to send again.",
                        target.task_id,
                        target.id,
                    )
            # "unknown" falls through untouched and is tried again next time.
        return checked > 0

    def _follow_up_order(self, waiting: list[TaskTarget]) -> list[TaskTarget]:
        """Round-robin, so a sweep's budget is not spent on the same few.

        Only FOLLOW_UP_PER_SWEEP groups are checked at a time, and the oldest
        pending post is also the one least likely ever to resolve. Always
        starting from the front would mean three posts an admin has abandoned
        soak up every sweep and a fourth is never looked at at all.
        """
        cursor = self.settings.get_int("follow_up_cursor", 0)
        return sorted(waiting, key=lambda target: (target.id <= cursor, target.id))

    # -- repeating schedules -----------------------------------------------
    def _materialise_schedules(self, now) -> bool:
        """Turn any schedule that has come due into an ordinary batch.

        Deliberately the only thing schedules do. Everything after this point --
        the serial queue, the 10-25 minute gap, the guards re-checked at posting
        time, crash recovery -- is the existing path, untouched, because a
        second posting path would have to re-earn all of it.
        """
        for schedule in self.schedules.due(now):
            try:
                rule = recurrence.build(schedule.times, schedule.days)
            except recurrence.InvalidRecurrence as exc:
                # Not fixable from here, and firing something we cannot describe
                # would be worse than stopping.
                self.schedules.set_state(schedule.id, SCHEDULE_PAUSED)
                self.emit(
                    "schedule_error",
                    f"{schedule.display_name} has an unusable repeat rule ({exc}); "
                    "it has been paused.",
                )
                return True

            verdict = recurrence.evaluate_due(
                rule, schedule.next_run_at, now, MISSED_GRACE
            )
            if verdict is None:
                continue

            if verdict.fire:
                self._fire_schedule(schedule, now)
            elif verdict.missed:
                self.emit(
                    "missed",
                    f"{schedule.display_name} missed its slot at "
                    f"{clock.format_local(schedule.next_run_at)}, probably while the "
                    "machine was asleep. It has not been fired late.",
                )

            if schedule.next_run_at is None:
                self.schedules.set_next_run(schedule.id, verdict.next_run_at)
            else:
                self.schedules.record_run(schedule.id, now, verdict.next_run_at)
            return True
        return False

    def _fire_schedule(self, schedule: Schedule, now) -> None:
        """Queue one occurrence, choosing each group's wording as it goes."""
        if self.tasks.unfinished_for_schedule(schedule.id) > 0:
            self.emit(
                "skipped",
                f"{schedule.display_name} came round again while its last batch was "
                "still queued; this run was skipped rather than stacked on top.",
            )
            return

        targets: list[tuple[int, str]] = []
        stale: list[str] = []
        for position, group_id in enumerate(schedule.group_ids):
            group = self.groups.get(group_id)
            if group is None:  # removed since the schedule was made
                continue
            # Rotating by run_count as well as position is what stops one
            # wording going to every group at once, run after run.
            body = recurrence.pick_body(
                schedule.bodies,
                schedule.run_count + position,
                self.groups.recent_bodies(group.id),
            )
            if body is None:
                stale.append(group.display_name)
                continue
            targets.append((group.id, body.strip()))

        if stale:
            self.emit(
                "skipped",
                f"{schedule.display_name}: every wording has already gone to "
                f"{', '.join(stale)}. Add another wording — reposting the same text "
                "is what gets accounts restricted.",
            )
        if not targets:
            return

        task = self.tasks.create(
            schedule.bodies[0],
            targets,
            media_paths=schedule.media_paths,
            scheduled_for=schedule.next_run_at,
            schedule_id=schedule.id,
        )
        self.emit(
            "scheduled",
            f"{schedule.display_name} queued for {len(targets)} group"
            f"{'s' if len(targets) != 1 else ''}.",
            task.id,
        )

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
        # Claim it first. If another worker got there, leave it alone entirely:
        # posting it as well is the duplicate this whole app is built to avoid.
        if not self.tasks.claim_target(target.id):
            return True

        self.tasks.mark_task(task.id, TASK_RUNNING, started=True)
        self.blocker.acquire()
        self._busy = True
        self.emit("posting", f"Posting to {group.display_name}…", task.id, target.id)


        # Checked before the browser is touched: an attachment moved or deleted
        # since the batch was queued otherwise surfaces as a Playwright timeout
        # deep in set_input_files, which tells the user nothing about which
        # file is missing.
        missing = [p for p in task.media_paths if not Path(p).exists()]
        if missing:
            names = ", ".join(Path(p).name for p in missing)
            self._halt(
                task,
                target,
                f"Attachment no longer on disk: {names}. Nothing was posted — "
                "re-attach the file, or remove it from the post, and queue again.",
            )
            return True

        request = PostRequest(
            group_url=group.url,
            body=target.body,
            media_paths=tuple(Path(p) for p in task.media_paths),
        )

        try:
            outcome = self.poster.post(request)
        except ConnectionFailed as exc:
            # The one failure that is safe to wait out. It is raised by
            # session.attach() before a page even exists, so nothing was typed
            # and nothing can have been published -- the reasoning that makes
            # every other failure terminal does not apply.
            #
            # Halting here threw away a whole scheduled batch whenever Chrome
            # happened to be down: after a Windows Update restart, after a
            # browser crash, or in the minute between the machine logging in
            # and Chrome finishing its start-up. On an unattended machine that
            # is the difference between a batch that goes out late and a batch
            # that never goes out at all.
            self._defer_for_connection(task, target, exc)
            return True
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
        except BaseException as exc:
            # KeyboardInterrupt and SystemExit are not Exception, so they slid
            # past the handler above and killed the worker thread outright --
            # leaving the target stuck in `running`, and the keep-awake request
            # still held so the machine could not sleep. Record and release,
            # then let it carry on unwinding.
            self._halt(task, target, f"Interrupted: {type(exc).__name__}.")
            raise
        finally:
            self._busy = False

        # Chrome answered, so whatever trouble it was having is behind us and
        # the next outage gets the full two hours of patience again.
        self._forget_connection_trouble(task.id)

        if outcome.pending:
            # Treated as posted for every safety purpose: the cooldown starts
            # and the wording is remembered, because it has been submitted and
            # will appear the moment an admin approves. Recording it as failed
            # would leave the guard blind and invite the same text being sent
            # again -- two live posts once both are approved.
            self.tasks.mark_target(
                target.id,
                TARGET_AWAITING_APPROVAL,
                posted=True,
                error=outcome.detail,
            )
            self.groups.mark_posted(group.id)
            self.emit(
                "pending",
                f"{group.display_name} holds posts for admin approval — "
                "submitted, not visible yet.",
                task.id,
                target.id,
            )
        elif not outcome.posted:
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

    def _defer_for_connection(
        self, task: Task, target: TaskTarget, exc: ConnectionFailed
    ) -> None:
        """Put the group back and wait for Chrome, rather than failing it.

        The claim is handed back first, so the target is `pending` again and
        the batch picks up exactly where it was. It is not marked attempted:
        it was not attempted.
        """
        self.tasks.release_target(target.id)
        first = self._connection_trouble.setdefault(task.id, self._now())

        if self._now() - first > CONNECTION_GIVE_UP:
            self._forget_connection_trouble(task.id)
            hours = int(CONNECTION_GIVE_UP.total_seconds() // 3600)
            self._halt(
                task,
                target,
                f"Chrome has not been reachable for {hours} hours, so this batch "
                "stopped waiting for it. Nothing was posted. Start Chrome with "
                "'main.py start' and queue the batch again.",
            )
            return

        # No blocker.release() here on purpose. _sync_power runs after every
        # tick and decides on one rule -- is this batch going to do something
        # within KEEP_AWAKE_HORIZON -- and a five-minute retry is. Releasing
        # here would be undone a moment later, which is worse than not doing
        # it: it reads like a decision when nothing decides it.
        self.tasks.set_resume_at(task.id, self._now() + CONNECTION_RETRY)

        # Announced once, not every five minutes. Repeating the same sentence a
        # hundred times through an overnight outage is how a real warning stops
        # being read.
        if task.id not in self._connection_announced:
            self._connection_announced.add(task.id)
            self.emit(
                "deferred",
                f"Chrome is not reachable ({exc}). Nothing was posted; the batch "
                "waits and tries again shortly.",
                task.id,
            )

    def _forget_connection_trouble(self, task_id: int) -> None:
        """Both halves, always together -- a stale 'already announced' would
        silence the next outage on this batch entirely."""
        self._connection_trouble.pop(task_id, None)
        self._connection_announced.discard(task_id)

    def _halt(self, task: Task, target: TaskTarget, message: str) -> None:
        """Stop the whole batch. Never retry, never continue to the next group."""
        self._forget_connection_trouble(task.id)
        self.tasks.mark_target(target.id, TARGET_FAILED, error=message)
        self.tasks.set_resume_at(task.id, None)
        self.tasks.mark_task(task.id, TASK_HALTED, error=message, finished=True)
        self.blocker.release()
        self.emit("halted", message, task.id, target.id)

    # -- crash recovery ----------------------------------------------------
    def _recovery_waiting_too_long(self) -> bool:
        if self._recovery_trouble is None:
            self._recovery_trouble = self._now()
        return self._now() - self._recovery_trouble > CONNECTION_GIVE_UP

    def _defer_recovery(self, exc: ConnectionFailed) -> None:
        self._recovery_deferred = True
        self._next_recovery_at = self._now() + CONNECTION_RETRY
        if self._recovery_announced:
            return
        self._recovery_announced = True
        self.emit(
            "error",
            "A batch was interrupted and cannot be checked until Chrome is up "
            f"({exc}). Nothing has been given up on.",
        )

    def _escalate_unchecked(self, target: TaskTarget, exc: Exception) -> None:
        """Hand an unresolvable interruption to the user.

        The batch stops rather than guessing. "It probably did not post" is the
        guess that produces a duplicate, and a duplicate is the worst thing
        this app can do.
        """
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
        self.emit(
            "halted",
            "A batch was interrupted and needs checking.",
            target.task_id,
            target.id,
        )

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
            except ConnectionFailed as exc:
                # "Could not look", not "looked and found nothing". Condemning
                # a target here would sentence it on the strength of Chrome
                # being down -- which is precisely the state of a machine in
                # the first seconds after a restart, when this runs. It stays
                # `running` and recovery comes round again; leaving it claimed
                # is the cautious direction, because nothing else will touch it
                # while it sits there.
                #
                # Bounded, though. The task stays `running` while it waits, so
                # the machine is held awake for it -- waiting for ever on a
                # browser that is never coming back would keep a laptop awake
                # for ever too. Past the horizon it escalates exactly as it
                # always did.
                if self._recovery_waiting_too_long():
                    self._escalate_unchecked(target, exc)
                    continue
                self._defer_recovery(exc)
                return
            except Exception as exc:
                self._escalate_unchecked(target, exc)
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

        # A pass that got to the end resolved everything it found, so the next
        # interruption starts with a clean slate -- full patience, and a warning
        # that will be spoken again rather than swallowed as a repeat.
        self._recovery_trouble = None
        self._recovery_announced = False
