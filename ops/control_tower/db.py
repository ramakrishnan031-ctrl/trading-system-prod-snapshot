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
            recommended_action = excluded.recommended_action,
            -- re-occurrence: a RESOLVED finding seen again -> back to OPEN
            -- (clears resolved_at); OPEN/ACKNOWLEDGED keep their status.
            status = CASE WHEN control_tower_findings.status='RESOLVED'
                          THEN 'OPEN' ELSE control_tower_findings.status END,
            resolved_at = CASE WHEN control_tower_findings.status='RESOLVED'
                               THEN NULL ELSE control_tower_findings.resolved_at END
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


def prior_status_map(conn) -> dict:
    """{(category, resource_name, reason): status} BEFORE this run's UPSERTs — so
    the reporter can classify each detected finding as new / re-opened / unchanged."""
    return {(r[0], r[1], r[2]): r[3] for r in conn.execute(
        "SELECT category, resource_name, reason, status FROM control_tower_findings")}


def auto_resolve_stale(conn, ran_categories, scan_time: str, now: str) -> int:
    """RESOLVE OPEN/ACKNOWLEDGED findings in categories that RAN this run but were
    NOT re-detected (last_seen < scan_time). GUARD: only `ran_categories` — a
    check that errored/skipped must never falsely resolve its findings."""
    cats = list(ran_categories)
    if not cats:
        return 0
    q = ",".join("?" for _ in cats)
    cur = conn.execute(
        f"UPDATE control_tower_findings SET status='RESOLVED', resolved_at=? "
        f"WHERE category IN ({q}) AND status IN ('OPEN','ACKNOWLEDGED') "
        f"AND last_seen < ?",
        (now, *cats, scan_time))
    return cur.rowcount


def ack_finding(conn, fid: int, now: str) -> int:
    cur = conn.execute(
        "UPDATE control_tower_findings SET status='ACKNOWLEDGED', acked_at=? "
        "WHERE id=? AND status!='RESOLVED'", (now, fid))
    conn.commit()
    return cur.rowcount


def unack_finding(conn, fid: int) -> int:
    cur = conn.execute(
        "UPDATE control_tower_findings SET status='OPEN', acked_at=NULL "
        "WHERE id=? AND status='ACKNOWLEDGED'", (fid,))
    conn.commit()
    return cur.rowcount


_SEV_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO")


def get_findings(conn, statuses):
    q = ",".join("?" for _ in statuses)
    order = " ".join(f"WHEN '{s}' THEN {i}" for i, s in enumerate(_SEV_ORDER))
    return conn.execute(
        f"SELECT id, severity, category, resource_name, reason, first_seen, "
        f"last_seen, status, acked_at, resolved_at FROM control_tower_findings "
        f"WHERE status IN ({q}) ORDER BY CASE severity {order} ELSE 9 END, id",
        tuple(statuses)).fetchall()


def open_severities(conn):
    return [r[0] for r in conn.execute(
        "SELECT severity FROM control_tower_findings WHERE status='OPEN'")]


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
