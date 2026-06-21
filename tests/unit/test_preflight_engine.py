"""
tests/unit/test_preflight_engine.py -- Phase B (engine readiness) checks +
phase_b_checks composition. HTTP is monkeypatched (no real app).
"""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from scripts.preflight.base import CheckContext, Criticality, Status
from scripts.preflight.checks import engine, phase_b_checks, services, vm_health


def _ctx(tmp_path, db=None):
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    return CheckContext(config_dir=cfg, db_path=db or (tmp_path / "trading_system.db"),
                        mode="live", as_of_date=date(2026, 6, 22), phase="B")


def _db_capital(tmp_path, cash_floor):
    tmp_path.mkdir(parents=True, exist_ok=True)
    db = tmp_path / "trading_system.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE capital_snapshot (id INTEGER PRIMARY KEY, cash_floor REAL, margin_used REAL)")
    conn.execute("INSERT INTO capital_snapshot VALUES (1, ?, 0)", (cash_floor,))  # None -> NULL row
    conn.commit()
    conn.close()
    return db


# ── app_health ────────────────────────────────────────────────────────────────────
def test_app_health(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (200, {"status": "healthy", "uptime_seconds": 12}))
    assert engine.AppHealthCheck().run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (503, {"status": "degraded", "checks": {"token": {"ok": False}, "db": {"ok": True}}}))
    res = engine.AppHealthCheck().run(_ctx(tmp_path))
    assert res.status is Status.FAIL and "token" in res.detail
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (0, {"error": "connection refused"}))
    assert engine.AppHealthCheck().run(_ctx(tmp_path)).status is Status.FAIL


def test_app_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (200, {"signals_received": 4, "open_positions": 1}))
    res = engine.AppMetricsCheck().run(_ctx(tmp_path))
    assert res.status is Status.PASS and res.metrics["signals_received"] == 4
    monkeypatch.setattr(engine, "_http_get_json", lambda u, timeout=5.0: (0, {}))
    assert engine.AppMetricsCheck().run(_ctx(tmp_path)).status is Status.FAIL


# ── fund_manager_balance ────────────────────────────────────────────────────────────
def test_fund_manager_balance(tmp_path):
    ok = _db_capital(tmp_path, 10247.0)
    assert engine.FundManagerBalanceCheck().run(_ctx(tmp_path, ok)).status is Status.PASS
    zero = _db_capital(tmp_path / "z", 0.0)
    assert engine.FundManagerBalanceCheck().run(_ctx(tmp_path / "z", zero)).status is Status.FAIL
    none = _db_capital(tmp_path / "n", None)
    assert engine.FundManagerBalanceCheck().run(_ctx(tmp_path / "n", none)).status is Status.FAIL


def test_fund_manager_no_snapshot(tmp_path):
    db = tmp_path / "trading_system.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE capital_snapshot (id INTEGER PRIMARY KEY, cash_floor REAL, margin_used REAL)")
    conn.commit()
    conn.close()
    assert engine.FundManagerBalanceCheck().run(_ctx(tmp_path, db)).status is Status.WARN


# ── ntp strict (Phase B escalation) ──────────────────────────────────────────────
def test_ntp_strict(tmp_path, monkeypatch):
    chk = engine.NtpStrictCheck()
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 0.1)
    assert chk.run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 1.0)
    assert chk.run(_ctx(tmp_path)).status is Status.WARN
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: 3.0)
    assert chk.run(_ctx(tmp_path)).status is Status.FAIL
    monkeypatch.setattr(vm_health, "_ntp_skew_seconds", lambda: None)
    assert chk.run(_ctx(tmp_path)).status is Status.SKIPPED


# ── phase B composition ─────────────────────────────────────────────────────────────
def test_phase_b_composition():
    cs = phase_b_checks()
    names = [c.name for c in cs]
    assert "svc_trading_system" in names          # app process (alert-only)
    assert {"app_health", "app_metrics", "fund_manager_balance", "vm_ntp_strict"} <= set(names)
    assert "kite_token_fresh_today" in names       # fast re-gate
    # trading-system service check must NOT auto-start the app (token-watcher owns it)
    svc = next(c for c in cs if c.name == "svc_trading_system")
    assert svc.auto_fixable is False
