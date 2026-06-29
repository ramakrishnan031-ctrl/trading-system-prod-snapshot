"""Control Tower Phase 1d (RUNNER + CRON WIRING + daily_report HEARTBEAT) tests.

Covers the one daily runner (size-logger FIRST, then aggregate/lifecycle/health/
status/reporter), the cron self-reference guard (the runner never flags its own
in-flight run as missed), the non-trading-day skip (no false freshness alerts on
weekday holidays), the cron_registry runner entry, and the daily_report
observability heartbeat (the 23-Jun "daily_report missed" source fix).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import pytest

from core import db_connect
from core.state_store import StateStore
from ops.control_tower import aggregator, runner
from ops.control_tower.db import get_findings

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_DIR = Path("config")
# 2026-06-29 is a trading Monday (same fixture date the 1b/1c suites use). The
# control_tower runner is due 17:05, so a 17:05 `now` makes it "expected".
_NOW = datetime(2026, 6, 29, 17, 5, tzinfo=_IST)


def _db(tmp_path) -> Path:
    db = tmp_path / "t.db"
    StateStore(db).close()                      # v40 schema (control_tower_* + analytics)
    return db


def _make_proj(tmp_path) -> Path:
    root = tmp_path / "proj"
    (root / "data_store" / "backups").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "data_store" / "backups" / "a.backup").write_bytes(b"x" * 2048)
    (root / "logs" / "system.log").write_text("hello")
    return root


# ── runner orchestration: size-logger FIRST, then aggregator ──────────────────

def test_runner_run_writes_size_then_aggregation(tmp_path):
    db = _db(tmp_path)
    root = _make_proj(tmp_path)
    res = runner.run(db, root, _CONFIG_DIR, report_dir=tmp_path / "rep",
                     telegram=False, now=_NOW)
    assert res["size"]["date"] == "2026-06-29"
    assert res["aggregation"]["overall_status"] in (
        "HEALTHY", "WARNING", "ATTENTION", "CRITICAL")

    conn = db_connect.connect(db, attach=False)
    # the SAME date row carries the 1a size cols AND the aggregator's health_score
    # (two field-scoped UPSERTs, no clobber) -> proves both steps ran on one row.
    trend = conn.execute(
        "SELECT backup_size_mb, disk_used_pct, health_score "
        "FROM control_tower_trends WHERE date='2026-06-29'").fetchone()
    status = conn.execute(
        "SELECT overall_status, last_successful_run "
        "FROM control_tower_status WHERE run_date='2026-06-29'").fetchone()
    conn.close()
    assert trend and trend[0] is not None and trend[1] is not None  # size-logger wrote
    assert trend[2] is not None                                     # aggregator wrote health
    assert status and status[0] == res["aggregation"]["overall_status"]
    assert status[1] is not None                                    # last_successful_run set
    assert (tmp_path / "rep" / "report_2026-06-29.html").exists()
    assert (tmp_path / "rep" / "report_2026-06-29.csv").exists()


# ── self-reference guard: the runner never flags its own in-flight run ────────

def test_read_cron_self_exclusion(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=False)
    # at 17:05 the control_tower job is due + has no heartbeat yet
    without = {f.resource_name for f in aggregator.read_cron(
        conn, _CONFIG_DIR, tmp_path / "cron_marks", _NOW).findings}
    withx = {f.resource_name for f in aggregator.read_cron(
        conn, _CONFIG_DIR, tmp_path / "cron_marks", _NOW,
        frozenset({"control_tower"})).findings}
    conn.close()
    assert "control_tower" in without          # due + unseen -> would self-flag
    assert "control_tower" not in withx         # the guard suppresses it


def test_runner_does_not_flag_itself(tmp_path):
    db = _db(tmp_path)
    root = _make_proj(tmp_path)
    runner.run(db, root, _CONFIG_DIR, report_dir=tmp_path / "rep",
               telegram=False, now=_NOW)
    conn = db_connect.connect(db, attach=False)
    names = {r[3] for r in get_findings(conn, ("OPEN",))}   # resource_name col
    conn.close()
    assert "control_tower" not in names                     # self_job threaded through


# ── cron_registry: the runner entry replaced the standalone size-logger ───────

def test_cron_registry_control_tower_runner_entry():
    from core.cron_registry import load_cron_registry
    reg = load_cron_registry(_CONFIG_DIR / "cron_registry.yaml")
    j = reg.get("control_tower")
    assert j.command == "ops/control_tower/runner.py"
    assert j.monitored is True and j.marker_name == "control_tower"
    assert j.due_time == time(17, 5) and j.cadence == "market_day"
    with pytest.raises(KeyError):                           # superseded, removed
        reg.get("control_tower_size_logger")


# ── non-trading-day skip (no false alerts on weekday holidays) ────────────────

def test_runner_skips_non_trading_day(tmp_path, monkeypatch):
    db = _db(tmp_path)
    root = _make_proj(tmp_path)
    import utils.holiday_guard as hg
    monkeypatch.setattr(hg, "is_trading_day", lambda d, config_dir: False)
    rc = runner.main(["--db", str(db), "--root", str(root),
                      "--report-dir", str(tmp_path / "rep"), "--no-telegram"])
    assert rc == 0
    conn = db_connect.connect(db, attach=False)
    hb = conn.execute("SELECT status FROM cron_heartbeat "
                      "WHERE job_name='control_tower'").fetchone()
    n_status = conn.execute("SELECT COUNT(*) FROM control_tower_status").fetchone()[0]
    conn.close()
    assert hb and hb[0] == "SKIPPED"            # SKIPPED heartbeat recorded
    assert n_status == 0                         # aggregation did NOT run


def test_runner_force_bypasses_skip_and_heartbeats(tmp_path, monkeypatch):
    db = _db(tmp_path)
    root = _make_proj(tmp_path)
    import utils.holiday_guard as hg
    monkeypatch.setattr(hg, "is_trading_day", lambda d, config_dir: False)
    rc = runner.main(["--db", str(db), "--root", str(root),
                      "--report-dir", str(tmp_path / "rep"),
                      "--force", "--no-telegram"])
    assert rc == 0
    conn = db_connect.connect(db, attach=False)
    n_status = conn.execute("SELECT COUNT(*) FROM control_tower_status").fetchone()[0]
    hb = conn.execute("SELECT status FROM cron_heartbeat "
                      "WHERE job_name='control_tower'").fetchone()
    conn.close()
    assert n_status == 1                         # --force ran the full aggregation
    assert hb and hb[0] == "SUCCESS"             # SUCCESS heartbeat recorded


# ── daily_report observability heartbeat (the source fix) ─────────────────────

def test_daily_report_helper_records_heartbeat(tmp_path):
    db = _db(tmp_path)
    from reports.daily_report import record_daily_report_heartbeat
    record_daily_report_heartbeat(db, "SUCCESS", 1.23)
    conn = db_connect.connect(db, attach=False)
    row = conn.execute("SELECT status FROM cron_heartbeat "
                       "WHERE job_name='daily_report'").fetchone()
    conn.close()
    assert row and row[0] == "SUCCESS"           # job_name MUST match registry key


def test_daily_report_main_writes_heartbeat(tmp_path):
    db = _db(tmp_path)
    from reports import daily_report
    out = tmp_path / "out"
    rc = daily_report.main(["--db", str(db), "--output-dir", str(out),
                            "--date", "2026-06-29", "--force"])
    assert rc == 0
    assert (out / "daily_report_2026-06-29.xlsx").exists()
    conn = db_connect.connect(db, attach=False)
    row = conn.execute("SELECT status FROM cron_heartbeat "
                       "WHERE job_name='daily_report'").fetchone()
    conn.close()
    assert row and row[0] == "SUCCESS"           # ran the xlsx + heartbeated
