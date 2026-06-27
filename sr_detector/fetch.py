"""
sr_detector/fetch.py — Trading System v2 · S&R Detector V1

Purpose:
    Detector-side fetch orchestration: resolve a symbol → instrument_token, fetch
    the 3 timeframe windows (one request each), cache within the session, and
    FAIL SAFE (never raise; an error yields no candles so the detector logs a
    fetch_failed row and skips analysis — spec C).

Design:
    SR-F1 — The actual rate-limited broker call is INJECTED as `fetch_fn(token,
            from_date, to_date, interval) -> list[dict]`. main.py builds it as a
            closure over (market-data kite handle, rate_limiter.acquire("historical")),
            so this package imports nothing from broker/ and never touches a
            hardcoded API key. Paper + live share the same closure (parity).
    SR-F2 — Token via the injected instrument_cache.get_by_symbol(sym).instrument_token
            (already loaded; not rebuilt via kite.instruments()).
    SR-F3 — Intra-session cache keyed by (symbol, interval) with a TTL, so a
            re-signalling symbol is not re-fetched.
    SR-F4 — Every failure path (no token, fetch raises, empty result) returns
            None for that TF; nothing propagates into the pipeline.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from sr_detector.models import Candle


class OhlcFetcher:
    def __init__(
        self,
        fetch_fn: Callable[[int, datetime, datetime, str], list],
        instrument_cache,
        *,
        lookback_days: int,
        logger,
        now_fn: Callable[[], datetime],
        cache_ttl_sec: float = 1800.0,
    ) -> None:
        self._fetch_fn = fetch_fn
        self._cache = instrument_cache
        self._lookback_days = int(lookback_days)
        self._log = logger
        self._now_fn = now_fn
        self._cache_ttl_sec = float(cache_ttl_sec)
        # (symbol, interval) -> (fetched_at_monotonic_dt, candles)
        self._mem: Dict[tuple, tuple] = {}
        self._lock = threading.Lock()

    def fetch_timeframes(self, symbol: str, intervals: List[str]) -> Dict[str, List[Candle]]:
        """
        Return {interval: [Candle,...]} for the intervals that fetched OK. A
        failed/empty interval is simply omitted (never raises). An empty dict
        means the detector should record a fetch_failed / NO_CLEAR_STRUCTURE row.
        """
        token = self._resolve_token(symbol)
        if token is None:
            return {}

        out: Dict[str, List[Candle]] = {}
        for interval in intervals:
            candles = self._fetch_one(symbol, token, interval)
            if candles:
                out[interval] = candles
        return out

    # ── internal ──────────────────────────────────────────────────────────────

    def _resolve_token(self, symbol: str) -> Optional[int]:
        try:
            row = self._cache.get_by_symbol(symbol)
            token = int(getattr(row, "instrument_token"))
            return token or None
        except Exception as exc:   # InstrumentNotFoundError or anything
            self._safe_log("warning", "sr_detector fetch: token lookup failed for %s: %s", symbol, exc)
            return None

    def _fetch_one(self, symbol: str, token: int, interval: str) -> Optional[List[Candle]]:
        cached = self._cache_get(symbol, interval)
        if cached is not None:
            return cached
        try:
            now = self._now_fn()
            from_dt = now - timedelta(days=self._lookback_days)
            rows = self._fetch_fn(token, from_dt, now, interval)
            candles = [Candle.from_kite(r) for r in (rows or [])]
            if candles:
                self._cache_put(symbol, interval, candles)
            return candles or None
        except Exception as exc:   # fail-safe: never propagate (SR-F4)
            self._safe_log(
                "warning",
                "sr_detector fetch: historical_data failed for %s/%s: %s",
                symbol, interval, exc,
            )
            return None

    def _cache_get(self, symbol: str, interval: str) -> Optional[List[Candle]]:
        with self._lock:
            entry = self._mem.get((symbol, interval))
        if entry is None:
            return None
        fetched_at, candles = entry
        age = (self._now_fn() - fetched_at).total_seconds()
        if age > self._cache_ttl_sec:
            return None
        return candles

    def _cache_put(self, symbol: str, interval: str, candles: List[Candle]) -> None:
        with self._lock:
            self._mem[(symbol, interval)] = (self._now_fn(), candles)

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass
