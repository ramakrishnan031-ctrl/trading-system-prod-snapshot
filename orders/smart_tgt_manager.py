"""
orders/smart_tgt_manager.py -- Trading System v2

Purpose:
    Trail SL on open CO trades using candle-close data. Fixed-step algorithm
    per S3. Modifies CO trigger_price in place via adapter.modify_order
    (P8_P13 -- NOT cancel-replace). Ghost-SL-safe: internal state updated
    ONLY after broker confirms the modification.

Locked Design Decisions:
    ST1  -- Trail via CO trigger_price modification, not cancel-replace (P8_P13).
    ST2  -- Constructor: adapter, state_store, candle_store, logger,
            quote_fn=None, enabled=True, on_critical_failure=None.
            Register self._on_candle_close with candle_store at construction.
    ST3  -- register_trade() / unregister_trade(): add/remove from _tracked
            dict and smart_tgt_state table. ValueError on dup register.
    ST4  -- Fixed-step trail algorithm: best_price tracked per trade (max for
            LONG, min for SHORT). steps_count = int((dist - trigger) / step).
            new_sl = entry * (1 + trigger + steps * step) for LONG.
            Only advance SL (never lower LONG SL; never raise SHORT SL).
    ST5  -- Ghost SL fix: modify broker first; update _tracked + DB only on
            confirm. Failure -> ERROR log + consecutive_failures counter.
    ST6  -- candle_store fires fn(CandleData) for all symbols. Filter by
            instrument_token. Fast path: dict lookup.
    ST7  -- on_reconnect(ts): discard best_price, recompute from candle
            history ONCE per trade. Do NOT re-fire per-bar callbacks (G6/LF7).
    ST8  -- start(): reads smart_tgt_state table, repopulates _tracked,
            runs optional startup LTP check per recovered trade.
    ST9  -- Thread safety: threading.RLock guards _tracked and _enabled.
    ST10 -- 3 consecutive modify failures -> CRITICAL log + on_critical_failure.
            Successful modify resets counter.
    ST11 -- Lifecycle: start() for DB recovery + re-register callback.
            stop() unregisters callback, clears _tracked (preserves DB).
    ST12 -- enabled=False or disable(): register/candle/reconnect all no-op.
    ST13 -- Reconnect chaining owned by main.py. on_reconnect() must be called
            AFTER candle_store.mark_reconnect() (so partials are discarded
            before we recompute best_price from closed history).
    ST14 -- state_store.get_co_entry_order_for_trade(trade_id) added.
    ST15 -- smart_tgt_state table for crash recovery.

What This Module Does NOT Do:
    - Does not cancel-replace orders (P8_P13: modify trigger_price only)
    - Does not place new orders or close positions
    - Does not modify the TGT LIMIT order (only the CO SL bracket)
    - Does not modify LIMIT_TRIPLE trades (CO only per P8_P13)
    - Does not subscribe to EventBus (driven by candle_store callback)
"""
from __future__ import annotations

import math
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.time_authority import now_ist_iso

# DUP-1 (2026-04-26 audit): _IST removed; never read locally.
# DUP-2 (2026-04-26 audit): local _now_ist_iso() removed; using canonical
# core.time_authority.now_ist_iso() at the two call sites.
_MAX_CONSECUTIVE_FAILURES = 3
_MAX_HISTORY_CANDLES = 390  # LF14: 1 full trading day of 1-min candles


# ---------------------------------------------------------------------------
# SmartTgtManager
# ---------------------------------------------------------------------------

