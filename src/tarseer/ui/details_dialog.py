from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..core import github
from ..core.assets import match_asset, pattern_from_name
from ..core.manager import AppManager
from ..core.models import ManagedApp, Release
from .alerts import show_error, show_launcher_warnings
from .launcher_choices import LauncherChoices
from .release_file_dialog import ReleaseFileDialog
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
        self._loading = False  # launcher rows are being (re)filled
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
        self.installed_row.set_subtitle(app.installed_tag or _("Unknown version"))
        can_rollback = self.manager.can_rollback(app)
        self.previous_row.set_visible(can_rollback)
        self.previous_row.set_subtitle(app.previous_tag or _("Unknown version"))
        self.folder_row.set_subtitle(_home_short(install_dir))

        self._loading = True
        self._update_sensitivity()
        self.launcher.load(install_dir, app, done=self._on_launcher_loaded)

    def _on_launcher_loaded(self) -> None:
        self._loading = False
        self.launcher.show_icon(self.icon)
        self._update_sensitivity()

    def _update_sensitivity(self) -> None:
        busy = self.row.busy
        self.busy_banner.set_revealed(busy)
        for widget in (self.general_group, self.version_group, self.remove_button):
            widget.set_sensitive(not busy)
        self.launcher_group.set_sensitive(not busy and not self._loading)

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
        # While the row was busy, Update All may have moved past it (clearing
        # `queued`) without redrawing it: do it now, on success and failure alike.
        self.row.refresh()

    def _busy_error(self, heading: str) -> None:
        """Something else claimed the app while the user was choosing."""
        show_error(self, heading, _("{name} is busy with another operation, so nothing was "
                                    "changed. Try again when it has finished.").format(name=self.app.name))

    # ── General ──────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_name_apply(self, *_args) -> None:
        if not self._start():
            self.name_row.set_text(self.app.name)
            return

        def on_success(_result):
            self._finish()
            self.refresh()
            self.emit("app-changed", self.app, None)

        def on_error(error: Exception):
            self._finish()
            show_error(self, _("Name Not Changed"), error)
            self.name_row.set_text(self.app.name)

        run_task(self.manager.set_name, self.app, self.name_row.get_text(),
                 on_success=on_success, on_error=on_error)

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

        def on_success(release: Release):
            # Only accept repositories that exist and have releases.
            self.repo_row.set_sensitive(True)
            self._finish()
            if match_asset(release.assets, self.app.asset_pattern):
                self._commit_repo(repo, release, None)
                return
            # The new repository names its files differently: pick the file again.
            message = _("No file in this repository's latest release matches the one "
                        "{name} used before. Choose the file to download from now on.").format(
                            name=self.app.name)
            self._choose_file(release, repo, message,
                              chosen=lambda pattern: self._commit_repo(repo, release, pattern),
                              cancelled=self._repo_not_changed)

        def on_error(error: Exception):
            self.repo_row.set_sensitive(True)
            self._finish()
            show_error(self, _("Repository Not Changed"), error)

        run_task(self.manager.fetch_latest, repo, on_success=on_success, on_error=on_error)

    def _repo_not_changed(self) -> None:
        self.repo_row.set_text(self.app.repo)
        self.toast(_("Repository not changed"))

    def _commit_repo(self, repo: str, release: Release, pattern: str | None) -> None:
        if not self._start():
            self.repo_row.set_text(self.app.repo)
            self._busy_error(_("Repository Not Changed"))
            return

        def on_success(_result):
            self._finish()
            self.refresh()
            self.toast(_("Repository changed"))
            # The release just fetched replaces the row's check result from the old repo.
            self.emit("app-changed", self.app, release)

        def on_error(error: Exception):
            self._finish()
            self.repo_row.set_text(self.app.repo)
            show_error(self, _("Repository Not Changed"), error)

        run_task(self.manager.set_repo, self.app, repo, pattern,
                 on_success=on_success, on_error=on_error)

    # ── Release file ─────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_change_file(self, *_args) -> None:
        if not self._start():
            return
        repo = self.app.repo

        def on_success(release: Release):
            self._finish()
            self._choose_file(release, repo, None,
                              chosen=lambda pattern: self._commit_pattern(pattern, release),
                              cancelled=lambda: None)

        def on_error(error: Exception):
            self._finish()
            show_error(self, _("Could Not Look Up Releases"), error)

        run_task(self.manager.fetch_latest, repo, on_success=on_success, on_error=on_error)

    def _choose_file(self, release: Release, repo: str, message: str | None,
                     chosen, cancelled) -> None:
        """Let the user pick a file of `release`; calls chosen(pattern) or cancelled()."""
        current = match_asset(release.assets, self.app.asset_pattern)
        picker = ReleaseFileDialog(release, repo, current, message)
        if not picker.has_files:
            show_error(self, _("No Usable Files"),
                       _("Release {tag} of {repo} has no tarball files.").format(tag=release.tag, repo=repo))
            cancelled()
            return
        picker.connect("file-chosen", lambda _d, asset: chosen(pattern_from_name(asset.name, release.tag)))
        picker.connect("cancelled", lambda _d: cancelled())
        picker.present(self)

    def _commit_pattern(self, pattern: str, release: Release) -> None:
        if pattern == self.app.asset_pattern:
            return
        if not self._start():
            self._busy_error(_("Release File Not Changed"))
            return

        def on_success(_result):
            self._finish()
            self.refresh()
            self.emit("app-changed", self.app, release)
            if release.tag == self.app.installed_tag:
                self._offer_reinstall(release)  # otherwise nothing would use it for a while
            else:
                self.toast(_("Release file changed. It is used from the next update."))

        def on_error(error: Exception):
            self._finish()
            show_error(self, _("Release File Not Changed"), error)

        run_task(self.manager.set_asset_pattern, self.app, pattern,
                 on_success=on_success, on_error=on_error)

    def _offer_reinstall(self, release: Release) -> None:
        alert = Adw.AlertDialog(
            heading=_("Reinstall {name} Now?").format(name=self.app.name),
            body=_("{tag} is already the latest version, so the new release file would only "
                   "be used from the next update.\n\nReinstalling downloads it now. The current "
                   "files are kept as the previous version, so you can roll back to them.")
                 .format(tag=release.tag),
        )
        alert.add_response("later", _("_Later"))
        alert.add_response("reinstall", _("_Reinstall"))
        alert.set_response_appearance("reinstall", Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response("reinstall")
        alert.set_close_response("later")

        def on_response(_alert, response: str):
            if response != "reinstall":
                self.toast(_("Release file changed. It is used from the next update."))
                return
            if self.row.update(reinstall=release):
                self.close()  # the progress shows in the app's row
            else:
                self._busy_error(_("{name} Not Reinstalled").format(name=self.app.name))

        alert.connect("response", on_response)
        alert.present(self)

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
            show_error(self, _("Could Not Switch Versions"), error)

        run_task(self.manager.rollback, self.app, on_success=on_success, on_error=on_error)

    # ── App grid entry ───────────────────────────────────────────────────────
    def _on_launcher_changed(self, *_args) -> None:
        if self._loading:
            return
        executable, icon = self.launcher.selection()
        if (executable, icon) == (self.app.executable, self.app.icon):
            return
        if not self._start():
            return

        def finish(error: Exception | None = None):
            self._finish()
            if error is not None:  # conflicts and bad paths alike
                show_launcher_warnings(self, [str(error)])
            self.launcher.show_icon(self.icon)
            self.emit("app-changed", self.app, None)

        run_task(self.manager.set_launcher, self.app, executable, icon,
                 on_success=lambda _r: finish(), on_error=finish)

    # ── Files ────────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_open_folder(self, *_args) -> None:
        folder = Gio.File.new_for_path(str(self.manager.install_dir(self.app)))

        def done(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error as e:
                if e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                    return  # the user cancelled an app chooser
                show_error(self, _("Could Not Open the Folder"), e)

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
            show_error(self, _("Could Not Remove {name}").format(name=app.name), error)

        run_task(self.manager.remove, app, on_success=on_success, on_error=on_error)
