"""High-level operations the UI calls. Blocking: run them in a worker thread."""

from __future__ import annotations

import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Callable

from . import config, desktop, github, installer
from .assets import match_asset, validate_pattern
from .desktop import DesktopConflictError
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
        # Messages for the UI about menu entries that could not be set up.
        self.startup_warnings: list[str] = self._sync_launchers()

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
    ) -> list[str]:
        """Install or update `app` to `release`, registering it if new.

        Returns warnings (e.g. the menu entry could not be created); the
        install itself succeeded if this returns. A new app that cannot be
        saved to the config is removed again, so a failed install leaves nothing behind.
        """
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
            self._revalidate_launcher_paths(app)
            try:
                warnings = self._write_launcher(app)
            except OSError as e:
                warnings = [f"Could not create the app grid entry for {app.name}: {e}"]
            is_new = app.id not in self._apps
            self._apps[app.id] = app
            try:
                self._save()
            except OSError as e:
                if not is_new:
                    # The new version is in place; the next successful save records it.
                    return warnings + [
                        f"{app.name} was updated to {release.tag}, but its settings could "
                        f"not be saved ({e}). After a restart it may show the old version."
                    ]
                self._apps.pop(app.id)
                self._discard(app)
                raise InstallError(f"Could not save settings: {e}") from e
        return warnings

    def rollback(self, app: ManagedApp) -> list[str]:
        """Swap the current and backed-up versions. If the previous tag was
        never recorded, `installed_tag` becomes None (version unknown)."""
        installer.rollback(self.install_dir(app))
        with self._lock:
            app.installed_tag, app.previous_tag = app.previous_tag, app.installed_tag
            self._revalidate_launcher_paths(app)
            warnings = self._write_launcher(app)
            self._save()
        return warnings

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
            try:
                self._write_launcher(app, strict=True)
            finally:
                self._save()

    def set_name(self, app: ManagedApp, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("The name cannot be empty")
        with self._lock:
            app.name = name
            self._write_launcher(app)  # conflicts were already reported at install time
            self._save()

    def set_repo(self, app: ManagedApp, repo: str, asset_pattern: str | None = None) -> None:
        """Point the app at another repository (e.g. the project was renamed),
        optionally with a new release file pattern if the files are named differently.
        Callers should check the repository exists first (see fetch_latest)."""
        repo = github.parse_repo(repo)
        if asset_pattern is not None:
            validate_pattern(asset_pattern)
        with self._lock:
            app.repo = repo
            if asset_pattern is not None:
                app.asset_pattern = asset_pattern
            self._write_launcher(app)
            self._save()

    def set_asset_pattern(self, app: ManagedApp, asset_pattern: str) -> None:
        """Choose which release file future updates download."""
        validate_pattern(asset_pattern)
        with self._lock:
            app.asset_pattern = asset_pattern
            self._save()

    # ── Internals ────────────────────────────────────────────────────────────
    def _revalidate_launcher_paths(self, app: ManagedApp) -> None:
        """Re-guess launcher paths that are unset or missing in the version now installed."""
        target = self.install_dir(app)
        if not app.executable or not (target / app.executable).is_file():
            app.executable = desktop.guess_executable(target, app.name)
        if app.icon and not (target / app.icon).is_file():
            app.icon = None
        if not app.icon:
            app.icon = desktop.guess_icon(target, app.name)

    def _discard(self, app: ManagedApp) -> None:
        """Best-effort removal of a new app's files and menu entry after a failed install."""
        for step in (
            lambda: desktop.remove_desktop_entry(self.paths.applications_dir, app),
            lambda: installer.remove(self.install_dir(app)),
        ):
            try:
                step()
            except OSError:
                pass

    def _write_launcher(self, app: ManagedApp, strict: bool = False) -> list[str]:
        """Create/refresh the menu entry. Name conflicts raise if `strict`,
        otherwise they are returned as warnings."""
        if not app.executable:
            desktop.remove_desktop_entry(self.paths.applications_dir, app)
            return []
        try:
            desktop.write_desktop_entry(self.paths.applications_dir, app, self.install_dir(app))
        except DesktopConflictError as e:
            if strict:
                raise
            return [str(e)]
        return []

    def _sync_launchers(self) -> list[str]:
        """Bring menu entries to the preferred "<id>.desktop" name where possible
        (migrates legacy "tarseer-<id>.desktop" files, recreates missing ones)."""
        warnings, changed = [], False
        for app in self._apps.values():
            if not app.executable or not self.install_dir(app).is_dir():
                continue
            preferred = desktop.preferred_desktop_path(self.paths.applications_dir, app)
            if app.desktop_file == preferred.name and preferred.exists():
                continue
            try:
                warnings += self._write_launcher(app)
            except OSError as e:
                warnings.append(f"Could not update the app grid entry for {app.name}: {e}")
            changed = True
        if changed:
            try:
                self._save()
            except OSError as e:
                warnings.append(f"Could not save settings: {e}")
        return warnings

    def _save(self) -> None:
        config.save_apps(self.paths.config_file, list(self._apps.values()))
