"""Persistence of the managed-apps list (a small JSON file)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .i18n import _
from .models import ManagedApp, is_valid_id

CONFIG_VERSION = 1


class ConfigError(Exception):
    pass


def load_apps(path: Path) -> list[ManagedApp]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        apps = [ManagedApp.from_dict(item) for item in data.get("apps", [])]
    except (OSError, ValueError, TypeError, AttributeError) as e:
        raise ConfigError(_("Could not read {path}: {error}").format(path=path, error=e)) from e
    for app in apps:
        if not is_valid_id(app.id):
            raise ConfigError(_("Could not read {path}: invalid app id {id!r}").format(path=path, id=app.id))
    return apps


def save_apps(path: Path, apps: list[ManagedApp]) -> None:
    """Write atomically, so a crash never leaves a half-written config."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": CONFIG_VERSION, "apps": [a.to_dict() for a in apps]}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".apps-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
