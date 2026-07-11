# =============================================================================
# Script        : core/candle_math.py
# Purpose       : Domain-agnostic, PURE candle math for the V3 Shared-Engine
#                 "Common Utilities" substrate. Two genuinely-new helpers that
#                 did not exist anywhere before (Phase-0 gap):
#                   (1) True Range series + ATR (Wilder / SMA) from OHLC candles.
#                   (2) Timeframe RESAMPLE (base bars -> higher timeframe).
# Manual Input  : No.
# How it works  : Pure functions over the SHARED OHLCV `Candle` value object
#                 (sr_detector.models.Candle — the single candle type in the
#                 codebase; NO parallel type is defined here). No I/O, no
#                 globals, no clock, no broker, no config read, no mode branch —
#                 deterministic and identical in paper and live by construction.
#                 Session boundaries for resample are INJECTED by the caller
#                 (derive them from core.market_windows) so this module never
#                 hardcodes 09:15 and never duplicates calendar/holiday logic.
# Inputs        : Sequences of Candle (chronologically ordered); an ATR period;
#                 a target timeframe + session bounds for resample.
# Outputs       : float | None (atr), list[float] (true_range_series),
#                 ResampleResult (resample). No side effects.
# Layer         : 1 (stdlib only AT IMPORT TIME). atr()/true_range_series()
#                 reference no project module at all. resample() is the ONE
#                 place that constructs a Candle, and it imports the shared type
#                 with a FUNCTION-LOCAL import — mirroring the established
#                 `# local import avoids cycle` pattern in core/market_windows.py
#                 (line ~283) — so there is no import-time dependency on the
#                 sr_detector package and no layer inversion.
# Wiring        : NONE yet (V3 Step 1 is build + unit-test only). Nothing in the
#                 live trading / scoring / sizing / S&R path consumes these
#                 functions; later modules (03.01 S&R, 03.02 Regime, 03.06
#                 Sizing) wire them in and own the config defaults.
# =============================================================================
from __future__ import annotations

from dataclasses import dataclass
from datetime import date as _date, datetime, time, timedelta
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:
    # Type hints ONLY — not imported at runtime. The single runtime reference to
    # the shared Candle type is the function-local import inside resample().
    from sr_detector.models import Candle


# ─────────────────────────────────────────────────────────────────────────────
# (1) True Range + ATR
# ─────────────────────────────────────────────────────────────────────────────

# ATR smoothing methods (parameters, not hardcoded defaults in business logic).
ATR_WILDER = "wilder"
ATR_SMA = "sma"


def true_range_series(candles: Sequence["Candle"]) -> List[float]:
    """Return the True Range for each candle, in input order.

        TR_t = max( high_t - low_t,
                    |high_t - close_{t-1}|,
                    |low_t  - close_{t-1}| )

    The FIRST candle has no previous close, so TR_0 = high_0 - low_0 (a
    degenerate "range", not a true range — atr() excludes it; see below).

    Pure: reads only .high/.low/.close (duck-typed on the shared Candle),
    constructs nothing, and is deterministic. Returns a list the same length as
    `candles` ([] for empty input). Candles are processed in the given order
    and never re-sorted (caller supplies chronological order).
    """
    trs: List[float] = []
    prev_close: Optional[float] = None
    for c in candles:
        high = float(c.high)
        low = float(c.low)
        if prev_close is None:
            tr = high - low
        else:
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)
        prev_close = float(c.close)
    return trs


