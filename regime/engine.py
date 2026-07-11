"""
regime/engine.py — Trading System v2 · V3 03.02 Market Regime Engine

A NEW, index-level regime engine. Computes a three-axis regime state
(direction / volatility / day-type), each with an ordinal confidence, plus a
positive-confirmation-only extreme flag. SHADOW — nothing it emits gates live
trading in this step.

FAIL-SAFE is the contract (Task 6): a broken regime must NEVER halt the book.
  • missing/insufficient index candles → status UNKNOWN, multiplier 0 (neutral);
  • an indicator error on an axis → that axis returns its most-uncertain value
    at LOW/TRANSITION confidence;
  • any exception → log with context, return UNKNOWN + neutral.

REUSE (no duplication): core.candle_math (ema/adx/atr/true_range_series), the
existing OhlcFetcher for index candles (by config token — the index is not in
the instrument cache), sr_detector.pivots.find_swing_pivots, MarketWindows for
session progress, the Candle model. Pure w.r.t. injected collaborators →
paper==live by construction (no mode branch).
"""
from __future__ import annotations

from statistics import mean
from typing import Callable, List, Optional

from core.candle_math import adx as _adx
from core.candle_math import atr as _atr
from core.candle_math import ema_series, true_range_series
from regime.models import (
    CONF_HIGH,
    CONF_LOW,
    CONF_MEDIUM,
    CONF_TRANSITION,
    DAY_RANGE,
    DAY_TREND,
    DAY_UNDETERMINED,
    DIR_BEAR,
    DIR_BULL,
    DIR_SIDEWAYS,
    STATUS_OK,
    VOL_HIGH,
    VOL_LOW,
    VOL_NORMAL,
    AxisResult,
    RegimeState,
    axis,
    neutral_axis,
    unknown_state,
)
from sr_detector.pivots import find_swing_pivots


def _attr(cfg, name, default):
    return getattr(cfg, name, default)


