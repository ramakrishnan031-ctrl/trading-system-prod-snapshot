"""
scripts/compute_strategy_metrics.py -- Trading System v2  FIX-134 Item 36

Purpose:
    EOD script that computes per-strategy performance metrics (Sharpe ratio,
    win rate, average P&L) and stores them in the strategy_metrics table.

    If Sharpe < -0.3 for 3 consecutive days, auto-sets strategy to PAPER_ONLY
    and sends Telegram CRITICAL alert.

    Run by cron at 16:15 IST -- after EOD report and reconciliation.

Exit codes:
    0 -- success
    1 -- error during execution
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.config_loader import load_all as load_config
from core.logger import get_logger
from core.state_store import StateStore
from core.time_authority import now_ist, today_ist

_SHARPE_DEMOTION_THRESHOLD = -0.3
_CONSECUTIVE_BAD_DAYS = 3
_ANNUALIZATION_FACTOR = math.sqrt(252)


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="compute_strategy_metrics",
        description="FIX-134: EOD per-strategy Sharpe ratio computation.",
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
        help="Date to compute for (default: today IST)",
    )
    parser.add_argument(
        "--lookback", metavar="N", type=int, default=30,
        help="Days of history for Sharpe (default: 30)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute and log but do not write to DB.",
    )
    return parser.parse_args(argv)


def compute_sharpe(
    store: StateStore,
    strategy_name: str,
    as_of_date: str,
    lookback_days: int = 30,
) -> Optional[float]:
    """
    Compute annualized Sharpe ratio for a strategy.

    Fetches daily net_pnl sums for the strategy over the lookback window.
    Sharpe = mean(daily_pnl) / std(daily_pnl) * sqrt(252)

    Returns None if fewer than 2 trading days of data.
    """
    from datetime import datetime, timedelta
    start_date = (
        datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")

    rows = store.fetch_all(
        """
        SELECT SUBSTR(updated_at, 1, 10) AS trade_date,
               SUM(net_pnl) AS daily_pnl
        FROM trades
        WHERE strategy = ?
          AND status = 'CLOSED'
          AND net_pnl IS NOT NULL
          AND SUBSTR(updated_at, 1, 10) >= ?
          AND SUBSTR(updated_at, 1, 10) <= ?
        GROUP BY trade_date
        ORDER BY trade_date
        """,
        (strategy_name, start_date, as_of_date),
    )

    if len(rows) < 2:
        return None

    daily_pnls = [float(r["daily_pnl"]) for r in rows]
    mean_pnl = sum(daily_pnls) / len(daily_pnls)
    variance = sum((x - mean_pnl) ** 2 for x in daily_pnls) / (len(daily_pnls) - 1)
    std_pnl = math.sqrt(variance) if variance > 0 else 0.0

    if std_pnl == 0.0:
        return 0.0

    return (mean_pnl / std_pnl) * _ANNUALIZATION_FACTOR


def compute_win_rate(
    store: StateStore,
    strategy_name: str,
    as_of_date: str,
    lookback_days: int = 30,
) -> tuple[Optional[float], Optional[float], int]:
    """
    Compute win rate and average P&L for a strategy over the lookback window.

    Returns (win_rate, avg_pnl, total_trades).
    """
    from datetime import datetime, timedelta
    start_date = (
        datetime.strptime(as_of_date, "%Y-%m-%d") - timedelta(days=lookback_days)
    ).strftime("%Y-%m-%d")

    rows = store.fetch_all(
        """
        SELECT net_pnl
        FROM trades
        WHERE strategy = ?
          AND status = 'CLOSED'
          AND net_pnl IS NOT NULL
          AND SUBSTR(updated_at, 1, 10) >= ?
          AND SUBSTR(updated_at, 1, 10) <= ?
        """,
        (strategy_name, start_date, as_of_date),
    )

    total = len(rows)
    if total == 0:
        return None, None, 0

    pnls = [float(r["net_pnl"]) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    win_rate = wins / total
    avg_pnl = sum(pnls) / total
    return win_rate, avg_pnl, total


def _get_active_strategies(store: StateStore, date_iso: str) -> list[str]:
    """Get distinct strategy names that had trades in the last 30 days."""
    from datetime import datetime, timedelta
    start_date = (
        datetime.strptime(date_iso, "%Y-%m-%d") - timedelta(days=30)
    ).strftime("%Y-%m-%d")
    rows = store.fetch_all(
        """
        SELECT DISTINCT strategy FROM trades
        WHERE status = 'CLOSED'
          AND SUBSTR(updated_at, 1, 10) >= ?
        """,
        (start_date,),
    )
    return [r["strategy"] for r in rows if r["strategy"]]


def _check_demotion(
    store: StateStore,
    strategy_name: str,
    log: logging.Logger,
    notifier=None,
    mode_label: str = "LIVE",
) -> bool:
    """
    Check if Sharpe < -0.3 for 3 consecutive days.
    Returns True if demotion triggered.
    """
    rows = store.fetch_all(
        """
        SELECT sharpe FROM strategy_metrics
        WHERE strategy = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (strategy_name, _CONSECUTIVE_BAD_DAYS),
    )
    if len(rows) < _CONSECUTIVE_BAD_DAYS:
        return False

    all_bad = all(
        r["sharpe"] is not None and r["sharpe"] < _SHARPE_DEMOTION_THRESHOLD
        for r in rows
    )
    if not all_bad:
        return False

    log.critical(
        "strategy_metrics.demotion_triggered",
        extra={
            "strategy": strategy_name,
            "threshold": _SHARPE_DEMOTION_THRESHOLD,
            "consecutive_days": _CONSECUTIVE_BAD_DAYS,
            "recent_sharpes": [r["sharpe"] for r in rows],
        },
    )
    if notifier is not None:
        try:
            sharpes = ", ".join(f"{r['sharpe']:.2f}" for r in rows)
            notifier.send(
                severity="ERROR",
                title=f"[{mode_label}] STRATEGY DEMOTED -- {strategy_name}",
                body=(
                    f"Sharpe < {_SHARPE_DEMOTION_THRESHOLD} for {_CONSECUTIVE_BAD_DAYS} days.\n"
                    f"Recent Sharpes: {sharpes}\n"
                    f"Strategy set to PAPER_ONLY."
                ),
                source_module="compute_strategy_metrics",
            )
        except Exception as exc:
            log.error("strategy_metrics.notifier_failed: %s", exc)
    return True


