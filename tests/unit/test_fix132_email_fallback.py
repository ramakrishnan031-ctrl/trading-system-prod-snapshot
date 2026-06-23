"""
tests/unit/test_fix132_email_fallback.py

FIX-132 Item 10: Email fallback for CRITICAL alerts when Telegram fails.
  - Telegram fails 3x for CRITICAL -> email sent
  - Telegram fails 3x for WARNING -> no email (WARNING not critical)
  - Email SMTP error -> logged, not raised
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from alerts.telegram_notifier import TelegramNotifier, ChannelConfig


def _log():
    return logging.getLogger("test_email_fallback")


@dataclass
class _EmailFallbackConfig:
    enabled: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    from_addr_env: str = "ALERT_EMAIL_USER"
    password_env: str = "ALERT_EMAIL_PASSWORD"
    to_addr_env: str = "ALERT_EMAIL_TO"
    use_tls: bool = True


# Throwaway temp dir so test runs never pollute the real data_store/ (sentinels)
# or logs/ (failed-alerts log). Cleaned up at interpreter exit.
_TEST_TMP = tempfile.mkdtemp(prefix="test_fix132_")
atexit.register(lambda: shutil.rmtree(_TEST_TMP, ignore_errors=True))


def _make_notifier(email_cfg=None, max_retries=1):
    """Create a notifier with mocked HTTP that always fails."""
    return TelegramNotifier(
        bot_token="test_token",
        chat_ids=["test_chat"],
        failed_alerts_log_path=str(Path(_TEST_TMP) / "test_failed.log"),
        sentinel_dir=_TEST_TMP,
        logger=_log(),
        max_retries=max_retries,
        retry_backoff_seconds=0.01,
        rate_limit_per_minute=999,
        paper_mode=False,
        email_fallback_config=email_cfg,
    )


class TestEmailFallbackCritical:

    def test_critical_telegram_fail_sends_email(self) -> None:
        """Telegram fails all retries for CRITICAL -> email fallback triggered."""
        cfg = _EmailFallbackConfig(enabled=True)
        notifier = _make_notifier(email_cfg=cfg, max_retries=1)

        env_vars = {
            "ALERT_EMAIL_USER": "test@gmail.com",
            "ALERT_EMAIL_PASSWORD": "app_password",
            "ALERT_EMAIL_TO": "ops@example.com",
        }

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls, \
             patch.dict(os.environ, env_vars, clear=False):
            # Telegram always fails (500 error)
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp

            # SMTP succeeds
            mock_smtp = MagicMock()
            mock_smtp_cls.return_value = mock_smtp

            result = notifier.send("CRITICAL", "Capital breach", "Danger", "fund_manager")

            # Telegram failed
            assert not result.success
            # Email was attempted
            mock_smtp_cls.assert_called_once_with("smtp.gmail.com", 587, timeout=15)
            mock_smtp.starttls.assert_called_once()
            mock_smtp.login.assert_called_once_with("test@gmail.com", "app_password")
            mock_smtp.sendmail.assert_called_once()
            mock_smtp.quit.assert_called_once()

            sent_args = mock_smtp.sendmail.call_args
            assert sent_args[0][0] == "test@gmail.com"
            assert sent_args[0][1] == ["ops@example.com"]
        print("  OK: CRITICAL Telegram fail -> email fallback sent")

    def test_warning_telegram_fail_no_email(self) -> None:
        """WARNING alert Telegram failure does NOT trigger email fallback."""
        cfg = _EmailFallbackConfig(enabled=True)
        notifier = _make_notifier(email_cfg=cfg, max_retries=1)

        env_vars = {
            "ALERT_EMAIL_USER": "test@gmail.com",
            "ALERT_EMAIL_PASSWORD": "app_password",
            "ALERT_EMAIL_TO": "ops@example.com",
        }

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls, \
             patch.dict(os.environ, env_vars, clear=False):
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp

            notifier.send("WARNING", "Low memory", "Check swap", "system")

            # Email should NOT be called for WARNING
            mock_smtp_cls.assert_not_called()
        print("  OK: WARNING Telegram fail -> no email (correct)")

    def test_email_smtp_error_logged_not_raised(self) -> None:
        """SMTP connection error is logged but does not crash the system."""
        cfg = _EmailFallbackConfig(enabled=True)
        notifier = _make_notifier(email_cfg=cfg, max_retries=1)

        env_vars = {
            "ALERT_EMAIL_USER": "test@gmail.com",
            "ALERT_EMAIL_PASSWORD": "app_password",
            "ALERT_EMAIL_TO": "ops@example.com",
        }

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls, \
             patch.dict(os.environ, env_vars, clear=False):
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp

            # SMTP raises connection error
            mock_smtp_cls.side_effect = ConnectionRefusedError("SMTP down")

            # Should NOT raise
            result = notifier.send("CRITICAL", "System down", "Help", "main")

            # Telegram failed, email failed, but no crash
            assert not result.success
        print("  OK: SMTP error logged, not raised")

    def test_email_disabled_no_attempt(self) -> None:
        """When email_fallback.enabled=False, no email attempt is made."""
        cfg = _EmailFallbackConfig(enabled=False)
        notifier = _make_notifier(email_cfg=cfg, max_retries=1)

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp

            notifier.send("CRITICAL", "Test", "Body", "test")

            mock_smtp_cls.assert_not_called()
        print("  OK: email disabled -> no attempt")

    def test_no_email_config_no_crash(self) -> None:
        """When no email_fallback_config is provided, CRITICAL still works."""
        notifier = _make_notifier(email_cfg=None, max_retries=1)

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp

            result = notifier.send("CRITICAL", "Test", "Body", "test")

            assert not result.success
            mock_smtp_cls.assert_not_called()
        print("  OK: no email config -> no crash")

    def test_missing_env_vars_no_crash(self) -> None:
        """Missing ALERT_EMAIL_* env vars -> logged warning, no crash."""
        cfg = _EmailFallbackConfig(enabled=True)
        notifier = _make_notifier(email_cfg=cfg, max_retries=1)

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls, \
             patch.dict(os.environ, {}, clear=False):
            # Remove the env vars if they exist
            for k in ["ALERT_EMAIL_USER", "ALERT_EMAIL_PASSWORD", "ALERT_EMAIL_TO"]:
                os.environ.pop(k, None)

            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_post.return_value = mock_resp

            result = notifier.send("CRITICAL", "Test", "Body", "test")

            # No crash, email not attempted
            assert not result.success
            mock_smtp_cls.assert_not_called()
        print("  OK: missing env vars -> no crash")

    def test_telegram_success_no_email(self) -> None:
        """When Telegram succeeds for CRITICAL, email is NOT sent."""
        cfg = _EmailFallbackConfig(enabled=True)
        notifier = _make_notifier(email_cfg=cfg, max_retries=1)

        with patch("requests.post") as mock_post, \
             patch("smtplib.SMTP") as mock_smtp_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            result = notifier.send("CRITICAL", "Test", "Body", "test")

            assert result.success
            mock_smtp_cls.assert_not_called()
        print("  OK: Telegram success -> no email fallback")


if __name__ == "__main__":
    tests = [
        TestEmailFallbackCritical().test_critical_telegram_fail_sends_email,
        TestEmailFallbackCritical().test_warning_telegram_fail_no_email,
        TestEmailFallbackCritical().test_email_smtp_error_logged_not_raised,
        TestEmailFallbackCritical().test_email_disabled_no_attempt,
        TestEmailFallbackCritical().test_no_email_config_no_crash,
        TestEmailFallbackCritical().test_missing_env_vars_no_crash,
        TestEmailFallbackCritical().test_telegram_success_no_email,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
