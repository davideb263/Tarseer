import unittest

from tarseer.core import github
from tarseer.core.models import CheckResult, Release
from tarseer.core.schedule import CLOCK_SKEW, check_due, check_succeeded

HOUR = 3600


class ScheduleTest(unittest.TestCase):
    def test_check_due(self):
        now = 1_000_000
        self.assertTrue(check_due(0, 6 * HOUR, now))  # never checked
        self.assertFalse(check_due(now - 5 * HOUR, 6 * HOUR, now))
        self.assertTrue(check_due(now - 6 * HOUR, 6 * HOUR, now))
        self.assertTrue(check_due(now - 3 * 24 * HOUR, 6 * HOUR, now))  # e.g. after suspend
        # The clock went backwards: don't wait until it catches up.
        self.assertFalse(check_due(now + CLOCK_SKEW, 6 * HOUR, now))
        self.assertTrue(check_due(now + CLOCK_SKEW + 1, 6 * HOUR, now))

    def test_check_succeeded(self):
        ok = CheckResult(release=Release(tag="v1"))
        failed = CheckResult(error=github.GitHubError("Network error"))
        self.assertTrue(check_succeeded({"a": failed, "b": ok}))
        self.assertFalse(check_succeeded({"a": failed}))
        self.assertFalse(check_succeeded({}))


if __name__ == "__main__":
    unittest.main()