def atr(
    candles: Sequence["Candle"],
    period: int,
    method: str = ATR_WILDER,
) -> Optional[float]:
    """Average True Range over `period`, or None when data is INSUFFICIENT.

    Requires at least ``period + 1`` candles, so that ``period`` true ranges
    each have a real previous close. Fewer candles -> **None** (never fabricate
    a value). `period` is a caller-supplied PARAMETER — no default period is
    baked in here; any default belongs in config, at the wiring site.

    method:
      - ``"wilder"`` (default): Wilder's smoothing (the standard ATR). Seed =
        mean of the first ``period`` prev-close true ranges; then
        ``ATR_t = (ATR_{t-1} * (period - 1) + TR_t) / period``.
      - ``"sma"``: simple mean of the LAST ``period`` prev-close true ranges.

    The degenerate first TR (high - low, with no previous close) is EXCLUDED
    from the ATR computation — it is not a true range in Wilder's sense. This
    is why the data requirement is ``period + 1`` (not ``period``).

    Pure and deterministic: same candles + period + method -> same output.
    Raises ValueError on an invalid `period` (<= 0) or unknown `method`.
    """
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError(f"atr period must be a positive int, got {period!r}")
    if method not in (ATR_WILDER, ATR_SMA):
        raise ValueError(
            f"atr method must be {ATR_WILDER!r} or {ATR_SMA!r}, got {method!r}"
        )
    if len(candles) < period + 1:
        return None

    trs = true_range_series(candles)
    real = trs[1:]  # drop the degenerate first TR (no previous close)
    # len(real) == len(candles) - 1 >= period  (guaranteed by the gate above)

    if method == ATR_SMA:
        window = real[-period:]
        return sum(window) / period

    # Wilder's smoothing.
    atr_val = sum(real[:period]) / period      # seed = SMA of the first `period` TRs
    for tr in real[period:]:
        atr_val = (atr_val * (period - 1) + tr) / period
    return atr_val


# ─────────────────────────────────────────────────────────────────────────────
# (1b) Volume-weighted average price
# ─────────────────────────────────────────────────────────────────────────────

def session_vwap(candles: Sequence["Candle"]) -> Optional[float]:
    """Volume-weighted average price over the given candles, or None.

        VWAP = Σ( typical_price_t · volume_t ) / Σ( volume_t )
        typical_price_t = (high_t + low_t + close_t) / 3

    The caller scopes `candles` to the intended session/window (e.g. today's
    in-session bars) — this function is a pure reducer over whatever it is given,
    exactly like atr(). Returns **None** when there are no candles or the total
    volume is zero (never fabricates a price). Deterministic; no I/O, no clock.
    """
    num = 0.0
    vol = 0.0
    for c in candles:
        v = float(c.volume)
        if v <= 0.0:
            continue
        typical = (float(c.high) + float(c.low) + float(c.close)) / 3.0
        num += typical * v
        vol += v
    if vol <= 0.0:
        return None
    return num / vol


# ─────────────────────────────────────────────────────────────────────────────
# (1c) Trend indicators — EMA and Wilder's ADX
# ─────────────────────────────────────────────────────────────────────────────

def ema_series(values: Sequence[float], period: int) -> List[float]:
    """Exponential moving average series, seeded with the SMA of the first
    `period` values. Returns one EMA per input from index (period-1) onward
    (length = max(0, len - period + 1)); [] when there are fewer than `period`
    values. `period` is a PARAMETER (no baked default). Pure/deterministic.
    """
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError(f"ema period must be a positive int, got {period!r}")
    vals = [float(v) for v in values]
    if len(vals) < period:
        return []
    k = 2.0 / (period + 1.0)
    seed = sum(vals[:period]) / period
    out = [seed]
    for v in vals[period:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def ema(values: Sequence[float], period: int) -> Optional[float]:
    """The latest EMA over `values`, or None when there are fewer than `period`
    values (never fabricated). Thin wrapper over ema_series()."""
    series = ema_series(values, period)
    return series[-1] if series else None


def adx(candles: Sequence["Candle"], period: int = 14) -> Optional[float]:
    """Wilder's Average Directional Index (trend strength), or None when data is
    INSUFFICIENT. Requires at least ``2*period + 1`` candles so the DX series is
    long enough to seed + smooth the ADX. Uses true_range_series for TR (reuse,
    no duplication). Pure and deterministic. ADX is direction-agnostic: it
    measures how trending the series is, not which way.
    """
    if not isinstance(period, int) or isinstance(period, bool) or period <= 0:
        raise ValueError(f"adx period must be a positive int, got {period!r}")
    n = len(candles)
    if n < 2 * period + 1:
        return None

    plus_dm: List[float] = []
    minus_dm: List[float] = []
    trs: List[float] = []
    for i in range(1, n):
        cur, prev = candles[i], candles[i - 1]
        up = float(cur.high) - float(prev.high)
        dn = float(prev.low) - float(cur.low)
        plus_dm.append(up if (up > dn and up > 0.0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0.0) else 0.0)
        trs.append(max(
            float(cur.high) - float(cur.low),
            abs(float(cur.high) - float(prev.close)),
            abs(float(cur.low) - float(prev.close)),
        ))

    def _wilder(x: List[float]) -> List[float]:
        s = sum(x[:period])
        out = [s]
        for v in x[period:]:
            s = s - s / period + v
            out.append(s)
        return out

    sm_pdm, sm_mdm, sm_tr = _wilder(plus_dm), _wilder(minus_dm), _wilder(trs)
    dxs: List[float] = []
    for pdm, mdm, tr in zip(sm_pdm, sm_mdm, sm_tr):
        if tr <= 0.0:
            dxs.append(0.0)
            continue
        pdi = 100.0 * pdm / tr
        mdi = 100.0 * mdm / tr
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom > 0.0 else 0.0)

    if len(dxs) < period:
        return None
    adx_val = sum(dxs[:period]) / period
    for dx in dxs[period:]:
        adx_val = (adx_val * (period - 1) + dx) / period
    return adx_val


