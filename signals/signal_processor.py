"""
signals/signal_processor.py -- Trading System v2

Purpose:
    Worker pool that drains signal_queue and runs each signal through the
    full pipeline: pre-flight -> strategy lookup -> secondary screen ->
    price derivation -> position sizing -> risk approval -> capital reserve
    -> order placement -> status update.

    Owns orchestration only; delegates all decisions to injected modules.
    order_placer may be None (pipeline degrades to PROCESSED_NO_PLACER).

Locked Design Decisions:
    SP1  -- Pure orchestration; no business logic
    SP2  -- ThreadPoolExecutor (audit #29 fix); dispatcher never blocks
    SP3  -- Constructor with all injected deps
    SP4  -- start() / stop() / is_running() lifecycle
    SP5  -- Single dispatcher thread; submits to executor, never processes
    SP6  -- Ordered pipeline with short-circuit on first reject
    SP7  -- in_flight released in finally (audit #21 fix)
    SP8  -- sl_price via FIXED_PCT; ATR = WARNING + fallback
    SP9  -- Signal status written to state_store at each state change
    SP11 -- Thread-safety: all deps are independently thread-safe
    SP12 -- BrokerError -> record_api_failure; all exceptions caught per worker
    SP13 -- stats() metrics: processed, rejected, placed, avg_ms, active, depth
    SP14 -- Layer 5; no direct broker import
    SP16 -- order_placer = None acceptable (Module 33 will wire)

    SPW1 -- Module 30 wiring update: strategies + scan_webhook_map + screener
    SPW2 -- Constructor: strategies/scan_webhook_map/screener/scorer required
    SPW3 -- Pipeline: step2=strategy lookup via map; step3=secondary_screener
    SPW4 -- _derive_prices(): entry offset + SL + bounds enforcement
    SPW5 -- _derive_target(): FIXED_PCT / RISK_REWARD / ATR fallback
    SPW6 -- order_placer.place() receives tgt_price
    SPW7 -- P18 compliance: screener writes PASSED/REJECTED; processor does not
    SPW8 -- in_flight via finally on all paths (screener SKIPPED/REJECTED too)
    SPW9 -- stats() extended with screener outcome metrics

What This Module Does NOT Do:
    - Does not implement quality scoring (quality_scorer handles that inside screener)
    - Does not implement ATR-based stop-loss (uses FIXED_PCT fallback)
    - Does not place orders (order_placer module, wired in Module 33)
    - Does not manage the HTTP server or receive signals (webhook_receiver)
"""
from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Tuple

from core.exceptions import BrokerError
from core.time_authority import now_ist

_IST = timezone(timedelta(hours=5, minutes=30), name="IST")


# ---------------------------------------------------------------------------
# Internal control-flow exception for clean pipeline rejection
# ---------------------------------------------------------------------------

class _PipelineReject(Exception):
    """Raised inside _process_one to cleanly short-circuit the pipeline."""

    def __init__(self, check: str, reason: str) -> None:
        super().__init__(reason)
        self.check = check   # used as REJECTED_<check> suffix
        self.reason = reason


# ---------------------------------------------------------------------------
# SignalProcessor
# ---------------------------------------------------------------------------

