"""
tests/unit/test_cron_officer_phase3.py — SCOPE 2 (cron_officer enhancements).

Covers the hardening checklist: auto-discovery (synthetic, no YAML write-back);
status-count integrity (sum == total) + RAN_UNVERIFIED non-escalation + render;
ROSTER INTEGRITY clean(OK)/drift(DEGRADED) + sha256 stamp; alert-liveness self-test
healthy/dead; and FAIL-SAFE (a new-block failure must NOT crash the core report).
"""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import scripts.cron_officer as co
from core.cron_registry import CronRegistry
from core.state_store import StateStore
from scripts.cron_report_render import (
    COMPLETED, FAILED, NO_SIGNAL, RAN_UNVERIFIED, CronReport, JobOutcome,
    render_eod_html, render_eod_plaintext,
)
from scripts.generate_crontab import compose, load_jobs

_REG = Path("config/cron_registry.yaml")
MON = date(2026, 6, 15)


def _perfect_live() -> str:
    return "\n".join(compose(j) for j in load_jobs(_REG) if j.get("enabled", True)) + "\n"


def _line_for(name: str) -> str:
    return next(compose(j) for j in load_jobs(_REG) if j["name"] == name)


def _store_with_watchers(tmp_path) -> StateStore:
    store = StateStore(tmp_path / "t.db")
    for n in ("cron_officer_eod", "check_cron_drift"):
        store.insert_cron_heartbeat(job_name=n, executed_at="2026-06-15T10:00:00+05:30",
                                    status="SUCCESS", duration_sec=1.0, message=None)
    return store


# ── 1. auto-discovery (runtime only) ─────────────────────────────────────────
def test_auto_discover_synthetic_and_no_yaml_writeback():
    reg = CronRegistry.load(_REG)
    before = _REG.read_bytes()
    synthetic = ("0 0 * * * cd /home/ubuntu/systems/trading-system && set -a && . ./.env && set +a "
                 "&& PYTHONPATH=. /home/ubuntu/systems/venv/bin/python scripts/some_future_job.py "
                 ">> logs/x.log 2>&1")
    discovered = co._auto_discover(reg, _perfect_live() + synthetic + "\n")
    assert [j.name for j in discovered] == ["some_future_job"]
    assert all(j.status == RAN_UNVERIFIED for j in discovered)
    assert _REG.read_bytes() == before, "auto-discovery must NOT mutate the registry YAML"


def test_auto_discover_empty_when_all_registered():
    reg = CronRegistry.load(_REG)
    assert co._auto_discover(reg, _perfect_live()) == []   # claude now registered -> nothing to find


# ── 2. status-count integrity + RAN_UNVERIFIED non-escalation + render ───────
def test_status_counts_sum_to_total():
    jobs = [JobOutcome("a", "c", "10:00", time(10, 0), COMPLETED, "heartbeat_db"),
            JobOutcome("b", "c", "11:00", time(11, 0), FAILED, "heartbeat_db"),
            JobOutcome("c", "c", "12:00", time(12, 0), RAN_UNVERIFIED, "none"),
            JobOutcome("d", "c", "13:00", time(13, 0), NO_SIGNAL, "exit_code_file")]
    rep = CronReport(day=MON, weekday="Monday", mode="Live", is_eod=True, jobs=jobs)
    assert sum(rep.status_counts().values()) == rep.total == 4
    assert rep.ran_unverified == 1


def test_ran_unverified_does_not_escalate_severity():
    jobs = [JobOutcome("c", "ON_DEMAND", "12:00", time(12, 0), RAN_UNVERIFIED, "none")]
    assert co._compute_severity(jobs, watcher_stale=False) == "INFO"


