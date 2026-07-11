"""
sr_detector/zone_builder.py — Trading System v2 · S&R V2 (SNR-V2)

Purpose:
    The SINGLE zone-building path, shared by the V1 detector (async observer) and
    the V2 ZoneWarmer. Derive-don't-duplicate: the pivots→zones→confluence
    sequence and the config→params mapping live here ONCE. Pure + stdlib/core only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date as _date, datetime, time as _time, timedelta
from typing import Dict, List, Optional, Tuple

from core.candle_math import session_vwap
from sr_detector.confluence import ConfluenceContext, ScoringParams, score_zones
from sr_detector.flags import FlagParams
from sr_detector.models import TF_ROLE, Candle, ScoredZone
from sr_detector.pivots import find_swing_pivots
from sr_detector.zones import cluster_zones, volume_profile_nodes


def _attr(cfg, name, default):
    return getattr(cfg, name, default)


@dataclass(frozen=True)
class ZoneKnobs:
    """Pivot/zone/volume/recency knobs (the non-scoring half of the config)."""
    intervals: Tuple[str, ...]
    default_pivot_n: int
    pivot_n_by_tf: Dict[str, int]
    cluster_pct: float
    volume_bins: int
    volume_node_frac: float
    recency_window_days: float


def build_zone_knobs(cfg) -> ZoneKnobs:
    return ZoneKnobs(
        intervals=tuple(_attr(cfg, "timeframes", ["day", "60minute", "30minute"])),
        default_pivot_n=int(_attr(cfg, "default_pivot_n", 5)),
        pivot_n_by_tf=dict(_attr(cfg, "pivot_n_by_tf", {}) or {}),
        cluster_pct=float(_attr(cfg, "cluster_pct", 0.5)),
        volume_bins=int(_attr(cfg, "volume_bins", 24)),
        volume_node_frac=float(_attr(cfg, "volume_node_frac", 0.7)),
        recency_window_days=float(_attr(cfg, "recency_window_days", 90.0)),
    )


def build_scoring_params(cfg) -> ScoringParams:
    return ScoringParams(
        w_swing=float(_attr(cfg, "w_swing", 1.0)),
        w_volume=float(_attr(cfg, "w_volume", 1.0)),
        w_multi_tf=float(_attr(cfg, "w_multi_tf", 1.0)),
        w_prior_day=float(_attr(cfg, "w_prior_day", 1.0)),
        w_round=float(_attr(cfg, "w_round", 0.5)),
        w_recency=float(_attr(cfg, "w_recency", 0.5)),
        t_high=float(_attr(cfg, "t_high", 5.0)),
        t_med=float(_attr(cfg, "t_med", 3.0)),
        touch_cap=int(_attr(cfg, "touch_cap", 4)),
        merge_pct=float(_attr(cfg, "merge_pct", 0.4)),
        band_buffer_pct=float(_attr(cfg, "band_buffer_pct", 0.1)),
        recency_min_factor=float(_attr(cfg, "recency_min_factor", 0.0)),
    )


def build_flag_params(cfg) -> FlagParams:
    return FlagParams(
        entry_proximity_pct=float(_attr(cfg, "entry_proximity_pct", 1.0)),
        volume_surge_mult=float(_attr(cfg, "volume_surge_mult", 1.5)),
        weak_breakout_frac=float(_attr(cfg, "weak_breakout_frac", 0.25)),
        retest_sl_buffer_pct=float(_attr(cfg, "retest_sl_buffer_pct", 0.3)),
    )


def prior_day_levels(daily: List[Candle]) -> Tuple[float, ...]:
    if not daily:
        return ()
    prior = daily[-2] if len(daily) >= 2 else daily[-1]
    return (prior.high, prior.low, prior.close)


def scored_zones_from_candles(
    tf_candles: Dict[str, List[Candle]],
    *,
    knobs: ZoneKnobs,
    scoring: ScoringParams,
    now: Optional[datetime],
) -> List[ScoredZone]:
    """pivots → zones → confluence, over an already-fetched per-TF candle set."""
    zones_by_tf: Dict[str, list] = {}
    for tf, candles in tf_candles.items():
        n = knobs.pivot_n_by_tf.get(tf, knobs.default_pivot_n)
        pivots = find_swing_pivots(candles, left=n, right=n)
        zones_by_tf[tf] = cluster_zones(pivots, cluster_pct=knobs.cluster_pct, timeframe=tf)

    daily = tf_candles.get("day") or next(iter(tf_candles.values()))
    ctx = ConfluenceContext(
        prior_day_levels=prior_day_levels(daily),
        volume_nodes=tuple(volume_profile_nodes(
            daily, bins=knobs.volume_bins, node_frac=knobs.volume_node_frac)),
        now=now,
        recency_window_days=knobs.recency_window_days,
    )
    return score_zones(zones_by_tf, ctx, scoring)


def split_zones(scored: List[ScoredZone]) -> Tuple[List[ScoredZone], List[ScoredZone]]:
    """Return (resistance_zones, support_zones)."""
    return (
        [z for z in scored if z.kind == "RESISTANCE"],
        [z for z in scored if z.kind == "SUPPORT"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# V3 03.01 — Layer-A anchors (VWAP + ORB, intraday) and Layer-B swing labelling.
# Pure functions; stdlib + core.candle_math only. Reference levels for the
# manual-marking validation export; they do NOT gate capital in this step.
# ─────────────────────────────────────────────────────────────────────────────

def round_number_levels(price: Optional[float]) -> Tuple[float, ...]:
    """The magnitude / half-magnitude round levels straddling `price`.

    e.g. price 523 → magnitude 100 → {500, 600} and half {500, 550} → sorted
    (500, 550, 600). Pure; returns () for a missing/non-positive price.
    """
    if price is None or price <= 0:
        return ()
    magnitude = 10.0 ** math.floor(math.log10(price))
    levels: set = set()
    for step in (magnitude, magnitude / 2.0):
        if step <= 0:
            continue
        below = math.floor(price / step) * step
        levels.add(round(below, 6))
        levels.add(round(below + step, 6))
    return tuple(sorted(levels))


def intraday_anchors(
    fine_candles: List[Candle],
    *,
    on_date: "_date",
    session_open: "_time",
    session_close: "_time",
    orb_window_minutes: int,
) -> Dict[str, Optional[float]]:
    """VWAP + opening-range (ORB) anchors from today's fine intraday candles.

    Scopes `fine_candles` to `on_date`'s in-session bars, then:
      - vwap  = core.candle_math.session_vwap over the scoped bars;
      - orb_high / orb_low = max high / min low over the OPENING window
        [session_open, session_open + orb_window_minutes).
    Returns None fields when the data is insufficient (never fabricated). Pure;
    compares only wall-clock times so candle tzinfo (aware or naive IST) is fine.
    """
    scoped = [
        c for c in fine_candles
        if c.ts.date() == on_date and session_open <= c.ts.time() < session_close
    ]
    vwap = session_vwap(scoped) if scoped else None

    orb_high: Optional[float] = None
    orb_low: Optional[float] = None
    if scoped and orb_window_minutes > 0:
        open_dt = datetime.combine(on_date, session_open)
        end_dt = open_dt + timedelta(minutes=orb_window_minutes)
        end_t = end_dt.time() if end_dt.date() == on_date else session_close
        window = [c for c in scoped if c.ts.time() < end_t]
        if window:
            orb_high = max(c.high for c in window)
            orb_low = min(c.low for c in window)

    return {
        "vwap": vwap,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_window_minutes": orb_window_minutes,
    }


def build_anchor_payload(
    daily_candles: List[Candle],
    reference_price: Optional[float],
    intraday: Optional[Dict[str, Optional[float]]] = None,
) -> dict:
    """Combine Layer-A anchors into one export/evidence payload: prior-day
    PDH/PDL/PDC (existing), round numbers, reference price, and the intraday
    VWAP/ORB block (when computed). Pure."""
    pdl = prior_day_levels(daily_candles)
    prior = (
        {"PDH": pdl[0], "PDL": pdl[1], "PDC": pdl[2]} if len(pdl) == 3 else {}
    )
    payload: dict = {
        "prior_day": prior,
        "round_numbers": list(round_number_levels(reference_price)),
        "reference_price": reference_price,
    }
    if intraday:
        payload.update(intraday)
    return payload


def build_swings_payload(scored_zones: List[ScoredZone]) -> Dict[str, list]:
    """Group scored structural zones by S&R Timeframe-Policy ROLE (30m=PRIMARY,
    1h=MAJOR, day=DAILY, …) for the manual-marking export. A merged zone present
    on multiple TFs is listed under each contributing role. Pure."""
    out: Dict[str, list] = {}
    for z in scored_zones:
        for tf in z.timeframes:
            role = TF_ROLE.get(tf, tf)
            out.setdefault(role, []).append({
                "band_low": round(z.band_low, 4),
                "band_high": round(z.band_high, 4),
                "kind": z.kind,
                "confidence": z.confidence,
                "score": round(z.score, 4),
                "touches": z.touches,
                "timeframe": tf,
            })
    return out
