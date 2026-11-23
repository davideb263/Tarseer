from __future__ import annotations

from gi.repository import Adw, Gtk


def show_launcher_warnings(parent: Gtk.Widget, warnings: list[str]) -> None:
    """Explain app grid entry problems the user has to fix (too long for a toast)."""
    if not warnings:
        return
    dialog = Adw.AlertDialog(
        heading=ngettext("App Grid Entry Not Created", "App Grid Entries Not Created", len(warnings)),
        body="\n\n".join(warnings),
    )
    dialog.add_response("ok", _("_OK"))
    dialog.present(parent)
