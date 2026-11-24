import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tarseer.core import installer
from tarseer.core.installer import InstallError

from .helpers import ELF, make_tarball


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "opt"
        self.target = self.root / "app"

    def tearDown(self):
        self._tmp.cleanup()

    def leftovers(self):
        return [p.name for p in self.root.iterdir() if p.name.startswith(".tarseer-")]

    def test_flat_archive(self):
        archive = make_tarball(self.tmp / "a.txz", {"calibre": ELF, "lib/x.so": b"x"},
                               mode="w:xz", executables=("calibre",))
        self.assertFalse(installer.install_archive(archive, self.target))
        self.assertTrue((self.target / "calibre").is_file())
        self.assertTrue((self.target / "calibre").stat().st_mode & 0o100)
        self.assertEqual(self.leftovers(), [])

    def test_subdir_archive_is_flattened(self):
        archive = make_tarball(self.tmp / "s.tar.gz", {"Sparrow/bin/Sparrow": ELF, "Sparrow/lib/a": b"a"})
        installer.install_archive(archive, self.target)
        self.assertTrue((self.target / "bin" / "Sparrow").is_file())
        self.assertFalse((self.target / "Sparrow").exists())

    def test_update_makes_backup_and_drops_stale_files(self):
        v1 = make_tarball(self.tmp / "v1.tar.gz", {"app": b"1", "old-only": b"x"})
        v2 = make_tarball(self.tmp / "v2.tar.gz", {"app": b"2"})
        installer.install_archive(v1, self.target)
        self.assertTrue(installer.install_archive(v2, self.target))
        self.assertEqual((self.target / "app").read_bytes(), b"2")
        self.assertFalse((self.target / "old-only").exists())  # the script's flat-mode bug
        self.assertEqual((installer.backup_path(self.target) / "app").read_bytes(), b"1")

    def test_corrupt_archive_keeps_current_install(self):
        good = make_tarball(self.tmp / "g.tar.gz", {"app": b"good"})
        installer.install_archive(good, self.target)
        bad = self.tmp / "bad.tar.gz"
        bad.write_bytes(b"not a tarball")
        with self.assertRaises(InstallError):
            installer.install_archive(bad, self.target)
        self.assertEqual((self.target / "app").read_bytes(), b"good")
        self.assertEqual(self.leftovers(), [])

    def test_failed_swap_restores_current_install(self):
        installer.install_archive(make_tarball(self.tmp / "1.tar.gz", {"app": b"1"}), self.target)
        v2 = make_tarball(self.tmp / "2.tar.gz", {"app": b"2"})
        real_rename = installer.os.rename
        calls = []

        def flaky_rename(src, dst):
            calls.append((src, dst))
            if len(calls) == 2:  # the "new version into place" step
                raise OSError("disk full")
            return real_rename(src, dst)

        with mock.patch.object(installer.os, "rename", side_effect=flaky_rename):
            with self.assertRaises(InstallError):
                installer.install_archive(v2, self.target)
        self.assertEqual((self.target / "app").read_bytes(), b"1")

    def fail_rename(self, src_name, dst_name):
        """Patch os.rename to fail for one src -> dst move (by base name)."""
        real_rename = installer.os.rename

        def rename(src, dst):
            if (Path(src).name, Path(dst).name) == (src_name, dst_name):
                raise OSError("EBUSY")
            return real_rename(src, dst)

        return mock.patch.object(installer.os, "rename", side_effect=rename)

    def state(self):
        """(current, backup) app contents; None where missing."""
        read = lambda d: (d / "app").read_bytes() if d.exists() else None  # noqa: E731
        return read(self.target), read(installer.backup_path(self.target))

    def install_v1_v2(self):
        for v in (b"1", b"2"):
            installer.install_archive(make_tarball(self.tmp / f"{v.decode()}.tar.gz", {"app": v}), self.target)

    def test_failed_swap_keeps_old_backup(self):
        self.install_v1_v2()
        v3 = make_tarball(self.tmp / "3.tar.gz", {"app": b"3"})
        with self.fail_rename("extracted", "app"):  # flat archive: root is "extracted"
            with self.assertRaises(InstallError):
                installer.install_archive(v3, self.target)
        self.assertEqual(self.state(), (b"2", b"1"))
        self.assertEqual(self.leftovers(), [])

    def test_failed_rollback_steps_restore_state(self):
        self.install_v1_v2()
        for step in (("app.bak", "app"), ("app.swap", "app.bak")):
            with self.subTest(step), self.fail_rename(*step):
                with self.assertRaises(InstallError):
                    installer.rollback(self.target)
            self.assertEqual(self.state(), (b"2", b"1"))
            self.assertFalse(self.root.joinpath("app.swap").exists())

    def test_interrupted_rollback_is_recovered(self):
        self.install_v1_v2()
        swap = self.root / "app.swap"
        # Stopped after moving the current version aside.
        os.rename(self.target, swap)
        installer.rollback(self.target)
        self.assertEqual(self.state(), (b"1", b"2"))
        # Stopped after the backup moved in, before the old current became the backup.
        os.rename(installer.backup_path(self.target), swap)
        installer.rollback(self.target)
        self.assertEqual(self.state(), (b"2", b"1"))
        self.assertFalse(swap.exists())

    def test_unsafe_archives_rejected(self):
        for name, kwargs in {
            "traversal": dict(files={"../evil": b"x"}),
            "symlink_absolute": dict(files={"ok": b"x"}, symlinks={"link": "/etc/passwd"}),
            "symlink_escape": dict(files={"ok": b"x"}, symlinks={"link": "../../outside"}),
        }.items():
            with self.subTest(name):
                target = self.root / name
                archive = make_tarball(self.tmp / f"{name}.tar.gz", **kwargs)
                with self.assertRaises(InstallError):
                    installer.install_archive(archive, target)
                self.assertFalse(target.exists())
        self.assertFalse((self.tmp / "evil").exists())

    def test_link_escaping_the_flattened_folder_rejected(self):
        # Inside the staging folder at extraction time, but "../victim" from the
        # install dir once "app/" is flattened into it: a sibling app's folder.
        (self.root / "victim").mkdir(parents=True)
        archive = make_tarball(self.tmp / "l.tar.gz", {"app/ok": b"x"},
                               symlinks={"app/link": "../victim", "app/inner": "ok"})
        with self.assertRaises(InstallError) as ctx:
            installer.install_archive(archive, self.target)
        self.assertIn("link", str(ctx.exception))
        self.assertFalse(self.target.exists())
        self.assertEqual(self.leftovers(), [])
        # Links staying inside the app folder are fine.
        ok = make_tarball(self.tmp / "ok.tar.gz", {"app/ok": b"x", "app/sub/f": b"y"},
                          symlinks={"app/inner": "ok", "app/sub/up": "../ok"})
        installer.install_archive(ok, self.target)
        self.assertEqual((self.target / "sub" / "up").read_bytes(), b"x")

    def test_archive_too_large_for_disk_rejected(self):
        installer.install_archive(make_tarball(self.tmp / "1.tar.gz", {"app": b"1"}), self.target)
        big = make_tarball(self.tmp / "big.tar.gz", {"app": b"x" * 1000})
        usage = shutil._ntuple_diskusage(10**9, 0, installer.DISK_RESERVE + 999)
        with mock.patch.object(installer.shutil, "disk_usage", return_value=usage), \
                self.assertRaises(InstallError) as ctx:
            installer.install_archive(big, self.target)
        self.assertIn("disk space", str(ctx.exception))
        self.assertEqual((self.target / "app").read_bytes(), b"1")
        self.assertEqual(self.leftovers(), [])

    def test_absolute_member_is_kept_inside_target(self):
        archive = make_tarball(self.tmp / "abs.tar.gz", {"/tmp/tarseer-evil": b"x", "app": b"1"})
        installer.install_archive(archive, self.target)
        self.assertTrue((self.target / "tmp" / "tarseer-evil").is_file())

    def test_empty_archive(self):
        with self.assertRaises(InstallError):
            installer.install_archive(make_tarball(self.tmp / "e.tar.gz", {}), self.target)

    def test_rollback_swaps_and_is_reversible(self):
        installer.install_archive(make_tarball(self.tmp / "1.tar.gz", {"app": b"1"}), self.target)
        installer.install_archive(make_tarball(self.tmp / "2.tar.gz", {"app": b"2"}), self.target)
        installer.rollback(self.target)
        self.assertEqual((self.target / "app").read_bytes(), b"1")
        installer.rollback(self.target)
        self.assertEqual((self.target / "app").read_bytes(), b"2")

    def test_rollback_without_backup(self):
        installer.install_archive(make_tarball(self.tmp / "1.tar.gz", {"app": b"1"}), self.target)
        with self.assertRaises(InstallError):
            installer.rollback(self.target)

    def test_remove(self):
        installer.install_archive(make_tarball(self.tmp / "1.tar.gz", {"app": b"1"}), self.target)
        installer.install_archive(make_tarball(self.tmp / "2.tar.gz", {"app": b"2"}), self.target)
        installer.remove(self.target)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
