"""Background update checks: at login (`tarseer --background`) and, unless the
user chose "at login only", periodically while Tarseer keeps running."""

from __future__ import annotations

import os
import sys
import time
from typing import TYPE_CHECKING, Callable

from gi.repository import GLib

from ..core.manager import AppManager
from ..core.schedule import check_due, check_succeeded
from . import portal
from .settings import app_settings
from .tasks import run_task

if TYPE_CHECKING:
    from ..main import TarseerApplication

# Seconds after login before the first check: let the session (and network) settle.
LOGIN_DELAY = 120
# How often a resident service looks whether a check is due.
TICK = 15 * 60
# "At login only": tries before giving up when a check fails (e.g. offline).
LOGIN_RETRIES = 3
RETRY_DELAY = 5 * 60
# How long a check waits for the keyring before going on without the token.
TOKEN_TIMEOUT = 30


def _log(message: str) -> None:
    print(f"Tarseer: {message}", file=sys.stderr)


def interval_seconds() -> int:
    """Seconds between periodic checks; 0 means "at login only".
    TARSEER_CHECK_INTERVAL=<seconds> overrides the setting (development)."""
    if override := os.environ.get("TARSEER_CHECK_INTERVAL"):
        return max(0, int(override))
    return app_settings().get_uint("check-interval-hours") * 3600


class BackgroundService:
    """Keeps the application alive (hold) while it runs, window or not."""

    def __init__(self, app: TarseerApplication):
        self.app = app
        self.running = False
        self.checking = False
        self._login_only = False
        self._retries_left = 0
        self._timer_id = 0

    # ── Lifecycle ────────────────────────────────────────────────────────────
    def start_at_login(self, delay: int = LOGIN_DELAY) -> bool:
        """`tarseer --background`. False if background checks are off (a stale
        autostart entry): the caller then has nothing to keep running."""
        if not app_settings().get_boolean("background-checks"):
            _log("background checks are off, nothing to do")
            return False
        if self.app.manager is None:
            _log(f"not checking: {self.app.config_error}")
            return False
        interval = interval_seconds()
        self._login_only = interval == 0
        self._retries_left = LOGIN_RETRIES
        self._begin()
        self._schedule(delay, self._run_scheduled)
        _log(f"background checks on, first check in {delay} s"
             + ("" if self._login_only else f", then every {interval} s"))
        return True

    def start_resident(self) -> None:
        """Keep checking periodically from a normal launch (or after the user
        turned background checks on). Does nothing for "at login only"."""
        if self.running or not app_settings().get_boolean("background-checks"):
            return
        if self.app.manager is None:
            return  # the config couldn't be read: nothing to check
        if interval_seconds() == 0:
            return
        self._login_only = False
        self._begin()
        self._schedule_tick()

    def stop(self) -> None:
        """Stop checking and let the application exit once no window is left."""
        if not self.running:
            return
        self.running = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self.app.release()

    def restart(self) -> None:
        """Settings changed: follow them (used by Preferences)."""
        self.stop()
        self.start_resident()

    def _begin(self) -> None:
        self.running = True
        self.app.hold()
        interval = interval_seconds()
        if self._login_only:
            portal.set_status(_("Checking for updates"))
        elif interval % 3600 == 0:
            hours = interval // 3600
            portal.set_status(ngettext("Checks for updates every hour",
                                       "Checks for updates every {n} hours", hours).format(n=hours))
        else:
            portal.set_status(_("Checks for updates periodically"))

    # ── Timers ───────────────────────────────────────────────────────────────
    def _schedule(self, seconds: int, callback: Callable[[], None]) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)

        def fire():
            self._timer_id = 0
            callback()
            return GLib.SOURCE_REMOVE

        self._timer_id = GLib.timeout_add_seconds(max(0, seconds), fire)

    def _schedule_tick(self) -> None:
        self._schedule(min(TICK, max(1, interval_seconds())), self._on_tick)

    def _on_tick(self) -> None:
        interval = interval_seconds()
        if interval == 0 or not app_settings().get_boolean("background-checks"):
            # Changed behind Preferences' back (e.g. with gsettings): follow it.
            _log("periodic checks turned off, stopping")
            self.stop()
            return
        if check_due(app_settings().get_int64("last-check"), interval, time.time()):
            self._run_scheduled()
        else:
            self._schedule_tick()

    def _run_scheduled(self) -> None:
        self.run_check(self._after_scheduled_check)

    def _after_scheduled_check(self, succeeded: bool) -> None:
        if not self.running:
            return
        if not self._login_only:
            self._schedule_tick()  # a failed check is retried at the next tick
        elif succeeded or self._retries_left <= 0:
            _log("login check finished" if succeeded else "login check failed, giving up")
            self.stop()
        else:
            self._retries_left -= 1
            _log(f"retrying in {RETRY_DELAY} s")
            self._schedule(RETRY_DELAY, self._run_scheduled)

    # ── Checking ─────────────────────────────────────────────────────────────
    def run_check(self, done: Callable[[bool], None]) -> None:
        """Check every app unless the window is already busy with apps or the
        quota is low. Errors are only logged: nobody asked for this check.
        done(succeeded) runs on the main loop."""
        manager: AppManager | None = self.app.manager
        if manager is None:
            _log(f"skipped the check: {self.app.config_error}")
            done(False)
            return
        if self.checking or self.app.window_busy():
            _log("skipped the check: Tarseer is busy")
            done(False)
            return
        if not manager.apps:
            done(True)
            return
        self.checking = True

        def on_success(results):
            self.checking = False
            if results is None:
                _log("skipped the check: too few GitHub API requests left")
                done(False)
                return
            for app_id, result in results.items():
                if result.error is not None:
                    _log(f"could not check {app_id}: {result.error}")
            succeeded = check_succeeded(results)
            _log(f"checked {len(results)} apps, {len(manager.updates)} updates")
            self.app.check_finished(results, background=True)
            done(succeeded)

        def on_error(error: Exception):
            # has_quota_for() failed: usually no network yet.
            self.checking = False
            _log(f"check failed: {error}")
            done(False)

        self.app.when_token_ready(
            lambda: run_task(manager.check_if_quota, on_success=on_success, on_error=on_error),
            timeout=TOKEN_TIMEOUT)
