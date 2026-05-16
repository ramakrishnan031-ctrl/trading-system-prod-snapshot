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
from datetime import datetime
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
    volume: int        # always 0 (LF11: unreliable from ticks)
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

    def update(self, ltp: float, exchange_timestamp: Optional[datetime] = None) -> bool:
        """FIX-049: Update accumulator with new tick. Returns False if tick discarded (late).

        Args:
            ltp: Last traded price
            exchange_timestamp: Optional exchange timestamp of the tick. If provided and
                              tick belongs to an already-closed minute, it's discarded.

        Returns:
            True if tick was accepted, False if discarded (late tick from previous minute)
        """
        # FIX-049: Discard ticks from already-closed minutes
        if exchange_timestamp is not None:
            if exchange_timestamp < self.window_start:
                # Tick is from a previous minute that's already closed
                return False

        if ltp > self.high:
            self.high = ltp
        if ltp < self.low:
            self.low = ltp
        self.close = ltp
        self.tick_count += 1
        return True


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
        # FIX-049: Track last closed window to detect late ticks
        self._last_closed_window: Optional[datetime] = None

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

    def on_tick(self, instrument_token: int, ltp: float, ts: datetime, exchange_timestamp: Optional[datetime] = None) -> None:
        """LF11: Accumulate LTP into current candle window. LTP ONLY.

        Audit 3.4: exchange ohlc from tick is NEVER used here.
        FIX-049: exchange_timestamp param added. If provided and tick belongs to
                 an already-closed minute, the tick is discarded and DEBUG logged.
        """
        with self._lock:
            # FIX-049: Check if tick belongs to an already-closed window
            if exchange_timestamp is not None and self._last_closed_window is not None:
                tick_window = exchange_timestamp.replace(second=0, microsecond=0, tzinfo=None)
                last_closed_naive = self._last_closed_window.replace(tzinfo=None)
                if tick_window <= last_closed_naive:
                    # Late tick from already-closed window → discard
                    symbol = self._token_map.get(instrument_token, str(instrument_token))
                    self._log.debug(
                        f"CandleStore: discarded late tick for {symbol} "
                        f"(exchange_ts={exchange_timestamp.isoformat()}, "
                        f"tick_window={tick_window.isoformat()}, "
                        f"last_closed={self._last_closed_window.isoformat()})"
                    )
                    return

            # FIX-049: Calculate current window start (minute boundary)
            now = exchange_timestamp if exchange_timestamp is not None else now_ist()
            window_start = now.replace(second=0, microsecond=0)

            if instrument_token in self._accum:
                # Update existing accumulator
                accepted = self._accum[instrument_token].update(ltp, exchange_timestamp)
                if not accepted:
                    # FIX-049: Late tick from same window but before accumulator window_start
                    # (This shouldn't normally happen, but kept as defense-in-depth)
                    symbol = self._token_map.get(instrument_token, str(instrument_token))
                    self._log.debug(
                        f"CandleStore: discarded late tick for {symbol} "
                        f"(exchange_ts={exchange_timestamp.isoformat() if exchange_timestamp else 'None'}, "
                        f"window_start={self._accum[instrument_token].window_start.isoformat()})"
                    )
            else:
                # Create new accumulator for this window
                self._accum[instrument_token] = _Accumulator(
                    open=ltp, high=ltp, low=ltp, close=ltp, tick_count=1,
                    window_start=window_start
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
        """LF14: Return last N closed candles for the given token."""
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
        # FIX-049: Track the window we're closing (for late tick detection)
        window_being_closed = close_ts.replace(second=0, microsecond=0)

        # Section 1: snapshot and reset accumulators, identify synthetic candidates
        with self._lock:
            to_close = dict(self._accum)
            self._accum.clear()
            # FIX-049: Update last closed window timestamp
            self._last_closed_window = window_being_closed
            token_map_snapshot = dict(self._token_map)
            # FIX-020: tokens with no ticks but previous history → synthetic candle
            tokens_with_no_ticks = set(token_map_snapshot.keys()) - set(to_close.keys())
            synthetic_candidates: Dict[int, float] = {}
            for token in tokens_with_no_ticks:
                hist = self._history.get(token)
                if hist and len(hist) > 0:
                    # Emit synthetic candle with prev_close price
                    synthetic_candidates[token] = hist[-1].close

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
                    volume=0,
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
