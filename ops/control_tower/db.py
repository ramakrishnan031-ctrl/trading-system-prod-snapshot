"""ops/control_tower/db.py -- Control Tower Phase 1b DAO.

UPSERTs findings on the dedup identity (category, resource_name, reason),
replaces the day's freshness rows, writes one run row, and ensures the 1b query
indexes. NO health-score / status roll-up (those are 1c).
"""
from __future__ import annotations

from typing import Iterable

from .model import Finding

# 1b query-path index NOT already created by the v40 schema (the dedup-unique +
# (status,severity) + runs(started_at) + freshness(run_date,stage) indexes ship
# in 1a). Idempotent — created on every aggregator run.
_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_ct_findings_last_seen "
    "ON control_tower_findings(last_seen)",
)


def ensure_indexes(conn) -> None:
    for ddl in _INDEX_DDL:
        conn.execute(ddl)


def upsert_finding(conn, f: Finding, scan_time: str) -> None:
    """INSERT a new finding (first_seen=last_seen=scan_time, status=OPEN) or, on
    the (category, resource_name, reason) dedup identity, UPDATE last_seen +
    refresh the mutable fields. NEVER touches first_seen on update."""
    conn.execute(
        """
        INSERT INTO control_tower_findings
            (scan_time, category, severity, resource_type, resource_name,
             location, reason, recommended_action, status, first_seen, last_seen)
        VALUES
            (:scan, :category, :severity, :resource_type, :resource_name,
             :location, :reason, :action, 'OPEN', :scan, :scan)
        ON CONFLICT(category, resource_name, reason) DO UPDATE SET
            scan_time          = excluded.scan_time,
            last_seen          = excluded.last_seen,
            severity           = excluded.severity,
            resource_type      = excluded.resource_type,
            location           = excluded.location,
            recommended_action = excluded.recommended_action
        """,
        {
            "scan": scan_time, "category": f.category, "severity": f.severity,
            "resource_type": f.resource_type, "resource_name": f.resource_name,
            "location": f.location, "reason": f.reason, "action": f.recommended_action,
        },
    )


def replace_freshness(conn, run_date: str, rows: Iterable[dict]) -> None:
    """One row per (run_date, stage); a re-run replaces the day's set."""
    conn.execute("DELETE FROM control_tower_freshness WHERE run_date = ?", (run_date,))
    conn.executemany(
        """
        INSERT INTO control_tower_freshness
            (run_date, stage, expected_by, actual_at, delay_minutes, status)
        VALUES (:run_date, :stage, :expected_by, :actual_at, :delay_minutes, :status)
        """,
        [{"run_date": run_date, **r} for r in rows],
    )


def write_run(conn, run: dict) -> None:
    """Write ONE control_tower_runs row (RAW per-severity counts only — the
    weighted health score + status roll-up are 1c)."""
    conn.execute(
        """
        INSERT INTO control_tower_runs
            (run_id, started_at, completed_at, duration_s, checks_run,
             findings_total, critical_count, high_count, medium_count,
             low_count, status)
        VALUES
            (:run_id, :started_at, :completed_at, :duration_s, :checks_run,
             :findings_total, :critical_count, :high_count, :medium_count,
             :low_count, :status)
        """,
        run,
    )
