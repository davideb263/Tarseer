"""When background checks run. Pure functions: the timers live in the UI."""

from __future__ import annotations

from .models import CheckResult

# A clock this far behind the recorded last check was changed: check again.
CLOCK_SKEW = 60


def check_due(last_check: float, interval: float, now: float) -> bool:
    """Whether a periodic check is due. Wall-clock based, so time spent
    suspended counts (GLib's monotonic timers stop during suspend)."""
    if last_check <= 0 or last_check > now + CLOCK_SKEW:
        return True
    return now - last_check >= interval


def check_succeeded(results: dict[str, CheckResult]) -> bool:
    """A check counts as done if GitHub answered for at least one app. If every
    lookup failed (e.g. offline right after login) it should be retried."""
    return any(r.release is not None for r in results.values())
