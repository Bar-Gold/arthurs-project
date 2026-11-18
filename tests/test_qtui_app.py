"""Window-level behaviour: what closing the window does.

Closing it stops the posting worker, because the worker *is* this window's
thread. That was silent -- a daily repeat set up and then closed away simply
never ran again, and nothing on screen said so. The warning is the one dialog
the app raises of its own accord, so it is injected exactly like check_fn and
group_namer: a suite that popped a real modal would hang rather than fail.
"""

from __future__ import annotations

import pytest

from fbposter.db.models import SCHEDULE_PAUSED, TASK_DONE
from fbposter.db.repo import GroupRepo, ScheduleRepo, TaskRepo
from fbposter.connection import ConnectionResult, ConnectionState

from .conftest import SilentNamer, discard, fresh_database


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
        self.db = fresh_database(tmp_path / "close.db")
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
        discard(win.app)


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


class TestChromeClosesWithTheApp:
    """The user's choice (2026-10-14): the background Chrome closes when the
    window does, after the scheduler and the Chrome watcher have stopped."""

    def test_closing_the_window_closes_chrome(self, window):
        win = window()
        order = []
        win.app._close_chrome = lambda: order.append("chrome")
        win.worker.stop = lambda: order.append("worker")
        assert win.app.close() is True
        assert order == ["worker", "chrome"]
        assert not win.app._chrome_watch.isActive()

    def test_staying_open_keeps_chrome(self, window):
        win = window(answer=False)
        win.batch()
        closed = []
        win.app._close_chrome = lambda: closed.append(1)
        assert win.app.close() is False
        assert closed == []

    def test_a_failure_to_close_chrome_does_not_stop_the_window_closing(self, window):
        win = window()

        def broken():
            raise RuntimeError("Chrome did not answer")

        win.app._close_chrome = broken
        assert win.app.close() is True

    def test_a_window_the_suite_builds_never_closes_a_real_chrome(self, window):
        from fbposter import tabs

        win = window()
        assert win.app._close_chrome() == tabs.GONE


class TestAMacClosesToTheDock:
    """The user's choice (2026-11-18): on a Mac the red button hides the
    window and posting goes on, as in Mail; a quit (the app menu, the Dock,
    Cmd+Q) is what stops it, and asks first as closing does on Windows."""

    @pytest.fixture
    def mac(self, window):
        built = []

        def build(**kwargs):
            win = window(**kwargs)
            win.app.hide_on_close = True
            built.append(win)
            return win

        yield build
        # So the window fixture's own close really closes it.
        for win in built:
            win.app.hide_on_close = False

    @staticmethod
    def quit_event():
        from PySide6.QtCore import QEvent

        return QEvent(QEvent.Quit)

    def test_the_red_button_hides_and_nothing_stops(self, mac):
        win = mac()
        win.batch()
        closed = []
        win.app._close_chrome = lambda: closed.append(1)
        assert win.app.close() is False, "the window closed instead of hiding"
        assert not win.app.isVisible()
        assert win.asked == [], "hiding stops nothing, so there is nothing to ask"
        assert not win.worker.stopped
        assert closed == [], "Chrome closed with the posting still going on"
        assert TaskRepo(win.db).unfinished_count() == 1, "the database was closed"

    def test_a_click_on_the_dock_icon_brings_it_back(self, mac):
        from PySide6.QtCore import Qt

        win = mac()
        win.app.close()
        win.app.on_application_state(Qt.ApplicationActive)
        assert win.app.isVisible()

    def test_turning_active_shows_nothing_the_red_button_did_not_hide(self, mac):
        from PySide6.QtCore import Qt

        win = mac()
        win.app.hide()
        win.app.on_application_state(Qt.ApplicationActive)
        assert not win.app.isVisible()

    def test_quitting_stops_the_posting_and_closes_chrome(self, mac):
        win = mac()
        order = []
        win.app._close_chrome = lambda: order.append("chrome")
        win.worker.stop = lambda: order.append("worker")
        event = self.quit_event()
        assert win.app.eventFilter(win.app, event) is False, "Qt was not left to quit"
        assert order == ["worker", "chrome"]

    def test_quitting_with_posts_waiting_asks_and_no_calls_it_off(self, mac):
        win = mac(answer=False)
        win.batch()
        win.app.close()  # to the Dock first, as it usually is
        event = self.quit_event()
        assert win.app.eventFilter(win.app, event) is True
        assert not event.isAccepted(), "an accepted Quit tells macOS to go on quitting"
        assert len(win.asked) == 1
        assert win.app.isVisible(), "the question needs its window"
        assert not win.worker.stopped
        # And the next red button hides again, rather than quitting.
        assert win.app._quitting is False

    def test_windows_still_closes_on_the_red_button(self, window):
        win = window()
        assert win.app.hide_on_close is False
        assert win.app.close() is True
        assert win.worker.stopped

    def test_the_question_says_quit_on_a_mac(self):
        from fbposter.qtui.app import close_question

        mac = close_question("1 batch still to go out", mac=True)
        assert mac["go"] == "Quit anyway" and "quits" in mac["title"]
        assert "1 batch still to go out" in mac["detail"]
        windows = close_question("1 batch still to go out", mac=False)
        assert windows["go"] == "Close anyway"


class TestPostsToLookAtAreWarnings:
    """A post that might have gone out, and a post Facebook gave no id, are
    each something to look at, so neither may read as "that went fine"."""

    @pytest.mark.parametrize("kind", ["unconfirmed", "no_post_id"])
    def test_they_show_as_warnings(self, qt_app, monkeypatch, kind):
        from fbposter.worker import WorkerEvent

        shown = []
        monkeypatch.setattr(
            qt_app, "toast", lambda message, level="info", undo=None: shown.append(level)
        )
        qt_app._handle_worker_event(WorkerEvent(kind, "Look at the group."))
        assert shown == ["warning"]
