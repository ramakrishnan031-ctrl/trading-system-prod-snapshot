"""
v3_chain/score.py — Trading System v2 · V3 Step 10a · the 3-layer score COMPOSER.

NOT a second scorer (spec §4 anti-duplication). step_executor already computes the 8
steps for EVERY signal; this module RE-COMPOSES those raw values into the 3-layer
budget (Playbook 40 / Context 40 / Execution 20) and folds in the new V3 factors.

Step 10a computes CONTEXT (40) + EXECUTION (20) only; the PLAYBOOK layer is `None`
(no playbook exists yet). The total is therefore PARTIAL (out of 60) and is emitted
honestly as `partial_total` — it is NEVER renormalized to 100 (that would fabricate a
number).

THE ANTI-INFLATION RULE (confluence grouping): members of a correlated group are
combined into ONE bounded value BEFORE weighting, so they cannot each take a full
weight. `htf_alignment` groups {1h-EMA-position, 1h-swing-structure}; `momentum_position`
groups {rsi_range, vwap_position}.

Pure + deterministic: stdlib only.
"""
from __future__ import annotations

from typing import List, Optional


def combine(members: List[Optional[float]], method: str) -> float:
    """Combine a confluence group's member fractions (each in [0,1], None = absent)
    into ONE bounded value in [0,1]. "mean" = average of present members (the
    anti-inflation default); "max" = the strongest present member. All-absent → 0.0."""
    present = [max(0.0, min(1.0, float(m))) for m in members if m is not None]
    if not present:
        return 0.0
    if str(method) == "max":
        return max(present)
    return sum(present) / len(present)


def _frac(step_results: dict, name: str) -> float:
    """A step's raw fraction in [0,1] (missing → 0.0, honest)."""
    try:
        return max(0.0, min(1.0, float(step_results.get(name, 0.0))))
    except (TypeError, ValueError):
        return 0.0


def compose_score(
    step_results: dict,
    *,
    regime_fraction: Optional[float],
    sr_target_fraction: Optional[float],
    htf_ema_fraction: Optional[float],
    htf_swing_fraction: Optional[float],
    cfg,
) -> dict:
    """Compose the Context (40) + Execution (20) layers from the existing step
    results + the V3 context factors. Returns a JSON-friendly breakdown; Playbook is
    None (10a). `cfg` is a V3ChainConfig.

    CONTEXT (40): regime_preference · sr_target_quality · htf_alignment[group] ·
                  sector_strength · momentum_position[group]  (each weighted to 8).
    EXECUTION (20): volume_surge · atr · time_of_day · spread — the existing execution
                  steps, proportionally rescaled by (exec_budget / Σ raw exec weights).
    """
    method = getattr(cfg, "confluence_combine", "mean")

    # ── CONTEXT (40) ──────────────────────────────────────────────────────────
    regime_f = 0.0 if regime_fraction is None else max(0.0, min(1.0, float(regime_fraction)))
    sr_target_f = 0.0 if sr_target_fraction is None else max(0.0, min(1.0, float(sr_target_fraction)))
    htf_group = combine([htf_ema_fraction, htf_swing_fraction], method)   # confluence group
    momentum_group = combine(                                             # confluence group
        [_frac(step_results, "rsi_range"), _frac(step_results, "vwap_position")], method)
    sector_f = _frac(step_results, "sector_strength")

    ctx_regime = regime_f * float(cfg.w_regime_preference)
    ctx_sr = sr_target_f * float(cfg.w_sr_target_quality)
    ctx_htf = htf_group * float(cfg.w_htf_alignment)
    ctx_sector = sector_f * float(cfg.w_sector_strength)
    ctx_momentum = momentum_group * float(cfg.w_momentum_position)
    context_total = ctx_regime + ctx_sr + ctx_htf + ctx_sector + ctx_momentum

    # ── EXECUTION (20) — proportional rescale of the raw exec weights ──────────
    raw_sum = (float(cfg.w_exec_volume_surge) + float(cfg.w_exec_atr)
               + float(cfg.w_exec_time_of_day) + float(cfg.w_exec_spread))
    scale = (float(cfg.exec_budget) / raw_sum) if raw_sum > 0 else 0.0
    ex_volume = _frac(step_results, "volume_surge") * float(cfg.w_exec_volume_surge) * scale
    ex_atr = _frac(step_results, "atr_filter") * float(cfg.w_exec_atr) * scale
    ex_tod = _frac(step_results, "time_of_day") * float(cfg.w_exec_time_of_day) * scale
    ex_spread = _frac(step_results, "spread_check") * float(cfg.w_exec_spread) * scale
    execution_total = ex_volume + ex_atr + ex_tod + ex_spread

    return {
        "playbook": None,   # 10a: no playbook (recorded null; never renormalized)
        "context": {
            "regime_preference": round(ctx_regime, 4),
            "sr_target_quality": round(ctx_sr, 4),
            "htf_alignment": round(ctx_htf, 4),
            "sector_strength": round(ctx_sector, 4),
            "momentum_position": round(ctx_momentum, 4),
            "total": round(context_total, 4),
        },
        "execution": {
            "volume_surge": round(ex_volume, 4),
            "atr": round(ex_atr, 4),
            "time_of_day": round(ex_tod, 4),
            "spread": round(ex_spread, 4),
            "total": round(execution_total, 4),
        },
        "partial_total": round(context_total + execution_total, 4),
    }