def run_strategy_metrics(
    *,
    store: StateStore,
    date_iso: str,
    lookback_days: int = 30,
    log: logging.Logger,
    notifier=None,
    mode_label: str = "LIVE",
    dry_run: bool = False,
) -> list[dict]:
    """Compute and store metrics for all active strategies."""
    now_str = now_ist().isoformat()
    strategies = _get_active_strategies(store, date_iso)
    log.info(
        "strategy_metrics.start",
        extra={"date": date_iso, "strategies": strategies},
    )

    results = []
    for strat in strategies:
        sharpe = compute_sharpe(store, strat, date_iso, lookback_days)
        win_rate, avg_pnl, total_trades = compute_win_rate(
            store, strat, date_iso, lookback_days
        )

        result = {
            "strategy": strat,
            "date": date_iso,
            "sharpe": round(sharpe, 4) if sharpe is not None else None,
            "win_rate": round(win_rate, 4) if win_rate is not None else None,
            "avg_pnl": round(avg_pnl, 2) if avg_pnl is not None else None,
            "total_trades": total_trades,
        }
        results.append(result)

        log.info(
            "strategy_metrics.computed",
            extra=result,
        )

        if not dry_run:
            with store.transaction() as cur:
                cur.execute(
                    """
                    INSERT OR REPLACE INTO strategy_metrics
                      (strategy, date, sharpe, win_rate, avg_pnl,
                       total_trades, computed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strat, date_iso, result["sharpe"],
                        result["win_rate"], result["avg_pnl"],
                        total_trades, now_str,
                    ),
                )

            _check_demotion(store, strat, log, notifier, mode_label)

    return results


def main(argv=None) -> int:
    args = _parse_args(argv)
    log = get_logger("compute_strategy_metrics")

    mode = os.environ.get("TRADING_MODE", "live").lower()
    mode_label = "PAPER" if mode == "paper" else "LIVE"

    config_dir = Path(args.config)
    try:
        load_config(config_dir)
    except Exception as exc:
        log.error("strategy_metrics: config load failed: %s", exc)
        return 1

    db_path = Path(args.db) if args.db else Path("data_store") / "trading_system.db"
    try:
        store = StateStore(db_path=db_path)
    except Exception as exc:
        log.error("strategy_metrics: state_store open failed: %s", exc)
        return 1

    date_iso = args.date or today_ist()
    try:
        run_strategy_metrics(
            store=store,
            date_iso=date_iso,
            lookback_days=args.lookback,
            log=log,
            notifier=None,
            mode_label=mode_label,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        log.error("strategy_metrics.unexpected_error: %s", exc, exc_info=True)
        store.close()
        return 1

    store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
