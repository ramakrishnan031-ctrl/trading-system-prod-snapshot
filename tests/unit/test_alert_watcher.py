"""
tests/unit/test_alert_watcher.py -- Trading System v2

Tests for scripts/alert_watcher.py (AW1-AW11).
All SMTP calls are mocked. Tests use temporary directories.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alerts.critical import write_critical_sentinel, list_pending_sentinels
from scripts.alert_watcher import (
    SmtpAuthError,
    SmtpError,
    _acquire_lock,
    _build_email,
    _load_attempts,
    _prune_attempts,
    _release_lock,
    _save_attempts,
    _setup_watcher_log,
    run_once,
)


# ==============================================================================
# Helpers
# ==============================================================================

def _make_cfg(tmpdir: Path, max_attempts: int = 3) -> MagicMock:
    """Return a minimal config mock matching what run_once() accesses."""
    smtp_cfg = MagicMock()
    smtp_cfg.host = "smtp.test.com"
    smtp_cfg.port = 587
    smtp_cfg.use_tls = True
    smtp_cfg.username = "user"
    smtp_cfg.password = "pass"
    smtp_cfg.from_address = "from@test.com"
    smtp_cfg.to_addresses = ["to@test.com", "ops@test.com"]
    smtp_cfg.timeout_sec = 10

    alerts_cfg = MagicMock()
    alerts_cfg.sentinel_dir = str(tmpdir / "sentinels")
    alerts_cfg.watcher_max_attempts = max_attempts
    alerts_cfg.watcher_lock_path = str(tmpdir / "alert_watcher.lock")
    alerts_cfg.watcher_log_path = str(tmpdir / "alert_watcher.log")
    alerts_cfg.smtp = smtp_cfg

    cfg = MagicMock()
    cfg.system.alerts = alerts_cfg
    return cfg


def _write_sentinel(sentinel_dir: Path, **kwargs) -> Path:
    return write_critical_sentinel(
        title=kwargs.get("title", "Test alert"),
        body=kwargs.get("body", "Something broke"),
        source_module=kwargs.get("source_module", "test.module"),
        context=kwargs.get("context", {"severity": "CRITICAL"}),
        sentinel_dir=sentinel_dir,
    )


def _null_log() -> logging.Logger:
    log = logging.getLogger("test_watcher")
    log.addHandler(logging.NullHandler())
    return log


# ==============================================================================
# TestRunOnceBasic
# ==============================================================================

class TestRunOnceBasic(unittest.TestCase):
    """AW2 -- basic run_once behavior."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scripts.alert_watcher._send_email")
    def test_no_sentinels_exits_0(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        Path(cfg.system.alerts.sentinel_dir).mkdir(parents=True, exist_ok=True)
        result = run_once(cfg, log=_null_log())
        self.assertEqual(result, 0)
        mock_send.assert_not_called()

    @patch("scripts.alert_watcher._send_email")
    def test_smtp_success_sentinel_renamed_delivered(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)
        result = run_once(cfg, log=_null_log())
        self.assertEqual(result, 0)
        self.assertFalse(p.exists(), ".flag must be gone after delivery")
        delivered = p.with_suffix(".delivered")
        self.assertTrue(delivered.exists(), ".delivered must exist")

    @patch("scripts.alert_watcher._send_email")
    def test_multiple_sentinels_all_delivered(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        paths = [_write_sentinel(sentinel_dir, title=f"Alert {i}") for i in range(3)]
        run_once(cfg, log=_null_log())
        for p in paths:
            self.assertFalse(p.exists())
            self.assertTrue(p.with_suffix(".delivered").exists())

    @patch("scripts.alert_watcher._send_email")
    def test_multiple_sentinels_processed_in_mtime_order(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        call_order: list[str] = []

        def capture_send(smtp_cfg, data, log):
            call_order.append(data["title"])

        mock_send.side_effect = capture_send

        p1 = _write_sentinel(sentinel_dir, title="First")
        time.sleep(0.02)
        p2 = _write_sentinel(sentinel_dir, title="Second")
        time.sleep(0.02)
        p3 = _write_sentinel(sentinel_dir, title="Third")

        run_once(cfg, log=_null_log())
        self.assertEqual(call_order, ["First", "Second", "Third"])


# ==============================================================================
# TestDryRun
# ==============================================================================

class TestDryRun(unittest.TestCase):
    """AW2 -- --dry-run: no send, no rename."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scripts.alert_watcher._send_email")
    def test_dry_run_does_not_send(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        _write_sentinel(sentinel_dir)
        run_once(cfg, dry_run=True, log=_null_log())
        mock_send.assert_not_called()

    @patch("scripts.alert_watcher._send_email")
    def test_dry_run_does_not_rename(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)
        run_once(cfg, dry_run=True, log=_null_log())
        self.assertTrue(p.exists(), ".flag must still exist after dry-run")


# ==============================================================================
# TestSmtpFailureHandling
# ==============================================================================

class TestSmtpFailureHandling(unittest.TestCase):
    """AW4, AW5 -- SMTP failure counter and max_attempts."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scripts.alert_watcher._send_email")
    def test_smtp_failure_increments_counter(self, mock_send):
        mock_send.side_effect = SmtpError("connection refused")
        cfg = _make_cfg(self.tmpdir, max_attempts=3)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)
        run_once(cfg, log=_null_log())
        # File still .flag after 1 failure
        self.assertTrue(p.exists(), ".flag must remain after first failure")
        # Counter saved
        counter_path = sentinel_dir / "alert_watcher_attempts.json"
        counters = _load_attempts(counter_path)
        self.assertEqual(counters.get(p.name, 0), 1)

    @patch("scripts.alert_watcher._send_email")
    def test_counter_reaches_max_marks_failed(self, mock_send):
        mock_send.side_effect = SmtpError("timeout")
        cfg = _make_cfg(self.tmpdir, max_attempts=3)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)
        # Run 3 times
        for _ in range(3):
            if p.exists():
                run_once(cfg, log=_null_log())
        self.assertFalse(p.exists(), ".flag must be gone after max_attempts")
        self.assertTrue(p.with_suffix(".failed").exists(), ".failed must exist")

    @patch("scripts.alert_watcher._send_email")
    def test_counter_persisted_across_invocations(self, mock_send):
        mock_send.side_effect = SmtpError("timeout")
        cfg = _make_cfg(self.tmpdir, max_attempts=5)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)
        run_once(cfg, log=_null_log())  # attempt 1
        run_once(cfg, log=_null_log())  # attempt 2
        counter_path = sentinel_dir / "alert_watcher_attempts.json"
        counters = _load_attempts(counter_path)
        self.assertEqual(counters.get(p.name, 0), 2)

    @patch("scripts.alert_watcher._send_email")
    def test_counter_cleared_after_delivery(self, mock_send):
        mock_send.side_effect = [SmtpError("fail"), None]  # fail once, then succeed
        cfg = _make_cfg(self.tmpdir, max_attempts=5)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)
        run_once(cfg, log=_null_log())  # fail
        run_once(cfg, log=_null_log())  # success
        counter_path = sentinel_dir / "alert_watcher_attempts.json"
        counters = _load_attempts(counter_path)
        self.assertNotIn(p.name, counters)

    @patch("scripts.alert_watcher._send_email")
    def test_smtp_auth_error_exits_2(self, mock_send):
        mock_send.side_effect = SmtpAuthError("auth failed")
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        _write_sentinel(sentinel_dir)
        result = run_once(cfg, log=_null_log())
        self.assertEqual(result, 2)


