"""Tests for the background-thread to UI-thread bridge."""

from __future__ import annotations

import threading
import tkinter as tk

import pytest

from fbposter.ui.background import BackgroundRunner

from .conftest import pump_until


@pytest.fixture
def root(ui_app):
    """A frame inside the shared interpreter, fresh for each test."""
    frame = tk.Frame(ui_app)
    yield frame
    frame.destroy()


def test_result_is_delivered_to_the_callback(root):
    received = []
    runner = BackgroundRunner(root, poll_ms=10)
    runner.submit(lambda: 6 * 7, on_success=received.append)

    assert pump_until(root, lambda: received), "callback never fired"
    assert received == [42]
    runner.stop()


def test_callback_runs_on_the_main_thread(root):
    """The whole point of the bridge: callbacks touch widgets, so they must not
    run on the worker thread."""
    threads = []
    runner = BackgroundRunner(root, poll_ms=10)
    runner.submit(
        lambda: threading.current_thread().name,
        on_success=lambda _r: threads.append(threading.current_thread()),
    )

    assert pump_until(root, lambda: threads)
    assert threads[0] is threading.main_thread()
    runner.stop()


def test_worker_thread_is_not_the_main_thread(root):
    """...and the blocking work itself must not be, or the window would freeze."""
    names = []
    runner = BackgroundRunner(root, poll_ms=10)
    runner.submit(lambda: threading.current_thread(), on_success=names.append)

    assert pump_until(root, lambda: names)
    assert names[0] is not threading.main_thread()
    runner.stop()


def test_exception_is_routed_to_on_error(root):
    errors = []
    successes = []

    def boom():
        raise ValueError("kaboom")

    runner = BackgroundRunner(root, poll_ms=10)
    runner.submit(boom, on_success=successes.append, on_error=errors.append)

    assert pump_until(root, lambda: errors), "error callback never fired"
    assert isinstance(errors[0], ValueError)
    assert str(errors[0]) == "kaboom"
    assert successes == []
    runner.stop()


def test_pending_count_returns_to_zero(root):
    done = []
    runner = BackgroundRunner(root, poll_ms=10)
    runner.submit(lambda: None, on_success=done.append)
    assert runner.pending == 1

    assert pump_until(root, lambda: done)
    assert runner.pending == 0
    runner.stop()


def test_a_failing_callback_does_not_kill_the_pump(root):
    """One bad handler must not stop every later result from arriving."""
    second = []

    def bad(_result):
        raise RuntimeError("callback blew up")

    runner = BackgroundRunner(root, poll_ms=10)
    runner.submit(lambda: "first", on_success=bad)
    assert pump_until(root, lambda: runner.pending == 0)

    runner.submit(lambda: "second", on_success=second.append)
    assert pump_until(root, lambda: second), "pump stopped after a bad callback"
    assert second == ["second"]
    runner.stop()


def test_stop_is_safe_to_call_twice(root):
    runner = BackgroundRunner(root, poll_ms=10)
    runner.stop()
    runner.stop()
