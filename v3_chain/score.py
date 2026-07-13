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


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ── PLAYBOOK-layer factor helpers (spec §4; PB-01, Step 10b) ──────────────────
# Pure math for the three Playbook inputs. Each returns a fraction in [0,1]; any
# missing/degenerate input → 0.0 (honest, never fabricates strength).

def retest_quality_fraction(pullback_low, level, atr30, span: float) -> float:
    """retest_quality (spec §4): peaks at a PRECISE retest (dist ≈ 0), decays for both
    a shallow no-touch and a deep plunge-through.
        dist = (pullback_low − LEVEL) / ATR30 ;  frac = max(0, 1 − |dist| / span)."""
    try:
        if pullback_low is None or level is None or not atr30:
            return 0.0
        a, s = float(atr30), float(span)
        if a <= 0 or s <= 0:
            return 0.0
        dist = (float(pullback_low) - float(level)) / a
        return max(0.0, 1.0 - abs(dist) / s)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def confirmation_measure_fraction(candle_open, candle_high, candle_low, candle_close,
                                  min_body_frac: float) -> float:
    """The playbook confirmation-candle measure (spec §4):
        0.5 × body_norm + 0.5 × close_position
      body_norm = clamp((body_frac − min)/(1 − min), 0, 1)  — rewards only the EXCESS
        over the GATE minimum (a candle exactly at the gate floor scores 0 here).
      close_position = (close − low) / (high − low)."""
    try:
        o, h = float(candle_open), float(candle_high)
        l, c = float(candle_low), float(candle_close)
        rng = h - l
        if rng <= 0:
            return 0.0
        body_frac = abs(c - o) / rng
        m = float(min_body_frac)
        denom = 1.0 - m
        body_norm = 0.0 if denom <= 0 else _clamp01((body_frac - m) / denom)
        close_position = _clamp01((c - l) / rng)
        return 0.5 * body_norm + 0.5 * close_position
    except (TypeError, ValueError):
        return 0.0


def level_significance_fraction(touches, cap: int) -> float:
    """level_significance (spec §4): strength of the BROKEN LEVEL as support (prior
    touches). frac = min(touches, cap) / cap. (Callers may pass a zone-confidence
    fraction directly instead; this is the touch-count normalization.)"""
    try:
        if touches is None or not cap or int(cap) <= 0:
            return 0.0
        return _clamp01(min(int(touches), int(cap)) / float(cap))
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
    playbook: Optional[dict] = None,
) -> dict:
    """Compose the 3-layer score from the existing step results + the V3 factors.
    Returns a JSON-friendly breakdown. `cfg` is a V3ChainConfig.

    CONTEXT (40): regime_preference · sr_target_quality · htf_alignment[group] ·
                  sector_strength · momentum_position[group]  (each weighted to 8).
    EXECUTION (20): volume_surge · atr · time_of_day · spread — the existing execution
                  steps, proportionally rescaled by (exec_budget / Σ raw exec weights).
    PLAYBOOK (40): computed ONLY when `playbook` is supplied (a v3_playbook candidate,
                  Step 10b) — a dict of pre-computed fractions
                  {retest_quality_frac, confirmation_measure_frac, level_significance_frac}.
                  confirmation_strength is a CONFLUENCE GROUP with the existing
                  `price_action` step (they share the 15 pts — no double-count).
                  When `playbook is None` (Step 10a) the Playbook layer is null and the
                  score is emitted as `partial_total` (out of 60) — NEVER renormalized.
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

    # ── PLAYBOOK (40) — only for a v3_playbook candidate (spec §4). ────────────
    playbook_block = None
    if playbook is not None:
        retest_f = _clamp01(float(playbook.get("retest_quality_frac", 0.0) or 0.0))
        confirm_measure_f = float(playbook.get("confirmation_measure_frac", 0.0) or 0.0)
        level_f = _clamp01(float(playbook.get("level_significance_frac", 0.0) or 0.0))
        # confirmation_strength CONFLUENCE GROUP: {playbook confirmation measure,
        # the existing price_action step} — they share the 15 pts (anti-inflation).
        # A watchlist candidate (PB-01) never ran the intraday step screener, so its
        # price_action step is ABSENT (a missing key, not a real 0). An absent member is
        # DROPPED from the confluence (never dilutes) → confirmation_strength = the
        # playbook measure alone. When a real screener price_action IS present (a webhook
        # signal) it groups normally. (combine() already ignores None members.)
        pa_member = _frac(step_results, "price_action") if "price_action" in step_results else None
        confirm_group = combine([confirm_measure_f, pa_member], method)
        pb_retest = retest_f * float(cfg.w_retest_quality)
        pb_confirm = confirm_group * float(cfg.w_confirmation_strength)
        pb_level = level_f * float(cfg.w_level_significance)
        playbook_total = pb_retest + pb_confirm + pb_level
        playbook_block = {
            "retest_quality": round(pb_retest, 4),
            "confirmation_strength": round(pb_confirm, 4),
            "level_significance": round(pb_level, 4),
            "total": round(playbook_total, 4),
        }

    result = {
        "playbook": playbook_block,   # None (10a) or the Playbook-40 breakdown (10b)
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
        # partial_total (context+execution) is ALWAYS present (byte-identical to 10a);
        # the FULL total (out of 100) is added ONLY when the Playbook layer is present.
        "partial_total": round(context_total + execution_total, 4),
    }
    if playbook_block is not None:
        result["total"] = round(playbook_block["total"] + context_total + execution_total, 4)
    return result
