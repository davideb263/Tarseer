import email.message
import hashlib
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from tarseer.core import config, github
from tarseer.core.models import ManagedApp, Paths, Release


class PathsTest(unittest.TestCase):
    def test_dev_root_override(self):
        with mock.patch.dict("os.environ", {"TARSEER_DEV_ROOT": "/dev-root"}):
            paths = Paths.default()
        self.assertEqual(paths.install_root, Path("/dev-root/opt"))
        self.assertEqual(paths.config_file, Path("/dev-root/config/apps.json"))
        self.assertEqual(paths.cache_file, Path("/dev-root/cache/releases.json"))

    def test_real_paths_ignore_xdg_data_home(self):
        env = {"XDG_DATA_HOME": "/sandbox/data", "XDG_CONFIG_HOME": "/sandbox/config",
               "XDG_CACHE_HOME": "/sandbox/cache"}
        with mock.patch.dict("os.environ", env), mock.patch.dict("os.environ", {}, clear=False) as e:
            e.pop("TARSEER_DEV_ROOT", None)
            paths = Paths.default()
        self.assertEqual(paths.applications_dir, Path.home() / ".local/share/applications")
        self.assertEqual(paths.config_file, Path("/sandbox/config/tarseer/apps.json"))
        self.assertEqual(paths.cache_file, Path("/sandbox/cache/tarseer/releases.json"))

    def test_cache_file_defaults_next_to_config(self):
        paths = Paths(Path("/o"), Path("/a"), Path("/c/apps.json"))
        self.assertEqual(paths.cache_file, Path("/c/releases-cache.json"))


class ConfigTest(unittest.TestCase):
    def test_roundtrip_missing_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "sub" / "apps.json"
            self.assertEqual(config.load_apps(path), [])
            app = ManagedApp(id="a", name="A", repo="o/r", asset_pattern="^a$", installed_tag="v1")
            config.save_apps(path, [app])
            self.assertEqual(config.load_apps(path), [app])

            data = json.loads(path.read_text())
            data["apps"][0]["from_a_future_version"] = True
            path.write_text(json.dumps(data))
            self.assertEqual(config.load_apps(path), [app])

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "apps.json"
            path.write_text("{nope")
            with self.assertRaises(config.ConfigError):
                config.load_apps(path)

    def test_invalid_app_ids_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "apps.json"
            for bad in ("", "..", "../x", "a/b", "app.bak", ".hidden", "Caps", None):
                with self.subTest(bad):
                    path.write_text(json.dumps({"apps": [
                        {"id": bad, "name": "A", "repo": "o/r", "asset_pattern": "x"}]}))
                    with self.assertRaises(config.ConfigError):
                        config.load_apps(path)


def http_error(code, headers=None):
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError("https://api.github.com/x", code, "err", msg, io.BytesIO(b""))


