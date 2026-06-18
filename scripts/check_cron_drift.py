#!/usr/bin/env python3
"""
scripts/check_cron_drift.py — FIX-145 / TASK #3 (Cron Officer)

Checks that every monitored cron job that was DUE today has emitted a heartbeat
in the last 24 hours. Sends a Telegram alert listing any missing jobs.

The expected-job set is now derived from config/cron_registry.yaml (the single
source of truth) — no hardcoded list. Only jobs with `monitored: true` that are
due today and were scheduled at/before "now" are expected (so a 16:00 job isn't
flagged at an 08:00 run, and weekly/monthly jobs aren't flagged off-schedule).
A SKIPPED heartbeat (NSE holiday) counts as "ran".

Designed to run daily at 18:00 IST (after market close + all cron jobs).

Usage:
    python scripts/check_cron_drift.py [--db-path PATH] [--config-dir PATH] [--dry-run]
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from core.cron_registry import CronRegistry
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist

_log = get_logger("check_cron_drift")


def check_cron_drift(store: StateStore, registry: CronRegistry, config_dir: Path) -> list[str]:
    """Return the names of monitored, due-today jobs missing a heartbeat (24h)."""
    now = now_ist()
    cutoff_iso = (now - timedelta(hours=24)).isoformat()

    heartbeats = store.get_cron_heartbeats_since(cutoff_iso)
    seen_jobs = {h["job_name"] for h in heartbeats}

    expected = registry.expected_heartbeat_jobs(
        now.date(), config_dir=config_dir, before_time=now.time()
    )
    return [job.name for job in expected if job.name not in seen_jobs]


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

    try:
        registry = CronRegistry.load(args.config_dir / "cron_registry.yaml")
    except Exception as e:
        _log.error("check_cron_drift.registry_load_failed", extra={"error": str(e)})
        print(f"Failed to load cron registry: {e}")
        return 1

    store = StateStore(args.db_path)
    missing = check_cron_drift(store, registry, args.config_dir)

    if not missing:
        expected_n = len(registry.expected_heartbeat_jobs(now_ist().date(), config_dir=args.config_dir, before_time=now_ist().time()))
        _log.info("check_cron_drift.all_ok", extra={"jobs_checked": expected_n})
        print(f"All {expected_n} expected cron jobs ran in last 24h")
        store.close()
        return 0

    # Build alert message
    lines = ["CRON DRIFT ALERT: Missing heartbeats in last 24h\n"]
    for name in missing:
        schedule = registry.get(name).schedule
        lines.append(f"  - {name} (expected: {schedule})")
    alert_msg = "\n".join(lines)
    print(alert_msg)

    _log.warning("check_cron_drift.missing_jobs", extra={"missing": missing, "count": len(missing)})

    if args.dry_run:
        print("\n[DRY-RUN] Would send Telegram alert")
        store.close()
        return 1

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
