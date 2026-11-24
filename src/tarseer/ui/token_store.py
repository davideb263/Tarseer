"""The GitHub access token in the user's keyring (libsecret; the Secret portal
inside Flatpak). All calls are asynchronous: they never block the main loop."""

from __future__ import annotations

import os
from typing import Callable

import gi

gi.require_version("Secret", "1")
from gi.repository import GLib, Secret  # noqa: E402

_SCHEMA = Secret.Schema.new(
    "io.github.davideb263.Tarseer.GitHubToken",
    Secret.SchemaFlags.NONE,
    {"service": Secret.SchemaAttributeType.STRING, "profile": Secret.SchemaAttributeType.STRING},
)
# A dev sandbox (TARSEER_DEV_ROOT) gets its own entry, so tests never touch the real token.
_ATTRIBUTES = {"service": "api.github.com", "profile": os.environ.get("TARSEER_DEV_ROOT") or "default"}
_LABEL = "Tarseer GitHub access token"


def load(done: Callable[[str | None, Exception | None], None]) -> None:
    """done(token or None, error or None)."""

    def finish(_source, result):
        try:
            done(Secret.password_lookup_finish(result) or None, None)
        except GLib.Error as e:
            done(None, e)

    Secret.password_lookup(_SCHEMA, _ATTRIBUTES, None, finish)


def save(token: str, done: Callable[[Exception | None], None]) -> None:
    def finish(_source, result):
        try:
            Secret.password_store_finish(result)
            done(None)
        except GLib.Error as e:
            done(e)

    Secret.password_store(_SCHEMA, _ATTRIBUTES, Secret.COLLECTION_DEFAULT, _LABEL, token, None, finish)


def clear(done: Callable[[Exception | None], None]) -> None:
    def finish(_source, result):
        try:
            Secret.password_clear_finish(result)
            done(None)
        except GLib.Error as e:
            done(e)

    Secret.password_clear(_SCHEMA, _ATTRIBUTES, None, finish)
