"""
tests/integration/test_alerts_pipeline.py -- Trading System v2

End-to-end integration tests for the full alerts pipeline (G8):
  telegram_notifier.send(CRITICAL) -> sentinel written
  alert_watcher.run_once() -> sentinel emailed -> .delivered

All network calls (Telegram HTTP + SMTP) are mocked.
Uses a real temporary filesystem.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alerts.critical import list_pending_sentinels
from alerts.telegram_notifier import TelegramNotifier
from scripts.alert_watcher import run_once


def _make_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    log.addHandler(logging.NullHandler())
    return log


def _make_watcher_cfg(sentinel_dir: Path, max_attempts: int = 3) -> MagicMock:
    smtp_cfg = MagicMock()
    smtp_cfg.host = "smtp.test.com"
    smtp_cfg.port = 587
    smtp_cfg.use_tls = True
    smtp_cfg.username = "u"
    smtp_cfg.password = "p"
    smtp_cfg.from_address = "from@t.com"
    smtp_cfg.to_addresses = ["ops@t.com"]
    smtp_cfg.timeout_sec = 10

    alerts_cfg = MagicMock()
    alerts_cfg.sentinel_dir = str(sentinel_dir)
    alerts_cfg.watcher_max_attempts = max_attempts
    alerts_cfg.watcher_lock_path = str(sentinel_dir / "watcher.lock")
    alerts_cfg.watcher_log_path = str(sentinel_dir / "watcher.log")
    alerts_cfg.smtp = smtp_cfg

    cfg = MagicMock()
    cfg.system.alerts = alerts_cfg
    return cfg


class TestAlertsPipeline(unittest.TestCase):
    """G8 design: CRITICAL guaranteed delivery via sentinel + watcher fallback."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)
        self.sentinel_dir = self.tmpdir / "sentinels"

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    @patch("scripts.alert_watcher._send_email")
    def test_critical_send_then_watcher_delivers(self, mock_smtp, mock_tg):
        """Full path: notifier writes sentinel, watcher emails it, .delivered."""
        mock_tg.return_value = MagicMock(status_code=200)

        notifier = TelegramNotifier(
            bot_token="tok", chat_ids=["-100"],
            failed_alerts_log_path=self.tmpdir / "failed.log",
            sentinel_dir=self.sentinel_dir,
            logger=_make_logger("tg"),
        )
        result = notifier.send("CRITICAL", "Capital breach", "LHS != RHS", "fund_manager",
                               context={"delta": -500.0})

        self.assertIsNotNone(result.sentinel_path)
        flags = list_pending_sentinels(self.sentinel_dir)
        self.assertEqual(len(flags), 1, "Exactly one .flag expected after CRITICAL send")

        cfg = _make_watcher_cfg(self.sentinel_dir)
        watcher_result = run_once(cfg, log=_make_logger("watcher"))
        self.assertEqual(watcher_result, 0)

        flags_after = list_pending_sentinels(self.sentinel_dir)
        self.assertEqual(flags_after, [], "No .flag files should remain after watcher run")
        delivered = list(self.sentinel_dir.glob("*.delivered"))
        self.assertEqual(len(delivered), 1, "Exactly one .delivered expected")

    @patch("alerts.telegram_notifier.requests.post")
    @patch("scripts.alert_watcher._send_email")
    def test_telegram_failure_watcher_still_delivers(self, mock_smtp, mock_tg):
        """G8 fallback: Telegram fails but sentinel ensures email delivery."""
        mock_tg.return_value = MagicMock(status_code=500)

        notifier = TelegramNotifier(
            bot_token="tok", chat_ids=["-100"],
            failed_alerts_log_path=self.tmpdir / "failed.log",
            sentinel_dir=self.sentinel_dir,
            logger=_make_logger("tg"),
            max_retries=0,
        )
        result = notifier.send("CRITICAL", "Broker auth failed", "Token expired", "zerodha_adapter")

        self.assertIsNotNone(result.sentinel_path, "Sentinel must be written even if Telegram fails")
        self.assertFalse(result.success, "Telegram delivery must have failed")

        flags = list_pending_sentinels(self.sentinel_dir)
        self.assertEqual(len(flags), 1, "Sentinel must still be .flag for watcher to pick up")

        cfg = _make_watcher_cfg(self.sentinel_dir)
        run_once(cfg, log=_make_logger("watcher"))

        delivered = list(self.sentinel_dir.glob("*.delivered"))
        self.assertEqual(len(delivered), 1, "Watcher must deliver via email fallback")

    @patch("alerts.telegram_notifier.requests.post")
    @patch("scripts.alert_watcher._send_email")
    def test_no_double_send_on_second_watcher_run(self, mock_smtp, mock_tg):
        """Watcher must not re-send an already .delivered sentinel."""
        mock_tg.return_value = MagicMock(status_code=200)

        notifier = TelegramNotifier(
            bot_token="tok", chat_ids=["-100"],
            failed_alerts_log_path=self.tmpdir / "failed.log",
            sentinel_dir=self.sentinel_dir,
            logger=_make_logger("tg"),
        )
        notifier.send("CRITICAL", "t", "b", "mod")

        cfg = _make_watcher_cfg(self.sentinel_dir)
        run_once(cfg, log=_make_logger("w1"))     # first run: delivers
        call_count_after_first = mock_smtp.call_count

        run_once(cfg, log=_make_logger("w2"))     # second run: nothing to do
        self.assertEqual(mock_smtp.call_count, call_count_after_first,
                         "No additional SMTP calls on second run")

    @patch("alerts.telegram_notifier.requests.post")
    @patch("scripts.alert_watcher._send_email")
    def test_sentinel_json_content_matches_alert(self, mock_smtp, mock_tg):
        """Sentinel file contains the exact alert data passed to send()."""
        import json as _json
        mock_tg.return_value = MagicMock(status_code=200)

        notifier = TelegramNotifier(
            bot_token="tok", chat_ids=["-100"],
            failed_alerts_log_path=self.tmpdir / "failed.log",
            sentinel_dir=self.sentinel_dir,
            logger=_make_logger("tg"),
        )
        ctx = {"exception_type": "CapitalInvariantViolation", "delta": -999.0}
        result = notifier.send("CRITICAL", "Invariant breach", "LHS!=RHS", "capital.fund_manager",
                               context=ctx)

        data = _json.loads(result.sentinel_path.read_text(encoding="utf-8"))
        self.assertEqual(data["title"], "Invariant breach")
        self.assertEqual(data["body"], "LHS!=RHS")
        self.assertEqual(data["source_module"], "capital.fund_manager")
        self.assertEqual(data["context"], ctx)


def run_all_tests() -> int:
    tests = [
        TestAlertsPipeline("test_critical_send_then_watcher_delivers"),
        TestAlertsPipeline("test_telegram_failure_watcher_still_delivers"),
        TestAlertsPipeline("test_no_double_send_on_second_watcher_run"),
        TestAlertsPipeline("test_sentinel_json_content_matches_alert"),
    ]

    suite = unittest.TestSuite(tests)
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    result = runner.run(suite)

    passed = len(tests) - len(result.failures) - len(result.errors)
    total = len(tests)

    print("=" * 70)
    print("INTEGRATION: alerts pipeline (G8 end-to-end)")
    print("=" * 70)
    for t in tests:
        name = t._testMethodName
        failed_names = [str(f[0]) for f in result.failures + result.errors]
        status = "FAIL" if any(name in f for f in failed_names) else "OK"
        print(f"  {status}  {name}")
    print("=" * 70)

    if result.failures or result.errors:
        for label, items in [("FAILURES", result.failures), ("ERRORS", result.errors)]:
            for tc, tb in items:
                print(f"\n{label}: {tc}")
                print(tb)
        print(f"\nFAILED: {total - passed}/{total}")
        return 1

    print(f"PASSED: all {total} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
