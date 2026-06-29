"""ops/control_tower/status.py -- Control Tower Phase 1c status roll-up.

One control_tower_status row per run_date (last run of the day wins): the
per-domain statuses, disk/backup, OPEN critical/high counts, the overall health
band, and last_successful_run.
"""
from __future__ import annotations


def freshness_status(freshness_rows) -> str:
    statuses = {r["status"] for r in freshness_rows}
    if "MISSING" in statuses:
        return "MISSING"
    if "OVERDUE" in statuses:
        return "OVERDUE"
    return "OK"


def write_status(conn, run_date: str, *, source_status: dict, fresh_status: str,
                 disk_pct, backup_mb, critical: int, high: int,
                 overall: str, last_successful_run) -> None:
    conn.execute(
        """
        INSERT INTO control_tower_status
            (run_date, security_status, cron_status, config_status,
             freshness_status, disk_pct, backup_size_mb, critical_count,
             high_count, overall_status, last_successful_run)
        VALUES (:run_date, :security, :cron, :config, :freshness, :disk_pct,
                :backup, :critical, :high, :overall, :lsr)
        ON CONFLICT(run_date) DO UPDATE SET
            security_status     = excluded.security_status,
            cron_status         = excluded.cron_status,
            config_status       = excluded.config_status,
            freshness_status    = excluded.freshness_status,
            disk_pct            = excluded.disk_pct,
            backup_size_mb      = excluded.backup_size_mb,
            critical_count      = excluded.critical_count,
            high_count          = excluded.high_count,
            overall_status      = excluded.overall_status,
            last_successful_run = excluded.last_successful_run
        """,
        {
            "run_date": run_date,
            "security": source_status.get("security"),
            "cron": source_status.get("cron"),
            "config": source_status.get("config"),
            "freshness": fresh_status, "disk_pct": disk_pct, "backup": backup_mb,
            "critical": critical, "high": high, "overall": overall,
            "lsr": last_successful_run,
        },
    )