class SmartTgtManager:
    """
    Trail SL on CO orders using candle-close data (ST1-ST15).

    Driven by candle_store callbacks -- no own thread. Lifecycle:
        mgr = SmartTgtManager(adapter, state_store, candle_store, log)
        mgr.start()            # DB recovery + re-register callback
        mgr.register_trade(...)
        # candle_store fires _on_candle_close() on each minute close
        # on_reconnect() wired via main.py after candle_store.mark_reconnect
        mgr.unregister_trade(trade_id)
        mgr.stop()
    """

    def __init__(
        self,
        adapter,
        state_store,
        candle_store,
        logger,
        quote_fn: Optional[Callable] = None,
        enabled: bool = True,
        on_critical_failure: Optional[Callable[[str, str], None]] = None,
        async_modify: bool = False,
        rate_limiter: Optional[Any] = None,
    ) -> None:
        self._adapter = adapter
        self._state_store = state_store
        self._candle_store = candle_store
        self._log = logger
        self._quote_fn = quote_fn
        self._enabled = enabled
        self._on_critical_failure = on_critical_failure
        # D.1 (2026-04-25): trailing N CO orders at minute boundary can burst
        # past Zerodha's order quota and trigger 429. Acquire from the "order"
        # bucket before every modify_order call so the trail paces itself
        # alongside fresh placements/cancels owned by OrderPlacer. Optional
        # to keep the existing test suite (which constructs without RL) green.
        self._rl = rate_limiter

        self._tracked: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.RLock()

        # Audit #8: instrument_cache wired via set_instrument_cache() after
        # construction (same pattern as OrderPlacer/IC8). Used to snap the
        # trailed SL to a valid tick before modify_order so the broker does
        # not reject every update with "invalid trigger_price".
        self._instrument_cache = None

        # B.4 / Audit 5.4: optional async path for adapter.modify_order so the
        # candle-close consumer thread is not blocked by N * ~100ms HTTP
        # serial calls. Opt-in via async_modify=True so legacy callers and
        # the existing test suite keep their synchronous semantics; main.py
        # wires async_modify=True in production.
        #
        # Newest-wins coalesce: _pending_modify[trade_id] always stores the
        # most-recent target SL; older worker tasks pop nothing and exit.
        self._async_modify = bool(async_modify)
        self._modify_executor: Optional[ThreadPoolExecutor] = None
        self._pending_modify: Dict[str, float] = {}
        self._pending_lock = threading.Lock()
        self._inflight_futures: List[Any] = []
        if self._async_modify:
            self._modify_executor = ThreadPoolExecutor(
                max_workers=2, thread_name_prefix="smart-tgt-modify",
            )

        # ST2: register callback at construction (if enabled)
        if enabled:
            candle_store.register_on_candle_close(self._on_candle_close)

    def set_instrument_cache(self, cache) -> None:
        """Audit #8: wire InstrumentCache for tick_size lookup (LONG/SHORT SL rounding)."""
        self._instrument_cache = cache

    # ------------------------------------------------------------------
    # Lifecycle (ST11)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """
        ST8: Startup recovery -- reads smart_tgt_state table and repopulates
        _tracked. Runs optional LTP check per recovered trade. Also
        re-registers candle callback (idempotent; handles stop+restart cycle).
        """
        if not self._enabled:
            return

        # Re-register (idempotent: candle_store checks for duplicates)
        self._candle_store.register_on_candle_close(self._on_candle_close)

        rows = self._state_store.get_all_smart_tgt_states()

        with self._lock:
            for row in rows:
                trade_id = row["trade_id"]
                if trade_id in self._tracked:
                    continue
                bp = row["best_price"]
                self._tracked[trade_id] = {
                    "trade_id":             trade_id,
                    "symbol":               row["symbol"],
                    "instrument_token":     row["instrument_token"],
                    "direction":            row["direction"],
                    "entry_price":          float(row["entry_price"]),
                    "initial_sl":           float(row["initial_sl"]),
                    "current_sl":           float(row["current_sl"]),
                    "qty":                  int(row["qty"]),
                    "trigger_pct":          float(row["trigger_pct"]),
                    "step_pct":             float(row["step_pct"]),
                    "best_price":           float(bp) if bp is not None else None,
                    "trail_count":          int(row["trail_count"]),
                    "last_trail_ts":        row["last_trail_ts"],
                    "consecutive_failures": 0,
                }

        if rows:
            self._log.info(
                f"SmartTgtManager: recovered {len(rows)} trade(s) from smart_tgt_state"
            )

        for row in rows:
            self._startup_ltp_check(row["trade_id"])

    def stop(self) -> None:
        """
        ST11: Unregister candle callback; clear _tracked in memory.
        Does NOT touch smart_tgt_state DB (preserved for restart).

        B.4 / Audit 5.4: drain + shut down the modify executor (if async
        path is enabled). wait=True with a short timeout so any in-flight
        broker modify completes and the broker / DB stay consistent.
        """
        self._candle_store.unregister_on_candle_close(self._on_candle_close)
        if self._modify_executor is not None:
            self._flush_inflight(timeout_sec=5.0)
            try:
                self._modify_executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        with self._lock:
            self._tracked.clear()
        with self._pending_lock:
            self._pending_modify.clear()
        self._log.info("SmartTgtManager stopped")

    def disable(self) -> None:
        """ST12: Runtime disable. All subsequent operations become no-ops."""
        with self._lock:
            self._enabled = False

    # ------------------------------------------------------------------
    # Public API (ST3)
    # ------------------------------------------------------------------

    def register_trade(
        self,
        trade_id: str,
        symbol: str,
        instrument_token: int,
        direction: str,
        entry_price: float,
        initial_sl: float,
        qty: int,
        trigger_pct: float,
        step_pct: float,
    ) -> None:
        """
        ST3: Add trade to tracking. Inserts row in smart_tgt_state.
        Raises ValueError if trade_id already registered.
        No-op when enabled=False.
        """
        if not self._enabled:
            self._log.debug(
                f"SmartTgtManager.register_trade: disabled; {trade_id!r} ignored"
            )
            return

        with self._lock:
            if trade_id in self._tracked:
                raise ValueError(
                    f"trade_id {trade_id!r} already registered in SmartTgtManager"
                )
            self._tracked[trade_id] = {
                "trade_id":             trade_id,
                "symbol":               symbol,
                "instrument_token":     instrument_token,
                "direction":            direction,
                "entry_price":          float(entry_price),
                "initial_sl":           float(initial_sl),
                "current_sl":           float(initial_sl),
                "qty":                  qty,
                "trigger_pct":          float(trigger_pct),
                "step_pct":             float(step_pct),
                "best_price":           None,
                "trail_count":          0,
                "last_trail_ts":        None,
                "consecutive_failures": 0,
            }

        ts = now_ist_iso()
        self._state_store.insert_smart_tgt_state(
            trade_id=trade_id,
            symbol=symbol,
            instrument_token=instrument_token,
            direction=direction,
            entry_price=entry_price,
            initial_sl=initial_sl,
            current_sl=initial_sl,
            qty=qty,
            trigger_pct=trigger_pct,
            step_pct=step_pct,
            registered_at=ts,
        )
        self._log.info(
            f"SmartTgtManager.register_trade: {trade_id} ({symbol}) "
            f"dir={direction} entry={entry_price} sl={initial_sl} "
            f"trigger={trigger_pct:.4f} step={step_pct:.4f}"
        )

    def unregister_trade(self, trade_id: str) -> bool:
        """
        ST3: Remove trade from tracking. Deletes smart_tgt_state row.
        Returns True if found+removed; False if not found (idempotent).
        No-op (returns False) when enabled=False.
        """
        if not self._enabled:
            return False

        with self._lock:
            if trade_id not in self._tracked:
                return False
            del self._tracked[trade_id]

        self._state_store.delete_smart_tgt_state(trade_id)
        self._log.info(f"SmartTgtManager.unregister_trade: {trade_id} removed")
        return True

    def tracked_trade_ids(self) -> List[str]:
        """Return snapshot list of currently tracked trade_ids."""
        with self._lock:
            return list(self._tracked.keys())

    def size(self) -> int:
        """Return number of currently tracked trades."""
        with self._lock:
            return len(self._tracked)

    # ------------------------------------------------------------------
    # Reconnect protocol (ST7, G6, LF7)
    # ------------------------------------------------------------------

    def on_reconnect(self, reconnect_ts: datetime) -> None:
        """
        ST7/ST13: Called by main.py AFTER candle_store.mark_reconnect().
        Recomputes trail ONCE per trade from closed candle history.
        Does NOT re-fire per-bar callbacks for missed bars (G6/LF7).
        """
        if not self._enabled:
            return

        with self._lock:
            trade_ids = list(self._tracked.keys())

        self._log.warning(
            f"SmartTgtManager.on_reconnect at {reconnect_ts.isoformat()}: "
            f"recomputing trail for {len(trade_ids)} trade(s)"
        )

        for trade_id in trade_ids:
            self._recompute_on_reconnect(trade_id)

    # ------------------------------------------------------------------
    # Candle callback (ST6)
    # ------------------------------------------------------------------

    def _on_candle_close(self, candle: Any) -> None:
        """
        ST6: Fired by candle_store for every candle close.
        Filters by instrument_token; processes matching tracked trades.
        """
        if not self._enabled:
            return

        with self._lock:
            matching_ids = [
                tid for tid, info in self._tracked.items()
                if info["instrument_token"] == candle.instrument_token
            ]

        for trade_id in matching_ids:
            self._process_candle(trade_id, candle)

    # ------------------------------------------------------------------
    # Trail algorithm (ST4)
    # ------------------------------------------------------------------

    def _process_candle(self, trade_id: str, candle: Any) -> None:
        """Update best_price from candle; compute and apply trail if advanced."""
        with self._lock:
            info = self._tracked.get(trade_id)
            if info is None:
                return

            # ST4: track most favorable intracandle price
            direction = info["direction"]
            favorable = candle.high if direction == "LONG" else candle.low

            if info["best_price"] is None:
                info["best_price"] = favorable
            elif direction == "LONG":
                info["best_price"] = max(info["best_price"], favorable)
            else:
                info["best_price"] = min(info["best_price"], favorable)

            target_sl = self._compute_target_sl(info)

        if target_sl is not None:
            # B.4 / Audit 5.4: hand off to executor when async_modify is on,
            # otherwise call synchronously (legacy path used by tests).
            if self._async_modify and self._modify_executor is not None:
                self._submit_modify(trade_id, target_sl)
            else:
                self._modify_co_sl(trade_id, target_sl)

    def _submit_modify(self, trade_id: str, target_sl: float) -> None:
        """
        B.4 / Audit 5.4: newest-wins coalesce of modify_order requests.

        Why: candle_store fires _on_candle_close serially across all
        tracked trades. With N trades all needing a trail on the same
        candle close, the legacy synchronous path stacks N * ~100ms HTTP
        calls on the consumer thread, causing later candle closes to back
        up. The executor parallelises 2-wide and the pending dict ensures
        only the latest target SL ever hits the broker per trade.

        Coalesce semantics:
          - _pending_modify[trade_id] is set/overwritten with the newest target.
          - A worker task is submitted on every call. When the task runs it
            atomically pops the current pending value. Older tasks for the
            same trade thus pop nothing (None) and exit cheaply.
          - Net result: O(N) workers spawned, but at most one broker call
            per trade per drain cycle, always for the most recent target.
        """
        with self._pending_lock:
            self._pending_modify[trade_id] = target_sl
        try:
            fut = self._modify_executor.submit(self._drain_modify, trade_id)
            self._inflight_futures.append(fut)
            # Trim completed futures so the list does not grow without bound.
            self._inflight_futures = [
                f for f in self._inflight_futures if not f.done()
            ]
        except RuntimeError:
            # Executor already shut down (stop() was called). Drop the
            # request silently; the modify will replay on next candle
            # close after restart, or be picked up by reconciler.
            with self._pending_lock:
                self._pending_modify.pop(trade_id, None)

    def _drain_modify(self, trade_id: str) -> None:
        """
        B.4 worker: pop the latest pending target and run _modify_co_sl.
        Earlier workers for the same trade_id no-op once the dict is empty.
        """
        with self._pending_lock:
            target_sl = self._pending_modify.pop(trade_id, None)
        if target_sl is None:
            return
        try:
            self._modify_co_sl(trade_id, target_sl)
        except Exception as exc:
            # _modify_co_sl handles its own errors; this is the absolute
            # last-line guard so a worker exception never propagates into
            # the executor and kills the thread.
            self._log.error(
                f"SmartTgtManager._drain_modify: unhandled error for "
                f"{trade_id}: {exc}\n{traceback.format_exc()}"
            )

    def _flush_inflight(self, timeout_sec: float = 5.0) -> None:
        """
        B.4 / Audit 5.4: wait for any in-flight async modify workers to
        finish. Used by stop() to drain before shutdown and by tests to
        synchronise on the executor.
        """
        from concurrent.futures import wait as _futures_wait
        futures = list(self._inflight_futures)
        if futures:
            _futures_wait(futures, timeout=timeout_sec)
        self._inflight_futures = [
            f for f in self._inflight_futures if not f.done()
        ]

    def _compute_target_sl(self, info: dict) -> Optional[float]:
        """
        ST4: Compute desired new SL from current best_price.
        Returns None if no advance needed (not yet at trigger or SL would
        not improve).
        """
        best_price = info["best_price"]
        if best_price is None:
            return None

        entry_price = info["entry_price"]
        if entry_price == 0.0:
            return None

        distance_pct = abs(best_price - entry_price) / entry_price
        trigger_pct = info["trigger_pct"]

        if distance_pct < trigger_pct:
            return None

        step_pct = info["step_pct"]
        if step_pct <= 0.0:
            return None

        steps_count = int((distance_pct - trigger_pct) / step_pct)
        direction = info["direction"]

        if direction == "LONG":
            new_sl = entry_price * (1.0 + trigger_pct + steps_count * step_pct)
            if new_sl <= info["current_sl"]:
                return None
        else:  # SHORT
            new_sl = entry_price * (1.0 - trigger_pct - steps_count * step_pct)
            if new_sl >= info["current_sl"]:
                return None

        return new_sl

    # ------------------------------------------------------------------
    # CO SL modification -- Ghost SL fix (ST5)
    # ------------------------------------------------------------------

    def _modify_co_sl(self, trade_id: str, new_sl: float) -> None:
        """
        ST5: Modify CO trigger_price at broker. Update internal state and DB
        ONLY after broker confirms (Ghost SL fix -- never pre-update state).
        Failure tracked per-trade; 3 consecutive -> CRITICAL + callback.

        Audit #8: round new_sl to tick_size before broker call. Directional
        round (ceil for LONG, floor for SHORT) so tick-snap always tightens
        SL -- never retreats below current_sl. If rounding collapses the
        advance to a no-op, skip the modify silently.
        """
        # ST14: look up CO broker_order_id
        co_row = self._state_store.get_co_entry_order_for_trade(trade_id)
        if co_row is None:
            with self._lock:
                info = self._tracked.get(trade_id)
                if info is not None:
                    info["consecutive_failures"] += 1
            self._log.error(
                f"SmartTgtManager: no CO entry order for {trade_id}; "
                f"cannot trail SL to {new_sl:.4f}"
            )
            return

        co_order_id = co_row["order_id"]

        # Audit #8: snap new_sl to tick_size (directional round)
        with self._lock:
            info = self._tracked.get(trade_id)
            if info is None:
                return
            symbol = info["symbol"]
            direction = info["direction"]
            current_sl = info["current_sl"]
        new_sl = self._round_sl_to_tick(symbol, new_sl, direction)
        if direction == "LONG" and new_sl <= current_sl:
            return
        if direction == "SHORT" and new_sl >= current_sl:
            return

        # D.1: rate-limit modify_order against the "order" bucket. acquire()
        # blocks until a token is available; on timeout (BrokerRateLimitError)
        # we skip this trail tick and let the next candle close retry. The
        # ratchet semantics (LONG SL only moves up, SHORT only down) make
        # skipping a tick safe; new_sl is recomputed next candle anyway.
        if self._rl is not None:
            try:
                self._rl.acquire("order")
            except Exception as rl_exc:
                self._log.warning(
                    f"SmartTgtManager: rate limiter acquire raised for "
                    f"{trade_id}; skipping trail to {new_sl:.4f}: {rl_exc}"
                )
                return

        # Broker call (outside lock -- may be slow)
        try:
            result = self._adapter.modify_order(
                broker_order_id=co_order_id,
                trigger_price=new_sl,
            )
        except Exception as exc:
            self._log.error(
                f"SmartTgtManager: modify_order raised for {trade_id}: "
                f"{exc}\n{traceback.format_exc()}"
            )
            with self._lock:
                info = self._tracked.get(trade_id)
                if info is not None:
                    info["consecutive_failures"] += 1
                    failures = info["consecutive_failures"]
                    symbol = info["symbol"]
                else:
                    return
            self._maybe_fire_critical(trade_id, symbol, failures, str(exc))
            return

        if result.success:
            ts = now_ist_iso()
            with self._lock:
                info = self._tracked.get(trade_id)
                if info is None:
                    return  # race: trade unregistered during broker call
                old_sl = info["current_sl"]
                info["current_sl"] = new_sl
                info["consecutive_failures"] = 0
                info["trail_count"] += 1
                trail_count = info["trail_count"]
                info["last_trail_ts"] = ts
                symbol = info["symbol"]
                best_price = info["best_price"]

            self._log.info(
                f"SmartTgtManager: trailed SL for {trade_id} ({symbol}) "
                f"{old_sl:.4f} -> {new_sl:.4f} (trail #{trail_count})"
            )
            # H-6: DB persist runs AFTER broker modify_order succeeded AND after
            # memory mutation. On DB failure we do NOT roll memory back:
            # broker already has new_sl, and memory must mirror broker to
            # avoid a no-op re-trail on the next candle close. The trail is
            # a ratcheting operation (LONG SL only moves up, SHORT only down),
            # so a post-restart rehydrate from stale DB would at worst cause
            # one wasted broker modify -- never an incorrect SL. The grep tag
            # below (SMART_TGT_TRAIL_DB_PERSIST_FAILED) lets ops notice and
            # investigate before the next restart.
            try:
                self._state_store.update_smart_tgt_state(
                    trade_id=trade_id,
                    current_sl=new_sl,
                    trail_count=trail_count,
                    last_trail_ts=ts,
                    best_price=best_price,
                )
            except Exception as exc:
                self._log.error(
                    "SMART_TGT_TRAIL_DB_PERSIST_FAILED: memory+broker "
                    "remain consistent at new_sl; DB still holds old_sl. "
                    f"trade_id={trade_id} symbol={symbol} "
                    f"old_sl={old_sl:.4f} new_sl={new_sl:.4f} "
                    f"trail_count={trail_count} error={exc}\n"
                    f"{traceback.format_exc()}"
                )
                # Do NOT roll back memory; do NOT re-raise. See comment above.

        else:  # modify failed
            with self._lock:
                info = self._tracked.get(trade_id)
                if info is None:
                    return
                info["consecutive_failures"] += 1
                failures = info["consecutive_failures"]
                symbol = info["symbol"]

            self._log.error(
                f"SmartTgtManager: modify_order failed for {trade_id} ({symbol}): "
                f"{result.reason} "
                f"(failure {failures}/{_MAX_CONSECUTIVE_FAILURES})"
            )
            self._maybe_fire_critical(trade_id, symbol, failures, result.reason)

    def _round_sl_to_tick(self, symbol: str, raw_sl: float, direction: str) -> float:
        """
        Audit #8: snap raw_sl to a valid tick.

        LONG trail tightens SL upward: use ceil so rounded_sl >= raw_sl
        (never weakens the advance). SHORT trail tightens SL downward: use
        floor so rounded_sl <= raw_sl. Returns raw_sl unchanged if no
        instrument_cache is wired or tick lookup fails.
        """
        if self._instrument_cache is None:
            return raw_sl
        try:
            tick = self._instrument_cache.tick_size(symbol)
            if tick <= 0:
                return raw_sl
            if direction == "LONG":
                return round(math.ceil(raw_sl / tick) * tick, 10)
            return round(math.floor(raw_sl / tick) * tick, 10)
        except Exception:
            return raw_sl

    def _maybe_fire_critical(
        self, trade_id: str, symbol: str, failures: int, reason: str
    ) -> None:
        """Fire CRITICAL alert + callback when failure threshold is reached (ST10)."""
        if failures >= _MAX_CONSECUTIVE_FAILURES:
            self._log.critical(
                f"SmartTgtManager: {_MAX_CONSECUTIVE_FAILURES} consecutive "
                f"modify failures for {trade_id} ({symbol}); "
                f"manual intervention required"
            )
            if self._on_critical_failure is not None:
                try:
                    self._on_critical_failure(trade_id, reason)
                except Exception as exc:
                    self._log.error(
                        f"SmartTgtManager: on_critical_failure raised: {exc}"
                    )

    # ------------------------------------------------------------------
    # Reconnect recompute (ST7)
    # ------------------------------------------------------------------

    def _recompute_on_reconnect(self, trade_id: str) -> None:
        """
        ST7: Discard best_price; fetch closed candle history; recompute trail
        ONCE. G6: candle_store.mark_reconnect already discarded partial
        candles so history only contains confirmed closed candles.
        """
        with self._lock:
            info = self._tracked.get(trade_id)
            if info is None:
                return
            token = info["instrument_token"]
            direction = info["direction"]
            # Discard stale best_price -- candle partials already gone (G6)
            info["best_price"] = None

        candles = self._candle_store.get_candles(token, n=_MAX_HISTORY_CANDLES)

        if not candles:
            self._log.warning(
                f"SmartTgtManager: no closed candles for {trade_id} "
                f"after reconnect; skipping recompute"
            )
            return

        # Recompute best_price from history -- single pass (LF7: no re-fire)
        with self._lock:
            info = self._tracked.get(trade_id)
            if info is None:
                return

            if direction == "LONG":
                best = max(c.high for c in candles)
            else:
                best = min(c.low for c in candles)
            info["best_price"] = best

            target_sl = self._compute_target_sl(info)

        if target_sl is not None:
            self._modify_co_sl(trade_id, target_sl)

    # ------------------------------------------------------------------
    # Startup LTP check (ST8)
    # ------------------------------------------------------------------

    def _startup_ltp_check(self, trade_id: str) -> None:
        """
        ST8: On restart, verify LTP is not past the stored SL on the wrong
        side. If quote_fn is None, skip check with a WARNING.
        """
        if self._quote_fn is None:
            self._log.warning(
                f"SmartTgtManager: quote_fn not provided; "
                f"skipping startup LTP check for {trade_id}"
            )
            return

        with self._lock:
            info = self._tracked.get(trade_id)
            if info is None:
                return
            symbol = info["symbol"]
            current_sl = info["current_sl"]
            direction = info["direction"]

        try:
            quotes = self._quote_fn([symbol])
            quote = quotes.get(symbol)
            if quote is None:
                self._log.warning(
                    f"SmartTgtManager: no quote for {symbol} on startup LTP check"
                )
                return
            ltp = float(quote.last_price)
        except Exception as exc:
            self._log.warning(
                f"SmartTgtManager: startup LTP fetch failed for {symbol}: {exc}"
            )
            return

        # G5b-style check: has price already crossed SL on the wrong side?
        wrong_side = (
            (direction == "LONG" and ltp <= current_sl) or
            (direction == "SHORT" and ltp >= current_sl)
        )

        if wrong_side:
            self._log.critical(
                f"SmartTgtManager: {trade_id} ({symbol}) LTP={ltp:.4f} is "
                f"past current_sl={current_sl:.4f} on wrong side; "
                f"flagging for reconciler review -- NOT modifying CO"
            )
            if self._on_critical_failure is not None:
                try:
                    self._on_critical_failure(
                        trade_id,
                        f"startup_ltp_check: LTP={ltp} past SL={current_sl} "
                        f"direction={direction}",
                    )
                except Exception as exc:
                    self._log.error(
                        f"SmartTgtManager: on_critical_failure raised: {exc}"
                    )
        else:
            self._log.info(
                f"SmartTgtManager: {trade_id} ({symbol}) startup check OK; "
                f"LTP={ltp:.4f} vs current_sl={current_sl:.4f}; resuming trail"
            )
