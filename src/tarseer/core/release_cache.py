"""The last GitHub reply per repository, kept on disk so the next check can
ask "changed since?" (with a token, a 304 answer costs no rate limit)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Iterable

from .models import Asset, Release

CACHE_VERSION = 1


class ReleaseCache:
    """Thread-safe {repo: Release}. Only releases with an ETag are worth keeping.

    It's a cache: an unreadable file starts it empty, and a failed write only
    means the next check sends full requests.

    The file is read on first use, not on creation, so that disk work happens
    in the worker thread doing the first lookup. `keep()`, if given, is then
    asked for the repositories still in use: the others are forgotten.
    """

    def __init__(self, path: Path, keep: Callable[[], Iterable[str]] | None = None):
        self.path = path
        self._lock = threading.Lock()
        self._keep = keep
        self._data: dict[str, Release] | None = None

    @property
    def _releases(self) -> dict[str, Release]:
        """The cached releases, read from disk the first time. Call with the lock held."""
        if self._data is None:
            self._data = self._load()
            if self._keep is not None:
                self._prune(set(self._keep()))
        return self._data

    def get(self, repo: str) -> Release | None:
        with self._lock:
            return self._releases.get(repo)

    def put(self, repo: str, release: Release) -> None:
        if not release.etag:
            return
        with self._lock:
            if self._releases.get(repo) is release:
                return  # GitHub said "not modified": nothing new to write
            self._releases[repo] = release
            self._save()

    def keep_only(self, repos: Iterable[str]) -> None:
        """Forget repositories no managed app uses any more."""
        keep = set(repos)
        with self._lock:
            self._releases  # noqa: B018 - load first, so that _prune sees the file
            self._prune(keep)

    def _prune(self, keep: set[str]) -> None:
        stale = [r for r in self._data if r not in keep]
        if stale:
            for repo in stale:
                del self._data[repo]
            self._save()

    def _load(self) -> dict[str, Release]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != CACHE_VERSION:
                return {}
            return {repo: _release_from_dict(item) for repo, item in data["releases"].items()}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            return {}

    def _save(self) -> None:
        payload = {"version": CACHE_VERSION,
                   "releases": {repo: asdict(r) for repo, r in self._data.items()}}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".releases-", suffix=".json")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
                os.replace(tmp, self.path)
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise
        except OSError:
            pass  # see the class docstring


def _release_from_dict(data: dict) -> Release:
    release = Release(**{k: v for k, v in data.items() if k != "assets"})
    release.assets = [Asset(**a) for a in data.get("assets", [])]
    if not isinstance(release.tag, str) or not release.etag:
        raise ValueError("incomplete cached release")
    return release
