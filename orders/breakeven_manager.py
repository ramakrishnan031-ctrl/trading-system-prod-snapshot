"""
orders/breakeven_manager.py -- Trading System v2  FIX-132 Item 8

Purpose:
    Milestone-based SL advancement for LIMIT_TRIPLE trades.
    Distinct from SmartTgtManager (which trails CO bracket SL step-by-step).

    At 60% of target distance from entry → advance SL to breakeven (entry price)
    At 80% of target distance from entry → advance SL to 40% of target distance

Locked Design Decisions:
    BM1  -- LIMIT_TRIPLE protocol only; CO orders are handled by SmartTgtManager.
    BM2  -- Two milestones per trade: breakeven (60%) and partial_lock (80%).
    BM3  -- Each milestone fires ONCE per trade (idempotent flag per trade).
    BM4  -- Ghost-SL safe: modifies broker FIRST; updates state only on confirm.
    BM5  -- Driven by candle close (high/low) to detect intrabar extremes.
    BM6  -- register_trade / unregister_trade; in-memory state only (no new DB table).
    BM7  -- Thresholds configurable per strategy: breakeven_trigger_pct,
            partial_lock_trigger_pct, partial_lock_sl_pct (all in % of target distance).
    BM8  -- adapter.modify_order(broker_order_id, trigger_price=new_sl) for SL advance.
    BM9  -- SL broker_order_id looked up from state_store orders table.
    BM10 -- Thread-safe: threading.RLock guards _tracked dict.

What This Module Does NOT Do:
    - Does not handle CO orders (SmartTgtManager's job)
    - Does not trail SL continuously (milestone only; use SmartTgtManager for trailing)
    - Does not persist state to DB (in-memory; reset on restart)
    - Does not place new orders (only modifies existing SL trigger)
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from orders.price_math import (
    DEFAULT_SL_LIMIT_OFFSET_PCT,
    DEFAULT_TICK,
    calc_sl_limit_price,
    round_to_tick,
)


@dataclass
class _TradeInfo:
    trade_id: str
    symbol: str
    direction: str          # LONG | SHORT
    entry_price: float
    target_price: float     # tgt_initial (used to compute target_distance)
    breakeven_trigger_pct: float   # % of target distance to trigger breakeven (default 60)
    partial_lock_trigger_pct: float  # % of target distance for partial lock (default 80)
    partial_lock_sl_pct: float       # SL moves to this % of target distance (default 40)
    best_price: Optional[float] = field(default=None)
    breakeven_applied: bool = field(default=False)
    partial_lock_applied: bool = field(default=False)


class BreakevenManager:
    """
    Milestone-based SL advancement for LIMIT_TRIPLE trades (BM1-BM10).

    Usage::
        mgr = BreakevenManager(adapter, state_store, logger)
        mgr.register_trade(trade_id, symbol, "LONG", entry_price, target_price,
                           sl_breakeven_trigger_pct=60, ...)
        # candle_store fires: mgr.on_candle_close(candle)
        mgr.unregister_trade(trade_id)
    """

    def __init__(
        self,
        adapter: Any,        # ZerodhaAdapter with .modify_order()
        state_store: Any,    # StateStore with .fetch_one()
        logger: Any,
        notifier: Any = None,
        modify_max_retries: int = 3,
        modify_retry_backoff_sec: float = 2.0,
        sl_limit_offset_pct: float = DEFAULT_SL_LIMIT_OFFSET_PCT,
        instrument_cache: Any = None,
    ) -> None:
        self._adapter = adapter
        self._store = state_store
        self._log = logger
        self._notifier = notifier
        self._modify_max_retries = max(1, modify_max_retries)
        self._modify_retry_backoff = modify_retry_backoff_sec
        # FIX-181: tick lookup so the trailed SL trigger + limit are snapped to a
        # valid tick (modify_order has no symbol, so the adapter cannot snap it).
        self._instrument_cache = instrument_cache
        # P0 (2026-06-15): SL legs are stop-limit (SL), not SL-M. When we advance
        # the SL trigger we must also move the limit price the same offset, else
        # the stale limit drifts away from the new trigger. See calc_sl_limit_price.
        self._sl_limit_offset_pct = sl_limit_offset_pct
        self._tracked: Dict[str, _TradeInfo] = {}
        self._lock = threading.RLock()
        self._consecutive_failures: Dict[str, int] = {}

    def set_instrument_cache(self, cache: Any) -> None:
        """FIX-181: late-bind InstrumentCache for tick-aware SL trailing."""
        self._instrument_cache = cache

    def _tick_for(self, symbol: str) -> float:
        """FIX-181: instrument tick for `symbol`; falls back to DEFAULT_TICK."""
        if self._instrument_cache is None:
            return DEFAULT_TICK
        try:
            tick = self._instrument_cache.tick_size(symbol)
            return tick if tick and tick > 0 else DEFAULT_TICK
        except Exception:
            return DEFAULT_TICK

    # ── public API ─────────────────────────────────────────────────────────────

    def register_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_price: float,
        target_price: float,
        breakeven_trigger_pct: float = 60.0,
        partial_lock_trigger_pct: float = 80.0,
        partial_lock_sl_pct: float = 40.0,
    ) -> None:
        """BM6: Register a LIMIT_TRIPLE trade for milestone SL advancement."""
        if abs(target_price - entry_price) < 0.001:
            self._log.warning(
                "breakeven_manager.register_skip: target too close to entry",
                extra={"trade_id": trade_id, "entry": entry_price, "target": target_price},
            )
            return
        with self._lock:
            if trade_id in self._tracked:
                return  # idempotent
            self._tracked[trade_id] = _TradeInfo(
                trade_id=trade_id,
                symbol=symbol,
                direction=direction,
                entry_price=float(entry_price),
                target_price=float(target_price),
                breakeven_trigger_pct=float(breakeven_trigger_pct),
                partial_lock_trigger_pct=float(partial_lock_trigger_pct),
                partial_lock_sl_pct=float(partial_lock_sl_pct),
            )
        self._log.info(
            "breakeven_manager.registered",
            extra={
                "trade_id": trade_id,
                "direction": direction,
                "entry": entry_price,
                "target": target_price,
                "breakeven_at_pct": breakeven_trigger_pct,
                "partial_lock_at_pct": partial_lock_trigger_pct,
            },
        )

    def unregister_trade(self, trade_id: str) -> bool:
        """BM6: Remove trade from tracking. Returns True if found."""
        with self._lock:
            if trade_id not in self._tracked:
                return False
            del self._tracked[trade_id]
        self._log.info("breakeven_manager.unregistered", extra={"trade_id": trade_id})
        return True

    def on_candle_close(self, candle: Any) -> None:
        """BM5: Called by candle_store on each minute candle close. Thread-safe."""
        with self._lock:
            snapshot = dict(self._tracked)

        for trade_id, info in snapshot.items():
            try:
                self._process_trade(trade_id, info, candle)
            except Exception as exc:
                self._log.error(
                    "breakeven_manager.on_candle_close_error",
                    extra={"trade_id": trade_id, "error": str(exc)},
                )

    def tracked_ids(self) -> list[str]:
        with self._lock:
            return list(self._tracked.keys())

    # ── internal ───────────────────────────────────────────────────────────────

    def _process_trade(self, trade_id: str, info: _TradeInfo, candle: Any) -> None:
        """Check thresholds and advance SL if milestone reached."""
        direction = info.direction
        # Use candle extreme (high for LONG, low for SHORT) to capture intrabar moves
        current_extreme = getattr(candle, "high", None) if direction == "LONG" else getattr(candle, "low", None)
        if current_extreme is None:
            return

        with self._lock:
            if trade_id not in self._tracked:
                return  # may have been unregistered concurrently
            t = self._tracked[trade_id]
            # Update best price
            if t.best_price is None:
                t.best_price = current_extreme
            elif direction == "LONG":
                t.best_price = max(t.best_price, current_extreme)
            else:
                t.best_price = min(t.best_price, current_extreme)
            best = t.best_price

        target_distance = abs(info.target_price - info.entry_price)
        if target_distance <= 0:
            return

        # Compute how far price has moved as % of target distance
        if direction == "LONG":
            progress_pct = (best - info.entry_price) / target_distance * 100.0
        else:
            progress_pct = (info.entry_price - best) / target_distance * 100.0

        if progress_pct < 0:
            return

        # Check partial_lock first (higher threshold)
        with self._lock:
            t = self._tracked.get(trade_id)
            if t is None:
                return
            apply_partial = (
                not t.partial_lock_applied
                and progress_pct >= t.partial_lock_trigger_pct
            )
            apply_breakeven = (
                not t.breakeven_applied
                and not apply_partial  # don't double-advance
                and progress_pct >= t.breakeven_trigger_pct
            )

        if apply_partial:
            new_sl = self._compute_partial_lock_sl(info, direction, target_distance)
            self._advance_sl(trade_id, info, new_sl, "partial_lock")
        elif apply_breakeven:
            new_sl = info.entry_price
            self._advance_sl(trade_id, info, new_sl, "breakeven")

    def _compute_partial_lock_sl(
        self, info: _TradeInfo, direction: str, target_distance: float
    ) -> float:
        """BM2: SL at partial_lock_sl_pct% of target distance from entry."""
        lock_distance = (info.partial_lock_sl_pct / 100.0) * target_distance
        if direction == "LONG":
            return info.entry_price + lock_distance
        else:
            return info.entry_price - lock_distance

    def _advance_sl(
        self, trade_id: str, info: _TradeInfo, new_sl: float, milestone: str
    ) -> None:
        """
        BM4: Modify broker SL order; mark milestone only on broker confirmation.

        FIX-148 (GAP 2): Retries up to modify_max_retries with backoff.
        Sends WARNING Telegram alert after all retries exhausted.
        """
        broker_order_id = self._get_sl_broker_order_id(trade_id)
        if not broker_order_id:
            self._log.warning(
                "breakeven_manager.no_sl_order",
                extra={"trade_id": trade_id, "milestone": milestone},
            )
            return

        # P0 (2026-06-15): SL is a stop-limit order — advance the limit price
        # alongside the trigger so it keeps the configured offset. Exit side for
        # a LONG position is SELL (limit below trigger); for SHORT it is BUY
        # (limit above trigger).
        exit_side = "SELL" if info.direction == "LONG" else "BUY"
        # FIX-181 (GICRE incident): snap trigger + limit to the instrument tick.
        # modify_order carries no symbol, so the adapter cannot snap this leg —
        # an off-tick trigger/limit gets rejected by Zerodha.
        tick = self._tick_for(info.symbol)
        new_trigger = round_to_tick(new_sl, tick, mode="nearest")
        new_limit = calc_sl_limit_price(
            exit_side, new_trigger, self._sl_limit_offset_pct, tick_size=tick,
        )

        # BM4: modify broker FIRST — with FIX-148 retry logic
        last_error = None
        for attempt in range(1, self._modify_max_retries + 1):
            try:
                result = self._adapter.modify_order(
                    broker_order_id,
                    price=new_limit,
                    trigger_price=new_trigger,
                    symbol=info.symbol,
                )
            except Exception as exc:
                last_error = str(exc)
                self._log.error(
                    "breakeven_manager.modify_failed",
                    extra={
                        "trade_id": trade_id, "milestone": milestone,
                        "attempt": attempt, "max": self._modify_max_retries,
                        "error": last_error,
                    },
                )
                if attempt < self._modify_max_retries:
                    time.sleep(self._modify_retry_backoff)
                continue

            if result.success:
                # Success — reset failure counter
                self._consecutive_failures.pop(trade_id, None)

                with self._lock:
                    t = self._tracked.get(trade_id)
                    if t is not None:
                        if milestone == "breakeven":
                            t.breakeven_applied = True
                        elif milestone == "partial_lock":
                            t.partial_lock_applied = True
                            t.breakeven_applied = True

                self._log.info(
                    "breakeven_manager.sl_advanced",
                    extra={
                        "trade_id": trade_id,
                        "milestone": milestone,
                        "new_sl": round(new_sl, 2),
                        "symbol": info.symbol,
                        "direction": info.direction,
                        "attempt": attempt,
                    },
                )
                return

            # Modify rejected by broker
            last_error = result.reason
            self._log.error(
                "breakeven_manager.modify_rejected",
                extra={
                    "trade_id": trade_id, "milestone": milestone,
                    "new_sl": new_sl, "reason": result.reason,
                    "attempt": attempt, "max": self._modify_max_retries,
                },
            )
            if attempt < self._modify_max_retries:
                time.sleep(self._modify_retry_backoff)

        # All retries exhausted — track consecutive failures + alert
        count = self._consecutive_failures.get(trade_id, 0) + 1
        self._consecutive_failures[trade_id] = count

        self._log.warning(
            "breakeven_manager.modify_retries_exhausted",
            extra={
                "trade_id": trade_id,
                "milestone": milestone,
                "new_sl": new_sl,
                "consecutive_failures": count,
                "last_error": last_error,
            },
        )

        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="WARNING",
                    title=f"Breakeven SL modify failed -- {info.symbol}",
                    body=(
                        f"Trade: {trade_id}\n"
                        f"Milestone: {milestone}\n"
                        f"New SL: {new_sl:.2f}\n"
                        f"Retries: {self._modify_max_retries} exhausted\n"
                        f"Consecutive failures: {count}\n"
                        f"Last error: {last_error}\n"
                        f"Original SL still active at broker"
                    ),
                    source_module="breakeven_manager",
                )
            except Exception as exc:
                # M-X2: don't let a notifier failure swallow the fact that breakeven SL-modify
                # exhausted its retries (original SL still active at broker) — log it LOUD even
                # when the alert itself could not be delivered.
                self._log.error(
                    "breakeven_manager: SL-modify-exhausted notifier send FAILED: %s", exc,
                )

    def _get_sl_broker_order_id(self, trade_id: str) -> Optional[str]:
        """BM9: Look up active SL order's broker_order_id from state_store."""
        try:
            row = self._store.fetch_one(
                """SELECT broker_order_id FROM orders
                   WHERE trade_id = ? AND leg = 'SL'
                     AND status NOT IN ('CANCELLED', 'COMPLETE', 'REJECTED', 'FAILED')
                   LIMIT 1""",
                (trade_id,),
            )
            return row["broker_order_id"] if row else None
        except Exception as exc:
            self._log.error(
                "breakeven_manager.db_lookup_error",
                extra={"trade_id": trade_id, "error": str(exc)},
            )
            return None
