"""
tests/unit/test_telegram_notifier.py -- Trading System v2

Tests for alerts/telegram_notifier.py (TG1-TG12).
All HTTP calls are mocked via unittest.mock.patch.
All tests use temporary directories.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alerts.telegram_notifier import ChannelConfig, TelegramNotifier, SendResult, _format_message
from alerts.critical import list_pending_sentinels


# ==============================================================================
# Helpers
# ==============================================================================

def _make_notifier(tmpdir: Path, **kwargs) -> TelegramNotifier:
    log = logging.getLogger("test_tg")
    log.addHandler(logging.NullHandler())
    return TelegramNotifier(
        bot_token=kwargs.get("bot_token", "test-token-123"),
        chat_ids=kwargs.get("chat_ids", ["-100111", "-100222", "-100333"]),
        failed_alerts_log_path=tmpdir / "failed_alerts.log",
        sentinel_dir=tmpdir / "sentinels",
        logger=log,
        timeout_sec=kwargs.get("timeout_sec", 5.0),
        max_retries=kwargs.get("max_retries", 2),
        paper_mode=kwargs.get("paper_mode", False),
        enabled=kwargs.get("enabled", True),
    )


def _mock_ok():
    """Return a mock requests.Response with status 200."""
    resp = MagicMock()
    resp.status_code = 200
    return resp


def _mock_status(code: int, retry_after: str | None = None):
    resp = MagicMock()
    resp.status_code = code
    resp.headers = {}
    if retry_after is not None:
        resp.headers["Retry-After"] = retry_after
    return resp


# ==============================================================================
# TestConstructorValidation
# ==============================================================================

class TestConstructorValidation(unittest.TestCase):
    """TG10 -- constructor validation."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_empty_token_raises_value_error(self):
        with self.assertRaises(ValueError):
            _make_notifier(self.tmpdir, bot_token="")

    def test_whitespace_token_raises_value_error(self):
        with self.assertRaises(ValueError):
            _make_notifier(self.tmpdir, bot_token="   ")

    def test_empty_chat_ids_raises_value_error(self):
        with self.assertRaises(ValueError):
            _make_notifier(self.tmpdir, chat_ids=[])

    def test_valid_construction_succeeds(self):
        n = _make_notifier(self.tmpdir)
        self.assertIsInstance(n, TelegramNotifier)


# ==============================================================================
# TestInfoWarnTier
# ==============================================================================

