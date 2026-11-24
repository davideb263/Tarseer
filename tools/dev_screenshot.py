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
parser.add_argument("--add", metavar="REPO", help="walk through the Add App dialog for REPO")
parser.add_argument("--add-shots", metavar="PREFIX",
                    help="with --add: save PREFIX-repo.png, -asset.png, -progress.png, -review.png")
parser.add_argument("--add-name", help="with --add: override the suggested name")
parser.add_argument("--details", metavar="APP_ID", help="finally open the details dialog of APP_ID")
parser.add_argument("--details-action", choices=["rollback", "remove-confirm", "remove", "change-file"],
                    help="with --details: press a button (remove-confirm only shows the alert; "
                         "change-file screenshots the file picker, then picks --pick-file)")
parser.add_argument("--details-repo", metavar="REPO",
                    help="with --details: change the repository (screenshots the file picker if one opens)")
parser.add_argument("--picker-shot", metavar="PATH", help="screenshot of the release file picker")
parser.add_argument("--pick-file", metavar="NAME", help="file to choose in the picker (default: preselected)")
parser.add_argument("--details-shot", metavar="PATH", help="with --details: screenshot before the action")
parser.add_argument("--details-bottom-shot", metavar="PATH",
                    help="with --details: also screenshot scrolled to the bottom")
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
    node = snap.to_node()
    if node is None:  # nothing drawn yet
        print("skipped (not drawn yet)", path, file=sys.stderr)
        return
    texture = win.get_renderer().render_texture(node, Graphene.Rect().init(0, 0, w, h))
    texture.save_to_png(path)
    print("saved", path)


def wait_for(condition, then, interval=300):
    def poll():
        if time.monotonic() - started > args.timeout:
            print("timeout", file=sys.stderr)
            if app.get_active_window():  # show where it got stuck (e.g. an error alert)
                screenshot(app.get_active_window(), args.out)
            app.quit()
            return GLib.SOURCE_REMOVE
        if not condition():
            return GLib.SOURCE_CONTINUE
        then()
        return GLib.SOURCE_REMOVE
    GLib.timeout_add(interval, poll)


def drive_add_dialog(win, then):
    """Open the dialog and click through it, screenshotting each step."""
    from tarseer.ui.add_dialog import AddDialog

    win.activate_action("win.add-app", None)
    shot = (lambda name: screenshot(win, f"{args.add_shots}-{name}.png")) if args.add_shots else (lambda name: None)

    def dialog():
        return next((d for d in [win.get_visible_dialog()] if isinstance(d, AddDialog)), None)

    def tag():
        d = dialog()
        return d.nav.get_visible_page().get_tag() if d else None

    def step_repo():
        dialog().repo_entry.set_text(args.add)
        GLib.timeout_add(800, lambda: (shot("repo"), dialog().on_continue(),
                                       wait_for(lambda: tag() == "asset", step_asset))[-1] and False)

    def step_asset():
        if args.add_name:
            dialog().name_entry.set_text(args.add_name)
        GLib.timeout_add(600, lambda: (shot("asset"), dialog().on_install(), progress_shot())[-1] and False)

    def progress_shot():
        GLib.timeout_add(2500, lambda: (shot("progress") if tag() == "progress" else None) and False)
        wait_for(lambda: tag() == "review", step_review)

    def step_review():
        GLib.timeout_add(800, lambda: (shot("review"), dialog().on_done(), then())[-1] and False)

    wait_for(lambda: tag() == "repo", step_repo)


def drive_details(win, then):
    row = win.rows_by_id[args.details]
    dialog = win.open_details(row)

    def scroll_to_bottom():
        stack = [dialog]
        while stack:
            widget = stack.pop()
            if isinstance(widget, Gtk.ScrolledWindow):
                adj = widget.get_vadjustment()
                adj.set_value(adj.get_upper())
            child = widget.get_first_child()
            while child:
                stack.append(child)
                child = child.get_next_sibling()

    def act():
        if args.details_shot:
            screenshot(win, args.details_shot)
        if args.details_bottom_shot:
            scroll_to_bottom()
            GLib.timeout_add(400, lambda: (screenshot(win, args.details_bottom_shot), act2())[-1] and False)
        else:
            act2()

    def act2():
        if args.details_action == "rollback":
            dialog.on_rollback()
        elif args.details_action == "remove-confirm":
            dialog.on_remove()
        elif args.details_action == "remove":
            dialog._on_remove_response(None, "remove")
        elif args.details_action == "change-file":
            dialog.on_change_file()
            return wait_for(picker, lambda: use_picker(after))
        if args.details_repo:
            dialog.repo_row.set_text(args.details_repo)
            dialog.on_repo_apply()
            # Either a picker opens (new file names) or the change just goes through.
            return wait_for(lambda: picker() or not win.rows_by_id[args.details].busy,
                            lambda: use_picker(after) if picker() else after())
        after()

    def after():
        GLib.timeout_add(1000, lambda: wait_idle(win, then) and False)

    def picker():
        from tarseer.ui.release_file_dialog import ReleaseFileDialog
        d = dialog.get_root().get_visible_dialog() if dialog.get_root() else None
        return d if isinstance(d, ReleaseFileDialog) else None

    def use_picker(done):
        def pick():
            p = picker()
            if args.picker_shot:
                screenshot(win, args.picker_shot)
            if args.pick_file:
                for _row, check, asset in p.asset_list._rows:
                    if asset.name == args.pick_file:
                        check.set_active(True)
            p.on_use()
            done()
        GLib.timeout_add(800, lambda: pick() and False)

    GLib.timeout_add(1200, lambda: act() and False)


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

    def final_shot():
        GLib.timeout_add(700, lambda: (screenshot(win, args.out), application.quit())[-1] and False)

    def finish():
        if args.details:
            drive_details(win, final_shot)
        else:
            final_shot()

    def do_update():
        if args.update:
            row = next(r for r in win.rows if r.app.id == args.update)
            row.update()
            if args.progress_shot:
                GLib.timeout_add(3000, lambda: screenshot(win, args.progress_shot) and False)
            GLib.timeout_add(500, lambda: wait_idle(win, finish) and False)
        else:
            finish()

    def do_add():
        if args.add:
            drive_add_dialog(win, lambda: GLib.timeout_add(800, lambda: do_check() and False))
        else:
            do_check()

    def do_check():
        if args.check:
            win.activate_action("win.check-updates", None)
            GLib.timeout_add(500, lambda: wait_idle(win, do_update) and False)
        else:
            do_update()

    GLib.timeout_add(800, lambda: do_add() and False)


def excepthook(*exc_info):
    # An exception inside a GLib callback would otherwise leave the app running until --timeout.
    sys.__excepthook__(*exc_info)
    app.quit()


sys.excepthook = excepthook
app.connect_after("activate", on_activate)
sys.exit(app.run([sys.argv[0]]))
