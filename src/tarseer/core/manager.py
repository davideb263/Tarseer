"""High-level operations the UI calls. Blocking: run them in a worker thread."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Callable

from . import config, desktop, github, installer
from .assets import match_asset
from .installer import InstallError
from .models import ManagedApp, Paths, Release

_RESERVED_SUFFIXES = (".bak", ".swap")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "app"


class AppManager:
    def __init__(self, paths: Paths | None = None):
        self.paths = paths or Paths.default()
        self._lock = threading.Lock()
        self._apps: dict[str, ManagedApp] = {
            a.id: a for a in config.load_apps(self.paths.config_file)
        }

    # ── Queries ──────────────────────────────────────────────────────────────
    @property
    def apps(self) -> list[ManagedApp]:
        return sorted(self._apps.values(), key=lambda a: a.name.lower())

    def get(self, app_id: str) -> ManagedApp | None:
        return self._apps.get(app_id)

    def install_dir(self, app: ManagedApp) -> Path:
        return self.paths.install_root / app.id

    def can_rollback(self, app: ManagedApp) -> bool:
        return installer.has_backup(self.install_dir(app))

    def unique_id(self, name: str) -> str:
        base = slugify(name)
        candidate, n = base, 2
        while candidate in self._apps or (self.paths.install_root / candidate).exists():
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    @staticmethod
    def update_available(app: ManagedApp, release: Release) -> bool:
        return app.installed_tag != release.tag

    # ── Network ──────────────────────────────────────────────────────────────
    @staticmethod
    def fetch_latest(repo: str) -> Release:
        return github.get_latest_release(repo)

    # ── Mutations ────────────────────────────────────────────────────────────
    def install(
        self,
        app: ManagedApp,
        release: Release,
        progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
    ) -> ManagedApp:
        """Install or update `app` to `release`, registering it if new."""
        if app.id.endswith(_RESERVED_SUFFIXES) or "/" in app.id or app.id.startswith("."):
            raise InstallError(f"Invalid app id: {app.id!r}")
        asset = match_asset(release.assets, app.asset_pattern)
        if asset is None:
            raise InstallError(
                f"No file in release {release.tag} matches the pattern {app.asset_pattern}"
            )

        root = self.paths.install_root
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=root, prefix=".tarseer-dl-")
        archive = Path(tmp_name)
        try:
            os.close(fd)
            github.download(asset.url, archive, progress=progress, cancel=cancel)
            target = self.install_dir(app)
            made_backup = installer.install_archive(archive, target)
        finally:
            archive.unlink(missing_ok=True)

        with self._lock:
            app.previous_tag = app.installed_tag if made_backup else None
            app.installed_tag = release.tag
            # Re-guess launcher paths if they are unset or vanished in this version.
            if not app.executable or not (target / app.executable).is_file():
                app.executable = desktop.guess_executable(target, app.name)
            if app.icon and not (target / app.icon).is_file():
                app.icon = None
            if not app.icon:
                app.icon = desktop.guess_icon(target, app.name)
            self._apps[app.id] = app
            self._write_launcher(app)
            self._save()
        return app

    def rollback(self, app: ManagedApp) -> None:
        installer.rollback(self.install_dir(app))
        with self._lock:
            app.installed_tag, app.previous_tag = app.previous_tag, app.installed_tag
            self._write_launcher(app)
            self._save()

    def remove(self, app: ManagedApp) -> None:
        installer.remove(self.install_dir(app))
        desktop.remove_desktop_entry(self.paths.applications_dir, app)
        with self._lock:
            self._apps.pop(app.id, None)
            self._save()

    def set_launcher(self, app: ManagedApp, executable: str | None, icon: str | None) -> None:
        target = self.install_dir(app)
        for rel in (executable, icon):
            if rel is None:
                continue
            path = (target / rel).resolve()
            if not path.is_relative_to(target.resolve()) or not path.is_file():
                raise InstallError(f"{rel} is not a file inside {target}")
        with self._lock:
            app.executable, app.icon = executable, icon
            self._write_launcher(app)
            self._save()

    # ── Internals ────────────────────────────────────────────────────────────
    def _write_launcher(self, app: ManagedApp) -> None:
        if app.executable:
            desktop.write_desktop_entry(self.paths.applications_dir, app, self.install_dir(app))
        else:
            desktop.remove_desktop_entry(self.paths.applications_dir, app)

    def _save(self) -> None:
        config.save_apps(self.paths.config_file, list(self._apps.values()))
