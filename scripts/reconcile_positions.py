"""
scripts/reconcile_positions.py -- Trading System v2  FIX-134 Item 31

Purpose:
    Daily EOD script that compares broker open positions (symbol-by-symbol)
    against system open trades and detects orphans (at broker but not system)
    and missing positions (in system but not at broker).

    Run by cron at 15:45 IST -- after market close, before EOD report.

Decision rules:
    Both match        : status=OK
    At broker only    : status=ORPHAN_AT_BROKER, CRITICAL alert
    In system only    : status=MISSING_AT_BROKER, CRITICAL alert

Paper mode:
    Broker fetch skipped. Compares internal state only (all trades -> OK).

Exit codes:
    0 -- all positions match or paper mode
    1 -- error during execution
    2 -- orphan or missing position detected
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from alerts.telegram_notifier import TelegramNotifier
from core.config_loader import load_all as load_config
from core.logger import get_logger
from core.market_windows import is_broker_api_available
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="reconcile_positions",
        description="FIX-134: EOD broker vs system position reconciliation.",
    )
    parser.add_argument(
        "--config", metavar="PATH", default="config",
        help="Path to config directory (default: config/)",
    )
    parser.add_argument(
        "--db", metavar="PATH", default=None,
        help="Path to SQLite DB (default: data_store/trading_system.db)",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD", default=None,
        help="Date to reconcile (default: today IST)",
    )
    parser.add_argument(
        "--mode", choices=["paper", "live"], default=None,
        help="Force paper/live mode (default: from TRADING_MODE env var)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute and log but do not write to DB.",
    )
    return parser.parse_args(argv)


def _fetch_broker_positions(log: logging.Logger) -> dict[str, int]:
    """
    Fetch net open positions from Zerodha. Returns {symbol: net_qty}.
    Only includes positions with non-zero quantity.
    """
    from kiteconnect import KiteConnect

    api_key = os.environ.get("ZERODHA_API_KEY", "")
    access_token = os.environ.get("ZERODHA_ACCESS_TOKEN", "")

    if not api_key or not access_token:
        raise RuntimeError(
            "ZERODHA_API_KEY and ZERODHA_ACCESS_TOKEN must be set for live position fetch"
        )

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    try:
        raw_positions = kite.positions()
    except Exception as exc:
        raise RuntimeError(f"Kite positions() failed: {exc}") from exc

    net_positions = raw_positions.get("net", []) if isinstance(raw_positions, dict) else []
    result: dict[str, int] = {}
    for pos in net_positions:
        sym = pos.get("tradingsymbol", "")
        qty = int(pos.get("quantity", 0) or 0)
        if sym and qty != 0:
            result[sym] = qty
    log.info(
        "reconcile_positions.broker_fetched",
        extra={"position_count": len(result)},
    )
    return result


def _get_system_positions(store: StateStore, date_iso: str) -> dict[str, int]:
    """
    Get open trades from DB for today. Returns {symbol: qty_filled}.
    Only OPEN/PARTIAL trades with qty_filled > 0.
    """
    rows = store.fetch_all(
        """
        SELECT symbol, qty_filled, direction
        FROM trades
        WHERE status IN ('OPEN', 'PARTIAL')
          AND SUBSTR(created_at, 1, 10) <= ?
          AND qty_filled > 0
        """,
        (date_iso,),
    )
    result: dict[str, int] = {}
    for row in rows:
        sym = row["symbol"]
        qty = int(row["qty_filled"])
        direction = row["direction"]
        signed_qty = qty if direction == "LONG" else -qty
        result[sym] = result.get(sym, 0) + signed_qty
    return result


def run_position_reconciliation(
    *,
    store: StateStore,
    date_iso: str,
    is_paper: bool,
    log: logging.Logger,
    notifier=None,
    mode_label: str = "LIVE",
    dry_run: bool = False,
) -> list[dict]:
    """
    Compare broker vs system positions symbol-by-symbol.
    Returns list of result dicts with keys: date, symbol, broker_qty, system_qty, status.
    """
    now_str = now_ist().isoformat()

    system_positions = _get_system_positions(store, date_iso)
    log.info(
        "reconcile_positions.system_positions",
        extra={"date": date_iso, "count": len(system_positions)},
    )

    if is_paper:
        results = []
        for sym, qty in system_positions.items():
            results.append({
                "date": date_iso,
                "symbol": sym,
                "broker_qty": qty,
                "system_qty": qty,
                "status": "OK",
            })
        if not dry_run:
            for r in results:
                _insert_position_reconciliation(store, r, now_str)
        log.info(
            "reconcile_positions.paper_mode",
            extra={"position_count": len(results)},
        )
        return results

    try:
        broker_positions = _fetch_broker_positions(log)
    except Exception as exc:
        log.error("reconcile_positions.broker_fetch_failed: %s", exc)
        return [{
            "date": date_iso,
            "symbol": "ALL",
            "broker_qty": None,
            "system_qty": len(system_positions),
            "status": "ERROR",
        }]

    all_symbols = set(broker_positions.keys()) | set(system_positions.keys())
    results = []
    has_mismatch = False

    for sym in sorted(all_symbols):
        broker_qty = broker_positions.get(sym, 0)
        system_qty = system_positions.get(sym, 0)

        if broker_qty != 0 and system_qty == 0:
            status = "ORPHAN_AT_BROKER"
            has_mismatch = True
        elif broker_qty == 0 and system_qty != 0:
            status = "MISSING_AT_BROKER"
            has_mismatch = True
        elif broker_qty != system_qty:
            status = "QTY_MISMATCH"
            has_mismatch = True
        else:
            status = "OK"

        r = {
            "date": date_iso,
            "symbol": sym,
            "broker_qty": broker_qty,
            "system_qty": system_qty,
            "status": status,
        }
        results.append(r)

        if status != "OK":
            log.critical(
                "reconcile_positions.mismatch",
                extra={
                    "symbol": sym,
                    "broker_qty": broker_qty,
                    "system_qty": system_qty,
                    "status": status,
                },
            )

    if not dry_run:
        for r in results:
            _insert_position_reconciliation(store, r, now_str)

    if has_mismatch and notifier is not None:
        mismatches = [r for r in results if r["status"] != "OK"]
        body_lines = [f"Date: {date_iso}", ""]
        for m in mismatches:
            body_lines.append(
                f"  {m['symbol']}: broker={m['broker_qty']} system={m['system_qty']} -> {m['status']}"
            )
        try:
            notifier.send(
                severity="ERROR",
                title=f"[{mode_label}] POSITION RECONCILIATION MISMATCH",
                body="\n".join(body_lines),
                source_module="reconcile_positions",
            )
        except Exception as ne:
            log.error("reconcile_positions.notifier_failed: %s", ne)

    log.info(
        "reconcile_positions.complete",
        extra={
            "total": len(results),
            "mismatches": sum(1 for r in results if r["status"] != "OK"),
        },
    )
    return results


def _insert_position_reconciliation(
    store: StateStore, result: dict, now_str: str
) -> None:
    with store.transaction() as cur:
        cur.execute(
            """
            INSERT INTO position_reconciliation
              (date, symbol, broker_qty, system_qty, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result["date"],
                result["symbol"],
                result.get("broker_qty"),
                result.get("system_qty"),
                result["status"],
                now_str,
            ),
        )


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("reconcile_positions")

    mode = args.mode or os.environ.get("TRADING_MODE", "live").lower()
    is_paper = (mode == "paper")
    mode_label = "PAPER" if is_paper else "LIVE"

    # FIX-180 Part 12: a live current-day reconcile fetches broker positions.
    # Skip on weekend / Friday-after-17:30 when the Zerodha API is unavailable.
    # Paper mode (no broker fetch) and explicit --date backfills are unaffected.
    # FIX-181: pass the holiday set so the cutoff prepones to Thursday when
    # Friday is an NSE holiday.
    from utils.holiday_guard import current_holiday_set
    if (not is_paper and not args.date and not is_broker_api_available(
            holidays=current_holiday_set(Path(args.config)))):
        log.info("reconcile_positions.skipped_api_unavailable (weekend/after-hours)")
        return 0

    config_dir = Path(args.config)
    try:
        load_config(config_dir)
    except Exception as exc:
        log.error("reconcile_positions: config load failed: %s", exc)
        return 1

    db_path = Path(args.db) if args.db else _ROOT / "data_store" / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("reconcile_positions: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()

    # Bug 10 (FIX-180): build the Telegram notifier from env so EOD position
    # mismatches actually alert. from_env() returns None if the token/chat env
    # vars are missing — log a WARNING but do not crash (run-without-alerts).
    notifier = TelegramNotifier.from_env(log)
    if notifier is None:
        log.warning(
            "reconcile_positions: Telegram env not set "
            "(TELEGRAM_BOT_TOKEN/TELEGRAM_CHANNEL_PRIMARY); mismatch alerts disabled"
        )

    log.info(
        "reconcile_positions.start",
        extra={"date": date_iso, "mode": mode_label, "dry_run": args.dry_run},
    )

    try:
        results = run_position_reconciliation(
            store=store,
            date_iso=date_iso,
            is_paper=is_paper,
            log=log,
            notifier=notifier,
            mode_label=mode_label,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        log.error("reconcile_positions.unexpected_error: %s", exc, exc_info=True)
        store.close()
        return 1

    store.close()

    has_mismatch = any(r["status"] not in ("OK", "ERROR") for r in results)
    has_error = any(r["status"] == "ERROR" for r in results)
    if has_mismatch:
        return 2
    if has_error:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
