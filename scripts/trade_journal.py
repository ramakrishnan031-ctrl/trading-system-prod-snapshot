"""
scripts/trade_journal.py -- Trading System v2  FIX-133 Item 30

Purpose:
    Generate daily trade journal entries from closed trades.
    Populates trade_journal table with structured analysis per trade.

Usage:
    python scripts/trade_journal.py --date 2026-05-31
    python scripts/trade_journal.py  (defaults to today)

Locked Design:
    TJ1  -- Runs after EOD report (16:10 IST cron).
    TJ2  -- Populates from existing DB data — no external queries.
    TJ3  -- entry_reason = scanner name + screener score.
    TJ4  -- exit_reason from trades.exit_reason.
    TJ5  -- mfe_captured_pct = (exit - entry) / (mfe - entry) * 100.
    TJ6  -- slippage = "HIGH" if entry_slip > 0.5%, else "LOW".
    TJ7  -- Idempotent: deletes existing entries for the date before insert.
    TJ8  -- PARITY: paper and live both journal.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.state_store import StateStore
from core.time_authority import now_ist


def _log():
    return logging.getLogger("trade_journal")


def generate_journal(store: StateStore, date_iso: str, logger: logging.Logger) -> int:
    """Generate trade journal entries for the given date. Returns count inserted."""

    trades = store.fetch_all(
        """SELECT t.trade_id, t.symbol, t.strategy, t.direction,
                  t.entry_actual_price, t.exit_price, t.exit_reason,
                  t.net_pnl, t.qty_filled, t.entry_target_price,
                  s.scanner, s.trigger_price
           FROM trades t
           LEFT JOIN signals s ON t.signal_id = s.signal_id
           WHERE DATE(t.created_at) = ? AND t.status = 'CLOSED'
           ORDER BY t.created_at""",
        (date_iso,),
    )

    if not trades:
        logger.info("trade_journal.no_trades", extra={"date": date_iso})
        return 0

    # TJ7: idempotent — clear existing journal for this date
    store.execute(
        "DELETE FROM trade_journal WHERE date = ?",
        (date_iso,),
    )

    # Fetch screener scores
    scores: Dict[str, int] = {}
    score_rows = store.fetch_all(
        """SELECT sr.signal_id, sr.score
           FROM screener_results sr
           JOIN signals s ON sr.signal_id = s.signal_id
           WHERE DATE(s.received_at) = ?""",
        (date_iso,),
    )
    for row in score_rows:
        scores[row["signal_id"]] = int(row["score"] or 0)

    # Fetch MFE data from trade_excursions
    mfe_map: Dict[str, float] = {}
    try:
        excursions = store.fetch_all(
            """SELECT trade_id, mfe_price FROM trade_excursions
               WHERE DATE(created_at) = ?""",
            (date_iso,),
        )
        for row in excursions:
            mfe_map[row["trade_id"]] = float(row["mfe_price"] or 0)
    except Exception:
        pass  # trade_excursions may not exist or be empty

    count = 0
    ts = now_ist().isoformat()

    for trade in trades:
        t = dict(trade)
        trade_id = t["trade_id"]
        entry_price = float(t["entry_actual_price"] or 0)
        exit_price = float(t["exit_price"] or 0)
        target_price = float(t["entry_target_price"] or 0)

        # TJ3: entry reason
        scanner = t.get("scanner", "") or ""
        sig_id_row = store.fetch_one(
            "SELECT signal_id FROM trades WHERE trade_id = ?", (trade_id,)
        )
        sig_id = sig_id_row["signal_id"] if sig_id_row else ""
        score = scores.get(sig_id, 0)
        entry_reason = f"{scanner} (score={score})" if scanner else f"score={score}"

        # TJ6: slippage assessment
        trigger_price = float(t.get("trigger_price") or entry_price)
        if trigger_price > 0 and entry_price > 0:
            slip_pct = abs(entry_price - trigger_price) / trigger_price * 100
            slippage = "HIGH" if slip_pct > 0.5 else "LOW"
        else:
            slippage = "LOW"

        # TJ5: MFE captured %
        mfe_price = mfe_map.get(trade_id)
        mfe_captured = None
        if mfe_price and entry_price > 0:
            direction = t["direction"]
            if direction == "LONG" and mfe_price > entry_price:
                mfe_captured = (exit_price - entry_price) / (mfe_price - entry_price) * 100
            elif direction == "SHORT" and mfe_price < entry_price:
                mfe_captured = (entry_price - exit_price) / (entry_price - mfe_price) * 100

        store.execute(
            """INSERT INTO trade_journal
               (date, trade_id, strategy, symbol, direction,
                entry_reason, exit_reason, slippage_assessment,
                mfe_captured_pct, entry_price, exit_price, net_pnl,
                notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (date_iso, trade_id, t["strategy"], t["symbol"], t["direction"],
             entry_reason, t.get("exit_reason", ""),
             slippage, round(mfe_captured, 1) if mfe_captured is not None else None,
             entry_price, exit_price, float(t.get("net_pnl") or 0),
             "", ts),
        )
        count += 1

    logger.info("trade_journal.generated", extra={"date": date_iso, "entries": count})
    return count


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate daily trade journal")
    parser.add_argument("--date", default=None, help="Date YYYY-MM-DD (default: today)")
    parser.add_argument("--db", default="data_store/trading_system.db", help="Database path")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logger = _log()

    date_iso = args.date or date.today().isoformat()
    store = StateStore(Path(args.db))

    count = generate_journal(store, date_iso, logger)
    print(f"Trade journal: {count} entries for {date_iso}")


def _cron_main(argv=None) -> int:
    """Cron entry: holiday-skip + heartbeat + per-job alert (TASK #3)."""
    from utils.cron_heartbeat import HeartbeatTimer, skip_if_non_trading_day

    if skip_if_non_trading_day("trade_journal"):
        return 0
    timer = HeartbeatTimer("trade_journal", alert=True)
    with timer:
        rc = main(argv)
        rc = 0 if rc is None else rc
        if rc != 0:
            timer.status = "FAILED"
            timer.message = f"exit code {rc}"
    return rc


if __name__ == "__main__":
    sys.exit(_cron_main())
