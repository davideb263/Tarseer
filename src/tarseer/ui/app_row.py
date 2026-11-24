from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..core.github import CancelledError, RateLimitError
from ..core.manager import AppManager
from ..core.models import ManagedApp, Release
from .tasks import ThrottledProgress, run_task


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/app_row.ui")
class AppRow(Adw.ActionRow):
    """One managed app: version info plus check/update state."""

    __gtype_name__ = "TarseerAppRow"
    __gsignals__ = {
        # Human-readable message for the window to show as a toast.
        "notify-user": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        # (heading, body): an error to show in full in an alert.
        "show-error": (GObject.SignalFlags.RUN_FIRST, None, (str, str)),
        # list[str] of app grid entry problems the user has to fix.
        "launcher-warnings": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # The row was redrawn; `has_update` may have changed.
        "refreshed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    icon: Gtk.Image = Gtk.Template.Child()
    state_stack: Gtk.Stack = Gtk.Template.Child()
    progress_bar: Gtk.ProgressBar = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    error_icon: Gtk.Image = Gtk.Template.Child()

    def __init__(self, manager: AppManager, app: ManagedApp):
        super().__init__()
        self.manager = manager
        self.app = app
        self.release: Release | None = None
        self.busy = False
        self.queued = False  # waiting for its turn in "Update All"
        self._error: str | None = None
        # Results of the last update(quiet=True), for the caller to report.
        self.last_error: str | None = None
        self.last_warnings: list[str] = []
        self.last_cancelled = False
        self._cancel: threading.Event | None = None  # set while an update downloads
        self.refresh()

    # ── Display ──────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.set_title(self.app.name)
        installed = self.app.installed_tag or _("Unknown version")
        if self.release and self.manager.update_available(self.app, self.release):
            self.set_subtitle(f"{installed} → {self.release.tag}")
            self.state_stack.set_visible_child_name("queued" if self.queued else "update")
        elif self._error:
            self.set_subtitle(installed)
            self.error_icon.set_tooltip_text(self._error)
            self.state_stack.set_visible_child_name("error")
        else:
            self.set_subtitle(installed)
            self.state_stack.set_visible_child_name("current" if self.release else "none")
        self._load_icon()
        self.emit("refreshed")

    def set_queued(self, queued: bool) -> None:
        self.queued = queued
        if not self.busy:
            self.refresh()

    def _load_icon(self) -> None:
        if self.app.icon:
            path = self.manager.install_dir(self.app) / self.app.icon
            if Path(path).is_file():
                self.icon.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(path))))
                return
        self.icon.set_from_icon_name("application-x-executable")

    def set_release(self, release: Release) -> None:
        """Replace the last check result (e.g. after the repository changed)."""
        self.release = release
        self._error = None

    @property
    def has_update(self) -> bool:
        return bool(self.release and self.manager.update_available(self.app, self.release))

    # ── Check ────────────────────────────────────────────────────────────────
    def check(self, done: Callable[["AppRow"], None] | None = None) -> None:
        if self.busy:
            if done:
                done(self)
            return
        self.busy = True
        self._error = None
        self.state_stack.set_visible_child_name("checking")

        def finish():
            self.busy = False
            self.refresh()
            if done:
                done(self)

        def on_success(release: Release):
            self.release = release
            finish()

        def on_error(error: Exception):
            self._error = str(error)
            if isinstance(error, RateLimitError):
                message = str(error)  # same for every app, so the window shows it once
            else:
                message = _("{app}: {error}").format(app=self.app.name, error=error)
            self.emit("notify-user", message)
            finish()

        run_task(self.manager.fetch_latest, self.app.repo, on_success=on_success, on_error=on_error)

    # ── Update ───────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_update_clicked(self, *_args) -> None:
        self.update()

    def update(self, done: Callable[["AppRow"], None] | None = None, quiet: bool = False) -> bool:
        """Update to the last checked release. `done(row)` is called when finished.

        With `quiet`, success and failure aren't reported to the user; the caller
        reads `last_error` and `last_warnings` instead. Returns False (and doesn't
        call `done`) if there is nothing to do or the row is busy.
        """
        self.queued = False
        if self.busy:
            return False  # its own operation redraws the row when it finishes
        if not self.has_update:
            self.refresh()
            return False
        self.busy = True
        self._error = None
        self.last_error, self.last_warnings, self.last_cancelled = None, [], False
        release = self.release
        cancel = self._cancel = threading.Event()
        self.cancel_button.set_visible(True)
        self.cancel_button.set_sensitive(True)
        self.progress_bar.set_fraction(0)
        self.state_stack.set_visible_child_name("progress")
        self.set_subtitle(_("Downloading…"))

        def on_progress(done_bytes: int, total: int):
            if cancel.is_set():
                return  # keep showing "Cancelling…"
            if total:
                self.progress_bar.set_fraction(done_bytes / total)
                self.set_subtitle(_("Downloading {done} of {total}").format(
                    done=GLib.format_size(done_bytes), total=GLib.format_size(total)))
                if done_bytes >= total:
                    self.set_subtitle(_("Installing…"))
                    self.cancel_button.set_visible(False)  # extraction can't be cancelled
            else:
                self.progress_bar.pulse()

        def finish():
            self.busy = False
            self._cancel = None
            self.refresh()
            if done:
                done(self)

        def on_success(warnings: list[str]):
            self.last_warnings = warnings
            if not quiet:
                self.emit("notify-user", _("{app} updated to {tag}").format(app=self.app.name, tag=release.tag))
                if warnings:
                    self.emit("launcher-warnings", warnings)
            finish()

        def on_error(error: Exception):
            if isinstance(error, CancelledError):
                self.last_cancelled = True
                if not quiet:
                    self.emit("notify-user", _("{app} update cancelled").format(app=self.app.name))
                finish()
                return
            self._error = self.last_error = str(error)
            if not quiet:
                self.emit("show-error", _("Could Not Update {app}").format(app=self.app.name), str(error))
            finish()

        run_task(self.manager.install, self.app, release, ThrottledProgress(on_progress), cancel,
                 on_success=on_success, on_error=on_error)
        return True

    @Gtk.Template.Callback()
    def on_cancel_clicked(self, *_args) -> None:
        self.cancel()

    def cancel(self) -> None:
        """Stop the running download, if any; the update then ends as cancelled."""
        if self._cancel is not None:
            self._cancel.set()
            self.cancel_button.set_sensitive(False)
            self.set_subtitle(_("Cancelling…"))
