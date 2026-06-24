"""
scripts/replay_signals.py -- Trading System v2  FIX-133 Item 19

Purpose:
    Read-only replay of historical signals through current strategy + screener
    + position sizer logic. Outputs a CSV comparing original vs replay decisions.

Usage:
    python scripts/replay_signals.py --from 2026-05-08 --to 2026-05-15
    python scripts/replay_signals.py --from 2026-05-08 --to 2026-05-15 --db path/to/db

Output:
    reports/output/replay_YYYY-MM-DD.csv

Locked Design:
    RP1  -- Read-only: NO database writes, NO broker calls.
    RP2  -- Uses same screener/sizer logic as live (parity).
    RP3  -- One row per signal: original vs replay decision.
    RP4  -- Fails gracefully on missing data (marks as SKIPPED).
"""
from __future__ import annotations

import argparse
import csv
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_store import StateStore
from strategies.loader import StrategyLoader


def _log():
    return logging.getLogger("replay_signals")


def fetch_signals(store: StateStore, from_date: str, to_date: str) -> List[dict]:
    """Fetch all signals in the date range."""
    rows = store.fetch_all(
        """SELECT signal_id, symbol, scanner, strategy, triggered_at,
                  trigger_price, status, rejection_reason, trade_id
           FROM signals
           WHERE DATE(received_at) >= ? AND DATE(received_at) <= ?
           ORDER BY received_at""",
        (from_date, to_date),
    )
    return [dict(r) for r in rows]


def fetch_trade_for_signal(store: StateStore, trade_id: str) -> Optional[dict]:
    """Fetch the trade row if the signal was traded."""
    if not trade_id:
        return None
    row = store.fetch_one(
        "SELECT qty_filled, entry_actual_price, net_pnl, status FROM trades WHERE trade_id = ?",
        (trade_id,),
    )
    return dict(row) if row else None


def replay_one_signal(
    signal: dict,
    strategies: Dict[str, Any],
    store: StateStore,
    logger: logging.Logger,
) -> dict:
    """Replay a single signal through current strategy config + sizer logic."""
    result = {
        "signal_id": signal["signal_id"],
        "symbol": signal["symbol"],
        "strategy": signal["strategy"],
        "scanner": signal["scanner"],
        "trigger_price": signal["trigger_price"],
        "original_status": signal["status"],
        "original_rejection": signal["rejection_reason"] or "",
        "replay_decision": "UNKNOWN",
        "replay_qty": 0,
        "replay_reason": "",
        "original_trade_id": signal["trade_id"] or "",
        "original_pnl": "",
    }

    trade = fetch_trade_for_signal(store, signal.get("trade_id"))
    if trade:
        result["original_pnl"] = str(trade.get("net_pnl", ""))

    strategy_name = signal["strategy"]
    if strategy_name not in strategies:
        result["replay_decision"] = "SKIPPED"
        result["replay_reason"] = f"Strategy '{strategy_name}' not in current config"
        return result

    strategy_obj = strategies[strategy_name]

    trigger_price = signal.get("trigger_price")
    if not trigger_price or trigger_price <= 0:
        result["replay_decision"] = "SKIPPED"
        result["replay_reason"] = "No valid trigger_price"
        return result

    # Replay screening: check min_score threshold
    screener_row = store.fetch_one(
        """SELECT score, tier, status FROM screener_results
           WHERE signal_id = ? ORDER BY ts DESC LIMIT 1""",
        (signal["signal_id"],),
    )

    if screener_row:
        sr = dict(screener_row)
        score = int(sr.get("score", 0) or 0)
        scr_status = sr.get("status", "")
        tier = sr.get("tier", "LOW")

        if scr_status != "PASSED":
            result["replay_decision"] = "REJECTED"
            result["replay_reason"] = f"Screener rejected (score={score})"
            return result

        current_min_score = getattr(strategy_obj, "min_score", 0)
        if score < current_min_score:
            result["replay_decision"] = "REJECTED"
            result["replay_reason"] = (
                f"Score {score} < current min_score {current_min_score}"
            )
            return result
    else:
        tier = "MEDIUM"

    # Replay sizing: compute basic risk-based qty (RP2: same formula as live sizer)
    try:
        import math

        direction = strategy_obj.direction
        sl_pct = getattr(strategy_obj, "sl_pct", 0.015)
        sl_price = (
            trigger_price * (1 - sl_pct)
            if direction == "LONG"
            else trigger_price * (1 + sl_pct)
        )
        sl_distance = abs(trigger_price - sl_price)

        if sl_distance < 0.01:
            result["replay_decision"] = "REJECTED"
            result["replay_reason"] = "SL distance too small"
            return result

        replay_capital = 100000.0
        # BUILD 1 (#11, 24-Jun): per-strategy max_risk_pct was removed from the
        # StrategyConfig schema (dead in live; only replay read it). getattr
        # falls back to 0.01 — the value every strategy YAML used — so replay
        # sizing is unchanged.
        risk_pct = getattr(strategy_obj, "max_risk_pct", 0.01)
        risk_rs = replay_capital * risk_pct
        qty = int(math.floor(risk_rs / sl_distance))
        lot_size = strategy_obj.lot_size
        qty = (qty // lot_size) * lot_size

        if qty >= 1:
            margin = qty * trigger_price / 5.0  # approx intraday leverage
            result["replay_decision"] = "WOULD_TRADE"
            result["replay_qty"] = qty
            result["replay_reason"] = f"qty={qty}, margin={margin:.0f}"
        else:
            result["replay_decision"] = "REJECTED"
            result["replay_reason"] = "Computed qty < 1 lot"
    except Exception as exc:
        result["replay_decision"] = "ERROR"
        result["replay_reason"] = str(exc)

    return result


def run_replay(
    db_path: str,
    from_date: str,
    to_date: str,
    config_dir: str = "config",
) -> List[dict]:
    """Run full replay and return results."""
    logger = _log()
    store = StateStore(Path(db_path))

    loader = StrategyLoader()
    strat_map = loader.load_all_strategies(Path(config_dir) / "strategies")
    strategies = strat_map

    signals = fetch_signals(store, from_date, to_date)
    logger.info("Replay: %d signals from %s to %s", len(signals), from_date, to_date)

    results = []
    for sig in signals:
        row = replay_one_signal(sig, strategies, store, logger)
        results.append(row)

    return results


def write_csv(results: List[dict], output_path: Path) -> None:
    """Write replay results to CSV."""
    if not results:
        print("No signals found in date range.")
        return

    fieldnames = [
        "signal_id", "symbol", "strategy", "scanner", "trigger_price",
        "original_status", "original_rejection", "original_trade_id",
        "original_pnl", "replay_decision", "replay_qty", "replay_reason",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    print(f"Replay output: {output_path} ({len(results)} signals)")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Replay historical signals")
    parser.add_argument("--from", dest="from_date", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--to", dest="to_date", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--db", default="data_store/trading_system.db", help="Database path")
    parser.add_argument("--config", default="config", help="Config directory")
    parser.add_argument("--output", default=None, help="Output CSV path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    results = run_replay(args.db, args.from_date, args.to_date, args.config)

    output = Path(args.output) if args.output else Path(
        f"reports/output/replay_{args.from_date}.csv"
    )
    write_csv(results, output)


if __name__ == "__main__":
    main()
