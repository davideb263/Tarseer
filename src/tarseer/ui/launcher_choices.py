from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, Gtk

from ..core import desktop
from ..core.models import ManagedApp


class LauncherChoices:
    """Fills a Program and an Icon Adw.ComboRow with what's in an install folder."""

    def __init__(self, exec_row: Adw.ComboRow, icon_row: Adw.ComboRow):
        self.exec_row = exec_row
        self.icon_row = icon_row
        self.install_dir: Path | None = None
        self.executables: list[str] = []
        self.icons: list[str | None] = []

    def load(self, install_dir: Path, app: ManagedApp) -> None:
        self.install_dir = install_dir
        self.executables = desktop.rank_executables(install_dir, app.name)
        if app.executable and app.executable not in self.executables:
            self.executables.insert(0, app.executable)
        if self.executables:
            self.exec_row.set_model(Gtk.StringList.new(self.executables))
            self.exec_row.set_selected(self.executables.index(app.executable) if app.executable else 0)
            self.exec_row.set_sensitive(True)
            self.exec_row.set_subtitle("")
        else:
            self.exec_row.set_model(Gtk.StringList.new([]))
            self.exec_row.set_sensitive(False)
            self.exec_row.set_subtitle(_("No programs found, so there is no app grid entry"))

        icons = desktop.rank_icons(install_dir, app.name)
        self.icons = [None, *icons]
        self.icon_row.set_model(Gtk.StringList.new([_("Generic icon"), *icons]))
        self.icon_row.set_selected(self.icons.index(app.icon) if app.icon in self.icons else 0)

    def selection(self) -> tuple[str | None, str | None]:
        executable = self.executables[self.exec_row.get_selected()] if self.executables else None
        index = self.icon_row.get_selected()
        icon = self.icons[index] if 0 <= index < len(self.icons) else None
        return executable, icon

    def show_icon(self, image: Gtk.Image) -> None:
        """Preview the selected icon in `image`."""
        _executable, icon = self.selection()
        if icon and self.install_dir:
            image.set_from_gicon(Gio.FileIcon.new(Gio.File.new_for_path(str(self.install_dir / icon))))
        else:
            image.set_from_icon_name("application-x-executable")