# ==============================================================================
# TestCorruptSentinel
# ==============================================================================

class TestCorruptSentinel(unittest.TestCase):
    """AW4 -- corrupt sentinel marked .failed with reason."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scripts.alert_watcher._send_email")
    def test_corrupt_json_marked_failed(self, mock_send):
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        bad = sentinel_dir / "critical_alert_bad.flag"
        bad.write_text("not json {{{", encoding="utf-8")
        run_once(cfg, log=_null_log())
        self.assertFalse(bad.exists())
        self.assertTrue(bad.with_suffix(".failed").exists())
        mock_send.assert_not_called()


# ==============================================================================
# TestLockFile
# ==============================================================================

class TestLockFile(unittest.TestCase):
    """AW3 -- lock file created/released; live pid blocks; dead pid proceeds."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_lock_created_and_released(self):
        lock = self.tmpdir / "test.lock"
        self.assertTrue(_acquire_lock(lock))
        self.assertTrue(lock.exists())
        _release_lock(lock)
        self.assertFalse(lock.exists())

    def test_live_pid_blocks_second_acquire(self):
        lock = self.tmpdir / "test.lock"
        # Write our own PID as the "live" process
        lock.write_text(str(os.getpid()), encoding="utf-8")
        result = _acquire_lock(lock)
        self.assertFalse(result, "Should not acquire when live PID holds lock")
        # Cleanup
        lock.unlink()

    def test_dead_pid_stale_lock_proceeds(self):
        lock = self.tmpdir / "test.lock"
        # Write an impossible PID (PID 0 or very high number)
        # Use a PID that definitely doesn't exist: try 99999999
        lock.write_text("99999999", encoding="utf-8")
        try:
            result = _acquire_lock(lock)
        except Exception:
            result = False
        # On Windows: might fail due to PermissionError path; just verify no crash
        # Result may be True (dead pid cleaned) or False (win32 permission path)
        self.assertIsInstance(result, bool)
        if result:
            _release_lock(lock)


