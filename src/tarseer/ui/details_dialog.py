from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GObject, Gtk

from ..core import github
from ..core.manager import AppManager
from ..core.models import ManagedApp
from .alerts import show_launcher_warnings
from .launcher_choices import LauncherChoices
from .tasks import run_task


def _home_short(path: Path) -> str:
    return str(path).replace(str(Path.home()), "~", 1)


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/details_dialog.ui")
class DetailsDialog(Adw.Dialog):
    """Everything about one installed app: settings, rollback, removal."""

    __gtype_name__ = "TarseerDetailsDialog"
    __gsignals__ = {
        # (ManagedApp, Release | None): the app's latest release if it was just
        # fetched, None to keep the row's last check result. Same as AddDialog's.
        "app-changed": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
        "app-removed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        # Toast for the main window (the dialog may be closed by then).
        "notify-user": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    busy_banner: Adw.Banner = Gtk.Template.Child()
    icon: Gtk.Image = Gtk.Template.Child()
    title_label: Gtk.Label = Gtk.Template.Child()
    version_label: Gtk.Label = Gtk.Template.Child()
    general_group: Adw.PreferencesGroup = Gtk.Template.Child()
    name_row: Adw.EntryRow = Gtk.Template.Child()
    repo_row: Adw.EntryRow = Gtk.Template.Child()
    pattern_row: Adw.ActionRow = Gtk.Template.Child()
    version_group: Adw.PreferencesGroup = Gtk.Template.Child()
    installed_row: Adw.ActionRow = Gtk.Template.Child()
    previous_row: Adw.ActionRow = Gtk.Template.Child()
    rollback_button: Gtk.Button = Gtk.Template.Child()
    launcher_group: Adw.PreferencesGroup = Gtk.Template.Child()
    exec_row: Adw.ComboRow = Gtk.Template.Child()
    icon_row: Adw.ComboRow = Gtk.Template.Child()
    folder_row: Adw.ActionRow = Gtk.Template.Child()
    remove_button: Gtk.Button = Gtk.Template.Child()

    def __init__(self, manager: AppManager, row):
        """`row` is the AppRow: its `busy` flag guards against concurrent operations."""
        super().__init__()
        self.manager = manager
        self.row = row
        self.app: ManagedApp = row.app
        self.launcher = LauncherChoices(self.exec_row, self.icon_row)
        self._loading = False
        self.exec_row.connect("notify::selected", self._on_launcher_changed)
        self.icon_row.connect("notify::selected", self._on_launcher_changed)
        self.refresh()

    def toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, use_markup=False, timeout=5))

    # ── Display ──────────────────────────────────────────────────────────────
    def refresh(self) -> None:
        app = self.app
        install_dir = self.manager.install_dir(app)
        self.set_title(app.name)
        self.title_label.set_label(app.name)
        self.version_label.set_label(app.installed_tag or "")
        self.name_row.set_text(app.name)
        self.repo_row.set_text(app.repo)
        self.pattern_row.set_subtitle(app.asset_pattern)
        self.installed_row.set_subtitle(app.installed_tag or _("Unknown"))
        can_rollback = self.manager.can_rollback(app)
        self.previous_row.set_visible(can_rollback)
        self.previous_row.set_subtitle(app.previous_tag or _("Unknown version"))
        self.folder_row.set_subtitle(_home_short(install_dir))

        self._loading = True
        try:
            self.launcher.load(install_dir, app)
        finally:
            self._loading = False
        self.launcher.show_icon(self.icon)
        self._update_sensitivity()

    def _update_sensitivity(self) -> None:
        busy = self.row.busy
        self.busy_banner.set_revealed(busy)
        for widget in (self.general_group, self.version_group, self.launcher_group, self.remove_button):
            widget.set_sensitive(not busy)

    def _start(self) -> bool:
        """Claim the app for an operation; False if something else is running."""
        if self.row.busy:
            self._update_sensitivity()
            return False
        self.row.busy = True
        self._update_sensitivity()
        return True

    def _finish(self) -> None:
        self.row.busy = False
        self._update_sensitivity()

    # ── General ──────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_name_apply(self, *_args) -> None:
        try:
            self.manager.set_name(self.app, self.name_row.get_text())
        except (ValueError, OSError) as e:
            self.toast(str(e))
            self.name_row.set_text(self.app.name)
            return
        self.refresh()
        self.emit("app-changed", self.app, None)

    @Gtk.Template.Callback()
    def on_repo_apply(self, *_args) -> None:
        try:
            repo = github.parse_repo(self.repo_row.get_text())
        except ValueError:
            self.toast(_("Enter a repository like “owner/name” or a github.com link"))
            return
        if repo == self.app.repo:
            self.repo_row.set_text(repo)
            return
        if not self._start():
            return
        self.repo_row.set_sensitive(False)

        def on_success(release):
            self.repo_row.set_sensitive(True)
            self._finish()
            try:
                self.manager.set_repo(self.app, repo)
            except (ValueError, OSError) as e:
                self.toast(str(e))
                return
            self.refresh()
            self.toast(_("Repository changed"))
            # The release just fetched replaces the row's check result from the old repo.
            self.emit("app-changed", self.app, release)

        def on_error(error: Exception):
            self.repo_row.set_sensitive(True)
            self._finish()
            self.toast(_("Not changed: {error}").format(error=error))

        # Only accept repositories that exist and have releases.
        run_task(self.manager.fetch_latest, repo, on_success=on_success, on_error=on_error)

    # ── Version ──────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_rollback(self, *_args) -> None:
        if not self._start():
            return
        target = self.app.previous_tag or _("the previous version")

        def on_success(warnings: list[str]):
            self._finish()
            self.refresh()
            self.toast(_("Switched to {tag}").format(tag=target))
            self.emit("app-changed", self.app, None)
            show_launcher_warnings(self, warnings)

        def on_error(error: Exception):
            self._finish()
            self.refresh()
            self.toast(_("Could not switch versions: {error}").format(error=error))

        run_task(self.manager.rollback, self.app, on_success=on_success, on_error=on_error)

    # ── App grid entry ───────────────────────────────────────────────────────
    def _on_launcher_changed(self, *_args) -> None:
        if self._loading:
            return
        executable, icon = self.launcher.selection()
        if (executable, icon) == (self.app.executable, self.app.icon):
            return
        try:
            self.manager.set_launcher(self.app, executable, icon)
        except Exception as e:  # noqa: BLE001 - conflicts and bad paths alike
            show_launcher_warnings(self, [str(e)])
        self.launcher.show_icon(self.icon)
        self.emit("app-changed", self.app, None)

    # ── Files ────────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_open_folder(self, *_args) -> None:
        folder = Gio.File.new_for_path(str(self.manager.install_dir(self.app)))

        def done(launcher, result):
            try:
                launcher.launch_finish(result)
            except Exception as e:  # noqa: BLE001
                self.toast(_("Could not open the folder: {error}").format(error=e))

        Gtk.FileLauncher.new(folder).launch(self.get_root(), None, done)

    # ── Remove ───────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_remove(self, *_args) -> None:
        app = self.app
        confirm = Adw.AlertDialog(
            heading=_("Remove {name}?").format(name=app.name),
            body=_("This deletes {folder}, its previous version and its app grid entry.\n\n"
                   "Settings and data the app keeps elsewhere (for example in ~/.config) are not touched.")
                 .format(folder=_home_short(self.manager.install_dir(app))),
        )
        confirm.add_response("cancel", _("_Cancel"))
        confirm.add_response("remove", _("_Remove"))
        confirm.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        confirm.set_default_response("cancel")
        confirm.set_close_response("cancel")
        confirm.connect("response", self._on_remove_response)
        confirm.present(self)

    def _on_remove_response(self, _dialog, response: str) -> None:
        if response != "remove" or not self._start():
            return
        self.set_can_close(False)
        app = self.app

        def on_success(_result):
            self.row.busy = False
            self.set_can_close(True)
            self.emit("app-removed", app)
            self.emit("notify-user", _("{name} removed").format(name=app.name))
            self.close()

        def on_error(error: Exception):
            self.set_can_close(True)
            self._finish()
            self.refresh()
            self.toast(_("Could not remove {name}: {error}").format(name=app.name, error=error))

        run_task(self.manager.remove, app, on_success=on_success, on_error=on_error)
