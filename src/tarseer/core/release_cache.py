"""The last GitHub reply per repository, kept on disk so the next check can
ask "changed since?" (with a token, a 304 answer costs no rate limit)."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import Asset, Release

CACHE_VERSION = 1


class ReleaseCache:
    """Thread-safe {repo: Release}. Only releases with an ETag are worth keeping.

    It's a cache: an unreadable file starts it empty, and a failed write only
    means the next check sends full requests.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._releases: dict[str, Release] = self._load()

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
            stale = [r for r in self._releases if r not in keep]
            if stale:
                for repo in stale:
                    del self._releases[repo]
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
                   "releases": {repo: asdict(r) for repo, r in self._releases.items()}}
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
