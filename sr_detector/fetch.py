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

    def fetch_interval(
        self, symbol: str, interval: str, lookback_days: Optional[int] = None
    ) -> Optional[List[Candle]]:
        """Fetch ONE interval with an explicit (usually short) lookback window.

        V3 03.01: the Layer-A intraday anchors (VWAP/ORB) need TODAY's fine bars
        (e.g. 5-minute, lookback 1) — a different window than the multi-day
        structural fetch. Reuses the same token/cache/fetch_fn machinery; returns
        None on any failure (fail-safe, SR-F4). fetch_timeframes() is unchanged.
        """
        token = self._resolve_token(symbol)
        if token is None:
            return None
        return self._fetch_one(symbol, token, interval, lookback_days=lookback_days)

    def fetch_by_token(
        self, token: int, interval: str, lookback_days: Optional[int] = None,
        *, cache_key: Optional[str] = None,
    ) -> Optional[List[Candle]]:
        """Fetch ONE interval for an EXPLICIT instrument_token (skips the
        symbol→token cache resolution).

        V3 03.02: the market-index (e.g. NIFTY 50) is NOT in the instrument
        cache, so its token is supplied from config and fetched directly through
        the SAME rate-limited fetch_fn (reuse, not a new data path). Returns None
        on any failure (fail-safe). `cache_key` scopes the intra-session cache
        (defaults to the token).
        """
        if not token:
            return None
        key = cache_key or f"__token_{int(token)}"
        return self._fetch_one(key, int(token), interval, lookback_days=lookback_days)

    # ── internal ──────────────────────────────────────────────────────────────

    def _resolve_token(self, symbol: str) -> Optional[int]:
        try:
            row = self._cache.get_by_symbol(symbol)
            token = int(getattr(row, "instrument_token"))
            return token or None
        except Exception as exc:   # InstrumentNotFoundError or anything
            self._safe_log("warning", "sr_detector fetch: token lookup failed for %s: %s", symbol, exc)
            return None

    def _fetch_one(
        self, symbol: str, token: int, interval: str,
        lookback_days: Optional[int] = None,
    ) -> Optional[List[Candle]]:
        cached = self._cache_get(symbol, interval)
        if cached is not None:
            return cached
        try:
            now = self._now_fn()
            lb = self._lookback_days if lookback_days is None else int(lookback_days)
            from_dt = now - timedelta(days=lb)
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
