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
from strategies.control import (  # Slice 2: strategy-control gate
    strategy_will_trade,
    CAUSE_TRADE_TYPE,  # SLICE2.5-PHASE-4: promote a trade_type mismatch to its own label
)


# ---------------------------------------------------------------------------
# Internal control-flow exception for clean pipeline rejection
# ---------------------------------------------------------------------------

class _PipelineReject(Exception):
    """Raised inside _process_one to cleanly short-circuit the pipeline."""

    def __init__(self, check: str, reason: str) -> None:
        super().__init__(reason)
        self.check = check   # used as REJECTED_<check> suffix
        self.reason = reason


class _AdmitCtx:
    """V3 03.05: mutable lifecycle holder threaded through the extracted ADMIT callable
    (_admit_and_place). Carries the reservation/in-flight/requeue/placed state so the
    caller's except+finally see it on EVERY exit path (normal, early-return, and raise) —
    this is what preserves the pre-extraction behaviour byte-for-byte (esp. the queue.Full
    reservation-leak guard, where reservation_id must remain visible to the outer handler)."""
    __slots__ = ("reservation_id", "requeued", "in_flight_incremented", "placed")

    def __init__(self) -> None:
        self.reservation_id = None            # type: Optional[str]
        self.requeued = False                 # FIX-069
        self.in_flight_incremented = False    # FIX-165c
        self.placed = False                   # True once order_placer.place() succeeded


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
        min_gap_between_entries_sec: float = 0.0,  # Bug G: entry throttle (global)
        entry_burst_window_sec: float = 60.0,      # Bug G
        entry_burst_max: int = 0,                  # Bug G: 0 = off
        per_symbol_cooldown_sec: float = 0.0,      # Bug G (full): per-symbol cooldown
        notifier=None,                      # TelegramNotifier; optional
        mode: str = "LIVE",                 # session mode label for alert title
        shadow_tracker=None,                # B.5 / Audit 5.1: ShadowTracker, optional
        sr_detector=None,                   # SNR-DETECTOR-V1: async non-gating S&R observer, optional
        zone_warmer=None,                   # SNR-V2: ZoneWarmer — enqueue symbol on signal arrival
        retest_sl_buffer_pct: float = 0.2,  # SNR-V2: structure SL = band_low − this% (continue_from_retest)
        rate_limiter=None,                  # FIX-007: optional RateLimiter for order pre-check
        quote_fn=None,                      # FIX-067: quote function for momentum fresh LTP
        strategy_governor=None,             # FIX-130 Item 6: intraday strategy circuit breaker
        perf_weights: Optional[Dict[str, float]] = None,  # FIX-132 Item 9
        trade_type: str = "INTRADAY",       # Slice 2 LAYER 1: master product gate
        force_intraday_only: bool = False,  # Slice 2 LAYER 0: read for the control resolver
        allocator=None,                     # V3 03.05: PortfolioAllocator (None = OFF → FCFS byte-identical)
        v3_chain=None,                      # V3 Step 10: V3ChainRunner (None = OFF → byte-identical; also set via set_v3_chain)
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
        self._sr_detector = sr_detector                    # SNR-DETECTOR-V1 (None = dormant)
        self._zone_warmer = zone_warmer                    # SNR-V2 (None = no warming)
        self._retest_diverter = None                       # SNR-V2 — set via set_retest_diverter()
        self._retest_sl_buffer_pct = float(retest_sl_buffer_pct)  # SNR-V2
        self._rate_limiter = rate_limiter                  # FIX-007: optional pre-check
        self._quote_fn = quote_fn                          # FIX-067: momentum fresh LTP
        self._strategy_governor = strategy_governor        # FIX-130 Item 6: circuit breaker
        self._perf_weights: Dict[str, float] = dict(perf_weights or {})  # FIX-132 Item 9
        self._trade_type = trade_type                      # Slice 2 LAYER 1
        self._force_intraday_only = bool(force_intraday_only)  # Slice 2 LAYER 0
        self._allocator = allocator                        # V3 03.05: None unless shadow/enforce (also settable via set_allocator)
        self._v3_chain = v3_chain                          # V3 Step 10: None unless shadow (also settable via set_v3_chain)

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

        # Bug G (full integration): entry throttle — global min-gap + burst +
        # per-symbol cooldown, atomic check-and-record (prevents the 19-Jun burst
        # AND rapid same-symbol re-entry). See signals/entry_throttle.py.
        from signals.entry_throttle import EntryThrottle
        self._entry_throttle = EntryThrottle(
            min_gap_sec=min_gap_between_entries_sec,
            burst_window_sec=entry_burst_window_sec,
            burst_max=entry_burst_max,
            per_symbol_cooldown_sec=per_symbol_cooldown_sec,
        )

        # FIX-190 (Bug B): in-memory runtime counters for /metrics observability
        # (entries placed/throttled/rejected aren't captured by the DB signal
        # status alone — esp. throttle drops, which never become trades).
        self._rt_metrics_lock = threading.Lock()
        self._rt_metrics = {
            "signals_processed": 0,
            "entries_placed": 0,
            "entries_throttled": 0,
            "entries_rejected": 0,
        }

        # Stats (SP13 + SPW9)
        self._stats: Dict[str, Any] = {
            "processed": 0,
            "rejected": {},
            "placed": 0,
            "total_ms": 0.0,
            "pipeline_total": 0,
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

    def set_retest_diverter(self, diverter) -> None:
        """SNR-V2: late-bind the RetestDiverter (built after the RetestMonitor,
        which needs continue_from_retest). None keeps the divert dormant."""
        self._retest_diverter = diverter

    def _warm_zones(self, signal_tuple) -> None:
        """SNR-V2: enqueue a signal's symbol for zone warming so the cache is hot
        by the time (or soon after) the pipeline reaches the divert check. Fully
        guarded — never affects dispatch."""
        if self._zone_warmer is None:
            return
        try:
            if isinstance(signal_tuple, dict):
                symbol = signal_tuple.get("symbol")
            else:
                symbol = signal_tuple[2]
            if symbol:
                self._zone_warmer.enqueue(symbol)
        except Exception:
            pass

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
            self._warm_zones(signal_tuple)   # SNR-V2: warm in parallel with processing
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
            # Direction line FIRST (LONG/SHORT). direction may arrive as
            # LONG/SHORT/BUY/SELL → normalise to LONG/SHORT for display.
            dir_disp = "SHORT" if str(direction).upper() in ("SHORT", "SELL") else "LONG"
            body = (
                f"Direction: {dir_disp}\n"
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

    def _sr_observe(
        self,
        *,
        symbol: str,
        strategy_name: str,
        score,                  # int | None
        entry_price: float,
        sl_price: float,
        tgt_price: float,
        qty: int,
        direction: str,
        signal_id: str,
        intent: str,
    ) -> None:
        """
        SNR-DETECTOR-V1: hand a PLACED candidate to the async, non-gating S&R
        detector. Called ONLY after the order reached placement (spec cond. a).
        Builds the candidate snapshot and enqueues it — the detector returns
        immediately and does its slow work (3 historical fetches → confluence →
        flags → shadow-log row) on its own background worker. This must NEVER
        delay placement or raise into the pipeline, so it is fully guarded and a
        no-op when the detector is not wired (flag off → sr_detector=None).
        """
        if self._sr_detector is None:
            return
        try:
            from sr_detector import Candidate
            cand = Candidate(
                signal_id=signal_id,
                symbol=symbol,
                strategy=strategy_name,
                direction=str(direction),
                intended_entry=float(entry_price),
                sl_price=float(sl_price),
                tgt_price=float(tgt_price),
                qty=int(qty),
                intent=str(intent),
                mode=str(self._mode).lower(),
                ts=now_ist(),
                score=(int(score) if score is not None else None),
            )
            self._sr_detector.observe(cand)
        except Exception as exc:
            # Observer must never affect the pipeline (parity with shadow side-effects).
            self._log.error("sr_detector observe failed for %s: %s", symbol, exc)

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

    def _bump_metric(self, key: str, n: int = 1) -> None:
        """FIX-190 (Bug B): increment an in-memory runtime counter (thread-safe)."""
        try:
            with self._rt_metrics_lock:
                self._rt_metrics[key] = self._rt_metrics.get(key, 0) + n
        except Exception:
            pass  # metrics must never affect the pipeline

    def get_runtime_metrics(self) -> Dict[str, int]:
        """FIX-190 (Bug B): snapshot of the in-memory signal funnel for /metrics:
        signals_processed -> signals_screened_(passed|rejected|skipped) ->
        entries_(placed|throttled|rejected).

        The screened_* totals come from the existing SPW9 screener stats — the
        DOMINANT rejection layer (e.g. score < min_pass_score), which early-returns
        before the entry counters, so without it /metrics looked like signals
        'vanished' (19-Jun: 449 processed, ~all screened out, only the post-screen
        rejects reached entries_rejected)."""
        with self._rt_metrics_lock:
            m = dict(self._rt_metrics)
        try:
            with self._stats_lock:
                m["signals_screened_passed"] = self._stats["screener_passed"]
                m["signals_screened_rejected"] = sum(
                    self._stats["screener_rejected"].values()
                )
                m["signals_screened_skipped"] = sum(
                    self._stats["screener_skipped"].values()
                )
        except Exception:
            pass  # _stats absent in a bare/test instance -> entry counters only
        # Bug G (full): per-reason throttle breakdown (which gate fired).
        try:
            m.update(self._entry_throttle.metrics())
        except Exception:
            pass
        # Bug B (full): broker quota gauges for live monitoring. These mirror the
        # reservation-aware DAILY_TRADES gate exactly, so /metrics reflects the
        # real cap decision (not a parallel tally):
        #   broker_in_flight     = count_live_reservations() = reserved-not-placed
        #                          + PENDING_FILL (orders sent, awaiting fill)
        #   broker_filled_today  = settled_today = today's executed trades that
        #                          opened (excl PENDING_FILL)
        #   broker_quota_used    = max(daily_count, settled_today + in_flight)
        #                          — identical to risk_engine's effective_daily
        #   broker_quota_max     = the configured daily cap (live_test override aware)
        #   broker_quota_available = max(max - used, 0)
        # Best-effort and fully guarded: metrics must never raise or block.
        try:
            today = now_ist().date().isoformat()
            daily_count = self._store.count_trades_today(today)
            settled_today = self._store.count_settled_trades_today(today)
            _cres = getattr(self._fm, "count_live_reservations", None)
            in_flight = _cres() if callable(_cres) else 0
            used = max(daily_count, settled_today + in_flight)
            max_daily = int(getattr(self._risk, "_max_daily", 0) or 0)
            m["broker_in_flight"] = in_flight
            m["broker_filled_today"] = settled_today
            m["broker_quota_used"] = used
            m["broker_quota_max"] = max_daily
            m["broker_quota_available"] = max(max_daily - used, 0)
        except Exception:
            pass  # gauges are best-effort; never break /metrics
        return m

    # ------------------------------------------------------------------
    # Core pipeline (SP6, SPW3)
    # ------------------------------------------------------------------

    def _enforce_strategy_position_cap(self, strategy_name: str, strategy_obj) -> None:
        """H-7 (Wave-5): enforce the per-strategy concurrent-position cap ATOMICALLY.

        MUST be called while holding self._fm.portfolio_lock, immediately before
        reserve(), so the count reflects every reservation already committed under the
        lock — closing the TOCTOU where a Chartink burst (one webhook -> N symbols -> up
        to `worker_count` workers on the SAME strategy) each read the same stale
        outside-lock count and all passed, blowing a cap of N.

        Authoritative count (mirrors risk_engine's global FIX-185 OPEN_POSITIONS): the
        strategy's OPEN/PARTIAL trades (DB) + its live fund_manager reservations, floored
        by the DB active count (incl. PENDING_FILL) so a reservation lost across a restart
        is still caught; +1 for THIS candidate (it has neither reserved nor inserted a row
        yet). Reject when that exceeds the cap. De-duplicates the three former
        outside-lock copies (was H-7).
        """
        max_strat_pos = getattr(strategy_obj, "max_concurrent_positions", 2)
        row = self._store.fetch_one(
            "SELECT "
            "SUM(CASE WHEN status IN ('OPEN','PARTIAL') THEN 1 ELSE 0 END) AS open_partial, "
            "SUM(CASE WHEN status IN ('OPEN','PARTIAL','PENDING_FILL') THEN 1 ELSE 0 END) AS active "
            "FROM trades WHERE strategy = ?",
            (strategy_name,),
        )
        open_partial = int(row["open_partial"]) if row and row["open_partial"] is not None else 0
        active_incl_pending = int(row["active"]) if row and row["active"] is not None else 0
        reserved = self._fm.count_live_reservations_for_strategy(strategy_name)
        # +1 = THIS candidate. max() with the DB floor can only HARDEN, never loosen.
        effective = max(open_partial + reserved, active_incl_pending) + 1
        if effective > max_strat_pos:
            raise _PipelineReject(
                "STRATEGY_POSITION_LIMIT",
                f"{strategy_name} at cap: {effective - 1}/{max_strat_pos} open+in-flight "
                f"(open_partial={open_partial}, reserved={reserved}, "
                f"active_incl_pending={active_incl_pending})",
            )

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
        handed_off = False  # V3 03.05: True when this candidate was handed to the allocator (enforce); worker owns the claim

        try:
            # SP9: mark PROCESSING immediately
            self._store.update_signal_status(signal_id, "PROCESSING")
            self._bump_metric("signals_processed")  # FIX-190 (Bug B)
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

            # Slice 2 — strategy-control gate (LAYERS 1+3). Reject BEFORE any
            # sizing/reservation if the master trade_type or the per-strategy
            # ON/OFF switch says this strategy must not trade today. The SAME
            # resolver drives the status table, so the table can never disagree.
            _verdict = strategy_will_trade(
                strategy_obj, trade_type=self._trade_type,
                force_intraday_only=self._force_intraday_only,
            )
            if not _verdict.will_trade:
                # SLICE2.5-PHASE-4: a trade_type×intent mismatch (control LAYER 1×2)
                # gets its OWN reject label "TRADE_TYPE" (-> REJECTED_TRADE_TYPE + its
                # own per-check tally) so a delivery go-live can SEE trade_type rejects
                # distinctly; every other strategy-control reject (disabled switch, the
                # force-breaker branch) keeps the generic STRATEGY_CONTROL label. Keyed
                # on the machine-readable verdict.cause, never on the message string.
                _check = (CAUSE_TRADE_TYPE if _verdict.cause == CAUSE_TRADE_TYPE
                          else "STRATEGY_CONTROL")
                raise _PipelineReject(_check, _verdict.reason)

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

            # SNR-V2 Phase A: WAIT_FOR_RETEST pre-placement divert. Runs BEFORE any
            # sizing/reservation, so a parked candidate holds NO capital. Reads the
            # ZoneCache SYNCHRONOUSLY (never fetches on the hot path); a miss /
            # non-long / no-HIGH-zone falls through to normal placement. Dormant
            # unless the master flag is on (diverter stays None when disabled).
            if self._retest_diverter is not None and self._retest_diverter.enabled:
                if self._retest_diverter.maybe_divert(
                    signal_id=signal_id, symbol=symbol, side=side,
                    direction=strategy_obj.direction, entry_price=entry_price,
                    sl_price=sl_price, strategy_name=strategy_name,
                    intent=strategy_obj.intent, tier=screen_result.tier,
                    trigger_price=trigger_price, score=screen_result.score,
                ):
                    _into = "long inside HIGH resistance" if side == "BUY" \
                        else "short inside HIGH support"
                    self._log.info(
                        f"SNR-V2: {symbol} ({signal_id}) diverted to WAIT_FOR_RETEST "
                        f"({_into}) — no capital reserved"
                    )
                    return  # parked (or dropped as dup); do NOT size/reserve/place

            # ----------------------------------------------------------
            # FIX-067 / M-S1 (Wave-5): momentum fresh-LTP re-anchor.
            # Momentum strategies (pullback_wait_enabled=false) place immediately;
            # the webhook trigger_price can be stale by 2+ s of pipeline time. Fetch
            # the live LTP and RE-ANCHOR the ENTIRE placement basis onto it — entry +
            # SL here, and (downstream, because they all derive from these) target,
            # sizing.qty, and reserved capital — so sized risk == actual risk and
            # reserved capital == order value (audit M-S1: all four were stale-anchored).
            #   D1 (plumbing): get_quote takes a LIST and returns dict[str, Quote]
            #   (frozen dataclass, attr .last_price), keyed by bare symbol. The old
            #   `_quote_fn(symbol)` + `.get("last_price")` was inert (a bare string was
            #   iterated per-char; Quote has no .get) so the fresh branch silently fell
            #   back to stale in BOTH modes — mirror the 7 correct get_quote callers.
            # Placed AFTER the retest diverter (its routing zone-check keeps its
            # trigger-derived basis — unchanged) and BEFORE sizing/reserve, so the H-7
            # per-strategy-cap + reserve section inside portfolio_lock stays atomic and
            # untouched (it is count-based, orthogonal to price). Pullback strategies
            # skip this (EntryGate already waits for current price). trigger_price is
            # preserved for FIX-128's slippage guard, which now sizes its tolerance off
            # the fresh SL. Parity: paper get_quote returns the same dict[str, Quote].
            if not strategy_obj.pullback_wait_enabled and self._quote_fn is not None:
                live_ltp = None
                try:
                    quotes = self._quote_fn([symbol])
                    q = quotes.get(symbol) if quotes else None
                    live_ltp = float(q.last_price) if q is not None else None
                except Exception as exc:
                    self._log.warning(
                        f"FIX-067 momentum fresh quote fetch error for {symbol}: {exc}"
                    )
                if live_ltp and live_ltp > 0:
                    self._log.info(
                        f"FIX-067 momentum fresh quote: {symbol} stale={trigger_price:.2f} "
                        f"live={live_ltp:.2f} delta={live_ltp - trigger_price:+.2f} "
                        f"— re-anchoring entry/SL/TGT/sizing/reservation"
                    )
                    entry_price, sl_price = self._derive_prices(
                        live_ltp, strategy_obj, now_time=now.time()
                    )
                else:
                    self._log.warning(
                        f"FIX-067 momentum fresh quote unavailable for {symbol} "
                        f"(ltp={live_ltp}); using stale webhook price"
                    )

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
                    self._ks.record_api_failure(be)
                raise _PipelineReject("SIZING_BROKER_ERROR", str(be)) from be

            if not sizing.success:
                raise _PipelineReject(f"SIZING_{sizing.constraint}", sizing.reason)
            self._heartbeat(symbol)  # FIX-011: checkpoint 3 (sizing done)

            # ── V3 Step 10 decision chain (SHADOW, LOG-ONLY) — default-OFF: v3_chain is
            #    None → this hook is a single skipped flag check → BYTE-IDENTICAL. Fire-
            #    and-forget: it MUST NEVER reject, delay, alter, or block a live signal or
            #    order (G-NO-INTERFERENCE). Placed BEFORE the allocator hook so it observes
            #    every sized signal even if the allocator (enforce) hands one off. ──
            if self._v3_chain is not None:
                try:
                    self._v3_chain.observe(self._build_v3_signal(
                        signal_id, symbol, scanner_name, strategy_name, strategy_obj,
                        side, entry_price, sl_price, screen_result,
                        trigger_price, triggered_at, now))
                except Exception as _v3_exc:   # the chain must NEVER break admission
                    self._log.error("v3_chain observe error (ignored): %s", _v3_exc)

            # ── V3 03.05 Portfolio Allocator routing (default-OFF: allocator is None
            #    → this hook is skipped entirely → FCFS admission BYTE-IDENTICAL) ──
            if self._allocator is not None:
                try:
                    _amode = self._allocator.mode
                    if _amode == "enforce" and self._allocator.in_scope(strategy_obj):
                        # ENFORCE + in-scope: hand off to the batch worker; the receiver's
                        # symbol in-flight claim stays held until the worker admits/rejects
                        # (A8). Do NOT self-admit; do NOT release the claim here.
                        self._allocator.submit(self._build_candidate(
                            signal_id, symbol, scanner_name, strategy_name, strategy_obj,
                            side, entry_price, sl_price, sizing, screen_result,
                            trigger_price, triggered_at, retry_count, now,
                            with_payload=True))
                        handed_off = True
                        return
                    if _amode == "shadow":
                        # SHADOW: fire-and-forget copy for the regret observer, then fall
                        # through to the UNCHANGED fused admit (live FCFS still governs).
                        self._allocator.observe(self._build_candidate(
                            signal_id, symbol, scanner_name, strategy_name, strategy_obj,
                            side, entry_price, sl_price, sizing, screen_result,
                            trigger_price, triggered_at, retry_count, now,
                            with_payload=False))
                except _PipelineReject:
                    raise
                except Exception as _alloc_exc:   # the allocator must NEVER break admission
                    self._log.error("allocator routing error (ignored): %s", _alloc_exc)

            # Fused admission (OFF / shadow / enforce-out-of-scope). _admit_and_place is
            # the SAME callable the enforce worker uses (R2: no forked reservation path).
            # The inner try/finally syncs ac's lifecycle flags back to the locals the
            # outer except/finally read — preserving every existing behaviour (incl. the
            # queue.Full reservation-leak guard and FIX-165c in_flight bookkeeping).
            _ac = _AdmitCtx()
            try:
                self._admit_and_place(
                    _ac, signal_id=signal_id, symbol=symbol, scanner_name=scanner_name,
                    strategy_name=strategy_name, strategy_obj=strategy_obj, side=side,
                    entry_price=entry_price, sl_price=sl_price, sizing=sizing,
                    screen_result=screen_result, trigger_price=trigger_price,
                    triggered_at=triggered_at, retry_count=retry_count, now=now)
            finally:
                reservation_id = _ac.reservation_id
                requeued = _ac.requeued
                in_flight_incremented = _ac.in_flight_incremented

        except _PipelineReject as rej:
            self._log.info(
                f"Signal {signal_id} ({symbol}) rejected at {rej.check}: {rej.reason}"
            )
            # FIX-190 (Bug B): count non-throttle rejects separately (throttle
            # drops are already counted under entries_throttled).
            if rej.check != "ENTRY_THROTTLED":
                self._bump_metric("entries_rejected")
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
            # V3 03.05: also skip when handed_off — the allocator worker owns the symbol
            # claim until it admits/rejects (A8). In OFF, handed_off is always False, so
            # this condition is byte-identical to the pre-allocator behaviour.
            # SP7: ALWAYS release in-flight (audit #21 fix; SPW8: covers screener paths)
            if not requeued and not handed_off and self._in_flight_release is not None:
                try:
                    self._in_flight_release(symbol)
                except Exception as rel_exc:
                    self._log.error(f"in_flight_release failed for {symbol}: {rel_exc}")

            elapsed_ms = (time.monotonic() - start_mono) * 1000
            with self._stats_lock:
                self._stats["total_ms"] += elapsed_ms
                self._stats["pipeline_total"] += 1
    def _admit_and_place(
        self, ac, *, signal_id, symbol, scanner_name, strategy_name, strategy_obj,
        side, entry_price, sl_price, sizing, screen_result, trigger_price,
        triggered_at, retry_count, now,
    ) -> None:
        """V3 03.05: the extracted ADMIT+place block (Steps 6-9). The SINGLE
        admission callable used by BOTH the fused FCFS path (_process_one) and the
        enforce worker (admit_prepared) — R2: no forked reservation path. Operates on
        a mutable _AdmitCtx `ac` (reservation_id/requeued/in_flight_incremented/placed)
        so the caller's except+finally observe the lifecycle on every exit path.
        Byte-identical to the pre-extraction inline block (only the three locals became
        ac.* fields, plus the ac.placed marker after a successful place())."""
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
            ac.in_flight_incremented = True  # FIX-165c
            processor_in_flight = self._in_flight_count

        with self._fm.portfolio_lock:
            # H-7 (Wave-5): the per-strategy cap is enforced ATOMICALLY inside
            # portfolio_lock, immediately before reserve(), so a Chartink burst can't
            # slip past a stale pre-lock count. One deduped check for all 3 paths.
            self._enforce_strategy_position_cap(strategy_name, strategy_obj)
            try:
                approval = self._risk.approve(
                    symbol, side, strategy_obj.intent, sizing, signal_id,
                    processor_in_flight_count=processor_in_flight
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure(be)
                raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be

            if not approval.approved:
                raise _PipelineReject(approval.failed_check, approval.reason)

            try:
                reservation = self._fm.reserve(
                    symbol, sizing.qty, entry_price, strategy_obj.intent, signal_id,
                    strategy=strategy_name,   # H-7 (Wave-5): tag for the per-strategy cap
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure(be)
                raise _PipelineReject("RESERVE_BROKER_ERROR", str(be)) from be

            if not reservation.success:
                raise _PipelineReject("RESERVE_FAILED", reservation.reason_if_failed)

            ac.reservation_id = reservation.reservation_id

            # SP9: signal reserved (kept inside the lock so the SQL row
            # appears atomically with the reservation entry).
            self._store.update_signal_status(signal_id, "RESERVED")

        self._heartbeat(symbol)  # FIX-011: checkpoint 4 (reservation done)

        # ----------------------------------------------------------
        # Step 8: Order placement (SP16: optional)
        # ----------------------------------------------------------
        if self._placer is None:
            self._log.info(
                f"order_placer=None; releasing reservation {ac.reservation_id} for {signal_id}"
            )
            try:
                self._fm.release(ac.reservation_id, "no_order_placer")
            except Exception as rel_exc:
                self._log.error(f"Failed to release reservation {ac.reservation_id}: {rel_exc}")
            ac.reservation_id = None
            self._store.update_signal_status(signal_id, "PROCESSED_NO_PLACER")
            with self._stats_lock:
                self._stats["processed_no_placer"] += 1
            return

        # Derive target price (SPW5, SPW6). Uses the (possibly fresh-re-anchored)
        # entry_price/sl_price from the FIX-067/M-S1 momentum block above, so the
        # target sits on the same fresh basis as entry/SL/qty/reservation.
        tgt_price = self._derive_target(entry_price, sl_price, strategy_obj)

        # Telegram alert: INTRADAY SIGNAL (fires before order placement)
        self._emit_signal_alert(
            symbol=symbol,
            strategy_name=strategy_name,
            score=screen_result.score,
            entry_price=entry_price,
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
            if ac.reservation_id:
                self._fm.release(ac.reservation_id, "kill_switch_after_pipeline")
                ac.reservation_id = None
            raise _PipelineReject("KILL_SWITCH_LATE", "Kill switch active before placement")

        if self._stop_event.is_set():
            self._log.warning(
                f"FIX-070: shutdown event set - aborting placement for {signal_id} ({symbol})"
            )
            if ac.reservation_id:
                self._fm.release(ac.reservation_id, "shutdown_before_placement")
                ac.reservation_id = None
            raise _PipelineReject("SHUTDOWN", "System shutdown before placement")

        # FIX-190 (Bug G): entry throttle — space out placed entries to prevent
        # a burst (the 5-entries-in-5s 19-Jun spike). Released reservation on
        # reject so the slot frees for a later signal.
        _tr = self._entry_throttle.admit(symbol)
        if not _tr.allowed:
            if ac.reservation_id:
                self._fm.release(ac.reservation_id, "entry_throttled")
                ac.reservation_id = None
            self._bump_metric("entries_throttled")
            raise _PipelineReject(
                "ENTRY_THROTTLED", f"Entry throttled: {_tr.reason}"
            )

        try:
            self._placer.place(
                symbol=symbol,
                side=side,
                qty=sizing.qty,
                entry_price=entry_price,  # FIX-067/M-S1: fresh-re-anchored (or stale fallback)
                sl_price=sl_price,
                intent=strategy_obj.intent,
                signal_id=signal_id,
                reservation_id=ac.reservation_id,
                strategy=strategy_name,
                tgt_price=tgt_price,
                signal_trigger_price=trigger_price,  # FIX-128: for slippage guard
                sizing_breakdown=sizing.breakdown,   # Diary #4: sizing audit
                tgt_risk_reward=getattr(strategy_obj, "tgt_risk_reward", None),  # Slice 1: freeze strategy R:R for fill-time TGT recalc (None -> fill falls back to default + WARN)
            )
            ac.reservation_id = None   # placer owns it now
            ac.placed = True   # V3 03.05: mark placement for the enforce tally
            self._bump_metric("entries_placed")  # FIX-190 (Bug B)
        except BrokerTimeoutError as timeout_err:
            # A-2 (02-Jul): a place() timeout is AMBIGUOUS — the order may already
            # be live at the broker. Do NOT retry (a retry = a second, duplicate
            # entry / 2x exposure — the entry throttle only masks it). order_placer
            # has already set the trade UNKNOWN_IN_FLIGHT and KEPT its reservation
            # (FIX-068); the 15s reconciler recovery (_recover_in_flight_entries) is
            # the SOLE owner — it correlates the entry by tag and adopts+protects (or
            # FAILs on confirmed broker-absence) and reconstructs capital. So: record
            # the API failure, mark the SIGNAL TIMEOUT (the TRADE stays authoritative
            # UNKNOWN_IN_FLIGHT — TIMEOUT is an already-allowed signal status, so no
            # schema/report change), relinquish the reservation handle
            # WITHOUT releasing it (recovery owns it — SINGULAR ownership), free the
            # symbol lock (ac.requeued stays False), and return. Parity: paper simulates
            # the timeout through this same handler.
            if self._ks:
                self._ks.record_api_failure(timeout_err)
            self._store.update_signal_status(
                signal_id, "TIMEOUT", str(timeout_err)
            )
            ac.reservation_id = None   # recovery owns the reservation — do NOT release
            return

        except BrokerRateLimitError as transient_err:
            # FIX-069: a client-side rate-limit is raised PRE-submission (nothing was
            # sent to the broker) -> re-queuing for retry is idempotent. Keep lock
            # held to prevent duplicate admission. Max 3 retries (45s total, 15s each).
            if self._ks:
                self._ks.record_api_failure(transient_err)

            if retry_count >= 3:
                # Max retries exhausted - mark as failed and release lock
                self._log.warning(
                    f"FIX-069: signal {signal_id} ({symbol}) abandoned after "
                    f"{retry_count} retries on {type(transient_err).__name__}"
                )
                raise  # Let outer exception handler mark PLACEMENT_FAILED

            # Re-queue with incremented retry count
            retry_count += 1
            ac.requeued = True  # Signal finally block to NOT release lock
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
                if ac.reservation_id:
                    self._fm.release(ac.reservation_id, "requeued_transient_error")
                    ac.reservation_id = None
                return  # Exit without releasing lock (requeued=True)
            except queue.Full:
                # Queue full - can't retry, must fail and release lock
                self._log.error(
                    f"FIX-069: queue full, cannot re-queue {signal_id} ({symbol})"
                )
                ac.requeued = False  # Force lock release
                raise  # Let outer handler mark PLACEMENT_FAILED
        except BrokerError as be:
            if self._ks:
                self._ks.record_api_failure(be)
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

        # SNR-DETECTOR-V1: non-gating observer — runs only now that the order
        # reached placement. Enqueues + returns immediately (never blocks/raises).
        self._sr_observe(
            symbol=symbol,
            strategy_name=strategy_name,
            score=screen_result.score,
            entry_price=entry_price,  # FIX-067/M-S1: the actual placed (fresh-re-anchored) entry
            sl_price=sl_price,
            tgt_price=tgt_price,
            qty=sizing.qty,
            direction=strategy_obj.direction,
            signal_id=signal_id,
            intent=strategy_obj.intent,
        )

    # ------------------------------------------------------------------
    # V3 03.05 Portfolio Allocator integration (default-OFF; see
    # docs/v3/V3_STEP6_PORTFOLIO_ALLOCATOR_PLAN.md)
    # ------------------------------------------------------------------

    def set_allocator(self, allocator) -> None:
        """Late-bind the PortfolioAllocator (built after this processor in main.py so it
        can reference admit_prepared/reject_prepared). None keeps FCFS byte-identical."""
        self._allocator = allocator

    def set_v3_chain(self, v3_chain) -> None:
        """Late-bind the V3ChainRunner (V3 Step 10 shadow enrichment). None keeps the
        hot path byte-identical (the observe hook is a single skipped flag check)."""
        self._v3_chain = v3_chain

    def _build_v3_signal(self, signal_id, symbol, scanner_name, strategy_name,
                         strategy_obj, side, entry_price, sl_price, screen_result,
                         trigger_price, triggered_at, now):
        """Build the LIGHT V3Signal snapshot for the shadow chain (fire-and-forget).
        Captures the LIVE placement basis + the screener outputs AT signal time so the
        async worker re-reads NOTHING live (NO-LOOKAHEAD). Imports v3_chain lazily so
        there is no import-time coupling when the chain is off. Computes the LIVE target
        with the SAME derivation the admit path uses (a pure O(1) call — no divergence)."""
        from v3_chain.models import V3Signal
        try:
            live_tgt = self._derive_target(entry_price, sl_price, strategy_obj)
        except Exception:
            live_tgt = None
        return V3Signal(
            signal_id=signal_id, symbol=symbol, scanner_name=scanner_name,
            strategy_name=strategy_name, side=side, intent=strategy_obj.intent,
            entry_price=float(entry_price), live_sl_price=float(sl_price),
            live_tgt_price=(float(live_tgt) if live_tgt is not None else None),
            trigger_price=float(trigger_price),
            score=float(getattr(screen_result, "score", 0.0)),
            tier=str(getattr(screen_result, "tier", "")),
            step_results=dict(getattr(screen_result, "step_results", {}) or {}),
            market_data=dict(getattr(screen_result, "market_data_snapshot", {}) or {}),
            sector=self._sector_for(symbol),
            as_of=now, triggered_at=triggered_at,
            v3_playbook=bool(getattr(strategy_obj, "v3_playbook", False)))

    def _sector_for(self, symbol: str) -> str:
        ic = self._instrument_cache
        if ic is None:
            return "UNKNOWN"
        for _m in ("sector_for", "get_sector"):
            _fn = getattr(ic, _m, None)
            if callable(_fn):
                try:
                    _s = _fn(symbol)
                    return str(_s) if _s else "UNKNOWN"
                except Exception:
                    return "UNKNOWN"
        return "UNKNOWN"

    def _build_candidate(self, signal_id, symbol, scanner_name, strategy_name,
                         strategy_obj, side, entry_price, sl_price, sizing,
                         screen_result, trigger_price, triggered_at, retry_count, now,
                         *, with_payload):
        """Build the allocator's ScoredCandidate from a screened+sized signal. The heavy
        AdmitPayload is attached ONLY for enforce (with_payload); shadow needs only the
        light ranking/regret fields. Imports allocation lazily (no import-time coupling
        when the allocator is off)."""
        from allocation.models import AdmitPayload, ScoredCandidate
        try:
            _epoch = triggered_at.replace(tzinfo=ist_timezone()).timestamp()
        except Exception:
            _epoch = 0.0
        _payload = None
        if with_payload:
            _payload = AdmitPayload(
                scanner_name=scanner_name, strategy_obj=strategy_obj, sizing=sizing,
                screen_result=screen_result, entry_price=entry_price, sl_price=sl_price,
                trigger_price=trigger_price, triggered_at=triggered_at,
                retry_count=retry_count, now=now)
        return ScoredCandidate(
            signal_id=signal_id, symbol=symbol, strategy_name=strategy_name, side=side,
            intent=strategy_obj.intent, score=float(getattr(screen_result, "score", 0.0)),
            tier=str(getattr(screen_result, "tier", "")),
            margin_required=float(getattr(sizing, "margin_required", 0.0)),
            sector=self._sector_for(symbol), triggered_epoch=float(_epoch), payload=_payload)

    def admit_prepared(self, candidate) -> bool:
        """ENFORCE-worker entry: admit ONE ranked, prepared candidate. Reuses the SHARED
        _admit_and_place (same reserve+place as the fused path) and mirrors _process_one's
        reject/exception handlers + finally cleanup (R5 migration scaffolding — consolidate
        with _process_one when enforce is activated). Returns True iff the entry placed."""
        ac = _AdmitCtx()
        p = candidate.payload
        try:
            self._admit_and_place(
                ac, signal_id=candidate.signal_id, symbol=candidate.symbol,
                scanner_name=p.scanner_name, strategy_name=candidate.strategy_name,
                strategy_obj=p.strategy_obj, side=candidate.side, entry_price=p.entry_price,
                sl_price=p.sl_price, sizing=p.sizing, screen_result=p.screen_result,
                trigger_price=p.trigger_price, triggered_at=p.triggered_at,
                retry_count=p.retry_count, now=p.now)
        except _PipelineReject as rej:
            self._store.update_signal_status(candidate.signal_id, f"REJECTED_{rej.check}", rej.reason)
            if ac.reservation_id:
                try:
                    self._fm.release(ac.reservation_id, f"rejected_{rej.check.lower()}")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {ac.reservation_id}: {rel_exc}")
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket[rej.check] = bucket.get(rej.check, 0) + 1
        except Exception as exc:
            self._log.error(f"allocator admit exception for {candidate.signal_id} ({candidate.symbol}): {exc}")
            self._store.update_signal_status(candidate.signal_id, "PLACEMENT_FAILED", str(exc))
            if ac.reservation_id:
                try:
                    self._fm.release(ac.reservation_id, "placement_failed")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {ac.reservation_id}: {rel_exc}")
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket["PLACEMENT_FAILED"] = bucket.get("PLACEMENT_FAILED", 0) + 1
        finally:
            if ac.in_flight_incremented:
                try:
                    with self._in_flight_lock:
                        self._in_flight_count -= 1
                except Exception as lock_exc:
                    self._log.error(f"_in_flight_count decrement failed: {lock_exc}")
            if not ac.requeued and self._in_flight_release is not None:
                try:
                    self._in_flight_release(candidate.symbol)
                except Exception as rel_exc:
                    self._log.error(f"in_flight_release failed for {candidate.symbol}: {rel_exc}")
        return ac.placed

    def reject_prepared(self, candidate, reason: str) -> None:
        """ENFORCE: a candidate the allocator's portfolio pre-checks dropped BEFORE admit
        (no reservation / no in_flight increment happened — those live in _admit_and_place).
        Set the signal status + release the receiver's symbol in-flight claim so it is
        treated exactly like an FCFS reject (A8)."""
        try:
            self._store.update_signal_status(candidate.signal_id, f"REJECTED_{reason}",
                                             f"allocator pre-check: {reason}")
        except Exception as exc:
            self._log.error(f"reject_prepared status write failed for {candidate.signal_id}: {exc}")
        with self._stats_lock:
            bucket = self._stats["rejected"]
            bucket[reason] = bucket.get(reason, 0) + 1
        if self._in_flight_release is not None:
            try:
                self._in_flight_release(candidate.symbol)
            except Exception as rel_exc:
                self._log.error(f"in_flight_release failed for {candidate.symbol}: {rel_exc}")


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
            raise _PipelineReject(
                "UNKNOWN_TGT_METHOD",
                f"Unknown tgt_method: {tgt_method!r}",
            )

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
            self._bump_metric("signals_processed")  # FIX-190 (Bug B)

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

            # Slice 2 — strategy-control gate (LAYERS 1+3), mirroring _process_one
            # so a pullback-wait resumption respects the master trade_type + the
            # per-strategy switch exactly like a direct webhook entry.
            _verdict = strategy_will_trade(
                strategy_obj, trade_type=self._trade_type,
                force_intraday_only=self._force_intraday_only,
            )
            if not _verdict.will_trade:
                # SLICE2.5-PHASE-4: a trade_type×intent mismatch (control LAYER 1×2)
                # gets its OWN reject label "TRADE_TYPE" (-> REJECTED_TRADE_TYPE + its
                # own per-check tally) so a delivery go-live can SEE trade_type rejects
                # distinctly; every other strategy-control reject (disabled switch, the
                # force-breaker branch) keeps the generic STRATEGY_CONTROL label. Keyed
                # on the machine-readable verdict.cause, never on the message string.
                _check = (CAUSE_TRADE_TYPE if _verdict.cause == CAUSE_TRADE_TYPE
                          else "STRATEGY_CONTROL")
                raise _PipelineReject(_check, _verdict.reason)

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
                    self._ks.record_api_failure(be)
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

            with self._fm.portfolio_lock:
                # H-7 (Wave-5): per-strategy cap enforced atomically inside portfolio_lock
                # (gate path). One deduped check for all 3 paths.
                self._enforce_strategy_position_cap(strategy_name, strategy_obj)
                try:
                    approval = self._risk.approve(
                        symbol, side, strategy_obj.intent, sizing, signal_id,
                        processor_in_flight_count=processor_in_flight
                    )
                except BrokerError as be:
                    if self._ks:
                        self._ks.record_api_failure(be)
                    raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be

                if not approval.approved:
                    raise _PipelineReject(approval.failed_check, approval.reason)

                try:
                    reservation = self._fm.reserve(
                        symbol, sizing.qty, entry_price, strategy_obj.intent, signal_id,
                        strategy=strategy_name,   # H-7 (Wave-5): tag for the per-strategy cap
                    )
                except BrokerError as be:
                    if self._ks:
                        self._ks.record_api_failure(be)
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

            # FIX-190 (Bug G): entry throttle (gate path).
            _tr = self._entry_throttle.admit(symbol)
            if not _tr.allowed:
                if reservation_id:
                    self._fm.release(reservation_id, "entry_throttled")
                    reservation_id = None
                self._bump_metric("entries_throttled")
                raise _PipelineReject(
                    "ENTRY_THROTTLED", f"Entry throttled: {_tr.reason}"
                )

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
                    sizing_breakdown=sizing.breakdown,   # Diary #4: sizing audit
                    tgt_risk_reward=getattr(strategy_obj, "tgt_risk_reward", None),  # Slice 1: freeze strategy R:R for fill-time TGT recalc (None -> fill falls back to default + WARN)
                )
                reservation_id = None   # placer owns it now
                self._bump_metric("entries_placed")  # FIX-190 (Bug B)
            except BrokerTimeoutError as timeout_err:
                # A-2 (02-Jul): an ambiguous place() timeout must NOT fall through to the
                # PLACEMENT_FAILED handler (which would RELEASE the reservation that
                # order_placer KEPT for the UNKNOWN_IN_FLIGHT trade — a second capital
                # owner). The 15s reconciler recovery owns it. Mark the signal TIMEOUT,
                # keep the reservation (SINGULAR ownership), return. (No retry here — the
                # gate path never re-queued; this only removes the double-release.)
                if self._ks:
                    self._ks.record_api_failure(timeout_err)
                self._store.update_signal_status(
                    signal_id, "TIMEOUT", str(timeout_err)
                )
                reservation_id = None   # recovery owns the reservation — do NOT release
                return
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure(be)
                raise
            except Exception:
                raise

            self._store.update_signal_status(signal_id, "PROCESSED")
            with self._stats_lock:
                self._stats["processed"] += 1
                self._stats["placed"] += 1

            # SNR-DETECTOR-V1: non-gating observer (gate-release path; score N/A).
            self._sr_observe(
                symbol=symbol,
                strategy_name=strategy_name,
                score=None,
                entry_price=entry_price,
                sl_price=sl_price,
                tgt_price=tgt_price,
                qty=sizing.qty,
                direction=direction,
                signal_id=signal_id,
                intent=strategy_obj.intent,
            )

        except _PipelineReject as rej:
            self._log.info(
                f"Gate signal {signal_id} ({symbol}) rejected at {rej.check}: {rej.reason}"
            )
            # FIX-190 (Bug B): count non-throttle rejects separately.
            if rej.check != "ENTRY_THROTTLED":
                self._bump_metric("entries_rejected")
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
                self._stats["pipeline_total"] += 1

    # ------------------------------------------------------------------
    # SNR-V2 Phase A — WAIT_FOR_RETEST resume (sibling of continue_from_gate)
    # ------------------------------------------------------------------

    def continue_from_retest(self, parked) -> None:
        """
        Resume a CONFIRMED WAIT_FOR_RETEST candidate. Re-checks kill-switch /
        market-window / strategy-control / governor (NOT the :607 60s expiry —
        this is a park-and-resume), then sizes + reserves (capital reserved HERE
        for the first time) and places a MARKET entry with a STRUCTURE SL below
        the reclaimed zone + an R:R-preserving TGT. Called from RetestMonitor's
        poll thread on CONFIRMED. ``parked`` is a ParkedCandidate (typed as object
        to avoid a circular import).
        """
        signal_id = parked.signal_id
        symbol = parked.symbol
        strategy_name = parked.strategy
        start_mono = time.monotonic()
        reservation_id: Optional[str] = None
        in_flight_incremented = False

        try:
            self._store.update_signal_status(signal_id, "PROCESSING")
            self._bump_metric("signals_processed")

            # Last-mile gates (NO 60s expiry — park-and-resume).
            if self._ks and self._ks.is_active("entry"):
                raise _PipelineReject("KILL_SWITCH", "Kill switch is active")
            now = now_ist()
            if not self._mw.is_entry_allowed(now):
                raise _PipelineReject("OUTSIDE_ENTRY_WINDOW", "Outside entry window")

            strategy_obj = self._strategies.get(strategy_name)
            if strategy_obj is None:
                raise _PipelineReject("UNKNOWN_STRATEGY", f"Strategy {strategy_name!r} not loaded")

            _verdict = strategy_will_trade(
                strategy_obj, trade_type=self._trade_type,
                force_intraday_only=self._force_intraday_only,
            )
            if not _verdict.will_trade:
                _check = (CAUSE_TRADE_TYPE if _verdict.cause == CAUSE_TRADE_TYPE
                          else "STRATEGY_CONTROL")
                raise _PipelineReject(_check, _verdict.reason)

            if not self._mw.is_entry_allowed_for_strategy(now, strategy_obj):
                raise _PipelineReject(
                    "OUTSIDE_ENTRY_WINDOW",
                    f"Outside per-strategy entry window "
                    f"({strategy_obj.entry_start_time}-{strategy_obj.entry_end_time})",
                )

            if self._strategy_governor is not None:
                paused, pause_reason = self._strategy_governor.check(strategy_name, now.time())
                if paused:
                    raise _PipelineReject("STRATEGY_CIRCUIT_BREAKER", pause_reason)

            # Structure SL on the far side of the confirmed zone; entry est = LTP.
            #   LONG : SL below band_low, break (reclaim) level = band_high.
            #   SHORT: SL above band_high, break (rejection) level = band_low.
            is_long = parked.direction in ("LONG", "BUY")
            side = "BUY" if is_long else "SELL"
            if is_long:
                structure_sl = parked.zone_band_low * (1.0 - self._retest_sl_buffer_pct / 100.0)
                break_level = parked.zone_band_high
            else:
                structure_sl = parked.zone_band_high * (1.0 + self._retest_sl_buffer_pct / 100.0)
                break_level = parked.zone_band_low
            entry_est = self._retest_entry_estimate(symbol, break_level)
            tier = parked.tier

            # Entry must sit on the profitable side of the structure SL.
            bad_structure = (entry_est <= structure_sl) if is_long else (entry_est >= structure_sl)
            if bad_structure:
                raise _PipelineReject(
                    "RETEST_BAD_STRUCTURE",
                    f"entry estimate {entry_est:.2f} vs structure SL {structure_sl:.2f} "
                    f"(direction={parked.direction})")

            try:
                sizing = self._sizer.calculate(
                    symbol, side, entry_est, structure_sl, strategy_obj.intent,
                    tier, strategy_obj.lot_size,
                    perf_weight=self._perf_weights.get(strategy_obj.name, 1.0),
                )
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure(be)
                raise _PipelineReject("SIZING_BROKER_ERROR", str(be)) from be
            if not sizing.success:
                raise _PipelineReject(f"SIZING_{sizing.constraint}", sizing.reason)

            tgt_price = self._derive_target(entry_est, structure_sl, strategy_obj)

            with self._in_flight_lock:
                self._in_flight_count += 1
                processor_in_flight = self._in_flight_count
                in_flight_incremented = True

            with self._fm.portfolio_lock:
                # H-7 (Wave-5): per-strategy cap enforced atomically inside portfolio_lock
                # (retest path). One deduped check for all 3 paths.
                self._enforce_strategy_position_cap(strategy_name, strategy_obj)
                try:
                    approval = self._risk.approve(
                        symbol, side, strategy_obj.intent, sizing, signal_id,
                        processor_in_flight_count=processor_in_flight)
                except BrokerError as be:
                    if self._ks:
                        self._ks.record_api_failure(be)
                    raise _PipelineReject("RISK_BROKER_ERROR", str(be)) from be
                if not approval.approved:
                    raise _PipelineReject(approval.failed_check, approval.reason)
                try:
                    reservation = self._fm.reserve(
                        symbol, sizing.qty, entry_est, strategy_obj.intent, signal_id,
                        strategy=strategy_name)   # H-7 (Wave-5): tag for the per-strategy cap
                except BrokerError as be:
                    if self._ks:
                        self._ks.record_api_failure(be)
                    raise _PipelineReject("RESERVE_BROKER_ERROR", str(be)) from be
                if not reservation.success:
                    raise _PipelineReject("RESERVE_FAILED", reservation.reason_if_failed)
                reservation_id = reservation.reservation_id
                self._store.update_signal_status(signal_id, "RESERVED")

            if self._placer is None:
                try:
                    self._fm.release(reservation_id, "no_order_placer")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {reservation_id}: {rel_exc}")
                reservation_id = None
                self._store.update_signal_status(signal_id, "PROCESSED_NO_PLACER")
                with self._stats_lock:
                    self._stats["processed_no_placer"] += 1
                return

            self._emit_signal_alert(
                symbol=symbol, strategy_name=strategy_name, score=None,
                entry_price=entry_est, sl_price=structure_sl, tgt_price=tgt_price,
                qty=sizing.qty, direction=parked.direction)

            if self._ks and self._ks.is_active("entry"):
                if reservation_id:
                    self._fm.release(reservation_id, "kill_switch_after_retest_pipeline")
                    reservation_id = None
                raise _PipelineReject("KILL_SWITCH_LATE", "Kill switch active before placement (retest)")
            if self._stop_event.is_set():
                if reservation_id:
                    self._fm.release(reservation_id, "shutdown_before_retest_placement")
                    reservation_id = None
                raise _PipelineReject("SHUTDOWN", "System shutdown before placement (retest)")

            _tr = self._entry_throttle.admit(symbol)
            if not _tr.allowed:
                if reservation_id:
                    self._fm.release(reservation_id, "entry_throttled")
                    reservation_id = None
                self._bump_metric("entries_throttled")
                raise _PipelineReject("ENTRY_THROTTLED", f"Entry throttled: {_tr.reason}")

            try:
                self._placer.place(
                    symbol=symbol, side=side, qty=sizing.qty,
                    entry_price=entry_est, sl_price=structure_sl,
                    intent=strategy_obj.intent, signal_id=signal_id,
                    reservation_id=reservation_id, strategy=strategy_name,
                    tgt_price=tgt_price, signal_trigger_price=parked.trigger_price,
                    sizing_breakdown=sizing.breakdown,
                    tgt_risk_reward=getattr(strategy_obj, "tgt_risk_reward", None),
                    entry_order_type="MARKET",   # SNR-V2: reclaim confirmed → MARKET entry
                )
                reservation_id = None
                self._bump_metric("entries_placed")
            except BrokerTimeoutError as timeout_err:
                # A-2 (02-Jul): ambiguous place() timeout — do NOT fall through to the
                # PLACEMENT_FAILED handler (it would RELEASE the reservation order_placer
                # KEPT for the UNKNOWN_IN_FLIGHT trade). The 15s reconciler recovery owns
                # it. Mark the signal TIMEOUT, keep the reservation (SINGULAR ownership),
                # return.
                if self._ks:
                    self._ks.record_api_failure(timeout_err)
                self._store.update_signal_status(
                    signal_id, "TIMEOUT", str(timeout_err)
                )
                reservation_id = None   # recovery owns the reservation — do NOT release
                return
            except BrokerError as be:
                if self._ks:
                    self._ks.record_api_failure(be)
                raise

            self._store.update_signal_status(signal_id, "PROCESSED")
            with self._stats_lock:
                self._stats["processed"] += 1
                self._stats["placed"] += 1

            self._sr_observe(
                symbol=symbol, strategy_name=strategy_name, score=None,
                entry_price=entry_est, sl_price=structure_sl, tgt_price=tgt_price,
                qty=sizing.qty, direction=parked.direction,
                signal_id=signal_id, intent=strategy_obj.intent)

        except _PipelineReject as rej:
            self._log.info(f"Retest signal {signal_id} ({symbol}) rejected at {rej.check}: {rej.reason}")
            if rej.check != "ENTRY_THROTTLED":
                self._bump_metric("entries_rejected")
            self._store.update_signal_status(signal_id, f"REJECTED_{rej.check}", rej.reason)
            if reservation_id:
                try:
                    self._fm.release(reservation_id, f"rejected_{rej.check.lower()}")
                except Exception as rel_exc:
                    self._log.error(f"Failed to release reservation {reservation_id}: {rel_exc}")
            with self._stats_lock:
                bucket = self._stats["rejected"]
                bucket[rej.check] = bucket.get(rej.check, 0) + 1

        except Exception as exc:
            self._log.error(
                f"Pipeline exception for retest signal {signal_id} ({symbol}): "
                f"{exc}\n{traceback.format_exc()}")
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
            if in_flight_incremented:
                try:
                    with self._in_flight_lock:
                        self._in_flight_count -= 1
                except Exception as lock_exc:
                    self._log.error(f"_in_flight_count decrement failed (retest): {lock_exc}")
            elapsed_ms = (time.monotonic() - start_mono) * 1000
            with self._stats_lock:
                self._stats["total_ms"] += elapsed_ms
                self._stats["pipeline_total"] += 1

    def _retest_entry_estimate(self, symbol: str, fallback: float) -> float:
        """Current LTP for MARKET-entry sizing/risk; fall back to the reclaim level."""
        try:
            if self._quote_fn is not None:
                # D1 (FIX-067/M-S1): get_quote takes a LIST and returns dict[str, Quote]
                # (frozen dataclass, attr .last_price), keyed by bare symbol. The old
                # `_quote_fn(symbol)` + `.get("last_price")` was inert (silently fell to
                # the reclaim-level fallback, never the live LTP). Mirror get_quote callers.
                quotes = self._quote_fn([symbol])
                q = quotes.get(symbol) if quotes else None
                ltp = float(q.last_price) if q is not None else None
                if ltp and ltp > 0:
                    return float(ltp)
        except Exception as exc:
            self._log.warning(f"retest entry estimate quote failed for {symbol}: {exc}")
        return float(fallback)

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
            pipeline_total = self._stats["pipeline_total"]
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
                "avg_pipeline_ms": total_ms / pipeline_total if pipeline_total > 0 else 0.0,
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
