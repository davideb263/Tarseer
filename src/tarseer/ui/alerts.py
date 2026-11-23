from __future__ import annotations

from gi.repository import Adw, Gtk


def show_launcher_warnings(parent: Gtk.Widget, warnings: list[str]) -> None:
    """Explain problems the user has to fix (too long for a toast): app grid
    entry conflicts, settings that could not be saved, ..."""
    if not warnings:
        return
    dialog = Adw.AlertDialog(
        heading=_("Needs Your Attention"),
        body="\n\n".join(warnings),
    )
    dialog.add_response("ok", _("_OK"))
    dialog.present(parent)
