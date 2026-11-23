"""Run blocking work off the GTK main loop and deliver results back onto it."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from gi.repository import GLib


def _call(fn: Callable, *args) -> bool:
    fn(*args)
    return GLib.SOURCE_REMOVE


def run_task(
    func: Callable[..., Any],
    *args,
    on_success: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
) -> threading.Thread:
    """Call func(*args) in a daemon thread; callbacks run on the main loop."""

    def worker():
        try:
            result = func(*args)
        except Exception as e:  # noqa: BLE001 - every failure is reported to the UI
            if on_error:
                GLib.idle_add(_call, on_error, e)
            return
        if on_success:
            GLib.idle_add(_call, on_success, result)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    return thread


class ThrottledProgress:
    """A progress(done, total) callback, safe to call from any thread, that
    forwards to `callback` on the main loop at most every `interval` seconds."""

    def __init__(self, callback: Callable[[int, int], None], interval: float = 0.1):
        self._callback = callback
        self._interval = interval
        self._last = 0.0

    def __call__(self, done: int, total: int) -> None:
        now = time.monotonic()
        if now - self._last >= self._interval or (total and done >= total):
            self._last = now
            GLib.idle_add(_call, self._callback, done, total)
