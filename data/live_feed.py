"""
data/live_feed.py  -  KiteTicker WebSocket manager (LF1-LF8)

Manages KiteTicker connection, symbol subscriptions, and LTP tick
distribution via a bounded queue + single consumer thread.

Audit 3.3: NO thread-per-callback. Single consumer drains queue.
"""

from __future__ import annotations

import queue
import threading
import weakref
from datetime import datetime
from typing import Callable, List, Optional, Set

from kiteconnect import KiteTicker

from core.effect_telemetry import handle as _effect_handle
from core.logger import log_exception
from alerts.delivery import send_alert_recorded
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
        kill_switch=None,  # FIX-029: optional KillSwitch for soft_kill on queue full
        max_reconnect_attempts: int = 10,
        reconnect_delay_sec: int = 5,
        paper_mode: bool = False,
        # B.6 / Audit 12: tick-age watchdog (live mode only).
        tick_stale_threshold_sec: int = 30,
        watchdog_check_interval_sec: int = 10,
        market_windows=None,
        # FIX-059: chunked subscription to avoid broker API limits
        subscription_batch_size: int = 50,
    ) -> None:
        # LF2
        self._api_key = api_key
        self._access_token = access_token
        self._paper_mode = paper_mode
        # effect-telemetry (ledger #1, frozen contract A2.3): dormant tripwire
        # — a tick subscription made. ENFORCES IA-P1-06 (the tick path is
        # dormant BY DECISION e754c7e; acted>0 = the decision silently ended).
        self._fx_subscribe = _effect_handle("live_feed")
        # G.2 (2026-04-25): never log even a prefix of the API key or access
        # token. Logs are read by support, cloud providers, and anyone with
        # incident access; even 6 chars narrows the brute-force search space
        # for an attacker who already has the secret length and Kite's
        # generation policy. Booleans are sufficient for boot diagnostics.
        logger.info(
            "LiveFeedManager init: paper_mode=%s api_key_set=%s token_set=%s",
            paper_mode,
            bool(api_key),
            bool(access_token),
        )
        self._log = logger
        self._on_critical_failure = on_critical_failure
        self._kill_switch = kill_switch  # FIX-029
        self._max_reconnect_attempts = max_reconnect_attempts
        self._reconnect_delay_sec = reconnect_delay_sec
        self._subscription_batch_size = subscription_batch_size  # FIX-059

        # LF4: tracked subscriptions for auto-resubscribe on reconnect
        self._subscribed: Set[int] = set()

        # LF5: tick distribution
        # FIX-103: Use weak references to prevent memory leaks when callbacks
        # are never unregistered. Bound methods use WeakMethod, functions use ref.
        self._callbacks: List = []  # List[weakref.ref | weakref.WeakMethod]
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

        # B.6 / Audit 12: tick-age watchdog state.
        # KiteTicker can present as connected (no on_close fired) yet stop
        # delivering ticks (half-open socket / silent broker drop). Without a
        # watchdog the system silently misses entries until reconcile/EOD
        # surfaces it. The watchdog samples _last_tick_at every
        # watchdog_check_interval_sec; if no tick arrived for
        # tick_stale_threshold_sec during market hours, fire ONE critical
        # alert and force a reconnect via ticker.close() (which triggers
        # _on_close + the kiteconnect auto-reconnect path). Critical alert
        # fires ONCE per stale episode (cleared when ticks resume).
        #
        # FIX-064: The watchdog sets a flag instead of calling ticker.close()
        # directly; the consumer thread checks the flag and calls .close()
        # from its own thread context to avoid cross-thread call hazards.
        self._tick_stale_threshold_sec = int(tick_stale_threshold_sec)
        self._watchdog_check_interval_sec = int(watchdog_check_interval_sec)
        self._market_windows = market_windows
        self._last_tick_at: Optional[datetime] = None
        self._last_tick_lock = threading.Lock()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._watchdog_alert_fired: bool = False
        self._force_reconnect = threading.Event()  # FIX-064
        # FIX-088: ticker thread identity for checkpoint guard
        self._ticker_thread_id: Optional[int] = None

        # FIX-134 Item 37: reconnect count for metrics/alerting
        self._reconnect_count: int = 0
        self._notifier = None  # set via set_notifier() from main.py
        self._mode_label: str = "LIVE"

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        """LF3: Start consumer thread and KiteTicker in background thread."""
        if self._paper_mode:
            self._log.info("LiveFeedManager: paper mode — skipping WebSocket connection")
            return
        self._stop_event.clear()
        self._consumer_thread = threading.Thread(
            target=self._consume_ticks,
            daemon=True,
            name="live-feed-consumer",
        )
        self._consumer_thread.start()
        # B.6 / Audit 12: start tick-age watchdog (live mode only).
        self._start_watchdog()
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
        # B.6: join watchdog thread on shutdown (best-effort, bounded wait).
        if self._watchdog_thread is not None:
            self._watchdog_thread.join(
                timeout=self._watchdog_check_interval_sec + 1
            )
            self._watchdog_thread = None

    def is_connected(self) -> bool:
        """LF3: Return current connection state."""
        return self._connected

    def subscribe(self, instrument_tokens: List[int]) -> None:
        """
        LF4: Subscribe tokens. MODE_LTP default. Tracks in _subscribed.
        FIX-059: Batched subscription (default 50 tokens/batch) to avoid broker limits.
        """
        # effect-telemetry (frozen A2.3): ANY call is the dormancy tripwire
        # (only latent caller: order_placer exit-retry — IA-P1-06).
        self._fx_subscribe.inc()
        with self._lock:
            new = [t for t in instrument_tokens if t not in self._subscribed]
            self._subscribed.update(instrument_tokens)

        if new and self._connected and self._ticker is not None:
            # FIX-059: chunk tokens into batches to avoid broker API limits
            batch_size = self._subscription_batch_size
            for i in range(0, len(new), batch_size):
                batch = new[i : i + batch_size]
                self._ticker.subscribe(batch)
                self._ticker.set_mode(KiteTicker.MODE_LTP, batch)
                self._log.info(
                    "LiveFeedManager: subscribed batch %d/%d (%d tokens)",
                    i // batch_size + 1,
                    (len(new) + batch_size - 1) // batch_size,
                    len(batch),
                )

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
        """
        LF5: Register a tick consumer callback. No duplicates.

        FIX-103: Store weak reference to prevent memory leaks. Bound methods
        use WeakMethod; plain functions use ref. Dead refs auto-cleaned on invoke.
        """
        with self._lock:
            # Check if already registered (compare actual callables)
            for weak_cb in self._callbacks:
                if weak_cb() is fn:
                    return  # Already registered

            # Create appropriate weak reference
            if hasattr(fn, '__self__'):
                # Bound method: use WeakMethod
                weak_ref = weakref.WeakMethod(fn)
            else:
                # Plain function: use ref
                weak_ref = weakref.ref(fn)

            self._callbacks.append(weak_ref)

    def unregister_callback(self, fn: Callable) -> None:
        """
        LF5: Remove a previously registered callback.

        FIX-103: Find and remove the weak reference matching the given callable.
        """
        with self._lock:
            for i, weak_cb in enumerate(self._callbacks):
                if weak_cb() is fn:
                    del self._callbacks[i]
                    return

    def set_on_reconnect_callback(self, fn: Callable[[datetime], None]) -> None:
        """LF7: Inject candle_store reconnect notifier (called once per gap)."""
        self._on_reconnect_cb = fn

    def set_notifier(self, notifier, mode_label: str = "LIVE") -> None:
        """FIX-134: Inject Telegram notifier for reconnect/noreconnect alerts."""
        self._notifier = notifier
        self._mode_label = mode_label

    @property
    def reconnect_count(self) -> int:
        """FIX-134 Item 37: total reconnect attempts since startup."""
        return self._reconnect_count

    # ------------------------------------------------------------------ #
    # KiteTicker event callbacks
    # ------------------------------------------------------------------ #

    def _on_ticks(self, ws, ticks: list) -> None:
        """LF5: Normalize ticks and push to bounded queue. Drop oldest if full."""
        # B.6 / Audit 12: stamp last-tick time on every batch so the watchdog
        # can spot a silent feed. now_ist() returns aware datetime; cheap.
        if ticks:
            with self._last_tick_lock:
                self._last_tick_at = now_ist()
                if self._watchdog_alert_fired:
                    # Ticks resumed -> clear so the next stale episode can
                    # alert again.
                    self._watchdog_alert_fired = False
                    self._log.info(
                        "LiveFeedManager: ticks resumed; watchdog alert cleared"
                    )
        for raw in ticks:
            # Audit #20: extract top-of-book bid/ask when depth is present
            # (MODE_FULL). shadow_tracker uses these for SL simulation to
            # avoid LTP-at-ask optimism. Missing depth falls back to 0.0
            # and shadow_tracker reverts to LTP-based checks.
            bid = 0.0
            ask = 0.0
            depth = raw.get("depth") or {}
            if depth:
                buys = depth.get("buy") or []
                sells = depth.get("sell") or []
                if buys:
                    bid = float(buys[0].get("price", 0.0) or 0.0)
                if sells:
                    ask = float(sells[0].get("price", 0.0) or 0.0)
            tick = {
                "instrument_token": raw.get("instrument_token"),
                "last_price": float(raw.get("last_price", 0.0)),
                "bid": bid,
                "ask": ask,
                "timestamp": raw.get("exchange_timestamp") or raw.get("timestamp"),
                "volume": raw.get("volume_traded"),
            }
            # LF6: NOTE - raw["ohlc"] is DAY's OHLC, NOT minute OHLC. Not included.
            # FIX-029: queue.Full triggers soft_kill instead of dropping ticks
            if not self._tick_queue.full():
                self._tick_queue.put_nowait(tick)
            else:
                self._log.critical(
                    "LiveFeedManager: tick queue full (capacity=%d) - triggering soft_kill "
                    "LIVEFEED_QUEUE_FULL: consumer thread may be dead or subscribers blocked",
                    self.TICK_QUEUE_CAPACITY,
                )
                if self._kill_switch is not None:
                    self._kill_switch.soft_kill("LIVEFEED_QUEUE_FULL")
                else:
                    self._log.critical(
                        "LiveFeedManager: no kill_switch configured - cannot trigger soft_kill"
                    )
                # Still try to enqueue the tick (may block briefly or raise)
                try:
                    self._tick_queue.put_nowait(tick)
                except queue.Full:
                    # If put_nowait fails, we've already triggered soft_kill
                    pass

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
        """LF7 / FIX-134: Called on each reconnect attempt by KiteTicker."""
        self._reconnect_count += 1
        now = now_ist()
        gap_sec: Optional[float] = None
        if self._disconnect_time is not None:
            gap_sec = (now - self._disconnect_time).total_seconds()

        self._log.warning(
            "LiveFeedManager: reconnect attempt %d (total=%d)%s"
            % (
                attempts_count,
                self._reconnect_count,
                (" gap=%.0fs" % gap_sec) if gap_sec is not None else "",
            )
        )

        # FIX-134 Item 37: Telegram WARNING on every reconnect
        if self._notifier is not None:
            try:
                self._notifier.send(
                    severity="WARNING",
                    title=f"[{self._mode_label}] WebSocket Reconnecting",
                    body=(
                        f"Attempt {attempts_count} (total={self._reconnect_count})"
                        + (f"\nGap: {gap_sec:.0f}s" if gap_sec else "")
                    ),
                    source_module="live_feed",
                )
            except Exception:
                pass

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
        """LF3 / FIX-134: Max reconnect attempts exhausted.

        During market hours this is a genuine incident: critical failure +
        SOFT_KILL + CRITICAL alert.

        FIX-189 (P1-B): OUTSIDE market hours the WebSocket legitimately cannot
        authenticate — the access token expires overnight and Zerodha runs
        nightly maintenance. That produced the false 07:07 "KiteTicker max
        reconnect exhausted" CRITICAL while the service was (wrongly) running
        overnight. When we can tell we are off-hours, downgrade to a single
        WARNING and do NOT SOFT_KILL or escalate. If no market_windows was
        injected we fail safe and keep the legacy escalation.
        """
        self._connected = False

        during_market = False
        if self._market_windows is not None:
            try:
                during_market = self._market_windows.is_market_open(now_ist())
            except Exception:
                during_market = True  # fail safe -> escalate

        if self._market_windows is not None and not during_market:
            self._log.warning(
                "LiveFeedManager: max reconnect attempts (%d) exhausted outside "
                "market hours — token likely expired (overnight/maintenance); "
                "not escalating (no SOFT_KILL / no CRITICAL)."
                % self._max_reconnect_attempts
            )
            if self._notifier is not None:
                try:
                    self._notifier.send(
                        severity="WARNING",
                        title=f"[{self._mode_label}] WebSocket idle off-hours",
                        body=(
                            "Max reconnects exhausted outside market hours "
                            "(expected: token expired / broker maintenance). "
                            "No action taken."
                        ),
                        source_module="live_feed",
                    )
                except Exception:
                    pass
            return

        self._log.critical(
            "LiveFeedManager: max reconnect attempts (%d) exhausted"
            % self._max_reconnect_attempts
        )
        if self._on_critical_failure is not None:
            self._on_critical_failure(
                "KiteTicker max reconnect (%d) exhausted"
                % self._max_reconnect_attempts
            )
        # FIX-134 Item 37: trigger SOFT_KILL when max attempts reached
        if self._kill_switch is not None:
            self._kill_switch.soft_kill("LIVEFEED_RECONNECT_EXHAUSTED")
        # FIX-134: CRITICAL Telegram alert
        # == ALERT DELIVERY CONTRACT (Phase 1, 09-Aug-2026) =================
        # BOUNDARY TRACED BEFORE CONVERTING, not assumed from Phase 0: the
        # `_log.critical`, the `_on_critical_failure` callback and the
        # `soft_kill` above ALL run before this block, and the callback itself
        # calls `soft_kill` first and only then notifies. A notification failure
        # therefore cannot skip, delay or alter the SOFT_KILL (INVARIANT 1).
        # The swallow is NOT removed -- the helper never raises -- it now leaves
        # a machine-readable record instead of vanishing (INVARIANT 2).
        # Return value ignored on purpose: nothing here may branch on delivery.
        send_alert_recorded(
            self._notifier, self._log,
            severity="ERROR",
            title=f"[{self._mode_label}] WebSocket DEAD -- Max Reconnects Exhausted",
            body=(
                f"Max attempts: {self._max_reconnect_attempts}\n"
                f"Total reconnects this session: {self._reconnect_count}\n"
                f"SOFT_KILL triggered. Manual intervention required."
            ),
            source_module="live_feed",
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
        """
        LF5: Single consumer thread. Drains queue, invokes callbacks.

        Audit 3.3: One thread, not one-per-callback.

        FIX-029: Consumer thread is IMMORTAL. All callback exceptions are caught,
        logged as CRITICAL with full traceback, but NEVER re-raised. This ensures
        a single misbehaving subscriber cannot kill the tick distribution thread
        and starve the entire system of live data.

        FIX-064: Checks _force_reconnect flag and calls ticker.close() from
        this thread context (not from watchdog thread) to avoid cross-thread
        call hazards.

        FIX-088: Records thread identity for state_store checkpoint guard.
        """
        # FIX-088: capture ticker thread ID for checkpoint guard
        import threading
        self._ticker_thread_id = threading.get_ident()

        while not self._stop_event.is_set():
            # FIX-064: Check reconnect flag FIRST (before queue timeout)
            if self._force_reconnect.is_set():
                self._force_reconnect.clear()
                ticker = self._ticker
                if ticker is not None:
                    try:
                        self._log.info(
                            "LiveFeedManager: consumer thread closing ticker "
                            "due to force_reconnect flag"
                        )
                        ticker.close()
                    except Exception as exc:
                        self._log.error(
                            "LiveFeedManager: ticker.close raised: %s", exc,
                        )
            try:
                batch = [self._tick_queue.get(timeout=0.1)]
                # Drain remaining items without blocking
                try:
                    while True:
                        batch.append(self._tick_queue.get_nowait())
                except queue.Empty:
                    pass

                # FIX-079: Validate tick schema before dispatch to prevent hollow
                # ticks (circuit breaker mode: instrument_token present but no
                # last_price) from causing KeyError in subscribers and crashing
                # the consumer thread. Discard hollow ticks; log DEBUG.
                validated_batch = []
                for tick in batch:
                    token = tick.get("instrument_token")
                    ltp = tick.get("last_price")
                    if token is None or ltp is None or ltp == 0.0:
                        self._log.debug(
                            "LiveFeedManager: hollow tick discarded token=%s ltp=%s",
                            token, ltp,
                        )
                        continue
                    validated_batch.append(tick)

                # Skip dispatch if all ticks were hollow
                if not validated_batch:
                    continue

                # FIX-103: Resolve weak refs and filter out dead ones
                with self._lock:
                    weak_callbacks = list(self._callbacks)
                    # Clean up dead references inline
                    self._callbacks = [wc for wc in self._callbacks if wc() is not None]

                # Dereference and invoke alive callbacks
                callbacks = [wc() for wc in weak_callbacks]
                callbacks = [cb for cb in callbacks if cb is not None]

                for cb in callbacks:
                    # FIX-029: bare except with CRITICAL log + traceback
                    # DO NOT re-raise: consumer thread must be immortal
                    try:
                        cb(validated_batch)
                    except Exception as exc:
                        log_exception(self._log, exc)
                        # Extract callback name for better diagnostics
                        cb_name = getattr(cb, '__name__', None) or getattr(
                            cb, '__class__', None
                        ) or repr(cb)
                        self._log.critical(
                            "LiveFeedManager: subscriber callback raised exception and was "
                            "caught to protect consumer thread. Callback: %s, Exception: %s",
                            cb_name,
                            str(exc),
                        )
            except queue.Empty:
                continue

    # ------------------------------------------------------------------ #
    # FIX-029 — Consumer thread health check
    # ------------------------------------------------------------------ #

    def _check_consumer_health(self) -> None:
        """
        FIX-029: Periodic health-check for consumer thread.

        If the thread is detected as dead (not alive), attempt to restart it once.
        If restart fails, trigger soft_kill to halt new trading and alert operators.

        This method is called from the watchdog loop to ensure continuous monitoring.
        """
        if self._consumer_thread is None:
            return  # Not started yet

        if self._consumer_thread.is_alive():
            return  # Thread is healthy

        # Thread is dead - this is CRITICAL
        self._log.critical(
            "LiveFeedManager: consumer thread is DEAD - attempting restart once"
        )

        # Attempt restart
        try:
            self._consumer_thread = threading.Thread(
                target=self._consume_ticks,
                daemon=True,
                name="live-feed-consumer-restarted",
            )
            self._consumer_thread.start()
            self._log.warning(
                "LiveFeedManager: consumer thread restarted successfully"
            )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "LiveFeedManager: consumer thread restart FAILED - triggering soft_kill"
            )
            if self._kill_switch is not None:
                self._kill_switch.soft_kill("LIVEFEED_CONSUMER_THREAD_DEAD")
            else:
                self._log.critical(
                    "LiveFeedManager: no kill_switch configured - cannot trigger soft_kill"
                )

    # ------------------------------------------------------------------ #
    # B.6 / Audit 12 — tick-age watchdog
    # ------------------------------------------------------------------ #

    def tick_age_seconds(self) -> Optional[float]:
        """B.6: seconds since the last tick was observed; None if no tick yet."""
        with self._last_tick_lock:
            last = self._last_tick_at
        if last is None:
            return None
        return (now_ist() - last).total_seconds()

    def _start_watchdog(self) -> None:
        """B.6: launch the watchdog thread (live mode only)."""
        if self._paper_mode:
            return
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            return
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            daemon=True,
            name="live-feed-watchdog",
        )
        self._watchdog_thread.start()
        self._log.info(
            "LiveFeedManager: watchdog started (stale_threshold=%ds, check=%ds)",
            self._tick_stale_threshold_sec, self._watchdog_check_interval_sec,
        )

    def _watchdog_loop(self) -> None:
        """B.6 / Audit 12: detect a half-open feed.

        Sleeps in small increments so stop_event can break us out promptly.
        Checks tick age every watchdog_check_interval_sec; if a tick has
        not arrived in tick_stale_threshold_sec AND the market windows
        (when wired) say we are inside the entry window, fire ONE critical
        alert and force-close the ticker so kiteconnect's auto-reconnect
        machinery rebuilds the socket.
        """
        # B1 (25-Jul-2026): ONE loop. The tick-age arming check used to be a
        # SEPARATE pre-loop that spun until the first tick arrived, and
        # _check_consumer_health() lived in the loop AFTER it. Because nothing
        # subscribes to the WebSocket in ordinary operation (see
        # docs/audit/tick_candle_dormancy_25jul2026.md -- 32 connects, 0 tokens
        # subscribed, 0 ticks ever), that pre-loop never exited, so FIX-029's
        # consumer-thread death detector NEVER RAN in production: a protection
        # for a thread unrelated to ticks, disabled by an unrelated wiring gap.
        # The health check now runs every interval unconditionally; the tick-age
        # alarm keeps its original arm-on-first-tick semantics below.
        armed = False

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._watchdog_check_interval_sec)
            if self._stop_event.is_set():
                return

            # FIX-029: Check consumer thread health. Deliberately BEFORE the
            # tick-age arming gate -- a dead consumer thread is an incident
            # whether or not any tick has ever arrived. No-ops when the thread
            # is healthy or not yet started, so this adds no new alerts.
            self._check_consumer_health()

            # B.6 tick-age arming: only meaningful once a tick has actually been
            # seen. B2 (25-Jul-2026): deliberately NOT armed on subscription or
            # on connect -- with nothing subscribed, "no tick for 30s" is not an
            # anomaly, and arming it would alarm every interval of every day.
            if not armed:
                with self._last_tick_lock:
                    armed = self._last_tick_at is not None
                if not armed:
                    continue

            # Skip when paper-mode somehow flipped or we never connected.
            if self._paper_mode or not self._connected:
                continue

            # Skip when outside market hours so off-session restarts do
            # not keep firing alerts.
            if self._market_windows is not None:
                try:
                    if not self._market_windows.is_entry_allowed(now_ist()):
                        continue
                except Exception as exc:
                    self._log.warning(
                        "LiveFeedManager.watchdog: market_windows.is_entry_allowed "
                        "raised %s; treating as in-window (fail-open on hours check)",
                        exc,
                    )

            age = self.tick_age_seconds()
            if age is None or age < self._tick_stale_threshold_sec:
                continue

            if self._watchdog_alert_fired:
                # Already alerted for this stale episode; wait for ticks to
                # resume (which clears the flag) before alerting again.
                continue

            self._watchdog_alert_fired = True
            self._log.critical(
                "LiveFeedManager watchdog: no tick for %.1fs (threshold=%ds); "
                "forcing reconnect", age, self._tick_stale_threshold_sec,
            )
            if self._on_critical_failure is not None:
                try:
                    self._on_critical_failure(
                        f"tick-age watchdog: no tick for {age:.0f}s "
                        f"(threshold={self._tick_stale_threshold_sec}s)"
                    )
                except Exception as exc:
                    self._log.error(
                        "LiveFeedManager watchdog: on_critical_failure raised: %s",
                        exc,
                    )
            # FIX-064: Set reconnect flag instead of calling ticker.close()
            # directly from watchdog thread. Consumer thread will check flag
            # and call close() from its own thread context.
            self._force_reconnect.set()
            self._log.info(
                "LiveFeedManager watchdog: force_reconnect flag set; "
                "consumer thread will close ticker"
            )
