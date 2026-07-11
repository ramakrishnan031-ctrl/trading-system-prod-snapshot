# screening/hard_gate.py — Trading System v2 · V3 03.03 Hard-Gate
#
# The pre-scoring binary Hard-Gate battery. For the CURRENT LIVE flow it houses
# only the rules relocated out of the score: at-circuit, circuit-proximity, and
# freshness (signal_age, 60s). Liquidity is a NO-OP (spread_check stays in the
# 8-step score, A8). The V3-playbook gates (confirmation / pullback / R:R /
# strong-HTF / extreme) are PLAYBOOK-shadow scope — evaluated + logged on the V3
# path only, they NEVER reject a live order in this step.
#
# This module also owns the shared re-scale helpers (the 8-step proportional
# total + tier + min_score re-scale) so the live v3 path and the offline parity
# recompute tool use ONE implementation (no duplication).
#
# Extraction, not duplication: `circuit_proximity_reason` is the RELOCATED body
# of secondary_screener._circuit_proximity_reason — the OFF path delegates to it
# here, so the OFF result stays byte-identical while there is a single source.
#
# Locked decisions: V3 03.03/03.04 (plan docs/v3/V3_STEP4_HARDGATE_SCORER_PLAN.md)

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# Single source of truth for the circuit-band margin (same constant the post-fill
# placeability gate uses) — kept identical to the pre-relocation import.
from orders.price_math import DEFAULT_CIRCUIT_MARGIN_PCT

# Gate reject reasons (status vocabulary — screen() maps these to REJECTED_<reason>).
GATE_AT_CIRCUIT = "AT_CIRCUIT"
GATE_CIRCUIT_PROXIMITY = "CIRCUIT_PROXIMITY"
GATE_STALE = "SIGNAL_AGE"

# Default freshness cutoff (A4) — matches the OLD signal_age>60s → 0.0 hard reject.
DEFAULT_FRESHNESS_MAX_SEC = 60.0


@dataclass(frozen=True)
class GateVerdict:
    """Result of HardGate.evaluate(). `passed=False` short-circuits screen() to
    REJECTED_<reason>. `reason` is None on pass."""
    passed: bool
    reason: Optional[str] = None
    evidence: dict = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Relocated pure rule: circuit-proximity (shared by the OFF path + the gate)
# ─────────────────────────────────────────────────────────────────────────────

def circuit_proximity_reason(
    trigger_price, direction, market_data: dict, *, enabled: bool = True,
) -> Optional[str]:
    """Pre-fill circuit-proximity reject (NOCIL fix, framing-b, BOTH legs).

    Reject an entry sitting at/beyond the exit-clamp ceiling — i.e. where NO
    profitable TGT *or* no valid SL could be placed inside the day's circuit band
    — so the trade is doomed before it fills. Uses the SAME
    DEFAULT_CIRCUIT_MARGIN_PCT as the post-fill placeability gate.

        TGT-ceiling: LONG reject if entry >= upper*(1-m); SHORT if entry <= lower*(1+m)
        SL-ceiling:  LONG reject if entry <= lower*(1+m); SHORT if entry >= upper*(1-m)

    Fail-open: flag OFF, or missing/non-numeric/non-positive band or entry -> None
    (admit). This is the RELOCATED body of the former
    secondary_screener._circuit_proximity_reason (byte-identical logic).
    """
    if not enabled:
        return None

    def _pos_num(x):
        return (
            x if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0
            else None
        )

    upper = _pos_num(market_data.get("upper_circuit"))
    lower = _pos_num(market_data.get("lower_circuit"))
    entry = _pos_num(trigger_price)
    if entry is None or (upper is None and lower is None):
        return None

    margin = DEFAULT_CIRCUIT_MARGIN_PCT
    upper_ceiling = upper * (1.0 - margin) if upper is not None else None
    lower_floor = lower * (1.0 + margin) if lower is not None else None
    is_long = str(direction).upper() in ("LONG", "BUY")

    if is_long:
        if upper_ceiling is not None and entry >= upper_ceiling:
            return (
                f"LONG entry {entry} >= upper-ceiling {upper_ceiling:.2f} "
                f"(upper_circuit {upper}); no profitable TGT fits the band"
            )
        if lower_floor is not None and entry <= lower_floor:
            return (
                f"LONG entry {entry} <= lower-floor {lower_floor:.2f} "
                f"(lower_circuit {lower}); no valid SL fits the band"
            )
    else:
        if lower_floor is not None and entry <= lower_floor:
            return (
                f"SHORT entry {entry} <= lower-floor {lower_floor:.2f} "
                f"(lower_circuit {lower}); no profitable TGT fits the band"
            )
        if upper_ceiling is not None and entry >= upper_ceiling:
            return (
                f"SHORT entry {entry} >= upper-ceiling {upper_ceiling:.2f} "
                f"(upper_circuit {upper}); no valid SL fits the band"
            )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# HardGate — the pre-scoring battery (LIVE-scope rules only for the live flow)
