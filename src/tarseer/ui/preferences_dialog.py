from __future__ import annotations

from gi.repository import Adw, GLib, GObject, Gtk

from ..core.github import RateLimit, TokenRejectedError
from ..core.manager import AppManager
from . import token_store
from .alerts import show_error
from .tasks import run_task

NEW_TOKEN_URL = ("https://github.com/settings/personal-access-tokens/new"
                 "?name=Tarseer&description=Checks+GitHub+releases+for+Tarseer")


@Gtk.Template(resource_path="/io/github/davideb263/Tarseer/ui/preferences_dialog.ui")
class PreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "TarseerPreferencesDialog"
    __gsignals__ = {
        # The user saved or removed the token.
        "token-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    token_row: Adw.PasswordEntryRow = Gtk.Template.Child()
    status_row: Adw.ActionRow = Gtk.Template.Child()
    status_spinner: Adw.Spinner = Gtk.Template.Child()
    remove_row: Adw.ButtonRow = Gtk.Template.Child()

    def __init__(self, manager: AppManager, token_loading: bool = False):
        """With `token_loading`, the saved token is still being read: call
        token_loaded() once it is."""
        super().__init__()
        self.manager = manager
        self._sync_token_rows()
        if token_loading:
            self.status_spinner.set_visible(True)
            self.status_row.set_subtitle(_("Reading the saved token from your keyring…"))
        else:
            self.refresh_status()

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
