"""
tests/unit/test_preflight_signals.py -- Phase C (signal warmup) checks + the
passive watch loop. HTTP monkeypatched (no real webhook/app).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.preflight import orchestrator
from scripts.preflight.base import CheckContext, Status
from scripts.preflight.checks import engine, phase_c_checks, signals


def _ctx(tmp_path):
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    return CheckContext(config_dir=cfg, db_path=tmp_path / "trading_system.db",
                        mode="live", as_of_date=date(2026, 6, 22), phase="C")


def test_webhook_responsive(tmp_path, monkeypatch):
    # 200 (no webhook secret configured) -> the endpoint answered -> reachable.
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (200, {}))
    assert signals.WebhookResponsiveCheck().run(_ctx(tmp_path)).status is Status.PASS
    # 401: AB-910 §1.7 put /health behind the webhook secret and this check calls it
    # UNAUTHENTICATED by design, so a 401 is the endpoint ANSWERING -> Flask is up and
    # Chartink signals can arrive -> PASS. Pre-fix this returned FAIL and fired a false
    # CRITICAL every trading day (the S4 /health-401 family, 3rd site; mirrors the boot
    # self-check utils/startup_checks.py:807, which already tolerates 401).
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (401, {}))
    assert signals.WebhookResponsiveCheck().run(_ctx(tmp_path)).status is Status.PASS
    # ANTI-VACUITY: the 401 tolerance must NOT swallow a genuine outage. A real 'down'
    # (connection refused -> status 0) and a real 5xx must STILL fail CRITICAL -- else
    # the check could never go red and would be worthless (the trap check_scanner:703
    # would fall into if this tolerance were copied to the external Chartink path).
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (0, {"error": "refused"}))
    assert signals.WebhookResponsiveCheck().run(_ctx(tmp_path)).status is Status.FAIL
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (503, {}))
    assert signals.WebhookResponsiveCheck().run(_ctx(tmp_path)).status is Status.FAIL


def test_signals_arrived(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_http_get_json",
                        lambda u, timeout=5.0: (200, {"signals_received": 3, "last_signal_at": "09:18:01"}))
    assert signals.SignalsArrivedCheck().run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (200, {"signals_received": 0}))
    res = signals.SignalsArrivedCheck().run(_ctx(tmp_path))
    assert res.status is Status.WARN and "quiet" in res.detail
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (503, {}))
    assert signals.SignalsArrivedCheck().run(_ctx(tmp_path)).status is Status.WARN


def test_webhook_backpressure(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_http_get_json",
                        lambda u, timeout=5.0: (200, {"queue_depth": 2, "queue_capacity": 100}))
    assert signals.WebhookBackpressureCheck().run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(engine, "_http_get_json",
                        lambda u, timeout=5.0: (200, {"queue_depth": 95, "queue_capacity": 100}))
    assert signals.WebhookBackpressureCheck().run(_ctx(tmp_path)).status is Status.WARN
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (0, {}))
    assert signals.WebhookBackpressureCheck().run(_ctx(tmp_path)).status is Status.SKIPPED


def test_phase_c_composition():
    names = [c.name for c in phase_c_checks()]
    assert names == ["webhook_responsive", "signals_arrived", "webhook_backpressure"]


def test_watch_phase_c_single_sample(tmp_path, monkeypatch):
    # watch_sec=0 -> exactly one sample, no sleep
    monkeypatch.setattr(engine, "_http_get_json",
                        lambda u, timeout=5.0: (200, {"signals_received": 2, "queue_depth": 0, "queue_capacity": 100}))
    rep = orchestrator.watch_phase_c(_ctx(tmp_path), phase_c_checks(), "rid",
                                     watch_sec=0, interval_sec=30)
    assert rep.total == 3
    assert rep.overall_status in ("READY", "READY_WITH_WARNINGS")
    assert rep.failed_critical == 0
