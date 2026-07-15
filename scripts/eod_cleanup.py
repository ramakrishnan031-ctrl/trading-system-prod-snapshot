"""
scripts/eod_cleanup.py -- Trading System v2  FIX-135 Item 48

Purpose:
    End-of-day cleanup of session artifacts:
      1. Mark stale PROCESSING signals as EXPIRED
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
    # Window for the step-4 signal-fingerprint prune. Default None => read from config
    # (system.eod_cleanup.signal_retention_days, default 90). An explicit value OVERRIDES
    # config — used for a one-time lower-window backlog clear (Phase B) and by tests.
    # `--fingerprint-days` kept as a backward-compat alias (same dest).
    parser.add_argument("--signal-retention-days", "--fingerprint-days",
                        dest="signal_retention_days", type=int, default=None)
    return parser.parse_args(argv)


def _resolve_signal_retention_days(cli_override, log: logging.Logger) -> int:
    """Resolve the prune window: an explicit CLI value wins (one-time backlog clear / tests);
    else read `system.eod_cleanup.signal_retention_days` (default 90). A config-load failure
    falls back to the SAME model default — never a bare literal — so a hygiene cron can't crash
    on a config error and the window is always config-sourced."""
    if cli_override is not None:
        return int(cli_override)
    try:
        from core.config_loader import load_all
        return load_all().system.eod_cleanup.signal_retention_days
    except Exception as exc:  # noqa: BLE001 — config must never crash a hygiene cron
        from core.config_loader import EodCleanupConfig
        fallback = EodCleanupConfig().signal_retention_days
        log.warning("eod_cleanup: config load failed (%s); using default retention=%dd",
                    exc, fallback)
        return fallback


def run_eod_cleanup(
    *,
    store: StateStore,
    date_iso: str,
    log: logging.Logger,
    dry_run: bool = False,
    signal_retention_days: int = 90,
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

    # 4. Prune old fingerprints (children-first, FK-safe, batched; capital-guarded)
    pruned_fp = _cleanup_old_fingerprints(
        store, date_iso, signal_retention_days, log, dry_run
    )
    results["fingerprints_pruned"] = pruned_fp

    log.info("eod_cleanup.complete", extra=results)
    return results


def _cleanup_stale_signals(
    store: StateStore, date_iso: str, log: logging.Logger, dry_run: bool
) -> int:
    if dry_run:
        row = store.fetch_one(
            # M-SC3 (audit 04-Jul): reaper filtered 'IN_PROCESS', a status the pipeline NEVER
            # persists (signal_processor sets 'PROCESSING' at signals.status) → dead code, stuck
            # signals never cleaned. Filter the real in-flight status.
            "SELECT COUNT(*) AS n FROM signals WHERE status = 'PROCESSING' AND SUBSTR(triggered_at, 1, 10) < ?",
            (date_iso,),
        )
        count = int(row["n"]) if row else 0
        log.info("eod_cleanup.stale_signals: %d (dry-run)", count)
        return count

    with store.transaction() as cur:
        cur.execute(
            "UPDATE signals SET status = 'EXPIRED' WHERE status = 'PROCESSING' AND SUBSTR(triggered_at, 1, 10) < ?",
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


# Analytics/shadow children of signals(signal_id) that the prune DROPS alongside the
# parent noise signal. `trades` is DELIBERATELY ABSENT — a trade-linked signal is never
# in the prune set (the NOT EXISTS guard below), so capital/P&L history is never touched.
# Full FK graph (core/schema.sql): signals(signal_id) parents seven children — these five
# plus `trades` (protected) and ... verified: trades, screener_results, gate_state,
# shadow_trades, sr_detector_results, retest_state.
_SIGNAL_ANALYTICS_CHILDREN = (
    "screener_results",
    "gate_state",
    "shadow_trades",
    "sr_detector_results",
    "retest_state",
)

# signal_ids deleted per transaction. Bounds transaction size + rollback blast radius so a
# large backlog (~108k) clears in COMMITted chunks instead of one giant all-or-nothing tx.
_PRUNE_BATCH = 2000


def _cleanup_old_fingerprints(
    store: StateStore, date_iso: str, signal_retention_days: int,
    log: logging.Logger, dry_run: bool,
) -> int:
    cutoff = (
        datetime.strptime(date_iso, "%Y-%m-%d") - timedelta(days=signal_retention_days)
    ).strftime("%Y-%m-%d")

    # Prune old terminal NOISE fingerprints (expired / duplicate / any reject) so the
    # signals table + its dedup index do not grow unbounded. The trade audit trail
    # (QUEUED->...->TRADED / PLACEMENT_FAILED / PROCESSED) is KEPT by the status filter.
    #
    # CAPITAL-SAFETY INVARIANT (primary guard): a hygiene job must NEVER delete a signal
    # linked to capital/P&L history. `NOT EXISTS (trades)` excludes any signal with a
    # `trades` child regardless of status — belt-and-suspenders beyond the status filter.
    #
    # FK-SAFETY (root cause of the 15:50 rollback): P10 (2e61fad) broadened the filter to
    # GLOB 'REJECTED*'. Those signals are FK-referenced by analytics children
    # (screener_results et al.) and foreign_keys=ON, so a bare `DELETE FROM signals` rolls
    # back the whole statement. We DROP the analytics children first, THEN the parent, in
    # COMMITted batches (children-first cascade; beyond-window = DROP, no archive).
    #
    # The dry-run counts with the SAME predicate as the DELETE (preview == action).
    where = ("(status IN ('EXPIRED', 'DUPLICATE') OR status GLOB 'REJECTED*') "
             "AND fingerprint_date < ? "
             "AND NOT EXISTS (SELECT 1 FROM trades t WHERE t.signal_id = signals.signal_id)")

    if dry_run:
        row = store.fetch_one(
            f"SELECT COUNT(*) AS n FROM signals WHERE {where}",
            (cutoff,),
        )
        count = int(row["n"]) if row else 0
        log.info("eod_cleanup.old_fingerprints: %d (dry-run, cutoff=%s, retention=%dd)",
                 count, cutoff, signal_retention_days)
        return count

    total = 0
    while True:
        with store.transaction() as cur:
            rows = cur.execute(
                f"SELECT signal_id FROM signals WHERE {where} LIMIT ?",
                (cutoff, _PRUNE_BATCH),
            ).fetchall()
            ids = [r[0] for r in rows]
            if not ids:
                break
            placeholders = ",".join("?" * len(ids))
            # children-first (FK-safe), then the parent signals — one COMMIT per batch
            for child in _SIGNAL_ANALYTICS_CHILDREN:
                cur.execute(
                    f"DELETE FROM {child} WHERE signal_id IN ({placeholders})", ids
                )
            cur.execute(
                f"DELETE FROM signals WHERE signal_id IN ({placeholders})", ids
            )
            total += len(ids)
        # transaction COMMITs on context exit; loop re-selects the next batch until empty
    log.info("eod_cleanup.fingerprints_pruned: %d (cutoff=%s, retention=%dd)",
             total, cutoff, signal_retention_days)
    return total


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
    retention_days = _resolve_signal_retention_days(args.signal_retention_days, log)
    log.info("eod_cleanup.start", extra={"date": date_iso, "dry_run": args.dry_run,
                                         "signal_retention_days": retention_days})

    try:
        run_eod_cleanup(
            store=store,
            date_iso=date_iso,
            log=log,
            dry_run=args.dry_run,
            signal_retention_days=retention_days,
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
