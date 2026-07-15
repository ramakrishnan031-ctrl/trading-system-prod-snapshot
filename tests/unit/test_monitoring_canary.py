"""CANARY (15-Jul-2026): the daily monitoring self-test proves the four alert paths and
fails loudly via the surviving channel. The whole module is new, so every import here
fails on the pre-fix tree (fail-on-old); the probes are pure + dependency-injected."""
from __future__ import annotations

import smtplib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from scripts.monitoring_canary import (
    _format_report,
    check_dashboard,
    check_email_path,
    check_sentinel_ingestion,
    check_telegram_path,
)


def _smtp_cfg():
    return SimpleNamespace(host="smtp.test", port=587, use_tls=True, timeout_sec=5,
                           username="u", resolved_password=lambda: "p")


def test_email_path_login_ok(monkeypatch):
    server = MagicMock()
    monkeypatch.setattr("scripts.monitoring_canary.smtplib.SMTP", lambda *a, **k: server)
    ok, _ = check_email_path(_smtp_cfg())
    assert ok is True
    server.login.assert_called_once()


def test_email_path_auth_fail_names_credential(monkeypatch):
    server = MagicMock()
    server.login.side_effect = smtplib.SMTPAuthenticationError(535, b"BadCredentials")
    monkeypatch.setattr("scripts.monitoring_canary.smtplib.SMTP", lambda *a, **k: server)
    ok, detail = check_email_path(_smtp_cfg())
    assert ok is False and "ALERT_SMTP_PASSWORD" in detail


def test_telegram_path():
    good = MagicMock(status_code=200); good.json.return_value = {"ok": True}
    assert check_telegram_path("tok", getter=lambda url: good)[0] is True
    bad = MagicMock(status_code=401); bad.json.return_value = {"ok": False}
    assert check_telegram_path("tok", getter=lambda url: bad)[0] is False
    assert check_telegram_path("", getter=lambda url: good)[0] is False   # unconfigured


def test_sentinel_ingestion(tmp_path):
    assert check_sentinel_ingestion(tmp_path)[0] is True                  # clean
    (tmp_path / "alert_watcher_degraded.json").write_text("{}")
    assert check_sentinel_ingestion(tmp_path)[0] is False                 # F1 degraded → fail


def test_dashboard():
    active = MagicMock(stdout="active\n")
    assert check_dashboard(runner=lambda: active)[0] is True
    dead = MagicMock(stdout="inactive\n")
    assert check_dashboard(runner=lambda: dead)[0] is False


def test_format_report_lists_all_four_paths():
    results = {"email": {"ok": False, "detail": "535"},
               "telegram": {"ok": True, "detail": "ok"},
               "sentinel": {"ok": True, "detail": "0 pending"},
               "dashboard": {"ok": True, "detail": "active"},
               "overall_ok": False}
    rep = _format_report(results)
    for p in ("email", "telegram", "sentinel", "dashboard"):
        assert p in rep
    assert "🔴" in rep and "✅" in rep


def test_canary_is_registered_and_monitored():
    from core.cron_registry import CronRegistry
    reg = CronRegistry.load(Path("config") / "cron_registry.yaml")
    job = reg.get("monitoring_canary")
    assert job is not None
    assert job.enabled and job.monitored
