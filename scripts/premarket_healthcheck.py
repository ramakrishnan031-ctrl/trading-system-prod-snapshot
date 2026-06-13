#!/usr/bin/env python3
"""
scripts/premarket_healthcheck.py — FIX-146

Pre-market health check that runs at 08:30 IST (45 min before market open).
Catches problems early — before token arrives and main system starts.

Checks:
  1. Config files present and parseable
  2. Required secrets in environment
  3. Database accessible and schema version matches
  4. Disk space adequate
  5. Clock skew within bounds

Sends Telegram alert on any failure. Exit 0 = all OK, exit 1 = problems found.

Usage:
    python scripts/premarket_healthcheck.py [--dry-run]

Cron:
    30 8 * * 1-5 cd ~/systems/trading-system && PYTHONPATH=. ~/systems/venv/bin/python \\
        scripts/premarket_healthcheck.py >> logs/premarket-healthcheck.log 2>&1
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date as _date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.time_authority import now_ist
from core.logger import get_logger

_log = get_logger("premarket_healthcheck")

# Required secrets that must be set (match actual .env variable names)
REQUIRED_SECRETS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHANNEL_PRIMARY",
    "WEBHOOK_SECRET",
]

# Config files that must exist
CONFIG_FILES = [
    "system_config.yaml",
    "broker_costs.yaml",
    "broker_limits.yaml",
    "slippage_model.yaml",
    "scoring_weights.yaml",
    "scan_webhook_map.yaml",
    "chartink_scanners.yaml",
    f"nse_holidays_{_date.today().year}.yaml",
]


def check_config_files(config_dir: Path) -> list[str]:
    """Return list of missing config files."""
    missing = []
    for f in CONFIG_FILES:
        if not (config_dir / f).exists():
            missing.append(f)
    return missing


def check_secrets() -> list[str]:
    """Return list of missing required secrets."""
    missing = []
    for secret in REQUIRED_SECRETS:
        if not os.environ.get(secret):
            missing.append(secret)
    return missing


def check_database(db_path: Path) -> tuple[bool, str]:
    """Check database accessible and schema version matches."""
    if not db_path.exists():
        return False, f"Database not found: {db_path}"

    try:
        from core.state_store import StateStore, EXPECTED_SCHEMA_VERSION

        store = StateStore(db_path)
        version = store.get_schema_version()
        store.close()

        if version != EXPECTED_SCHEMA_VERSION:
            return False, f"Schema version mismatch: DB={version}, code={EXPECTED_SCHEMA_VERSION}"

        return True, f"Schema v{version} OK"
    except Exception as e:
        return False, f"Database error: {e}"


def check_disk_space(min_gb: float = 2.0) -> tuple[bool, str]:
    """Check free disk space is adequate."""
    try:
        import shutil
        total, used, free = shutil.disk_usage(Path.cwd())
        free_gb = free / (1024 ** 3)

        if free_gb < min_gb:
            return False, f"Low disk space: {free_gb:.1f}GB free (need {min_gb}GB)"

        return True, f"Disk OK: {free_gb:.1f}GB free"
    except Exception as e:
        return False, f"Disk check error: {e}"


def check_clock_skew() -> tuple[bool, str]:
    """Check clock is synchronized with NTP."""
    try:
        import ntplib
        from datetime import datetime, timezone

        client = ntplib.NTPClient()
        response = client.request("pool.ntp.org", version=3, timeout=5)

        ntp_time = datetime.fromtimestamp(response.tx_time, tz=timezone.utc)
        local_time = datetime.now(tz=timezone.utc)
        skew_sec = abs((local_time - ntp_time).total_seconds())

        if skew_sec > 30:
            return False, f"Clock skew too large: {skew_sec:.1f}s"

        return True, f"Clock OK: skew={skew_sec:.2f}s"
    except ImportError:
        return True, "Clock check skipped (ntplib not installed)"
    except Exception as e:
        return True, f"Clock check skipped: {e}"


def run_healthcheck(
    config_dir: Path,
    db_path: Path,
    dry_run: bool = False,
) -> tuple[bool, list[str]]:
    """
    Run all pre-market health checks.

    Returns:
        (all_ok, list_of_issues)
    """
    issues = []

    # 1. Config files
    missing_config = check_config_files(config_dir)
    if missing_config:
        issues.append(f"Missing config: {', '.join(missing_config)}")

    # 2. Required secrets
    missing_secrets = check_secrets()
    if missing_secrets:
        issues.append(f"Missing secrets: {', '.join(missing_secrets)}")

    # 3. Database
    db_ok, db_msg = check_database(db_path)
    if not db_ok:
        issues.append(db_msg)

    # 4. Disk space
    disk_ok, disk_msg = check_disk_space()
    if not disk_ok:
        issues.append(disk_msg)

    # 5. Clock skew
    clock_ok, clock_msg = check_clock_skew()
    if not clock_ok:
        issues.append(clock_msg)

    return len(issues) == 0, issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-market health check")
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    parser.add_argument("--db-path", type=Path, default=Path("data_store/trading_system.db"))
    parser.add_argument("--dry-run", action="store_true", help="Don't send Telegram alert")
    args = parser.parse_args()

    ts = now_ist().isoformat()
    print(f"{ts} Pre-market healthcheck starting...")

    all_ok, issues = run_healthcheck(
        config_dir=args.config_dir,
        db_path=args.db_path,
        dry_run=args.dry_run,
    )

    if all_ok:
        msg = f"{ts} PRE-MARKET HEALTHCHECK: All OK"
        print(msg)
        _log.info("premarket_healthcheck.ok")

        # Record heartbeat
        try:
            from utils.cron_heartbeat import record_heartbeat
            record_heartbeat("premarket_healthcheck")
        except Exception:
            pass

        return 0

    # Build alert
    alert_lines = ["PRE-MARKET HEALTHCHECK FAILED\n"]
    for issue in issues:
        alert_lines.append(f"  - {issue}")
    alert_msg = "\n".join(alert_lines)

    print(alert_msg)
    _log.warning("premarket_healthcheck.failed", extra={"issues": issues})

    if args.dry_run:
        print("\n[DRY-RUN] Would send Telegram alert")
        return 1

    # Send Telegram alert
    try:
        from alerts.telegram_notifier import TelegramNotifier

        notifier = TelegramNotifier.from_env()
        if notifier is None:
            raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL_PRIMARY not set")
        notifier.send_alert(alert_msg, level="CRITICAL")
        print("Telegram alert sent")
    except Exception as e:
        _log.error("premarket_healthcheck.telegram_failed", extra={"error": str(e)})
        print(f"Failed to send Telegram: {e}")

    return 1


if __name__ == "__main__":
    sys.exit(main())
