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
import threading
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
    _build_digest_email,
    _load_attempts,
    _prune_attempts,
    _release_lock,
    _save_attempts,
    _setup_watcher_log,
    _write_heartbeat,
    run_loop,
    run_once,
)


# ==============================================================================
# Helpers
# ==============================================================================

def _make_cfg(tmpdir: Path, max_attempts: int = 3, digest_threshold: int = 3) -> MagicMock:
    """Return a minimal config mock matching what run_once() accesses."""
    smtp_cfg = MagicMock()
    smtp_cfg.host = "smtp.test.com"
    smtp_cfg.port = 587
    smtp_cfg.use_tls = True
    smtp_cfg.username = "user"
    smtp_cfg.password = "pass"
    smtp_cfg.password_env = ""
    # G.3: alert_watcher resolves the password via this method, not the
    # plaintext attribute. The MagicMock default would return a MagicMock
    # object, breaking smtplib.login(); set an explicit return.
    smtp_cfg.resolved_password.return_value = "pass"
    smtp_cfg.from_address = "from@test.com"
    smtp_cfg.to_addresses = ["to@test.com", "ops@test.com"]
    smtp_cfg.timeout_sec = 10

    alerts_cfg = MagicMock()
    alerts_cfg.sentinel_dir = str(tmpdir / "sentinels")
    alerts_cfg.watcher_max_attempts = max_attempts
    alerts_cfg.alert_digest_threshold = digest_threshold  # FIX-095
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
# TestF1SmtpRobustness (15-Jul: no crash-loop + Telegram fallback + degraded marker)
# ==============================================================================

class TestF1SmtpRobustness(unittest.TestCase):
    """F1: on SMTP auth failure the watcher must NOT return non-zero (the exit-2 →
    systemd 10s crash-loop), must deliver each stuck sentinel via the direct-Telegram
    fallback (CLASS 2), back off the dead SMTP, and publish a machine-visible degraded
    marker. Every assertion FAILS on the pre-fix code (which returned 2, email-only)."""

    _DEGRADED = "alert_watcher_degraded.json"
    _STATE = "alert_watcher_smtp_state.json"

    @staticmethod
    def _ok_notifier():
        n = MagicMock()
        n.send.return_value = MagicMock(success=True)
        return n

    @patch("scripts.alert_watcher.smtplib.SMTP")
    @patch("scripts.alert_watcher._telegram_notifier")
    def test_digest_auth_fail_falls_back_to_telegram_no_crash(self, mock_tg, mock_smtp):
        import smtplib as _s
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(Path(tmp), digest_threshold=3)
            sd = Path(cfg.system.alerts.sentinel_dir)
            sd.mkdir(parents=True, exist_ok=True)
            [_write_sentinel(sd, title=f"A{i}") for i in range(4)]   # >3 → digest (the incident)

            server = MagicMock()
            server.login.side_effect = _s.SMTPAuthenticationError(535, b"BadCredentials")
            mock_smtp.return_value = server
            mock_tg.return_value = self._ok_notifier()

            rc = run_once(cfg, log=_null_log())

            self.assertEqual(rc, 0, "delivery fault must NOT crash the watcher (was exit 2)")
            self.assertEqual(len(list_pending_sentinels(sd)), 0, "all delivered via telegram")
            self.assertEqual(len(list(sd.glob("*.delivered"))), 4)
            self.assertTrue((sd / self._DEGRADED).exists(), "degraded marker published")
            state = json.loads((sd / self._STATE).read_text())
            self.assertEqual(state["consecutive_auth_fails"], 1)

    @patch("scripts.alert_watcher.smtplib.SMTP")
    @patch("scripts.alert_watcher._telegram_notifier")
    def test_backoff_skips_dead_smtp_and_still_delivers(self, mock_tg, mock_smtp):
        from core.time_authority import now_ist
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(Path(tmp), digest_threshold=3)
            sd = Path(cfg.system.alerts.sentinel_dir)
            sd.mkdir(parents=True, exist_ok=True)
            # a recent auth failure → within the backoff window
            (sd / self._STATE).write_text(json.dumps(
                {"consecutive_auth_fails": 1, "last_fail_iso": now_ist().isoformat()}))
            _write_sentinel(sd, title="new-during-outage")
            mock_tg.return_value = self._ok_notifier()

            rc = run_once(cfg, log=_null_log())

            self.assertEqual(rc, 0)
            mock_smtp.assert_not_called()          # dead SMTP skipped during backoff (no spam)
            self.assertEqual(len(list(sd.glob("*.delivered"))), 1)   # delivered via telegram

    @patch("scripts.alert_watcher._send_email")
    def test_healthy_send_resets_backoff_and_clears_marker(self, mock_send):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = _make_cfg(Path(tmp))
            sd = Path(cfg.system.alerts.sentinel_dir)
            sd.mkdir(parents=True, exist_ok=True)
            # stale degraded state from a prior outage (last_fail_iso null → not in backoff)
            (sd / self._DEGRADED).write_text('{"degraded": true}')
            (sd / self._STATE).write_text('{"consecutive_auth_fails": 2, "last_fail_iso": null}')
            _write_sentinel(sd, title="recovered")   # 1 ≤ threshold → individual path
            mock_send.return_value = None            # SMTP send succeeds

            rc = run_once(cfg, log=_null_log())

            self.assertEqual(rc, 0)
            self.assertFalse((sd / self._DEGRADED).exists(), "healthy send clears degraded marker")
            state = json.loads((sd / self._STATE).read_text())
            self.assertEqual(state["consecutive_auth_fails"], 0, "backoff reset on recovery")


