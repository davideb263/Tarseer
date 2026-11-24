from __future__ import annotations

import sys

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from ..core.github import RateLimit, TokenRejectedError
from ..core.manager import AppManager
from . import portal, token_store
from .alerts import show_error
from .settings import app_settings
from .tasks import run_task

# check-interval-hours for each entry of the frequency dropdown (0: at login only).
FREQUENCY_HOURS = [0, 6, 12, 24]

NEW_TOKEN_URL = ("https://github.com/settings/personal-access-tokens/new"
                 "?name=Tarseer&description=Checks+GitHub+releases+for+Tarseer")


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/preferences_dialog.ui")
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "TarseerPreferencesDialog"
    __gsignals__ = {
        # The user saved or removed the token.
        "token-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    background_row: Adw.SwitchRow = Gtk.Template.Child()
    frequency_row: Adw.ComboRow = Gtk.Template.Child()
    last_check_row: Adw.ActionRow = Gtk.Template.Child()
    token_row: Adw.PasswordEntryRow = Gtk.Template.Child()
    status_row: Adw.ActionRow = Gtk.Template.Child()
    status_spinner: Adw.Spinner = Gtk.Template.Child()
    remove_row: Adw.ButtonRow = Gtk.Template.Child()

    def __init__(self, manager: AppManager, token_loading: bool = False):
        """With `token_loading`, the saved token is still being read: call
        token_loaded() once it is."""
        super().__init__()
        self.manager = manager
        self.settings = app_settings()
        self._setup_updates()
        self._sync_token_rows()
        if token_loading:
            self.status_spinner.set_visible(True)
            self.status_row.set_subtitle(_("Reading the saved token from your keyring…"))
        else:
            self.refresh_status()

    # ── Background checks ────────────────────────────────────────────────────
    def _setup_updates(self) -> None:
        self.background_row.set_active(self.settings.get_boolean("background-checks"))
        hours = self.settings.get_uint("check-interval-hours")
        # A value set by hand (gsettings) shows as the nearest longer choice.
        index = next((i for i, h in enumerate(FREQUENCY_HOURS) if h >= hours), len(FREQUENCY_HOURS) - 1)
        self.frequency_row.set_selected(index)
        self._background_handler = self.background_row.connect("notify::active", self._on_background_toggled)
        self.frequency_row.connect("notify::selected", self._on_frequency_changed)
        handler = self.settings.connect("changed::last-check", lambda *_: self._show_last_check())
        self.connect("closed", lambda *_: self.settings.disconnect(handler))
        self._show_last_check()

    def _show_last_check(self) -> None:
        stamp = self.settings.get_int64("last-check")
        if stamp <= 0:
            self.last_check_row.set_subtitle(_("Never"))
            return
        when = GLib.DateTime.new_from_unix_local(stamp)
        now = GLib.DateTime.new_now_local()
        time = when.format("%H:%M")
        if when.get_ymd() == now.get_ymd():
            text = _("Today at {time}").format(time=time)
        elif when.get_ymd() == now.add_days(-1).get_ymd():
            text = _("Yesterday at {time}").format(time=time)
        else:
            text = _("{date} at {time}").format(date=when.format("%x"), time=time)
        self.last_check_row.set_subtitle(text)

    def _set_background_row(self, active: bool) -> None:
        """Change the switch without asking the portal again."""
        with self.background_row.handler_block(self._background_handler):
            self.background_row.set_active(active)

    def _on_background_toggled(self, row, _pspec) -> None:
        enable = row.get_active()
        row.set_sensitive(False)  # one portal request at a time

        def done(granted: bool, error: Exception | None):
            row.set_sensitive(True)
            if enable and not granted:
                self._set_background_row(False)
                if error is not None:
                    body = _("The request to run in the background failed: {error}").format(error=error)
                else:
                    body = _("Tarseer isn't allowed to run in the background. You can allow it in "
                             "Settings → Apps → Tarseer.")
                show_error(self, _("Background Checks Not Turned On"), body)
                return
            if error is not None:
                # Turning off: the setting is off anyway, so a leftover autostart
                # entry only starts Tarseer to exit right away.
                print(f"Tarseer: could not remove the autostart entry: {error}", file=sys.stderr)
            self.settings.set_boolean("background-checks", enable)
            Gio.Application.get_default().background.restart()

        portal.request_autostart(self.get_root(), enable,
                                 _("Check for app updates when you log in"), done)

    def _on_frequency_changed(self, row, _pspec) -> None:
        index = row.get_selected()
        if index >= len(FREQUENCY_HOURS):
            return
        self.settings.set_uint("check-interval-hours", FREQUENCY_HOURS[index])
        Gio.Application.get_default().background.restart()

    # ── Token ────────────────────────────────────────────────────────────────
    def token_loaded(self) -> None:
        self._sync_token_rows()
        self.refresh_status()

    def toast(self, message: str) -> None:
        self.add_toast(Adw.Toast(title=message, use_markup=False))

    def _sync_token_rows(self) -> None:
        has_token = self.manager.has_token()
        self.remove_row.set_visible(has_token)
        self.token_row.set_title(_("Replace Token") if has_token else _("Access Token"))

    # ── Status ───────────────────────────────────────────────────────────────
    def refresh_status(self) -> None:
        self.status_spinner.set_visible(True)

        def on_error(error: Exception):
            self.status_spinner.set_visible(False)
            self.status_row.set_subtitle(_("Could not check: {error}").format(error=error))

        run_task(self.manager.rate_limit, on_success=self._show_limit, on_error=on_error)

    def _show_limit(self, limit: RateLimit) -> None:
        self.status_spinner.set_visible(False)
        reset = GLib.DateTime.new_from_unix_local(int(limit.reset_at)).format("%H:%M")
        self.status_row.set_subtitle(
            _("{remaining} of {limit} · resets at {time}").format(
                remaining=limit.remaining, limit=limit.limit, time=reset))

    # ── Token ────────────────────────────────────────────────────────────────
    @Gtk.Template.Callback()
    def on_create_token(self, *_args) -> None:
        def done(launcher, result):
            try:
                launcher.launch_finish(result)
            except GLib.Error as e:
                show_error(self, _("Could Not Open the Browser"), e)

        Gtk.UriLauncher.new(NEW_TOKEN_URL).launch(self.get_root(), None, done)

    @Gtk.Template.Callback()
    def on_token_apply(self, *_args) -> None:
        token = self.token_row.get_text().strip()
        if not token:
            return
        self.token_row.set_sensitive(False)
        self.status_spinner.set_visible(True)

        def failed(heading: str, error: Exception | str):
            self.token_row.set_sensitive(True)
            self.status_spinner.set_visible(False)
            show_error(self, heading, error)

        def on_valid(limit: RateLimit):
            # Only keep tokens GitHub accepts; then store before using it.
            def saved(error: Exception | None):
                if error is not None:
                    failed(_("Could Not Save the Token"),
                           _("The token is valid, but it could not be stored in your keyring: {error}")
                           .format(error=error))
                    return
                self.manager.set_token(token)
                self.emit("token-changed")
                self.token_row.set_text("")
                self.token_row.set_sensitive(True)
                self._sync_token_rows()
                self._show_limit(limit)
                self.toast(_("Token saved"))

            token_store.save(token, saved)

        def on_error(error: Exception):
            if isinstance(error, TokenRejectedError):
                error = _("GitHub rejected this token. Check that it was copied completely "
                          "and hasn't expired.")
            failed(_("Token Not Saved"), error)

        run_task(self.manager.rate_limit, token, on_success=on_valid, on_error=on_error)

    @Gtk.Template.Callback()
    def on_remove_token(self, *_args) -> None:
        self.remove_row.set_sensitive(False)

        def cleared(error: Exception | None):
            self.remove_row.set_sensitive(True)
            if error is not None:
                show_error(self, _("Could Not Remove the Token"), error)
                return
            self.manager.set_token(None)
            self.emit("token-changed")
            self._sync_token_rows()
            self.refresh_status()
            self.toast(_("Token removed"))

        token_store.clear(cleared)
