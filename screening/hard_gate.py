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
from core.effect_telemetry import handle as _effect_handle
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
        # effect-telemetry (ledger #1, frozen contract A2.1): one handle,
        # resolved once — the hot-path op is a single integer increment.
        self._fx_verdict = _effect_handle("hard_gate")

    def evaluate(
        self, *, trigger_price, triggered_at, direction, market_data: dict,
    ) -> GateVerdict:
        # effect-telemetry (frozen A2.1): a hardgate verdict produced — every
        # path returns and evaluate() never raises, so entry-count == verdicts.
        self._fx_verdict.inc()
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


# ─────────────────────────────────────────────────────────────────────────────
# V3 Step 10 — GENERIC playbook-scope gates (G-RR / G-HTF / G-EXTREME).
#
# These are the gates computable for ANY signal (no playbook context) — the ones
# Step 10a runs in SHADOW over live signals to measure the S&R R:R gate against
# real trade outcomes (G-KALYAN). They are PURE FUNCTIONS (float/duck-typed
# inputs) and, on the V3 path, LOG-ONLY: the caller records the verdict and NEVER
# rejects/delays/alters a live order. The playbook-scope must-haves G-CONFIRM /
# G-PULLBACK (and the Playbook score layer) are Step 10b (they need a playbook).
#
# Missing-data rules per the V3 DECISION CONTENT SPECIFICATION v1.0 §3:
#   G-RR      must-have  → missing S&R FAILS (never guess a target).
#   G-HTF     must-NOT   → missing data PASSES (fail-OPEN; only a STRONG contra fails).
#   G-EXTREME must-NOT   → missing/OFF regime PASSES (03.02 fail-safe).
# ─────────────────────────────────────────────────────────────────────────────

GATE_RR = "RR"
GATE_HTF = "HTF"
GATE_EXTREME = "EXTREME"


def _is_long(side: str) -> bool:
    return str(side).strip().upper() in ("BUY", "LONG")


def gate_rr(
    side: str,
    entry: float,
    *,
    sl_zone_edge: Optional[float],
    tgt_zone_edge: Optional[float],
    atr30: Optional[float],
    rr_floor: float,
    sl_buffer_atr_mult: float,
) -> GateVerdict:
    """G-RR (must-have; missing S&R → FAIL). Generic side-aware S&R R:R (spec §3;
    the 10a version uses the nearest structural zones, without the playbook
    level/retest-low which arrive in 10b).

      LONG  : SL = support_edge − buffer (below support); TGT = resistance_edge above.
      SHORT : SL = resistance_edge + buffer (above resistance); TGT = support_edge below.
      buffer = sl_buffer_atr_mult × ATR30  (0 when ATR30 is unavailable).
      R:R = reward / risk;  PASS iff R:R >= rr_floor.

    `sl_zone_edge` / `tgt_zone_edge` are the relevant band edges the caller selected
    (None when no such zone exists → FAIL). The computed SL/TGT/RR are returned in
    `evidence` so the would-be record can log them EVEN on a fail (the comparison to
    the live FIXED_PCT SL is itself a finding). Never raises."""
    try:
        if entry is None or entry <= 0:
            return GateVerdict(False, GATE_RR, {"detail": "no entry price"})
        if sl_zone_edge is None or tgt_zone_edge is None:
            return GateVerdict(False, GATE_RR, {
                "detail": "missing S&R zone (no safe SL or TGT)",
                "sl_zone_edge": sl_zone_edge, "tgt_zone_edge": tgt_zone_edge,
            })
        buffer = (float(sl_buffer_atr_mult) * float(atr30)) if atr30 else 0.0
        if _is_long(side):
            sl = float(sl_zone_edge) - buffer
            tgt = float(tgt_zone_edge)
            risk = entry - sl
            reward = tgt - entry
        else:
            sl = float(sl_zone_edge) + buffer
            tgt = float(tgt_zone_edge)
            risk = sl - entry
            reward = entry - tgt
        ev = {"v3_sl": round(sl, 4), "v3_tgt": round(tgt, 4)}
        if risk <= 0 or reward <= 0:
            ev["detail"] = f"invalid geometry (risk={risk:.4f}, reward={reward:.4f})"
            ev["v3_rr"] = None
            return GateVerdict(False, GATE_RR, ev)
        rr = reward / risk
        ev["v3_rr"] = round(rr, 4)
        if rr >= float(rr_floor):
            return GateVerdict(True, None, ev)
        ev["detail"] = f"R:R {rr:.2f} < floor {rr_floor}"
        return GateVerdict(False, GATE_RR, ev)
    except Exception as exc:   # a gate must never raise into the shadow worker
        return GateVerdict(False, GATE_RR, {"detail": f"error: {exc}"})


