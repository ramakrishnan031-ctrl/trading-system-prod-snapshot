#!/usr/bin/env python3
"""
scripts/check_cron_drift.py — FIX-145

Checks that all expected cron jobs have run in the last 24 hours.
Sends Telegram alert if any job is missing its heartbeat.

Designed to run daily at 18:00 IST (after market close + all cron jobs).

Usage:
    python scripts/check_cron_drift.py [--db-path PATH] [--config-dir PATH]
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from core.state_store import StateStore
from core.time_authority import now_ist
from core.logger import get_logger

_log = get_logger("check_cron_drift")

# Expected cron jobs and their typical schedule (for reference in alerts)
# Only include critical jobs that we want to monitor; hourly jobs like disk_monitor
# would generate too many heartbeats and are less critical.
EXPECTED_JOBS = {
    # Pre-market
    "auto_refresh_token": "08:00 IST Mon-Fri",
    "fetch_fno_ban": "08:30 IST Mon-Fri",
    # EOD cleanup chain
    "fetch_daily_candles": "15:40 IST Mon-Fri",
    "reconcile_positions": "15:45 IST Mon-Fri",
    "eod_cleanup": "15:50 IST Mon-Fri",
    "eod_verify": "15:55 IST Mon-Fri",
    # Reports chain
    "daily_review": "16:00 IST Mon-Fri",
    "wal_checkpoint": "16:00 IST Mon-Fri",
    "daily_report": "16:05 IST Mon-Fri",
    "trade_journal": "16:10 IST Mon-Fri",
    "compute_strategy_metrics": "16:15 IST Mon-Fri",
    "gemini_log_review": "16:20 IST Mon-Fri",
}


def check_cron_drift(store: StateStore) -> list[str]:
    """
    Check for missing cron heartbeats in the last 24 hours.

    Returns list of missing job names (empty = all OK).
    """
    cutoff = now_ist() - timedelta(hours=24)
    cutoff_iso = cutoff.isoformat()

    heartbeats = store.get_cron_heartbeats_since(cutoff_iso)
    seen_jobs = {h["job_name"] for h in heartbeats}

    missing = []
    for job_name in EXPECTED_JOBS:
        if job_name not in seen_jobs:
            missing.append(job_name)

    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description="Check for missing cron heartbeats")
    parser.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--dry-run", action="store_true", help="Print alert but don't send Telegram")
    args = parser.parse_args()

    if not args.db_path.exists():
        _log.warning("check_cron_drift.db_not_found", extra={"db_path": str(args.db_path)})
        print(f"Database not found: {args.db_path}")
        return 1

    store = StateStore(args.db_path)

    missing = check_cron_drift(store)

    if not missing:
        _log.info("check_cron_drift.all_ok", extra={"jobs_checked": len(EXPECTED_JOBS)})
        print(f"All {len(EXPECTED_JOBS)} expected cron jobs ran in last 24h")
        return 0

    # Build alert message
    lines = ["CRON DRIFT ALERT: Missing heartbeats in last 24h\n"]
    for job in missing:
        schedule = EXPECTED_JOBS.get(job, "unknown")
        lines.append(f"  - {job} (expected: {schedule})")

    alert_msg = "\n".join(lines)
    print(alert_msg)

    _log.warning(
        "check_cron_drift.missing_jobs",
        extra={"missing": missing, "count": len(missing)},
    )

    if args.dry_run:
        print("\n[DRY-RUN] Would send Telegram alert")
        return 1

    # Send Telegram alert
    try:
        from alerts.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier.from_config(args.config_dir)
        notifier.send_alert(alert_msg, level="WARNING")
        print("Telegram alert sent")
    except Exception as e:
        _log.error("check_cron_drift.telegram_failed", extra={"error": str(e)})
        print(f"Failed to send Telegram alert: {e}")

    store.close()
    return 1


if __name__ == "__main__":
    sys.exit(main())
