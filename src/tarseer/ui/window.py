from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from ..core.github import RateLimitError, TokenRejectedError
from ..core.manager import AppManager
from ..core.models import CheckResult, ManagedApp, Release
from .add_dialog import AddDialog
from .alerts import show_error, show_launcher_warnings
from .details_dialog import DetailsDialog
from .preferences_dialog import PreferencesDialog
from .settings import app_settings
from .app_row import AppRow
from .tasks import on_main_loop, run_task


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/window.ui")
class TarseerWindow(Adw.ApplicationWindow):
    __gtype_name__ = "TarseerWindow"

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    main_stack: Gtk.Stack = Gtk.Template.Child()
    list_box: Gtk.ListBox = Gtk.Template.Child()
    error_page: Adw.StatusPage = Gtk.Template.Child()
    update_all_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Kept: once destroyed, get_application() returns None, but a check or
        # update still running reports to this window (the app runs on when
        # background checks keep it alive) and must reach the application.
        self._app = self.get_application()
        self._destroyed = False
        self.connect("destroy", self._on_destroy)

        # Remember the window size between launches.
        self.settings = app_settings()
        self.settings.bind("window-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)

        self.check_action = Gio.SimpleAction.new("check-updates", None)
        self.check_action.connect("activate", lambda *_: self.check_updates())
        self.add_action(self.check_action)
        self._app.set_accels_for_action("win.check-updates", ["<primary>r", "F5"])

        self.add_app_action = Gio.SimpleAction.new("add-app", None)
        self.add_app_action.connect("activate", lambda *_: self.open_add_dialog())
        self.add_action(self.add_app_action)
        self._app.set_accels_for_action("win.add-app", ["<primary>n"])

        self.update_all_action = Gio.SimpleAction.new("update-all", None)
        self.update_all_action.connect("activate", lambda *_: self.update_all())
        self.add_action(self.update_all_action)
        self.stop_updates_action = Gio.SimpleAction.new("stop-updates", None)
        self.stop_updates_action.connect("activate", lambda *_: self.stop_updates())
        self.add_action(self.stop_updates_action)

        self.preferences_action = Gio.SimpleAction.new("preferences", None)
        self.preferences_action.connect("activate", lambda *_: self.open_preferences())
        self.add_action(self.preferences_action)
        self._app.set_accels_for_action("win.preferences", ["<primary>comma"])

        self.rows: list[AppRow] = []
        self.rows_by_id: dict[str, AppRow] = {}
        self._checking = False
        self._check_errors = 0
        self._shown_messages: set[str] = set()
        # "Update All" run: rows still waiting, and the rows it handled so far.
        self._update_queue: list[AppRow] | None = None
        self._update_done: list[AppRow] = []
        self._update_current: AppRow | None = None
        self._update_stopped = False
        self._check_after_token = False
        self._preferences: PreferencesDialog | None = None

        app = self._app
        self.manager: AppManager | None = app.manager
        if self.manager is None:
            # The config couldn't be read (the app keeps no manager: saving would overwrite it).
            self.error_page.set_description(str(app.config_error))
            self.main_stack.set_visible_child_name("error")
            self.check_action.set_enabled(False)
            self.add_app_action.set_enabled(False)
            self.update_all_action.set_enabled(False)
            self.preferences_action.set_enabled(False)
            return
        # The application outlives this window when it keeps running in the background.
        handler = app.connect("token-loaded", self._on_token_loaded)
        self.connect("destroy", lambda *_: app.disconnect(handler))
        self.populate()
        if self.manager.startup_warnings:
            # Wait until the window is on screen so the alert has a parent to attach to.
            self.connect("map", self._show_startup_warnings)

    def _show_startup_warnings(self, *_args) -> None:
        # Once per run, not again in every window reopened while running in the background.
        warnings, self.manager.startup_warnings = self.manager.startup_warnings, []
        show_launcher_warnings(self, warnings)

    def _show_launcher_warnings(self, warnings: list[str]) -> None:
        if not self._destroyed:
            show_launcher_warnings(self, warnings)

    def _on_destroy(self, *_args) -> None:
        self._destroyed = True

    def _on_token_loaded(self, _app) -> None:
        if self._preferences is not None:
            self._preferences.token_loaded()
        if self._check_after_token:
            self._check_after_token = False
            self.check_updates()

    # ── Preferences ──────────────────────────────────────────────────────────
    def open_preferences(self) -> None:
        app = self._app
        dialog = self._preferences = PreferencesDialog(self.manager, token_loading=app.token_loading)
        dialog.connect("token-changed", lambda *_: setattr(app, "token_set_by_user", True))
        dialog.connect("closed", self._on_preferences_closed)
        dialog.present(self)

    def _on_preferences_closed(self, dialog: PreferencesDialog) -> None:
        if self._preferences is dialog:
            self._preferences = None

    def populate(self) -> None:
        """Sync the list with the manager, keeping existing rows (and their check state)."""
        for row in self.rows:
            self.list_box.remove(row)
        previous, self.rows_by_id = self.rows_by_id, {}
        for app in self.manager.apps:
            row = previous.get(app.id)
            if row is None:
                row = AppRow(self.manager, app)
                row.connect("notify-user", lambda _row, msg: self.toast(msg))
                row.connect("show-error", lambda _row, heading, body: self._show_row_error(heading, body))
                row.connect("launcher-warnings", lambda _row, w: self._show_launcher_warnings(w))
                row.connect("refreshed", lambda _row: self._sync_actions())
            self.rows_by_id[app.id] = row
            self.list_box.append(row)
        self.rows = list(self.rows_by_id.values())
        self.main_stack.set_visible_child_name("list" if self.rows else "empty")
        self._sync_actions()

    @property
    def busy(self) -> bool:
        """Checking, updating all, or any row busy (e.g. an update or a details operation)."""
        return self._checking or self._update_queue is not None or any(r.busy for r in self.rows)

    def refresh_rows(self) -> None:
        """Show check results the manager got elsewhere (a background check)."""
        for row in self.rows:
            if not row.busy:
                row.refresh()
        self._sync_actions()

    def _sync_actions(self) -> None:
        """Checking and "Update All" both change the rows' state: one at a time."""
        if self.manager is None:
            return
        updating_all = self._update_queue is not None
        self.check_action.set_enabled(bool(self.rows) and not self._checking and not updating_all)
        updates = sum(1 for r in self.rows if r.has_update)
        self.update_all_button.set_visible(updating_all or updates >= 2)
        self.update_all_action.set_enabled(updates >= 1 and not self._checking and not updating_all)
        # While running, the same button stops the run.
        if updating_all:
            self.update_all_button.set_action_name("win.stop-updates")
            self.update_all_button.set_label(_("_Stop"))
            self.update_all_button.set_tooltip_text(_("Cancel the current download and skip the remaining updates"))
            self.update_all_button.remove_css_class("suggested-action")
        else:
            self.update_all_button.set_action_name("win.update-all")
            self.update_all_button.set_label(_("_Update All"))
            self.update_all_button.set_tooltip_text(
                _("Update every app with an available update, one after another"))
            self.update_all_button.add_css_class("suggested-action")
        self.stop_updates_action.set_enabled(updating_all and not self._update_stopped)
        if not updates:
            self._app.updates_changed()

    # ── Add app ──────────────────────────────────────────────────────────────
    def open_add_dialog(self) -> None:
        dialog = AddDialog(self.manager)
        dialog.connect("app-changed", self._on_app_changed)
        dialog.present(self)

    # ── Details ──────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_row_activated(self, _list_box, row: AppRow) -> None:
        self.open_details(row)

    def open_details(self, row: AppRow) -> DetailsDialog:
        dialog = DetailsDialog(self.manager, row)
        dialog.connect("app-changed", self._on_app_changed)
        dialog.connect("app-removed", lambda *_: self.populate())
        dialog.connect("notify-user", lambda _d, msg: self.toast(msg))
        dialog.present(self)
        return dialog

    def _on_app_changed(self, _dialog, app: ManagedApp, release: Release | None) -> None:
        is_new = app.id not in self.rows_by_id
        self.populate()
        row = self.rows_by_id[app.id]
        if release is not None:
            row.set_release(release)  # freshly fetched (install or repo change)
        row.refresh()
        if is_new:
            self.toast(_("{name} installed").format(name=app.name))

    def _first_time_this_check(self, message: str) -> bool:
        """During a check, many rows can fail the same way (e.g. rate limit or a
        revoked token): False if `message` was already shown in this check."""
        if not self._checking:
            return True
        if message in self._shown_messages:
            return False
        self._shown_messages.add(message)
        return True

    def toast(self, message: str) -> None:
        if not self._destroyed and self._first_time_this_check(message):
            self.toast_overlay.add_toast(Adw.Toast(title=message, use_markup=False))

    def _show_row_error(self, heading: str, body: str) -> None:
        # Keyed on the heading: rate limit bodies differ in the minutes left.
        if not self._destroyed and self._first_time_this_check(heading):
            show_error(self, heading, body)

    # ── Check for updates ────────────────────────────────────────────────────
    def check_updates(self) -> None:
        if self._checking or not self.rows or self._update_queue is not None:
            return
        if self._app.background.checking:
            self.toast(_("Already checking for updates in the background"))
            return
        if self._app.token_loading:
            self._check_after_token = True  # use the saved token, not the 60/hour limit
            return
        # Rows busy with another operation (e.g. in a details dialog) are skipped.
        apps = [row.app for row in self.rows if row.start_check()]
        if not apps:
            return
        self._checking = True
        self._sync_actions()
        self._check_errors = 0
        self._shown_messages.clear()
        # Results arrive on the main loop in order, all before on_success/on_error.
        run_task(self.manager.check_updates, apps, on_main_loop(self._on_app_checked),
                 on_success=self._on_check_finished,
                 on_error=self._on_check_failed)

    def _on_app_checked(self, app: ManagedApp, result: CheckResult) -> None:
        row = self.rows_by_id.get(app.id)
        if row is not None:
            row.end_check()
        if result.error is not None:
            self._check_errors += 1
            self._report_check_error(app, result.error)

    def _report_check_error(self, app: ManagedApp, error: Exception) -> None:
        if isinstance(error, RateLimitError):
            # Same for every app and the user has to act: shown once per check.
            self._show_row_error(_("GitHub Rate Limit Reached"), str(error))
        elif isinstance(error, TokenRejectedError):
            self._show_row_error(_("Access Token Rejected"), str(error))
        else:
            self.toast(_("{app}: {error}").format(app=app.name, error=error))

    def _on_check_failed(self, error: Exception) -> None:
        # Not a per-app failure (those are results): unexpected, but never leave rows "checking".
        for row in self.rows:
            if row.state_stack.get_visible_child_name() == "checking":
                row.end_check()
        self._checking = False
        self._sync_actions()
        if not self._destroyed:
            show_error(self, _("Could Not Check for Updates"), error)

    def _on_check_finished(self, results: dict[str, CheckResult]) -> None:
        self._checking = False
        self._sync_actions()
        self._app.check_finished(results, background=False)
        if self._check_errors:
            return  # each failing app already showed its own toast or alert
        updates = sum(1 for r in self.rows if r.has_update)
        if updates:
            self.toast(ngettext("{n} update available", "{n} updates available", updates).format(n=updates))
        else:
            self.toast(_("All apps are up to date"))

    # ── Update all ───────────────────────────────────────────────────────────
    def update_all(self) -> None:
        """Update every app with an available update, one at a time, so downloads
        don't compete and a failure doesn't stop the others."""
        if self._update_queue is not None or self._checking:
            return
        queue = [r for r in self.rows if r.has_update and not r.busy]
        if not queue:
            return
        self._update_queue, self._update_done, self._update_stopped = queue, [], False
        for row in queue:
            row.set_queued(True)
        self._sync_actions()
        self._update_next()

    def stop_updates(self) -> None:
        """Cancel the running download and drop the rest of the queue."""
        if self._update_queue is None:
            return
        self._update_stopped = True
        queue, self._update_queue = self._update_queue, []
        for row in queue:
            row.set_queued(False)
        if self._update_current is not None:
            self._update_current.cancel()
        self._sync_actions()

    def _update_next(self, _finished: AppRow | None = None) -> None:
        if _finished is not None:
            self._update_done.append(_finished)
        self._update_current = None
        while self._update_queue:
            row = self._update_queue.pop(0)
            if self.rows_by_id.get(row.app.id) is not row:
                continue  # removed (or replaced) while waiting
            if row.update(done=self._update_next, quiet=True):
                self._update_current = row
                return
            # Nothing to do any more (already updated or busy): skip it.
        self._finish_update_all()

    def _finish_update_all(self) -> None:
        done, self._update_queue, self._update_done = self._update_done, None, []
        stopped, self._update_stopped = self._update_stopped, False
        self._sync_actions()
        failed = [r for r in done if r.last_error]
        updated = sum(1 for r in done if not r.last_error and not r.last_cancelled)
        problems = [_("{app}: {error}").format(app=r.app.name, error=r.last_error) for r in failed]
        problems += [w for r in done for w in r.last_warnings]
        if stopped and not updated:
            self.toast(_("Updates stopped"))
        elif stopped:
            self.toast(ngettext("Updates stopped after {n} app", "Updates stopped after {n} apps",
                                updated).format(n=updated))
        elif updated:
            self.toast(ngettext("{n} app updated", "{n} apps updated", updated).format(n=updated))
        if self._destroyed:
            return  # closed during the run: nobody to tell
        if failed:
            heading = ngettext("{n} Update Failed", "{n} Updates Failed", len(failed)).format(n=len(failed))
            show_error(self, heading, "\n\n".join(problems))
        elif problems:
            show_launcher_warnings(self, problems)
