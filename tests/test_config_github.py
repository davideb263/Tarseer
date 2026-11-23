import email.message
import io
import json
import tempfile
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
