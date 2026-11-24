from __future__ import annotations

from gi.repository import Adw, GObject, Gtk

from ..core.models import Asset, Release
from .asset_list import AssetList


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/release_file_dialog.ui")
class ReleaseFileDialog(Adw.Dialog):
    """Pick which file of a release an app downloads.

    Emits "file-chosen" (Asset) when the user confirms, or "cancelled" if the
    dialog is closed without choosing. Check `has_files` before presenting.
    """

    __gtype_name__ = "TarseerReleaseFileDialog"
    __gsignals__ = {
        "file-chosen": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
        "cancelled": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    assets_group: Adw.PreferencesGroup = Gtk.Template.Child()

    def __init__(self, release: Release, repo: str, current: Asset | None = None,
                 message: str | None = None):
        super().__init__()
        self._chosen = False
        self.asset_list = AssetList(self.assets_group)
        self.has_files = self.asset_list.fill(release, repo, current)
        description = _("From release {tag} of {repo}.").format(tag=release.tag, repo=repo)
        if message:
            description = message + "\n\n" + description
        self.assets_group.set_description(description)
        self.connect("closed", self._on_closed)

    @Gtk.Template.Callback()
    def on_use(self, *_args) -> None:
        self._chosen = True
        self.emit("file-chosen", self.asset_list.selected())
        self.close()

    def _on_closed(self, *_args) -> None:
        if not self._chosen:
            self.emit("cancelled")
