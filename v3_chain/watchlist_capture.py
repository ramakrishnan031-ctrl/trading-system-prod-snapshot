"""
v3_chain/watchlist_capture.py — Trading System v2 · V3 Step 10b · EOD watchlist capture.

The EOD side of PB-01: an "eod" Chartink scanner's alert is routed here (never the
intraday order path). For each breakout symbol this worker — OFF the hot path — computes
THE LEVEL OURSELVES (never trusts the payload price), stamps the single valid
`trading_date` (= the next trading day), records the nearest 30m/1h S&R zone (for the S&R
validation programme), and persists ONE `pb01_watchlist` row.

Two defences against a messy Chartink config (spec §7 + the addendum):
  • SETTLED-ONLY   — a breakout on a FORMING daily candle (an intraday 5-min firing) is
                     SKIPPED; only a settled end-of-day session is captured.
  • DEDUPE         — UNIQUE(symbol, trading_date) at the store; a repeated/premature
                     firing can never create a duplicate or an early row.

ANALYSIS ONLY — this places no order, holds no capital. Fail-safe: any error on one
symbol is logged and skipped; the worker never crashes.
"""
from __future__ import annotations

import json
import queue
import threading
from datetime import date as _date
from typing import Any, List, Optional

_SENTINEL = object()
_TF_30 = "30minute"
_TF_60 = "60minute"
_TF_DAY = "day"


def compute_breakout_level(
    daily_candles: List[Any], breakout_date: _date, lookback_sessions: int,
) -> Optional[float]:
    """LEVEL = the BROKEN LEVEL = the highest daily HIGH of the `lookback_sessions`
    sessions STRICTLY BEFORE `breakout_date` (spec §7 — exactly the level the Chartink
    `Max(20, Daily High)`-of-1-day-ago clause used). Deterministic; does NOT depend on
    unvalidated swing detection.

    Returns the level (float) or None when fewer than `lookback_sessions` prior sessions
    are available (insufficient data → skip capture, never guess a level). Pure."""
    prior = [c for c in (daily_candles or []) if c.ts.date() < breakout_date]
    if len(prior) < int(lookback_sessions):
        return None
    window = prior[-int(lookback_sessions):]
    return float(max(c.high for c in window))


