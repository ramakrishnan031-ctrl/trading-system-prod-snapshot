"""
scripts/eod_cleanup.py -- Trading System v2  FIX-135 Item 48

Purpose:
    End-of-day cleanup of session artifacts:
      1. Mark stale IN_PROCESS signals as EXPIRED
      2. Mark stale OPEN/SUBMITTED/PENDING/TRIGGER_PENDING orders as CANCELLED
      3. Delete orphaned smart_tgt_state rows
      4. Prune old signal fingerprints (>7 days)

    Run at 15:50 IST (after square-off, before EOD report).

Exit codes:
    0 -- success
    1 -- error during execution
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="eod_cleanup",
        description="FIX-135: EOD session artifact cleanup.",
    )
    parser.add_argument("--db", metavar="PATH", default=None)
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fingerprint-days", type=int, default=7)
    return parser.parse_args(argv)


def run_eod_cleanup(
    *,
    store: StateStore,
    date_iso: str,
    log: logging.Logger,
    dry_run: bool = False,
    fingerprint_retention_days: int = 7,
) -> dict[str, int]:
    """
    Run all EOD cleanup actions. Returns counts of each action.
    """
    results = {}

    # 1. Mark stale signals
    stale_signals = _cleanup_stale_signals(store, date_iso, log, dry_run)
    results["stale_signals_expired"] = stale_signals

    # 2. Mark stale orders
    stale_orders = _cleanup_stale_orders(store, date_iso, log, dry_run)
    results["stale_orders_cancelled"] = stale_orders

    # 3. Orphaned smart_tgt_state
    orphaned_tgt = _cleanup_orphaned_smart_tgt(store, log, dry_run)
    results["orphaned_smart_tgt_deleted"] = orphaned_tgt

    # 4. Prune old fingerprints
    pruned_fp = _cleanup_old_fingerprints(
        store, date_iso, fingerprint_retention_days, log, dry_run
    )
    results["fingerprints_pruned"] = pruned_fp

    log.info("eod_cleanup.complete", extra=results)
    return results


def _cleanup_stale_signals(
    store: StateStore, date_iso: str, log: logging.Logger, dry_run: bool
) -> int:
    if dry_run:
        row = store.fetch_one(
            "SELECT COUNT(*) AS n FROM signals WHERE status = 'IN_PROCESS' AND SUBSTR(triggered_at, 1, 10) < ?",
            (date_iso,),
        )
        count = int(row["n"]) if row else 0
        log.info("eod_cleanup.stale_signals: %d (dry-run)", count)
        return count

    with store.transaction() as cur:
        cur.execute(
            "UPDATE signals SET status = 'EXPIRED' WHERE status = 'IN_PROCESS' AND SUBSTR(triggered_at, 1, 10) < ?",
            (date_iso,),
        )
        count = cur.rowcount
    log.info("eod_cleanup.stale_signals_expired: %d", count)
    return count


def _cleanup_stale_orders(
    store: StateStore, date_iso: str, log: logging.Logger, dry_run: bool
) -> int:
    active_statuses = ("OPEN", "SUBMITTED", "PENDING", "TRIGGER_PENDING")
    closed_trade_statuses = ("CLOSED", "CLOSED_MANUAL", "CANCELLED", "FAILED")

    if dry_run:
        row = store.fetch_one(
            "SELECT COUNT(*) AS n FROM orders WHERE status IN ('OPEN','SUBMITTED','PENDING','TRIGGER_PENDING') AND SUBSTR(placed_at, 1, 10) < ?",
            (date_iso,),
        )
        count = int(row["n"]) if row else 0
        log.info("eod_cleanup.stale_orders: %d (dry-run)", count)
        return count

    total = 0
    with store.transaction() as cur:
        # Cancel orders from prior days whose trades are closed/cancelled
        cur.execute(
            """UPDATE orders SET status = 'CANCELLED', updated_at = datetime('now','localtime')
               WHERE status IN ('OPEN','SUBMITTED','PENDING','TRIGGER_PENDING')
               AND SUBSTR(placed_at, 1, 10) < ?
               AND trade_id IN (
                   SELECT trade_id FROM trades
                   WHERE status IN ('CLOSED','CLOSED_MANUAL','CANCELLED','FAILED')
               )""",
            (date_iso,),
        )
        total += cur.rowcount
        # Cancel prior-day PENDING orders with no matching trade (orphans)
        cur.execute(
            """UPDATE orders SET status = 'CANCELLED', updated_at = datetime('now','localtime')
               WHERE status IN ('OPEN','SUBMITTED','PENDING','TRIGGER_PENDING')
               AND SUBSTR(placed_at, 1, 10) < ?
               AND trade_id NOT IN (SELECT trade_id FROM trades)""",
            (date_iso,),
        )
        total += cur.rowcount
    log.info("eod_cleanup.stale_orders_cancelled: %d", total)
    return total


def _cleanup_orphaned_smart_tgt(
    store: StateStore, log: logging.Logger, dry_run: bool
) -> int:
    if dry_run:
        row = store.fetch_one(
            "SELECT COUNT(*) AS n FROM smart_tgt_state WHERE trade_id NOT IN (SELECT trade_id FROM trades WHERE status IN ('OPEN', 'PARTIAL'))",
        )
        count = int(row["n"]) if row else 0
        log.info("eod_cleanup.orphaned_smart_tgt: %d (dry-run)", count)
        return count

    with store.transaction() as cur:
        cur.execute(
            "DELETE FROM smart_tgt_state WHERE trade_id NOT IN (SELECT trade_id FROM trades WHERE status IN ('OPEN', 'PARTIAL'))",
        )
        count = cur.rowcount
    log.info("eod_cleanup.orphaned_smart_tgt_deleted: %d", count)
    return count


def _cleanup_old_fingerprints(
    store: StateStore, date_iso: str, retention_days: int,
    log: logging.Logger, dry_run: bool,
) -> int:
    cutoff = (
        datetime.strptime(date_iso, "%Y-%m-%d") - timedelta(days=retention_days)
    ).strftime("%Y-%m-%d")

    if dry_run:
        row = store.fetch_one(
            "SELECT COUNT(*) AS n FROM signals WHERE fingerprint_date < ?",
            (cutoff,),
        )
        count = int(row["n"]) if row else 0
        log.info("eod_cleanup.old_fingerprints: %d (dry-run, cutoff=%s)", count, cutoff)
        return count

    with store.transaction() as cur:
        cur.execute(
            "DELETE FROM signals WHERE status IN ('EXPIRED', 'DUPLICATE', 'REJECTED') AND fingerprint_date < ?",
            (cutoff,),
        )
        count = cur.rowcount
    log.info("eod_cleanup.fingerprints_pruned: %d (cutoff=%s)", count, cutoff)
    return count


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("eod_cleanup")

    db_path = Path(args.db) if args.db else _ROOT / "data_store" / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("eod_cleanup: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()
    log.info("eod_cleanup.start", extra={"date": date_iso, "dry_run": args.dry_run})

    try:
        run_eod_cleanup(
            store=store,
            date_iso=date_iso,
            log=log,
            dry_run=args.dry_run,
            fingerprint_retention_days=args.fingerprint_days,
        )
    except Exception as exc:
        log.error("eod_cleanup.unexpected_error: %s", exc, exc_info=True)
        store.close()
        return 1

    store.close()
    return 0


def _cron_main(argv=None) -> int:
    """Cron entry: holiday-skip + heartbeat + per-job alert (TASK #3)."""
    from utils.cron_heartbeat import HeartbeatTimer, skip_if_non_trading_day

    if skip_if_non_trading_day("eod_cleanup"):
        return 0
    timer = HeartbeatTimer("eod_cleanup", alert=True)
    with timer:
        rc = main(argv)
        rc = 0 if rc is None else rc
        if rc != 0:
            timer.status = "FAILED"
            timer.message = f"exit code {rc}"
    return rc


if __name__ == "__main__":
    sys.exit(_cron_main())
