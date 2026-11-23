import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio, Gtk  # noqa: E402

from .ui.window import TarseerWindow  # noqa: E402

APP_ID = "io.github.davideb263.Tarseer"


class TarseerApplication(Adw.Application):
    def __init__(self, version: str):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
            resource_base_path="/io/github/davideb263/Tarseer",
        )
        self.version = version
        self._create_action("quit", lambda *_: self.quit(), ["<primary>q"])
        self._create_action("about", self._on_about)

    def do_activate(self):
        window = self.props.active_window or TarseerWindow(application=self)
        window.present()

    def _on_about(self, *_args):
        about = Adw.AboutDialog(
            application_name="Tarseer",
            application_icon=APP_ID,
            developer_name="Davide Botter",
            version=self.version,
            license_type=Gtk.License.GPL_3_0,
            comments="Install and update apps distributed as tarballs on GitHub",
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
