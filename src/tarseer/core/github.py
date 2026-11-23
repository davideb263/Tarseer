"""Minimal GitHub Releases API client (stdlib only, blocking: call from a thread)."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from .models import Asset, Release

API = "https://api.github.com"
USER_AGENT = "Tarseer"
CHUNK = 64 * 1024

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)")

# Indirection so tests can patch the network.
_urlopen = urllib.request.urlopen


class GitHubError(Exception):
    pass


class NotFoundError(GitHubError):
    pass


class RateLimitError(GitHubError):
    def __init__(self, reset_at: float | None):
        self.reset_at = reset_at
        when = ""
        if reset_at:
            minutes = max(1, round((reset_at - time.time()) / 60))
            when = f" Try again in about {minutes} min."
        super().__init__("GitHub API rate limit reached." + when)


class CancelledError(Exception):
    pass


def parse_repo(text: str) -> str:
    """Accept "owner/repo" or any github.com URL and return "owner/repo"."""
    text = text.strip()
    m = _URL_RE.match(text)
    if m:
        owner, repo = m.group(1), m.group(2)
        repo = repo.removesuffix(".git")
        text = f"{owner}/{repo}"
    if not _REPO_RE.match(text):
        raise ValueError(f"Not a GitHub repository: {text!r}")
    return text


def release_from_json(data: dict) -> Release:
    return Release(
        tag=data["tag_name"],
        name=data.get("name") or "",
        html_url=data.get("html_url") or "",
        assets=[
            Asset(name=a["name"], url=a["browser_download_url"], size=a.get("size") or 0)
            for a in data.get("assets", [])
        ],
    )


def _request(url: str, accept: str) -> urllib.request.Request:
    return urllib.request.Request(url, headers={
        "Accept": accept,
        "User-Agent": USER_AGENT,
        "X-GitHub-Api-Version": "2022-11-28",
    })


def _translate_http_error(e: urllib.error.HTTPError, what: str) -> GitHubError:
    if e.code in (403, 429) and e.headers.get("x-ratelimit-remaining") == "0":
        reset = e.headers.get("x-ratelimit-reset")
        return RateLimitError(float(reset) if reset else None)
    if e.code == 404:
        return NotFoundError(f"{what} not found")
    return GitHubError(f"GitHub returned HTTP {e.code} for {what}")


def get_latest_release(repo: str, timeout: float = 15) -> Release:
    """Latest stable release (GitHub's /latest excludes drafts and pre-releases)."""
    url = f"{API}/repos/{repo}/releases/latest"
    try:
        with _urlopen(_request(url, "application/vnd.github+json"), timeout=timeout) as resp:
            return release_from_json(json.load(resp))
    except urllib.error.HTTPError as e:
        raise _translate_http_error(e, f"Latest release of {repo}") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Network error: {e.reason}") from e
    except (ValueError, KeyError) as e:
        raise GitHubError(f"Unexpected response from GitHub: {e}") from e


def download(
    url: str,
    dest: Path,
    progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
    timeout: float = 30,
) -> None:
    """Stream `url` into `dest`. progress(done_bytes, total_bytes or 0)."""
    try:
        with _urlopen(_request(url, "application/octet-stream"), timeout=timeout) as resp, \
                open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                out.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    except urllib.error.HTTPError as e:
        raise _translate_http_error(e, "Download") from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Network error: {e.reason}") from e
