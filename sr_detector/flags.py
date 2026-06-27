"""
sr_detector/flags.py — Trading System v2 · S&R Detector V1

Purpose:
    From a placed candidate + its confluence-scored zones, emit zero-or-more
    structural flags and a RETEST PROPOSAL (proposal only in V1 — no outcome
    simulation). Resistance/longs are the first-class target (that is where the
    ~13% LONG win-rate problem lives); support/shorts get the mirror set.
    Pure functions; stdlib only. SHADOW — never rejects, never sizes.

Flags (spec F):
    BUYING_INTO_RESISTANCE    long entry within buffer of a HIGH resistance zone
    SELLING_INTO_SUPPORT      short mirror
    WEAK_BREAKOUT             breakout candle lacks follow-through
    NO_VOLUME_CONFIRMATION    breakout without a volume surge
    LOW_CONFIDENCE_STRUCTURE  nearest zone only LOW/MED confidence
    NO_CLEAR_STRUCTURE        no usable zones
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from sr_detector.models import Candidate, RetestProposal, ScoredZone


@dataclass(frozen=True)
class BreakoutContext:
    """Recent price action used for breakout/volume flags (built from candles)."""
    last_close: float
    last_high: float
    last_low: float
    last_volume: float
    avg_volume: float


@dataclass(frozen=True)
class FlagParams:
    entry_proximity_pct: float = 1.0   # entry within this % of a zone → "into" it
    volume_surge_mult: float = 1.5     # breakout volume must exceed this × avg
    weak_breakout_frac: float = 0.25   # clears band by < frac×width → weak follow-through
    retest_sl_buffer_pct: float = 0.3  # proposed retest SL this % beyond the zone


@dataclass(frozen=True)
class FlagResult:
    nearest_resistance: Optional[ScoredZone]
    nearest_support: Optional[ScoredZone]
    dist_to_resistance_pct: Optional[float]
    dist_to_support_pct: Optional[float]
    flags: Tuple[str, ...]
    retest: RetestProposal


def compute_flags_and_retest(
    candidate: Candidate,
    scored_zones: List[ScoredZone],
    breakout: Optional[BreakoutContext],
    params: FlagParams,
) -> FlagResult:
    entry = float(candidate.intended_entry)
    resistances = [z for z in scored_zones if z.kind == "RESISTANCE"]
    supports = [z for z in scored_zones if z.kind == "SUPPORT"]

    nearest_res = _nearest(resistances, entry, prefer_above=True)
    nearest_sup = _nearest(supports, entry, prefer_above=False)

    dist_res = ((nearest_res.center - entry) / entry * 100.0) if (nearest_res and entry) else None
    dist_sup = ((entry - nearest_sup.center) / entry * 100.0) if (nearest_sup and entry) else None

    flags: List[str] = []
    retest = RetestProposal()

    if not scored_zones:
        flags.append("NO_CLEAR_STRUCTURE")
        return FlagResult(nearest_res, nearest_sup, dist_res, dist_sup, tuple(flags), retest)

    if candidate.is_long:
        # Buying into overhead resistance.
        overhead = nearest_res
        if overhead is not None and _is_into_zone(entry, overhead, params.entry_proximity_pct, from_below=True):
            if overhead.confidence == "HIGH":
                flags.append("BUYING_INTO_RESISTANCE")
                retest = _retest_for_long(overhead, params)
            else:
                flags.append("LOW_CONFIDENCE_STRUCTURE")
        _breakout_flags(flags, overhead, breakout, params, is_long=True)
    else:
        # Selling into underlying support (mirror).
        underlying = nearest_sup
        if underlying is not None and _is_into_zone(entry, underlying, params.entry_proximity_pct, from_below=False):
            if underlying.confidence == "HIGH":
                flags.append("SELLING_INTO_SUPPORT")
                retest = _retest_for_short(underlying, params)
            else:
                flags.append("LOW_CONFIDENCE_STRUCTURE")
        _breakout_flags(flags, underlying, breakout, params, is_long=False)

    # de-dupe while preserving order
    seen: set = set()
    ordered = [f for f in flags if not (f in seen or seen.add(f))]
    return FlagResult(nearest_res, nearest_sup, dist_res, dist_sup, tuple(ordered), retest)


# ─────────────────────────────────────────────────────────────────────────────
# Internal
# ─────────────────────────────────────────────────────────────────────────────

def _nearest(zones: List[ScoredZone], entry: float, *, prefer_above: bool) -> Optional[ScoredZone]:
    if not zones:
        return None
    if prefer_above:
        preferred = [z for z in zones if z.band_high >= entry]
    else:
        preferred = [z for z in zones if z.band_low <= entry]
    pool = preferred or zones
    return min(pool, key=lambda z: abs(z.center - entry))


def _is_into_zone(entry: float, zone: ScoredZone, proximity_pct: float, *, from_below: bool) -> bool:
    """
    Long: entry sits at/inside the band or just below it (within proximity), but
    NOT above band_high (that is a breakout, handled separately). Short mirrors.
    """
    pad = zone.center * (proximity_pct / 100.0)
    if from_below:
        return (zone.band_low - pad) <= entry <= zone.band_high
    return zone.band_low <= entry <= (zone.band_high + pad)


def _breakout_flags(
    flags: List[str],
    zone: Optional[ScoredZone],
    breakout: Optional[BreakoutContext],
    params: FlagParams,
    *,
    is_long: bool,
) -> None:
    if zone is None or breakout is None:
        return
    width = max(zone.band_high - zone.band_low, 1e-9)

    if is_long:
        cleared = breakout.last_close > zone.band_high
        poke_fail = breakout.last_high > zone.band_high and breakout.last_close <= zone.band_high
        weak = cleared and (breakout.last_close - zone.band_high) < params.weak_breakout_frac * width
        any_breakout = cleared or poke_fail
    else:
        cleared = breakout.last_close < zone.band_low
        poke_fail = breakout.last_low < zone.band_low and breakout.last_close >= zone.band_low
        weak = cleared and (zone.band_low - breakout.last_close) < params.weak_breakout_frac * width
        any_breakout = cleared or poke_fail

    if weak or poke_fail:
        flags.append("WEAK_BREAKOUT")
    if any_breakout and breakout.avg_volume > 0 and breakout.last_volume < params.volume_surge_mult * breakout.avg_volume:
        flags.append("NO_VOLUME_CONFIRMATION")


def _retest_for_long(zone: ScoredZone, params: FlagParams) -> RetestProposal:
    # Wait for a break above and a retest of the zone from above (res→support).
    sl = zone.band_low * (1.0 - params.retest_sl_buffer_pct / 100.0)
    return RetestProposal(would_wait=True, entry=zone.band_high, sl=sl)


def _retest_for_short(zone: ScoredZone, params: FlagParams) -> RetestProposal:
    sl = zone.band_high * (1.0 + params.retest_sl_buffer_pct / 100.0)
    return RetestProposal(would_wait=True, entry=zone.band_low, sl=sl)
