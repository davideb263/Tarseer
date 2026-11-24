"""Dev tool: launch Tarseer, optionally drive it, and save PNG screenshots.

Runs INSIDE the installed Flatpak, e.g.:
  DEV=/path/to/devroot
  flatpak run --filesystem="$PWD/tools:ro" --filesystem="$DEV" --env=TARSEER_DEV_ROOT="$DEV" \
      --command=python3 io.github.davideb263.Tarseer "$PWD/tools/dev_screenshot.py" \
      "$DEV/shot.png" --check --update sparrow
Background checks: settings for a dev root are in "$DEV/settings.ini" (group [tarseer]).
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
from gi.repository import Adw, Gio, GLib, Graphene, Gtk  # noqa: E402

Gio.Resource.load("/app/share/tarseer/tarseer.gresource")._register()
from tarseer.main import TarseerApplication  # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("out", help="final screenshot path")
parser.add_argument("--check", action="store_true", help="run 'Check for updates' first")
parser.add_argument("--background-check", action="store_true",
                    help="instead of --check: run a background check (as the service does) with the window open")
parser.add_argument("--update", metavar="APP_ID", help="then update this app ('all': press Update All)")
parser.add_argument("--checked-shot", metavar="PATH", help="with --check: screenshot after the check")
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
parser.add_argument("--reinstall-response", choices=["later", "reinstall"],
                    help="with change-file: answer the 'Reinstall now?' alert (when the tag is the latest)")
parser.add_argument("--reinstall-shot", metavar="PATH", help="screenshot of the 'Reinstall now?' alert")
parser.add_argument("--pick-file", metavar="NAME", help="file to choose in the picker (default: preselected)")
parser.add_argument("--details-shot", metavar="PATH", help="with --details: screenshot before the action")
parser.add_argument("--details-bottom-shot", metavar="PATH",
                    help="with --details: also screenshot scrolled to the bottom")
parser.add_argument("--slow-download", action="store_true",
                    help="throttle downloads (~250 KB/s) so progress and cancelling can be seen")
parser.add_argument("--cancel-after", type=float, metavar="SECONDS",
                    help="cancel the --update (Stop for 'all') or the --add install after SECONDS")
parser.add_argument("--preferences", metavar="PATH",
                    help="finally open Preferences and screenshot it to PATH")
parser.add_argument("--pref-background", choices=["on", "off"],
                    help="with --preferences: flip the background checks switch, screenshot -background")
parser.add_argument("--pref-frequency", type=int, metavar="INDEX",
                    help="with --pref-background: also pick this frequency entry")
parser.add_argument("--pref-token", metavar="TOKEN",
                    help="with --preferences: apply TOKEN, then screenshot to PATH with -applied")
parser.add_argument("--fake-token-ok", action="store_true",
                    help="pretend GitHub accepts any token (the API status shows 5000/hour)")
parser.add_argument("--pref-remove", action="store_true",
                    help="with --pref-token: then remove the token (screenshot -removed)")
parser.add_argument("--size", default="720x560")
parser.add_argument("--timeout", type=float, default=300)
args = parser.parse_args()

if args.slow_download:
    from tarseer.core import github

    class _SlowResponse:
        def __init__(self, resp):
            self._resp = resp
            self.headers = resp.headers

        def read(self, n=-1):
            if n and n > 0:
                time.sleep(0.25)
            return self._resp.read(n)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return self._resp.__exit__(*exc)

    _real_urlopen = github._urlopen
    github._urlopen = lambda *a, **kw: _SlowResponse(_real_urlopen(*a, **kw))

if args.fake_token_ok:
    from tarseer.core import github as _gh
    _real_limit = _gh.get_rate_limit

    def _fake_limit(token=None, timeout=15):
        if token or _gh.has_token():
            return _gh.RateLimit(5000, 4987, time.time() + 2400)
        return _real_limit(None, timeout)
    _gh.get_rate_limit = _fake_limit

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

    def cancel_install():
        dialog().on_install_cancel()
        shot("cancelling")
        wait_for(lambda: tag() == "asset",
                 lambda: GLib.timeout_add(800, lambda: (shot("cancelled"), app.quit())[-1] and False))

    def progress_shot():
        GLib.timeout_add(2500, lambda: (shot("progress") if tag() == "progress" else None) and False)
        if args.cancel_after:
            GLib.timeout_add(int(args.cancel_after * 1000), lambda: cancel_install() and False)
            return
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
            return wait_for(picker, lambda: use_picker(answer_reinstall if args.reinstall_response else after))
        if args.details_repo:
            dialog.repo_row.set_text(args.details_repo)
            dialog.on_repo_apply()
            # Either a picker opens (new file names) or the change just goes through.
            return wait_for(lambda: picker() or not win.rows_by_id[args.details].busy,
                            lambda: use_picker(after) if picker() else after())
        after()

    def after():
        GLib.timeout_add(1000, lambda: wait_idle(win, then) and False)

    def reinstall_alert():
        d = dialog.get_root().get_visible_dialog() if dialog.get_root() else None
        return d if isinstance(d, Adw.AlertDialog) else None

    def answer_reinstall():
        def answer():
            alert = reinstall_alert()
            if args.reinstall_shot:
                screenshot(win, args.reinstall_shot)
            alert.emit("response", args.reinstall_response)
            alert.force_close()
            GLib.timeout_add(500, lambda: after() and False)
        wait_for(reinstall_alert, lambda: GLib.timeout_add(600, lambda: answer() and False))

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


def drive_preferences(win, then):
    win.activate_action("win.preferences", None)
    dialog = win.get_visible_dialog()

    def idle():
        return not dialog.status_spinner.get_visible()

    def shoot():
        screenshot(win, args.preferences)
        if args.pref_background:
            dialog.background_row.set_active(args.pref_background == "on")
            if args.pref_frequency is not None:
                dialog.frequency_row.set_selected(args.pref_frequency)
            GLib.timeout_add(1500, lambda: (
                screenshot(win, args.preferences.replace(".png", "-background.png")), token())[-1] and False)
            return
        token()

    def token():
        if not args.pref_token:
            return then()
        dialog.token_row.set_text(args.pref_token)
        dialog.on_token_apply()
        GLib.timeout_add(500, lambda: wait_for(idle, lambda: GLib.timeout_add(
            1000, lambda: (screenshot(win, args.preferences.replace(".png", "-applied.png")), remove())[-1]
            and False)) and False)

    def remove():
        if not args.pref_remove:
            return then()
        dialog.on_remove_token()
        GLib.timeout_add(1500, lambda: wait_for(idle, lambda: (
            screenshot(win, args.preferences.replace(".png", "-removed.png")), then())) and False)

    wait_for(idle, lambda: GLib.timeout_add(500, lambda: shoot() and False))


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
        elif args.preferences:
            drive_preferences(win, final_shot)
        else:
            final_shot()

    def do_update():
        if args.checked_shot:
            screenshot(win, args.checked_shot)
        if args.update == "all":
            win.activate_action("win.update-all", None)
            print("states:", {r.app.id: r.state_stack.get_visible_child_name() for r in win.rows})
            if args.cancel_after:
                def stop():
                    if args.progress_shot:
                        screenshot(win, args.progress_shot.replace(".png", "-before-stop.png"))
                    win.activate_action("win.stop-updates", None)
                GLib.timeout_add(int(args.cancel_after * 1000), lambda: stop() and False)
            if args.progress_shot:  # on the next frame: small downloads finish within a second
                clock = win.get_frame_clock()
                handler = []

                def painted(*_a):
                    clock.disconnect(handler[0])
                    screenshot(win, args.progress_shot)
                handler.append(clock.connect("after-paint", painted))
            wait_for(lambda: win._update_queue is None, lambda: GLib.timeout_add(1500, lambda: finish() and False))
        elif args.update:
            row = next(r for r in win.rows if r.app.id == args.update)
            row.update()
            if args.cancel_after:
                GLib.timeout_add(int(args.cancel_after * 1000), lambda: row.cancel() and False)
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
        if args.background_check:
            def checked(ok):
                print("background check succeeded:", ok)
                GLib.timeout_add(300, lambda: do_update() and False)
            application.background.run_check(checked)
        elif args.check:
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
