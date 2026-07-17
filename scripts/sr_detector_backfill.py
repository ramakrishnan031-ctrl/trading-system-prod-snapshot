"""
scripts/sr_detector_backfill.py — Trading System v2 · S&R Detector V1

Purpose:
    EOD OUTCOME BACKFILL (spec I) — separate from the live observer. Joins each
    day's sr_detector_results rows (by signal_id) to the actual trade record and
    fills actual_fill / actual_result / win_loss / pnl. Only TERMINAL trades are
    finalised; a still-open trade is left NULL so a later run picks it up. The
    hypothetical_retest_result (simulating the retest from post-signal candles) is
    a LATER follow-on and is intentionally left NULL here.

Usage:
    python scripts/sr_detector_backfill.py [YYYY-MM-DD]     # default: today (IST)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data_store" / "trading_system.db"

# Trade statuses that are terminal for outcome purposes.
_TERMINAL = {"CLOSED", "CLOSED_MANUAL", "CANCELLED", "FAILED"}


def _is_terminal(status: str) -> bool:
    return bool(status) and (status in _TERMINAL or status.startswith("REJECTED"))


def _win_loss(net_pnl) -> str:
    if net_pnl is None:
        return "UNKNOWN"
    if net_pnl > 0:
        return "WIN"
    if net_pnl < 0:
        return "LOSS"
    return "FLAT"


def backfill(store, date_iso: str) -> dict:
    """
    Backfill outcomes for date_iso. Returns a small stats dict. Pure w.r.t. the
    store (testable with an in-memory StateStore).
    """
    rows = store.get_sr_results_for_backfill(date_iso)
    stats = {"considered": len(rows), "filled": 0, "skipped_open": 0, "no_trade": 0}

    for r in rows:
        sr_id = r["id"]
        signal_id = r["signal_id"]
        trade = store.fetch_one(
            "SELECT entry_actual_price, exit_reason, net_pnl, status "
            "FROM trades WHERE signal_id = ? ORDER BY created_at DESC LIMIT 1",
            (signal_id,),
        )

        if trade is None:
            # Observer fires post-placement, so by EOD a missing trade is terminal.
            store.update_sr_outcome(
                sr_id, actual_fill=None, actual_result="NO_TRADE",
                win_loss="NO_FILL", pnl=None,
            )
            stats["no_trade"] += 1
            continue

        status = trade["status"]
        if not _is_terminal(status):
            stats["skipped_open"] += 1
            continue

        if status == "CLOSED" or status == "CLOSED_MANUAL":
            actual_result = trade["exit_reason"] or status
            win_loss = _win_loss(trade["net_pnl"])
            pnl = trade["net_pnl"]
        else:  # CANCELLED / FAILED / REJECTED*
            actual_result = status
            win_loss = "NO_FILL"
            pnl = trade["net_pnl"]

        store.update_sr_outcome(
            sr_id,
            actual_fill=trade["entry_actual_price"],
            actual_result=actual_result,
            win_loss=win_loss,
            pnl=pnl,
        )
        stats["filled"] += 1

    return stats


def main(argv=None) -> int:
    import argparse
    from datetime import datetime

    parser = argparse.ArgumentParser(prog="sr_detector_backfill")
    parser.add_argument("date", nargs="?", default=None, help="YYYY-MM-DD (default: today IST)")
    parser.add_argument("--db", default=str(DB_PATH), help="DB path")
    args = parser.parse_args(argv)

    if args.date:
        date_iso = args.date
    else:
        try:
            from core.time_authority import now_ist
            date_iso = now_ist().date().isoformat()
        except Exception:
            date_iso = datetime.now().date().isoformat()

    from core.state_store import StateStore
    store = StateStore(Path(args.db))
    try:
        stats = backfill(store, date_iso)
    finally:
        try:
            store.close()
        except Exception:
            pass
    print(
        f"sr_detector_backfill {date_iso}: considered={stats['considered']} "
        f"filled={stats['filled']} skipped_open={stats['skipped_open']} "
        f"no_trade={stats['no_trade']}"
    )
    return 0


def _cron_main(argv=None) -> int:
    """Cron entry: S1 holiday-skip, then the real job.

    S1 (2026-07-17): the registry declares this job market_day_only + cadence
    market_day, but NOTHING enforced it at the cron entry — `cadence` only tells
    the Cron Officer not to EXPECT a heartbeat on a holiday; cron still fired the
    job. skip_if_non_trading_day FAILS OPEN (weekday fallback on any calendar
    error) so a trading day is never skipped. The guard is here and not in main()
    so a manual/ad-hoc run on a non-trading day is never blocked.
    """
    from utils.cron_heartbeat import skip_if_non_trading_day

    if skip_if_non_trading_day("sr_detector_backfill"):
        return 0
    return main(argv)


if __name__ == "__main__":
    sys.exit(_cron_main())
