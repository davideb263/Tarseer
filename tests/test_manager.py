import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tarseer.core import github
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
        app = manager.install(self.new_app(manager), self.release("2.4.2"))
        self.assertEqual((app.id, app.installed_tag, app.previous_tag), ("sparrow", "2.4.2", None))
        self.assertEqual((app.executable, app.icon), ("bin/Sparrow", "lib/Sparrow.png"))
        entry = self.paths.applications_dir / "tarseer-sparrow.desktop"
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
        app = manager.install(self.new_app(manager), self.release("2.4.2"))
        with self.assertRaises(InstallError):
            manager.set_launcher(app, "../../../etc/passwd", None)
        manager.set_launcher(app, "bin/Sparrow", None)
        self.assertIn("Icon=application-x-executable",
                      (self.paths.applications_dir / "tarseer-sparrow.desktop").read_text())


if __name__ == "__main__":
    unittest.main()