# ─────────────────────────────────────────────────────────────────────────────

class HardGate:
    """Pre-scoring binary gate. LIVE rules only: at-circuit, circuit-proximity,
    freshness. Liquidity is a NO-OP (A8). evaluate() never raises."""

    def __init__(
        self,
        *,
        now_fn,
        logger=None,
        freshness_max_sec: float = DEFAULT_FRESHNESS_MAX_SEC,
        circuit_proximity_reject_enabled: bool = True,
    ) -> None:
        self._now_fn = now_fn
        self._log = logger
        self._freshness_max_sec = float(freshness_max_sec)
        self._proximity_enabled = bool(circuit_proximity_reject_enabled)

    def evaluate(
        self, *, trigger_price, triggered_at, direction, market_data: dict,
    ) -> GateVerdict:
        # 1. at-circuit (mirrors step_executor._step_9_circuit_check: hard reject).
        cs = market_data.get("circuit_state", "")
        if cs in ("upper_circuit", "lower_circuit"):
            return GateVerdict(False, GATE_AT_CIRCUIT, {"circuit_state": cs})

        # 2. circuit-proximity (relocated shared rule; same as the OFF pre-fill reject).
        reason = circuit_proximity_reason(
            trigger_price, direction, market_data, enabled=self._proximity_enabled
        )
        if reason is not None:
            return GateVerdict(False, GATE_CIRCUIT_PROXIMITY, {"detail": reason})

        # 3. freshness (mirrors _step_10_signal_age >max_sec → reject; A4 cutoff 60s).
        #    Missing triggered_at → admit (neutral, matches the OLD 0.5 default).
        if triggered_at is not None:
            now = self._now_fn()
            ta = triggered_at
            if ta.tzinfo is None and now.tzinfo is not None:
                ta = ta.replace(tzinfo=now.tzinfo)
            elif ta.tzinfo is not None and now.tzinfo is None:
                ta = ta.replace(tzinfo=None)
            age_sec = (now - ta).total_seconds()
            if age_sec > self._freshness_max_sec:
                return GateVerdict(False, GATE_STALE, {"age_sec": age_sec})

        # 4. liquidity = NO-OP (A8) — spread_check remains one of the 8 scored steps.
        return GateVerdict(True, None, {})


# ─────────────────────────────────────────────────────────────────────────────
# Shared re-scale helpers (used by the live v3 path AND the offline recompute)
# ─────────────────────────────────────────────────────────────────────────────

def rescaled_total(step_results: dict, step_weights: dict, gate_steps) -> int:
    """8-step proportional total: Σ(raw·weight for KEPT present steps) /
    Σ(weight for KEPT present) × 100, rounded, capped 100. Mirrors
    quality_scorer's proportional formula on the kept subset (missing steps
    excluded from the denominator, FIX-042 parity). `gate_steps` = the step names
    the gate handles (excluded from scoring)."""
    gate = set(gate_steps)
    achieved = 0.0
    present = 0
    for name, weight in step_weights.items():
        if name in gate or name not in step_results:
            continue
        achieved += float(step_results[name]) * weight
        present += weight
    if present <= 0:
        return 0
    return min(100, int(round(achieved / present * 100.0)))


def tier_for(score: int, high_threshold: int, medium_threshold: int) -> str:
    """HIGH/MEDIUM/LOW from a score + thresholds (same rule as quality_scorer)."""
    if score >= high_threshold:
        return "HIGH"
    if score >= medium_threshold:
        return "MEDIUM"
    return "LOW"


def rescale_min_score(old_min_score: int) -> int:
    """Re-scale a per-strategy min_score override to the 8-step scale (A7,
    fresh-reference): new = 1.25·old − 25, floored at 0. Zero (use-global) stays
    zero. All 15 strategies are currently 0, so this is inert today."""
    if not old_min_score or old_min_score <= 0:
        return 0
    return max(0, int(round(1.25 * old_min_score - 25.0)))
