"""
sr_detector/confluence.py — Trading System v2 · S&R Detector V1

Purpose:
    Turn per-timeframe zones into MERGED, confluence-scored zones. Confidence is
    NEVER a single method — it is a weighted sum of independent votes, bucketed
    into HIGH / MEDIUM / LOW, with the evidence breakdown preserved so we can
    audit + calibrate (spec E). Pure functions; stdlib only.

Locked Design Decisions:
    SR-C1 — Merge same-kind zones ACROSS timeframes by centre proximity
            (within merge_pct) into one band; the merged band spans the union.
    SR-C2 — Votes (each weighted, config-tuned):
              swing      = w_swing  * min(total_touches, touch_cap)
              multi_tf   = w_multi  * num_distinct_timeframes
              volume     = w_volume if a volume node falls in the band
              prior_day  = w_prior  if a PDH/PDL/PDC level falls in the band
              round_num  = w_round  if a round number falls in the band
              recency    = w_recency * recency_factor (recent touches → higher)
    SR-C3 — HIGH  = score>=t_high AND >=2 distinct method-types contribute AND
                    present on >=2 timeframes.
            MEDIUM= score>=t_med.
            LOW   = otherwise.
            (V1 logs ALL; only HIGH would ever drive a flag — V2 veto.)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sr_detector.models import ScoredZone, Zone


@dataclass(frozen=True)
class ConfluenceContext:
    """External evidence the scorer overlays onto raw zones."""
    prior_day_levels: Tuple[float, ...] = ()     # PDH, PDL, PDC
    volume_nodes: Tuple[float, ...] = ()
    now: Optional[datetime] = None               # recency reference
    recency_window_days: float = 90.0


@dataclass(frozen=True)
class ScoringParams:
    """Confluence weights + thresholds. All CONFIG-tuned (visual-review phase)."""
    w_swing: float = 1.0
    w_volume: float = 1.0
    w_multi_tf: float = 1.0
    w_prior_day: float = 1.0
    w_round: float = 0.5
    w_recency: float = 0.5
    t_high: float = 5.0
    t_med: float = 3.0
    touch_cap: int = 4
    merge_pct: float = 0.4
    band_buffer_pct: float = 0.0
    recency_min_factor: float = 0.0


def score_zones(
    zones_by_tf: Dict[str, List[Zone]],
    ctx: ConfluenceContext,
    params: ScoringParams,
) -> List[ScoredZone]:
    """Merge across TFs and score each merged zone. RESISTANCE + SUPPORT separately."""
    out: List[ScoredZone] = []
    for kind in ("RESISTANCE", "SUPPORT"):
        groups = _merge_across_tfs(zones_by_tf, kind, params.merge_pct)
        for group in groups:
            out.append(_score_group(group, kind, ctx, params))
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────────────

def _merge_across_tfs(
    zones_by_tf: Dict[str, List[Zone]], kind: str, merge_pct: float
) -> List[List[Zone]]:
    """Greedy-cluster same-kind zones from all TFs by centre proximity."""
    flat: List[Zone] = []
    for tf_zones in zones_by_tf.values():
        flat.extend(z for z in tf_zones if z.kind == kind)
    flat.sort(key=lambda z: z.center)

    groups: List[List[Zone]] = []
    for z in flat:
        if not groups:
            groups.append([z])
            continue
        cur = groups[-1]
        cur_center = sum(x.center for x in cur) / len(cur)
        tol = cur_center * (merge_pct / 100.0)
        if abs(z.center - cur_center) <= tol:
            cur.append(z)
        else:
            groups.append([z])
    return groups


def _score_group(
    group: List[Zone], kind: str, ctx: ConfluenceContext, params: ScoringParams
) -> ScoredZone:
    band_low = min(z.band_low for z in group)
    band_high = max(z.band_high for z in group)
    timeframes = tuple(sorted({z.timeframe for z in group}))
    total_touches = sum(z.touches for z in group)
    last_touch = max(
        (z.last_touch_ts for z in group if z.last_touch_ts is not None),
        default=None,
    )

    evidence: List[Tuple[str, float]] = []

    # swing (always present for a real zone)
    swing = params.w_swing * min(total_touches, params.touch_cap)
    if swing > 0:
        evidence.append(("swing_pivots", swing))

    # multi-timeframe agreement
    multi = params.w_multi_tf * len(timeframes)
    if multi > 0:
        evidence.append(("multi_tf", multi))

    # volume node
    if _level_in_band(ctx.volume_nodes, band_low, band_high, params.band_buffer_pct):
        evidence.append(("volume_node", params.w_volume))

    # prior-day level
    if _level_in_band(ctx.prior_day_levels, band_low, band_high, params.band_buffer_pct):
        evidence.append(("prior_day_level", params.w_prior_day))

    # round number
    if _has_round_number(band_low, band_high):
        evidence.append(("round_number", params.w_round))

    # recency
    rfac = _recency_factor(last_touch, ctx, params)
    if rfac > 0 and params.w_recency > 0:
        evidence.append(("recency", params.w_recency * rfac))

    score = sum(c for _, c in evidence)
    distinct_types = len(evidence)
    confidence = _bucket(score, distinct_types, len(timeframes), params)

    return ScoredZone(
        band_low=band_low,
        band_high=band_high,
        kind=kind,
        score=score,
        confidence=confidence,
        touches=total_touches,
        timeframes=timeframes,
        evidence=tuple(evidence),
        last_touch_ts=last_touch,
    )


def _bucket(score: float, distinct_types: int, num_tfs: int, p: ScoringParams) -> str:
    if score >= p.t_high and distinct_types >= 2 and num_tfs >= 2:
        return "HIGH"
    if score >= p.t_med:
        return "MEDIUM"
    return "LOW"


def _level_in_band(
    levels: Tuple[float, ...], low: float, high: float, buffer_pct: float
) -> bool:
    if not levels:
        return False
    center = (low + high) / 2.0
    pad = center * (buffer_pct / 100.0)
    return any((low - pad) <= lv <= (high + pad) for lv in levels)


def _has_round_number(low: float, high: float) -> bool:
    """A multiple of the price magnitude (or half-magnitude) falling in the band."""
    center = (low + high) / 2.0
    if center <= 0:
        return False
    magnitude = 10.0 ** math.floor(math.log10(center))   # e.g. 523 -> 100
    for step in (magnitude, magnitude / 2.0):
        if step <= 0:
            continue
        k = math.ceil(low / step - 1e-9)
        if k * step <= high + 1e-9:
            return True
    return False


def _recency_factor(
    last_touch: Optional[datetime], ctx: ConfluenceContext, p: ScoringParams
) -> float:
    if last_touch is None:
        return 0.0
    now = ctx.now
    if now is None:
        return 1.0
    # tz-safety: kite candle ts may be aware (IST) while now_fn() differs.
    if (now.tzinfo is None) != (last_touch.tzinfo is None):
        now = now.replace(tzinfo=None)
        last_touch = last_touch.replace(tzinfo=None)
    age_days = (now - last_touch).total_seconds() / 86400.0
    if ctx.recency_window_days <= 0:
        return 1.0
    factor = 1.0 - (age_days / ctx.recency_window_days)
    return max(p.recency_min_factor, min(1.0, factor))
