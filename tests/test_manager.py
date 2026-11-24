import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tarseer.core import config, desktop, github
from tarseer.core.assets import pattern_from_name
from tarseer.core.installer import InstallError
from tarseer.core.manager import AppManager, slugify
from tarseer.core.models import Asset, ManagedApp, Paths, Release
from tarseer.core.release_cache import ReleaseCache

from .helpers import ELF, PNG_256, make_tarball


class ManagerTest(unittest.TestCase):
    """End-to-end through the manager with the network replaced by local files."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.paths = Paths(
            install_root=self.tmp / "opt",
            applications_dir=self.tmp / "applications",
            config_file=self.tmp / "config" / "apps.json",
        )
        self.archives = {}
        patcher = mock.patch.object(github, "download", side_effect=self.fake_download)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def fake_download(self, url, dest, progress=None, cancel=None, size=0, digest=None):
        self.download_args = (size, digest)
        shutil.copyfile(self.archives[url], dest)
        if progress:
            progress(1, 1)

    def release(self, version, extra=None):
        name = f"sparrowwallet-{version}-x86_64.tar.gz"
        files = {"Sparrow/bin/Sparrow": ELF, "Sparrow/lib/Sparrow.png": PNG_256, **(extra or {})}
        self.archives[name] = make_tarball(self.tmp / name, files, executables=("Sparrow/bin/Sparrow",))
        return Release(tag=version, assets=[Asset(name=name, url=name)])

    def new_app(self, manager):
        return ManagedApp(
            id=manager.unique_id("Sparrow"), name="Sparrow", repo="sparrowwallet/sparrow",
            asset_pattern=pattern_from_name("sparrowwallet-2.4.2-x86_64.tar.gz", "2.4.2"),
        )

    def test_install_update_rollback_remove(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        self.assertEqual(manager.install(app, self.release("2.4.2")), [])
        self.assertEqual((app.id, app.installed_tag, app.previous_tag), ("sparrow", "2.4.2", None))
        self.assertEqual((app.executable, app.icon), ("bin/Sparrow", "lib/Sparrow.png"))
        entry = self.paths.applications_dir / "sparrow.desktop"
        self.assertIn(str(self.paths.install_root / "sparrow" / "bin" / "Sparrow"), entry.read_text())
        self.assertFalse(manager.can_rollback(app))

        # Config persisted: a fresh manager sees the app.
        self.assertEqual(AppManager(self.paths).get("sparrow").installed_tag, "2.4.2")

        newer = self.release("2.5.0")
        self.assertTrue(manager.update_available(app, newer))
        extracting = []
        manager.install(app, newer, extracting=lambda: extracting.append(app.installed_tag))
        self.assertEqual(extracting, ["2.4.2"])  # called once, before the swap
        self.assertEqual((app.installed_tag, app.previous_tag), ("2.5.0", "2.4.2"))
        self.assertFalse(manager.update_available(app, newer))

        manager.rollback(app)
        self.assertEqual((app.installed_tag, app.previous_tag), ("2.4.2", "2.5.0"))

        manager.remove(app)
        self.assertEqual(manager.apps, [])
        self.assertFalse(entry.exists())
        self.assertEqual(list(self.paths.install_root.iterdir()), [])
        self.assertEqual(AppManager(self.paths).apps, [])

    def test_new_app_rolled_back_when_config_cannot_be_saved(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        with mock.patch.object(config, "save_apps", side_effect=PermissionError("read-only")):
            with self.assertRaises(InstallError):
                manager.install(app, self.release("2.4.2"))
        self.assertEqual(manager.apps, [])
        self.assertEqual(list(self.paths.install_root.iterdir()), [])
        self.assertEqual(list(self.paths.applications_dir.glob("*.desktop")), [])
        self.assertEqual(manager.unique_id("Sparrow"), "sparrow")  # a retry gets the same id

    def test_menu_entry_failure_is_a_warning(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        with mock.patch.object(desktop, "write_desktop_entry", side_effect=PermissionError("denied")):
            warnings = manager.install(app, self.release("2.4.2"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("denied", warnings[0])
        self.assertEqual(AppManager(self.paths).get("sparrow").installed_tag, "2.4.2")

    def test_update_kept_with_warning_when_config_cannot_be_saved(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        with mock.patch.object(config, "save_apps", side_effect=PermissionError("read-only")):
            warnings = manager.install(app, self.release("2.5.0"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("read-only", warnings[0])
        self.assertEqual(manager.get("sparrow").installed_tag, "2.5.0")
        self.assertTrue(manager.can_rollback(app))

    def test_rollback_revalidates_launcher_paths(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        # 2.5.0 renames the binary and drops the old one; the user picks the new name.
        name = "sparrowwallet-2.5.0-x86_64.tar.gz"
        self.archives[name] = make_tarball(
            self.tmp / name, {"Sparrow/bin/SparrowApp": ELF}, executables=("Sparrow/bin/SparrowApp",))
        manager.install(app, Release(tag="2.5.0", assets=[Asset(name=name, url=name)]))
        manager.set_launcher(app, "bin/SparrowApp", None)

        manager.rollback(app)
        self.assertEqual((app.executable, app.icon), ("bin/Sparrow", "lib/Sparrow.png"))
        entry = (self.paths.applications_dir / "sparrow.desktop").read_text()
        self.assertIn(str(self.paths.install_root / "sparrow" / "bin" / "Sparrow"), entry)

    def test_rollback_to_unrecorded_version(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        manager.install(app, self.release("2.5.0"))
        app.previous_tag = None  # e.g. backup made before the tag was recorded
        manager.rollback(app)
        self.assertEqual((app.installed_tag, app.previous_tag), (None, "2.5.0"))
        self.assertTrue(manager.update_available(app, self.release("2.5.0")))

    def test_cancelled_download_changes_nothing(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        cancel = threading.Event()
        with self.assertRaises(github.CancelledError):
            # Cancelled as the last chunk arrives: the download itself completes.
            manager.install(app, self.release("2.4.3"), progress=lambda d, t: cancel.set(), cancel=cancel)
        self.assertEqual((app.installed_tag, app.previous_tag), ("2.4.2", None))
        self.assertFalse(manager.can_rollback(app))
        self.assertEqual(AppManager(self.paths).get("sparrow").installed_tag, "2.4.2")
        self.assertEqual([p.name for p in self.paths.install_root.iterdir()], ["sparrow"])
        # A new app cancelled during its first install leaves nothing behind.
        other = ManagedApp(id="other", name="Other", repo="o/other", asset_pattern=app.asset_pattern)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(github.CancelledError):
            manager.install(other, self.release("2.4.3"), cancel=cancel)
        self.assertIsNone(manager.get("other"))
        self.assertEqual([p.name for p in self.paths.install_root.iterdir()], ["sparrow"])

    def test_no_matching_asset(self):
        manager = AppManager(self.paths)
        rel = Release(tag="3.0", assets=[Asset(name="sparrow-3.0.dmg", url="x")])
        with self.assertRaises(InstallError):
            manager.install(self.new_app(manager), rel)
        self.assertEqual(manager.apps, [])

    def test_unique_id_and_slugify(self):
        manager = AppManager(self.paths)
        manager.install(self.new_app(manager), self.release("2.4.2"))
        self.assertEqual(manager.unique_id("Sparrow"), "sparrow-2")
        self.assertEqual(slugify("  Calibre E-book!! "), "calibre-e-book")
        self.assertEqual(slugify("???"), "app")

    def test_set_launcher_rejects_paths_outside_install(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        with self.assertRaises(InstallError):
            manager.set_launcher(app, "../../../etc/passwd", None)
        manager.set_launcher(app, "bin/Sparrow", None)
        self.assertIn("Icon=application-x-executable",
                      (self.paths.applications_dir / "sparrow.desktop").read_text())

    def test_set_launcher_rejects_control_characters(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2", extra={"Sparrow/bin/x\ny": ELF}))
        with self.assertRaises(InstallError):
            manager.set_launcher(app, "bin/x\ny", None)
        self.assertEqual(app.executable, "bin/Sparrow")

    def test_invalid_id_never_touches_install_root(self):
        manager = AppManager(self.paths)
        manager.install(self.new_app(manager), self.release("2.4.2"))
        for bad in ("", "..", "x/../.."):
            app = ManagedApp(id=bad, name="Bad", repo="o/r", asset_pattern=".*")
            with self.subTest(bad):
                with self.assertRaises(InstallError):
                    manager.install(app, self.release("2.4.3"))
                with self.assertRaises(InstallError):
                    manager.remove(app)
        self.assertTrue((self.paths.install_root / "sparrow" / "bin" / "Sparrow").is_file())

    def test_download_gets_size_and_digest(self):
        manager = AppManager(self.paths)
        rel = self.release("2.4.2")
        rel.assets[0].size, rel.assets[0].digest = 123, "sha256:ab"
        manager.install(self.new_app(manager), rel)
        self.assertEqual(self.download_args, (123, "sha256:ab"))

    def test_set_name_and_repo(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        manager.set_name(app, "  Sparrow Wallet ")
        manager.set_repo(app, "https://github.com/sparrowwallet/sparrow-renamed")
        entry = (self.paths.applications_dir / "sparrow.desktop").read_text()
        self.assertIn("Name=Sparrow Wallet\n", entry)
        self.assertIn("github.com/sparrowwallet/sparrow-renamed", entry)
        reloaded = AppManager(self.paths).get("sparrow")
        self.assertEqual((reloaded.name, reloaded.repo), ("Sparrow Wallet", "sparrowwallet/sparrow-renamed"))
        with self.assertRaises(ValueError):
            manager.set_name(app, "   ")
        with self.assertRaises(ValueError):
            manager.set_repo(app, "not a repo")

    def test_set_asset_pattern_and_repo_with_pattern(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        manager.set_asset_pattern(app, r"^sparrowwallet-[0-9.]+-aarch64\.tar\.gz$")
        self.assertEqual(AppManager(self.paths).get("sparrow").asset_pattern,
                         r"^sparrowwallet-[0-9.]+-aarch64\.tar\.gz$")
        for bad in ("", "  ", "^foo(["):
            with self.assertRaises(ValueError):
                manager.set_asset_pattern(app, bad)
        manager.set_repo(app, "someone/sparrow-fork", r"^fork-[0-9.]+\.tar\.gz$")
        reloaded = AppManager(self.paths).get("sparrow")
        self.assertEqual((reloaded.repo, reloaded.asset_pattern),
                         ("someone/sparrow-fork", r"^fork-[0-9.]+\.tar\.gz$"))
        # An invalid pattern changes nothing, not even the repository.
        with self.assertRaises(ValueError):
            manager.set_repo(app, "other/repo", "(")
        self.assertEqual(app.repo, "someone/sparrow-fork")
        # Without a pattern, the old one is kept.
        manager.set_repo(app, "other/repo")
        self.assertEqual(app.asset_pattern, r"^fork-[0-9.]+\.tar\.gz$")

    def foreign_entry(self, name):
        self.paths.applications_dir.mkdir(parents=True, exist_ok=True)
        path = self.paths.applications_dir / name
        path.write_text("[Desktop Entry]\nName=Hand-written\nExec=/var/opt/Sparrow/bin/Sparrow\n")
        return path

    def test_conflicting_entry_is_never_touched(self):
        foreign = self.foreign_entry("sparrow.desktop")
        before = foreign.read_text()
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        warnings = manager.install(app, self.release("2.4.2"))
        self.assertEqual(len(warnings), 1)
        self.assertIn("not created by Tarseer", warnings[0])
        self.assertEqual(app.installed_tag, "2.4.2")  # the install itself succeeded
        self.assertIsNone(app.desktop_file)
        self.assertEqual(foreign.read_text(), before)

        # Explicit launcher change reports the conflict as an error.
        with self.assertRaises(desktop.DesktopConflictError):
            manager.set_launcher(app, "bin/Sparrow", None)

        # Next startup still warns; after the user removes the file, the entry appears.
        self.assertEqual(len(AppManager(self.paths).startup_warnings), 1)
        foreign.unlink()
        manager = AppManager(self.paths)
        self.assertEqual(manager.startup_warnings, [])
        self.assertTrue((self.paths.applications_dir / "sparrow.desktop").is_file())
        self.assertEqual(manager.get("sparrow").desktop_file, "sparrow.desktop")

        manager.remove(manager.get("sparrow"))
        self.assertFalse((self.paths.applications_dir / "sparrow.desktop").exists())

    def test_remove_leaves_foreign_entry(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        entry = self.paths.applications_dir / "sparrow.desktop"
        entry.unlink()
        foreign = self.foreign_entry("sparrow.desktop")
        manager.remove(app)
        self.assertTrue(foreign.exists())

    def test_legacy_entry_migrated_on_startup(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        # Simulate a v0.1-dev install: prefixed file, no desktop_file in config.
        apps_dir = self.paths.applications_dir
        (apps_dir / "sparrow.desktop").rename(apps_dir / "tarseer-sparrow.desktop")
        app.desktop_file = None
        manager._save()

        migrated = AppManager(self.paths)
        self.assertEqual(migrated.startup_warnings, [])
        self.assertEqual(sorted(p.name for p in apps_dir.iterdir()), ["sparrow.desktop"])
        self.assertEqual(AppManager(self.paths).get("sparrow").desktop_file, "sparrow.desktop")

    def test_legacy_entry_kept_when_clean_name_is_taken(self):
        manager = AppManager(self.paths)
        app = self.new_app(manager)
        manager.install(app, self.release("2.4.2"))
        apps_dir = self.paths.applications_dir
        (apps_dir / "sparrow.desktop").rename(apps_dir / "tarseer-sparrow.desktop")
        app.desktop_file = "tarseer-sparrow.desktop"
        manager._save()
        foreign = self.foreign_entry("sparrow.desktop")

        manager = AppManager(self.paths)
        app = manager.get("sparrow")
        manager.install(app, self.release("2.5.0"))  # keeps refreshing the file it owns
        self.assertEqual(app.installed_tag, "2.5.0")
        self.assertIn("X-Tarseer-App=sparrow", (apps_dir / "tarseer-sparrow.desktop").read_text())
        self.assertIn("Hand-written", foreign.read_text())


class CheckUpdatesTest(unittest.TestCase):
    """AppManager.check_updates with GitHub replaced by a dict of replies."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.paths = Paths(tmp / "opt", tmp / "applications", tmp / "config" / "apps.json")
        apps = [
            ManagedApp(id=f"app{i}", name=f"App {i}", repo=f"owner/app{i}",
                       asset_pattern="x", installed_tag="v1")
            for i in range(4)
        ]
        config.save_apps(self.paths.config_file, apps)
        self.manager = AppManager(self.paths)
        self.replies = {}  # repo -> Release or Exception
        self.calls = []
        self.cached_args = []
        patcher = mock.patch.object(github, "get_latest_release", side_effect=self.fake_latest)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fake_latest(self, repo, cached=None):
        self.calls.append(repo)
        self.cached_args.append(cached)
        reply = self.replies.get(repo, Release(tag="v1"))
        if isinstance(reply, Exception):
            raise reply
        return reply

    def test_results_are_returned_cached_and_reported(self):
        self.replies["owner/app1"] = Release(tag="v2")
        self.replies["owner/app2"] = github.NotFoundError("gone")
        seen = []
        results = self.manager.check_updates(on_result=lambda app, r: seen.append(app.id))
        self.assertEqual(sorted(results), ["app0", "app1", "app2", "app3"])
        self.assertEqual(sorted(seen), sorted(results))
        self.assertEqual(results["app1"].release.tag, "v2")
        self.assertIsInstance(results["app2"].error, github.NotFoundError)
        self.assertIsNone(results["app2"].release)
        self.assertGreater(results["app0"].checked_at, 0)
        self.assertIs(self.manager.last_check("app1"), results["app1"])
        self.assertEqual([a.id for a in self.manager.updates], ["app1"])

    def test_only_given_apps_are_checked(self):
        app = self.manager.get("app3")
        self.assertEqual(list(self.manager.check_updates([app])), ["app3"])
        self.assertEqual(self.calls, ["owner/app3"])
        self.assertIsNone(self.manager.last_check("app0"))
        self.assertEqual(self.manager.check_updates([]), {})

    def test_rate_limit_stops_the_check(self):
        limit = github.RateLimitError(None)
        self.replies["owner/app0"] = limit
        results = self.manager.check_updates(workers=1)
        self.assertEqual(self.calls, ["owner/app0"])
        self.assertTrue(all(r.error is limit for r in results.values()))

    def test_rejected_token_stops_the_check(self):
        self.replies["owner/app1"] = github.TokenRejectedError()
        results = self.manager.check_updates(workers=1)
        self.assertEqual(self.calls, ["owner/app0", "owner/app1"])
        self.assertIsNotNone(results["app0"].release)
        for app_id in ("app1", "app2", "app3"):
            self.assertIsInstance(results[app_id].error, github.TokenRejectedError)

    def test_other_errors_dont_stop_the_check(self):
        self.replies["owner/app0"] = github.GitHubError("Network error: timeout")
        results = self.manager.check_updates(workers=1)
        self.assertEqual(len(self.calls), 4)
        self.assertEqual(sum(r.error is not None for r in results.values()), 1)

    def test_new_result_replaces_the_old_one(self):
        self.replies["owner/app0"] = Release(tag="v2")
        self.manager.check_updates()
        self.replies["owner/app0"] = github.GitHubError("down")
        self.manager.check_updates()
        check = self.manager.last_check("app0")
        self.assertIsNone(check.release)
        self.assertEqual(self.manager.updates, [])

    def test_record_release_and_installed_update(self):
        app = self.manager.get("app2")
        self.manager.record_release(app.id, Release(tag="v3"))
        self.assertEqual(self.manager.updates, [app])
        app.installed_tag = "v3"  # what install() does
        self.assertEqual(self.manager.updates, [])

    def test_removed_app_loses_its_result(self):
        app = self.manager.get("app0")
        self.manager.record_release(app.id, Release(tag="v2"))
        self.manager.remove(app)
        self.assertIsNone(self.manager.last_check("app0"))
        # A check still running for a removed app doesn't bring its result back.
        self.manager.check_updates([app])
        self.assertIsNone(self.manager.last_check("app0"))

    def test_etag_replies_are_cached_and_sent_back(self):
        first = Release(tag="v2", etag='W/"1"')
        self.replies["owner/app0"] = first
        app = self.manager.get("app0")
        self.manager.check_updates([app])
        self.assertIsNone(self.cached_args[-1])
        # A new manager (next launch) reads the cache from disk.
        manager = AppManager(self.paths)
        manager.check_updates([manager.get("app0")])
        sent = self.cached_args[-1]
        self.assertEqual((sent.tag, sent.etag), ("v2", 'W/"1"'))
        # Replies without an ETag aren't kept.
        self.replies["owner/app1"] = Release(tag="v2")
        manager.check_updates([manager.get("app1")])
        AppManager(self.paths).check_updates([manager.get("app1")])
        self.assertIsNone(self.cached_args[-1])

    def test_cache_forgets_repos_no_app_uses(self):
        self.manager.fetch_latest("someone/else")  # e.g. looked up in Add App
        self.replies["someone/else"] = Release(tag="v1", etag='"x"')
        self.manager.fetch_latest("someone/else")
        self.assertTrue(self.paths.cache_file.exists())
        AppManager(self.paths)  # startup prunes it
        self.assertNotIn("someone/else", self.paths.cache_file.read_text())

    def test_quota(self):
        def limit(remaining):
            return mock.patch.object(github, "get_rate_limit",
                                     return_value=github.RateLimit(60, remaining, 0))
        apps = self.manager.apps[:2]
        with limit(14):
            self.assertTrue(self.manager.has_quota_for())  # 4 apps + 10
        with limit(13):
            self.assertFalse(self.manager.has_quota_for())
            self.assertTrue(self.manager.has_quota_for(apps))
        with limit(11):
            self.assertFalse(self.manager.has_quota_for(apps))

    def test_check_if_quota(self):
        quota = github.RateLimit(60, 13, 0)  # 4 apps + 10 needed
        with mock.patch.object(github, "get_rate_limit", return_value=quota):
            self.assertIsNone(self.manager.check_if_quota())
        self.assertEqual(self.calls, [])
        quota = github.RateLimit(60, 14, 0)
        with mock.patch.object(github, "get_rate_limit", return_value=quota):
            self.assertEqual(len(self.manager.check_if_quota()), 4)


class ReleaseCacheTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "cache" / "releases.json"

    def test_roundtrip(self):
        cache = ReleaseCache(self.path)
        release = Release(tag="v1", name="One", html_url="https://h", etag='W/"e"',
                          assets=[Asset(name="a.tar.gz", url="https://d/a", size=3, digest="sha256:00")])
        cache.put("o/r", release)
        cache.put("o/no-etag", Release(tag="v1"))
        loaded = ReleaseCache(self.path)
        self.assertEqual(loaded.get("o/r"), release)
        self.assertIsNone(loaded.get("o/no-etag"))
        loaded.keep_only(["other/repo"])
        self.assertIsNone(ReleaseCache(self.path).get("o/r"))

    def test_bad_file_starts_empty(self):
        self.path.parent.mkdir(parents=True)
        for text in ("not json", "[]", '{"version": 99, "releases": {}}',
                     '{"version": 1, "releases": {"o/r": {"tag": "v1"}}}',
                     '{"version": 1, "releases": {"o/r": {"tag": "v1", "etag": "e", "bogus": 1}}}'):
            with self.subTest(text):
                self.path.write_text(text)
                self.assertIsNone(ReleaseCache(self.path).get("o/r"))

    def test_unwritable_cache_is_not_an_error(self):
        self.path.parent.parent.mkdir(exist_ok=True)
        self.path.parent.write_text("a file where the folder should be")
        cache = ReleaseCache(self.path)
        cache.put("o/r", Release(tag="v1", etag='"e"'))
        self.assertEqual(cache.get("o/r").tag, "v1")  # still used in memory


if __name__ == "__main__":
    unittest.main()
