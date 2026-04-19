"""
data/live_feed.py  -  KiteTicker WebSocket manager (LF1-LF8)

Manages KiteTicker connection, symbol subscriptions, and LTP tick
distribution via a bounded queue + single consumer thread.

Audit 3.3: NO thread-per-callback. Single consumer drains queue.
"""

from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Callable, List, Optional, Set

from kiteconnect import KiteTicker

from core.logger import log_exception
from core.time_authority import now_ist


class LiveFeedManager:
    """
    LF1: KiteTicker WebSocket manager.

    Receives ticks from Kite broker WebSocket and distributes them to
    registered callbacks via a bounded queue and single consumer thread
    (audit 3.3: no thread-per-callback).
    """

    # LF5: bounded queue capacity
    TICK_QUEUE_CAPACITY = 10_000

    def __init__(
        self,
        api_key: str,
        access_token: str,
        logger,
        on_critical_failure: Optional[Callable[[str], None]] = None,
        max_reconnect_attempts: int = 10,
        reconnect_delay_sec: int = 5,
    ) -> None:
        # LF2
        self._api_key = api_key
        self._access_token = access_token
        self._log = logger
        self._on_critical_failure = on_critical_failure
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay_sec = reconnect_delay_sec

        # LF4: tracked subscriptions for auto-resubscribe on reconnect
        self._subscribed: Set[int] = set()

        # LF5: tick distribution
        self._callbacks: List[Callable[[list], None]] = []
        self._lock = threading.Lock()
        self._tick_queue: queue.Queue = queue.Queue(maxsize=self.TICK_QUEUE_CAPACITY)

        # LF3: connection state
        self._connected = False
        self._disconnect_time: Optional[datetime] = None
        self._reconnect_notified = False  # per-disconnect flag
        self._stop_event = threading.Event()
        self._consumer_thread: Optional[threading.Thread] = None
        self._ticker: Optional[KiteTicker] = None

        # LF7: candle_store reconnect notifier
        self._on_reconnect_cb: Optional[Callable[[datetime], None]] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        """LF3: Start consumer thread and KiteTicker in background thread."""
        self._stop_event.clear()
        self._consumer_thread = threading.Thread(
            target=self._consume_ticks,
            daemon=True,
            name="live-feed-consumer",
        )
        self._consumer_thread.start()
        self._ticker = self._create_ticker()
        self._ticker.connect(threaded=True)

    def disconnect(self) -> None:
        """LF3: Stop consumer thread and close WebSocket cleanly."""
        self._stop_event.set()
        if self._ticker is not None:
            try:
                self._ticker.close()
            except Exception:
                pass
        self._connected = False

    def is_connected(self) -> bool:
        """LF3: Return current connection state."""
        return self._connected

    def subscribe(self, instrument_tokens: List[int]) -> None:
        """LF4: Subscribe tokens. MODE_LTP default. Tracks in _subscribed."""
        with self._lock:
            new = [t for t in instrument_tokens if t not in self._subscribed]
            self._subscribed.update(instrument_tokens)
        if new and self._connected and self._ticker is not None:
            self._ticker.subscribe(new)
            self._ticker.set_mode(KiteTicker.MODE_LTP, new)

    def unsubscribe(self, instrument_tokens: List[int]) -> None:
        """LF4: Unsubscribe tokens and remove from _subscribed set."""
        with self._lock:
            self._subscribed.difference_update(instrument_tokens)
        if self._connected and self._ticker is not None:
            self._ticker.unsubscribe(instrument_tokens)

    def set_mode(self, tokens: List[int], mode: str) -> None:
        """LF4: Override tick mode for specified tokens."""
        if self._connected and self._ticker is not None:
            self._ticker.set_mode(mode, tokens)

    def register_callback(self, fn: Callable[[list], None]) -> None:
        """LF5: Register a tick consumer callback. No duplicates."""
        with self._lock:
            if fn not in self._callbacks:
                self._callbacks.append(fn)

    def unregister_callback(self, fn: Callable) -> None:
        """LF5: Remove a previously registered callback."""
        with self._lock:
            try:
                self._callbacks.remove(fn)
            except ValueError:
                pass

    def set_on_reconnect_callback(self, fn: Callable[[datetime], None]) -> None:
        """LF7: Inject candle_store reconnect notifier (called once per gap)."""
        self._on_reconnect_cb = fn

    # ------------------------------------------------------------------ #
    # KiteTicker event callbacks
    # ------------------------------------------------------------------ #

    def _on_ticks(self, ws, ticks: list) -> None:
        """LF5: Normalize ticks and push to bounded queue. Drop oldest if full."""
        for raw in ticks:
            tick = {
                "instrument_token": raw.get("instrument_token"),
                "last_price": float(raw.get("last_price", 0.0)),
                "timestamp": raw.get("exchange_timestamp") or raw.get("timestamp"),
                "volume": raw.get("volume_traded"),
            }
            # LF6: NOTE - raw["ohlc"] is DAY's OHLC, NOT minute OHLC. Not included.
            if not self._tick_queue.full():
                self._tick_queue.put_nowait(tick)
            else:
                # Drop oldest to make room (LF5)
                try:
                    self._tick_queue.get_nowait()
                except queue.Empty:
                    pass
                self._tick_queue.put_nowait(tick)
                self._log.warning(
                    "LiveFeedManager: tick queue full, dropped oldest tick"
                )

    def _on_connect(self, ws, response) -> None:
        """LF3 + BL-11: Successful connection. Re-subscribe all tracked tokens.

        BL-11: the re-subscribe call is wrapped in try/except. A broker
        rejection or transient failure during reconnect must NOT propagate
        into the kiteconnect ticker thread (which would crash it and leave
        the feed silently dead). On failure, log CRITICAL with the
        grep-friendly tag ``re-subscribe after connect FAILED`` and let the
        next _on_connect cycle retry.

        First-connect with an empty _subscribed is a no-op (nothing to push
        yet). Every subsequent _on_connect re-pushes the full set.
        """
        self._connected = True
        self._reconnect_notified = False
        self._log.info("LiveFeedManager: connected to KiteTicker")
        with self._lock:
            tokens = list(self._subscribed)
        if not tokens:
            return
        # BL-11: enumerate the re-subscribe size so ops can grep
        # "re-subscribing to N tokens after connect" during incident triage.
        self._log.info(
            "live_feed: re-subscribing to %d tokens after connect",
            len(tokens),
            extra={"token_count": len(tokens), "tokens": sorted(tokens)},
        )
        try:
            ws.subscribe(tokens)
            ws.set_mode(KiteTicker.MODE_LTP, tokens)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "live_feed: re-subscribe after connect FAILED; feed is live "
                "but no ticks will flow (will retry on next _on_connect)",
                extra={
                    "token_count": len(tokens),
                    "tokens": sorted(tokens),
                    "error": str(exc),
                },
            )

    def _on_close(self, ws, code, reason) -> None:
        """LF3: Connection closed. Record disconnect time for gap tracking."""
        self._connected = False
        self._disconnect_time = now_ist()
        self._log.warning(
            f"LiveFeedManager: disconnected code={code} reason={reason}"
        )

    def _on_error(self, ws, code, reason) -> None:
        """Error callback."""
        self._log.error(f"LiveFeedManager: error code={code} reason={reason}")

    def _on_reconnect(self, ws, attempts_count: int) -> None:
        """LF7: Called on each reconnect attempt by KiteTicker."""
        now = now_ist()
        gap_sec: Optional[float] = None
        if self._disconnect_time is not None:
            gap_sec = (now - self._disconnect_time).total_seconds()

        self._log.warning(
            "LiveFeedManager: reconnect attempt %d%s"
            % (
                attempts_count,
                (" gap=%.0fs" % gap_sec) if gap_sec is not None else "",
            )
        )

        # LF7: Notify candle_store once per disconnect event
        if not self._reconnect_notified:
            self._reconnect_notified = True
            if self._on_reconnect_cb is not None:
                self._on_reconnect_cb(now)

        # LF7: Alert if gap > 10 min
        if gap_sec is not None and gap_sec > 600:
            self._log.critical(
                "LiveFeedManager: feed gap > 10 min (%.0fs)" % gap_sec
            )
            if self._on_critical_failure is not None:
                self._on_critical_failure(
                    "feed gap %.0fs > 10 min" % gap_sec
                )

    def _on_noreconnect(self, ws) -> None:
        """LF3: Max reconnect attempts exhausted. Fire critical failure."""
        self._connected = False
        self._log.critical(
            "LiveFeedManager: max reconnect attempts (%d) exhausted"
            % self._max_reconnect_attempts
        )
        if self._on_critical_failure is not None:
            self._on_critical_failure(
                "KiteTicker max reconnect (%d) exhausted"
                % self._max_reconnect_attempts
            )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _create_ticker(self) -> KiteTicker:
        """Wire up KiteTicker with our callbacks and reconnect params."""
        ticker = KiteTicker(
            self._api_key,
            self._access_token,
            reconnect_max_tries=self._max_reconnect_attempts,
            reconnect_max_delay=self._reconnect_delay_sec,
        )
        ticker.on_ticks = self._on_ticks
        ticker.on_connect = self._on_connect
        ticker.on_close = self._on_close
        ticker.on_error = self._on_error
        ticker.on_reconnect = self._on_reconnect
        ticker.on_noreconnect = self._on_noreconnect
        return ticker

    def _consume_ticks(self) -> None:
        """LF5: Single consumer thread. Drains queue, invokes callbacks.

        Audit 3.3: One thread, not one-per-callback.
        """
        while not self._stop_event.is_set():
            try:
                batch = [self._tick_queue.get(timeout=0.1)]
                # Drain remaining items without blocking
                try:
                    while True:
                        batch.append(self._tick_queue.get_nowait())
                except queue.Empty:
                    pass
                with self._lock:
                    callbacks = list(self._callbacks)
                for cb in callbacks:
                    try:
                        cb(batch)
                    except Exception as exc:
                        self._log.error(
                            "LiveFeedManager: callback raised: %s" % exc
                        )
            except queue.Empty:
                continue
