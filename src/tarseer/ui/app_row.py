from __future__ import annotations

from pathlib import Path
from typing import Callable

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..core.github import RateLimitError
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
        # list[str] of app grid entry problems the user has to fix.
        "launcher-warnings": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    icon: Gtk.Image = Gtk.Template.Child()
    state_stack: Gtk.Stack = Gtk.Template.Child()
    progress_bar: Gtk.ProgressBar = Gtk.Template.Child()
    error_icon: Gtk.Image = Gtk.Template.Child()

    def __init__(self, manager: AppManager, app: ManagedApp):
        super().__init__()
        self.manager = manager
        self.app = app
        self.release: Release | None = None
        self.busy = False
        self._error: str | None = None
        self.refresh()

    # ── Display ──────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        self.set_title(self.app.name)
        installed = self.app.installed_tag or _("Unknown version")
        if self.release and self.manager.update_available(self.app, self.release):
            self.set_subtitle(f"{installed} → {self.release.tag}")
            self.state_stack.set_visible_child_name("update")
        elif self._error:
            self.set_subtitle(installed)
            self.error_icon.set_tooltip_text(self._error)
            self.state_stack.set_visible_child_name("error")
        else:
            self.set_subtitle(installed)
            self.state_stack.set_visible_child_name("current" if self.release else "none")
        self._load_icon()

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

    def update(self, done: Callable[["AppRow"], None] | None = None) -> None:
        if self.busy or not self.has_update:
            return
        self.busy = True
        self._error = None
        release = self.release
        self.progress_bar.set_fraction(0)
        self.state_stack.set_visible_child_name("progress")
        self.set_subtitle(_("Downloading…"))

        def on_progress(done_bytes: int, total: int):
            if total:
                self.progress_bar.set_fraction(done_bytes / total)
                self.set_subtitle(_("Downloading {done} of {total}").format(
                    done=GLib.format_size(done_bytes), total=GLib.format_size(total)))
                if done_bytes >= total:
                    self.set_subtitle(_("Installing…"))
            else:
                self.progress_bar.pulse()

        def finish():
            self.busy = False
            self.refresh()
            if done:
                done(self)

        def on_success(warnings: list[str]):
            self.emit("notify-user", _("{app} updated to {tag}").format(app=self.app.name, tag=release.tag))
            if warnings:
                self.emit("launcher-warnings", warnings)
            finish()

        def on_error(error: Exception):
            self._error = str(error)
            self.emit("notify-user", _("Could not update {app}: {error}").format(app=self.app.name, error=error))
            finish()

        run_task(self.manager.install, self.app, release, ThrottledProgress(on_progress),
                 on_success=on_success, on_error=on_error)
