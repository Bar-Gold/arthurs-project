"""Tests for sleep inhibition.

The Windows call is faked, so this runs anywhere and asserts the flags rather
than the effect.
"""

from __future__ import annotations

from fbposter.power import ES_CONTINUOUS, ES_SYSTEM_REQUIRED, KEEP_AWAKE, SleepBlocker


class Recorder:
    def __init__(self) -> None:
        self.flags: list[int] = []

    def __call__(self, flags: int) -> int:
        self.flags.append(flags)
        return 1


def make() -> tuple[SleepBlocker, Recorder]:
    recorder = Recorder()
    return SleepBlocker(setter=recorder), recorder


class TestFlags:
    def test_acquiring_asks_to_stay_awake(self):
        blocker, recorder = make()
        blocker.acquire()
        assert recorder.flags == [KEEP_AWAKE]

    def test_the_display_is_never_held_on(self):
        """Only system sleep is blocked; a screen lit for three hours would be
        its own kind of interference."""
        from fbposter import power

        assert not hasattr(power, "ES_DISPLAY_REQUIRED")
        assert KEEP_AWAKE == ES_CONTINUOUS | ES_SYSTEM_REQUIRED

    def test_releasing_restores_normal_behaviour(self):
        blocker, recorder = make()
        blocker.acquire()
        blocker.release()
        assert recorder.flags == [KEEP_AWAKE, ES_CONTINUOUS]


class TestIdempotence:
    def test_acquiring_twice_only_asks_once(self):
        blocker, recorder = make()
        blocker.acquire()
        blocker.acquire()
        assert len(recorder.flags) == 1

    def test_releasing_without_acquiring_does_nothing(self):
        blocker, recorder = make()
        blocker.release()
        assert recorder.flags == []

    def test_releasing_twice_only_asks_once(self):
        blocker, recorder = make()
        blocker.acquire()
        blocker.release()
        blocker.release()
        assert recorder.flags == [KEEP_AWAKE, ES_CONTINUOUS]

    def test_it_reports_whether_it_is_holding(self):
        blocker, _ = make()
        assert not blocker.held
        blocker.acquire()
        assert blocker.held
        blocker.release()
        assert not blocker.held


class TestContextManager:
    def test_it_releases_on_the_way_out(self):
        blocker, recorder = make()
        with blocker:
            assert blocker.held
        assert not blocker.held
        assert recorder.flags == [KEEP_AWAKE, ES_CONTINUOUS]

    def test_it_releases_even_when_the_body_raises(self):
        blocker, recorder = make()
        try:
            with blocker:
                raise RuntimeError("batch blew up")
        except RuntimeError:
            pass
        assert not blocker.held
        assert recorder.flags[-1] == ES_CONTINUOUS


class TestUnavailablePlatform:
    def test_it_degrades_quietly_where_the_call_does_not_exist(self):
        """Missing the API must not stop a batch running."""
        blocker = SleepBlocker(setter=None)
        blocker._setter = None
        assert not blocker.available
        blocker.acquire()
        assert not blocker.held
        blocker.release()
