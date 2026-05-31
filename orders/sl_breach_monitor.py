"""
orders/sl_breach_monitor.py -- Trading System v2  FIX-134 Item 39

Purpose:
    Tick-level backup SL monitor. For each open trade, if the broker SL order
    is missing/unconfirmed AND current LTP has breached the initial SL price,
    fire an emergency market exit.

    This is a BACKUP -- does NOT fire when the broker SL order is confirmed
    active (reconciliation_status = 'OK' or SL order in non-terminal status).

Design:
    - Wired into the tick dispatcher in main.py alongside candle_store / shadow_tracker.
    - Receives tick dict {instrument_token, last_price, ...}.
    - Queries DB for open trades with exposed SL on a throttled interval (default 5s).
    - LIVE mode: places emergency MARKET exit via adapter.
    - PAPER mode: logs only, no actual order placed (simulated fills handle it).
    - Telegram CRITICAL alert on every emergency exit.
    - Stores exit_reason = 'EMERGENCY_SL_TICK' in DB.
    - Cooldown per trade_id to prevent duplicate emergency exits.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from core.time_authority import now_ist


class SlBreachMonitor:
    """Tick-level backup SL breach detector."""

    def __init__(
        self,
        state_store,
        adapter=None,
        instrument_cache=None,
        logger: Optional[logging.Logger] = None,
        notifier=None,
        mode: str = "LIVE",
        check_interval_sec: float = 5.0,
    ) -> None:
        self._store = state_store
        self._adapter = adapter
        self._instrument_cache = instrument_cache
        self._log = logger or logging.getLogger("sl_breach_monitor")
        self._notifier = notifier
        self._mode = mode
        self._check_interval_sec = check_interval_sec

        self._token_map: dict[int, str] = {}
        self._last_check_time: float = 0.0
        self._lock = threading.Lock()
        self._ltp_cache: dict[str, float] = {}
        self._fired_trade_ids: set[str] = set()

    def set_token_map(self, token_map: dict[int, str]) -> None:
        self._token_map = token_map

    def on_tick(self, tick: dict) -> None:
        token = tick.get("instrument_token")
        ltp = tick.get("last_price", 0.0)
        if token is None or ltp <= 0:
            return

        symbol = self._token_map.get(token)
        if symbol:
            self._ltp_cache[symbol] = ltp

        now = time.monotonic()
        if now - self._last_check_time < self._check_interval_sec:
            return

        with self._lock:
            if now - self._last_check_time < self._check_interval_sec:
                return
            self._last_check_time = now
            self._check_breaches()

    def _check_breaches(self) -> None:
        exposed = self._get_exposed_trades()
        for trade in exposed:
            trade_id = trade["trade_id"]
            if trade_id in self._fired_trade_ids:
                continue
            symbol = trade["symbol"]
            direction = trade["direction"]
            sl_initial = float(trade["sl_initial"])
            qty_filled = int(trade["qty_filled"])
            ltp = self._ltp_cache.get(symbol)
            if ltp is None or ltp <= 0:
                continue

            breached = False
            if direction == "LONG" and ltp <= sl_initial:
                breached = True
            elif direction == "SHORT" and ltp >= sl_initial:
                breached = True

            if breached:
                self._fire_emergency_exit(
                    trade_id=trade_id,
                    symbol=symbol,
                    direction=direction,
                    qty=qty_filled,
                    ltp=ltp,
                    sl=sl_initial,
                )

    def _get_exposed_trades(self) -> list[dict]:
        """
        Find open trades where the SL order is missing or unconfirmed.
        A trade is 'exposed' if:
          1. Trade status is OPEN or PARTIAL with qty_filled > 0
          2. No active SL order exists (no non-terminal SL leg, or reconciliation_status = 'SL_MISSING')
        """
        rows = self._store.fetch_all(
            """
            SELECT t.trade_id, t.symbol, t.direction, t.sl_initial, t.qty_filled
            FROM trades t
            WHERE t.status IN ('OPEN', 'PARTIAL')
              AND t.qty_filled > 0
              AND NOT EXISTS (
                SELECT 1 FROM orders o
                WHERE o.trade_id = t.trade_id
                  AND o.leg = 'SL'
                  AND o.status IN ('OPEN', 'TRIGGER PENDING', 'SUBMITTED', 'PENDING')
                  AND o.superseded_by IS NULL
              )
            """,
            (),
        )
        return [dict(r) for r in rows]

    def _fire_emergency_exit(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        qty: int,
        ltp: float,
        sl: float,
    ) -> None:
        self._fired_trade_ids.add(trade_id)
        exit_side = "SELL" if direction == "LONG" else "BUY"

        self._log.critical(
            "sl_breach_monitor.EMERGENCY_SL: %s ltp=%.2f sl=%.2f side=%s qty=%d trade=%s",
            symbol, ltp, sl, exit_side, qty, trade_id,
        )

        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="ERROR",
                    title=f"[{self._mode}] EMERGENCY SL -- {symbol}",
                    body=(
                        f"LTP {ltp:.2f} breached SL {sl:.2f}\n"
                        f"Direction: {direction}, Qty: {qty}\n"
                        f"Broker SL order MISSING. Emergency {exit_side} fired.\n"
                        f"Trade: {trade_id}"
                    ),
                    source_module="sl_breach_monitor",
                )
            except Exception:
                pass

        if self._mode == "LIVE" and self._adapter is not None:
            try:
                broker_order_id = self._adapter.place_order(
                    symbol=symbol,
                    transaction_type=exit_side,
                    qty=qty,
                    order_type="MARKET",
                    product="MIS",
                    variety="regular",
                )
                self._log.critical(
                    "sl_breach_monitor.emergency_order_placed: %s broker_id=%s",
                    symbol, broker_order_id,
                )
                self._update_trade_exit(trade_id, ltp, broker_order_id, exit_side, qty)
            except Exception as exc:
                self._log.critical(
                    "sl_breach_monitor.emergency_order_FAILED: %s error=%s",
                    symbol, exc,
                )
        else:
            self._log.info(
                "sl_breach_monitor.paper_mode_skip: %s (would exit %s x%d)",
                symbol, exit_side, qty,
            )
            self._update_trade_exit(trade_id, ltp, None, exit_side, qty)

    def _update_trade_exit(
        self, trade_id: str, exit_price: float,
        broker_order_id: Optional[str],
        exit_side: str = "SELL", qty: int = 0,
    ) -> None:
        now_str = now_ist().isoformat()
        try:
            with self._store.transaction() as cur:
                cur.execute(
                    """
                    UPDATE trades
                    SET exit_reason = 'EMERGENCY_SL_TICK',
                        exit_price = ?,
                        exit_time = ?,
                        updated_at = ?
                    WHERE trade_id = ?
                      AND status IN ('OPEN', 'PARTIAL')
                    """,
                    (exit_price, now_str, now_str, trade_id),
                )
                if broker_order_id is not None:
                    cur.execute(
                        """
                        INSERT INTO orders
                          (order_id, trade_id, leg, transaction_type, order_type,
                           product, variety, qty_requested, status, placed_at, updated_at)
                        VALUES (?, ?, 'EOD', ?, 'MARKET', 'MIS', 'regular', ?,
                                'PENDING', ?, ?)
                        """,
                        (
                            broker_order_id, trade_id,
                            exit_side, qty, now_str, now_str,
                        ),
                    )
        except Exception as exc:
            self._log.error(
                "sl_breach_monitor.db_update_failed: trade=%s error=%s",
                trade_id, exc,
            )

    @property
    def fired_count(self) -> int:
        return len(self._fired_trade_ids)

    def reset_fired(self) -> None:
        self._fired_trade_ids.clear()
