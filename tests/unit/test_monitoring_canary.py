"""CANARY (15-Jul-2026): the daily monitoring self-test proves the four alert paths and
fails loudly via the surviving channel. The whole module is new, so every import here
fails on the pre-fix tree (fail-on-old); the probes are pure + dependency-injected."""
from __future__ import annotations

import json
import smtplib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from scripts.monitoring_canary import (
    _classify_respawn,
    _format_report,
    check_dashboard,
    check_email_path,
    check_sentinel_ingestion,
    check_service_respawn,
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


# ── Respawn-loop detection (16-Jul-2026) ─────────────────────────────────────
# Regression guard: alert-watcher can NEVER read HEALTHY while it is silently respawning.
# fail-on-old = _classify_respawn/check_service_respawn don't exist pre-fix (ImportError);
# pass-on-new = the assertions below. The classifier is pure; the wrapper injects runner+state.

def test_classify_respawn_flags_fast_restart():
    # the 16-Jul incident shape: ~8640 restarts accumulated over 24h (~360/hr) → NOT healthy.
    ok, detail = _classify_respawn(8640, "running", prev_nrestarts=0, elapsed_sec=86400.0)
    assert ok is False and "RESPAWN" in detail.upper()


def test_classify_respawn_auto_restart_substate_flags():
    # a stable daemon is never mid-restart at sample time → auto-restart alone is a red flag.
    ok, _ = _classify_respawn(5, "auto-restart", prev_nrestarts=5, elapsed_sec=3600.0)
    assert ok is False


def test_classify_respawn_stable_daemon_ok():
    # long-lived --loop daemon: NRestarts unchanged over 24h → healthy.
    assert _classify_respawn(3, "running", prev_nrestarts=3, elapsed_sec=86400.0)[0] is True


def test_classify_respawn_single_restart_not_flagged():
    # one legit restart in a short 5-min inter-run gap: delta=1 < min_delta → no false alarm.
    assert _classify_respawn(4, "running", prev_nrestarts=3, elapsed_sec=300.0)[0] is True


def test_classify_respawn_baseline_and_reset_are_benign():
    assert _classify_respawn(101570, "running", prev_nrestarts=None, elapsed_sec=0.0)[0] is True  # 1st run
    assert _classify_respawn(0, "running", prev_nrestarts=9000, elapsed_sec=86400.0)[0] is True    # redeploy


def test_check_service_respawn_detects_loop(tmp_path):
    state = tmp_path / "svc.json"
    state.write_text(json.dumps({"nrestarts": 0, "iso": "2026-07-15T08:20:00"}))
    runner = lambda: "NRestarts=8640\nSubState=running\n"  # noqa: E731
    ok, detail = check_service_respawn("alert-watcher.service", runner=runner,
                                       state_path=state, now_iso="2026-07-16T08:20:00")
    assert ok is False and "RESPAWN" in detail.upper()
    assert json.loads(state.read_text())["nrestarts"] == 8640  # baseline advanced for next run


def test_check_service_respawn_stable_daemon_ok(tmp_path):
    state = tmp_path / "svc.json"
    state.write_text(json.dumps({"nrestarts": 2, "iso": "2026-07-15T08:20:00"}))
    runner = lambda: "NRestarts=2\nSubState=running\n"  # noqa: E731
    ok, _ = check_service_respawn(runner=runner, state_path=state, now_iso="2026-07-16T08:20:00")
    assert ok is True


def test_check_service_respawn_first_run_records_baseline(tmp_path):
    state = tmp_path / "svc.json"
    runner = lambda: "NRestarts=100\nSubState=running\n"  # noqa: E731
    ok, _ = check_service_respawn(runner=runner, state_path=state, now_iso="2026-07-16T08:20:00")
    assert ok is True                                  # no prior sample → benign baseline
    assert json.loads(state.read_text())["nrestarts"] == 100


def test_check_service_respawn_unavailable_degrades_not_alarms(tmp_path):
    def _boom():
        raise FileNotFoundError("systemctl not found")
    ok, detail = check_service_respawn(runner=_boom, state_path=tmp_path / "svc.json")
    assert ok is True and "unavailable" in detail       # no spurious canary WARNING


def test_classify_respawn_threshold_override_changes_verdict():
    # a +2 restart bump over 10 min: HEALTHY at the default min_delta (3), FLAGGED when the
    # config lowers min_delta to 1 — the override reaches the verdict.
    assert _classify_respawn(12, "running", prev_nrestarts=10, elapsed_sec=600.0)[0] is True
    ok, detail = _classify_respawn(12, "running", prev_nrestarts=10, elapsed_sec=600.0,
                                   min_delta=1, max_restarts_per_hour=6.0)
    assert ok is False and "RESPAWN" in detail.upper()


def test_check_service_respawn_config_override_changes_verdict(tmp_path):
    # run_canary passes alerts.respawn_restart_delta_threshold as min_delta; prove it changes
    # the verdict at the probe boundary. Same +2-in-10min sample, two thresholds.
    seed = {"nrestarts": 10, "iso": "2026-07-16T08:00:00"}
    runner = lambda: "NRestarts=12\nSubState=running\n"  # noqa: E731
    state = tmp_path / "svc.json"
    state.write_text(json.dumps(seed))                                  # default delta>=3
    assert check_service_respawn(runner=runner, state_path=state,
                                 now_iso="2026-07-16T08:10:00")[0] is True
    state.write_text(json.dumps(seed))                                  # overridden delta>=1
    ok, _ = check_service_respawn(runner=runner, state_path=state,
                                  now_iso="2026-07-16T08:10:00", min_delta=1)
    assert ok is False
