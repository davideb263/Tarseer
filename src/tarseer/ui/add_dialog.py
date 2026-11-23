from __future__ import annotations

import re
from pathlib import Path

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..core import desktop, github
from ..core.assets import candidate_assets, is_tarball, pattern_from_name
from ..core.manager import AppManager
from ..core.models import Asset, ManagedApp, Release
from .tasks import ThrottledProgress, run_task


def _default_name(repo: str) -> str:
    """'sparrowwallet/sparrow' -> 'Sparrow', 'foo/some-tool' -> 'Some Tool'."""
    name = repo.split("/")[1]
    if name.islower():
        name = re.sub(r"[-_.]+", " ", name).title()
    return name


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/add_dialog.ui")
class AddDialog(Adw.Dialog):
    """Repository -> choose file -> install -> confirm program and icon."""

    __gtype_name__ = "TarseerAddDialog"
    __gsignals__ = {
        # (ManagedApp, Release | None): app installed, or its launcher changed.
        "app-changed": (GObject.SignalFlags.RUN_FIRST, None, (object, object)),
    }

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    nav: Adw.NavigationView = Gtk.Template.Child()
    repo_entry: Adw.EntryRow = Gtk.Template.Child()
    continue_button: Gtk.Button = Gtk.Template.Child()
    name_entry: Adw.EntryRow = Gtk.Template.Child()
    assets_group: Adw.PreferencesGroup = Gtk.Template.Child()
    install_button: Gtk.Button = Gtk.Template.Child()
    progress_page: Adw.StatusPage = Gtk.Template.Child()
    progress_bar: Gtk.ProgressBar = Gtk.Template.Child()
    review_icon: Gtk.Image = Gtk.Template.Child()
    review_title: Gtk.Label = Gtk.Template.Child()
    review_subtitle: Gtk.Label = Gtk.Template.Child()
    exec_row: Adw.ComboRow = Gtk.Template.Child()
    icon_row: Adw.ComboRow = Gtk.Template.Child()

    def __init__(self, manager: AppManager):
        super().__init__()
        self.manager = manager
        self.repo: str | None = None
        self.release: Release | None = None
        self.app: ManagedApp | None = None
        self._asset_rows: list[tuple[Adw.ActionRow, Gtk.CheckButton, Asset]] = []
        self._executables: list[str] = []
        self._icons: list[str | None] = []

    def toast(self, message: str) -> None:
        self.toast_overlay.add_toast(Adw.Toast(title=message, use_markup=False, timeout=6))

    # ── Step 1: repository ───────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_repo_changed(self, *_args) -> None:
        self.repo_entry.remove_css_class("error")

    @Gtk.Template.Callback()
    def on_continue(self, *_args) -> None:
        if not self.continue_button.get_sensitive():
            return
        try:
            repo = github.parse_repo(self.repo_entry.get_text())
        except ValueError:
            self.repo_entry.add_css_class("error")
            self.toast(_("Enter a repository like “owner/name” or a github.com link"))
            return
        existing = next((a for a in self.manager.apps if a.repo.lower() == repo.lower()), None)
        if existing:
            self.toast(_("{repo} is already installed as “{name}”").format(repo=repo, name=existing.name))
            return

        self.continue_button.set_sensitive(False)
        self.continue_button.set_label(_("Looking up releases…"))

        def done():
            self.continue_button.set_sensitive(True)
            self.continue_button.set_label(_("_Continue"))

        def on_success(release: Release):
            done()
            self._show_assets(repo, release)

        def on_error(error: Exception):
            done()
            self.repo_entry.add_css_class("error")
            self.toast(str(error))

        run_task(self.manager.fetch_latest, repo, on_success=on_success, on_error=on_error)

    # ── Step 2: name + file ──────────────────────────────────────────────────
    def _show_assets(self, repo: str, release: Release) -> None:
        candidates = candidate_assets(release.assets, repo=repo)
        if not candidates:
            # Nothing obviously for this machine: offer every tarball and let the user decide.
            candidates = [a for a in release.assets if is_tarball(a.name)]
        if not candidates:
            self.toast(_("Release {tag} of {repo} has no tarball files").format(tag=release.tag, repo=repo))
            return

        self.repo, self.release = repo, release
        for row, _check, _asset in self._asset_rows:
            self.assets_group.remove(row)
        self._asset_rows = []

        self.assets_group.set_description(
            _("From release {tag}. The best match for this computer is preselected.").format(tag=release.tag))
        group = None
        for asset in candidates:
            check = Gtk.CheckButton(group=group, valign=Gtk.Align.CENTER)
            group = group or check
            row = Adw.ActionRow(title=asset.name, use_markup=False, activatable_widget=check)
            if asset.size:
                row.set_subtitle(GLib.format_size(asset.size))
            row.add_prefix(check)
            self.assets_group.add(row)
            self._asset_rows.append((row, check, asset))
        self._asset_rows[0][1].set_active(True)

        self.name_entry.set_text(_default_name(repo))
        self.nav.push_by_tag("asset")

    @Gtk.Template.Callback()
    def on_name_changed(self, *_args) -> None:
        valid = bool(self.name_entry.get_text().strip())
        self.install_button.set_sensitive(valid)
        if valid:
            self.name_entry.remove_css_class("error")
        else:
            self.name_entry.add_css_class("error")

    @Gtk.Template.Callback()
    def on_install(self, *_args) -> None:
        name = self.name_entry.get_text().strip()
        asset = next(a for _row, check, a in self._asset_rows if check.get_active())
        release = self.release
        app = ManagedApp(
            id=self.manager.unique_id(name),
            name=name,
            repo=self.repo,
            asset_pattern=pattern_from_name(asset.name, release.tag),
        )

        self.set_can_close(False)
        self.progress_bar.set_fraction(0)
        self.progress_page.set_title(_("Installing {name}…").format(name=name))
        self.progress_page.set_description(_("Downloading…"))
        self.nav.push_by_tag("progress")

        def on_progress(done: int, total: int):
            if total:
                self.progress_bar.set_fraction(done / total)
                self.progress_page.set_description(_("Downloading {done} of {total}").format(
                    done=GLib.format_size(done), total=GLib.format_size(total)))
                if done >= total:
                    self.progress_page.set_description(_("Extracting…"))
            else:
                self.progress_bar.pulse()

        def on_success(installed: ManagedApp):
            self.set_can_close(True)
            self.app = installed
            self.emit("app-changed", installed, release)
            self._show_review()

        def on_error(error: Exception):
            self.set_can_close(True)
            self.nav.pop_to_tag("asset")
            self.toast(_("Installation failed: {error}").format(error=error))

        run_task(self.manager.install, app, release, ThrottledProgress(on_progress),
                 on_success=on_success, on_error=on_error)

    # ── Step 4: review ───────────────────────────────────────────────────────
    def _show_review(self) -> None:
        app = self.app
        install_dir = self.manager.install_dir(app)
        self.review_title.set_label(app.name)
        self.review_subtitle.set_label(_("{tag} installed in {path}").format(
            tag=app.installed_tag, path=str(install_dir).replace(str(Path.home()), "~", 1)))

        self._executables = desktop.rank_executables(install_dir, app.name)
        if app.executable and app.executable not in self._executables:
            self._executables.insert(0, app.executable)
        if self._executables:
            self.exec_row.set_model(Gtk.StringList.new(self._executables))
            self.exec_row.set_selected(self._executables.index(app.executable) if app.executable else 0)
            self.exec_row.set_sensitive(True)
            self.exec_row.set_subtitle("")
        else:
            self.exec_row.set_model(Gtk.StringList.new([]))
            self.exec_row.set_sensitive(False)
            self.exec_row.set_subtitle(_("No programs found, so no app grid entry was created"))

        icons = desktop.rank_icons(install_dir, app.name)
        self._icons = [None, *icons]
        self.icon_row.set_model(Gtk.StringList.new([_("Generic icon"), *icons]))
        self.icon_row.set_selected(self._icons.index(app.icon) if app.icon in self._icons else 0)
        self._update_preview()

        # Replace the stack so "back" can't return to the progress page.
        self.nav.replace_with_tags(["review"])

    @Gtk.Template.Callback()
    def on_icon_selected(self, *_args) -> None:
        self._update_preview()

    def _update_preview(self) -> None:
        if not self.app:
            return
        index = self.icon_row.get_selected()
        icon = self._icons[index] if 0 <= index < len(self._icons) else None
        if icon:
            path = self.manager.install_dir(self.app) / icon
            self.review_icon.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(path))))
        else:
            self.review_icon.set_from_icon_name("application-x-executable")

    @Gtk.Template.Callback()
    def on_done(self, *_args) -> None:
        app = self.app
        executable = self._executables[self.exec_row.get_selected()] if self._executables else None
        icon = self._icons[self.icon_row.get_selected()]
        if (executable, icon) != (app.executable, app.icon):
            try:
                self.manager.set_launcher(app, executable, icon)
            except Exception as e:  # noqa: BLE001
                self.toast(str(e))
                return
            self.emit("app-changed", app, None)
        self.close()
