"""High-level operations the UI calls. Blocking: run them in a worker thread."""

from __future__ import annotations

import os
import re
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import config, desktop, github, installer
from .assets import match_asset, validate_pattern
from .desktop import DesktopConflictError
from .i18n import _
from .installer import InstallError
from .release_cache import ReleaseCache
from .models import CheckResult, ManagedApp, Paths, Release, is_valid_id

# Parallel release lookups in check_updates.
CHECK_WORKERS = 4
# Requests left untouched by a background check, so the user can still add an
# app or check by hand right after it.
QUOTA_MARGIN = 10


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
        # Latest check result per app id (this session only).
        self._checks: dict[str, CheckResult] = {}
        # Read (and stale repositories pruned) on the first lookup, in a worker thread.
        self._release_cache = ReleaseCache(self.paths.cache_file, keep=self._repos_in_use)
        # Messages for the UI about menu entries that could not be set up.
        self.startup_warnings: list[str] = self._sync_launchers()

    # ── Queries ──────────────────────────────────────────────────────────────
    @property
    def apps(self) -> list[ManagedApp]:
        return sorted(self._apps.values(), key=lambda a: a.name.lower())

    def get(self, app_id: str) -> ManagedApp | None:
        return self._apps.get(app_id)

    def install_dir(self, app: ManagedApp) -> Path:
        # An id like "" or "../x" would point at the install root itself, or
        # outside it: remove() would then delete far more than one app.
        if not is_valid_id(app.id):
            raise InstallError(_("Invalid app id: “{id}”").format(id=app.id))
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

    def last_check(self, app_id: str) -> CheckResult | None:
        """The latest check result for the app, if it was checked this session."""
        with self._lock:
            return self._checks.get(app_id)

    def record_release(self, app_id: str, release: Release) -> None:
        """Store a release fetched outside check_updates (install, repository change)."""
        with self._lock:
            self._checks[app_id] = CheckResult(release=release, checked_at=time.time())

    @property
    def updates(self) -> list[ManagedApp]:
        """Apps whose last check found a release other than the installed one."""
        with self._lock:
            checks = dict(self._checks)
        return [a for a in self.apps
                if (c := checks.get(a.id)) and c.release and self.update_available(a, c.release)]

    def _repos_in_use(self) -> list[str]:
        with self._lock:
            return [a.repo for a in self._apps.values()]

    # ── Network ──────────────────────────────────────────────────────────────
    def fetch_latest(self, repo: str) -> Release:
        """Latest release of `repo`. Asks GitHub "changed since?" when an earlier
        reply is cached: with a token that costs no rate limit if it didn't."""
        release = github.get_latest_release(repo, cached=self._release_cache.get(repo))
        self._release_cache.put(repo, release)
        return release

    def has_quota_for(self, apps: list[ManagedApp] | None = None) -> bool:
        """Whether checking `apps` (default: all) leaves QUOTA_MARGIN requests of
        the GitHub API limit. Counts every app as a request: without a token even
        a "not modified" reply uses one.
        Raises like rate_limit()."""
        count = len(self._apps) if apps is None else len(apps)
        return github.get_rate_limit().remaining >= count + QUOTA_MARGIN

    def check_if_quota(self) -> dict[str, CheckResult] | None:
        """For unattended checks: check every app, or return None without
        checking if that would leave less than QUOTA_MARGIN requests."""
        if not self.has_quota_for():
            return None
        return self.check_updates()

    def check_updates(
        self,
        apps: list[ManagedApp] | None = None,
        on_result: Callable[[ManagedApp, CheckResult], None] | None = None,
        workers: int = CHECK_WORKERS,
    ) -> dict[str, CheckResult]:
        """Look up the latest release of `apps` (default: all), a few at a time.

        Each result is cached (see last_check) and passed to `on_result` from a
        worker thread as soon as it is known. A rate limit or rejected token is
        the same for every app: once one is hit, the apps not yet looked up get
        that error without sending a request.
        """
        apps = self.apps if apps is None else list(apps)
        fatal: list[Exception] = []  # the error every remaining app gets
        fatal_lock = threading.Lock()

        def check(app: ManagedApp) -> CheckResult:
            repo = app.repo  # set_repo may change it while the request is out
            with fatal_lock:
                error = fatal[0] if fatal else None
            if error is None:
                try:
                    result = CheckResult(release=self.fetch_latest(repo), checked_at=time.time())
                except Exception as e:  # noqa: BLE001 - reported per app
                    if isinstance(e, (github.RateLimitError, github.TokenRejectedError)):
                        with fatal_lock:
                            fatal.append(e)
                    result = CheckResult(error=e, checked_at=time.time())
            else:
                result = CheckResult(error=error, checked_at=time.time())
            with self._lock:
                # Not removed meanwhile, nor moved to another repository (whose
                # release set_repo's caller already recorded).
                current = self._apps.get(app.id)
                if current is not None and current.repo == repo:
                    self._checks[app.id] = result
            if on_result is not None:
                on_result(app, result)
            return result

        if not apps:
            return {}
        with ThreadPoolExecutor(max_workers=max(1, min(workers, len(apps)))) as pool:
            return dict(zip((a.id for a in apps), pool.map(check, apps)))

    @staticmethod
    def rate_limit(token: str | None = None) -> github.RateLimit:
        """API quota for `token`, or for the token in use. Validates a token too."""
        return github.get_rate_limit(token)

    @staticmethod
    def set_token(token: str | None) -> None:
        """Use `token` (None: none) for GitHub API requests from now on."""
        github.set_token(token)

    @staticmethod
    def has_token() -> bool:
        return github.has_token()

    # ── Mutations ────────────────────────────────────────────────────────────
    def install(
        self,
        app: ManagedApp,
        release: Release,
        progress: Callable[[int, int], None] | None = None,
        cancel: threading.Event | None = None,
        extracting: Callable[[], None] | None = None,
    ) -> list[str]:
        """Install or update `app` to `release`, registering it if new.

        Returns warnings (e.g. the menu entry could not be created); the
        install itself succeeded if this returns. Setting `cancel` during the
        download raises github.CancelledError and leaves the installed version as is;
        once extraction has started it can no longer be cancelled: `extracting()` is
        called (from this thread) right before that point. A new app that cannot be
        saved to the config is removed again, so a failed install leaves nothing behind.
        """
        target = self.install_dir(app)  # validates the id
        asset = match_asset(release.assets, app.asset_pattern)
        if asset is None:
            raise InstallError(
                _("No file in release {tag} matches the pattern {pattern}").format(
                    tag=release.tag, pattern=app.asset_pattern)
            )

        root = self.paths.install_root
        root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=root, prefix=".tarseer-dl-")
        archive = Path(tmp_name)
        try:
            os.close(fd)
            github.download(asset.url, archive, progress=progress, cancel=cancel,
                            size=asset.size, digest=asset.digest)
            if cancel is not None and cancel.is_set():
                # Cancelled on the last chunk: don't go on and replace the installed version.
                raise github.CancelledError()
            if extracting is not None:
                extracting()
            # Reinstalling the same version (e.g. another release file): keep
            # the backup, which holds an older version than the current files.
            reinstall = app.id in self._apps and app.installed_tag == release.tag
            new_backup = installer.install_archive(archive, target, keep_backup=reinstall)
        finally:
            archive.unlink(missing_ok=True)

        with self._lock:
            if new_backup:
                app.previous_tag = app.installed_tag
            elif not self.can_rollback(app):
                app.previous_tag = None
            app.installed_tag = release.tag
            self._revalidate_launcher_paths(app)
            try:
                warnings = self._write_launcher(app)
            except OSError as e:
                warnings = [_("Could not create the app grid entry for {name}: {error}").format(
                    name=app.name, error=e)]
            is_new = app.id not in self._apps
            self._apps[app.id] = app
            try:
                self._save()
            except OSError as e:
                if not is_new:
                    # The new version is in place; the next successful save records it.
                    return warnings + [
                        _("{name} was updated to {tag}, but its settings could not be saved "
                          "({error}). After a restart it may show the old version.").format(
                            name=app.name, tag=release.tag, error=e)
                    ]
                self._apps.pop(app.id)
                self._discard(app)
                raise InstallError(_("Could not save settings: {error}").format(error=e)) from e
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
            self._checks.pop(app.id, None)
            self._save()

    def set_launcher(self, app: ManagedApp, executable: str | None, icon: str | None) -> None:
        target = self.install_dir(app)
        for rel in (executable, icon):
            if rel is None:
                continue
            if not desktop.is_safe_relpath(rel):
                raise InstallError(_("“{name}” can't be used in an app grid entry").format(name=rel))
            path = (target / rel).resolve()
            if not path.is_relative_to(target.resolve()) or not path.is_file():
                raise InstallError(_("{name} is not a file inside {folder}").format(name=rel, folder=target))
        with self._lock:
            app.executable, app.icon = executable, icon
            try:
                self._write_launcher(app, strict=True)
            finally:
                self._save()

    def set_name(self, app: ManagedApp, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError(_("The name cannot be empty"))
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
        if not app.executable or not self._usable(target, app.executable):
            app.executable = desktop.guess_executable(target, app.name)
        if app.icon and not self._usable(target, app.icon):
            app.icon = None
        if not app.icon:
            app.icon = desktop.guess_icon(target, app.name)

    @staticmethod
    def _usable(target: Path, rel: str) -> bool:
        """`rel` is still a file inside `target` that a menu entry can name."""
        path = (target / rel).resolve()
        return (desktop.is_safe_relpath(rel) and path.is_relative_to(target.resolve())
                and path.is_file())

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
                warnings.append(_("Could not update the app grid entry for {name}: {error}").format(
                    name=app.name, error=e))
            changed = True
        if changed:
            try:
                self._save()
            except OSError as e:
                warnings.append(_("Could not save settings: {error}").format(error=e))
        return warnings

    def _save(self) -> None:
        config.save_apps(self.paths.config_file, list(self._apps.values()))
