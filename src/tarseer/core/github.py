"""Minimal GitHub Releases API client (stdlib only, blocking: call from a thread)."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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

# Optional personal access token, raising the API limit from 60 to 5000
# requests/hour. Sent only to the API, never with downloads: urllib keeps
# headers across redirects, which would leak it to the file host.
_token: str | None = None


def set_token(token: str | None) -> None:
    global _token
    _token = token.strip() if token and token.strip() else None


def has_token() -> bool:
    return _token is not None


class GitHubError(Exception):
    pass


class NotFoundError(GitHubError):
    pass


class RateLimitError(GitHubError):
    def __init__(self, reset_at: float | None, with_token: bool = False):
        self.reset_at = reset_at
        message = "GitHub API rate limit reached."
        if reset_at:
            minutes = max(1, round((reset_at - time.time()) / 60))
            message += f" Try again in about {minutes} min."
        if not with_token:
            message += " Adding a GitHub access token in Preferences raises the limit."
        super().__init__(message)


class TokenRejectedError(GitHubError):
    """GitHub answered 401: the access token is wrong, expired or revoked."""

    def __init__(self):
        super().__init__("GitHub rejected the access token. It may have expired or been "
                         "revoked: replace or remove it in Preferences.")


@dataclass(frozen=True)
class RateLimit:
    limit: int
    remaining: int
    reset_at: float  # Unix time


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


def _request(url: str, accept: str, token: str | None = None) -> urllib.request.Request:
    """`token` is only used for API URLs (see `_token`)."""
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if url.startswith(API + "/"):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def _translate_http_error(e: urllib.error.HTTPError, what: str, token: str | None = None) -> GitHubError:
    if e.code == 401 and token:  # without a token, 401 isn't about ours
        return TokenRejectedError()
    if e.code in (403, 429) and e.headers.get("x-ratelimit-remaining") == "0":
        reset = e.headers.get("x-ratelimit-reset")
        return RateLimitError(float(reset) if reset else None, with_token=bool(token))
    if e.code == 404:
        return NotFoundError(f"{what} not found")
    return GitHubError(f"GitHub returned HTTP {e.code} for {what}")


def get_latest_release(repo: str, timeout: float = 15) -> Release:
    """Latest stable release (GitHub's /latest excludes drafts and pre-releases)."""
    url = f"{API}/repos/{repo}/releases/latest"
    token = _token
    try:
        with _urlopen(_request(url, "application/vnd.github+json", token), timeout=timeout) as resp:
            return release_from_json(json.load(resp))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFoundError(f"{repo} has no releases, or the repository does not exist") from e
        raise _translate_http_error(e, f"Latest release of {repo}", token) from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Network error: {e.reason}") from e
    except (ValueError, KeyError) as e:
        raise GitHubError(f"Unexpected response from GitHub: {e}") from e


def get_rate_limit(token: str | None = None, timeout: float = 15) -> RateLimit:
    """Core API quota for `token` (default: the one set with set_token).
    Doesn't count against the limit; raises TokenRejectedError for a bad token."""
    token = token.strip() if token else _token
    try:
        with _urlopen(_request(f"{API}/rate_limit", "application/vnd.github+json", token),
                      timeout=timeout) as resp:
            core = json.load(resp)["resources"]["core"]
            return RateLimit(int(core["limit"]), int(core["remaining"]), float(core["reset"]))
    except urllib.error.HTTPError as e:
        raise _translate_http_error(e, "Rate limit", token) from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Network error: {e.reason}") from e
    except (ValueError, KeyError, TypeError) as e:
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
