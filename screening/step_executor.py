# screening/step_executor.py — Trading System v2
#
# Execute all 10 screening steps. Each step is a pure function returning a
# raw score (0.0-1.0). Exceptions are captured per step; run_all() never raises.
#
# Locked decisions: SE1-SE10, P9a, P18

from __future__ import annotations

import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError  # FIX-091
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time

from core.time_authority import now_ist


@dataclass(frozen=True)
class StepExecutorResult:
    """Result from running all 10 screening steps."""
    step_results: dict      # step_name -> float (0.0-1.0)
    step_statuses: dict     # step_name -> "PASSED" | "REJECTED" | "ERROR" | "TIMEOUT"  # FIX-091
    rejected_at: object     # str | None — first step that returned 0.0
    error_steps: list       # steps that raised exceptions
    latencies_ms: dict      # step_name -> float ms


# FIX-114: Default market-open time (IST) for signal_age calculation.
# Hardcoded fallback for callers that don't inject market_open parameter.
# Production code MUST inject market_open from SystemConfig.trading_hours.market_open
# to support special sessions (muhurat trading, early close, etc.).
# This constant exists only for backward compatibility with tests that pre-date
# the market_open parameter (SE2).
_DEFAULT_MARKET_OPEN = dt_time(9, 15)


class StepExecutor:
    """
    SE2: Stateless. Constructor takes only logger.
    SE3: run_all(signal, market_data, thresholds) -> StepExecutorResult
    SE5: Every step wrapped in try/except; errors recorded, never raised.
    SE6: Per-step latency tracked via time.monotonic().
    SE7: ALL 10 steps always run (no short-circuit).
    SE8: Missing market_data keys use per-step defaults (0.0 or 0.5).
    """

    def __init__(
        self,
        logger,
        market_open: dt_time | None = None,
        step_timeout_sec: float = 5.0,  # FIX-091: per-step timeout
    ) -> None:
        self._logger = logger
        # Audit #18: read market_open from config when provided; fall back
        # to the IST default so existing callers (tests, migrations) keep
        # working without code changes.
        self._market_open = market_open if market_open is not None else _DEFAULT_MARKET_OPEN
        self._step_timeout_sec = step_timeout_sec  # FIX-091

        # FIX-100: Reuse single executor across all run_all() calls.
        # Previous implementation created a new ThreadPoolExecutor per call,
        # adding ~5-10ms overhead per signal under high throughput. The executor
        # is single-worker (steps run sequentially per signal) but persists
        # across calls to avoid create/destroy churn.
        # M-S3: _executor_lock guards the swap in _rotate_executor() — run_all()
        # may be entered by several signal workers at once.
        self._executor_lock = threading.Lock()
        self._executor: ThreadPoolExecutor = self._new_executor()
        self._executor_shutdown = False
        self._executor_rotations = 0

    @staticmethod
    def _new_executor() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(max_workers=1, thread_name_prefix="step-exec")

    def _rotate_executor(self, stale: ThreadPoolExecutor) -> None:
        """M-S3: abandon the pool whose single worker is stuck on a timed-out step.

        `future.result(timeout=...)` abandons the WAIT, not the WORK — Python
        cannot interrupt a running function, and `future.cancel()` is a no-op once
        a task has started. With FIX-100's shared single-worker pool that means the
        hung task keeps the only worker: every remaining step of THIS signal, and
        every step of every LATER signal, queues behind it and times out in turn.
        One hung step thus costs 10x the timeout and never recovers.

        So: swap in a fresh pool and let the stale one go. The hung thread is
        abandoned, not leaked-and-waited-on — `shutdown(wait=False)` returns
        immediately and the stale pool disappears when its task finally returns.
        (A genuinely never-returning step would hold up interpreter exit either
        way; that is unchanged, not made worse.)

        Idempotent under concurrency: if another caller already rotated the same
        stale pool, or shutdown() has been called, this is a no-op.
        """
        with self._executor_lock:
            if self._executor_shutdown or self._executor is not stale:
                return
            self._executor = self._new_executor()
            self._executor_rotations += 1
            rotations = self._executor_rotations
        self._logger.warning(
            "step_executor: replaced the worker pool after a step timeout "
            "(rotation #%d) — the hung step keeps the abandoned worker so it "
            "cannot stall the remaining steps or the next signal",
            rotations,
        )
        try:
            stale.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 — best-effort; never fail a screening run
            pass

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def run_all(
        self,
        signal: dict,
        market_data: dict,
        thresholds: dict,
        exclude_steps: "set[str] | None" = None,
    ) -> StepExecutorResult:
        """Run the screening steps; capture exceptions; return full result.

        V3 03.03/03.04: `exclude_steps` names steps NOT to run (the Hard-Gate now
        owns them). Default None → all 10 steps run (the OFF path is byte-identical
        to before). In v3 mode the screener passes {circuit_check, signal_age} so
        only the 8 scored steps run.
        """
        direction = signal.get("direction", "LONG").upper()
        _exclude = exclude_steps or set()

        step_results: dict[str, float] = {}
        step_statuses: dict[str, str] = {}
        error_steps: list[str] = []
        latencies_ms: dict[str, float] = {}
        rejected_at: str | None = None

        steps = [
            (name, fn) for name, fn in (
                ("volume_surge",    self._step_1_volume_surge),
                ("vwap_position",   self._step_2_vwap_position),
                ("atr_filter",      self._step_3_atr_filter),
                ("rsi_range",       self._step_4_rsi_range),
                ("price_action",    self._step_5_price_action),
                ("sector_strength", self._step_6_sector_strength),
                ("time_of_day",     self._step_7_time_of_day),
                ("spread_check",    self._step_8_spread_check),
                ("circuit_check",   self._step_9_circuit_check),
                ("signal_age",      self._step_10_signal_age),
            ) if name not in _exclude
        ]

        # FIX-100: Reuse instance-level executor (was: create new executor per call)
        # FIX-091: Per-step timeout via future.result(timeout=...)
        for name, fn in steps:
            t0 = time.monotonic()
            # M-S3: snapshot the pool we submit to, so that on a timeout we
            # retire THAT pool and not whichever one a concurrent caller has
            # since installed.
            executor = self._executor
            try:
                # Submit step to executor with timeout
                future = executor.submit(fn, signal, market_data, thresholds, direction)
                score = future.result(timeout=self._step_timeout_sec)
            except FutureTimeoutError:
                # FIX-091: Step timeout -> neutral score, WARNING log
                elapsed = (time.monotonic() - t0) * 1000.0
                self._logger.warning(
                    "step_executor: step '%s' timed out after %.2fs",
                    name,
                    self._step_timeout_sec,
                )
                step_results[name] = 0.5  # neutral score
                step_statuses[name] = "TIMEOUT"
                latencies_ms[name] = elapsed
                # M-S3: the hung task still owns this pool's only worker.
                self._rotate_executor(executor)
                continue
            except Exception:
                elapsed = (time.monotonic() - t0) * 1000.0
                self._logger.error(
                    "step_executor: step '%s' raised exception:\n%s",
                    name,
                    traceback.format_exc(),
                )
                step_results[name] = 0.0
                step_statuses[name] = "ERROR"
                error_steps.append(name)
                latencies_ms[name] = elapsed
                if rejected_at is None:
                    rejected_at = name
                continue

            elapsed = (time.monotonic() - t0) * 1000.0
            latencies_ms[name] = elapsed

            step_results[name] = score
            if score == 0.0:
                step_statuses[name] = "REJECTED"
                if rejected_at is None:
                    rejected_at = name
            else:
                step_statuses[name] = "PASSED"

            self._logger.debug(
                "step_executor: %s score=%.3f latency=%.2fms",
                name, score, elapsed,
            )

        return StepExecutorResult(
            step_results=step_results,
            step_statuses=step_statuses,
            rejected_at=rejected_at,
            error_steps=error_steps,
            latencies_ms=latencies_ms,
        )

    def shutdown(self) -> None:
        """
        FIX-100: Shutdown the internal executor cleanly.

        Called by signal_processor.stop() or main.py shutdown sequence.
        Idempotent: safe to call multiple times.

        M-S3: takes the same lock as _rotate_executor and shuts down whichever
        pool is CURRENT, so a rotation can never leave shutdown() closing a pool
        that is no longer in use. Setting the flag inside the lock also stops a
        concurrent timeout from rotating a fresh pool in behind us.
        """
        with self._executor_lock:
            if self._executor_shutdown:
                return
            self._executor_shutdown = True
            executor = self._executor
        try:
            executor.shutdown(wait=True, cancel_futures=False)
        except Exception:
            pass  # Best-effort; executor may already be dead

    # -------------------------------------------------------------------------
    # Individual steps
    # -------------------------------------------------------------------------

    def _step_1_volume_surge(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Volume > avg_volume_20d * min_volume_surge (audit fix c: missing/0 -> 0.0)."""
        avg_vol = md.get("avg_volume_20d")
        if not avg_vol:
            return 0.0
        volume = md.get("volume", 0)
        min_surge = thr.get("min_volume_surge", 1.5)
        return 1.0 if volume > avg_vol * min_surge else 0.0

    def _step_2_vwap_position(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Direction-aware VWAP gate (audit fix b)."""
        ltp = md.get("ltp", 0.0)
        vwap = md.get("vwap")
        if vwap is None or ltp is None or ltp == 0.0:
            return 0.0  # No VWAP data (ETFs, bonds) -> fail gate
        if direction == "LONG":
            return 1.0 if ltp > vwap else 0.0
        else:  # SHORT
            return 1.0 if ltp < vwap else 0.0

    def _step_3_atr_filter(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """ADR% >= min_adr_pct. Missing/zero ATR -> 0.0."""
        atr = md.get("atr")
        ltp = md.get("ltp", 0.0)
        if not atr or not ltp:
            return 0.0
        adr_pct = (atr / ltp) * 100.0
        min_adr = thr.get("min_adr_pct", 0.5)
        return 1.0 if adr_pct >= min_adr else 0.0

    def _step_4_rsi_range(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """RSI in acceptable range per direction. Missing -> 0.5 (neutral).

        I.3 (2026-04-25): treat out-of-range RSI (< 0 or > 100) as missing
        rather than scoring it normally. Malformed market-data feeds have
        been observed to emit -1 / 101 / 999 placeholders for "no value";
        feeding them through the LONG-band check would silently grade the
        signal as 0.0 (out of band) when neutral 0.5 (missing) is the
        correct interpretation. Logs a warning so the data quality issue
        surfaces in postmortems rather than masking it as a screening fail.
        """
        rsi = md.get("rsi")
        if rsi is None:
            return 0.5
        try:
            rsi_f = float(rsi)
        except (TypeError, ValueError):
            self._logger.warning(
                "step_4_rsi_range: rsi=%r is not numeric; treating as missing", rsi
            )
            return 0.5
        if rsi_f < 0.0 or rsi_f > 100.0:
            self._logger.warning(
                "step_4_rsi_range: rsi=%s outside [0,100]; treating as missing "
                "(symbol=%s, scanner=%s)",
                rsi_f, signal.get("symbol"), signal.get("scanner"),
            )
            return 0.5
        if direction == "LONG":
            return 1.0 if 40.0 <= rsi_f <= 80.0 else 0.0
        else:  # SHORT
            return 1.0 if 20.0 <= rsi_f <= 60.0 else 0.0

    def _step_5_price_action(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Body% rewards strong directional candle bodies."""
        ltp = md.get("ltp", 0.0)
        open_price = md.get("open", ltp)
        day_high = md.get("day_high", ltp)
        day_low = md.get("day_low", ltp)
        body = abs(ltp - open_price)
        range_ = day_high - day_low + 1e-9
        body_pct = body / range_
        return min(1.0, body_pct * 2.0)

    def _step_6_sector_strength(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Placeholder: 1.0 if sector provided, 0.5 if missing."""
        sector = md.get("sector")
        return 1.0 if sector else 0.5

    def _minutes_since_open(self, now: datetime) -> float:
        """
        FIX-037: Minutes elapsed since market_open (IST).
        Clamps negative values to 0.0 (signals before market open).
        """
        open_today = now.replace(
            hour=self._market_open.hour,
            minute=self._market_open.minute,
            second=0,
            microsecond=0,
        )
        delta = now - open_today
        elapsed = delta.total_seconds() / 60.0
        if elapsed < 0:
            self._logger.debug(
                "signal_age clamped negative to zero: elapsed=%.2f minutes",
                elapsed,
            )
            return 0.0
        return elapsed

    def _step_7_time_of_day(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Entry time quality based on minutes since market open (IST)."""
        mins = self._minutes_since_open(now_ist())
        if mins < 15:
            return 0.5   # too early
        if mins < 60:
            return 1.0   # prime time
        if mins < 180:
            return 0.8   # good window
        return 0.5       # afternoon

    def _step_8_spread_check(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """
        FIX-043: Spread <= max_spread_pct using mid-price denominator.
        Missing bid/ask -> 0.5 (neutral). mid <= 0 -> WARNING + 0.5 (neutral).
        """
        bid = md.get("bid")
        ask = md.get("ask")
        if bid is None or ask is None:
            return 0.5

        # FIX-043: Use mid-price instead of LTP (LTP can be stale)
        mid = (ask + bid) / 2.0
        if mid <= 0.0:
            self._logger.warning(
                "step_executor.spread_check: mid_price <= 0 (bid=%s, ask=%s) - returning neutral",
                bid, ask
            )
            return 0.5

        spread_pct = ((ask - bid) / mid) * 100.0
        max_spread = thr.get("max_spread_pct", 0.1)
        return 1.0 if spread_pct <= max_spread else 0.0

    def _step_9_circuit_check(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Reject if upper or lower circuit (P9a add)."""
        circuit_state = md.get("circuit_state", "")
        if circuit_state in ("upper_circuit", "lower_circuit"):
            return 0.0
        return 1.0

    def _step_10_signal_age(
        self, signal: dict, md: dict, thr: dict, direction: str
    ) -> float:
        """Signal freshness: >60s -> 0.0, >30s -> 0.5, <=30s -> 1.0 (P9a add, SE4)."""
        triggered_at = signal.get("triggered_at")
        if triggered_at is None:
            return 0.5
        now = now_ist()
        if triggered_at.tzinfo is None and now.tzinfo is not None:
            triggered_at = triggered_at.replace(tzinfo=now.tzinfo)
        elif triggered_at.tzinfo is not None and now.tzinfo is None:
            triggered_at = triggered_at.replace(tzinfo=None)
        age_sec = (now - triggered_at).total_seconds()
        if age_sec <= 30:
            return 1.0
        if age_sec <= 60:
            return 0.5
        return 0.0
