# =============================================================================
# Script        : v3_chain/forward_shadow.py
# Purpose       : FORWARD SHADOW recorder (research; RECORDS ONLY, changes nothing).
#                 For EVERY live signal — not just those clearing min_pass — capture,
#                 OUT-OF-SAMPLE and forward, the data needed to test the band-inversion
#                 finding on FRESH data before any live min_pass_score decision:
#                   - old score + band (already persisted in screener_results)
#                   - the M-S4-fixed score (COMPUTED, never used for any decision)
#                   - the true-path SIMULATED outcome (same SL/TGT/cost as the audit)
#                   - the realised outcome (for signals that actually traded)
# Why (pure)    : This module is PURE (no I/O, no clock, no broker, no config, no mode
#                 branch). The EOD batch script (scripts/forward_shadow_record.py) does
#                 the fetch/read/append; this holds only the recompute + the walker so
#                 they are unit-testable and identical to the backfill/§3/Phase-2 method.
# NO-LOOKAHEAD  : the M-S4 score uses only daily stats computed from candles that CLOSED
#                 BEFORE the signal date (core.daily_stats). The sim walks only candles
#                 at/after the signal time.
# Live path     : UNTOUCHED. This runs EOD off the hot path and RECORDS; it never places,
#                 delays, or alters an order, and no production config changes.
# =============================================================================
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple


# Mirrors screening/step_executor _step_1/_step_3/_step_4/_step_6 exactly (the 4 dead
# steps M-S4 wires). Kept here (not imported) so the recompute is a small, auditable,
# dependency-free function — the same logic the §3/Phase-2 backfill validated.
def ms4_step_values(
    *,
    volume: float,
    ltp: float,
    direction: str,
    avg_volume_20d: Optional[float],
    atr14: Optional[float],
    rsi14: Optional[float],
    sector: Optional[str],
    min_volume_surge: float = 1.5,
    min_adr_pct: float = 0.5,
) -> Dict[str, float]:
    """Recompute the 4 M-S4-wired step values from REAL inputs. Fail-safe to today's
    behaviour (0.0 / 0.5) on any missing input — never fabricates."""
    vs = 1.0 if (avg_volume_20d and volume and volume > avg_volume_20d * min_volume_surge) else 0.0
    af = 1.0 if (atr14 and ltp and (atr14 / ltp * 100.0) >= min_adr_pct) else 0.0
    if rsi14 is None:
        rr = 0.5
    elif direction == "LONG":
        rr = 1.0 if 40.0 <= rsi14 <= 80.0 else 0.0
    else:
        rr = 1.0 if 20.0 <= rsi14 <= 60.0 else 0.0
    ss = 1.0 if sector else 0.5
    return {"volume_surge": vs, "atr_filter": af, "rsi_range": rr, "sector_strength": ss}


def ms4_score(old_step_results: Dict[str, float], ms4_values: Dict[str, float],
              weights: Dict[str, float]) -> int:
    """The M-S4-fixed score = Σ wᵢ·vᵢ with the 4 dead steps replaced (the rest of the
    persisted step_results are alive and unchanged). Matches the persisted OLD score
    formula (score == round(Σ w·v), validated at c1ad82e). COMPUTED-ONLY — never gates."""
    merged = dict(old_step_results)
    merged.update(ms4_values)
    return round(sum(weights.get(k, 0.0) * v for k, v in merged.items()))


def score_band(score: float) -> str:
    """The coarse band used by the inversion study (matches the audit's binning)."""
    lo = int(score) // 5 * 5
    if score < 35:
        return "0-34"
    if score >= 60:
        return "60-65"
    return f"{lo}-{lo + 4}"


def simulate_true_path(
    entry: float, direction: str, candles: Sequence[Tuple[float, float, float]],
    *, sl_pct: float = 0.01, tgt_r: float = 1.5,
) -> Optional[float]:
    """Walk the true 1-min path from entry to session end, HONOURING candle ordering
    (conservative: the adverse extreme is assumed hit before the favourable within a
    candle). `candles` = (high, low, close) in chronological order, from the signal time
    onward. Returns the realised R (SL=-1R, TGT=+tgt_r R, else the squareoff R at the last
    close), or None if there are no candles or a degenerate entry. Identical rules + the
    same conservative convention as the §3/Q2.8 walker."""
    if entry is None or entry <= 0 or not candles:
        return None
    long = direction == "LONG"
    sld = entry * sl_pct
    if sld <= 0:
        return None
    sl = entry - sld if long else entry + sld
    tg = entry + tgt_r * sld if long else entry - tgt_r * sld
    for h, l, _cl in candles:
        if long:
            if l <= sl:
                return -1.0
            if h >= tg:
                return tgt_r
        else:
            if h >= sl:
                return -1.0
            if l <= tg:
                return tgt_r
    last = candles[-1][2]
    return ((last - entry) if long else (entry - last)) / sld
