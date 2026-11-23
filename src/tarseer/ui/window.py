from gi.repository import Adw, Gio, Gtk

APP_ID = "io.github.davideb263.Tarseer"


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/window.ui")
class TarseerWindow(Adw.ApplicationWindow):
    __gtype_name__ = "TarseerWindow"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Remember the window size between launches.
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.settings.bind("window-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)