def gate_htf(
    side: str,
    *,
    htf_close: Optional[float],
    htf_ema: Optional[float],
    htf_swing_highs: Optional[list] = None,
    htf_swing_lows: Optional[list] = None,
) -> GateVerdict:
    """G-HTF (must-NOT-have / blocker; missing → PASS, fail-OPEN). FAILS only on a
    STRONG 1-hour contradiction (spec §3):

      LONG  fails iff (1h close < 1h EMA) AND (last two 1h swing HIGHS descending).
      SHORT fails iff (1h close > 1h EMA) AND (last two 1h swing LOWS ascending).

    A mild pullback against the 1h is ALLOWED — that IS the entry. `htf_swing_*` are
    chronological price lists (e.g. from find_swing_pivots); need >= 2 to judge the
    structure, else the structural leg is not met → PASS. Never raises."""
    try:
        if htf_close is None or htf_ema is None:
            return GateVerdict(True, None, {"detail": "no HTF data → pass (fail-open)"})
        highs = list(htf_swing_highs or [])
        lows = list(htf_swing_lows or [])
        if _is_long(side):
            below_ema = htf_close < htf_ema
            descending = len(highs) >= 2 and highs[-1] < highs[-2]
            if below_ema and descending:
                return GateVerdict(False, GATE_HTF, {
                    "detail": "strong 1h contradiction (close<EMA & swing highs descending)",
                    "close": htf_close, "ema": htf_ema, "last_highs": highs[-2:],
                })
        else:
            above_ema = htf_close > htf_ema
            ascending = len(lows) >= 2 and lows[-1] > lows[-2]
            if above_ema and ascending:
                return GateVerdict(False, GATE_HTF, {
                    "detail": "strong 1h contradiction (close>EMA & swing lows ascending)",
                    "close": htf_close, "ema": htf_ema, "last_lows": lows[-2:],
                })
        return GateVerdict(True, None, {"close": htf_close, "ema": htf_ema})
    except Exception as exc:
        return GateVerdict(True, None, {"detail": f"error → pass (fail-open): {exc}"})


def gate_extreme(regime_state) -> GateVerdict:
    """G-EXTREME (must-NOT-have / blocker; missing/OFF → PASS, fail-OPEN). FAILS iff a
    regime snapshot exists AND its extreme_flag is True (a confirmed halt/index
    circuit, 03.02 positive-confirmation-only). regime None / status UNKNOWN → PASS —
    a broken or disabled regime NEVER halts the book. Never raises."""
    try:
        if regime_state is None:
            return GateVerdict(True, None, {"detail": "regime OFF/unavailable → pass"})
        if bool(getattr(regime_state, "extreme_flag", False)):
            return GateVerdict(False, GATE_EXTREME, {"detail": "regime extreme_flag TRUE"})
        return GateVerdict(True, None, {})
    except Exception as exc:
        return GateVerdict(True, None, {"detail": f"error → pass (fail-open): {exc}"})


# ─────────────────────────────────────────────────────────────────────────────
# V3 Step 10b — PLAYBOOK-scope must-have gates (G-CONFIRM / G-PULLBACK).
#
# These are the PB-01-specific must-haves (spec §3). They evaluate ONLY for a
# v3_playbook candidate (the next-morning 5-min retest). Like the generic gates
# they are PURE FUNCTIONS and, on the V3 path, LOG-ONLY (the shadow chain records
# the verdict; no live order exists — PB-01 is enabled:false / would-be only).
# BOTH are must-have: any missing input → FAIL (never guess a confirmation).
# ─────────────────────────────────────────────────────────────────────────────

GATE_CONFIRM = "CONFIRM"
GATE_PULLBACK = "PULLBACK"


