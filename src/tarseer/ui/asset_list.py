from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..core.assets import selectable_assets
from ..core.models import Asset, Release


class AssetList:
    """Radio rows in a PreferencesGroup for choosing one file of a release."""

    def __init__(self, group: Adw.PreferencesGroup):
        self.group = group
        self._rows: list[tuple[Adw.ActionRow, Gtk.CheckButton, Asset]] = []

    def fill(self, release: Release, repo: str, current: Asset | None = None) -> bool:
        """Show the release's selectable files; False (and nothing shown) if there are none.
        `current` (the file the app uses now) is marked and preselected; otherwise
        the best match for this machine is."""
        candidates = selectable_assets(release.assets, repo=repo)
        if current is not None and current not in candidates:
            candidates.insert(0, current)  # e.g. a file for another architecture
        if not candidates:
            return False
        for row, _check, _asset in self._rows:
            self.group.remove(row)
        self._rows = []

        group = None
        for asset in candidates:
            check = Gtk.CheckButton(group=group, valign=Gtk.Align.CENTER)
            group = group or check
            row = Adw.ActionRow(title=asset.name, use_markup=False, activatable_widget=check)
            if asset.size:
                row.set_subtitle(GLib.format_size(asset.size))
            row.add_prefix(check)
            if asset is current:
                row.add_suffix(Gtk.Label(label=_("Current"), valign=Gtk.Align.CENTER,
                                         css_classes=["dim-label"]))
            self.group.add(row)
            self._rows.append((row, check, asset))
        chosen = next((c for _r, c, a in self._rows if a is current), self._rows[0][1])
        chosen.set_active(True)
        return True

    def selected(self) -> Asset:
        return next(a for _row, check, a in self._rows if check.get_active())
