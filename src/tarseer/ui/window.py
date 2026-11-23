from __future__ import annotations

from gi.repository import Adw, Gio, Gtk

from ..core.config import ConfigError
from ..core.manager import AppManager
from .app_row import AppRow

APP_ID = "io.github.davideb263.Tarseer"


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/window.ui")
class TarseerWindow(Adw.ApplicationWindow):
    __gtype_name__ = "TarseerWindow"

    toast_overlay: Adw.ToastOverlay = Gtk.Template.Child()
    main_stack: Gtk.Stack = Gtk.Template.Child()
    list_box: Gtk.ListBox = Gtk.Template.Child()
    error_page: Adw.StatusPage = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # Remember the window size between launches.
        self.settings = Gio.Settings(schema_id=APP_ID)
        self.settings.bind("window-width", self, "default-width", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-height", self, "default-height", Gio.SettingsBindFlags.DEFAULT)
        self.settings.bind("window-maximized", self, "maximized", Gio.SettingsBindFlags.DEFAULT)

        self.check_action = Gio.SimpleAction.new("check-updates", None)
        self.check_action.connect("activate", lambda *_: self.check_updates())
        self.add_action(self.check_action)
        self.get_application().set_accels_for_action("win.check-updates", ["<primary>r", "F5"])

        self.rows: list[AppRow] = []
        self._pending_checks = 0
        self._check_errors = 0
        self._shown_messages: set[str] = set()

        try:
            self.manager: AppManager | None = AppManager()
        except ConfigError as e:
            # Don't create a manager: saving would overwrite the unreadable file.
            self.manager = None
            self.error_page.set_description(str(e))
            self.main_stack.set_visible_child_name("error")
            self.check_action.set_enabled(False)
            return
        self.populate()

    def populate(self) -> None:
        for row in self.rows:
            self.list_box.remove(row)
        self.rows = [AppRow(self.manager, app) for app in self.manager.apps]
        for row in self.rows:
            row.connect("notify-user", lambda _row, msg: self.toast(msg))
            self.list_box.append(row)
        self.main_stack.set_visible_child_name("list" if self.rows else "empty")
        self.check_action.set_enabled(bool(self.rows))

    def toast(self, message: str) -> None:
        # During a check, many rows can fail the same way (e.g. rate limit): show it once.
        if self._pending_checks:
            if message in self._shown_messages:
                return
            self._shown_messages.add(message)
        self.toast_overlay.add_toast(Adw.Toast(title=message, use_markup=False))

    # ── Check for updates ────────────────────────────────────────────────────
    def check_updates(self) -> None:
        if self._pending_checks or not self.rows:
            return
        self.check_action.set_enabled(False)
        self._pending_checks = len(self.rows)
        self._check_errors = 0
        self._shown_messages.clear()
        for row in self.rows:
            row.check(done=self._on_row_checked)

    def _on_row_checked(self, row: AppRow) -> None:
        self._pending_checks -= 1
        if row.state_stack.get_visible_child_name() == "error":
            self._check_errors += 1
        if self._pending_checks:
            return
        self.check_action.set_enabled(True)
        if self._check_errors:
            return  # each failing row already showed its own toast
        updates = sum(1 for r in self.rows if r.has_update)
        if updates:
            self.toast(ngettext("{n} update available", "{n} updates available", updates).format(n=updates))
        else:
            self.toast(_("All apps are up to date"))
