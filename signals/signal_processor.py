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

import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from core.exceptions import BrokerError, BrokerRateLimitError, BrokerTimeoutError
from core.time_authority import ist_timezone, now_ist


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
        in_flight_heartbeat_fn: Optional[Callable[[str], None]] = None,  # FIX-011
        worker_count: int = 5,
        drain_poll_sec: float = 0.1,
        signal_expiry_sec: int = 60,
        instrument_cache=None,              # IC: optional InstrumentCache for lot_size/sector
        atr_fallback_mode: str = "WARN",   # MED #12: "WARN" or "HALT"
        tgt_min_pct: float = 0.003,        # BL-16: guard against degenerate target == entry
        notifier=None,                      # TelegramNotifier; optional
        mode: str = "LIVE",                 # session mode label for alert title
        shadow_tracker=None,                # B.5 / Audit 5.1: ShadowTracker, optional
        rate_limiter=None,                  # FIX-007: optional RateLimiter for order pre-check
        quote_fn=None,                      # FIX-067: quote function for momentum fresh LTP
        strategy_governor=None,             # FIX-130 Item 6: intraday strategy circuit breaker
        perf_weights: Optional[Dict[str, float]] = None,  # FIX-132 Item 9
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
        self._in_flight_heartbeat = in_flight_heartbeat_fn  # FIX-011
        self._worker_count = max(1, worker_count)
        self._drain_poll_sec = drain_poll_sec
        self._signal_expiry_sec = signal_expiry_sec
        self._instrument_cache = instrument_cache  # IC: Module 38
        self._atr_fallback_mode: str = atr_fallback_mode  # MED #12
        self._tgt_min_pct: float = float(tgt_min_pct)     # BL-16
        self._notifier = notifier                          # Telegram alerts (optional)
        self._mode = mode                                  # session mode label
        self._shadow_tracker = shadow_tracker              # B.5 / Audit 5.1
        self._rate_limiter = rate_limiter                  # FIX-007: optional pre-check
        self._quote_fn = quote_fn                          # FIX-067: momentum fresh LTP
        self._strategy_governor = strategy_governor        # FIX-130 Item 6: circuit breaker
        self._perf_weights: Dict[str, float] = dict(perf_weights or {})  # FIX-132 Item 9

        # Lifecycle
        self._running = False
        self._stop_event = threading.Event()
        self._dispatcher_thread: Optional[threading.Thread] = None
        self._executor: Optional[ThreadPoolExecutor] = None

        # Active-worker counter (for stats)
        self._active_workers = 0
        self._active_lock = threading.Lock()

        # FIX-018: TOCTOU fix — in-flight counter for signals between approve() and DB insert
        # Protects against concurrent signals both passing max_open_positions check
        # before either inserts into the in_flight table.
        self._in_flight_count = 0
        self._in_flight_lock = threading.RLock()

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
        self._last_expired_alert_ts: float = 0.0  # FIX-136 Item 51: rate-limit expired alerts

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

        # FIX-100: Shutdown screener's internal step_executor thread pool
        if self._screener is not None and hasattr(self._screener, 'shutdown'):
            self._screener.shutdown()

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
        # FIX-069: Support both tuple and dict signal formats
        if isinstance(signal_tuple, dict):
            signal_id = signal_tuple.get("signal_id", "unknown")
            symbol = signal_tuple.get("symbol", "unknown")
        else:
            signal_id = signal_tuple[0] if signal_tuple else "unknown"
            symbol = signal_tuple[2] if len(signal_tuple) > 2 else "unknown"

        # FIX-007: non-blocking rate-limiter pre-check. If the order token bucket
        # is exhausted, re-enqueue the signal and return immediately rather than
        # blocking the worker thread on adapter.place_order()'s acquire() call.
        if self._rate_limiter is not None:
            if not self._rate_limiter.try_acquire("order"):
                self._log.warning(
                    "rate_limiter: order bucket exhausted for %s (%s); re-queuing",
                    signal_id, symbol,
                )
                # FIX-048: Non-blocking put with timeout to prevent deadlock
                try:
                    self._queue.put(signal_tuple, timeout=1.0)
                except queue.Full:
                    # FIX-165f: Queue at capacity — abandon signal but clean up.
                    scanner_name = signal_tuple[1] if len(signal_tuple) > 1 else "unknown"
                    self._log.warning(
                        f"REJECTED_QUEUE_FULL: {symbol} {scanner_name} - "
                        f"queue at capacity, abandoning signal {signal_id}"
                    )
                    try:
                        self._store.update_signal_status(
                            signal_id, "REJECTED", "QUEUE_FULL"
                        )
                    except Exception:
                        pass
                    if self._in_flight_release is not None:
                        try:
                            self._in_flight_release(symbol)
                        except Exception:
                            pass
                return

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
    # Telegram alert helper (new)
    # ------------------------------------------------------------------

    def _emit_signal_alert(
        self,
        *,
        symbol: str,
        strategy_name: str,
        score,                      # int | None
        entry_price: float,
        sl_price: float,
        tgt_price: float,
        qty: int,
        direction: str,             # "LONG" | "SHORT" | "BUY" | "SELL"
    ) -> None:
        """Send INTRADAY SIGNAL telegram alert (optional; never crash)."""
        if self._notifier is None:
            return
        try:
            risk_amt = abs(float(entry_price) - float(sl_price)) * int(qty)
            capital_at_risk = float(entry_price) * int(qty)
            risk_pct = (risk_amt / capital_at_risk * 100.0) if capital_at_risk else 0.0
            est_profit = abs(float(tgt_price) - float(entry_price)) * int(qty)
            score_str = f"{int(score)}/100" if score is not None else "N/A"
            body = (
                f"Strategy: {strategy_name} | Score: {score_str}\n"
                f"Priority rank: #1 of 1 candidates | SIP: NO\n"
                f"Entry: ₹{float(entry_price):,.2f} (LIMIT) | "
                f"SL: ₹{float(sl_price):,.2f} | "
                f"TGT: ₹{float(tgt_price):,.2f}\n"
                f"Qty: {int(qty)} | Risk: ₹{risk_amt:,.2f} ({risk_pct:.1f}%) | "
                f"Est. net TGT: +₹{est_profit:,.2f}\n"
                f"Smart TGT: enabled | Timeout: 10 min"
            )
            self._notifier.send(
                severity="INFO",
                title=f"[{self._mode}] 🟢 INTRADAY SIGNAL — {symbol}",
                body=body,
                source_module="signal_processor",
            )
        except Exception as exc:
            self._log.error(
                "signal_processor: signal-alert notifier.send failed for %s: %s",
                symbol, exc,
            )

    # ------------------------------------------------------------------
    # Heartbeat helper (FIX-011)
    # ------------------------------------------------------------------

    def _heartbeat(self, symbol: str) -> None:
        """
        FIX-011: Update in-flight heartbeat timestamp at processing checkpoints.
        No-op if heartbeat callback is not wired.
        """
        if self._in_flight_heartbeat is not None:
            try:
                self._in_flight_heartbeat(symbol)
            except Exception as exc:
                self._log.error(f"in_flight_heartbeat failed for {symbol}: {exc}")

    # ------------------------------------------------------------------
    # Core pipeline (SP6, SPW3)
    # ------------------------------------------------------------------

    def _process_one(self, signal_tuple) -> None:
        """
        Run the full processing pipeline for one signal (SP6, SPW3).

        signal_tuple: (signal_id, scanner_name, symbol, trigger_price, triggered_at)
                      OR dict with keys: signal_id, scanner_name, symbol, trigger_price,
                      triggered_at, retry_count (FIX-069)
        triggered_at: naive datetime (IST)
        """
        # FIX-069: Support both tuple and dict formats for backward compatibility
        # and retry metadata tracking
        if isinstance(signal_tuple, dict):
            signal_id = signal_tuple["signal_id"]
            scanner_name = signal_tuple["scanner_name"]
            symbol = signal_tuple["symbol"]
            trigger_price = signal_tuple["trigger_price"]
            triggered_at = signal_tuple["triggered_at"]
            retry_count = signal_tuple.get("retry_count", 0)
        else:
            signal_id, scanner_name, symbol, trigger_price, triggered_at = signal_tuple
            retry_count = 0

        start_mono = time.monotonic()
        reservation_id: Optional[str] = None
        requeued = False  # FIX-069: track if signal was re-queued
        in_flight_incremented = False  # FIX-165c: track whether _in_flight_count was incremented

        try:
            # SP9: mark PROCESSING immediately
            self._store.update_signal_status(signal_id, "PROCESSING")
            self._heartbeat(symbol)  # FIX-011: checkpoint 1

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
            triggered_aware = triggered_at.replace(tzinfo=ist_timezone())
            age_sec = (now - triggered_aware).total_seconds()
            if age_sec > self._signal_expiry_sec:
                raise _PipelineReject(
                    "EXPIRED",
                    f"Signal age {age_sec:.1f}s > expiry {self._signal_expiry_sec}s",
                )

            # B.5 / Audit 5.1: shadow-tracker re-entry guard.
            # symbol_lock blocks re-entry while a real trade is OPEN, but
            # shadow tracking is a second inning on a CLOSED trade -- the
            # symbol_lock has already been released. Without this gate, a
            # new real signal on the same symbol would create overlapping
            # real + simulated positions. is_tracking() is in-memory and
            # cheap (no I/O); fail-open if shadow_tracker is not wired.
            if self._shadow_tracker is not None:
                try:
                    if self._shadow_tracker.is_tracking(symbol):
                        raise _PipelineReject(
                            "SHADOW_INNING_ACTIVE",
                            f"Symbol {symbol} has an active shadow inning; "
                            f"skip new entry to avoid overlapping real+simulated trades",
                        )
                except _PipelineReject:
                    raise
                except Exception as exc:
                    # Defensive: an exception inside is_tracking must NOT
                    # silently approve. Log and fail-closed (skip signal).
                    self._log.error(
                        f"shadow_tracker.is_tracking raised for {symbol}: {exc}; "
                        f"failing closed (skipping signal)"
                    )
                    raise _PipelineReject(
                        "SHADOW_TRACKER_ERROR",
                        f"shadow_tracker.is_tracking raised: {exc}",
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
                else getattr(map_entry, "strategy", str(map_entry))
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

            # CFG-5 (2026-04-26 audit): per-strategy entry-window enforcement.
            # The global window passed above; now check the narrower
            # strategy YAML window (e.g. gap_fade_long cuts off at 11:30).
            if not self._mw.is_entry_allowed_for_strategy(now, strategy_obj):
                raise _PipelineReject(
                    "OUTSIDE_ENTRY_WINDOW",
                    f"Outside per-strategy entry window "
                    f"({strategy_obj.entry_start_time}-{strategy_obj.entry_end_time})",
                )

            # FIX-130 (Item 6): intraday strategy circuit breaker
            if self._strategy_governor is not None:
                paused, pause_reason = self._strategy_governor.check(
                    strategy_name, now.time()
                )
                if paused:
                    raise _PipelineReject("STRATEGY_CIRCUIT_BREAKER", pause_reason)

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
            self._heartbeat(symbol)  # FIX-011: checkpoint 2 (screener done)

            # ----------------------------------------------------------
            # Step 4: Entry + SL price derivation (SPW4)
            # ----------------------------------------------------------
            entry_price, sl_price = self._derive_prices(
                trigger_price, strategy_obj, now_time=now.time()  # FIX-130: gap buffer
            )

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
                    perf_weight=self._perf_weights.get(strategy_obj.name, 1.0),  # FIX-132 Item 9
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("SIZING_BROKER_ERROR", str(be)) from be

            if not sizing.success:
                raise _PipelineReject(f"SIZING_{sizing.constraint}", sizing.reason)
            self._heartbeat(symbol)  # FIX-011: checkpoint 3 (sizing done)

            # ----------------------------------------------------------
            # Steps 6-7: Risk approval + Capital reservation
            # Audit 1.2 / Portfolio Lock: approve + reserve must be a single
            # critical section. Two concurrent signals on the same sector or
            # bucket would otherwise both pass risk_engine.approve (which
            # reads existing exposure) and then both fm.reserve, overshooting
            # max_sector_exposure_pct / max_open_positions. RLock so reserve()
            # re-entering self._fm._lock is safe.
            #
            # FIX-018: TOCTOU fix. Increment _in_flight_count BEFORE approve() so
            # concurrent signals see each other even before they insert into the
            # in_flight DB table. Decrement in finally block (every exit path).
            # ----------------------------------------------------------
            with self._in_flight_lock:
                self._in_flight_count += 1
                in_flight_incremented = True  # FIX-165c
                processor_in_flight = self._in_flight_count

            # FIX-135 Item 42: per-strategy position cap
            max_strat_pos = getattr(strategy_obj, "max_concurrent_positions", 2)
            strat_open = self._store.fetch_one(
                "SELECT COUNT(*) AS n FROM trades WHERE strategy = ? AND status IN ('OPEN', 'PARTIAL')",
                (strategy_name,),
            )
            strat_open_count = int(strat_open["n"]) if strat_open else 0
            if strat_open_count >= max_strat_pos:
                raise _PipelineReject(
                    "STRATEGY_POSITION_LIMIT",
                    f"{strategy_name} has {strat_open_count}/{max_strat_pos} open positions",
                )

            with self._fm.portfolio_lock:
                try:
                    approval = self._risk.approve(
                        symbol, side, strategy_obj.intent, sizing, signal_id,
                        processor_in_flight_count=processor_in_flight
                    )
                except BrokerError as be:
                    if self._ks:
                        self._ks.record_api_failure()
                    raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be

                if not approval.approved:
                    raise _PipelineReject(approval.failed_check, approval.reason)

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

                # SP9: signal reserved (kept inside the lock so the SQL row
                # appears atomically with the reservation entry).
                self._store.update_signal_status(signal_id, "RESERVED")

            self._heartbeat(symbol)  # FIX-011: checkpoint 4 (reservation done)

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

            # FIX-067: Fresh quote for momentum strategies (pullback_wait_enabled=false)
            # Momentum signals process immediately; webhook trigger_price may be stale
            # by the time we reach placement (2+ seconds of pipeline processing).
            # Fetch live LTP to avoid placing LIMIT at stale price into moved market.
            # Pullback strategies (pullback_wait_enabled=true) do NOT use this path
            # (they go through EntryGate which already waits for current price).
            fresh_entry_price = entry_price  # Default: use derived price
            if not strategy_obj.pullback_wait_enabled and self._quote_fn is not None:
                try:
                    quote = self._quote_fn(symbol)
                    live_ltp = quote.get("last_price") if quote else None
                    if live_ltp and live_ltp > 0:
                        price_delta = live_ltp - trigger_price
                        self._log.info(
                            f"FIX-067 momentum fresh quote: {symbol} stale={trigger_price:.2f} "
                            f"live={live_ltp:.2f} delta={price_delta:+.2f}"
                        )
                        # Use live LTP as new anchor for entry price derivation
                        fresh_entry_price, _ = self._derive_prices(
                            live_ltp, strategy_obj, now_time=now.time()
                        )
                    else:
                        self._log.warning(
                            f"FIX-067 momentum fresh quote failed for {symbol}: "
                            f"invalid LTP ({live_ltp}), using stale webhook price"
                        )
                except Exception as exc:
                    self._log.warning(
                        f"FIX-067 momentum fresh quote failed for {symbol}: {exc}, "
                        f"using stale webhook price"
                    )

            # Telegram alert: INTRADAY SIGNAL (fires before order placement)
            self._emit_signal_alert(
                symbol=symbol,
                strategy_name=strategy_name,
                score=screen_result.score,
                entry_price=fresh_entry_price,
                sl_price=sl_price,
                tgt_price=tgt_price,
                qty=sizing.qty,
                direction=strategy_obj.direction,
            )

            self._heartbeat(symbol)  # FIX-011: checkpoint 5 (before placement)

            # FIX-070: Second kill-switch check after pipeline processing.
            # TOCTOU fix: HARD_KILL could fire during steps 2-7 (screening, sizing,
            # reservation). Check again immediately before placement to prevent
            # opening positions after kill-switch activated.
            # Also check shutdown event - system shutdown could have been initiated.
            if self._ks and self._ks.is_active("entry"):
                self._log.warning(
                    f"FIX-070: kill-switch active after pipeline - aborting placement for {signal_id} ({symbol})"
                )
                if reservation_id:
                    self._fm.release(reservation_id, "kill_switch_after_pipeline")
                    reservation_id = None
                raise _PipelineReject("KILL_SWITCH_LATE", "Kill switch active before placement")

            if self._stop_event.is_set():
                self._log.warning(
                    f"FIX-070: shutdown event set - aborting placement for {signal_id} ({symbol})"
                )
                if reservation_id:
                    self._fm.release(reservation_id, "shutdown_before_placement")
                    reservation_id = None
                raise _PipelineReject("SHUTDOWN", "System shutdown before placement")

            try:
                self._placer.place(
                    symbol=symbol,
                    side=side,
                    qty=sizing.qty,
                    entry_price=fresh_entry_price,  # FIX-067: use fresh price
                    sl_price=sl_price,
                    intent=strategy_obj.intent,
                    signal_id=signal_id,
                    reservation_id=reservation_id,
                    strategy=strategy_name,
                    tgt_price=tgt_price,
                    signal_trigger_price=trigger_price,  # FIX-128: for slippage guard
                )
                reservation_id = None   # placer owns it now
            except (BrokerRateLimitError, BrokerTimeoutError) as transient_err:
                # FIX-069: Transient errors during placement -> re-queue with retry limit
                # These errors are recoverable - broker may be temporarily unavailable
                # or rate-limited. Re-queue signal for retry but keep lock held to
                # prevent duplicate admission. Max 3 retries (45s total with 15s delays).
                if self._ks:
                    self._ks.record_api_failure()

                if retry_count >= 3:
                    # Max retries exhausted - mark as failed and release lock
                    self._log.warning(
                        f"FIX-069: signal {signal_id} ({symbol}) abandoned after "
                        f"{retry_count} retries on {type(transient_err).__name__}"
                    )
                    raise  # Let outer exception handler mark PLACEMENT_FAILED

                # Re-queue with incremented retry count
                retry_count += 1
                requeued = True  # Signal finally block to NOT release lock
                self._log.warning(
                    f"FIX-069: re-queuing {signal_id} ({symbol}) due to "
                    f"{type(transient_err).__name__}, retry {retry_count}/3"
                )

                # Build signal dict with retry metadata
                signal_dict = {
                    "signal_id": signal_id,
                    "scanner_name": scanner_name,
                    "symbol": symbol,
                    "trigger_price": trigger_price,
                    "triggered_at": triggered_at,
                    "retry_count": retry_count,
                }

                try:
                    self._queue.put(signal_dict, timeout=1.0)
                    # Release reservation but keep lock - reconciler will retry
                    if reservation_id:
                        self._fm.release(reservation_id, "requeued_transient_error")
                        reservation_id = None
                    return  # Exit without releasing lock (requeued=True)
                except queue.Full:
                    # Queue full - can't retry, must fail and release lock
                    self._log.error(
                        f"FIX-069: queue full, cannot re-queue {signal_id} ({symbol})"
                    )
                    requeued = False  # Force lock release
                    raise  # Let outer handler mark PLACEMENT_FAILED
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
            if rej.check == "EXPIRED" and self._notifier:
                import time as _time
                now_mono = _time.monotonic()
                if now_mono - self._last_expired_alert_ts > 60.0:
                    self._last_expired_alert_ts = now_mono
                    try:
                        self._notifier.send_warning(
                            f"[{self._mode}] Signal EXPIRED: {symbol} age={rej.reason}"
                        )
                    except Exception:
                        pass

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
            # FIX-165c: Only decrement if we actually incremented.
            # Before this fix, early rejections (steps 1-4) decremented without
            # incrementing, driving _in_flight_count negative and disabling the
            # TOCTOU protection.
            if in_flight_incremented:
                try:
                    with self._in_flight_lock:
                        self._in_flight_count -= 1
                except Exception as lock_exc:
                    self._log.error(f"_in_flight_count decrement failed: {lock_exc}")

            # FIX-069: Only release in-flight lock if signal was NOT re-queued.
            # If requeued=True, lock must travel with signal to prevent duplicate
            # admission while signal is pending retry.
            # SP7: ALWAYS release in-flight (audit #21 fix; SPW8: covers screener paths)
            if not requeued and self._in_flight_release is not None:
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

    # Gap-window constants for sl_gap_buffer_pct (FIX-130 Item 4)
    _GAP_WINDOW_START = __import__("datetime").time(9, 15)
    _GAP_WINDOW_END   = __import__("datetime").time(9, 30)

    def _derive_prices(
        self,
        trigger_price: float,
        strategy,
        now_time: Optional[object] = None,  # datetime.time — if provided, enables gap buffer
    ) -> Tuple[float, float]:
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

        FIX-130 (Item 4): if strategy.sl_gap_buffer_pct > 0 and now_time is within
        09:15-09:30 gap window, widen SL by sl_gap_buffer_pct after bounds enforcement.
        LONG: sl moved lower (more room); SHORT: sl moved higher (more room).
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
            # M-4: invalid derived input is a rejection, not a placement failure.
            # Emits REJECTED_INVALID_DERIVED_PRICE rather than PLACEMENT_FAILED.
            raise _PipelineReject(
                "INVALID_DERIVED_PRICE",
                (
                    f"entry_price={entry_price:.4f} <= 0 "
                    f"(trigger={trigger_price}, "
                    f"offset={strategy.entry_offset_pct}, "
                    f"method={strategy.entry_method}). "
                    f"Check strategy.entry_offset_pct < 1.0."
                ),
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
            # E.1 (2026-04-25): explicit reject when sl_pct == 0 in the
            # FIXED_PCT branch. Strategy schema validates sl_pct > 0 only
            # when sl_method=FIXED_PCT in the YAML; ATR strategies declare
            # sl_pct=0.0 (legitimately, since ATR computes the SL). When
            # ATR is unavailable and we fall back to FIXED_PCT, sl_pct is
            # still 0.0 -- the bounds-enforcement step below would silently
            # widen sl_distance to sl_min_pct, masking the missing ATR
            # input. Reject explicitly so the operator notices the YAML
            # is incomplete (e.g. add a non-zero sl_pct fallback for
            # FIXED_PCT, or implement ATR).
            if sl_pct <= 0.0:
                raise _PipelineReject(
                    "ZERO_SL",
                    (
                        f"sl_pct={sl_pct} in FIXED_PCT branch "
                        f"(strategy={strategy.name}, direction={direction}). "
                        f"Likely cause: sl_method=ATR with sl_pct=0.0 fell "
                        f"back to FIXED_PCT and has no usable SL distance. "
                        f"Set a positive sl_pct fallback in the strategy YAML."
                    ),
                )
            if direction == "LONG":
                sl_price = entry_price * (1.0 - sl_pct)
            else:
                sl_price = entry_price * (1.0 + sl_pct)
        else:
            # M-4: unknown sl_method is a configuration error, not a broker
            # failure. Categorize as rejection.
            raise _PipelineReject(
                "INVALID_DERIVED_PRICE",
                f"Unknown sl_method: {sl_method!r}",
            )

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

        # FIX-130 (Item 4): apply SL gap-window buffer during 09:15-09:30.
        # Only active when strategy.sl_gap_buffer_pct > 0 AND now_time is provided
        # AND we're inside the gap risk window. Applied AFTER bounds enforcement
        # so the base SL is already within sl_min/sl_max before widening.
        gap_buffer = float(getattr(strategy, "sl_gap_buffer_pct", 0.0))
        if (
            gap_buffer > 0.0
            and now_time is not None
            and self._GAP_WINDOW_START <= now_time <= self._GAP_WINDOW_END
        ):
            factor = gap_buffer / 100.0
            original_sl = sl_price
            if direction == "LONG":
                sl_price = sl_price * (1.0 - factor)
            else:
                sl_price = sl_price * (1.0 + factor)
            self._log.info(
                f"sl_gap_buffer applied: strategy={strategy.name} "
                f"direction={direction} buffer={gap_buffer}% "
                f"sl {original_sl:.4f} -> {sl_price:.4f}"
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
                tgt_price = entry * (1.0 + tgt_pct)
            else:
                tgt_price = entry * (1.0 - tgt_pct)
        elif tgt_method == "RISK_REWARD":
            sl_distance = abs(entry - sl)
            ratio = float(strategy.tgt_risk_reward)
            if direction == "LONG":
                tgt_price = entry + sl_distance * ratio
            else:
                tgt_price = entry - sl_distance * ratio
        else:
            raise ValueError(f"Unknown tgt_method: {tgt_method!r}")

        # BL-16: guard against degenerate target (e.g. FIXED_PCT with tgt_pct=0.0
        # or RISK_REWARD with zero sl_distance) that would make tgt == entry.
        if entry > 0 and abs(tgt_price - entry) / entry < self._tgt_min_pct:
            raise _PipelineReject(
                "TGT_DISTANCE_TOO_SMALL",
                f"target distance {abs(tgt_price - entry) / entry:.5f} < "
                f"tgt_min_pct {self._tgt_min_pct:.5f} "
                f"(method={tgt_method}, entry={entry}, tgt={tgt_price})",
            )

        return tgt_price

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

    def continue_from_gate(self, entry: object, release_ltp: Optional[float] = None) -> None:
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

        FIX-025: release_ltp is the LTP captured at gate PRICE_HIT time,
        passed to order_placer for slippage protection.
        """
        signal_id = entry.signal_id          # type: ignore[attr-defined]
        symbol    = entry.symbol              # type: ignore[attr-defined]
        start_mono = time.monotonic()
        reservation_id: Optional[str] = None
        in_flight_incremented = False  # FIX-165c (gate path)

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

            # CFG-5 (2026-04-26 audit): per-strategy entry-window enforcement.
            if not self._mw.is_entry_allowed_for_strategy(now, strategy_obj):
                raise _PipelineReject(
                    "OUTSIDE_ENTRY_WINDOW",
                    f"Outside per-strategy entry window "
                    f"({strategy_obj.entry_start_time}-{strategy_obj.entry_end_time})",
                )

            # FIX-166 F06: strategy governor check (mirrors _process_one).
            # Gate-released entries must respect cooldowns and circuit breakers
            # just like direct webhook entries do.
            if self._strategy_governor is not None:
                paused, pause_reason = self._strategy_governor.check(
                    strategy_name, now.time()
                )
                if paused:
                    raise _PipelineReject("STRATEGY_CIRCUIT_BREAKER", pause_reason)

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
                    perf_weight=self._perf_weights.get(strategy_obj.name, 1.0),  # FIX-132 Item 9
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure()
                raise _PipelineReject("SIZING_BROKER_ERROR", str(be)) from be

            if not sizing.success:
                raise _PipelineReject(f"SIZING_{sizing.constraint}", sizing.reason)

            # Steps 6-7: Risk approval + Capital reservation
            # Audit 1.2 / Portfolio Lock: see continue_from_gate counterpart in
            # _process_one for rationale. Same critical section here so
            # gate-released signals do not race against direct webhook signals.
            #
            # FIX-018: TOCTOU fix. Increment _in_flight_count before approve().
            # Decrement in finally block (gate path also counts as in-flight).
            with self._in_flight_lock:
                self._in_flight_count += 1
                processor_in_flight = self._in_flight_count
                in_flight_incremented = True  # FIX-165c (gate path)

            # FIX-135 Item 42: per-strategy position cap (gate path)
            max_strat_pos = getattr(strategy_obj, "max_concurrent_positions", 2)
            strat_open = self._store.fetch_one(
                "SELECT COUNT(*) AS n FROM trades WHERE strategy = ? AND status IN ('OPEN', 'PARTIAL')",
                (strategy_name,),
            )
            strat_open_count = int(strat_open["n"]) if strat_open else 0
            if strat_open_count >= max_strat_pos:
                raise _PipelineReject(
                    "STRATEGY_POSITION_LIMIT",
                    f"{strategy_name} has {strat_open_count}/{max_strat_pos} open positions",
                )

            with self._fm.portfolio_lock:
                try:
                    approval = self._risk.approve(
                        symbol, side, strategy_obj.intent, sizing, signal_id,
                        processor_in_flight_count=processor_in_flight
                    )
                except BrokerError as be:
                    if self._ks:
                        self._ks.record_api_failure()
                    raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be

                if not approval.approved:
                    raise _PipelineReject(approval.failed_check, approval.reason)

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

            # Telegram alert: INTRADAY SIGNAL (gate-release path — score unavailable)
            self._emit_signal_alert(
                symbol=symbol,
                strategy_name=strategy_name,
                score=None,
                entry_price=entry_price,
                sl_price=sl_price,
                tgt_price=tgt_price,
                qty=sizing.qty,
                direction=direction,
            )

            # FIX-165e: Second kill-switch check before placement (gate path).
            # Mirrors FIX-070 in _process_one — TOCTOU: kill could fire during
            # sizing/risk/reservation steps.
            if self._ks and self._ks.is_active("entry"):
                self._log.warning(
                    f"FIX-165e: kill-switch active after gate pipeline - aborting placement for {signal_id} ({symbol})"
                )
                if reservation_id:
                    self._fm.release(reservation_id, "kill_switch_after_gate_pipeline")
                    reservation_id = None
                raise _PipelineReject("KILL_SWITCH_LATE", "Kill switch active before placement (gate)")

            if self._stop_event.is_set():
                self._log.warning(
                    f"FIX-165e: shutdown event set - aborting gate placement for {signal_id} ({symbol})"
                )
                if reservation_id:
                    self._fm.release(reservation_id, "shutdown_before_gate_placement")
                    reservation_id = None
                raise _PipelineReject("SHUTDOWN", "System shutdown before placement (gate)")

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
                    strategy=strategy_name,
                    tgt_price=tgt_price,
                    release_ltp=release_ltp,  # FIX-025
                    signal_trigger_price=entry.trigger_price,  # FIX-128: for slippage guard
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
            # FIX-018 / FIX-102 / FIX-165c: Decrement only if we incremented.
            if in_flight_incremented:
                try:
                    with self._in_flight_lock:
                        self._in_flight_count -= 1
                except Exception as lock_exc:
                    self._log.error(f"_in_flight_count decrement failed (gate): {lock_exc}")

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