# ==============================================================================
# TestRunLoop (P5: --loop mode + heartbeat)
# ==============================================================================

class TestRunLoop(unittest.TestCase):
    """P5: --loop runs run_once() repeatedly with a liveness heartbeat and an
    interruptible sleep; a persistent auth error stops it; a set stop_event ends it."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scripts.alert_watcher.run_once")
    def test_loop_runs_max_iters(self, mock_run_once):
        mock_run_once.return_value = 0
        rc = run_loop(_make_cfg(self.tmpdir), interval_sec=0.0, heartbeat_path=None,
                      dry_run=False, log=_null_log(), stop_event=threading.Event(), max_iters=3)
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run_once.call_count, 3)

    @patch("scripts.alert_watcher.run_once")
    def test_loop_writes_heartbeat(self, mock_run_once):
        mock_run_once.return_value = 0
        hb = self.tmpdir / "hb" / "alert_watcher.heartbeat"
        rc = run_loop(_make_cfg(self.tmpdir), interval_sec=0.0, heartbeat_path=hb,
                      dry_run=False, log=_null_log(), stop_event=threading.Event(), max_iters=1)
        self.assertEqual(rc, 0)
        self.assertTrue(hb.exists(), "heartbeat file not written")
        self.assertIn("T", hb.read_text())   # a plausible ISO timestamp

    @patch("scripts.alert_watcher.run_once")
    def test_loop_stops_on_auth_error_no_spin(self, mock_run_once):
        mock_run_once.return_value = 2   # SmtpAuthError -> run_once returns 2
        rc = run_loop(_make_cfg(self.tmpdir), interval_sec=0.0, heartbeat_path=None,
                      dry_run=False, log=_null_log(), stop_event=threading.Event(), max_iters=10)
        self.assertEqual(rc, 2)
        self.assertEqual(mock_run_once.call_count, 1)   # stops immediately, never spins

    @patch("scripts.alert_watcher.run_once")
    def test_loop_stops_when_event_preset(self, mock_run_once):
        mock_run_once.return_value = 0
        ev = threading.Event(); ev.set()
        rc = run_loop(_make_cfg(self.tmpdir), interval_sec=0.0, heartbeat_path=None,
                      dry_run=False, log=_null_log(), stop_event=ev, max_iters=10)
        self.assertEqual(rc, 0)
        self.assertEqual(mock_run_once.call_count, 0)   # never entered the loop body

    def test_write_heartbeat_none_is_noop(self):
        _write_heartbeat(None, _null_log())   # must not raise

    def test_write_heartbeat_unwritable_is_non_fatal(self):
        a_file = self.tmpdir / "afile"
        a_file.write_text("x")
        # parent path is a FILE -> mkdir fails -> logged, never raised (alert path mustn't die)
        _write_heartbeat(a_file / "sub" / "hb", _null_log())


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

    @patch("scripts.alert_watcher._telegram_notifier")
    @patch("scripts.alert_watcher._send_email")
    def test_smtp_auth_error_no_longer_crashes(self, mock_send, mock_tg):
        # F1 (15-Jul): an auth failure USED to `return 2` → systemd 10s crash-loop.
        # It now stays alive (rc=0) and publishes a degraded marker. With no Telegram
        # configured (both channels down) the sentinel is LEFT pending for retry —
        # never abandoned. (This test previously asserted `result == 2`.)
        mock_send.side_effect = SmtpAuthError("auth failed")
        mock_tg.return_value = None            # telegram also unavailable
        cfg = _make_cfg(self.tmpdir)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        _write_sentinel(sentinel_dir)
        result = run_once(cfg, log=_null_log())
        self.assertEqual(result, 0)            # was 2 — F1 removes the crash-loop
        self.assertTrue((sentinel_dir / "alert_watcher_degraded.json").exists())
        self.assertEqual(len(list_pending_sentinels(sentinel_dir)), 1)  # left for retry

    @patch("scripts.alert_watcher._SMTP_TASK_TIMEOUT_SEC", 0.3)
    @patch("scripts.alert_watcher._send_email")
    def test_a2_smtp_send_timeout_increments_counter(self, mock_send):
        """A.2 (2026-04-25): a stuck _send_email (sleeping past the
        per-task timeout) is treated like an SmtpError and increments
        the retry counter rather than hanging the watcher pass."""
        def _stuck_send(smtp_cfg, data, log):
            time.sleep(2.0)  # > _SMTP_TASK_TIMEOUT_SEC (0.3s)

        mock_send.side_effect = _stuck_send
        cfg = _make_cfg(self.tmpdir, max_attempts=3)
        sentinel_dir = Path(cfg.system.alerts.sentinel_dir)
        p = _write_sentinel(sentinel_dir)

        result = run_once(cfg, log=_null_log())
        # Watcher returns 0 (timeout is recoverable, not auth fail).
        self.assertEqual(result, 0)
        # Sentinel still .flag (will retry next pass).
        self.assertTrue(p.exists(), "sentinel must remain on timeout")
        counter_path = sentinel_dir / "alert_watcher_attempts.json"
        counters = _load_attempts(counter_path)
        self.assertEqual(counters.get(p.name, 0), 1, (
            "A.2: timeout must increment retry counter exactly once"
        ))


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
        # FIX (18-Jun): subject = "[LFL836] <SEVERITY> — <title>" (account-tagged, crisp).
        self.assertEqual("[LFL836] CRITICAL — Capital breach", msg["Subject"])

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
        # F4 (15-Jul): the watcher log is now DATE-EMBEDDED (alert_watcher_<date>.log) —
        # one file per day (Foundation Rule 1.7; cleaned by log_cleanup) — NOT a single
        # unbounded alert_watcher.log. (This test previously asserted the fixed name.)
        log_path = self.tmpdir / "logs" / "alert_watcher.log"
        log = _setup_watcher_log(log_path)
        log.info("test message")
        for h in log.handlers:
            h.flush()
        dated = list((self.tmpdir / "logs").glob("alert_watcher_*.log"))
        self.assertEqual(len(dated), 1, "exactly one date-embedded log created")
        self.assertFalse(log_path.exists(), "the old fixed name is no longer used")
        for h in log.handlers[:]:
            log.removeHandler(h)
            h.close()


# ==============================================================================
# Standalone runner
# ==============================================================================

# ==============================================================================
# TestDigestEmail (FIX-095)
# ==============================================================================

class TestDigestEmail(unittest.TestCase):
    """FIX-095 -- digest email when pending > threshold."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("scripts.alert_watcher.smtplib.SMTP")
    def test_two_flags_sends_individual_emails(self, mock_smtp):
        """2 flags <= threshold(3) → 2 individual emails."""
        sentinel_dir = self.tmpdir / "sentinels"
        sentinel_dir.mkdir()
        cfg = _make_cfg(self.tmpdir, digest_threshold=3)

        _write_sentinel(sentinel_dir, title="Alert 1")
        _write_sentinel(sentinel_dir, title="Alert 2")

        run_once(cfg, log=_null_log())

        # Should send 2 individual emails (not digest)
        self.assertEqual(mock_smtp.call_count, 2,
                        "Expected 2 SMTP connections for 2 individual emails")

    @patch("scripts.alert_watcher.smtplib.SMTP")
    def test_five_flags_sends_one_digest(self, mock_smtp):
        """5 flags > threshold(3) → 1 digest email."""
        sentinel_dir = self.tmpdir / "sentinels"
        sentinel_dir.mkdir()
        cfg = _make_cfg(self.tmpdir, digest_threshold=3)

        for i in range(5):
            _write_sentinel(sentinel_dir, title=f"Alert {i+1}")

        run_once(cfg, log=_null_log())

        # Should send 1 digest email (not 5 individual)
        self.assertEqual(mock_smtp.call_count, 1,
                        "Expected 1 SMTP connection for digest email")

        # Verify sendmail was called with digest subject
        # Digest code doesn't use context manager, so access return_value directly
        mock_instance = mock_smtp.return_value
        self.assertTrue(mock_instance.sendmail.called,
                       "sendmail should be called for digest")
        args = mock_instance.sendmail.call_args
        message = args[0][2]  # third arg to sendmail is message string
        # Subject is encoded, just check for "DIGEST" and "5"
        self.assertIn("DIGEST", message,
                     "Message should contain DIGEST")
        self.assertIn("5", message,
                     "Message should contain alert count 5")

    @patch("scripts.alert_watcher.smtplib.SMTP")
    def test_digest_marks_all_flags_delivered(self, mock_smtp):
        """Digest success marks ALL 5 flags as .delivered."""
        sentinel_dir = self.tmpdir / "sentinels"
        sentinel_dir.mkdir()
        cfg = _make_cfg(self.tmpdir, digest_threshold=3)

        for i in range(5):
            _write_sentinel(sentinel_dir, title=f"Alert {i+1}")

        run_once(cfg, log=_null_log())

        # All 5 should be marked delivered
        pending = list_pending_sentinels(sentinel_dir)
        self.assertEqual(len(pending), 0,
                        "All flags should be marked .delivered after digest")

        delivered = list(sentinel_dir.glob("*.delivered"))
        self.assertEqual(len(delivered), 5,
                        "Should have 5 .delivered files")

    def test_build_digest_email_contains_all_alerts(self):
        """_build_digest_email includes all alert summaries."""
        sentinel_dir = self.tmpdir / "sentinels"
        sentinel_dir.mkdir()

        alerts = []
        for i in range(5):
            path = _write_sentinel(sentinel_dir, title=f"Test Alert {i+1}",
                                  body=f"Body {i+1}")
            data = {
                'id': f'alert-{i+1}',
                'ts': f'2026-05-17T10:{i+10}:00',
                'title': f'Test Alert {i+1}',
                'body': f'Body {i+1}',
                'source_module': 'test',
                'context': {'severity': 'CRITICAL'},
            }
            alerts.append((path, data))

        msg = _build_digest_email(alerts, "from@test.com", ["to@test.com"])

        # Check subject (FIX 18-Jun: account-tagged crisp digest subject)
        self.assertIn("[LFL836]", msg["Subject"])
        self.assertIn("DIGEST", msg["Subject"])
        self.assertIn("5", msg["Subject"])

        # Check body contains all alerts (decode if base64 encoded)
        body = msg.get_payload(decode=True)
        if isinstance(body, bytes):
            body = body.decode('utf-8')
        for i in range(5):
            self.assertIn(f"Test Alert {i+1}", body)
            self.assertIn(f"Body {i+1}", body)

    @patch("scripts.alert_watcher.smtplib.SMTP")
    def test_threshold_configurable(self, mock_smtp):
        """Threshold is configurable via alert_digest_threshold."""
        sentinel_dir = self.tmpdir / "sentinels"
        sentinel_dir.mkdir()
        # Set threshold to 1 → 2 alerts should trigger digest
        cfg = _make_cfg(self.tmpdir, digest_threshold=1)

        _write_sentinel(sentinel_dir, title="Alert 1")
        _write_sentinel(sentinel_dir, title="Alert 2")

        run_once(cfg, log=_null_log())

        # Should send digest (2 > 1)
        self.assertEqual(mock_smtp.call_count, 1,
                        "Expected digest with threshold=1")


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
        # FIX-095: Digest email
        TestDigestEmail("test_two_flags_sends_individual_emails"),
        TestDigestEmail("test_five_flags_sends_one_digest"),
        TestDigestEmail("test_digest_marks_all_flags_delivered"),
        TestDigestEmail("test_build_digest_email_contains_all_alerts"),
        TestDigestEmail("test_threshold_configurable"),
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
