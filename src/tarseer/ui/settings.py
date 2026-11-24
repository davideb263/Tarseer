"""GSettings for the app. A dev sandbox (TARSEER_DEV_ROOT) gets its own keyfile,
so trying out background checks never changes the real settings."""

from __future__ import annotations

import os

from gi.repository import Gio

APP_ID = "io.github.davideb263.Tarseer"
_settings: Gio.Settings | None = None


def app_settings() -> Gio.Settings:
    global _settings
    if _settings is None:
        if dev_root := os.environ.get("TARSEER_DEV_ROOT"):
            backend = Gio.keyfile_settings_backend_new(
                os.path.join(dev_root, "settings.ini"), "/io/github/davideb263/Tarseer/", "tarseer")
            _settings = Gio.Settings.new_full(Gio.SettingsSchemaSource.get_default().lookup(APP_ID, True),
                                              backend, None)
        else:
            _settings = Gio.Settings(schema_id=APP_ID)
    return _settings
