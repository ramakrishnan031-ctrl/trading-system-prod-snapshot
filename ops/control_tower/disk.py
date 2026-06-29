"""ops/control_tower/disk.py -- Control Tower Phase 1b disk/backup findings.

Reuses the 1a FRESH disk% + size computation (size_logger.compute_sizes) and
GENERATES findings on thresholds. Growth findings fire ONLY once trend history
exists (>=1 prior control_tower_trends row) -- else skipped (never a misleading
"grew since nothing").
"""
from __future__ import annotations

from pathlib import Path

from .model import Finding
from .size_logger import compute_sizes

DISK_HIGH_PCT = 85.0
DISK_CRITICAL_PCT = 95.0
BACKUP_GROWTH_MB = 500.0    # backups dir delta vs the previous trend row
LOG_GROWTH_MB = 300.0       # logs dir delta vs the previous trend row


def evaluate(conn, root, db_path, today: str):
    """Return (sizes, findings). `sizes` = the 1a size dict (the caller may also
    UPSERT the trend row; 1b only reads it to derive findings)."""
    sizes = compute_sizes(Path(root), Path(db_path))
    findings: list[Finding] = []

    pct = sizes.get("disk_used_pct")
    if pct is not None and pct >= DISK_CRITICAL_PCT:
        findings.append(Finding("disk", "CRITICAL", "mount", "/",
            reason=f"disk usage {pct:.1f}% >= {DISK_CRITICAL_PCT:.0f}%",
            location="/", recommended_action="free space — prune backups/logs"))
    elif pct is not None and pct >= DISK_HIGH_PCT:
        findings.append(Finding("disk", "HIGH", "mount", "/",
            reason=f"disk usage {pct:.1f}% >= {DISK_HIGH_PCT:.0f}%",
            location="/", recommended_action="watch disk; prune soon"))

    # growth — only with prior history
    prev = conn.execute(
        "SELECT date, backup_size_mb, log_size_mb FROM control_tower_trends "
        "WHERE date < ? ORDER BY date DESC LIMIT 1", (today,)).fetchone()
    if prev is not None:
        _growth(findings, "backup", "data_store/backups",
                sizes.get("backup_size_mb"), prev[1], BACKUP_GROWTH_MB, prev[0])
        _growth(findings, "disk", "data_store/logs",
                sizes.get("log_size_mb"), prev[2], LOG_GROWTH_MB, prev[0])
    return sizes, findings


def _growth(findings, category, resource, cur, prev, delta_mb, prev_date):
    if cur is None or prev is None or (cur - prev) < delta_mb:
        return
    findings.append(Finding(
        category=category, severity="MEDIUM", resource_type="dir",
        resource_name=resource,
        reason=f"{resource} grew {cur - prev:.0f}MB since {prev_date} "
               f"({prev:.0f}->{cur:.0f}MB)",
        location=resource, recommended_action="check retention / rotation"))
