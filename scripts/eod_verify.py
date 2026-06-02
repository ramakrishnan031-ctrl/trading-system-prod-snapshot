"""
scripts/eod_verify.py -- Trading System v2  FIX-137 Item 59

Purpose:
    End-of-day verification: confirms all positions closed, orders settled,
    capital reconciled. Runs at 15:55 IST after cleanup, before report.

    Writes verification record to eod_verification table.
    Sends Telegram summary: VERIFIED or ISSUES_FOUND.

Exit codes:
    0 -- verified (or issues found but recorded)
    1 -- script error
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist


def run_eod_verification(
    store: StateStore,
    date_iso: str,
    log: logging.Logger | None = None,
) -> dict:
    """
    Run all EOD verification checks and persist result.

    Returns dict with keys: date, open_trades, pending_orders, pnl_variance, status
    """
    if log is None:
        log = logging.getLogger("eod_verify")

    open_trades = store.fetch_one(
        "SELECT COUNT(*) AS n FROM trades WHERE status IN ('OPEN', 'PARTIAL') "
        "AND substr(created_at, 1, 10) = ?",
        (date_iso,),
    )
    open_count = open_trades["n"] if open_trades else 0

    pending_orders = store.fetch_one(
        "SELECT COUNT(*) AS n FROM orders WHERE status = 'PENDING' "
        "AND substr(placed_at, 1, 10) = ?",
        (date_iso,),
    )
    pending_count = pending_orders["n"] if pending_orders else 0

    pnl_variance = 0.0
    try:
        recon_row = store.fetch_one(
            "SELECT ABS(system_net_pnl - broker_net_pnl) AS variance "
            "FROM pnl_reconciliation WHERE date = ? ORDER BY rowid DESC LIMIT 1",
            (date_iso,),
        )
        if recon_row and recon_row["variance"] is not None:
            pnl_variance = float(recon_row["variance"])
    except Exception:
        pass

    issues = []
    if open_count > 0:
        issues.append(f"{open_count} positions still OPEN")
        log.critical("eod_verify: %d positions still OPEN for %s", open_count, date_iso)
    if pending_count > 0:
        issues.append(f"{pending_count} orders still PENDING")
        log.critical("eod_verify: %d orders still PENDING for %s", pending_count, date_iso)
    if pnl_variance > 100.0:
        issues.append(f"P&L variance Rs{pnl_variance:.2f}")
        log.warning("eod_verify: P&L variance Rs%.2f for %s", pnl_variance, date_iso)

    status = "ISSUES_FOUND" if issues else "VERIFIED"
    verified_at = now_ist().isoformat()

    with store.transaction() as cur:
        cur.execute(
            "INSERT OR REPLACE INTO eod_verification "
            "(date, open_trades, pending_orders, pnl_variance, status, verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (date_iso, open_count, pending_count, pnl_variance, status, verified_at),
        )

    log.info(
        "eod_verify.complete",
        extra={"date": date_iso, "status": status, "open": open_count, "pending": pending_count},
    )

    return {
        "date": date_iso,
        "open_trades": open_count,
        "pending_orders": pending_count,
        "pnl_variance": pnl_variance,
        "status": status,
        "issues": issues,
    }


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(prog="eod_verify")
    parser.add_argument("--date", metavar="YYYY-MM-DD", default=None)
    parser.add_argument("--db", metavar="PATH", default=None)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("eod_verify")

    db_path = Path(args.db) if args.db else Path("data_store") / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("eod_verify: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()
    result = run_eod_verification(store, date_iso, log)

    if result["status"] == "VERIFIED":
        summary = f"EOD VERIFIED: {date_iso} -- all clear"
    else:
        summary = f"EOD ISSUES FOUND: {date_iso} -- {'; '.join(result['issues'])}"

    print(summary)

    try:
        from alerts.telegram_notifier import TelegramNotifier
        notifier = TelegramNotifier.from_env()
        if notifier:
            notifier.send_info(summary)
    except Exception as exc:
        log.warning("eod_verify: telegram alert failed: %s", exc)

    # FIX-145: Record heartbeat for cron drift monitoring
    try:
        from utils.cron_heartbeat import record_heartbeat
        record_heartbeat("eod_verify")
    except Exception:
        pass

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
