"""Control Tower Phase 1a (FOUNDATION) — unit tests.

Covers: the v40 schema (5 control_tower_* tables, pure addition), the F3
size-logger (fresh disk%, field-scoped idempotent UPSERT), and the F1 security
last_run.json status write (ADDITIVE — proves security behaviour unchanged).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from core import db_connect
from core.state_store import EXPECTED_SCHEMA_VERSION, StateStore
from ops.control_tower import size_logger
import scripts.security_monitor as sm

_IST = timezone(timedelta(hours=5, minutes=30))

_CT_TABLES = [
    "control_tower_findings", "control_tower_freshness",
    "control_tower_runs", "control_tower_status", "control_tower_trends",
]


def _fresh_store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "t.db")


# ── schema (v40, pure addition) ───────────────────────────────────────────────

def test_schema_is_v40_with_five_control_tower_tables(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        assert EXPECTED_SCHEMA_VERSION >= 40   # control_tower_* tables present since v40 (W0 later bumped to 41)
        assert store.get_schema_version() == EXPECTED_SCHEMA_VERSION
        rows = store.fetch_all(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'control_tower_%'")
        assert sorted(r["name"] for r in rows) == _CT_TABLES
    finally:
        store.close()


def test_findings_dedup_is_unique_index(tmp_path):
    store = _fresh_store(tmp_path)
    try:
        idx = store.fetch_all("PRAGMA index_list('control_tower_findings')")
        assert any(r["name"] == "idx_ct_findings_dedup" and r["unique"] for r in idx)
    finally:
        store.close()


def test_existing_tables_untouched_by_addition(tmp_path):
    # pure addition: the prior tables (e.g. trades, excursion_reconstruction_runs)
    # still exist alongside the new ones.
    store = _fresh_store(tmp_path)
    try:
        for t in ("trades", "signals", "excursion_reconstruction_runs"):
            assert store.fetch_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (t,))
    finally:
        store.close()


# ── F3 size-logger ────────────────────────────────────────────────────────────

def _make_proj(tmp_path, backup_bytes=2048, log_text="hello"):
    root = tmp_path / "proj"
    (root / "data_store" / "backups").mkdir(parents=True)
    (root / "logs").mkdir()
    (root / "data_store" / "backups" / "a.backup").write_bytes(b"x" * backup_bytes)
    (root / "logs" / "system.log").write_text(log_text)
    return root


def test_size_logger_run_writes_fresh_sizes(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db).close()                      # create v40 schema
    root = _make_proj(tmp_path)
    now = datetime(2026, 6, 29, 17, 5, tzinfo=_IST)

    res = size_logger.run(db, root, now)
    assert res["date"] == "2026-06-29"
    assert res["backup_size_mb"] == round(2048 / 1024 / 1024, 2)
    assert res["log_size_mb"] == round(len("hello") / 1024 / 1024, 2)
    # disk computed FRESH (real value in [0,100]), never the -1.0 sentinel
    assert res["disk_used_pct"] is not None and 0.0 <= res["disk_used_pct"] <= 100.0

    conn = db_connect.connect(db, attach=False)
    row = conn.execute(
        "SELECT disk_used_pct, backup_size_mb, health_score "
        "FROM control_tower_trends WHERE date='2026-06-29'").fetchone()
    conn.close()
    assert row is not None
    assert row[2] is None                       # health_score left for the aggregator


def test_size_logger_upsert_idempotent_and_field_scoped(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db).close()
    # the aggregator (1b) pre-set health_score on the same date row
    conn = db_connect.connect(db, attach=False)
    conn.execute("INSERT INTO control_tower_trends(date, health_score) VALUES ('2026-06-29', 88)")
    conn.commit(); conn.close()

    root = _make_proj(tmp_path)
    now = datetime(2026, 6, 29, 17, 5, tzinfo=_IST)
    size_logger.run(db, root, now)
    size_logger.run(db, root, now)              # idempotent

    conn = db_connect.connect(db, attach=False)
    rows = conn.execute(
        "SELECT health_score, backup_size_mb FROM control_tower_trends "
        "WHERE date='2026-06-29'").fetchall()
    conn.close()
    assert len(rows) == 1                        # UPSERT, not a duplicate
    assert rows[0][0] == 88                      # aggregator's health_score preserved
    assert rows[0][1] is not None               # size field was written


# ── F1 security last_run.json (ADDITIVE) ──────────────────────────────────────

def test_last_run_status_clean(tmp_path):
    p = tmp_path / "security" / "last_run.json"
    now = datetime(2026, 6, 29, 13, 15, tzinfo=_IST)
    sm._write_last_run_status(p, [], 9, now)
    # SS-B (26-Jul-2026) EXTENDED this schema additively: `persistent` /
    # `persistent_count` name the conditions that are still present but no longer
    # alerting each pass. That is what makes it safe for _dedup to stop re-alerting
    # a persistent condition, so the field is asserted here rather than tolerated.
    # The aggregator reads via .get(), so no consumer is affected.
    assert json.loads(p.read_text(encoding="utf-8")) == {
        "version": 1, "timestamp": now.isoformat(), "checks_run": 9,
        "findings_count": 0, "max_severity": "INFO", "clean": True,
        "persistent": [], "persistent_count": 0,
    }


def test_last_run_status_records_native_max_severity(tmp_path):
    # F1 records the NATIVE security severity (CRITICAL|WARNING|INFO); the
    # native->tower map lives in the aggregator (ops/control_tower/severity.py).
    p = tmp_path / "last_run.json"
    now = datetime(2026, 6, 29, 13, 15, tzinfo=_IST)
    findings = [sm.Finding("WARNING", "k1", "t", "b"), sm.Finding("INFO", "k2", "t", "b")]
    sm._write_last_run_status(p, findings, 9, now)
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["max_severity"] == "WARNING"       # native, not tower-mapped
    assert d["findings_count"] == 2 and d["clean"] is False
    sm._write_last_run_status(p, findings + [sm.Finding("CRITICAL", "k3", "t", "b")], 9, now)
    assert json.loads(p.read_text(encoding="utf-8"))["max_severity"] == "CRITICAL"


def test_last_run_writer_is_additive_and_never_raises(tmp_path):
    findings = [sm.Finding("INFO", "k", "t", "b")]
    before = list(findings)
    sm._write_last_run_status(tmp_path / "x.json", findings, 9, datetime.now(_IST))
    assert findings == before                   # never mutates findings

    # unwritable path -> logged + swallowed, never raises into the security pass.
    #
    # The target's PARENT is a FILE, so creating anything beneath it raises on BOTH
    # platforms and stays inside tmp_path. The old literal "/nonexistent_xyz/sub/
    # last.json" was POSIX-only: on Windows it is not even absolute (no drive letter),
    # it resolves to D:\nonexistent_xyz\..., and the write SUCCEEDS -- so this test
    # passed while never exercising the swallow branch it exists to prove, and it
    # littered a stray D:\nonexistent_xyz\ on the dev box every run (the register's
    # NR-4 "stray D:\ folders").
    blocker = tmp_path / "iam_a_file"
    blocker.write_text("not a directory", encoding="utf-8")
    sm._write_last_run_status(
        blocker / "sub" / "last.json", findings, 9, datetime.now(_IST))


def test_run_pass_records_check_count_unchanged(tmp_path):
    # run_pass still runs its 9 isolated checks; the F1 count side-effect is set.
    authlog = tmp_path / "auth.log"
    authlog.write_text("")
    sm.run_pass(sm.SecConfig(), {}, authlog, datetime.now(_IST), baseline=False)
    assert sm._LAST_PASS_CHECK_COUNT == 9