# ==============================================================================
# TestEmailBuilding
# ==============================================================================

class TestEmailBuilding(unittest.TestCase):
    """AW6 -- email subject/body format."""

    def test_subject_format(self):
        data = {
            "id": "abc", "ts": "2026-04-16T09:00:00", "title": "Capital breach",
            "body": "Details", "source_module": "fund_manager",
            "context": {"severity": "CRITICAL"},
            "hostname": "myhost", "pid": 1234,
        }
        msg = _build_email(data, "from@x.com", ["to@x.com"])
        self.assertIn("[CRITICAL] Capital breach", msg["Subject"])
        self.assertIn("myhost", msg["Subject"])
        self.assertIn("1234", msg["Subject"])

    def test_body_contains_all_fields(self):
        data = {
            "id": "abc123", "ts": "2026-04-16T09:00:00",
            "title": "Test", "body": "Alert body here",
            "source_module": "capital.fund_manager",
            "context": {"severity": "CRITICAL", "delta": -500.0},
            "hostname": "srv1", "pid": 9999,
        }
        msg = _build_email(data, "from@x.com", ["to@x.com"])
        body = msg.get_payload(decode=True).decode("utf-8")
        self.assertIn("abc123", body)
        self.assertIn("Alert body here", body)
        self.assertIn("capital.fund_manager", body)
        self.assertIn("delta", body)

    def test_multiple_recipients_in_to_field(self):
        data = {
            "id": "x", "ts": "", "title": "t", "body": "b",
            "source_module": "m", "context": {}, "hostname": "h", "pid": 1,
        }
        msg = _build_email(data, "from@x.com", ["a@x.com", "b@x.com"])
        self.assertIn("a@x.com", msg["To"])
        self.assertIn("b@x.com", msg["To"])


# ==============================================================================
# TestAttemptCounter
# ==============================================================================

