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


def show_error(parent: Gtk.Widget, heading: str, error: Exception | str) -> None:
    """An error the user has to read in full or act on (toasts cut it off)."""
    dialog = Adw.AlertDialog(heading=heading, body=str(error))
    dialog.add_response("ok", _("_OK"))
    dialog.present(parent)