# ─────────────────────────────────────────────────────────────────────────────
# (2) Timeframe resample
# ─────────────────────────────────────────────────────────────────────────────

# Accepted string value of `target_tf` for a one-bar-per-session-day resample.
DAILY_TF = "day"

# session_bounds: either a fixed (open_time, close_time) applied to every date,
# or a callable date -> (open_time, close_time) so per-day holidays / special
# sessions can be honoured (derive it from core.market_windows.MarketWindows).
SessionBounds = Union[
    Tuple[time, time],
    Callable[["_date"], Tuple[time, time]],
]


@dataclass(frozen=True)
class ResampleResult:
    """Result of resample().

    `bars`  — the resampled OHLCV Candles (the SHARED type), each with
              ``ts`` = the bucket START datetime, sorted ascending. A bucket
              with NO base candles is NOT emitted here (no fabricated prices).
    `partial_bucket_starts` — bucket-start datetimes of EMITTED bars whose full
              target window overruns the session close (e.g. NSE's 375-minute
              day leaves a 15-minute tail on a 30-minute grid). Flagged, never
              padded to a full window.
    `empty_bucket_starts`   — in-session grid slots that had NO base data
              (gaps). Reported, not fabricated. Empty for daily resamples.
    """
    bars: List["Candle"]
    partial_bucket_starts: Tuple[datetime, ...] = ()
    empty_bucket_starts: Tuple[datetime, ...] = ()

    @property
    def partial_last(self) -> bool:
        """True iff the final emitted bar is a partial (session-tail) bucket."""
        if not self.bars:
            return False
        return self.bars[-1].ts in set(self.partial_bucket_starts)