class GitHubTest(unittest.TestCase):
    def test_parse_repo(self):
        for text in ("kovidgoyal/calibre", " https://github.com/kovidgoyal/calibre ",
                     "github.com/kovidgoyal/calibre.git", "https://github.com/kovidgoyal/calibre/releases/latest"):
            self.assertEqual(github.parse_repo(text), "kovidgoyal/calibre", text)
        for text in ("calibre", "https://gitlab.com/a", "a/b/c", ""):
            with self.assertRaises(ValueError, msg=text):
                github.parse_repo(text)

    def test_latest_release_parsed(self):
        body = json.dumps({
            "tag_name": "v8.1.0", "name": "8.1", "html_url": "https://h",
            "assets": [{"name": "c.txz", "browser_download_url": "https://d/c.txz", "size": 3}],
        }).encode()
        resp = io.BytesIO(body)
        resp.headers = {"ETag": 'W/"abc"'}
        with mock.patch.object(github, "_urlopen", return_value=resp) as urlopen:
            rel = github.get_latest_release("o/r")
        self.assertEqual(rel.tag, "v8.1.0")
        self.assertEqual(rel.assets[0].url, "https://d/c.txz")
        self.assertEqual(rel.etag, 'W/"abc"')
        self.assertIsNone(urlopen.call_args.args[0].get_header("If-none-match"))

    def test_latest_release_conditional(self):
        cached = Release(tag="v1", etag='W/"abc"')
        requests = []

        def not_modified(req, timeout=None):
            requests.append(req)
            raise http_error(304)

        with mock.patch.object(github, "_urlopen", not_modified):
            self.assertIs(github.get_latest_release("o/r", cached=cached), cached)
        self.assertEqual(requests[0].get_header("If-none-match"), 'W/"abc"')

        # A cached reply without an ETag can't be revalidated: plain request.
        resp = io.BytesIO(json.dumps({"tag_name": "v2", "assets": []}).encode())
        resp.headers = {}
        with mock.patch.object(github, "_urlopen", return_value=resp) as urlopen:
            rel = github.get_latest_release("o/r", cached=Release(tag="v1"))
        self.assertEqual((rel.tag, rel.etag), ("v2", None))
        self.assertIsNone(urlopen.call_args.args[0].get_header("If-none-match"))

        # A 304 without a cached reply (can't happen with GitHub) is an error.
        with mock.patch.object(github, "_urlopen", side_effect=http_error(304)), \
                self.assertRaises(github.GitHubError):
            github.get_latest_release("o/r")

    def test_download_cancel(self):
        body = io.BytesIO(b"x" * (github.CHUNK * 3))
        body.headers = {"Content-Length": str(github.CHUNK * 3)}
        cancel = threading.Event()
        seen = []

        def progress(done, total):
            seen.append(done)
            cancel.set()

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(github, "_urlopen", return_value=body), \
                self.assertRaises(github.CancelledError):
            github.download("https://d/x", Path(tmp) / "x", progress=progress, cancel=cancel)
        self.assertEqual(seen, [github.CHUNK])

    def test_token_only_sent_to_api(self):
        requests = []

        def fake_urlopen(req, timeout=None):
            requests.append(req)
            resp = io.BytesIO(json.dumps({"tag_name": "v1", "assets": []}).encode())
            resp.headers = {}
            return resp

        self.addCleanup(github.set_token, None)
        github.set_token("  ghp_secret \n")
        self.assertTrue(github.has_token())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(github, "_urlopen", fake_urlopen):
            github.get_latest_release("o/r")
            github.download("https://github.com/o/r/releases/download/v1/x.tar.gz", Path(tmp) / "x")
        api, dl = requests
        self.assertEqual(api.get_header("Authorization"), "Bearer ghp_secret")
        self.assertIsNone(dl.get_header("Authorization"))
        github.set_token("   ")
        self.assertFalse(github.has_token())

    def test_release_digest_parsed(self):
        rel = github.release_from_json({"tag_name": "v1", "assets": [
            {"name": "a", "browser_download_url": "https://d/a", "digest": "sha256:ab"},
            {"name": "b", "browser_download_url": "https://d/b", "digest": None},
        ]})
        self.assertEqual([a.digest for a in rel.assets], ["sha256:ab", None])

    def download_bytes(self, data, **kwargs):
        body = io.BytesIO(data)
        body.headers = {}
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(github, "_urlopen", return_value=body):
            dest = Path(tmp) / "x"
            github.download("https://d/x", dest, **kwargs)
            return dest.read_bytes()

    def test_download_verified_against_size_and_digest(self):
        data = b"x" * (github.CHUNK + 10)
        good = "sha256:" + hashlib.sha256(data).hexdigest().upper()
        self.assertEqual(self.download_bytes(data, size=len(data), digest=good), data)
        self.assertEqual(self.download_bytes(data, digest="md5:whatever"), data)  # unknown: skipped
        for name, kwargs in {
            "truncated": dict(size=len(data) + 1),
            "too_long": dict(size=len(data) - 1),
            "tampered": dict(digest="sha256:" + "0" * 64),
        }.items():
            with self.subTest(name), self.assertRaises(github.GitHubError):
                self.download_bytes(data, **kwargs)

    def test_only_https(self):
        with self.assertRaises(github.GitHubError):
            github.download("http://github.com/o/r/x.tar.gz", Path(os.devnull))
        # The opener itself can't read local files either.
        with self.assertRaises(urllib.error.URLError):
            github._opener.open("file:///etc/hostname")

    def test_redirects_https_only_and_token_stays_on_host(self):
        handler = github._SafeRedirectHandler()
        req = github._request(f"{github.API}/repos/o/r/releases/latest", "a", "ghp_secret")
        follow = lambda url: handler.redirect_request(req, None, 301, "Moved", {}, url)  # noqa: E731
        same = follow(f"{github.API}/repositories/1/releases/latest")
        self.assertEqual(same.get_header("Authorization"), "Bearer ghp_secret")
        other = follow("https://example.com/x")
        self.assertIsNone(other.get_header("Authorization"))
        self.assertEqual(other.get_header("User-agent"), github.USER_AGENT)
        for insecure in ("http://api.github.com/x", "ftp://example.com/x", "file:///etc/passwd"):
            with self.subTest(insecure), self.assertRaises(github.GitHubError):
                follow(insecure)

    def test_rate_limit(self):
        body = json.dumps({"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 1700000000}}})
        seen = []

        def fake_urlopen(req, timeout=None):
            seen.append(req.get_header("Authorization"))
            return io.BytesIO(body.encode())

        with mock.patch.object(github, "_urlopen", fake_urlopen):
            limit = github.get_rate_limit("ghp_candidate")
        self.assertEqual(limit, github.RateLimit(5000, 4999, 1700000000.0))
        self.assertEqual(seen, ["Bearer ghp_candidate"])

    def test_rejected_token_and_rate_limit_hint(self):
        err = http_error(401)
        self.addCleanup(err.close)
        with mock.patch.object(github, "_urlopen", side_effect=err), \
                self.assertRaises(github.TokenRejectedError):
            github.get_rate_limit("bad")
        # Without a token (e.g. downloads), a 401 is not blamed on the token.
        err = http_error(401)
        self.addCleanup(err.close)
        with mock.patch.object(github, "_urlopen", side_effect=err), \
                self.assertRaises(github.GitHubError) as ctx:
            github.download("https://example.com/f.tar.gz", Path(os.devnull))
        self.assertNotIsInstance(ctx.exception, github.TokenRejectedError)
        self.assertIn("Preferences", str(github.RateLimitError(None)))
        self.assertNotIn("Preferences", str(github.RateLimitError(None, with_token=True)))

    def test_errors_translated(self):
        cases = [
            (http_error(404), github.NotFoundError),
            (http_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "0"}), github.RateLimitError),
            (http_error(500), github.GitHubError),
            (urllib.error.URLError("no route"), github.GitHubError),
        ]
        for exc, _expected in cases:
            if isinstance(exc, urllib.error.HTTPError):
                self.addCleanup(exc.close)
        for exc, expected in cases:
            with self.subTest(expected.__name__), mock.patch.object(github, "_urlopen", side_effect=exc):
                with self.assertRaises(expected):
                    github.get_latest_release("o/r")


if __name__ == "__main__":
    unittest.main()
