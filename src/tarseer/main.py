import sys
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from .core.config import ConfigError  # noqa: E402
from .core.manager import AppManager  # noqa: E402
from .core.models import CheckResult  # noqa: E402
from .core.schedule import check_succeeded, notification_state  # noqa: E402
from .ui import token_store  # noqa: E402
from .ui.background import BackgroundService  # noqa: E402
from .ui.settings import app_settings  # noqa: E402
from .ui.window import TarseerWindow  # noqa: E402

APP_ID = "io.github.davideb263.Tarseer"
NOTIFICATION_ID = "updates"
# Apps named in the notification body before "and N more".
NOTIFICATION_NAMES = 4


class TarseerApplication(Adw.Application):
    __gsignals__ = {
        # The saved GitHub token was read (or could not be).
        "token-loaded": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    def __init__(self, version: str):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            resource_base_path="/io/github/davideb263/Tarseer",
        )
        self.version = version
        self.manager: AppManager | None = None
        self.config_error: ConfigError | None = None
        # The saved token is read asynchronously (the keyring may need unlocking).
        self.token_loading = False
        self.token_set_by_user = False  # in Preferences, before loading finished
        self.background = BackgroundService(self)
        # Set by --background: the next activation starts the service, not a window.
        self._background_launch = False
        self._check_now = False  # --check-now: no delay before the first check
        # A notification may be showing, possibly from an earlier run: withdraw it
        # once it's known to be stale. Avoids a D-Bus call on every row redraw.
        self._notification_maybe_shown = True

        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without a window and check for updates in the background", None)
        self.add_main_option("check-now", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "With --background: check right away (for testing)", None)
        self._create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self._create_action("about", self._on_about)
        # Clicking the update notification (also starts Tarseer through D-Bus).
        self._create_action("show-updates", self._on_show_updates)

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if options.contains("background"):
            self.register(None)
            if self.get_is_remote():
                return 0  # Tarseer is already running: it checks on its own
            self._background_launch = True
            self._check_now = options.contains("check-now")
        return -1

    def do_startup(self):
        Adw.Application.do_startup(self)
        try:
            self.manager = AppManager()
        except ConfigError as e:
            # Don't create a manager: saving would overwrite the unreadable file.
            self.config_error = e
            return
        self.token_loading = True
        token_store.load(self._on_token_loaded)

    def do_activate(self):
        if self._background_launch:
            # The activation that follows `--background`: no window.
            self._background_launch = False
            if self._check_now:
                self.background.start_at_login(delay=0)
            else:
                self.background.start_at_login()
            return
        window = self.props.active_window or TarseerWindow(application=self)
        window.present()
        self.background.start_resident()

    # ── Token ────────────────────────────────────────────────────────────────
    def _on_token_loaded(self, token: str | None, error: Exception | None) -> None:
        self.token_loading = False
        if error is not None:
            # No keyring (or the portal refused): work without a token.
            print(f"Tarseer: could not read the GitHub token from the keyring: {error}", file=sys.stderr)
        elif not self.token_set_by_user:  # never undo a token the user just saved or removed
            AppManager.set_token(token)
        self.emit("token-loaded")

    def when_token_ready(self, callback, timeout: int | None = None) -> None:
        """Call `callback` once the saved token is in use, or after `timeout`
        seconds without it (a locked keyring may wait for the user)."""
        if not self.token_loading:
            callback()
            return
        state = {"handler": 0, "timer": 0}

        def fire(*_args):
            if state["handler"]:
                self.disconnect(state["handler"])
            if state["timer"]:
                GLib.source_remove(state["timer"])
            state["handler"] = state["timer"] = 0
            callback()

        def timed_out():
            state["timer"] = 0
            print("Tarseer: the keyring didn't answer, checking without the token", file=sys.stderr)
            fire()
            return GLib.SOURCE_REMOVE

        state["handler"] = self.connect("token-loaded", fire)
        if timeout is not None:
            state["timer"] = GLib.timeout_add_seconds(timeout, timed_out)

    # ── Background checks ────────────────────────────────────────────────────
    def window_busy(self) -> bool:
        window = self.props.active_window
        return isinstance(window, TarseerWindow) and window.busy

    def check_finished(self, results: dict[str, CheckResult], background: bool) -> None:
        """After any check: record when it worked, remember which updates the
        user knows about, and notify about new ones found in the background
        unless the window has focus. Updates found by a check in the window
        count as seen."""
        manager = self.manager
        updates = {}
        for app in manager.updates:
            check = manager.last_check(app.id)
            if check is not None and check.release is not None:
                updates[app.id] = check.release.tag
        failed = {app_id for app_id, r in results.items() if r.error is not None}
        settings = app_settings()
        if check_succeeded(results):
            # Also for checks in the window: no need for a background check right after.
            settings.set_int64("last-check", int(time.time()))
        notified = dict(settings.get_value("notified-tags").unpack())
        new, state = notification_state(updates, failed, notified)
        if state != notified:
            settings.set_value("notified-tags", GLib.Variant("a{ss}", state))
        for window in self.get_windows():
            if isinstance(window, TarseerWindow):
                window.refresh_rows()
        if not updates:
            self._withdraw_notification()
            return
        window = self.props.active_window
        if background and new and not (window is not None and window.is_active()):
            self._send_notification([(manager.get(app_id), tag) for app_id, tag in updates.items()])

    def updates_changed(self) -> None:
        """The window's rows changed: withdraw the notification once every app
        it may name has a check result and none has an update any more."""
        manager = self.manager
        if not self._notification_maybe_shown or manager is None or manager.updates:
            return
        notified = app_settings().get_value("notified-tags").unpack()
        if all(manager.last_check(app_id) is not None for app_id in notified if manager.get(app_id)):
            self._withdraw_notification()

    def _send_notification(self, updates) -> None:
        if len(updates) == 1:
            app, tag = updates[0]
            notification = Gio.Notification.new(_("Update Available"))
            notification.set_body(_("{app} {tag}").format(app=app.name, tag=tag))
        else:
            notification = Gio.Notification.new(
                _("{n} Updates Available").format(n=len(updates)))
            names = [app.name for app, _tag in updates]
            body = ", ".join(names[:NOTIFICATION_NAMES])
            if len(names) > NOTIFICATION_NAMES:
                more = len(names) - NOTIFICATION_NAMES
                body = ngettext("{names} and {n} more", "{names} and {n} more", more).format(
                    names=body, n=more)
            notification.set_body(body)
        notification.set_default_action("app.show-updates")
        self.send_notification(NOTIFICATION_ID, notification)
        self._notification_maybe_shown = True

    def _withdraw_notification(self) -> None:
        if self._notification_maybe_shown:
            self.withdraw_notification(NOTIFICATION_ID)
            self._notification_maybe_shown = False

    def _on_show_updates(self, *_args) -> None:
        self.activate()
        window = self.props.active_window
        manager = self.manager
        # Started by the click (the login check had exited): this process has no
        # results yet, so check now. With a token unchanged releases cost nothing.
        if (isinstance(window, TarseerWindow) and manager is not None
                and any(manager.last_check(a.id) is None for a in manager.apps)):
            window.check_updates()

    def _on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="Tarseer",
            application_icon=APP_ID,
            developer_name="Davide Botter",
            version=self.version,
            license_type=Gtk.License.GPL_3_0,
            comments="Install and update apps distributed as tarballs on GitHub",
            website="https://github.com/davideb263/Tarseer",
            issue_url="https://github.com/davideb263/Tarseer/issues",
        )
        about.present(self.props.active_window)

    def _create_action(self, name, callback, shortcuts=None):
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", callback)
        self.add_action(action)
        if shortcuts:
            self.set_accels_for_action(f"app.{name}", shortcuts)


def main(version: str) -> int:
    app = TarseerApplication(version)
    return app.run(sys.argv)
