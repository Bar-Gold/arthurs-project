"""The log file (`fbposter/applog.py`), added 2026-11-12 so a client's app can
say why its posts went where they did.

It must never fill the client's disk, never stop the app, and never be
written by the suite.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from fbposter import applog, strings
from fbposter.automation.poster import Companion, GroupPoster

from .test_poster import quiet_humanizer
from .test_together import GROUP, WORDS, Page
from .test_worker import db, make_worker  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def log_folder(tmp_path):
    yield tmp_path / "logs"
    applog.stop()


def lines_in(folder: Path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(folder.iterdir()))


class TestTheFile:
    def test_starts_beside_the_given_folder_and_says_so(self, log_folder):
        path = applog.start(log_folder)
        applog.stop()
        assert path == log_folder / applog.LOG_FILENAME
        assert "App started" in path.read_text(encoding="utf-8")

    def test_starting_twice_opens_one_file(self, log_folder):
        first = applog.start(log_folder)
        second = applog.start(log_folder / "elsewhere")
        assert first == second
        handlers = logging.getLogger(applog.LOGGER_NAME).handlers
        assert sum(isinstance(h, logging.FileHandler) for h in handlers) == 1

    def test_hebrew_is_written_as_it_is(self, log_folder):
        applog.start(log_folder)
        logging.getLogger("fbposter.worker").info("דירת 4 חדרים")
        applog.stop()
        assert "דירת 4 חדרים" in lines_in(log_folder)

    def test_a_folder_that_cannot_be_made_is_no_log_not_a_crash(self, tmp_path):
        blocker = tmp_path / "logs"
        blocker.write_text("a file where the folder should be")
        assert applog.start(blocker) is None
        assert applog._handler is None

    def test_stop_puts_the_error_hooks_back(self, log_folder):
        import sys
        import threading

        before = (sys.excepthook, threading.excepthook)
        applog.start(log_folder)
        assert sys.excepthook is not before[0]
        applog.stop()
        assert (sys.excepthook, threading.excepthook) == before


class TestItCannotFillTheDisk:
    def test_the_cap_is_under_ten_megabytes(self):
        assert applog.MAX_BYTES * (applog.BACKUPS + 1) <= 10_000_000

    def test_old_files_are_dropped(self, log_folder, monkeypatch):
        monkeypatch.setattr(applog, "MAX_BYTES", 2_000)
        applog.start(log_folder)
        logger = logging.getLogger("fbposter.worker")
        for index in range(2_000):
            logger.info("line %d %s", index, "x" * 80)
        applog.stop()

        files = list(log_folder.iterdir())
        assert len(files) == applog.BACKUPS + 1
        # One line over the size at most: a file turns over before the line
        # that would take it past.
        assert all(f.stat().st_size <= 2_000 for f in files)
        # The newest lines are the ones kept.
        assert "line 1999" in (log_folder / applog.LOG_FILENAME).read_text(encoding="utf-8")


class TestWhatGoesIn:
    def test_every_worker_event(self, db, log_folder):
        applog.start(log_folder)
        make_worker(db).emit("posted", "Posted to bar-test.", 7, 21)
        applog.stop()
        assert re.search(r"\[posted\] batch 7, group turn 21: Posted to bar-test\.", lines_in(log_folder))

    def test_what_a_ticked_group_check_saw(self, log_folder):
        applog.start(log_folder)
        page = Page({strings.my_posted_url(GROUP): "No posts yet"})
        GroupPoster(page, humanizer=quiet_humanizer()).check_companion(
            Companion(GROUP, "bar-test"), WORDS
        )
        applog.stop()
        text = lines_in(log_folder)
        assert "Check bar-test: not on " + strings.my_posted_url(GROUP) in text
        assert "No posts yet" in text


class TestOnlyTheRealAppWritesIt:
    def test_only_run_starts_it(self):
        callers = [
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "fbposter").rglob("*.py")
            if any(
                line.strip().startswith("applog.start(")
                for line in path.read_text(encoding="utf-8").splitlines()
            )
        ]
        assert callers == ["fbposter/qtui/app.py"]
        source = (ROOT / "fbposter" / "qtui" / "app.py").read_text(encoding="utf-8")
        run = source[source.index("def run() -> int:"):]
        assert "applog.start()" in run

    def test_nothing_is_printed_without_it(self, capsys):
        logging.getLogger("fbposter.worker").warning("no file attached")
        captured = capsys.readouterr()
        assert "no file attached" not in captured.err + captured.out


class TestTheSupportCallsQuestions:
    """The lines that answer "why did it do that?" (2026-11-13)."""

    def test_a_groups_read_says_what_it_left_out_and_whether_it_was_complete(self, caplog):
        from fbposter.automation.groupfinder import read_your_groups

        from .test_group_import import PENDING_HEADING, FakeListPage, card, instant, requested

        page = FakeListPage(
            [requested("980414154465411", "try-group") + card("1", "A") + card("2", "B")],
            headings=[PENDING_HEADING, "All groups you've joined (2)"],
        )
        with caplog.at_level(logging.INFO, logger="fbposter"):
            read_your_groups(page, instant())
        text = caplog.text
        assert "read 2 of 2" in text
        assert "Complete" in text
        assert "Left out as requests to join (1): try-group" in text

    def test_a_short_read_says_none_are_taken_off(self, caplog):
        from fbposter.automation.groupfinder import read_your_groups

        from .test_group_import import FakeListPage, card, instant

        page = FakeListPage([card("1", "A")], heading="All groups you've joined (5)")
        with caplog.at_level(logging.INFO, logger="fbposter"):
            read_your_groups(page, instant())
        assert "Not complete, so only new groups are added and none taken off" in caplog.text

    def test_an_error_event_carries_its_traceback(self, db, caplog):
        worker = make_worker(db)
        with caplog.at_level(logging.INFO, logger="fbposter"):
            try:
                raise RuntimeError("the disk is full")
            except RuntimeError:
                worker.emit("error", "History cleanup skipped: the disk is full")
        (record,) = [r for r in caplog.records if "History cleanup" in r.getMessage()]
        assert record.exc_info is not None

    def test_what_the_user_was_shown(self, qt_app, caplog):
        with caplog.at_level(logging.INFO, logger="fbposter"):
            qt_app.toast("Posting paused.", "warning")
        assert "Shown to the user (warning): Posting paused." in caplog.text
