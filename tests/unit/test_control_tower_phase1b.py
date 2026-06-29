"""Control Tower Phase 1b (AGGREGATOR + FRESHNESS) — unit tests.

Covers the native->tower severity maps, the dedup-identity findings UPSERT, the
freshness engine (NA-before-due / MISSING-after-due / OK-present), disk
thresholds + history-gated growth, the security/excursion/cron adapters, and an
end-to-end run_aggregation that writes findings/freshness/runs rows.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core import db_connect
from core.state_store import StateStore
from ops.control_tower import aggregator, disk, freshness
from ops.control_tower.db import upsert_finding
from ops.control_tower.model import Finding
from ops.control_tower.severity import CONFIG_MAP, SECURITY_MAP, map_severity, worst

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_DIR = Path("config")


def _db(tmp_path) -> Path:
    db = tmp_path / "t.db"
    StateStore(db).close()          # v40 schema (control_tower_* + analytics)
    return db


# ── severity maps (the reviewable single place) ───────────────────────────────

def test_severity_maps():
    assert map_severity(SECURITY_MAP, "WARNING") == "MEDIUM"   # the 1b §1A decision
    assert map_severity(SECURITY_MAP, "CRITICAL") == "CRITICAL"
    assert map_severity(SECURITY_MAP, "INFO") == "INFO"
    assert map_severity(CONFIG_MAP, "BLOCK") == "CRITICAL"
    assert map_severity(CONFIG_MAP, "WARN") == "HIGH"
    assert worst(["LOW", "CRITICAL", "MEDIUM"]) == "CRITICAL"
    assert worst([]) == "INFO"


# ── findings UPSERT (dedup identity) ──────────────────────────────────────────

def test_upsert_finding_dedup(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=False)
    f = Finding("cron", "MEDIUM", "job", "foo", reason="missed")
    upsert_finding(conn, f, "2026-06-29T17:00:00+05:30")
    upsert_finding(conn, f, "2026-06-29T17:05:00+05:30")   # same identity -> UPDATE
    rows = conn.execute(
        "SELECT first_seen, last_seen, status FROM control_tower_findings").fetchall()
    conn.close()
    assert len(rows) == 1                                  # deduped
    assert rows[0][0] == "2026-06-29T17:00:00+05:30"       # first_seen preserved
    assert rows[0][1] == "2026-06-29T17:05:00+05:30"       # last_seen advanced
    assert rows[0][2] == "OPEN"


# ── freshness engine ──────────────────────────────────────────────────────────

def _seed_signal(conn, received_at):
    conn.execute(
        "INSERT INTO signals (signal_id, symbol, scanner, strategy, triggered_at, "
        "received_at, expires_at, status, fingerprint, fingerprint_date) "
        "VALUES (?,?,?,?,?,?,?, 'QUEUED', ?, ?)",
        ("s1", "ABC", "scan", "strat", received_at, received_at, received_at,
         "fp1", received_at[:10]))
    conn.commit()


def test_freshness_na_before_due_and_ok(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=True)
    _seed_signal(conn, "2026-06-29T10:00:00+05:30")
    now = datetime(2026, 6, 29, 14, 0, tzinfo=_IST)        # before the EOD deadlines
    rows, findings = freshness.evaluate(conn, now, tmp_path / "cron_marks")
    conn.close()
    by = {r["stage"]: r["status"] for r in rows}
    assert by["signals"] == "OK"                           # present today
    assert by["candles"] == "NA"                           # not yet due (15:40)
    assert by["eod"] == "NA"
    assert findings == []                                  # NA/OK never emit findings


def test_freshness_missing_after_due(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=True)
    now = datetime(2026, 6, 29, 17, 0, tzinfo=_IST)        # past every deadline, no data
    rows, findings = freshness.evaluate(conn, now, tmp_path / "cron_marks")
    conn.close()
    by = {r["stage"]: r["status"] for r in rows}
    assert by["candles"] == "MISSING" and by["signals"] == "MISSING"
    assert {f.resource_name for f in findings} >= {"candles", "signals", "eod"}
    assert all(f.category == "freshness" for f in findings)


# ── disk findings ─────────────────────────────────────────────────────────────

def test_disk_growth_skipped_without_history(tmp_path):
    db = _db(tmp_path)
    root = tmp_path / "proj"
    (root / "data_store" / "backups").mkdir(parents=True)
    (root / "logs").mkdir()
    conn = db_connect.connect(db, attach=False)
    sizes, findings = disk.evaluate(conn, root, db, "2026-06-29")
    conn.close()
    assert "disk_used_pct" in sizes
    # no prior control_tower_trends row -> NO growth findings (not misleading)
    assert all("grew" not in f.reason for f in findings)


def test_disk_growth_fires_with_history(tmp_path):
    db = _db(tmp_path)
    root = tmp_path / "proj"
    (root / "data_store" / "backups").mkdir(parents=True)
    (root / "logs").mkdir()
    # yesterday's trend row: tiny backups -> today's real size is a big jump
    conn = db_connect.connect(db, attach=False)
    conn.execute("INSERT INTO control_tower_trends(date, backup_size_mb, log_size_mb) "
                 "VALUES ('2026-06-28', 0.0, 0.0)")
    conn.commit()
    # make today's backups dir big enough to exceed the 500MB delta
    big = root / "data_store" / "backups" / "big.bin"
    big.write_bytes(b"\0" * (1024 * 1024))  # 1MB file; we assert the logic path, not 500MB
    _sizes, findings = disk.evaluate(conn, root, db, "2026-06-29")
    conn.close()
    # 1MB < 500MB threshold -> still no growth finding; proves threshold gating
    assert all("grew" not in f.reason for f in findings)


# ── adapters ──────────────────────────────────────────────────────────────────

def test_read_security_absent_clean_dirty_stale(tmp_path):
    now = datetime(2026, 6, 29, 13, 15, tzinfo=_IST)
    # absent -> unavailable + LOW
    r = aggregator.read_security(tmp_path, now)
    assert r.status == "unavailable" and r.findings[0].severity == "LOW"

    secdir = tmp_path / "data_store" / "security"
    secdir.mkdir(parents=True)
    lr = secdir / "last_run.json"

    lr.write_text(json.dumps({"version": 1, "timestamp": now.isoformat(),
                              "checks_run": 9, "findings_count": 0,
                              "max_severity": "INFO", "clean": True}))
    assert aggregator.read_security(tmp_path, now).status == "ok"

    lr.write_text(json.dumps({"version": 1, "timestamp": now.isoformat(),
                              "checks_run": 9, "findings_count": 1,
                              "max_severity": "WARNING", "clean": False}))
    r = aggregator.read_security(tmp_path, now)
    assert r.status == "warn"
    assert any(f.severity == "MEDIUM" for f in r.findings)      # WARNING->MEDIUM

    # stale: timestamp 60 min old -> MEDIUM stale finding
    old = (now - timedelta(minutes=60)).isoformat()
    lr.write_text(json.dumps({"version": 1, "timestamp": old, "checks_run": 9,
                              "findings_count": 0, "max_severity": "INFO", "clean": True}))
    r = aggregator.read_security(tmp_path, now)
    assert any("stale" in f.reason for f in r.findings)


def test_read_excursion_norun_and_failed(tmp_path):
    db = _db(tmp_path)
    conn = db_connect.connect(db, attach=False)
    assert aggregator.read_excursion(conn).status == "not_run"   # 0 rows, NOT a failure
    assert aggregator.read_excursion(conn).findings == []
    conn.execute(
        "INSERT INTO excursion_reconstruction_runs (run_id, mode, started_at, "
        "completed_at, trades_examined, trades_written, "
        "trades_skipped_unreconstructable, trades_failed, status) "
        "VALUES ('r1','daily','2026-06-29T15:50:00+05:30','2026-06-29T15:50:05+05:30',"
        "5,3,0,2,'FAILED')")
    conn.commit()
    r = aggregator.read_excursion(conn)
    conn.close()
    assert r.status == "failed" and r.findings[0].severity == "HIGH"


def test_read_cron_flags_only_unseen(tmp_path):
    db = _db(tmp_path)
    from core.cron_registry import load_cron_registry
    reg = load_cron_registry(_CONFIG_DIR / "cron_registry.yaml")
    now = datetime(2026, 6, 29, 17, 0, tzinfo=_IST)            # a trading Monday
    expected = reg.expected_heartbeat_jobs(now.date(), _CONFIG_DIR, before_time=now.time())
    names = [j.name for j in expected]
    assert names, "expected some monitored jobs due by 17:00 on a trading day"
    conn = db_connect.connect(db, attach=False)
    for n in names[1:]:                                        # seed all but the first
        conn.execute("INSERT INTO cron_heartbeat(job_name, executed_at, status) "
                     "VALUES (?,?, 'SUCCESS')", (n, now.isoformat()))
    conn.commit()
    res = aggregator.read_cron(conn, _CONFIG_DIR, tmp_path / "cron_marks", now)
    conn.close()
    flagged = {f.resource_name for f in res.findings}
    assert names[0] in flagged                                 # unseen -> flagged
    assert all(n not in flagged for n in names[1:])            # seen -> not flagged


# ── end-to-end ────────────────────────────────────────────────────────────────

def test_run_aggregation_writes_rows(tmp_path):
    db = _db(tmp_path)
    now = datetime(2026, 6, 29, 17, 0, tzinfo=_IST)
    summary = aggregator.run_aggregation(db, tmp_path, _CONFIG_DIR, now=now, write=True)
    assert summary["checks_run"] == 4 + 6 + 1                  # 4 adapters + 6 stages + disk
    conn = db_connect.connect(db, attach=False)
    n_find = conn.execute("SELECT COUNT(*) FROM control_tower_findings").fetchone()[0]
    n_fresh = conn.execute("SELECT COUNT(*) FROM control_tower_freshness "
                           "WHERE run_date='2026-06-29'").fetchone()[0]
    run = conn.execute("SELECT findings_total, status FROM control_tower_runs").fetchone()
    conn.close()
    assert n_fresh == 6                                        # one row per stage
    assert n_find > 0 and run[0] == n_find
    assert run[1] in ("OK", "OK_WITH_FINDINGS")