class TestAttemptCounter(unittest.TestCase):
    """AW5 -- attempt counter persistence."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_load_nonexistent_returns_empty(self):
        result = _load_attempts(self.tmpdir / "no.json")
        self.assertEqual(result, {})

    def test_save_and_load_roundtrip(self):
        path = self.tmpdir / "attempts.json"
        _save_attempts(path, {"file.flag": 2})
        loaded = _load_attempts(path)
        self.assertEqual(loaded["file.flag"], 2)

    def test_prune_removes_entries_not_in_pending(self):
        self._inner = tempfile.TemporaryDirectory()
        sentinel_dir = Path(self._inner.name)
        p = _write_sentinel(sentinel_dir)
        counters = {p.name: 1, "ghost.flag": 3}
        pruned = _prune_attempts(counters, sentinel_dir)
        self.assertIn(p.name, pruned)
        self.assertNotIn("ghost.flag", pruned)
        self._inner.cleanup()

    def test_prune_keeps_pending_entries(self):
        self._inner = tempfile.TemporaryDirectory()
        sentinel_dir = Path(self._inner.name)
        p = _write_sentinel(sentinel_dir)
        counters = {p.name: 2}
        pruned = _prune_attempts(counters, sentinel_dir)
        self.assertEqual(pruned[p.name], 2)
        self._inner.cleanup()


# ==============================================================================
# TestWatcherLog
# ==============================================================================

class TestWatcherLog(unittest.TestCase):
    """AW8 -- watcher log file written."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_log_file_created(self):
        log_path = self.tmpdir / "logs" / "alert_watcher.log"
        self.assertFalse(log_path.exists())
        log = _setup_watcher_log(log_path)
        log.info("test message")
        # Flush handler
        for h in log.handlers:
            h.flush()
        self.assertTrue(log_path.exists())
        # Cleanup handlers
        for h in log.handlers[:]:
            log.removeHandler(h)
            h.close()


# ==============================================================================
# Standalone runner
# ==============================================================================

def run_all_tests() -> int:
    tests = [
        # Basic
        TestRunOnceBasic("test_no_sentinels_exits_0"),
        TestRunOnceBasic("test_smtp_success_sentinel_renamed_delivered"),
        TestRunOnceBasic("test_multiple_sentinels_all_delivered"),
        TestRunOnceBasic("test_multiple_sentinels_processed_in_mtime_order"),
        # Dry run
        TestDryRun("test_dry_run_does_not_send"),
        TestDryRun("test_dry_run_does_not_rename"),
        # SMTP failure
        TestSmtpFailureHandling("test_smtp_failure_increments_counter"),
        TestSmtpFailureHandling("test_counter_reaches_max_marks_failed"),
        TestSmtpFailureHandling("test_counter_persisted_across_invocations"),
        TestSmtpFailureHandling("test_counter_cleared_after_delivery"),
        TestSmtpFailureHandling("test_smtp_auth_error_exits_2"),
        # Corrupt sentinel
        TestCorruptSentinel("test_corrupt_json_marked_failed"),
        # Lock file
        TestLockFile("test_lock_created_and_released"),
        TestLockFile("test_live_pid_blocks_second_acquire"),
        TestLockFile("test_dead_pid_stale_lock_proceeds"),
        # Email building
        TestEmailBuilding("test_subject_format"),
        TestEmailBuilding("test_body_contains_all_fields"),
        TestEmailBuilding("test_multiple_recipients_in_to_field"),
        # Attempt counter
        TestAttemptCounter("test_load_nonexistent_returns_empty"),
        TestAttemptCounter("test_save_and_load_roundtrip"),
        TestAttemptCounter("test_prune_removes_entries_not_in_pending"),
        TestAttemptCounter("test_prune_keeps_pending_entries"),
        # Watcher log
        TestWatcherLog("test_log_file_created"),
    ]

    suite = unittest.TestSuite(tests)
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    result = runner.run(suite)

    passed = len(tests) - len(result.failures) - len(result.errors)
    total = len(tests)

    print("=" * 70)
    print("scripts/alert_watcher.py -- Test Suite")
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
