"""F4 (15-Jul-2026): monitoring hygiene.

- The stale `officer.telegram_ban_until: '2026-06-23'` is PURGED (it was a past date =
  inert, but confusing). Fails on the pre-fix registry (which still carried the date).
- The alert_watcher log date-embed (bounded growth) is covered by
  tests/unit/test_alert_watcher.py::TestSetupWatcherLog::test_log_file_created.
"""
from __future__ import annotations

from pathlib import Path

from core.cron_registry import CronRegistry


def test_stale_telegram_ban_purged():
    officer = CronRegistry.load(Path("config") / "cron_registry.yaml").officer
    assert officer.telegram_ban_until is None   # F4: purged (was '2026-06-23')
