import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from .core.config import ConfigError  # noqa: E402
from .core.manager import AppManager  # noqa: E402
from .ui import token_store  # noqa: E402
from .ui.background import BackgroundService  # noqa: E402
from .ui.window import TarseerWindow  # noqa: E402

APP_ID = "io.github.davideb263.Tarseer"


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

        self.add_main_option("background", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "Start without a window and check for updates in the background", None)
        self.add_main_option("check-now", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
                             "With --background: check right away (for testing)", None)
        self._create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self._create_action("about", self._on_about)

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

    def background_check_finished(self) -> None:
        for window in self.get_windows():
            if isinstance(window, TarseerWindow):
                window.refresh_rows()

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
