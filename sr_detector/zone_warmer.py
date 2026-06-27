"""
sr_detector/zone_warmer.py — Trading System v2 · S&R V2 Phase A (SNR-V2)

Purpose:
    Daemon worker (uniform worker pattern) that keeps the ZoneCache warm so the
    pre-placement divert check is INSTANT (no fetch on the hot path). On a queued
    symbol it fetches the structure TFs (day/60m/30m) via the reused OhlcFetcher
    (which paces through rate_limiter.acquire("historical")), builds scored zones
    via the shared zone_builder, and stores resistance+support in the cache.
    Re-warms cached symbols before TTL expiry. Enqueue a symbol when its signal
    ARRIVES so warming overlaps the pipeline window; a cache miss simply falls
    through to normal placement (never blocks).

What it does NOT do:
    - No 1-minute fetch (that is the RetestMonitor's job).
    - Never raises into the caller; warming failures are logged + skipped.
"""
from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Callable, Optional, Set

from sr_detector.zone_builder import ZoneKnobs, scored_zones_from_candles, split_zones
from sr_detector.confluence import ScoringParams
from sr_detector.zone_cache import ZoneCache


class ZoneWarmer:
    def __init__(
        self,
        *,
        fetcher,                 # sr_detector.fetch.OhlcFetcher (structure TFs)
        cache: ZoneCache,
        knobs: ZoneKnobs,
        scoring: ScoringParams,
        logger,
        now_fn: Callable[[], datetime],
        rewarm_margin_sec: float = 300.0,
        idle_sleep_sec: float = 2.0,
        max_queue: int = 512,
        enabled: bool = True,
    ) -> None:
        self._fetcher = fetcher
        self._cache = cache
        self._knobs = knobs
        self._scoring = scoring
        self._log = logger
        self._now_fn = now_fn
        self._rewarm_margin_sec = float(rewarm_margin_sec)
        self._idle_sleep_sec = float(idle_sleep_sec)
        self._enabled = bool(enabled)

        self._q: queue.Queue = queue.Queue(maxsize=int(max_queue))
        self._pending: Set[str] = set()
        self._pending_lock = threading.Lock()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if not self._enabled or self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="sr-zone-warmer", daemon=True)
        self._worker.start()
        self._safe_log("info", "zone_warmer: started")

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)
        self._worker = None

    # ── enqueue (signal-arrival hook) ─────────────────────────────────────────

    def enqueue(self, symbol: str) -> None:
        """Queue a symbol for warming. Deduped; never raises into the caller."""
        if not self._enabled or not symbol:
            return
        try:
            with self._pending_lock:
                if symbol in self._pending:
                    return
                self._pending.add(symbol)
            self._q.put_nowait(symbol)
        except queue.Full:
            with self._pending_lock:
                self._pending.discard(symbol)
            self._safe_log("warning", "zone_warmer: queue full, dropping %s", symbol)
        except Exception as exc:
            self._safe_log("error", "zone_warmer: enqueue failed: %s", exc)

    # ── worker ────────────────────────────────────────────────────────────────

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=self._idle_sleep_sec)
            except queue.Empty:
                self._enqueue_rewarms()      # idle: refresh near-expiry symbols
                continue
            try:
                if item is _SENTINEL:
                    continue
                with self._pending_lock:
                    self._pending.discard(item)
                self.warm(item)
            except Exception as exc:
                self._safe_log("error", "zone_warmer: warm error for %s: %s", item, exc)
            finally:
                self._q.task_done()

    def _enqueue_rewarms(self) -> None:
        try:
            for sym in self._cache.symbols_due_for_rewarm(self._rewarm_margin_sec):
                self.enqueue(sym)
        except Exception as exc:
            self._safe_log("error", "zone_warmer: rewarm scan failed: %s", exc)

    # Public + synchronous so tests can drive it deterministically.
    def warm(self, symbol: str) -> bool:
        """Fetch structure TFs, build zones, store in the cache. Returns True if
        zones were cached. A fetch miss leaves the cache untouched (returns False)."""
        tf_candles = self._fetcher.fetch_timeframes(symbol, list(self._knobs.intervals))
        if not tf_candles:
            self._safe_log("info", "zone_warmer: fetch miss for %s (no cache update)", symbol)
            return False
        scored = scored_zones_from_candles(
            tf_candles, knobs=self._knobs, scoring=self._scoring, now=self._safe_now())
        resistance, support = split_zones(scored)
        self._cache.put(symbol, resistance, support)
        self._safe_log("info", "zone_warmer: warmed %s (res=%d sup=%d)",
                       symbol, len(resistance), len(support))
        return True

    # ── helpers ───────────────────────────────────────────────────────────────

    def _safe_now(self) -> Optional[datetime]:
        try:
            return self._now_fn()
        except Exception:
            return None

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


_SENTINEL = object()
