"""Dev tool: launch Tarseer, optionally drive it, and save PNG screenshots.

Runs INSIDE the installed Flatpak, e.g.:
  DEV=/path/to/devroot
  flatpak run --filesystem="$PWD/tools:ro" --filesystem="$DEV" --env=TARSEER_DEV_ROOT="$DEV" \
      --command=python3 io.github.davideb263.Tarseer "$PWD/tools/dev_screenshot.py" \
      "$DEV/shot.png" --check --update sparrow
"""

import argparse
import gettext
import sys
import time

gettext.install("tarseer", names=["ngettext"])
sys.path.insert(1, "/app/share/tarseer")

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gio, GLib, Graphene, Gtk  # noqa: E402

Gio.Resource.load("/app/share/tarseer/tarseer.gresource")._register()
from tarseer.main import TarseerApplication  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("out", help="final screenshot path")
parser.add_argument("--check", action="store_true", help="run 'Check for updates' first")
parser.add_argument("--update", metavar="APP_ID", help="then update this app")
parser.add_argument("--progress-shot", metavar="PATH", help="screenshot 3s into the update")
parser.add_argument("--size", default="720x560")
parser.add_argument("--timeout", type=float, default=300)
args = parser.parse_args()

app = TarseerApplication("dev")
app.set_flags(app.get_flags() | Gio.ApplicationFlags.NON_UNIQUE)
started = time.monotonic()


def screenshot(win, path):
    w, h = win.get_width(), win.get_height()
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(win).snapshot(snap, w, h)
    texture = win.get_renderer().render_texture(snap.to_node(), Graphene.Rect().init(0, 0, w, h))
    texture.save_to_png(path)
    print("saved", path)


def wait_idle(win, then):
    def poll():
        if time.monotonic() - started > args.timeout:
            print("timeout", file=sys.stderr)
            app.quit()
            return GLib.SOURCE_REMOVE
        if any(r.busy for r in getattr(win, "rows", [])):
            return GLib.SOURCE_CONTINUE
        then()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(300, poll)


def on_activate(application):
    win = application.props.active_window
    w, h = (int(x) for x in args.size.split("x"))
    win.set_default_size(w, h)

    def finish():
        GLib.timeout_add(700, lambda: (screenshot(win, args.out), application.quit())[-1] and False)

    def do_update():
        if args.update:
            row = next(r for r in win.rows if r.app.id == args.update)
            row.update()
            if args.progress_shot:
                GLib.timeout_add(3000, lambda: screenshot(win, args.progress_shot) and False)
            GLib.timeout_add(500, lambda: wait_idle(win, finish) and False)
        else:
            finish()

    def do_check():
        if args.check:
            win.activate_action("win.check-updates", None)
            GLib.timeout_add(500, lambda: wait_idle(win, do_update) and False)
        else:
            do_update()

    GLib.timeout_add(800, lambda: do_check() and False)


app.connect_after("activate", on_activate)
sys.exit(app.run([sys.argv[0]]))
