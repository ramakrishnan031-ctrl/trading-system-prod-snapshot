"""
scripts/disk_monitor.py -- Trading System v2  FIX-140

Purpose:
    Runtime disk space monitor. Run via cron every hour.
    Checks disk usage and sends Telegram alerts at warning/critical thresholds.
    At critical level, auto-deletes oldest logs (never DB, never last 2 days).

Exit codes:
    0 -- success (check completed, space OK or cleaned up)
    1 -- error during execution
    2 -- critical threshold hit (cleanup attempted)
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.time_authority import now_ist


_WARNING_PCT = 75
_CRITICAL_PCT = 86
_KEEP_DAYS = 2


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="disk_monitor",
        description="FIX-140: Runtime disk space monitor with Telegram alerts.",
    )
    parser.add_argument("--project-dir", metavar="PATH", default=None)
    parser.add_argument("--warning-pct", type=int, default=_WARNING_PCT)
    parser.add_argument("--critical-pct", type=int, default=_CRITICAL_PCT)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-days", type=int, default=_KEEP_DAYS)
    return parser.parse_args(argv)


def check_disk_usage(
    project_dir: Path,
    log: logging.Logger,
    warning_pct: int = _WARNING_PCT,
    critical_pct: int = _CRITICAL_PCT,
    dry_run: bool = False,
    keep_days: int = _KEEP_DAYS,
) -> dict:
    """
    Check disk usage and take action at thresholds.

    Returns dict with: usage_pct, free_gb, total_gb, level, files_deleted
    """
    usage = shutil.disk_usage(project_dir)
    total_gb = usage.total / (1024 ** 3)
    free_gb = usage.free / (1024 ** 3)
    used_pct = int((usage.used / usage.total) * 100)

    result = {
        "usage_pct": used_pct,
        "free_gb": round(free_gb, 1),
        "total_gb": round(total_gb, 1),
        "level": "OK",
        "files_deleted": 0,
        "bytes_freed": 0,
    }

    if used_pct >= critical_pct:
        result["level"] = "CRITICAL"
        log.critical(
            "disk_monitor: CRITICAL disk usage %d%% (free=%.1fGB)",
            used_pct, free_gb,
        )
        deleted, freed = _cleanup_old_files(
            project_dir, log, dry_run=dry_run, keep_days=keep_days,
        )
        result["files_deleted"] = deleted
        result["bytes_freed"] = freed

    elif used_pct >= warning_pct:
        result["level"] = "WARNING"
        log.warning(
            "disk_monitor: WARNING disk usage %d%% (free=%.1fGB)",
            used_pct, free_gb,
        )
    else:
        log.info(
            "disk_monitor: OK disk usage %d%% (free=%.1fGB)",
            used_pct, free_gb,
        )

    return result


def _cleanup_old_files(
    project_dir: Path,
    log: logging.Logger,
    dry_run: bool = False,
    keep_days: int = _KEEP_DAYS,
) -> tuple[int, int]:
    """
    Delete oldest log/report files. NEVER touches DB or files < keep_days old.
    Returns (files_deleted, bytes_freed).
    """
    cutoff = now_ist() - timedelta(days=keep_days)
    candidates: list[tuple[float, Path]] = []

    logs_dir = project_dir / "logs"
    reports_dir = project_dir / "reports" / "output"

    for directory in (logs_dir, reports_dir):
        if not directory.exists():
            continue
        for f in directory.iterdir():
            if not f.is_file():
                continue
            if f.suffix in (".db", ".db-wal", ".db-shm"):
                continue
            try:
                mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=cutoff.tzinfo)
                if mtime < cutoff:
                    candidates.append((f.stat().st_mtime, f))
            except OSError:
                continue

    candidates.sort(key=lambda x: x[0])

    target_count = max(1, len(candidates) // 10)
    deleted = 0
    freed = 0

    for _, fpath in candidates[:target_count]:
        size = fpath.stat().st_size
        if dry_run:
            log.info("disk_monitor.dry_run: would delete %s (%d bytes)", fpath, size)
        else:
            try:
                fpath.unlink()
                log.info("disk_monitor.deleted: %s (%d bytes)", fpath, size)
                freed += size
                deleted += 1
            except OSError as exc:
                log.warning("disk_monitor.delete_failed: %s: %s", fpath, exc)

    return deleted, freed


def _send_telegram_alert(level: str, result: dict, log: logging.Logger) -> None:
    """Best-effort Telegram alert. Failures are logged, not raised."""
    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env(logger=log)
        if not notifier:
            log.debug("disk_monitor: Telegram env vars not set; skipping alert")
            return

        icon = "!!" if level == "CRITICAL" else "!"
        msg = (
            f"{icon} DISK {level}\n"
            f"Usage: {result['usage_pct']}%\n"
            f"Free: {result['free_gb']}GB / {result['total_gb']}GB"
        )
        if result["files_deleted"] > 0:
            msg += f"\nAuto-cleaned: {result['files_deleted']} files ({result['bytes_freed'] // 1024}KB freed)"
        notifier.send(
            severity=level,
            title=f"Disk {level}",
            body=msg,
            source_module="disk_monitor",
        )
    except Exception as exc:
        log.warning("disk_monitor: Telegram alert failed: %s", exc)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("disk_monitor")

    project_dir = Path(args.project_dir) if args.project_dir else _ROOT

    try:
        result = check_disk_usage(
            project_dir=project_dir,
            log=log,
            warning_pct=args.warning_pct,
            critical_pct=args.critical_pct,
            dry_run=args.dry_run,
            keep_days=args.keep_days,
        )
    except Exception as exc:
        log.error("disk_monitor: check failed: %s", exc, exc_info=True)
        return 1

    if result["level"] in ("WARNING", "CRITICAL"):
        _send_telegram_alert(result["level"], result, log)

    if result["level"] == "CRITICAL":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