class SignalProcessor:
    """
    Drains signal_queue with a ThreadPoolExecutor worker pool and runs each
    signal through the processing pipeline (SP1-SP16, SPW1-SPW10).

    Usage::
        proc = SignalProcessor(
            signal_queue=sq,
            state_store=store,
            bus=event_bus,
            fund_manager=fm,
            position_sizer=sizer,
            risk_engine=re,
            kill_switch=ks,
            market_windows=mw,
            strategies=strategy_dict,         # from load_all_strategies()
            scan_webhook_map=webhook_map,      # {scanner: {"strategy": name}}
            secondary_screener=screener,
            quality_scorer=scorer,             # kept for logging/metrics
            order_placer=None,                 # wired in Module 33
            logger=log,
            in_flight_release_fn=receiver.release_in_flight,
        )
        proc.start()
        # ... run ...
        proc.stop()
    """

    def __init__(
        self,
        signal_queue,                       # queue.Queue from receiver
        state_store,                        # StateStore
        bus,                                # EventBus
        fund_manager,                       # FundManager
        position_sizer,                     # PositionSizer
        risk_engine,                        # RiskEngine
        kill_switch,                        # KillSwitch
        market_windows,                     # MarketWindows
        strategies: Dict[str, Any],         # SPW2: {strategy_name: StrategyConfig}
        scan_webhook_map: Dict[str, Any],   # SPW2: {scanner_name: {"strategy": name}}
        secondary_screener,                 # SPW2: required (was None)
        quality_scorer,                     # SPW2: required, kept for logging/metrics
        order_placer=None,                  # SP16: still optional (Module 33)
        logger=None,
        in_flight_release_fn: Optional[Callable[[str], None]] = None,  # SP7
        worker_count: int = 5,
        drain_poll_sec: float = 0.1,
        signal_expiry_sec: int = 60,
        instrument_cache=None,              # IC: optional InstrumentCache for lot_size/sector
        atr_fallback_mode: str = "WARN",   # MED #12: "WARN" or "HALT"
    ) -> None:
        self._queue = signal_queue
        self._store = state_store
        self._bus = bus
        self._fm = fund_manager
        self._sizer = position_sizer
        self._risk = risk_engine
        self._ks = kill_switch
        self._mw = market_windows
        self._strategies: Dict[str, Any] = dict(strategies)
        self._scan_webhook_map: Dict[str, Any] = dict(scan_webhook_map)
        self._screener = secondary_screener
        self._scorer = quality_scorer
        self._placer = order_placer
        self._log = logger
        self._in_flight_release = in_flight_release_fn
        self._worker_count = max(1, worker_count)
        self._drain_poll_sec = drain_poll_sec
        self._signal_expiry_sec = signal_expiry_sec
        self._instrument_cache = instrument_cache  # IC: Module 38
        self._atr_fallback_mode: str = atr_fallback_mode  # MED #12

        # Lifecycle
        self._running = False
        self._stop_event = threading.Event()
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None

        # Active-worker counter (for stats)
        self._active_workers = 0
        self._active_lock = threading.Lock()

        # Stats (SP13 + SPW9)
        self._stats: Dict[str, Any] = {
            "processed": 0,
            "rejected": {},
            "placed": 0,
            "total_ms": 0.0,
            "processed_no_placer": 0,
            "screener_passed": 0,
            "screener_rejected": {},   # status -> count
            "screener_skipped": {},    # status -> count
            "screener_total_ms": 0.0,
        }
        self._stats_lock = threading.Lock()

        if order_placer is None:
            self._log.info("SignalProcessor: order_placer=None; pipeline stops at PROCESSED_NO_PLACER")

    # ------------------------------------------------------------------
    # Lifecycle (SP4)
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch dispatcher thread and executor worker pool."""
        if self._running:
            self._log.warning("SignalProcessor.start() called while already running")
            return
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self._worker_count,
            thread_name_prefix="sp-worker",
        )
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher_loop,
            name="sp-dispatcher",
            daemon=True,
        )
        self._running = True
        self._dispatcher_thread.start()
        self._log.info(
            f"SignalProcessor started: {self._worker_count} workers, "
            f"drain_poll={self._drain_poll_sec}s"
        )

    def stop(self) -> None:
        """
        Signal shutdown, drain remaining queue items, wait for in-flight
        tasks to complete (up to 5s per SP4).
        """
        if not self._running:
            return
        self._stop_event.set()

        if self._dispatcher_thread is not None:
            self._dispatcher_thread.join(timeout=10.0)

        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._executor = None

        self._running = False
        self._log.info("SignalProcessor stopped")

    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Dispatcher loop (SP5)
    # ------------------------------------------------------------------

    def _dispatcher_loop(self) -> None:
        """
        Single dispatcher thread.  Pulls signal tuples from the queue and
        hands each to the executor.  Never processes signals itself (SP5).
        After stop_event is set, drains any remaining queue items before exiting.
        """
        while not self._stop_event.is_set():
            try:
                signal_tuple = self._queue.get(timeout=self._drain_poll_sec)
            except Exception:  # queue.Empty
                continue
            if self._executor is not None:
                self._executor.submit(self._process_one_safe, signal_tuple)

        # Drain remaining items after stop signal so queued work is not silently lost
        while True:
            try:
                signal_tuple = self._queue.get_nowait()
                if self._executor is not None:
                    self._executor.submit(self._process_one_safe, signal_tuple)
            except Exception:
                break

    # ------------------------------------------------------------------
    # Safe wrapper -- catches all exceptions so workers never die (SP12)
    # ------------------------------------------------------------------

    def _process_one_safe(self, signal_tuple) -> None:
        """Wraps _process_one; catches all exceptions so workers never die."""
        signal_id = signal_tuple[0] if signal_tuple else "unknown"
        symbol = signal_tuple[2] if len(signal_tuple) > 2 else "unknown"

        with self._active_lock:
            self._active_workers += 1
        try:
            self._process_one(signal_tuple)
        except Exception as exc:
            self._log.error(
                f"Unhandled exception in pipeline for {signal_id} ({symbol}): "
                f"{exc}\n{traceback.format_exc()}"
            )
            try:
                self._store.update_signal_status(
                    signal_id, "PLACEMENT_FAILED", f"unhandled: {exc}"
                )
            except Exception:
                pass
        finally:
            with self._active_lock:
                self._active_workers -= 1

    # ------------------------------------------------------------------
    # Core pipeline (SP6, SPW3)
    # ------------------------------------------------------------------

    def _process_one(self, signal_tuple) -> None:
        """
        Run the full processing pipeline for one signal (SP6, SPW3).

        signal_tuple: (signal_id, scanner_name, symbol, trigger_price, triggered_at)
        triggered_at: naive datetime (IST)
        """
        signal_id, scanner_name, symbol, trigger_price, triggered_at = signal_tuple

        start_mono = time.monotonic()
        reservation_id: Optional[str] = None

        try:
            # SP9: mark PROCESSING immediately
            self._store.update_signal_status(signal_id, "PROCESSING")

            # ----------------------------------------------------------
            # Step 1: Pre-flight checks (cheap, no I/O)
            # ----------------------------------------------------------

            # Kill switch
            if self._ks and self._ks.is_active("entry"):
                raise _PipelineReject("KILL_SWITCH", "Kill switch is active")

            # Market window
            now = now_ist()
            if not self._mw.is_entry_allowed(now):
                raise _PipelineReject("OUTSIDE_ENTRY_WINDOW", "Outside entry window")

            # Signal age (defense-in-depth; receiver also checks)
            triggered_aware = triggered_at.replace(tzinfo=_IST)
            age_sec = (now - triggered_aware).total_seconds()
            if age_sec > self._signal_expiry_sec:
                raise _PipelineReject(
                    "EXPIRED",
                    f"Signal age {age_sec:.1f}s > expiry {self._signal_expiry_sec}s",
                )

            # ----------------------------------------------------------
            # Step 2: Strategy lookup (SPW3)
            # ----------------------------------------------------------
            map_entry = self._scan_webhook_map.get(scanner_name)
            if map_entry is None:
                raise _PipelineReject(
                    "UNKNOWN_STRATEGY",
                    f"No entry in scan_webhook_map for scanner {scanner_name!r}",
                )
            strategy_name = (
                map_entry.get("strategy") if isinstance(map_entry, dict)
                else str(map_entry)
            )
            if strategy_name is None:
                raise _PipelineReject(
                    "UNKNOWN_STRATEGY",
                    f"scan_webhook_map entry for {scanner_name!r} has no 'strategy' key",
                )
            strategy_obj = self._strategies.get(strategy_name)
            if strategy_obj is None:
                raise _PipelineReject(
                    "UNKNOWN_STRATEGY",
                    f"Strategy {strategy_name!r} not in loaded strategies",
                )

            # ----------------------------------------------------------
            # Step 3: Secondary screening (SPW3, P18)
            # Screener writes signal status (PASSED / REJECTED_<step>).
            # Processor does NOT double-write those statuses.
            # ----------------------------------------------------------
            t_screen = time.monotonic()
            screen_result = self._screener.screen(
                signal_id, symbol, scanner_name, trigger_price, triggered_at,
                direction=strategy_obj.direction,
                intent=strategy_obj.intent,
                strategy=strategy_obj,
                market_data=None,
            )
            screen_ms = (time.monotonic() - t_screen) * 1000
            self._record_screener_stats(screen_result, screen_ms)

            if screen_result.status.startswith("SKIPPED_"):
                # Quote unavailable or executor error; screener wrote status
                self._log.warning(
                    f"Signal {signal_id} ({symbol}) screener SKIPPED: {screen_result.status}"
                )
                return  # finally releases in_flight (SPW8)

            if not screen_result.passed:
                # Step-level or score rejection; screener wrote status
                self._log.info(
                    f"Signal {signal_id} ({symbol}) screener rejected: {screen_result.status}"
                )
                return  # finally releases in_flight (SPW8)

            # screen_result.passed=True; screener wrote PASSED

            # ----------------------------------------------------------
            # Step 4: Entry + SL price derivation (SPW4)
            # ----------------------------------------------------------
            entry_price, sl_price = self._derive_prices(trigger_price, strategy_obj)

            # SPW4: convert strategy direction ("LONG"/"SHORT") to order side
            # ("BUY"/"SELL") for downstream modules (position_sizer, order_placer).
            _dir = strategy_obj.direction
            side = "BUY" if _dir in ("LONG", "BUY") else "SELL"

            # ----------------------------------------------------------
            # Step 5: Position sizing
            # ----------------------------------------------------------
            try:
                sizing = self._sizer.calculate(
                    symbol,
                    side,
                    entry_price,
                    sl_price,
                    strategy_obj.intent,
                    screen_result.tier,
                    strategy_obj.lot_size,
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("SIZING_BROKER_ERROR", str(be)) from be

            if not sizing.success:
                raise _PipelineReject(f"SIZING_{sizing.constraint}", sizing.reason)

            # ----------------------------------------------------------
            # Step 6: Risk approval
            # ----------------------------------------------------------
            try:
                approval = self._risk.approve(
                    symbol, side, strategy_obj.intent, sizing, signal_id
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be

            if not approval.approved:
                raise _PipelineReject(approval.failed_check, approval.reason)

            # ----------------------------------------------------------
            # Step 7: Capital reservation
            # ----------------------------------------------------------
            try:
                reservation = self._fm.reserve(
                    symbol, sizing.qty, entry_price, strategy_obj.intent, signal_id
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("RESERVE_BROKER_ERROR", str(be)) from be

            if not reservation.success:
                raise _PipelineReject("RESERVE_FAILED", reservation.reason_if_failed)

            reservation_id = reservation.reservation_id

            # SP9: signal reserved
            self._store.update_signal_status(signal_id, "RESERVED")

            # ----------------------------------------------------------
            # Step 8: Order placement (SP16: optional)
            # ----------------------------------------------------------
            if self._placer is None:
                self._log.info(
                    f"order_placer=None; releasing reservation {reservation_id} for {signal_id}"
                )
                try:
                    self._fm.release(reservation_id, "no_order_placer")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {reservation_id}: {rel_exc}")
                reservation_id = None
                self._store.update_signal_status(signal_id, "PROCESSED_NO_PLACER")
                with self._stats_lock:
                    self._stats["processed_no_placer"] += 1
                return

            # Derive target price (SPW5, SPW6)
            tgt_price = self._derive_target(entry_price, sl_price, strategy_obj)

            try:
                self._placer.place(
                    symbol=symbol,
                    side=side,
                    qty=sizing.qty,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    intent=strategy_obj.intent,
                    signal_id=signal_id,
                    reservation_id=reservation_id,
                    tgt_price=tgt_price,
                )
                reservation_id = None   # placer owns it now
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise  # caught by outer except below
            except Exception:
                raise  # caught by outer except below

            # ----------------------------------------------------------
            # Step 9: Success (SPW7: only post-screen statuses here)
            # ----------------------------------------------------------
            self._store.update_signal_status(signal_id, "PROCESSED")
            with self._stats_lock:
                self._stats["processed"] += 1
                self._stats["placed"] += 1

        except _PipelineReject as rej:
            self._log.info(
                f"Signal {signal_id} ({symbol}) rejected at {rej.check}: {rej.reason}"
            )
            self._store.update_signal_status(signal_id, f"REJECTED_{rej.check}", rej.reason)
            # Release reservation if we had one
            if reservation_id:
                try:
                    self._fm.release(reservation_id, f"rejected_{rej.check.lower()}")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {reservation_id}: {rel_exc}")
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket[rej.check] = bucket.get(rej.check, 0) + 1

        except Exception as exc:
            # Placement failure or unexpected exception
            self._log.error(
                f"Pipeline exception for {signal_id} ({symbol}): {exc}\n{traceback.format_exc()}"
            )
            self._store.update_signal_status(signal_id, "PLACEMENT_FAILED", str(exc))
            if reservation_id:
                try:
                    self._fm.release(reservation_id, "placement_failed")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {reservation_id}: {rel_exc}")
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket["PLACEMENT_FAILED"] = bucket.get("PLACEMENT_FAILED", 0) + 1

        finally:
            # SP7: ALWAYS release in-flight (audit #21 fix; SPW8: covers screener paths)
            if self._in_flight_release is not None:
                try:
                    self._in_flight_release(symbol)
                except Exception as rel_exc:
                    self._log.error(f"in_flight_release failed for {symbol}: {rel_exc}")

            elapsed_ms = (time.monotonic() - start_mono) * 1000
            with self._stats_lock:
                self._stats["total_ms"] += elapsed_ms

    # ------------------------------------------------------------------
    # Price derivation (SPW4)
    # ------------------------------------------------------------------

    def _derive_prices(self, trigger_price: float, strategy) -> Tuple[float, float]:
        """
        Derive entry price and SL price from strategy config (SPW4).

        Entry:
            MARKET: entry = trigger_price
            LIMIT:  LONG  -> entry = trigger * (1 - entry_offset_pct)
                    SHORT -> entry = trigger * (1 + entry_offset_pct)

        SL:
            FIXED_PCT: LONG  -> sl = entry * (1 - sl_pct)
                       SHORT -> sl = entry * (1 + sl_pct)
            ATR: falls back to FIXED_PCT with WARNING

        Bounds:
            If sl_distance_pct < sl_min_pct: adjust sl + WARNING
            If sl_distance_pct > sl_max_pct: adjust sl + WARNING
        """
        direction = strategy.direction  # "LONG" or "SHORT"

        # --- Entry price ---
        entry_price = trigger_price
        if strategy.entry_method == "LIMIT":
            offset = float(strategy.entry_offset_pct)
            if direction == "LONG":
                entry_price = trigger_price * (1.0 - offset)
            else:
                entry_price = trigger_price * (1.0 + offset)

        if entry_price <= 0:
            raise ValueError(
                f"_derive_prices: entry_price={entry_price:.4f} <= 0 "
                f"(trigger={trigger_price}, offset={strategy.entry_offset_pct}, "
                f"method={strategy.entry_method}). "
                f"Check strategy.entry_offset_pct < 1.0."
            )

        # --- SL price ---
        sl_method = strategy.sl_method  # "FIXED_PCT" or "ATR"
        if sl_method == "ATR":
            if self._atr_fallback_mode == "HALT":
                raise _PipelineReject(
                    "REJECTED_NO_ATR_DATA",
                    "sl_method=ATR not implemented and atr_fallback_mode=HALT",
                )
            self._log.warning(
                "sl_method=ATR not yet implemented; falling back to FIXED_PCT"
            )
            sl_method = "FIXED_PCT"

        if sl_method == "FIXED_PCT":
            sl_pct = float(strategy.sl_pct)
            if direction == "LONG":
                sl_price = entry_price * (1.0 - sl_pct)
            else:
                sl_price = entry_price * (1.0 + sl_pct)
        else:
            raise ValueError(f"Unknown sl_method: {sl_method!r}")

        # --- Bounds enforcement ---
        sl_distance = abs(entry_price - sl_price)
        sl_distance_pct = sl_distance / entry_price if entry_price != 0 else 0.0

        if sl_distance_pct < strategy.sl_min_pct:
            self._log.warning(
                f"SL distance {sl_distance_pct:.4f} < sl_min_pct {strategy.sl_min_pct:.4f}; "
                f"adjusting for {strategy.direction}"
            )
            adj_dist = entry_price * strategy.sl_min_pct
            sl_price = (
                entry_price - adj_dist if direction == "LONG"
                else entry_price + adj_dist
            )
        elif sl_distance_pct > strategy.sl_max_pct:
            self._log.warning(
                f"SL distance {sl_distance_pct:.4f} > sl_max_pct {strategy.sl_max_pct:.4f}; "
                f"adjusting for {strategy.direction}"
            )
            adj_dist = entry_price * strategy.sl_max_pct
            sl_price = (
                entry_price - adj_dist if direction == "LONG"
                else entry_price + adj_dist
            )

        return entry_price, sl_price

    # ------------------------------------------------------------------
    # Target price derivation (SPW5)
    # ------------------------------------------------------------------

    def _derive_target(self, entry: float, sl: float, strategy) -> float:
        """
        Derive target price from strategy config (SPW5).

        FIXED_PCT:    LONG  -> tgt = entry * (1 + tgt_pct)
                      SHORT -> tgt = entry * (1 - tgt_pct)
        RISK_REWARD:  sl_distance * ratio from entry
        ATR:          falls back to FIXED_PCT with WARNING
        """
        direction = strategy.direction
        tgt_method = strategy.tgt_method  # "FIXED_PCT" | "RISK_REWARD" | "ATR"

        if tgt_method == "ATR":
            if self._atr_fallback_mode == "HALT":
                raise _PipelineReject(
                    "REJECTED_NO_ATR_DATA",
                    "tgt_method=ATR not implemented and atr_fallback_mode=HALT",
                )
            self._log.warning(
                "tgt_method=ATR not yet implemented; falling back to FIXED_PCT"
            )
            tgt_method = "FIXED_PCT"

        if tgt_method == "FIXED_PCT":
            tgt_pct = float(strategy.tgt_pct)
            if direction == "LONG":
                return entry * (1.0 + tgt_pct)
            else:
                return entry * (1.0 - tgt_pct)

        if tgt_method == "RISK_REWARD":
            sl_distance = abs(entry - sl)
            ratio = float(strategy.tgt_risk_reward)
            if direction == "LONG":
                return entry + sl_distance * ratio
            else:
                return entry - sl_distance * ratio

        raise ValueError(f"Unknown tgt_method: {tgt_method!r}")

    # ------------------------------------------------------------------
    # Screener stats helper (SPW9)
    # ------------------------------------------------------------------

    def _record_screener_stats(self, result, screen_ms: float) -> None:
        """Update screener outcome metrics (SPW9)."""
        with self._stats_lock:
            self._stats["screener_total_ms"] += screen_ms
            if result.status.startswith("SKIPPED_"):
                bucket = self._stats["screener_skipped"]
                bucket[result.status] = bucket.get(result.status, 0) + 1
            elif not result.passed:
                bucket = self._stats["screener_rejected"]
                bucket[result.status] = bucket.get(result.status, 0) + 1
            else:
                self._stats["screener_passed"] += 1

    # ------------------------------------------------------------------
    # Gate-release pipeline entry point (MAIN18)
    # ------------------------------------------------------------------

    def continue_from_gate(self, entry: object) -> None:
        """
        Resume post-screening pipeline for a WatchEntry released by EntryGate
        with reason PRICE_HIT (MAIN18).

        The WatchEntry already carries derived prices (entry_price, sl_price,
        tgt_price), so Steps 1-4 of _process_one (screening + price derivation)
        are bypassed.  Kill-switch and market-window are re-checked as a
        last-mile gate.  in_flight tracking is NOT released here -- the
        EntryGate already manages it.

        ``entry`` is typed as object to avoid a circular import; callers pass a
        WatchEntry instance (screening.entry_gate.WatchEntry).
        """
        signal_id = entry.signal_id          # type: ignore[attr-defined]
        symbol    = entry.symbol              # type: ignore[attr-defined]
        start_mono = time.monotonic()
        reservation_id: Optional[str] = None

        try:
            self._store.update_signal_status(signal_id, "PROCESSING")

            # Last-mile kill-switch check
            if self._ks and self._ks.is_active("entry"):
                raise _PipelineReject("KILL_SWITCH", "Kill switch is active")

            # Last-mile market-window check
            now = now_ist()
            if not self._mw.is_entry_allowed(now):
                raise _PipelineReject("OUTSIDE_ENTRY_WINDOW", "Outside entry window")

            # Strategy lookup (for intent, lot_size)
            strategy_name = entry.strategy_name  # type: ignore[attr-defined]
            strategy_obj = self._strategies.get(strategy_name)
            if strategy_obj is None:
                raise _PipelineReject(
                    "UNKNOWN_STRATEGY",
                    f"Strategy {strategy_name!r} not in loaded strategies",
                )

            entry_price = entry.entry_price  # type: ignore[attr-defined]
            sl_price    = entry.sl_price     # type: ignore[attr-defined]
            tgt_price   = entry.tgt_price    # type: ignore[attr-defined]
            direction   = entry.direction    # type: ignore[attr-defined]
            tier        = entry.tier         # type: ignore[attr-defined]

            # SPW4 (gate path): convert "LONG"/"SHORT" -> "BUY"/"SELL"
            # WatchEntry.direction is always "LONG"/"SHORT"; sizer/risk/placer
            # expect "BUY"/"SELL". Same conversion as _process_one (line ~382).
            side = "BUY" if direction in ("LONG", "BUY") else "SELL"

            # Step 5: Position sizing (prices already in WatchEntry)
            try:
                sizing = self._sizer.calculate(
                    symbol,
                    side,
                    entry_price,
                    sl_price,
                    strategy_obj.intent,
                    tier,
                    strategy_obj.lot_size,
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("SIZING_BROKER_ERROR", str(be)) from be

            if not sizing.success:
                raise _PipelineReject(f"SIZING_{sizing.constraint}", sizing.reason)

            # Step 6: Risk approval
            try:
                approval = self._risk.approve(
                    symbol, side, strategy_obj.intent, sizing, signal_id
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be

            if not approval.approved:
                raise _PipelineReject(approval.failed_check, approval.reason)

            # Step 7: Capital reservation
            try:
                reservation = self._fm.reserve(
                    symbol, sizing.qty, entry_price, strategy_obj.intent, signal_id
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("RESERVE_BROKER_ERROR", str(be)) from be

            if not reservation.success:
                raise _PipelineReject("RESERVE_FAILED", reservation.reason_if_failed)

            reservation_id = reservation.reservation_id
            self._store.update_signal_status(signal_id, "RESERVED")

            # Step 8: Order placement (optional)
            if self._placer is None:
                self._log.info(
                    f"order_placer=None; releasing reservation {reservation_id} "
                    f"for gate signal {signal_id}"
                )
                try:
                    self._fm.release(reservation_id, "no_order_placer")
                except Exception as rel_exc:
                    self._log.error(
                        f"Failed to release reservation {reservation_id}: {rel_exc}"
                    )
                reservation_id = None
                self._store.update_signal_status(signal_id, "PROCESSED_NO_PLACER")
                with self._stats_lock:
                    self._stats["processed_no_placer"] += 1
                return

            try:
                self._placer.place(
                    symbol=symbol,
                    side=side,
                    qty=sizing.qty,
                    entry_price=entry_price,
                    sl_price=sl_price,
                    intent=strategy_obj.intent,
                    signal_id=signal_id,
                    reservation_id=reservation_id,
                    tgt_price=tgt_price,
                )
                reservation_id = None   # placer owns it now
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise
            except Exception:
                raise

            self._store.update_signal_status(signal_id, "PROCESSED")
            with self._stats_lock:
                self._stats["processed"] += 1
                self._stats["placed"] += 1

        except _PipelineReject as rej:
            self._log.info(
                f"Gate signal {signal_id} ({symbol}) rejected at {rej.check}: {rej.reason}"
            )
            self._store.update_signal_status(
                signal_id, f"REJECTED_{rej.check}", rej.reason
            )
            if reservation_id:
                try:
                    self._fm.release(reservation_id, f"rejected_{rej.check.lower()}")
                except Exception as rel_exc:
                    self._log.error(
                        f"Failed to release reservation {reservation_id}: {rel_exc}"
                    )
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket[rej.check] = bucket.get(rej.check, 0) + 1

        except Exception as exc:
            self._log.error(
                f"Pipeline exception for gate signal {signal_id} ({symbol}): "
                f"{exc}\n{traceback.format_exc()}"
            )
            self._store.update_signal_status(signal_id, "PLACEMENT_FAILED", str(exc))
            if reservation_id:
                try:
                    self._fm.release(reservation_id, "placement_failed")
                except Exception as rel_exc:
                    self._log.error(
                        f"Failed to release reservation {reservation_id}: {rel_exc}"
                    )
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket["PLACEMENT_FAILED"] = bucket.get("PLACEMENT_FAILED", 0) + 1

        finally:
            elapsed_ms = (time.monotonic() - start_mono) * 1000
            with self._stats_lock:
                self._stats["total_ms"] += elapsed_ms

    # ------------------------------------------------------------------
    # Metrics (SP13, SPW9)
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """
        Return a snapshot of processing metrics (SP13, SPW9).

        Keys:
            signals_processed:         total that reached PROCESSED or PROCESSED_NO_PLACER
            signals_rejected:          dict[reason, count]  (pre/post-screen rejections)
            signals_placed:            subset of processed that reached order placement
            avg_pipeline_ms:           mean pipeline duration
            workers_active:            currently executing pipeline workers
            queue_depth:               current signal_queue size
            signals_screened_passed:   screener PASSED count
            signals_screened_rejected: dict[status, count]  (REJECTED_<step>)
            signals_screened_skipped:  dict[status, count]  (SKIPPED_<reason>)
            avg_screening_ms:          mean screener call duration
        """
        with self._stats_lock:
            total_done = self._stats["processed"] + self._stats["processed_no_placer"]
            total_ms = self._stats["total_ms"]
            screener_total = (
                self._stats["screener_passed"]
                + sum(self._stats["screener_rejected"].values())
                + sum(self._stats["screener_skipped"].values())
            )
            screener_ms = self._stats["screener_total_ms"]
            snap = {
                "signals_processed": total_done,
                "signals_rejected": dict(self._stats["rejected"]),
                "signals_placed": self._stats["placed"],
                "avg_pipeline_ms": total_ms / total_done if total_done > 0 else 0.0,
                "signals_screened_passed": self._stats["screener_passed"],
                "signals_screened_rejected": dict(self._stats["screener_rejected"]),
                "signals_screened_skipped": dict(self._stats["screener_skipped"]),
                "avg_screening_ms": screener_ms / screener_total if screener_total > 0 else 0.0,
            }
        with self._active_lock:
            active = self._active_workers
        snap["workers_active"] = active
        snap["queue_depth"] = self._queue.qsize()
        return snap
