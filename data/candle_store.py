"""
data/candle_store.py  -  Minute OHLC candle builder (LF9-LF18)

Builds minute candles from LTP ticks. Single source of truth for
intraday candle data.

Audit 3.4 fixes:
  - Candle OHLC built from LTP only (never from tick["ohlc"])
  - Candle close triggered by clock thread, not by tick arrival
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Deque, Dict, List, Optional

from core.time_authority import now_ist


@dataclass(frozen=True)
class CandleData:
    """LF13: Immutable candle record emitted on candle close."""
    instrument_token: int
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: int        # FIX-135 Item 46: accumulated from ticks (was always 0)
    ts: datetime       # IST candle close time (from clock thread)
    interval_sec: int
    is_synthetic: bool = False  # FIX-020: True for flat carry-forward candles


@dataclass
class _Accumulator:
    """Mutable per-symbol candle accumulator for the current window."""
    open: float
    high: float
    low: float
    close: float
    tick_count: int
    window_start: datetime  # FIX-049: minute boundary for this accumulator
    volume: int = 0  # FIX-135 Item 46: accumulated volume from ticks

    def update(self, ltp: float, volume: int = 0) -> None:
        """Update the accumulator with a new tick (LTP + volume).

        H-9 (Wave-3): the FIX-049 `exchange_timestamp` late-tick discard was removed
        (it was dead — never wired — and a landmine; see CandleStore.on_tick). Every
        tick routed here is accepted; window assignment is clock-driven upstream.
        """
        if ltp > self.high:
            self.high = ltp
        if ltp < self.low:
            self.low = ltp
        self.close = ltp
        self.tick_count += 1
        self.volume += volume


class CandleStore:
    """
    LF9: Build minute OHLC candles from LTP stream.

    on_tick() accumulates LTP per symbol. A clock thread fires every
    candle_interval_sec and closes all active accumulators, emitting
    CandleData to registered callbacks.
    """

    # LF14: generous buffer beyond 1 full trading day (390 = 6.5h * 60m).
    # 500 accommodates multi-inning trail calculations without unbounded growth.
    MAX_HISTORY = 500

    def __init__(
        self,
        logger,
        candle_interval_sec: int = 60,
    ) -> None:
        self._log = logger
        self._candle_interval_sec = candle_interval_sec

        # LF17: single lock guards _accum, _history, _token_map, _on_close_cbs
        self._lock = threading.Lock()
        self._accum: Dict[int, _Accumulator] = {}
        self._history: Dict[int, Deque[CandleData]] = {}
        self._on_close_cbs: List[Callable[[CandleData], None]] = []
        self._token_map: Dict[int, str] = {}
        # FIX-049 / H-9: the last window that actually CLOSED (one interval before
        # the boundary the timer fired on). No longer gates ticks (the late-tick
        # discard was removed in H-9); retained as a correct, observable timestamp
        # used by the synthetic-only guard log.
        self._last_closed_window: Optional[datetime] = None
        # H-9 landmine guard: count consecutive closes that emitted ONLY synthetic
        # (carry-forward) candles, i.e. zero real ticks — surfaces a starved feed
        # instead of letting the store silently drift 100% synthetic.
        self._consecutive_synthetic_only: int = 0

        self._stop_event = threading.Event()
        self._timer_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """LF12: Start clock-based candle close timer thread."""
        self._stop_event.clear()
        self._timer_thread = threading.Thread(
            target=self._timer_loop,
            daemon=True,
            name="candle-store-timer",
        )
        self._timer_thread.start()

    def stop(self) -> None:
        """Stop timer thread cleanly."""
        self._stop_event.set()

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def set_token_map(self, token_map: Dict[int, str]) -> None:
        """LF15: Inject instrument_token -> symbol mapping."""
        with self._lock:
            self._token_map = dict(token_map)

    def gc_sweep(self, valid_tokens: set[int]) -> int:
        """
        FIX-092: Garbage-collect dead tokens from internal state.

        Removes accumulators and history for tokens not in valid_tokens set.
        Called on InstrumentsRefreshed event to clean up delisted symbols.

        Args:
            valid_tokens: Set of instrument_tokens currently in the instrument cache.

        Returns:
            Number of tokens removed.
        """
        with self._lock:
            removed_count = 0

            # Clean _accum
            dead_accum = [t for t in self._accum if t not in valid_tokens]
            for token in dead_accum:
                del self._accum[token]
                removed_count += 1

            # Clean _history
            dead_history = [t for t in self._history if t not in valid_tokens]
            for token in dead_history:
                del self._history[token]
                if token not in dead_accum:  # Don't double-count if already in accum
                    removed_count += 1

            if removed_count > 0:
                self._log.info(
                    f"candle_store.gc_sweep: removed {removed_count} dead tokens",
                    extra={"removed_count": removed_count},
                )

            return removed_count

    def on_tick(self, instrument_token: int, ltp: float, ts: datetime, volume: int = 0) -> None:
        """LF11: Accumulate LTP + volume into the current (clock-driven) candle window.

        Audit 3.4: exchange OHLC from the tick is NEVER used here.
        FIX-135 Item 46: volume accumulated from ticks.

        H-9 (Wave-3): the FIX-049 `exchange_timestamp` late-tick discard was REMOVED.
        It was dead — the live tick dispatcher never passed exchange_timestamp, so it
        never ran — and, combined with the `_last_closed_window` off-by-one, it was a
        LANDMINE: had anyone wired it, every current-minute tick would satisfy
        `tick_window <= last_closed` and be discarded, driving the store 100%
        synthetic (plus a latent naive/aware TypeError). Removed by construction so a
        real tick can never be dropped as "late". Candle windows are clock-driven
        (the timer thread closes on `now_ist()` boundaries), so `ts` (the received/
        exchange tick time) is retained for the caller interface but is INFORMATIONAL
        ONLY — it neither gates nor windows anything.
        """
        with self._lock:
            window_start = now_ist().replace(second=0, microsecond=0)
            if instrument_token in self._accum:
                self._accum[instrument_token].update(ltp, volume)
            else:
                self._accum[instrument_token] = _Accumulator(
                    open=ltp, high=ltp, low=ltp, close=ltp, tick_count=1,
                    window_start=window_start, volume=volume,
                )

    def register_on_candle_close(self, fn: Callable[[CandleData], None]) -> None:
        """LF13: Register candle close callback. Idempotent."""
        with self._lock:
            if fn not in self._on_close_cbs:
                self._on_close_cbs.append(fn)

    def unregister_on_candle_close(self, fn: Callable[[CandleData], None]) -> None:
        """Remove a previously registered candle close callback. No-op if not registered."""
        with self._lock:
            try:
                self._on_close_cbs.remove(fn)
            except ValueError:
                pass

    def get_candles(self, instrument_token: int, n: int = 10) -> List[CandleData]:
        """LF14: Return last N closed candles for the given token. Newest-last (chronological order)."""
        with self._lock:
            hist = self._history.get(instrument_token)
            if hist is None:
                return []
            return list(hist)[-n:]

    def mark_reconnect(self, ts: datetime) -> None:
        """LF16: Discard all partial (unclosed) candles. Preserve closed history."""
        with self._lock:
            count = len(self._accum)
            self._accum.clear()
        self._log.warning(
            "CandleStore: mark_reconnect at %s, discarded %d partial candle(s)"
            % (ts.isoformat(), count)
        )

    # ------------------------------------------------------------------ #
    # Timer thread (LF12)
    # ------------------------------------------------------------------ #

    def _timer_loop(self) -> None:
        """
        Clock-aligned candle close. Fires at candle_interval_sec boundaries.

        FIX-080: Uses time.time() for wall-clock boundary alignment (candle close
        timing must match market time), but guards sleep_sec with max(0, x) to
        prevent ValueError on negative sleep from NTP step or leap second.
        """
        while not self._stop_event.is_set():
            now_ts = time.time()
            next_fire = (
                (now_ts // self._candle_interval_sec) + 1
            ) * self._candle_interval_sec
            sleep_sec = next_fire - now_ts
            # FIX-080: Guard against negative sleep (NTP step, leap second)
            sleep_sec = max(0.0, sleep_sec)
            if self._stop_event.wait(sleep_sec):
                break
            self._close_candles()

    def _close_candles(self) -> None:
        """Close all active accumulators and emit CandleData to callbacks.

        FIX-020: If a token had no ticks this minute BUT has previous history,
        emit a synthetic flat candle: O=H=L=C=prev_close, V=0, is_synthetic=True.

        Lock is acquired in two short critical sections to avoid holding
        it while calling user callbacks (LF17: no nesting, no deadlock risk).
        """
        close_ts = now_ist()
        # H-9: the timer fires AT the boundary (e.g. 10:01:00) to close the window
        # that just ENDED (10:00). `window_being_closed` here is the interval that
        # STARTS now; the window that actually closed is one interval earlier.
        window_being_closed = close_ts.replace(second=0, microsecond=0)

        # Section 1: snapshot and reset accumulators, identify synthetic candidates
        with self._lock:
            to_close = dict(self._accum)
            self._accum.clear()
            # H-9 off-by-one fix: last-closed = the interval that just ended, i.e.
            # ONE interval before the boundary the timer fired on (was stamping the
            # new/current window). Harmless today (no live reader) but correct, and
            # it defuses the late-tick landmine had the discard ever been re-wired.
            self._last_closed_window = window_being_closed - timedelta(
                seconds=self._candle_interval_sec
            )
            token_map_snapshot = dict(self._token_map)
            # FIX-020: tokens with no ticks but previous history → synthetic candle
            tokens_with_no_ticks = set(token_map_snapshot.keys()) - set(to_close.keys())
            synthetic_candidates: Dict[int, float] = {}
            for token in tokens_with_no_ticks:
                hist = self._history.get(token)
                if hist and len(hist) > 0:
                    # Emit synthetic candle with prev_close price
                    synthetic_candidates[token] = hist[-1].close

            # H-9 landmine guard: if a close emits ONLY synthetic (carry-forward)
            # candles despite mapped tokens — zero real ticks this interval — the
            # feed may be starved/misconfigured. Make it LOUD instead of letting the
            # store drift 100% synthetic unnoticed (the state the late-tick landmine
            # would have caused). Reset on any real candle.
            if to_close:
                self._consecutive_synthetic_only = 0
            elif synthetic_candidates:
                self._consecutive_synthetic_only += 1
                if (self._consecutive_synthetic_only == 3
                        or self._consecutive_synthetic_only % 30 == 0):
                    self._log.warning(
                        "CandleStore: %d consecutive interval(s) with ZERO real "
                        "ticks (last_closed=%s, %d token(s) carry-forward only) — "
                        "tick feed may be starved/misconfigured; candles are synthetic",
                        self._consecutive_synthetic_only,
                        self._last_closed_window.isoformat()
                        if self._last_closed_window else "n/a",
                        len(synthetic_candidates),
                    )

        # Build CandleData objects outside lock
        candles: List[CandleData] = []

        # Real candles from ticks
        for token, acc in to_close.items():
            symbol = token_map_snapshot.get(token, str(token))
            candles.append(
                CandleData(
                    instrument_token=token,
                    symbol=symbol,
                    open=acc.open,
                    high=acc.high,
                    low=acc.low,
                    close=acc.close,
                    volume=acc.volume,
                    ts=close_ts,
                    interval_sec=self._candle_interval_sec,
                    is_synthetic=False,
                )
            )

        # FIX-020: Synthetic flat candles for silent tokens
        for token, prev_close in synthetic_candidates.items():
            symbol = token_map_snapshot.get(token, str(token))
            candles.append(
                CandleData(
                    instrument_token=token,
                    symbol=symbol,
                    open=prev_close,
                    high=prev_close,
                    low=prev_close,
                    close=prev_close,
                    volume=0,
                    ts=close_ts,
                    interval_sec=self._candle_interval_sec,
                    is_synthetic=True,
                )
            )

        if not candles:
            # No real or synthetic candles to emit
            return

        # Section 2: persist to history and snapshot callbacks
        with self._lock:
            for candle in candles:
                tok = candle.instrument_token
                if tok not in self._history:
                    self._history[tok] = deque(maxlen=self.MAX_HISTORY)
                self._history[tok].append(candle)
            callbacks = list(self._on_close_cbs)

        # Fire callbacks outside lock (LF17)
        for candle in candles:
            for cb in callbacks:
                try:
                    cb(candle)
                except Exception as exc:
                    self._log.error("CandleStore: callback raised: %s" % exc)
