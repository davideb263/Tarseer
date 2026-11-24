"""Background portal: start at login and the status line in GNOME's
background-apps list. Asynchronous; failures are reported, never raised.

Talks D-Bus directly: libportal's request_background takes the command line
as a GPtrArray that PyGObject doesn't pass correctly (the autostart entry
ended up with an empty command)."""

from __future__ import annotations

import os
import secrets
import sys
from typing import TYPE_CHECKING, Callable

from gi.repository import Gio, GLib

if TYPE_CHECKING:
    from gi.repository import Gtk

BACKGROUND_COMMAND = ["tarseer", "--background"]
_BUS_NAME = "org.freedesktop.portal.Desktop"
_OBJECT_PATH = "/org/freedesktop/portal/desktop"
_INTERFACE = "org.freedesktop.portal.Background"
# The portal rejects longer status messages.
_STATUS_MAX = 96


def _bus() -> Gio.DBusConnection:
    return Gio.bus_get_sync(Gio.BusType.SESSION, None)


def request_autostart(window: Gtk.Window | None, enable: bool, reason: str,
                      done: Callable[[bool, Exception | None], None]) -> None:
    """Ask to start `tarseer --background` at login (enable) or stop doing so.
    done(granted, error) runs on the main loop; granted is False if the user
    or the system refused. (`window` is unused: GNOME shows no dialog for this.)"""
    if os.environ.get("TARSEER_DEV_ROOT"):
        # The portal knows nothing about dev sandboxes: never touch the real autostart.
        # TARSEER_DEV_DENY_BACKGROUND=1 acts like the user refused (to see the alert).
        granted = not (enable and os.environ.get("TARSEER_DEV_DENY_BACKGROUND"))
        print(f"Tarseer (dev): would {'enable' if enable else 'disable'} autostart"
              + ("" if granted else " (denied)"), file=sys.stderr)
        GLib.idle_add(lambda: done(granted, None) and False)
        return

    try:
        bus = _bus()
    except GLib.Error as e:
        GLib.idle_add(lambda: done(False, e) and False)
        return

    # The reply arrives as a Response signal on a request object whose path the
    # portal derives from our bus name and this token: subscribe before calling.
    token = "tarseer_" + secrets.token_hex(8)
    sender = bus.get_unique_name().lstrip(":").replace(".", "_")
    state = {"path": f"{_OBJECT_PATH}/request/{sender}/{token}", "sub": 0, "finished": False}

    def finish(granted: bool, error: Exception | None) -> None:
        if state["finished"]:
            return
        state["finished"] = True
        if state["sub"]:
            bus.signal_unsubscribe(state["sub"])
            state["sub"] = 0
        done(granted, error)

    def on_response(_conn, _sender, _path, _iface, _signal, params):
        code, results = params.unpack()
        # 0: success, 1: cancelled by the user, 2: other (e.g. denied).
        granted = code == 0 and bool(results.get("background", False))
        if enable and code == 0 and not results.get("autostart", False):
            granted = False
        finish(granted, None)

    def subscribe(path: str) -> None:
        state["sub"] = bus.signal_subscribe(
            _BUS_NAME, "org.freedesktop.portal.Request", "Response", path, None,
            Gio.DBusSignalFlags.NONE, on_response)

    def on_called(conn, result):
        try:
            (path,) = conn.call_finish(result).unpack()
        except GLib.Error as e:
            finish(False, e)
            return
        if path != state["path"] and not state["finished"]:
            # An old portal that ignores handle_token: listen where it says.
            bus.signal_unsubscribe(state["sub"])
            state["path"] = path
            subscribe(path)

    subscribe(state["path"])
    options = {
        "handle_token": GLib.Variant("s", token),
        "reason": GLib.Variant("s", reason),
        "autostart": GLib.Variant("b", enable),
        "commandline": GLib.Variant("as", BACKGROUND_COMMAND),
        "dbus-activatable": GLib.Variant("b", False),
    }
    bus.call(_BUS_NAME, _OBJECT_PATH, _INTERFACE, "RequestBackground",
             GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
             Gio.DBusCallFlags.NONE, -1, None, on_called)


def set_status(message: str) -> None:
    """The line under Tarseer in GNOME's list of background apps (best effort)."""
    if os.environ.get("TARSEER_DEV_ROOT"):
        print(f"Tarseer (dev): background status: {message}", file=sys.stderr)
        return

    def on_called(conn, result):
        try:
            conn.call_finish(result)
        except GLib.Error as e:
            print(f"Tarseer: could not set the background status: {e}", file=sys.stderr)

    try:
        bus = _bus()
    except GLib.Error as e:
        print(f"Tarseer: could not set the background status: {e}", file=sys.stderr)
        return
    options = {"message": GLib.Variant("s", message[:_STATUS_MAX])}
    bus.call(_BUS_NAME, _OBJECT_PATH, _INTERFACE, "SetStatus",
             GLib.Variant("(a{sv})", (options,)), None,
             Gio.DBusCallFlags.NONE, -1, None, on_called)