class TestInfoWarnTier(unittest.TestCase):
    """TG4 -- INFO/WARN: attempt send, drop silently on failure."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_info_send_success_calls_post_per_chat(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir, chat_ids=["-100111", "-100222"])
        result = n.send("INFO", "test", "body", "mod")
        self.assertEqual(mock_post.call_count, 2)
        self.assertTrue(result.success)
        self.assertEqual(result.tier, "INFO")

    @patch("alerts.telegram_notifier.requests.post")
    def test_info_send_failure_drops_silently(self, mock_post):
        mock_post.return_value = _mock_status(500)
        n = _make_notifier(self.tmpdir, max_retries=0)
        result = n.send("INFO", "test", "body", "mod")
        self.assertFalse(result.success)
        self.assertFalse(result.failed_log_written)
        # No sentinel, no failed log
        self.assertIsNone(result.sentinel_path)
        failed_log = self.tmpdir / "failed_alerts.log"
        self.assertFalse(failed_log.exists())

    @patch("alerts.telegram_notifier.requests.post")
    def test_warn_send_failure_drops_silently(self, mock_post):
        mock_post.return_value = _mock_status(401)
        n = _make_notifier(self.tmpdir, max_retries=0)
        result = n.send("WARN", "test", "body", "mod")
        self.assertFalse(result.success)
        self.assertFalse(result.failed_log_written)


# ==============================================================================
# TestErrorTier
# ==============================================================================

class TestErrorTier(unittest.TestCase):
    """TG4, TG8 -- ERROR: attempt, write failed_alerts.log on failure."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_error_send_success_no_failed_log(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir)
        result = n.send("ERROR", "title", "body", "mod")
        self.assertTrue(result.success)
        self.assertFalse(result.failed_log_written)
        self.assertFalse((self.tmpdir / "failed_alerts.log").exists())

    @patch("alerts.telegram_notifier.requests.post")
    def test_error_send_failure_writes_failed_log(self, mock_post):
        mock_post.return_value = _mock_status(500)
        n = _make_notifier(self.tmpdir, max_retries=0)
        result = n.send("ERROR", "title", "body", "mod", context={"k": "v"})
        self.assertTrue(result.failed_log_written)
        log_path = self.tmpdir / "failed_alerts.log"
        self.assertTrue(log_path.exists())

    @patch("alerts.telegram_notifier.requests.post")
    def test_failed_log_has_all_8_fields(self, mock_post):
        mock_post.return_value = _mock_status(500)
        n = _make_notifier(self.tmpdir, max_retries=0)
        n.send("ERROR", "title", "body", "mod.src", context={"a": 1})
        lines = (self.tmpdir / "failed_alerts.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        record = json.loads(lines[0])
        for field in ("ts", "severity", "title", "body", "source_module",
                      "context", "telegram_error", "chat_ids_attempted"):
            self.assertIn(field, record, f"Missing field: {field}")

    @patch("alerts.telegram_notifier.requests.post")
    def test_failed_log_appends_multiple_failures(self, mock_post):
        mock_post.return_value = _mock_status(500)
        n = _make_notifier(self.tmpdir, max_retries=0)
        n.send("ERROR", "t1", "b1", "mod")
        n.send("ERROR", "t2", "b2", "mod")
        lines = (self.tmpdir / "failed_alerts.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 2)


# ==============================================================================
# TestCriticalTier
# ==============================================================================

class TestCriticalTier(unittest.TestCase):
    """TG5 -- CRITICAL: sentinel first, then Telegram."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_critical_success_sentinel_written_first(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir)
        result = n.send("CRITICAL", "Breach", "body", "fund_manager")
        self.assertIsNotNone(result.sentinel_path)
        self.assertTrue(result.sentinel_path.exists())

    @patch("alerts.telegram_notifier.requests.post")
    def test_critical_telegram_failure_sentinel_still_written(self, mock_post):
        mock_post.return_value = _mock_status(500)
        n = _make_notifier(self.tmpdir, max_retries=0)
        result = n.send("CRITICAL", "Breach", "body", "fund_manager")
        self.assertIsNotNone(result.sentinel_path)
        self.assertTrue(result.sentinel_path.exists(), "Sentinel must exist even if Telegram fails")

    @patch("alerts.telegram_notifier.write_critical_sentinel")
    @patch("alerts.telegram_notifier.requests.post")
    def test_critical_sentinel_write_failure_logs_error(self, mock_post, mock_sentinel):
        mock_sentinel.side_effect = OSError("disk full")
        mock_post.return_value = _mock_ok()
        log = MagicMock()
        n = TelegramNotifier(
            bot_token="tok", chat_ids=["-100"],
            failed_alerts_log_path=self.tmpdir / "f.log",
            sentinel_dir=self.tmpdir / "s",
            logger=log, max_retries=0,
        )
        result = n.send("CRITICAL", "t", "b", "mod")
        log.error.assert_called()  # logger.error must be called

    @patch("alerts.telegram_notifier.requests.post")
    def test_critical_send_result_has_sentinel_path(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir)
        result = n.send("CRITICAL", "t", "b", "m")
        self.assertIsInstance(result.sentinel_path, Path)


# ==============================================================================
# TestMultiChat
# ==============================================================================

class TestMultiChat(unittest.TestCase):
    """TG6 -- multi-chat sends; per-chat success/fail tracking."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_all_chats_receive_message(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir, chat_ids=["a", "b", "c"])
        result = n.send("INFO", "t", "b", "m")
        self.assertEqual(mock_post.call_count, 3)
        self.assertEqual(len(result.delivered_to), 3)

    @patch("alerts.telegram_notifier.requests.post")
    def test_partial_failure_tracked_per_chat(self, mock_post):
        # First chat 200, second 500, third 200
        mock_post.side_effect = [_mock_ok(), _mock_status(500), _mock_ok()]
        n = _make_notifier(self.tmpdir, chat_ids=["a", "b", "c"], max_retries=0)
        result = n.send("INFO", "t", "b", "m")
        self.assertEqual(len(result.delivered_to), 2)
        self.assertEqual(len(result.failed_to), 1)
        self.assertIn("b", result.failed_to)


# ==============================================================================
# TestRetryLogic
# ==============================================================================

class TestRetryLogic(unittest.TestCase):
    """TG6 -- retry behavior."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.time.sleep")
    @patch("alerts.telegram_notifier.requests.post")
    def test_http_429_sleeps_and_retries_once(self, mock_post, mock_sleep):
        mock_post.side_effect = [_mock_status(429, retry_after="2"), _mock_ok()]
        n = _make_notifier(self.tmpdir, chat_ids=["a"])
        result = n.send("INFO", "t", "b", "m")
        mock_sleep.assert_called_once()
        self.assertTrue(result.success)

    @patch("alerts.telegram_notifier.time.sleep")
    @patch("alerts.telegram_notifier.requests.post")
    def test_fix097_retry_after_integer(self, mock_post, mock_sleep):
        """FIX-097: Integer Retry-After → use it (capped at 5s)."""
        mock_post.side_effect = [_mock_status(429, retry_after="45"), _mock_ok()]
        n = _make_notifier(self.tmpdir, chat_ids=["a"])
        result = n.send("INFO", "t", "b", "m")
        # Should sleep min(45.0, 5.0) = 5.0
        mock_sleep.assert_called_once_with(5.0)
        self.assertTrue(result.success)

    @patch("alerts.telegram_notifier.time.sleep")
    @patch("alerts.telegram_notifier.requests.post")
    def test_fix097_retry_after_http_date_fallback(self, mock_post, mock_sleep):
        """FIX-097: HTTP-date Retry-After → fallback 30s (capped at 5s)."""
        # HTTP-date format (not parseable as int/float)
        mock_post.side_effect = [
            _mock_status(429, retry_after="Fri, 31 Dec 1999 23:59:59 GMT"),
            _mock_ok()
        ]
        n = _make_notifier(self.tmpdir, chat_ids=["a"])
        result = n.send("INFO", "t", "b", "m")
        # Should fallback to 30s, capped at 5s → sleep(5.0)
        mock_sleep.assert_called_once_with(5.0)
        self.assertTrue(result.success)

    @patch("alerts.telegram_notifier.time.sleep")
    @patch("alerts.telegram_notifier.requests.post")
    def test_fix097_retry_after_missing_uses_default(self, mock_post, mock_sleep):
        """FIX-097: Missing Retry-After → fallback 30s (capped at 5s)."""
        resp = _mock_status(429)  # no retry_after param = no header
        mock_post.side_effect = [resp, _mock_ok()]
        n = _make_notifier(self.tmpdir, chat_ids=["a"])
        result = n.send("INFO", "t", "b", "m")
        # Should use default "30" from get(), capped at 5s
        mock_sleep.assert_called_once_with(5.0)
        self.assertTrue(result.success)

    @patch("alerts.telegram_notifier.time.sleep")
    @patch("alerts.telegram_notifier.requests.post")
    def test_http_500_retries_with_backoff(self, mock_post, mock_sleep):
        mock_post.side_effect = [_mock_status(500), _mock_status(500), _mock_ok()]
        n = _make_notifier(self.tmpdir, chat_ids=["a"], max_retries=2)
        result = n.send("INFO", "t", "b", "m")
        self.assertTrue(result.success)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("alerts.telegram_notifier.requests.post")
    def test_http_401_no_retry_permanent_fail(self, mock_post):
        mock_post.return_value = _mock_status(401)
        n = _make_notifier(self.tmpdir, chat_ids=["a"])
        result = n.send("INFO", "t", "b", "m")
        self.assertFalse(result.success)
        self.assertEqual(mock_post.call_count, 1)  # no retries

    @patch("alerts.telegram_notifier.requests.post")
    def test_http_400_no_retry(self, mock_post):
        mock_post.return_value = _mock_status(400)
        n = _make_notifier(self.tmpdir, chat_ids=["a"])
        result = n.send("INFO", "t", "b", "m")
        self.assertFalse(result.success)
        self.assertEqual(mock_post.call_count, 1)

    @patch("alerts.telegram_notifier.time.sleep")
    @patch("alerts.telegram_notifier.requests.post")
    def test_timeout_exception_retried(self, mock_post, mock_sleep):
        import requests as req_mod
        mock_post.side_effect = [req_mod.Timeout(), _mock_ok()]
        n = _make_notifier(self.tmpdir, chat_ids=["a"], max_retries=1)
        result = n.send("INFO", "t", "b", "m")
        self.assertTrue(result.success)


# ==============================================================================
# TestMessageFormatting
# ==============================================================================

class TestMessageFormatting(unittest.TestCase):
    """TG7 -- message format and truncation."""

    def test_subject_format(self):
        msg = _format_message("CRITICAL", "Capital breach", "Details", "fund_manager", {})
        self.assertIn("[CRITICAL] Capital breach", msg)

    def test_html_escaping(self):
        msg = _format_message("ERROR", "t", "<script>alert(1)</script>", "mod", {})
        self.assertNotIn("<script>", msg)
        self.assertIn("&lt;script&gt;", msg)

    def test_message_over_4096_chars_truncated(self):
        long_body = "X" * 5000
        msg = _format_message("INFO", "title", long_body, "mod", {})
        self.assertLessEqual(len(msg), 4096)
        self.assertIn("[truncated]", msg)

    def test_message_within_limit_not_truncated(self):
        msg = _format_message("INFO", "t", "short", "mod", {})
        self.assertLessEqual(len(msg), 4096)
        self.assertNotIn("[truncated]", msg)

    def test_context_included_in_message(self):
        msg = _format_message("WARN", "t", "b", "mod", {"key": "value"})
        self.assertIn("key", msg)
        self.assertIn("value", msg)


# ==============================================================================
# TestPaperMode
# ==============================================================================

class TestPaperMode(unittest.TestCase):
    """TG9 -- paper_mode: no HTTP, CRITICAL still writes sentinel."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_paper_mode_no_http_call(self, mock_post):
        n = _make_notifier(self.tmpdir, paper_mode=True)
        n.send("INFO", "t", "b", "m")
        mock_post.assert_not_called()

    @patch("alerts.telegram_notifier.requests.post")
    def test_paper_mode_critical_still_writes_sentinel(self, mock_post):
        n = _make_notifier(self.tmpdir, paper_mode=True)
        result = n.send("CRITICAL", "t", "b", "m")
        mock_post.assert_not_called()
        self.assertIsNotNone(result.sentinel_path)
        self.assertTrue(result.sentinel_path.exists())

    @patch("alerts.telegram_notifier.requests.post")
    def test_paper_mode_returns_success_true(self, mock_post):
        n = _make_notifier(self.tmpdir, paper_mode=True)
        result = n.send("ERROR", "t", "b", "m")
        self.assertTrue(result.success)
        mock_post.assert_not_called()


# ==============================================================================
# TestMasterSwitch (TASK-10)
# ==============================================================================

class TestMasterSwitch(unittest.TestCase):
    """TASK-10 -- telegram.enabled master ON/OFF switch."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_disabled_no_http_call(self, mock_post):
        n = _make_notifier(self.tmpdir, enabled=False)
        for sev in ("INFO", "WARN", "ERROR", "CRITICAL"):
            n.send(sev, "t", "b", "m")
        mock_post.assert_not_called()

    @patch("alerts.telegram_notifier.requests.post")
    def test_disabled_critical_writes_no_sentinel(self, mock_post):
        # When the master switch is OFF the notifier is a full silent no-op:
        # not even the CRITICAL sentinel is written (single gate at top of send).
        n = _make_notifier(self.tmpdir, enabled=False)
        result = n.send("CRITICAL", "t", "b", "m")
        mock_post.assert_not_called()
        self.assertIsNone(result.sentinel_path)
        self.assertEqual(list_pending_sentinels(self.tmpdir / "sentinels"), [])

    @patch("alerts.telegram_notifier.requests.post")
    def test_disabled_no_failed_alerts_log(self, mock_post):
        n = _make_notifier(self.tmpdir, enabled=False)
        result = n.send("ERROR", "t", "b", "m")
        self.assertFalse(result.failed_log_written)
        self.assertFalse((self.tmpdir / "failed_alerts.log").exists())

    @patch("alerts.telegram_notifier.requests.post")
    def test_disabled_returns_success_true(self, mock_post):
        n = _make_notifier(self.tmpdir, enabled=False)
        result = n.send("WARN", "t", "b", "m")
        self.assertTrue(result.success)
        self.assertEqual(result.tier, "WARN")
        self.assertEqual(result.delivered_to, [])

    @patch("alerts.telegram_notifier.requests.post")
    def test_enabled_explicit_sends_normally(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir, enabled=True)
        result = n.send("INFO", "t", "b", "m")
        self.assertTrue(result.success)
        self.assertTrue(mock_post.called)

    @patch("alerts.telegram_notifier.requests.post")
    def test_default_enabled_sends_normally(self, mock_post):
        # enabled omitted entirely -> defaults to True (no behavior change).
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir)
        result = n.send("INFO", "t", "b", "m")
        self.assertTrue(result.success)
        self.assertTrue(mock_post.called)


# ==============================================================================
# TestConcurrentSends
# ==============================================================================

class TestConcurrentSends(unittest.TestCase):
    """TG9 -- concurrent sends from multiple threads, no shared-state issues."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    @patch("alerts.telegram_notifier.requests.post")
    def test_concurrent_sends_no_race(self, mock_post):
        mock_post.return_value = _mock_ok()
        n = _make_notifier(self.tmpdir, chat_ids=["-100"], paper_mode=False)
        errors: list[Exception] = []
        lock = threading.Lock()

        def worker(sev: str):
            try:
                n.send(sev, "concurrent", "body", "mod")
            except Exception as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=worker, args=("INFO",)) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])


