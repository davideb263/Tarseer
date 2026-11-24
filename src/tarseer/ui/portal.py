"""Background portal: start at login and the status line in GNOME's
background-apps list. Asynchronous; failures are reported, never raised."""

from __future__ import annotations

import os
import sys
from typing import Callable

import gi

gi.require_version("Xdp", "1.0")
gi.require_version("XdpGtk4", "1.0")
from gi.repository import GLib, Gtk, Xdp, XdpGtk4  # noqa: E402

BACKGROUND_COMMAND = ["tarseer", "--background"]
_portal: Xdp.Portal | None = None


def _get_portal() -> Xdp.Portal:
    global _portal
    if _portal is None:
        _portal = Xdp.Portal.initable_new()
    return _portal


def request_autostart(window: Gtk.Window | None, enable: bool, reason: str,
                      done: Callable[[bool, Exception | None], None]) -> None:
    """Ask to start `tarseer --background` at login (enable) or stop doing so.
    done(granted, error): granted is False if the user or the system refused."""
    if os.environ.get("TARSEER_DEV_ROOT"):
        # The portal knows nothing about dev sandboxes: never touch the real autostart.
        print(f"Tarseer (dev): would {'enable' if enable else 'disable'} autostart", file=sys.stderr)
        GLib.idle_add(lambda: done(True, None) and False)
        return

    def finish(portal, result):
        try:
            done(portal.request_background_finish(result), None)
        except GLib.Error as e:
            done(False, e)

    try:
        portal = _get_portal()
    except GLib.Error as e:
        GLib.idle_add(lambda: done(False, e) and False)
        return
    parent = XdpGtk4.parent_new_gtk(window) if window is not None else None
    flags = Xdp.BackgroundFlags.AUTOSTART if enable else Xdp.BackgroundFlags.NONE
    portal.request_background(parent, reason, BACKGROUND_COMMAND, flags, None, finish, None)


def set_status(message: str) -> None:
    """The line under Tarseer in GNOME's list of background apps (best effort)."""
    if os.environ.get("TARSEER_DEV_ROOT"):
        print(f"Tarseer (dev): background status: {message}", file=sys.stderr)
        return

    def finish(portal, result):
        try:
            portal.set_background_status_finish(result)
        except GLib.Error as e:
            print(f"Tarseer: could not set the background status: {e}", file=sys.stderr)

    try:
        _get_portal().set_background_status(message, None, finish, None)
    except GLib.Error as e:
        print(f"Tarseer: could not set the background status: {e}", file=sys.stderr)
