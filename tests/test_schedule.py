import unittest

from tarseer.core import github
from tarseer.core.models import CheckResult, Release
from tarseer.core.schedule import CLOCK_SKEW, check_due, check_succeeded, notification_state

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

    def test_notification_state(self):
        # First update: notify.
        self.assertEqual(notification_state({"fd": "v2"}, set(), {}), (True, {"fd": "v2"}))
        # Same tag again: quiet.
        self.assertEqual(notification_state({"fd": "v2"}, set(), {"fd": "v2"}), (False, {"fd": "v2"}))
        # A newer tag, or another app: notify, with every update in the state.
        self.assertEqual(notification_state({"fd": "v3"}, set(), {"fd": "v2"}), (True, {"fd": "v3"}))
        self.assertEqual(notification_state({"fd": "v2", "rg": "15"}, set(), {"fd": "v2"}),
                         (True, {"fd": "v2", "rg": "15"}))
        # Installed (no update any more): forgotten, so a later update is new.
        self.assertEqual(notification_state({}, set(), {"fd": "v2"}), (False, {}))
        # A failed check keeps what was announced: no repeat when it works again.
        new, state = notification_state({}, {"fd"}, {"fd": "v2", "rg": "15"})
        self.assertEqual((new, state), (False, {"fd": "v2"}))
        self.assertEqual(notification_state({"fd": "v2"}, set(), state), (False, {"fd": "v2"}))


if __name__ == "__main__":
    unittest.main()
