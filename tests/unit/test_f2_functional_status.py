"""F2 (15-Jul-2026): EXECUTION status vs FUNCTIONAL status.

A job whose script exits 0 but whose real function failed (empty CSV, undelivered
email) must record status=SUCCESS (execution) + functional_status=<real outcome>,
so a delivery/artifact failure can no longer read as a clean SUCCESS (CLASS 1).
The functional status rides in the existing `message` column (no schema change).

Every test here FAILS on the pre-fix code (no functional_status param / attr /
parser / email-health helper).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.state_store import StateStore
from utils.cron_heartbeat import (
    HeartbeatTimer,
    _encode_functional,
    parse_functional_status,
    record_heartbeat,
)


def _rows(db: Path):
    store = StateStore(db_path=db)
    try:
        return store.get_cron_heartbeats_since("2000-01-01T00:00:00")
    finally:
        store.close()


def test_encode_parse_roundtrip():
    assert parse_functional_status(_encode_functional("EMPTY_NO_DATA", "ran ok")) == "EMPTY_NO_DATA"
    assert parse_functional_status(_encode_functional("OK", None)) == "OK"
    assert parse_functional_status("a plain message") is None
    assert parse_functional_status(None) is None
    # execution message is preserved after the func prefix
    assert "ran ok" in _encode_functional("EMPTY_NO_DATA", "ran ok")


def test_record_heartbeat_execution_and_functional_are_independent(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()   # build schema
    # the screened-csv silent-failure shape: EXECUTION ok, FUNCTIONAL empty
    assert record_heartbeat("generate_screened_csv", status="SUCCESS",
                            functional_status="EMPTY_NO_DATA", message="0 symbols",
                            db_path=db) is True
    row = [r for r in _rows(db) if r["job_name"] == "generate_screened_csv"][0]
    assert (row["status"] or "").upper() == "SUCCESS"                    # execution UNCHANGED
    assert parse_functional_status(row["message"]) == "EMPTY_NO_DATA"    # functional recorded


def test_heartbeat_timer_carries_functional_status(tmp_path):
    db = tmp_path / "t.db"
    StateStore(db_path=db).close()
    with HeartbeatTimer("some_report", db_path=db) as t:
        t.functional_status = "FAILED"   # e.g. the report artifact was not produced
    row = [r for r in _rows(db) if r["job_name"] == "some_report"][0]
    assert (row["status"] or "").upper() == "SUCCESS"                    # ran without exception
    assert parse_functional_status(row["message"]) == "FAILED"          # but FUNCTIONALLY failed


def test_officer_surfaces_email_delivery_degraded(tmp_path):
    from scripts.cron_officer import _email_delivery_health_line
    # healthy: no marker -> no line (keeps the report clean)
    assert _email_delivery_health_line(tmp_path) is None
    # degraded marker present -> a visible line naming the credential to restore
    (tmp_path / "alert_watcher_degraded.json").write_text(json.dumps(
        {"degraded": True, "reason": "SMTP auth failure — email delivery down",
         "since": "2026-07-15T20:00:00+05:30", "last_stuck": 8}))
    line = _email_delivery_health_line(tmp_path)
    assert line is not None
    assert "DEGRADED" in line and "ALERT_SMTP_PASSWORD" in line
