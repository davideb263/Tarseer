import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tarseer.core import config, desktop, github
from tarseer.core.assets import pattern_from_name
from tarseer.core.installer import InstallError
from tarseer.core.manager import AppManager, slugify
from tarseer.core.models import Asset, ManagedApp, Paths, Release

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

    def fake_download(self, url, dest, progress=None, cancel=None):
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
        manager.install(app, newer)
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


if __name__ == "__main__":
    unittest.main()
