"""Tests for the Cron Officer per-job alert policy + HeartbeatTimer alerting.

TASK #3 Layer 2/3B/5/6.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from alerts import cron_alerts
from utils.cron_heartbeat import HeartbeatTimer, skip_if_non_trading_day

_NO_DB = Path("does_not_exist_zzz.db")  # record_heartbeat no-ops when db missing


def _patch_notifier():
    """Patch TelegramNotifier.from_env -> MagicMock notifier; return the mock."""
    notifier = MagicMock()
    p = patch("alerts.telegram_notifier.TelegramNotifier.from_env", return_value=notifier)
    return p, notifier


# ── severity mapping ─────────────────────────────────────────────────────────


class TestAlertPolicy:
    def test_failed_critical_is_CRITICAL(self):
        p, n = _patch_notifier()
        with p:
            cron_alerts.alert_job_result("auto_refresh_token", "FAILED", critical=True, message="boom")
        assert n.send.call_args.kwargs["severity"] == "CRITICAL"

    def test_failed_noncritical_is_ERROR(self):
        p, n = _patch_notifier()
        with p:
            cron_alerts.alert_job_result("fetch_fno_ban", "FAILED", critical=False)
        assert n.send.call_args.kwargs["severity"] == "ERROR"

    def test_success_critical_is_INFO(self):
        p, n = _patch_notifier()
        with p:
            cron_alerts.alert_job_result("reconcile_positions", "SUCCESS", critical=True, duration_sec=2.0)
        assert n.send.call_args.kwargs["severity"] == "INFO"

    def test_success_noncritical_is_silent(self):
        p, n = _patch_notifier()
        with p:
            cron_alerts.alert_job_result("daily_review", "SUCCESS", critical=False)
        n.send.assert_not_called()

    def test_skipped_is_silent(self):
        p, n = _patch_notifier()
        with p:
            cron_alerts.alert_job_result("eod_verify", "SKIPPED", critical=True)
        n.send.assert_not_called()

    def test_unconfigured_or_disabled_no_crash(self):
        # from_env returns None when token/chat missing OR master switch off
        with patch("alerts.telegram_notifier.TelegramNotifier.from_env", return_value=None):
            cron_alerts.alert_job_result("auto_refresh_token", "FAILED", critical=True)  # no raise

    def test_resolves_criticality_from_real_registry(self):
        # critical=None -> looks up config/cron_registry.yaml; auto_refresh_token is critical
        p, n = _patch_notifier()
        with p:
            cron_alerts.alert_job_result("auto_refresh_token", "FAILED", critical=None)
        assert n.send.call_args.kwargs["severity"] == "CRITICAL"


# ── HeartbeatTimer alerting ──────────────────────────────────────────────────


class TestHeartbeatTimerAlert:
    def test_success_critical_alerts(self):
        with patch("alerts.cron_alerts.alert_job_result") as m:
            with HeartbeatTimer("x", db_path=_NO_DB, alert=True, critical=True):
                pass
        assert m.call_args.args[1] == "SUCCESS"

    def test_failure_sets_failed_and_propagates(self):
        with patch("alerts.cron_alerts.alert_job_result") as m:
            with pytest.raises(ValueError):
                with HeartbeatTimer("x", db_path=_NO_DB, alert=True, critical=True):
                    raise ValueError("kaboom")
        assert m.call_args.args[1] == "FAILED"

    def test_no_alert_when_alert_false(self):
        with patch("alerts.cron_alerts.alert_job_result") as m:
            with HeartbeatTimer("x", db_path=_NO_DB, alert=False):
                pass
        m.assert_not_called()


# ── holiday skip ─────────────────────────────────────────────────────────────


class TestSkipHoliday:
    def test_skips_on_non_trading_day(self):
        with patch("utils.holiday_guard.is_trading_day", return_value=False):
            assert skip_if_non_trading_day("eod_verify", db_path=_NO_DB) is True

    def test_runs_on_trading_day(self):
        with patch("utils.holiday_guard.is_trading_day", return_value=True):
            assert skip_if_non_trading_day("eod_verify", db_path=_NO_DB) is False
