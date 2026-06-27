"""
sr_detector/zone_builder.py — Trading System v2 · S&R V2 (SNR-V2)

Purpose:
    The SINGLE zone-building path, shared by the V1 detector (async observer) and
    the V2 ZoneWarmer. Derive-don't-duplicate: the pivots→zones→confluence
    sequence and the config→params mapping live here ONCE. Pure + stdlib/core only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sr_detector.confluence import ConfluenceContext, ScoringParams, score_zones
from sr_detector.flags import FlagParams
from sr_detector.models import Candle, ScoredZone
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