def resample(
    base_candles: Sequence["Candle"],
    target_tf: Union[int, str],
    session_bounds: SessionBounds,
) -> ResampleResult:
    """Resample base-timeframe candles into a higher timeframe.

    target_tf:
      - a positive int -> intraday bucket size in MINUTES (e.g. 5, 30, 60);
      - ``"day"``/``"daily"`` -> one bar per session date.

    Aggregation per bucket: open = first, high = max, low = min, close = last,
    volume = sum; the bar's ``ts`` = the bucket START datetime.

    Bucket alignment (intraday): buckets are anchored to each date's SESSION
    OPEN supplied via `session_bounds` (never hardcoded). e.g. 30m from a 09:15
    open -> 09:15, 09:45, 10:15, ... Intraday buckets NEVER span days (they are
    computed per date). Base candles outside ``[open, close)`` — pre-open /
    after-hours — are excluded.

    session_bounds: either a fixed ``(open_time, close_time)`` tuple applied to
    every date, or a callable ``date -> (open_time, close_time)``. Derive it
    from core.market_windows.MarketWindows so holidays / special sessions are
    honoured — this module does NOT duplicate calendar logic.

    Honesty guarantees:
      - an in-session grid slot with no base candles is NOT emitted as a bar;
        its start is reported in ``empty_bucket_starts``;
      - the final bucket whose full window overruns the session close is emitted
        from the data present and flagged (``partial_bucket_starts`` /
        ``partial_last``) — never padded.

    Pure and deterministic: no clock, no I/O; output depends only on inputs.
    Base candles are assumed chronologically ordered and to share one tzinfo
    convention (aware IST, or naive IST — matching the Candle contract); order
    is preserved, not re-sorted.
    """
    # The ONLY runtime reference to the sr_detector package — kept function-local
    # to avoid an import-time layer inversion (core importing a feature package).
    # Mirrors the established `# local import avoids cycle` pattern in
    # core/market_windows.py. Constructs the SHARED Candle type (no duplicate).
    from sr_detector.models import Candle

    is_daily = isinstance(target_tf, str) and target_tf.strip().lower() in ("day", "daily")
    if not is_daily:
        if isinstance(target_tf, bool) or not isinstance(target_tf, int) or target_tf <= 0:
            raise ValueError(
                f"target_tf must be a positive int (minutes) or 'day', got {target_tf!r}"
            )

    def _bounds_for(d: "_date") -> Tuple[time, time]:
        return session_bounds(d) if callable(session_bounds) else session_bounds

    # ── group base candles into buckets keyed by bucket-start datetime ──
    buckets: dict[datetime, dict] = {}
    dates_seen: dict[_date, Tuple[time, time]] = {}
    tz = None

    for cd in base_candles:
        ts = cd.ts
        if tz is None:
            tz = ts.tzinfo
        d = ts.date()
        open_t, close_t = _bounds_for(d)
        t = ts.time()
        if t < open_t or t >= close_t:
            continue  # pre-open / after-hours -> excluded
        dates_seen[d] = (open_t, close_t)

        open_dt = datetime.combine(d, open_t, tzinfo=ts.tzinfo)
        if is_daily:
            bstart = open_dt
        else:
            mins = (ts - open_dt).total_seconds() / 60.0
            k = int(mins // target_tf)
            bstart = open_dt + timedelta(minutes=k * target_tf)

        hi, lo = float(cd.high), float(cd.low)
        cl, op = float(cd.close), float(cd.open)
        vol = int(cd.volume)
        agg = buckets.get(bstart)
        if agg is None:
            buckets[bstart] = {"o": op, "h": hi, "l": lo, "c": cl, "v": vol}
        else:
            if hi > agg["h"]:
                agg["h"] = hi
            if lo < agg["l"]:
                agg["l"] = lo
            agg["c"] = cl          # last-in-order close
            agg["v"] += vol

    bars: List[Candle] = [
        Candle(ts=bs, open=a["o"], high=a["h"], low=a["l"], close=a["c"], volume=a["v"])
        for bs, a in sorted(buckets.items())
    ]

    # ── flag empty (gap) and partial (session-tail) intraday buckets ──
    partial: List[datetime] = []
    empty: List[datetime] = []
    if not is_daily:
        for d, (open_t, close_t) in sorted(dates_seen.items()):
            total_min = (close_t.hour * 60 + close_t.minute) - (open_t.hour * 60 + open_t.minute)
            if total_min <= 0:
                continue
            open_dt = datetime.combine(d, open_t, tzinfo=tz)
            close_dt = datetime.combine(d, close_t, tzinfo=tz)
            n_slots = (total_min + target_tf - 1) // target_tf  # ceil
            for k in range(n_slots):
                slot_dt = open_dt + timedelta(minutes=k * target_tf)
                slot_end = slot_dt + timedelta(minutes=target_tf)
                is_partial = slot_end > close_dt
                if slot_dt in buckets:
                    if is_partial:
                        partial.append(slot_dt)
                else:
                    empty.append(slot_dt)

    return ResampleResult(
        bars=bars,
        partial_bucket_starts=tuple(partial),
        empty_bucket_starts=tuple(empty),
    )
