import tempfile
import unittest
from pathlib import Path

from tarseer.core import desktop
from tarseer.core.models import ManagedApp

from .helpers import ELF, PNG_256, write_tree

SCRIPT = b"#!/bin/sh\nexec true\n"


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_calibre_like_flat_layout(self):
        names = ["calibre", "calibre-debug", "calibre-server", "ebook-viewer", "ebook-edit"]
        write_tree(self.dir, {n: ELF for n in names} | {"lib/libfoo.so.1": ELF},
                   executables=tuple(names) + ("lib/libfoo.so.1",))
        self.assertEqual(desktop.guess_executable(self.dir, "Calibre"), "calibre")

    def test_sparrow_like_layout(self):
        write_tree(self.dir, {
            "bin/Sparrow": ELF,
            "lib/runtime/bin/java": ELF,
            "lib/runtime/bin/keytool": ELF,
            "lib/Sparrow.png": PNG_256,
        }, executables=("bin/Sparrow", "lib/runtime/bin/java", "lib/runtime/bin/keytool"))
        self.assertEqual(desktop.guess_executable(self.dir, "Sparrow"), "bin/Sparrow")
        self.assertEqual(desktop.guess_icon(self.dir, "Sparrow"), "lib/Sparrow.png")

    def test_non_executable_and_data_files_ignored(self):
        write_tree(self.dir, {"app": ELF, "readme": b"#!/not/really"})
        self.assertIsNone(desktop.guess_executable(self.dir, "app"))

    def test_script_launcher_found(self):
        write_tree(self.dir, {"myapp.sh": SCRIPT, "bin/data.bin": b"\0"}, executables=("myapp.sh",))
        self.assertEqual(desktop.guess_executable(self.dir, "MyApp"), "myapp.sh")

    def test_icon_prefers_svg_and_skips_unrelated_images(self):
        write_tree(self.dir, {
            "resources/images/toolbar-save.png": PNG_256,
            "resources/calibre.png": PNG_256,
            "resources/icons/calibre.svg": b"<svg/>",
        })
        self.assertEqual(desktop.guess_icon(self.dir, "calibre"), "resources/icons/calibre.svg")

    def test_no_icon(self):
        write_tree(self.dir, {"images/button.png": PNG_256})
        self.assertIsNone(desktop.guess_icon(self.dir, "calibre"))

    def test_rank_lists_everything_best_first(self):
        write_tree(self.dir, {
            "bin/Sparrow": ELF, "bin/sparrow-cli": ELF, "uninstall": ELF,
            "lib/Sparrow.png": PNG_256, "images/button.png": PNG_256,
        }, executables=("bin/Sparrow", "bin/sparrow-cli", "uninstall"))
        execs = desktop.rank_executables(self.dir, "Sparrow")
        self.assertEqual(execs[0], "bin/Sparrow")
        self.assertEqual(execs[-1], "uninstall")  # noise ranked last, but still offered
        self.assertEqual(desktop.rank_icons(self.dir, "Sparrow"), ["lib/Sparrow.png", "images/button.png"])

    def test_render_entry_quotes_paths(self):
        app = ManagedApp(id="my-app", name="My App", repo="o/r", asset_pattern=".*",
                         executable="bin/my app", icon="icon.png")
        text = desktop.render_desktop_entry(app, Path('/home/u/.local/opt/my-app'))
        self.assertIn('Exec="/home/u/.local/opt/my-app/bin/my app"\n', text)
        self.assertIn("Icon=/home/u/.local/opt/my-app/icon.png\n", text)
        self.assertIn("X-Tarseer-App=my-app\n", text)

        app.executable = 'we$ird"name'
        text = desktop.render_desktop_entry(app, Path("/x"))
        self.assertIn('Exec="/x/we\\\\$ird\\\\"name"\n', text)

        app.executable = "bin/app"
        text = desktop.render_desktop_entry(app, Path("/x/100%full/%u"))
        self.assertIn('Exec="/x/100%%full/%%u/bin/app"\n', text)
        self.assertIn("Path=/x/100%full/%u\n", text)  # no field codes outside Exec

    def test_write_and_remove_entry(self):
        app = ManagedApp(id="a", name="A", repo="o/r", asset_pattern=".*", executable="a")
        apps_dir = self.dir / "applications"
        path = desktop.write_desktop_entry(apps_dir, app, self.dir / "a")
        self.assertEqual(path.name, "a.desktop")
        self.assertEqual(app.desktop_file, "a.desktop")
        self.assertTrue(desktop.is_owned(path, "a"))
        self.assertFalse(desktop.is_owned(path, "b"))
        self.assertIn("Icon=application-x-executable", path.read_text())
        desktop.remove_desktop_entry(apps_dir, app)
        self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