class MarketRegimeEngine:
    """Compute the index-level regime for one cycle. compute() NEVER raises."""

    def __init__(
        self,
        *,
        config,
        fetcher,                       # sr_detector.fetch.OhlcFetcher (reused)
        logger,
        now_fn: Callable[[], object],
        market_windows,               # core.market_windows.MarketWindows (session progress)
        exchange_status_fn: Optional[Callable[[], object]] = None,  # positive halt confirmation
    ) -> None:
        self._cfg = config
        self._fetcher = fetcher
        self._log = logger
        self._now_fn = now_fn
        self._mw = market_windows
        self._exchange_status_fn = exchange_status_fn

        self._enabled = bool(_attr(config, "enabled", False))
        self._index_token = int(_attr(config, "index_token", 256265))   # NIFTY 50 default
        self._index_symbol = str(_attr(config, "index_symbol", "NIFTY 50"))
        self._daily_lookback_days = int(_attr(config, "daily_lookback_days", 400))
        self._intraday_interval = str(_attr(config, "intraday_interval", "5minute"))
        self._intraday_lookback_days = int(_attr(config, "intraday_lookback_days", 1))
        self._ema_fast = int(_attr(config, "ema_fast", 50))
        self._ema_slow = int(_attr(config, "ema_slow", 200))
        self._slope_lookback = int(_attr(config, "slope_lookback", 5))
        self._adx_period = int(_attr(config, "adx_period", 14))
        self._adx_trend_threshold = float(_attr(config, "adx_trend_threshold", 25.0))
        self._atr_period = int(_attr(config, "atr_period", 14))
        self._vol_high_ratio = float(_attr(config, "vol_high_ratio", 1.3))
        self._vol_low_ratio = float(_attr(config, "vol_low_ratio", 0.7))
        self._trend_day_range_atr_mult = float(_attr(config, "trend_day_range_atr_mult", 1.5))
        self._min_daily_candles = max(self._ema_slow + 1, 2 * self._adx_period + 1)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── public API (never raises) ─────────────────────────────────────────────

    def compute(self, now=None) -> RegimeState:
        """Compute the regime for this cycle. Fail-safe: any failure → UNKNOWN +
        neutral multiplier (the book is NOT halted)."""
        ts_iso = None
        try:
            now = now if now is not None else self._now_fn()
            ts_iso = now.isoformat() if now is not None else None

            daily = self._fetch(self._index_token, "day", self._daily_lookback_days)
            if not daily or len(daily) < self._min_daily_candles:
                self._safe_log(
                    "warning",
                    "regime: insufficient index daily candles (%s < %s) — status UNKNOWN, neutral preference (NOT a halt)",
                    (len(daily) if daily else 0), self._min_daily_candles,
                )
                return unknown_state(ts=ts_iso, note="insufficient_index_daily_candles")

            intraday = self._fetch(self._index_token, self._intraday_interval,
                                   self._intraday_lookback_days) or []

            direction = self._safe_axis(self._direction, daily, default=DIR_SIDEWAYS, label="direction")
            volatility = self._safe_axis(self._volatility, daily, default=VOL_NORMAL, label="volatility")
            day_type = self._safe_axis(
                lambda d: self._day_type(intraday, d, now), daily,
                default=DAY_UNDETERMINED, label="day_type")
            extreme = self._extreme_flag()

            return RegimeState(
                status=STATUS_OK,
                direction=direction,
                volatility=volatility,
                day_type=day_type,
                extreme_flag=extreme,
                preference_multiplier=direction.multiplier,   # headline = direction
                ts=ts_iso,
            )
        except Exception as exc:   # ultimate fail-safe — never propagate
            self._safe_log("error", "regime: compute() failed (%s) — UNKNOWN, neutral (NOT a halt)", exc)
            return unknown_state(ts=ts_iso, note=f"compute_error:{exc}")

    # ── axis 1: DIRECTION (BULL / BEAR / SIDEWAYS) ────────────────────────────

    def _direction(self, daily: List) -> AxisResult:
        closes = [float(c.close) for c in daily]
        price = closes[-1]
        fast_series = ema_series(closes, self._ema_fast)
        slow_series = ema_series(closes, self._ema_slow)
        if not fast_series or not slow_series:
            return neutral_axis(DIR_SIDEWAYS)
        ema_fast, ema_slow = fast_series[-1], slow_series[-1]

        # sub-signals
        above_both = price > ema_fast and price > ema_slow
        below_both = price < ema_fast and price < ema_slow
        fast_over_slow = ema_fast > ema_slow
        i = min(self._slope_lookback, len(fast_series) - 1)
        slope_up = i > 0 and fast_series[-1] > fast_series[-1 - i]
        slope_down = i > 0 and fast_series[-1] < fast_series[-1 - i]
        struct_up, struct_down = self._swing_structure(daily)
        adx_val = _adx(daily, self._adx_period)
        trending = adx_val is not None and adx_val > self._adx_trend_threshold

        bull = sum((above_both, fast_over_slow, slope_up, struct_up))
        bear = sum((below_both, (not fast_over_slow), slope_down, struct_down))

        if bull > bear and bull >= 2:
            value, agree = DIR_BULL, bull
        elif bear > bull and bear >= 2:
            value, agree = DIR_BEAR, bear
        else:
            value, agree = DIR_SIDEWAYS, max(bull, bear)

        if value == DIR_SIDEWAYS:
            confidence = CONF_LOW if agree >= 2 else CONF_TRANSITION
        elif agree >= 4 and trending:
            confidence = CONF_HIGH
        elif agree >= 3:
            confidence = CONF_MEDIUM
        else:
            confidence = CONF_LOW

        return axis(value, confidence, evidence={
            "price": round(price, 2), "ema_fast": round(ema_fast, 2), "ema_slow": round(ema_slow, 2),
            "above_both": above_both, "below_both": below_both, "fast_over_slow": fast_over_slow,
            "slope_up": slope_up, "slope_down": slope_down,
            "struct_up": struct_up, "struct_down": struct_down,
            "adx": round(adx_val, 2) if adx_val is not None else None, "trending": trending,
            "bull_votes": bull, "bear_votes": bear,
        })

    def _swing_structure(self, daily: List):
        """Higher-highs/higher-lows (up) vs lower-highs/lower-lows (down) from the
        two most recent swing highs and lows (reuses find_swing_pivots)."""
        n = int(_attr(self._cfg, "swing_pivot_n", 3))
        pivots = find_swing_pivots(daily, left=n, right=n)
        highs = [p.price for p in pivots if p.kind == "HIGH"]
        lows = [p.price for p in pivots if p.kind == "LOW"]
        hh = len(highs) >= 2 and highs[-1] > highs[-2]
        hl = len(lows) >= 2 and lows[-1] > lows[-2]
        lh = len(highs) >= 2 and highs[-1] < highs[-2]
        ll = len(lows) >= 2 and lows[-1] < lows[-2]
        return (hh and hl), (lh and ll)

    # ── axis 2: VOLATILITY (HIGH / NORMAL / LOW) ──────────────────────────────

    def _volatility(self, daily: List) -> AxisResult:
        cur_atr = _atr(daily, self._atr_period)
        trs = true_range_series(daily)
        baseline = mean(trs[1:]) if len(trs) > 1 else None
        if cur_atr is None or not baseline or baseline <= 0:
            return neutral_axis(VOL_NORMAL)
        ratio = cur_atr / baseline
        if ratio >= self._vol_high_ratio:
            value = VOL_HIGH
        elif ratio <= self._vol_low_ratio:
            value = VOL_LOW
        else:
            value = VOL_NORMAL
        # confidence: strongly beyond the band → HIGH; comfortably in-band NORMAL → MEDIUM;
        # near a boundary → LOW.
        if value == VOL_HIGH:
            confidence = CONF_HIGH if ratio >= self._vol_high_ratio * 1.15 else CONF_MEDIUM
        elif value == VOL_LOW:
            confidence = CONF_HIGH if ratio <= self._vol_low_ratio * 0.85 else CONF_MEDIUM
        else:
            near = abs(ratio - self._vol_high_ratio) < 0.1 or abs(ratio - self._vol_low_ratio) < 0.1
            confidence = CONF_LOW if near else CONF_MEDIUM
        return axis(value, confidence, evidence={
            "cur_atr": round(cur_atr, 4), "baseline_tr": round(baseline, 4), "ratio": round(ratio, 3),
        })

    # ── axis 3: DAY-TYPE (TREND_DAY / RANGE_DAY / UNDETERMINED) ────────────────

    def _day_type(self, intraday: List, daily: List, now) -> AxisResult:
        progress = self._session_progress(now)
        ref_atr = _atr(daily, self._atr_period)
        # Early session, or too little intraday structure → UNDETERMINED (Task 3).
        if not intraday or len(intraday) < 3 or progress < 0.2 or not ref_atr or ref_atr <= 0:
            return axis(DAY_UNDETERMINED, CONF_LOW, evidence={
                "session_progress": round(progress, 3),
                "intraday_bars": len(intraday),
            })
        highs = [float(c.high) for c in intraday]
        lows = [float(c.low) for c in intraday]
        day_high, day_low = max(highs), min(lows)
        rng = day_high - day_low
        open_px = float(intraday[0].open)
        last_px = float(intraday[-1].close)
        range_atr_mult = rng / ref_atr if ref_atr > 0 else 0.0
        directionality = abs(last_px - open_px) / rng if rng > 0 else 0.0
        position = (last_px - day_low) / rng if rng > 0 else 0.5
        adx_intra = _adx(intraday, self._adx_period)
        adx_trending = adx_intra is not None and adx_intra > self._adx_trend_threshold

        wide = range_atr_mult >= self._trend_day_range_atr_mult
        one_way = directionality >= 0.6 or adx_trending
        at_extreme = position >= 0.75 or position <= 0.25

        if wide and one_way and at_extreme:
            value = DAY_TREND
        elif (not wide) and directionality < 0.4 and 0.3 <= position <= 0.7:
            value = DAY_RANGE
        else:
            value = DAY_UNDETERMINED

        # confidence rises with session_progress
        if value == DAY_UNDETERMINED:
            confidence = CONF_LOW if progress < 0.5 else CONF_MEDIUM
        elif progress >= 0.6:
            confidence = CONF_HIGH
        elif progress >= 0.35:
            confidence = CONF_MEDIUM
        else:
            confidence = CONF_LOW

        return axis(value, confidence, evidence={
            "session_progress": round(progress, 3), "range_atr_mult": round(range_atr_mult, 3),
            "directionality": round(directionality, 3), "position_in_range": round(position, 3),
            "adx": round(adx_intra, 2) if adx_intra is not None else None,
        })

    # ── extreme flag (Task 5) — positive confirmation ONLY ────────────────────

    def _extreme_flag(self) -> bool:
        """TRUE only on a CONFIRMED halt/circuit from the exchange-status feed.
        A MISSING feed is NOT a confirmed halt → FALSE + CRITICAL log. Emitted
        but NOT wired to stop trading in this step."""
        if self._exchange_status_fn is None:
            self._safe_log(
                "critical",
                "regime: exchange-status feed MISSING — extreme_flag left FALSE "
                "(absence of data is NOT a confirmed halt); regime continues",
            )
            return False
        try:
            status = self._exchange_status_fn()
        except Exception as exc:
            self._safe_log("critical", "regime: exchange-status feed errored (%s) — extreme_flag FALSE", exc)
            return False
        return self._is_confirmed_halt(status)

    @staticmethod
    def _is_confirmed_halt(status) -> bool:
        """Interpret the exchange-status payload. Positive confirmation only:
        an explicit halted/circuit/closed signal. Anything else (incl. None/
        unknown) → False."""
        if status is None:
            return False
        if isinstance(status, bool):
            return status
        if isinstance(status, str):
            return status.strip().upper() in {"HALT", "HALTED", "CIRCUIT", "CLOSED", "SUSPENDED"}
        if isinstance(status, dict):
            if status.get("halted") is True or status.get("extreme") is True:
                return True
            mkt = str(status.get("market_status", "")).strip().upper()
            return mkt in {"HALT", "HALTED", "CIRCUIT", "CLOSED", "SUSPENDED"}
        return False

    # ── helpers ───────────────────────────────────────────────────────────────

    def _fetch(self, token: int, interval: str, lookback_days: int) -> Optional[List]:
        try:
            return self._fetcher.fetch_by_token(token, interval, lookback_days)
        except Exception as exc:
            self._safe_log("warning", "regime: index fetch failed (%s/%s): %s", token, interval, exc)
            return None

    def _session_progress(self, now) -> float:
        """Fraction of the session elapsed [0,1], from MarketWindows' session
        bounds (reuse — no calendar duplication)."""
        try:
            mo = self._mw.market_open
            mc = self._mw.market_close
            open_dt = now.replace(hour=mo.hour, minute=mo.minute, second=0, microsecond=0)
            close_dt = now.replace(hour=mc.hour, minute=mc.minute, second=0, microsecond=0)
            if now <= open_dt:
                return 0.0
            if now >= close_dt:
                return 1.0
            return (now - open_dt).total_seconds() / (close_dt - open_dt).total_seconds()
        except Exception:
            return 0.0

    def _safe_axis(self, fn, daily, *, default: str, label: str) -> AxisResult:
        """Run an axis computation; any error → most-uncertain value at TRANSITION
        confidence (never crashes the cycle)."""
        try:
            return fn(daily)
        except Exception as exc:
            self._safe_log("warning", "regime: %s axis failed (%s) — neutral", label, exc)
            return neutral_axis(default)

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass
