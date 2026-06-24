"""
tests/unit/test_preflight_groups.py -- Phase-A groups 2 (services), 9 (recovery),
and the added group-6 config checks (yaml-valid, live-test caps).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts.preflight.base import CheckContext, Status
from scripts.preflight.checks import config_integrity, recovery, services


def _ctx(tmp_path: Path, **kw) -> CheckContext:
    (tmp_path / "data_store").mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    kw.setdefault("as_of_date", date(2026, 6, 22))
    return CheckContext(config_dir=cfg, db_path=tmp_path / "data_store" / "trading_system.db", **kw)


# ── services ─────────────────────────────────────────────────────────────────────
def test_service_name_derivation():
    assert services.ServiceActiveCheck("alert-watcher.service").name == "svc_alert_watcher"
    assert services.ServiceActiveCheck("cron").name == "svc_cron"


def test_service_active_inactive_and_periodic(tmp_path, monkeypatch):
    monkeypatch.setattr(services, "_active_state", lambda s: "active")
    assert services.ServiceActiveCheck("cron").run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(services, "_active_state", lambda s: "activating")  # periodic-oneshot
    assert services.ServiceActiveCheck("alert-watcher.service").run(_ctx(tmp_path)).status is Status.PASS
    monkeypatch.setattr(services, "_active_state", lambda s: "failed")
    assert services.ServiceActiveCheck("cron").run(_ctx(tmp_path)).status is Status.FAIL


def test_service_fix(tmp_path, monkeypatch):
    state = {"s": "inactive"}
    monkeypatch.setattr(services, "_active_state", lambda svc: state["s"])
    monkeypatch.setattr(services, "_start", lambda svc: state.update(s="active"))
    chk = services.ServiceActiveCheck("alert-watcher.service")
    ctx = _ctx(tmp_path)
    assert chk.run(ctx).status is Status.FAIL
    fr = chk.fix(ctx)
    assert fr.success and fr.after_state == "active"
    assert chk.run(ctx).status is Status.PASS


def test_support_services_list_excludes_app():
    names = [c.service for c in services.CHECKS]
    assert "trading-system.service" not in names      # Fork 1: app checked in Phase B
    assert "alert-watcher.service" in names and "fail2ban" in names


# ── recovery ─────────────────────────────────────────────────────────────────────
def test_today_log_writable_fail_then_fix(tmp_path):
    chk = recovery.TodayLogWritableCheck()
    ctx = _ctx(tmp_path)  # logs/ does not exist yet
    assert chk.run(ctx).status is Status.FAIL
    fr = chk.fix(ctx)
    assert fr.success
    assert chk.run(ctx).status is Status.PASS


def test_cron_marks_dir_fix(tmp_path):
    chk = recovery.CronMarksDirWritableCheck()
    ctx = _ctx(tmp_path)
    assert chk.run(ctx).status is Status.FAIL
    assert chk.fix(ctx).success
    assert chk.run(ctx).status is Status.PASS


def test_alert_backlog_warn_and_pass(tmp_path):
    ctx = _ctx(tmp_path)
    ds = tmp_path / "data_store"
    # few -> PASS
    for i in range(3):
        (ds / f"critical_alert_{i}.flag").write_text("x", encoding="utf-8")
    assert recovery.AlertBacklogCheck().run(ctx).status is Status.PASS
    # many -> WARN
    for i in range(60):
        (ds / f"critical_alert_bulk_{i}.flag").write_text("x", encoding="utf-8")
    assert recovery.AlertBacklogCheck().run(ctx).status is Status.WARN


def test_log_rotation_pass_and_skip(tmp_path):
    ctx = _ctx(tmp_path)
    chk = recovery.LogRotationCheck()
    assert chk.run(ctx).status is Status.SKIPPED  # no prior-day log
    logs = tmp_path / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "system_2026-06-21.log").write_text("x", encoding="utf-8")  # yesterday of 22nd
    assert chk.run(ctx).status is Status.PASS


# ── config (group 6 additions) ───────────────────────────────────────────────────
def test_config_yaml_valid_and_invalid(tmp_path):
    ctx = _ctx(tmp_path)
    (ctx.config_dir / "ok.yaml").write_text("k: 1\n", encoding="utf-8")
    assert config_integrity.ConfigYamlValidCheck().run(ctx).status is Status.PASS
    (ctx.config_dir / "bad.yaml").write_text("a:\n  - x\n - y\n", encoding="utf-8")  # bad indent
    assert config_integrity.ConfigYamlValidCheck().run(ctx).status is Status.FAIL


def _write_sys_config(ctx, *, mo=5, me=10):
    # BUILD 1 (#3): CapsConfigDriftCheck guards the BASE caps (live_test deleted).
    (ctx.config_dir / "system_config.yaml").write_text(
        f"risk:\n  max_open_positions: {mo}\n"
        f"  max_daily_trades: {me}\n",
        encoding="utf-8")


def test_caps_config_drift_pass(tmp_path):
    ctx = _ctx(tmp_path)
    _write_sys_config(ctx, mo=5, me=10)   # Rama's intended caps
    assert config_integrity.CapsConfigDriftCheck().run(ctx).status is Status.PASS


def test_caps_config_drift_fails(tmp_path):
    ctx = _ctx(tmp_path)
    _write_sys_config(ctx, mo=3, me=6)
    assert config_integrity.CapsConfigDriftCheck().run(ctx).status is Status.FAIL