def gate_confirm(
    *,
    candle_open: Optional[float],
    candle_high: Optional[float],
    candle_low: Optional[float],
    candle_close: Optional[float],
    candle_volume: Optional[float],
    level: Optional[float],
    baseline_5m_volume: Optional[float],
    min_body_frac: float,
    volume_mult: float,
) -> GateVerdict:
    """G-CONFIRM (playbook must-have; missing data → FAIL). A 5-minute candle CLOSES
    above LEVEL (a close, never a wick) with a decisive body and volume (spec §3):

        close > LEVEL
        body_frac = |close-open| / (high-low)  >=  min_body_frac   (rejects dojis)
        volume >= volume_mult × baseline_5m_volume

    `baseline_5m_volume` = SMA20(daily volume) / candles_per_session (computed by the
    caller — deliberately independent of the session's own first candles). Any
    missing/degenerate input → FAIL. Never raises."""
    try:
        vals = (candle_open, candle_high, candle_low, candle_close, candle_volume,
                level, baseline_5m_volume)
        if any(v is None for v in vals):
            return GateVerdict(False, GATE_CONFIRM, {"detail": "missing data → fail (must-have)"})
        o, h, l = float(candle_open), float(candle_high), float(candle_low)
        c, vol = float(candle_close), float(candle_volume)
        lvl, base = float(level), float(baseline_5m_volume)
        rng = h - l
        if rng <= 0:
            return GateVerdict(False, GATE_CONFIRM, {"detail": "degenerate candle (high<=low)"})
        if base <= 0:
            return GateVerdict(False, GATE_CONFIRM, {"detail": "no baseline volume → fail (must-have)"})
        ev = {"close": round(c, 4), "level": round(lvl, 4),
              "body_frac": None, "volume": vol, "baseline_5m_volume": round(base, 4)}
        if c <= lvl:
            ev["detail"] = f"close {c} not above LEVEL {lvl} (wick, not a close)"
            return GateVerdict(False, GATE_CONFIRM, ev)
        body_frac = abs(c - o) / rng
        ev["body_frac"] = round(body_frac, 4)
        if body_frac < float(min_body_frac):
            ev["detail"] = f"body_frac {body_frac:.2f} < min {min_body_frac} (doji/indecision)"
            return GateVerdict(False, GATE_CONFIRM, ev)
        if vol < float(volume_mult) * base:
            ev["detail"] = f"volume {vol:.0f} < {volume_mult}×baseline {base:.1f}"
            return GateVerdict(False, GATE_CONFIRM, ev)
        return GateVerdict(True, None, ev)
    except Exception as exc:
        return GateVerdict(False, GATE_CONFIRM, {"detail": f"error: {exc}"})


def gate_pullback(
    *,
    level: Optional[float],
    session_low: Optional[float],
    lowest_5m_close: Optional[float],
    atr30: Optional[float],
    proximity_pct: float,
    proximity_atr_mult: float,
    hold_buffer_atr_mult: float,
) -> GateVerdict:
    """G-PULLBACK (playbook must-have; missing data → FAIL). The valid pullback /
    higher-low (spec §3) — BOTH must hold:

      (a) TOUCHED: the session low since the open came within pullback_proximity of
          LEVEL, where pullback_proximity = max(proximity_pct×LEVEL,
          proximity_atr_mult×ATR30). (price genuinely came back — not a straight run.)
      (b) HELD:    NO 5-min candle CLOSED below (LEVEL - hold_buffer_atr_mult×ATR30)
          (a close below = the support/resistance flip failed = invalidated).

    `lowest_5m_close` = the lowest 5-min CLOSE seen since the open (the caller tracks
    it). Any missing input (incl. ATR30) → FAIL (must-have). Never raises."""
    try:
        if level is None or session_low is None or lowest_5m_close is None or atr30 is None:
            return GateVerdict(False, GATE_PULLBACK, {
                "detail": "missing data (level/session_low/lowest_close/ATR30) → fail (must-have)"})
        lvl, slow = float(level), float(session_low)
        lclose, a = float(lowest_5m_close), float(atr30)
        proximity = max(float(proximity_pct) * lvl, float(proximity_atr_mult) * a)
        hold_floor = lvl - float(hold_buffer_atr_mult) * a
        touched = slow <= lvl + proximity
        held = lclose >= hold_floor
        ev = {"level": round(lvl, 4), "session_low": round(slow, 4),
              "lowest_5m_close": round(lclose, 4), "proximity": round(proximity, 4),
              "hold_floor": round(hold_floor, 4), "touched": touched, "held": held}
        if not touched:
            ev["detail"] = (f"no touch: session_low {slow} > LEVEL+proximity "
                            f"{lvl + proximity:.2f} (straight-line run, never came back)")
            return GateVerdict(False, GATE_PULLBACK, ev)
        if not held:
            ev["detail"] = (f"not held: a 5m close {lclose} < hold_floor "
                            f"{hold_floor:.2f} (the S-R flip failed)")
            return GateVerdict(False, GATE_PULLBACK, ev)
        return GateVerdict(True, None, ev)
    except Exception as exc:
        return GateVerdict(False, GATE_PULLBACK, {"detail": f"error: {exc}"})
