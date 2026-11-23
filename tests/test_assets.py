import re
import unittest

from tarseer.core.assets import candidate_assets, is_tarball, match_asset, pattern_from_name, score_asset
from tarseer.core.models import Asset


def assets(*names):
    return [Asset(name=n, url=f"https://example.com/{n}") for n in names]


class TestSelection(unittest.TestCase):
    def test_is_tarball(self):
        for name in ("a.tar.gz", "a.tgz", "a.TXZ", "a.tar.zst", "a.tar.bz2", "a.tar"):
            self.assertTrue(is_tarball(name), name)
        for name in ("a.zip", "a.AppImage", "a.tar.gz.sig", "a.deb", "a.tar.gz.sha256"):
            self.assertFalse(is_tarball(name), name)

    def test_calibre_like_release(self):
        release = assets(
            "calibre-8.1.0-arm64.txz", "calibre-8.1.0-x86_64.txz", "calibre-8.1.0.dmg",
            "calibre-8.1.0.tar.xz", "calibre-8.1.0.tar.xz.sig", "calibre-64bit-8.1.0.msi",
        )
        names = [a.name for a in candidate_assets(release, "x86_64")]
        self.assertEqual(names[0], "calibre-8.1.0-x86_64.txz")
        self.assertNotIn("calibre-8.1.0-arm64.txz", names)
        self.assertNotIn("calibre-8.1.0.tar.xz.sig", names)

    def test_sparrow_like_release(self):
        release = assets(
            "sparrowwallet-2.4.2-aarch64.tar.gz", "sparrowwallet-2.4.2-x86_64.tar.gz",
            "Sparrow-2.4.2.dmg", "Sparrow-2.4.2-x86_64.exe", "sparrowwallet_2.4.2-1_amd64.deb",
        )
        self.assertEqual(candidate_assets(release, "x86_64")[0].name, "sparrowwallet-2.4.2-x86_64.tar.gz")
        self.assertEqual(candidate_assets(release, "aarch64")[0].name, "sparrowwallet-2.4.2-aarch64.tar.gz")

    def test_repo_name_breaks_ties_between_editions(self):
        release = assets("sparrowserver-2.5.6-x86_64.tar.gz", "sparrowwallet-2.5.6-x86_64.tar.gz")
        best = candidate_assets(release, "x86_64", repo="sparrowwallet/sparrow")[0]
        self.assertEqual(best.name, "sparrowwallet-2.5.6-x86_64.tar.gz")

    def test_foreign_os_and_source_ranked_out(self):
        self.assertIsNone(score_asset("tool-1.0-macos-x86_64.tar.gz", "x86_64"))
        self.assertIsNone(score_asset("tool-1.0-windows-x64.tar.gz", "x86_64"))
        self.assertLess(score_asset("tool-1.0-src.tar.gz", "x86_64"), score_asset("tool-1.0-linux.tar.gz", "x86_64"))

    def test_amd64_alias(self):
        self.assertEqual(
            candidate_assets(assets("t-linux-arm64.tar.gz", "t-linux-amd64.tar.gz"), "AMD64")[0].name,
            "t-linux-amd64.tar.gz",
        )


class TestPattern(unittest.TestCase):
    def test_tag_with_v_prefix_matches_future_versions(self):
        pattern = pattern_from_name("calibre-8.1.0-x86_64.txz", "v8.1.0")
        self.assertTrue(re.fullmatch(pattern, "calibre-8.10.2-x86_64.txz"))
        self.assertTrue(re.fullmatch(pattern, "calibre-9.0-x86_64.txz"))
        self.assertFalse(re.fullmatch(pattern, "calibre-9.0-arm64.txz"))

    def test_tag_without_prefix(self):
        pattern = pattern_from_name("sparrowwallet-2.4.2-x86_64.tar.gz", "2.4.2")
        self.assertTrue(re.fullmatch(pattern, "sparrowwallet-2.5.0-x86_64.tar.gz"))
        self.assertFalse(re.fullmatch(pattern, "sparrowwallet-2.5.0-aarch64.tar.gz"))

    def test_version_not_in_name_falls_back_to_dotted_numbers(self):
        pattern = pattern_from_name("tool_1.2.3_linux_x86_64.tar.gz", "release-2024-05")
        self.assertTrue(re.fullmatch(pattern, "tool_1.3_linux_x86_64.tar.gz"))

    def test_version_free_name_matches_itself(self):
        pattern = pattern_from_name("tool-linux-x86_64.tar.gz", "v3.0")
        self.assertIsNotNone(match_asset(assets("tool-linux-x86_64.tar.gz"), pattern))

    def test_match_asset(self):
        pattern = pattern_from_name("app-1.0-x86_64.tar.gz", "v1.0")
        found = match_asset(assets("app-2.0-aarch64.tar.gz", "app-2.0-x86_64.tar.gz"), pattern)
        self.assertEqual(found.name, "app-2.0-x86_64.tar.gz")
        self.assertIsNone(match_asset(assets("other.tar.gz"), pattern))


if __name__ == "__main__":
    unittest.main()
