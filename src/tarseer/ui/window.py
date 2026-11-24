from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from ..core.config import ConfigError
from ..core.manager import AppManager
from ..core.models import ManagedApp, Release
from .add_dialog import AddDialog
from .alerts import show_error, show_launcher_warnings
from .details_dialog import DetailsDialog
from .app_row import AppRow

APP_ID = "io.github.davideb263.Tarseer"


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

        # Remember the window size between launches.
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.settings.bind("window-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)

        self.check_action = Gio.SimpleAction.new("check-updates", None)
        self.check_action.connect("activate", lambda *_: self.check_updates())
        self.add_action(self.check_action)
        self.get_application().set_accels_for_action("win.check-updates", ["<primary>r", "F5"])

        self.add_app_action = Gio.SimpleAction.new("add-app", None)
        self.add_app_action.connect("activate", lambda *_: self.open_add_dialog())
        self.add_action(self.add_app_action)
        self.get_application().set_accels_for_action("win.add-app", ["<primary>n"])

        self.update_all_action = Gio.SimpleAction.new("update-all", None)
        self.update_all_action.connect("activate", lambda *_: self.update_all())
        self.add_action(self.update_all_action)

        self.rows: list[AppRow] = []
        self.rows_by_id: dict[str, AppRow] = {}
        self._pending_checks = 0
        self._check_errors = 0
        self._shown_messages: set[str] = set()
        # "Update All" run: rows still waiting, and the rows it handled so far.
        self._update_queue: list[AppRow] | None = None
        self._update_done: list[AppRow] = []

        try:
            self.manager: AppManager | None = AppManager()
        except ConfigError as e:
            # Don't create a manager: saving would overwrite the unreadable file.
            self.manager = None
            self.error_page.set_description(str(e))
            self.main_stack.set_visible_child_name("error")
            self.check_action.set_enabled(False)
            self.add_app_action.set_enabled(False)
            self.update_all_action.set_enabled(False)
            return
        self.populate()
        if self.manager.startup_warnings:
            # Wait until the window is on screen so the alert has a parent to attach to.
            self.connect("map", lambda *_: show_launcher_warnings(self, self.manager.startup_warnings))

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
                row.connect("show-error", lambda _row, heading, body: show_error(self, heading, body))
                row.connect("launcher-warnings", lambda _row, w: show_launcher_warnings(self, w))
                row.connect("refreshed", lambda _row: self._sync_actions())
            self.rows_by_id[app.id] = row
            self.list_box.append(row)
        self.rows = list(self.rows_by_id.values())
        self.main_stack.set_visible_child_name("list" if self.rows else "empty")
        self._sync_actions()

    def _sync_actions(self) -> None:
        """Checking and "Update All" both change the rows' state: one at a time."""
        if self.manager is None:
            return
        updating_all = self._update_queue is not None
        self.check_action.set_enabled(bool(self.rows) and not self._pending_checks and not updating_all)
        updates = sum(1 for r in self.rows if r.has_update)
        self.update_all_button.set_visible(updating_all or updates >= 2)
        self.update_all_action.set_enabled(updates >= 1 and not self._pending_checks and not updating_all)

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

    def toast(self, message: str) -> None:
        # During a check, many rows can fail the same way (e.g. rate limit): show it once.
        if self._pending_checks:
            if message in self._shown_messages:
                return
            self._shown_messages.add(message)
        self.toast_overlay.add_toast(Adw.Toast(title=message, use_markup=False))

    # ── Check for updates ────────────────────────────────────────────────────
    def check_updates(self) -> None:
        if self._pending_checks or not self.rows or self._update_queue is not None:
            return
        self._pending_checks = len(self.rows)
        self._sync_actions()
        self._check_errors = 0
        self._shown_messages.clear()
        for row in self.rows:
            row.check(done=self._on_row_checked)

    def _on_row_checked(self, row: AppRow) -> None:
        self._pending_checks -= 1
        if row.state_stack.get_visible_child_name() == "error":
            self._check_errors += 1
        if self._pending_checks:
            return
        self._sync_actions()
        if self._check_errors:
            return  # each failing row already showed its own toast
        updates = sum(1 for r in self.rows if r.has_update)
        if updates:
            self.toast(ngettext("{n} update available", "{n} updates available", updates).format(n=updates))
        else:
            self.toast(_("All apps are up to date"))

    # ── Update all ───────────────────────────────────────────────────────────
    def update_all(self) -> None:
        """Update every app with an available update, one at a time, so downloads
        don't compete and a failure doesn't stop the others."""
        if self._update_queue is not None or self._pending_checks:
            return
        queue = [r for r in self.rows if r.has_update and not r.busy]
        if not queue:
            return
        self._update_queue, self._update_done = queue, []
        for row in queue:
            row.set_queued(True)
        self._sync_actions()
        self._update_next()

    def _update_next(self, _finished: AppRow | None = None) -> None:
        if _finished is not None:
            self._update_done.append(_finished)
        while self._update_queue:
            row = self._update_queue.pop(0)
            if self.rows_by_id.get(row.app.id) is not row:
                continue  # removed (or replaced) while waiting
            if row.update(done=self._update_next, quiet=True):
                return
            # Nothing to do any more (already updated or busy): skip it.
        self._finish_update_all()

    def _finish_update_all(self) -> None:
        done, self._update_queue, self._update_done = self._update_done, None, []
        self._sync_actions()
        failed = [r for r in done if r.last_error]
        updated = len(done) - len(failed)
        problems = [_("{app}: {error}").format(app=r.app.name, error=r.last_error) for r in failed]
        problems += [w for r in done for w in r.last_warnings]
        if updated:
            self.toast(ngettext("{n} app updated", "{n} apps updated", updated).format(n=updated))
        if failed:
            heading = ngettext("{n} Update Failed", "{n} Updates Failed", len(failed)).format(n=len(failed))
            show_error(self, heading, "\n\n".join(problems))
        elif problems:
            show_launcher_warnings(self, problems)
