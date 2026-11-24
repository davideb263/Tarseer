import email.message
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
from tarseer.core.models import ManagedApp, Paths


class PathsTest(unittest.TestCase):
    def test_dev_root_override(self):
        with mock.patch.dict("os.environ", {"TARSEER_DEV_ROOT": "/dev-root"}):
            paths = Paths.default()
        self.assertEqual(paths.install_root, Path("/dev-root/opt"))
        self.assertEqual(paths.config_file, Path("/dev-root/config/apps.json"))

    def test_real_paths_ignore_xdg_data_home(self):
        env = {"XDG_DATA_HOME": "/sandbox/data", "XDG_CONFIG_HOME": "/sandbox/config"}
        with mock.patch.dict("os.environ", env), mock.patch.dict("os.environ", {}, clear=False) as e:
            e.pop("TARSEER_DEV_ROOT", None)
            paths = Paths.default()
        self.assertEqual(paths.applications_dir, Path.home() / ".local/share/applications")
        self.assertEqual(paths.config_file, Path("/sandbox/config/tarseer/apps.json"))


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
        with mock.patch.object(github, "_urlopen", return_value=io.BytesIO(body)):
            rel = github.get_latest_release("o/r")
        self.assertEqual(rel.tag, "v8.1.0")
        self.assertEqual(rel.assets[0].url, "https://d/c.txz")

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
