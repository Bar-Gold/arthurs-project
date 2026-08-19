"""Window-level behaviour: what closing the window does.

Closing it stops the posting worker, because the worker *is* this window's
thread. That was silent -- a daily repeat set up and then closed away simply
never ran again, and nothing on screen said so. The warning is the one dialog
the app raises of its own accord, so it is injected exactly like check_fn and
group_namer: a suite that popped a real modal would hang rather than fail.
"""

from __future__ import annotations

import pytest

from fbposter.db import Database
from fbposter.db.models import SCHEDULE_PAUSED, TASK_DONE
from fbposter.db.repo import GroupRepo, ScheduleRepo, TaskRepo
from fbposter.ui.connection import ConnectionResult, ConnectionState

from .conftest import SilentNamer


class StubWorker:
    """Enough of a worker for closeEvent -- and for the sidebar's worker row.

    The window keeps a QTimer pumping worker events, and that row reads
    `state` and `paused` off whatever is attached. A stub without them raises
    inside a Qt slot, which prints a traceback and carries on rather than
    failing anything, so it surfaces as noise in some unrelated test.
    """

    state = "idle"
    paused = False

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class Window:
    """An App with a worker attached and the close dialog answered for it."""

    def __init__(self, tmp_path, answer=True, running=True):
        from fbposter.qtui.app import App

        self.asked: list[str] = []
        self.answer = answer
        self.db = Database(tmp_path / "close.db")
        self.app = App(
            check_fn=lambda: ConnectionResult(ConnectionState.UNKNOWN, ""),
            db=self.db,
            group_namer=SilentNamer(),
            confirm_close=self._confirm,
        )
        self.worker = StubWorker()
        if running:
            self.app.worker = self.worker

    def _confirm(self, _parent, waiting: str) -> bool:
        self.asked.append(waiting)
        return self.answer

    def group(self):
        """One group, reused: these tests are about the close warning, and a
        second batch to the same group is as good as a second group."""
        repo = GroupRepo(self.db)
        existing = repo.list()
        if existing:
            return existing[0]
        return repo.add_from_url("https://www.facebook.com/groups/one")

    def batch(self):
        return TaskRepo(self.db).create("body", [(self.group().id, "body")])

    def repeat(self):
        return ScheduleRepo(self.db).create(
            name="Bikes",
            bodies=["one", "two"],
            group_ids=[self.group().id],
            times=["09:00"],
        )


@pytest.fixture
def window(qt_application, tmp_path):
    made = []

    def build(**kwargs):
        win = Window(tmp_path, **kwargs)
        made.append(win)
        return win

    yield build
    for win in made:
        # A window that refused to close is the point of one of these tests,
        # and it would otherwise outlive the test with its event timer still
        # running. Nothing is left waiting on it here.
        win.answer = True
        win.app.worker = None
        win.app.close()


class TestClosingWithNothingWaiting:
    def test_it_does_not_ask(self, window):
        win = window()
        assert win.app.close() is True
        assert win.asked == []
        assert win.worker.stopped

    def test_a_finished_batch_is_not_something_waiting(self, window):
        win = window()
        tasks = TaskRepo(win.db)
        tasks.mark_task(win.batch().id, TASK_DONE)
        assert win.app.close() is True
        assert win.asked == []

    def test_nor_is_a_paused_repeat(self, window):
        win = window()
        schedules = ScheduleRepo(win.db)
        schedules.set_state(win.repeat().id, SCHEDULE_PAUSED)
        assert win.app.close() is True
        assert win.asked == []


class TestClosingWithPostingStillDue:
    def test_a_queued_batch_is_worth_asking_about(self, window):
        win = window()
        win.batch()
        win.app.close()
        assert len(win.asked) == 1
        assert "1 batch" in win.asked[0]

    def test_an_active_repeat_is_too(self, window):
        win = window()
        win.repeat()
        win.app.close()
        assert len(win.asked) == 1
        assert "1 repeating post" in win.asked[0]

    def test_both_are_named(self, window):
        win = window()
        win.batch()
        win.batch()
        win.repeat()
        win.app.close()
        assert "2 batches" in win.asked[0]
        assert "1 repeating post" in win.asked[0]

    def test_saying_no_leaves_the_window_open_and_the_worker_running(self, window):
        win = window(answer=False)
        win.batch()

        assert win.app.close() is False, "closed anyway"
        assert not win.worker.stopped, "stopped the scheduler it just promised to keep"
        # The database must not have been closed either, or the window is alive
        # but unusable.
        assert TaskRepo(win.db).unfinished_count() == 1

    def test_saying_yes_closes(self, window):
        win = window(answer=True)
        win.batch()
        assert win.app.close() is True
        assert win.worker.stopped


class TestAWindowThatNeverStartedTheWorker:
    """Constructing an App does not start the worker, and the whole GUI suite
    rests on that -- every one of its windows is closed at teardown, so a
    dialog here would hang the run rather than fail it."""

    def test_it_closes_without_asking(self, window):
        win = window(running=False)
        win.batch()
        win.repeat()
        assert win.app.close() is True
        assert win.asked == []