# ==============================================================================
# TestChannelWhitelist
# ==============================================================================

class TestChannelWhitelist(unittest.TestCase):
    """Whitelist enforcement: only enabled channels with resolved env vars get messages."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmpdir.name)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_channel_notifier(self, channels: list[ChannelConfig], **kwargs) -> TelegramNotifier:
        log = logging.getLogger("test_tg_whitelist")
        log.addHandler(logging.NullHandler())
        return TelegramNotifier(
            bot_token="test-token-wl",
            channels=channels,
            failed_alerts_log_path=self.tmpdir / "failed_alerts.log",
            sentinel_dir=self.tmpdir / "sentinels",
            logger=log,
            paper_mode=kwargs.get("paper_mode", False),
        )

    @patch("alerts.telegram_notifier.requests.post")
    def test_only_enabled_channel_receives_message(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        channels = [
            ChannelConfig(chat_id_env="TG_CHAN_A", label="Channel A", enabled=True),
            ChannelConfig(chat_id_env="TG_CHAN_B", label="Channel B", enabled=False),
        ]
        n = self._make_channel_notifier(channels)
        with unittest.mock.patch.dict(os.environ, {"TG_CHAN_A": "-100111"}):
            result = n.send("INFO", "t", "b", "m")
        self.assertEqual(mock_post.call_count, 1)
        self.assertIn("-100111", result.delivered_to)

    @patch("alerts.telegram_notifier.requests.post")
    def test_disabled_channel_no_http_call(self, mock_post):
        channels = [
            ChannelConfig(chat_id_env="TG_CHAN_B", label="Channel B", enabled=False),
        ]
        n = self._make_channel_notifier(channels)
        with unittest.mock.patch.dict(os.environ, {"TG_CHAN_B": "-100222"}):
            result = n.send("INFO", "t", "b", "m")
        mock_post.assert_not_called()
        self.assertFalse(result.success)

    def test_missing_env_var_for_enabled_channel_warns_and_skips(self):
        channels = [
            ChannelConfig(chat_id_env="TG_MISSING_VAR", label="Missing Chan", enabled=True),
        ]
        log = MagicMock()
        n = TelegramNotifier(
            bot_token="tok",
            channels=channels,
            failed_alerts_log_path=self.tmpdir / "f.log",
            sentinel_dir=self.tmpdir / "s",
            logger=log,
        )
        env_without_var = {k: v for k, v in os.environ.items() if k != "TG_MISSING_VAR"}
        with unittest.mock.patch.dict(os.environ, env_without_var, clear=True):
            result = n.send("INFO", "t", "b", "m")
        log.warning.assert_called()
        self.assertFalse(result.success)

    @patch("alerts.telegram_notifier.requests.post")
    def test_all_channels_disabled_paper_mode_no_sends(self, mock_post):
        channels = [
            ChannelConfig(chat_id_env="TG_CHAN_A", label="A", enabled=False),
            ChannelConfig(chat_id_env="TG_CHAN_B", label="B", enabled=False),
        ]
        n = self._make_channel_notifier(channels, paper_mode=True)
        result = n.send("INFO", "t", "b", "m")
        mock_post.assert_not_called()
        self.assertTrue(result.success)  # paper_mode always reports success


# ==============================================================================
# BL-14: signature regression test
# ==============================================================================

class TestBl14SendSignatureLocked(unittest.TestCase):
    """Audit BL-14: lock TelegramNotifier.send() parameter names so callers
    passing tier=/source=/message= get caught at test time, not runtime."""

    def test_send_signature_parameter_names_locked(self):
        import inspect
        sig = inspect.signature(TelegramNotifier.send)
        params = list(sig.parameters.keys())
        # self + 5 declared args in exact order
        self.assertEqual(
            params,
            ["self", "severity", "title", "body", "source_module", "context"],
            f"TelegramNotifier.send signature drifted: {params}. "
            "If this is intentional, update ALL callers (main.py, eod_squareoff, "
            "shadow_tracker, order_reconciler) in the same commit.",
        )

    def test_send_context_has_default_none(self):
        import inspect
        sig = inspect.signature(TelegramNotifier.send)
        ctx = sig.parameters["context"]
        self.assertIs(ctx.default, None)


# ==============================================================================
# FIX-158c: Factory methods + convenience methods
# ==============================================================================

class TestFactoryMethods(unittest.TestCase):
    """FIX-158c: from_env(), from_config(), send_alert/send_critical/send_info."""

    def test_from_env_returns_notifier_when_vars_set(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHANNEL_PRIMARY": "-100999"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            n = TelegramNotifier.from_env()
        self.assertIsNotNone(n)
        self.assertEqual(n._chat_ids, ["-100999"])

    def test_from_env_returns_none_when_token_missing(self):
        env = {"TELEGRAM_CHANNEL_PRIMARY": "-100999"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            n = TelegramNotifier.from_env()
        self.assertIsNone(n)

    def test_from_env_returns_none_when_chat_id_missing(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok123"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            n = TelegramNotifier.from_env()
        self.assertIsNone(n)

    def test_from_config_delegates_to_from_env(self):
        env = {"TELEGRAM_BOT_TOKEN": "tok123", "TELEGRAM_CHANNEL_PRIMARY": "-100999"}
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            n = TelegramNotifier.from_config("/some/config/dir")
        self.assertIsNotNone(n)

    @patch("alerts.telegram_notifier.requests.post")
    def test_send_alert_convenience(self, mock_post):
        mock_post.return_value = _mock_ok()
        with tempfile.TemporaryDirectory() as tmpdir:
            n = _make_notifier(Path(tmpdir))
            result = n.send_alert("disk full", level="WARNING")
        self.assertTrue(result.success)
        self.assertEqual(result.tier, "WARNING")

    @patch("alerts.telegram_notifier.requests.post")
    def test_send_critical_convenience(self, mock_post):
        mock_post.return_value = _mock_ok()
        with tempfile.TemporaryDirectory() as tmpdir:
            n = _make_notifier(Path(tmpdir))
            result = n.send_critical("capital breach")
        self.assertTrue(result.success)
        self.assertEqual(result.tier, "CRITICAL")

    @patch("alerts.telegram_notifier.requests.post")
    def test_send_info_convenience(self, mock_post):
        mock_post.return_value = _mock_ok()
        with tempfile.TemporaryDirectory() as tmpdir:
            n = _make_notifier(Path(tmpdir))
            result = n.send_info("EOD verify ok")
        self.assertTrue(result.success)
        self.assertEqual(result.tier, "INFO")


# ==============================================================================
# Standalone runner
# ==============================================================================

def run_all_tests() -> int:
    tests = [
        # Constructor
        TestConstructorValidation("test_empty_token_raises_value_error"),
        TestConstructorValidation("test_whitespace_token_raises_value_error"),
        TestConstructorValidation("test_empty_chat_ids_raises_value_error"),
        TestConstructorValidation("test_valid_construction_succeeds"),
        # INFO/WARN
        TestInfoWarnTier("test_info_send_success_calls_post_per_chat"),
        TestInfoWarnTier("test_info_send_failure_drops_silently"),
        TestInfoWarnTier("test_warn_send_failure_drops_silently"),
        # ERROR
        TestErrorTier("test_error_send_success_no_failed_log"),
        TestErrorTier("test_error_send_failure_writes_failed_log"),
        TestErrorTier("test_failed_log_has_all_8_fields"),
        TestErrorTier("test_failed_log_appends_multiple_failures"),
        # CRITICAL
        TestCriticalTier("test_critical_success_sentinel_written_first"),
        TestCriticalTier("test_critical_telegram_failure_sentinel_still_written"),
        TestCriticalTier("test_critical_sentinel_write_failure_logs_error"),
        TestCriticalTier("test_critical_send_result_has_sentinel_path"),
        # Multi-chat
        TestMultiChat("test_all_chats_receive_message"),
        TestMultiChat("test_partial_failure_tracked_per_chat"),
        # Retry
        TestRetryLogic("test_http_429_sleeps_and_retries_once"),
        TestRetryLogic("test_http_500_retries_with_backoff"),
        TestRetryLogic("test_http_401_no_retry_permanent_fail"),
        TestRetryLogic("test_http_400_no_retry"),
        TestRetryLogic("test_timeout_exception_retried"),
        # Formatting
        TestMessageFormatting("test_subject_format"),
        TestMessageFormatting("test_html_escaping"),
        TestMessageFormatting("test_message_over_4096_chars_truncated"),
        TestMessageFormatting("test_message_within_limit_not_truncated"),
        TestMessageFormatting("test_context_included_in_message"),
        # Paper mode
        TestPaperMode("test_paper_mode_no_http_call"),
        TestPaperMode("test_paper_mode_critical_still_writes_sentinel"),
        TestPaperMode("test_paper_mode_returns_success_true"),
        # Concurrent
        TestConcurrentSends("test_concurrent_sends_no_race"),
        # Channel whitelist
        TestChannelWhitelist("test_only_enabled_channel_receives_message"),
        TestChannelWhitelist("test_disabled_channel_no_http_call"),
        TestChannelWhitelist("test_missing_env_var_for_enabled_channel_warns_and_skips"),
        TestChannelWhitelist("test_all_channels_disabled_paper_mode_no_sends"),
        # BL-14 signature lock
        TestBl14SendSignatureLocked("test_send_signature_parameter_names_locked"),
        TestBl14SendSignatureLocked("test_send_context_has_default_none"),
        # FIX-158c factory + convenience methods
        TestFactoryMethods("test_from_env_returns_notifier_when_vars_set"),
        TestFactoryMethods("test_from_env_returns_none_when_token_missing"),
        TestFactoryMethods("test_from_env_returns_none_when_chat_id_missing"),
        TestFactoryMethods("test_from_config_delegates_to_from_env"),
        TestFactoryMethods("test_send_alert_convenience"),
        TestFactoryMethods("test_send_critical_convenience"),
        TestFactoryMethods("test_send_info_convenience"),
    ]

    suite = unittest.TestSuite(tests)
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    result = runner.run(suite)

    passed = len(tests) - len(result.failures) - len(result.errors)
    total = len(tests)

    print("=" * 70)
    print("alerts/telegram_notifier.py -- Test Suite")
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