class WatchlistCaptureWorker:
    """Background worker: consume EOD breakout items → compute LEVEL → persist a
    watchlist row. Constructed ONLY when `watchlist.enabled` (else the EOD route drops
    the item — a fail-SAFE missed capture, logged, never silent)."""

    def __init__(
        self,
        *,
        config,               # WatchlistConfig
        store,                # StateStore (insert_pb01_watchlist)
        fetcher,              # sr_detector.fetch.OhlcFetcher (own instance)
        market_windows,       # MarketWindows (next_trading_day + market_close)
        logger,
        now_fn,
        zone_knobs=None,      # sr_detector.zone_builder.ZoneKnobs (nearest-zone record; optional)
        zone_scoring=None,    # sr_detector.confluence.ScoringParams (optional)
        max_queue: int = 256,
    ) -> None:
        self._cfg = config
        self._store = store
        self._fetcher = fetcher
        self._mw = market_windows
        self._log = logger
        self._now = now_fn
        self._knobs = zone_knobs
        self._scoring = zone_scoring
        self._q: queue.Queue = queue.Queue(maxsize=int(max_queue))
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._worker is not None:
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run, name="pb01-capture", daemon=True)
        self._worker.start()
        self._safe_log("info", "pb01 watchlist capture worker started")

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(_SENTINEL)
        except queue.Full:
            pass
        w = self._worker
        if w is not None:
            w.join(timeout=timeout)
        self._worker = None

    # ── enqueue (called by the webhook EOD route; fast, guarded) ──────────────
    def submit(self, *, scanner_name: str, symbol: str, triggered_at) -> bool:
        """Enqueue one EOD breakout symbol for capture. Returns False (logged) if the
        queue is full — a missed capture is fail-SAFE (an empty watchlist = no PB-01 the
        next day), never a bad trade. Never raises into the request thread."""
        try:
            self._q.put_nowait((scanner_name, symbol, triggered_at))
            return True
        except queue.Full:
            self._safe_log("warning", "pb01 capture queue full, dropping %s (fail-safe miss)", symbol)
            return False
        except Exception as exc:
            self._safe_log("error", "pb01 capture submit error (ignored): %s", exc)
            return False

    # ── background worker ─────────────────────────────────────────────────────
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                if item is _SENTINEL:
                    continue
                self._capture(*item)
            except Exception as exc:   # one broken symbol must NEVER kill the worker
                self._safe_log("error", "pb01 capture error (ignored): %s", exc)
            finally:
                self._q.task_done()

    def _capture(self, scanner_name: str, symbol: str, triggered_at) -> None:
        now = self._safe_now()
        breakout_date = triggered_at.date() if triggered_at is not None else (now.date() if now else None)
        if breakout_date is None:
            self._safe_log("warning", "pb01 capture %s: no breakout_date → skip", symbol)
            return

        # SETTLED-ONLY: a breakout on a FORMING daily candle (intraday firing) is not yet
        # a settled EOD result → skip (fail-safe). Settled iff the breakout session has
        # closed: an earlier date, or today at/after market close.
        if now is not None and breakout_date == now.date() and not self._market_closed(now):
            self._safe_log(
                "info", "pb01 capture %s: breakout_date %s is a FORMING session "
                "(now %s < close) → SKIP premature (await settled EOD)",
                symbol, breakout_date, now.time())
            return

        # Compute LEVEL from OUR OWN settled daily candles (never the payload price).
        daily = self._fetch(symbol, [_TF_DAY]).get(_TF_DAY) or []
        level = compute_breakout_level(daily, breakout_date, self._cfg.level_lookback_sessions)
        if level is None:
            self._safe_log(
                "warning", "pb01 capture %s: < %d prior daily sessions before %s → skip",
                symbol, self._cfg.level_lookback_sessions, breakout_date)
            return

        # trading_date = the SINGLE valid next trading day (holiday-aware) = the anti-
        # rehydration stamp. next_trading_day takes a datetime at/after the breakout EOD.
        anchor = self._breakout_eod_dt(breakout_date)
        trading_date = self._mw.next_trading_day(anchor).isoformat()

        # Record the nearest 30m/1h S&R zone to LEVEL (validation programme; fail-safe).
        sr_zone_json = self._nearest_zone_json(symbol, level)

        now_iso = (now or self._safe_now())
        now_iso = now_iso.isoformat() if now_iso is not None else ""
        row = {
            "symbol": symbol, "trading_date": trading_date, "level": level,
            "breakout_date": breakout_date.isoformat(), "source": scanner_name,
            "sr_zone_json": sr_zone_json, "status": "PENDING", "outcome_json": None,
            "captured_at": now_iso, "created_at": now_iso,
        }
        inserted = self._store.insert_pb01_watchlist(row)
        if inserted:
            self._safe_log(
                "info", "pb01 captured %s LEVEL=%.4f breakout=%s trading_date=%s",
                symbol, level, breakout_date, trading_date)
        else:
            self._safe_log(
                "info", "pb01 capture %s: duplicate (symbol,trading_date=%s) → deduped",
                symbol, trading_date)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _fetch(self, symbol: str, intervals: List[str]) -> dict:
        try:
            return self._fetcher.fetch_timeframes(symbol, intervals) or {}
        except Exception as exc:
            self._safe_log("warning", "pb01 capture %s: fetch failed: %s", symbol, exc)
            return {}

    def _nearest_zone_json(self, symbol: str, level: float) -> Optional[str]:
        """Nearest 30m/1h sr_detector zone to LEVEL, recorded (not used for the trigger).
        Fail-safe: any problem → None (the level itself is the deterministic trigger)."""
        if self._knobs is None or self._scoring is None:
            return None
        try:
            from sr_detector.zone_builder import scored_zones_from_candles
            fetched = self._fetch(symbol, [_TF_60, _TF_30])
            nonempty = {tf: cs for tf, cs in fetched.items() if cs}
            if not nonempty:
                return None
            zones = scored_zones_from_candles(
                nonempty, knobs=self._knobs, scoring=self._scoring, now=self._safe_now())
            if not zones:
                return None
            nearest = min(zones, key=lambda z: abs((z.band_low + z.band_high) / 2.0 - level))
            return json.dumps(nearest.to_dict())
        except Exception as exc:
            self._safe_log("debug", "pb01 capture %s: nearest-zone record skipped: %s", symbol, exc)
            return None

    def _market_closed(self, now) -> bool:
        try:
            close_t = getattr(self._mw, "market_close", None)
            return close_t is not None and now.time() >= close_t
        except Exception:
            return True   # fail toward "settled" only if we truly can't tell (conservative: capture)

    def _breakout_eod_dt(self, breakout_date: _date):
        from datetime import datetime as _dt, time as _t
        # A datetime firmly at the breakout day's EOD, so next_trading_day rolls to D+1.
        return _dt.combine(breakout_date, _t(23, 59))

    def _safe_now(self):
        try:
            return self._now()
        except Exception:
            return None

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass
