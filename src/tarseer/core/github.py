"""Minimal GitHub Releases API client (stdlib only, blocking: call from a thread)."""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .i18n import _
from .models import Asset, Release

API = "https://api.github.com"
USER_AGENT = "Tarseer"
CHUNK = 64 * 1024

_REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_URL_RE = re.compile(r"^(?:https?://)?(?:www\.)?github\.com/([^/\s]+)/([^/\s#?]+)")

# Optional personal access token, raising the API limit from 60 to 5000
# requests/hour. Sent only to the API, never with downloads (they redirect to
# a file host); _SafeRedirectHandler also drops it if the API ever redirects
# to another host.
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
        message = _("GitHub API rate limit reached.")
        if reset_at:
            minutes = max(1, round((reset_at - time.time()) / 60))
            message += " " + _("Try again in about {minutes} min.").format(minutes=minutes)
        if not with_token:
            message += " " + _("Adding a GitHub access token in Preferences raises the limit.")
        super().__init__(message)


class TokenRejectedError(GitHubError):
    """GitHub answered 401: the access token is wrong, expired or revoked."""

    def __init__(self):
        super().__init__(_("GitHub rejected the access token. It may have expired or been "
                           "revoked: replace or remove it in Preferences."))


@dataclass(frozen=True)
class RateLimit:
    limit: int
    remaining: int
    reset_at: float  # Unix time


class CancelledError(Exception):
    pass


def _host(url: str) -> str | None:
    return urllib.parse.urlsplit(url).hostname


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib's default follows redirects to http:// and ftp:// and keeps every
    header, Authorization included, even when the host changes."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise GitHubError(_("Refused an insecure redirect to {url}").format(url=newurl))
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and _host(newurl) != _host(req.full_url):
            new.remove_header("Authorization")
        return new


# HTTPS only: no file://, ftp:// or data: handlers to reach by accident.
# (ProxyHandler keeps honouring https_proxy like urlopen did.)
_opener = urllib.request.OpenerDirector()
for _handler in (urllib.request.ProxyHandler(), urllib.request.UnknownHandler(),
                 urllib.request.HTTPSHandler(), _SafeRedirectHandler(),
                 urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor()):
    _opener.add_handler(_handler)

# Indirection so tests can patch the network.
_urlopen = _opener.open


def parse_repo(text: str) -> str:
    """Accept "owner/repo" or any github.com URL and return "owner/repo"."""
    text = text.strip()
    m = _URL_RE.match(text)
    if m:
        owner, repo = m.group(1), m.group(2)
        repo = repo.removesuffix(".git")
        text = f"{owner}/{repo}"
    if not _REPO_RE.match(text):
        raise ValueError(_("Not a GitHub repository: {text!r}").format(text=text))
    return text


def release_from_json(data: dict) -> Release:
    return Release(
        tag=data["tag_name"],
        name=data.get("name") or "",
        html_url=data.get("html_url") or "",
        assets=[
            Asset(name=a["name"], url=a["browser_download_url"], size=a.get("size") or 0,
                  digest=a.get("digest") or None)
            for a in data.get("assets", [])
        ],
    )


def _request(url: str, accept: str, token: str | None = None) -> urllib.request.Request:
    """`token` is only used for API URLs (see `_token`)."""
    if urllib.parse.urlsplit(url).scheme != "https":
        raise GitHubError(_("Refused to download from a non-HTTPS address: {url}").format(url=url))
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
        return NotFoundError(_("{what} not found").format(what=what))
    return GitHubError(_("GitHub returned HTTP {code} for {what}").format(code=e.code, what=what))


def get_latest_release(repo: str, timeout: float = 15, cached: Release | None = None) -> Release:
    """Latest stable release (GitHub's /latest excludes drafts and pre-releases).

    With a `cached` reply for the same repo that has an ETag, the request is
    conditional: if nothing changed GitHub answers 304 and `cached` itself is
    returned. With a token, a 304 doesn't count against the rate limit (GitHub's
    docs); without one it does (checked 2026-11-24), but the reply is tiny.
    """
    url = f"{API}/repos/{repo}/releases/latest"
    token = _token
    req = _request(url, "application/vnd.github+json", token)
    if cached is not None and cached.etag:
        req.add_header("If-None-Match", cached.etag)
    try:
        with _urlopen(req, timeout=timeout) as resp:
            release = release_from_json(json.load(resp))
            release.etag = resp.headers.get("ETag") or None
            return release
    except urllib.error.HTTPError as e:
        if e.code == 304 and cached is not None:
            e.close()  # the usual reply once cached: don't leave its connection to GC
            return cached
        if e.code == 404:
            raise NotFoundError(_("{repo} has no releases, or the repository does not exist").format(repo=repo)) from e
        raise _translate_http_error(e, _("Latest release of {repo}").format(repo=repo), token) from e
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
        raise _translate_http_error(e, _("Rate limit"), token) from e
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
    size: int = 0,
    digest: str | None = None,
) -> None:
    """Stream `url` into `dest`. progress(done_bytes, total_bytes or 0).

    `size` and `digest` ("sha256:<hex>") are what the API says the file is;
    when given, a download that doesn't match raises GitHubError.
    """
    algorithm, _sep, expected_hash = (digest or "").partition(":")
    hasher = hashlib.sha256() if algorithm == "sha256" and expected_hash else None
    try:
        with _urlopen(_request(url, "application/octet-stream"), timeout=timeout) as resp, \
                open(dest, "wb") as out:
            total = size or int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(CHUNK):
                if cancel is not None and cancel.is_set():
                    raise CancelledError()
                done += len(chunk)
                if size and done > size:
                    raise GitHubError(_("Download is larger than the {size} bytes GitHub announced").format(size=size))
                out.write(chunk)
                if hasher:
                    hasher.update(chunk)
                if progress:
                    progress(done, total)
        if total and done != total:
            raise GitHubError(_("Download incomplete: got {done} of {total} bytes").format(done=done, total=total))
        if hasher and hasher.hexdigest() != expected_hash.lower():
            raise GitHubError(_("The downloaded file doesn't match the checksum GitHub published "
                                "for it. Try again later."))
    except urllib.error.HTTPError as e:
        raise _translate_http_error(e, _("Download")) from e
    except urllib.error.URLError as e:
        raise GitHubError(f"Network error: {e.reason}") from e