def test_ran_unverified_renders_in_report():
    jobs = [JobOutcome("future_job", "ON_DEMAND", "00:00", time(0, 0), RAN_UNVERIFIED, "none",
                       0.0, "needs registry entry + contract")]
    rep = CronReport(day=MON, weekday="Monday", mode="Live", is_eod=True, jobs=jobs)
    pt, html = render_eod_plaintext(rep), render_eod_html(rep)
    assert "future_job" in pt and "Unverified" in pt
    assert "future_job" in html


# ── 3. ROSTER INTEGRITY clean / drift + stamp ────────────────────────────────
def test_roster_integrity_clean(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_alert_path_health", lambda *a, **k: (True, "telegram: reachable; sentinel-dir: writable"))
    store = _store_with_watchers(tmp_path)
    lines, sev = co._roster_integrity(CronRegistry.load(_REG), Path("config"), store, MON, _perfect_live())
    store.close()
    assert lines[0].startswith("ROSTER INTEGRITY: OK")
    assert "Registry:" in lines[0] and sev == "INFO"


def test_roster_integrity_drift_degraded(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_alert_path_health", lambda *a, **k: (True, "ok"))
    store = _store_with_watchers(tmp_path)
    live_drift = _perfect_live().replace(_line_for("reconcile_positions") + "\n", "")
    lines, sev = co._roster_integrity(CronRegistry.load(_REG), Path("config"), store, MON, live_drift)
    store.close()
    assert lines[0].startswith("ROSTER INTEGRITY: DEGRADED") and sev == "CRITICAL"


def test_roster_integrity_dead_alert_path_is_loud(tmp_path, monkeypatch):
    monkeypatch.setattr(co, "_alert_path_health", lambda *a, **k: (False, "telegram: unreachable"))
    store = _store_with_watchers(tmp_path)
    lines, sev = co._roster_integrity(CronRegistry.load(_REG), Path("config"), store, MON, _perfect_live())
    store.close()
    assert sev == "CRITICAL" and any("alert path: DEAD" in ln for ln in lines)


# ── 4. alert-liveness self-test (low-noise) ──────────────────────────────────
def test_alert_path_health_healthy_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    class _R:
        status_code = 200
        def json(self):  # noqa: D401
            return {"ok": True}

    monkeypatch.setattr("requests.get", lambda *a, **k: _R())
    ok, detail = co._alert_path_health(Path("config"), sentinel_dir=tmp_path)
    assert ok and "reachable" in detail and "writable" in detail
    assert not list(tmp_path.glob("*.tmp")), "self-test sentinel must be ephemeral (cleaned)"


def test_alert_path_health_dead_telegram(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")

    def _boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr("requests.get", _boom)
    ok, detail = co._alert_path_health(Path("config"), sentinel_dir=tmp_path)
    assert not ok and "unreachable" in detail


# ── 5. FAIL-SAFE: a new-block failure must NOT crash the core report ─────────
def test_build_report_survives_roster_failure(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("roster boom")

    monkeypatch.setattr(co, "_roster_integrity", _boom)
    store = _store_with_watchers(tmp_path)
    rep = co.build_report(CronRegistry.load(_REG), store, MON, Path("config"), time(18, 50),
                          is_eod=True, crontab_text=_perfect_live())
    store.close()
    assert isinstance(rep, CronReport)
    assert any("ROSTER INTEGRITY: UNKNOWN" in ln for ln in rep.extra_lines)
    render_eod_plaintext(rep); render_eod_html(rep)   # core report still renders


def test_build_report_survives_auto_discover_failure(tmp_path, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("discover boom")

    monkeypatch.setattr(co, "_auto_discover", _boom)
    monkeypatch.setattr(co, "_roster_integrity", lambda *a, **k: (["ROSTER INTEGRITY: OK   Registry: x"], "INFO"))
    store = _store_with_watchers(tmp_path)
    rep = co.build_report(CronRegistry.load(_REG), store, MON, Path("config"), time(18, 50),
                          is_eod=True, crontab_text=_perfect_live())
    store.close()
    assert isinstance(rep, CronReport)           # core intact despite auto-discover crash
    render_eod_plaintext(rep)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
