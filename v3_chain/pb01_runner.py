"""
v3_chain/pb01_runner.py — Trading System v2 · V3 Step 10b · the PB-01 PLACE-FREE runner.

Builds + persists ONE would-be record from a confirmed PB-01 candidate (a Pb01Confirmation
handed over by the entry stage). This is the dedicated SHADOW orchestration the resume
note describes: PB-01 is `enabled: false`, so it is rejected before the `_process_one`
observe hook — its would-be verdict comes from HERE, and `_process_one` stays untouched.

  • G-NO-ORDER (the non-negotiable) — by CONSTRUCTION: this class writes ONE JSONL row
    and does nothing else. It holds NO fund_manager / order_placer / broker / reserve /
    queue reference of any kind. A PB-01 candidate can NEVER place, modify, or cancel a
    real order, through this path or any path. Fail-closed: any error → the record is
    skipped (logged), never an order.
  • NO-LOOKAHEAD — every structural input is truncated to the confirmation instant
    (`conf.as_of` = the confirmation candle's CLOSE) before any zone/indicator is built.
  • NO SECOND SCORER / GATE MODULE — REUSES the pure V3 helpers verbatim:
    screening.hard_gate (gate_rr/htf/extreme + the already-passed gate_confirm/pullback,
    recorded), v3_chain.score (compose_score + the Playbook-40 factor helpers), the
    shared regime_fraction/sr_target_fraction + zone/indicator helpers in v3_chain.runner.

The would-be OUTCOME (did it hit SL or TGT → expectancy) is NOT computed here — that is a
soak-time historical replay (the --pb01 report), so the verdict frozen here carries no
hindsight. Every threshold is a config SEED (spec §1), never hardcoded.
"""
from __future__ import annotations

import json
import os
from typing import Any, List, Optional

from core.candle_math import ema
from screening.hard_gate import (
    GATE_CONFIRM, GATE_EXTREME, GATE_HTF, GATE_PULLBACK, GATE_RR,
    gate_confirm, gate_extreme, gate_htf, gate_pullback, gate_rr,
)
from sr_detector.models import CONF_ANCHOR_ONLY
from sr_detector.pivots import find_swing_pivots
from sr_detector.zone_builder import scored_zones_from_candles, split_zones
from v3_chain.models import WouldBeRecord
from v3_chain.runner import (
    _htf_alignment_fractions, _nearest_resistance_above, _nearest_support_below, _safe,
    regime_fraction, sr_target_fraction,
)
from v3_chain.score import (
    compose_score, confirmation_measure_fraction, level_significance_fraction,
    retest_quality_fraction,
)
from v3_chain.truncate import last_close_ts, truncate_to_asof

_TF_30 = "30minute"
_TF_60 = "60minute"
_TF_DAY = "day"
_STRATEGY = "pb01_breakout_retest"


class Pb01WouldBeRunner:
    """Turns a confirmed PB-01 candidate into ONE would-be record. Place-free (no order
    path). Constructed only when `watchlist.enabled`."""

    def __init__(
        self,
        *,
        config,                 # WatchlistConfig (would_be_log_path)
        v3_cfg,                 # V3ChainConfig (gate/score SEED knobs)
        fetcher,                # sr_detector.fetch.OhlcFetcher (own instance)
        zone_knobs,             # sr_detector.zone_builder.ZoneKnobs (reuse)
        zone_scoring,           # sr_detector.confluence.ScoringParams (reuse)
        logger,
        now_fn,
        regime_runner=None,     # regime.runner.MarketRegimeShadowRunner | None (for .latest)
    ) -> None:
        self._cfg = config
        self._v3 = v3_cfg
        self._fetcher = fetcher
        self._knobs = zone_knobs
        self._scoring = zone_scoring
        self._log = logger
        self._now = now_fn
        self._regime_runner = regime_runner

    # ── entry point (the entry stage's on_confirm callback) ───────────────────
    def record(self, conf) -> Optional[WouldBeRecord]:
        """Build + persist the would-be record for a confirmed candidate. Never raises
        into the entry stage; a failure is logged and no record is written (fail-closed —
        an error is NEVER an order)."""
        try:
            rec = self._build(conf)
        except Exception as exc:
            self._safe_log("error", "pb01 would-be build failed for %s (skipped): %s",
                           getattr(conf, "symbol", "?"), exc)
            return None
        self._persist(rec)
        return rec

    # ── build (pure w.r.t. the injected fetcher + the frozen confirmation) ────
    def _build(self, conf) -> WouldBeRecord:
        as_of = conf.as_of
        entry = float(conf.entry_price)
        level = float(conf.level)
        atr30 = conf.atr30
        # PB-01 is a LONG intraday breakout-retest.
        long = True

        # 1. structure TFs, each TRUNCATED to the confirmation instant (NO-LOOKAHEAD).
        intervals = list(getattr(self._v3, "structure_intervals", [_TF_DAY, _TF_60, _TF_30]))
        fetched = self._fetcher.fetch_timeframes(conf.symbol, intervals) or {}
        truncated = {tf: truncate_to_asof(fetched.get(tf) or [], tf, as_of) for tf in intervals}
        last_close = {tf: last_close_ts(truncated.get(tf) or [], tf) for tf in intervals}
        c60 = truncated.get(_TF_60) or []
        daily_asof = truncated.get(_TF_DAY) or []

        # 2. S&R zones from the truncated structure set (shared builder).
        supports: List[Any] = []
        resistances: List[Any] = []
        try:
            nonempty = {tf: cs for tf, cs in truncated.items() if cs}
            if nonempty:
                scored = scored_zones_from_candles(
                    nonempty, knobs=self._knobs, scoring=self._scoring, now=as_of)
                resistances, supports = split_zones(scored)
        except Exception as exc:
            self._safe_log("warning", "pb01 would-be: zone build failed for %s: %s",
                           conf.symbol, exc)
        near_sup = _nearest_support_below(supports, entry)
        near_res = _nearest_resistance_above(resistances, entry)

        # 3. PB-01 SL is DETERMINISTIC (spec §3): SL = min(retest_low, LEVEL) − buffer,
        #    buffer = sl_buffer_atr_mult × ATR30. Computable regardless of a TGT zone.
        sl_edge = min(float(conf.pullback_low), level)
        buffer = (float(self._v3.sl_buffer_atr_mult) * float(atr30)) if atr30 else 0.0
        pb01_sl = round(sl_edge - buffer, 4)
        # G-RR: TGT = the NEAREST 30m/1h resistance ABOVE entry; no zone → G-RR FAILS
        #       (the Kalyan rule — a setup with no safe target is skipped, spec §3).
        tgt_edge = near_res.band_low if near_res is not None else None
        v_rr = gate_rr(
            side="BUY", entry=entry, sl_zone_edge=sl_edge, tgt_zone_edge=tgt_edge,
            atr30=atr30, rr_floor=float(self._v3.rr_floor),
            sl_buffer_atr_mult=float(self._v3.sl_buffer_atr_mult))
        v3_tgt = v_rr.evidence.get("v3_tgt")
        v3_rr = v_rr.evidence.get("v3_rr")

        # 4. HTF (1h) indicators + G-HTF (blocker; missing → pass).
        htf_close = c60[-1].close if c60 else None
        htf_ema = _safe(lambda: ema([c.close for c in c60], int(self._v3.htf_ema_period))) if c60 else None
        n = int(self._v3.htf_swing_pivot_n)
        pivots = (_safe(lambda: find_swing_pivots(c60, left=n, right=n)) or []) if c60 else []
        htf_highs = [p.price for p in pivots if p.kind == "HIGH"]
        htf_lows = [p.price for p in pivots if p.kind == "LOW"]
        v_htf = gate_htf("BUY", htf_close=htf_close, htf_ema=htf_ema,
                         htf_swing_highs=htf_highs, htf_swing_lows=htf_lows)

        # 5. regime + G-EXTREME (blocker; OFF/missing → pass).
        regime_state = self._regime_latest()
        v_ext = gate_extreme(regime_state)

        # 6. playbook must-haves — RECOMPUTE from the frozen confirmation to record the
        #    evidence (body_frac / volume / touched / held). They already passed (that is
        #    why we are here); recording makes the would-be row self-describing.
        v_confirm = gate_confirm(
            candle_open=conf.confirm_open, candle_high=conf.confirm_high,
            candle_low=conf.confirm_low, candle_close=conf.confirm_close,
            candle_volume=conf.confirm_volume, level=level,
            baseline_5m_volume=conf.baseline_5m_volume,
            min_body_frac=float(self._v3.confirm_min_body_frac),
            volume_mult=float(self._v3.confirm_volume_mult))
        v_pullback = gate_pullback(
            level=level, session_low=conf.session_low, lowest_5m_close=conf.lowest_5m_close,
            atr30=atr30, proximity_pct=float(self._v3.pullback_proximity_pct),
            proximity_atr_mult=float(self._v3.pullback_proximity_atr_mult),
            hold_buffer_atr_mult=float(self._v3.hold_buffer_atr_mult))

        gates = {
            GATE_CONFIRM: _g(v_confirm), GATE_PULLBACK: _g(v_pullback),
            GATE_RR: _g(v_rr), GATE_HTF: _g(v_htf), GATE_EXTREME: _g(v_ext),
        }
        # verdict: first failing gate in [EXTREME, HTF, RR, PULLBACK, CONFIRM] order.
        verdict = "WOULD_PASS_GATES"
        for name, v in ((GATE_EXTREME, v_ext), (GATE_HTF, v_htf), (GATE_RR, v_rr),
                        (GATE_PULLBACK, v_pullback), (GATE_CONFIRM, v_confirm)):
            if not v.passed:
                verdict = f"WOULD_REJECT_{name}"
                break

        # 7. score — Playbook 40 (FULL) + Context (regime/sr_target/htf NATIVE; the
        #    step-based sector/momentum are ABSENT — a watchlist candidate never ran the
        #    intraday step screener) + Execution (absent). step_results={} → the
        #    confirmation_strength confluence uses the playbook measure alone (no dilution).
        retest_f = retest_quality_fraction(
            conf.pullback_low, level, atr30, float(self._v3.retest_quality_atr_span))
        confirm_f = confirmation_measure_fraction(
            conf.confirm_open, conf.confirm_high, conf.confirm_low, conf.confirm_close,
            float(self._v3.confirm_min_body_frac))
        touches = _level_touches(daily_asof, level, atr30)
        level_f = level_significance_fraction(touches, int(self._v3.level_touches_cap))
        playbook = {"retest_quality_frac": retest_f, "confirmation_measure_frac": confirm_f,
                    "level_significance_frac": level_f}

        regime_frac, regime_unavail = regime_fraction(self._v3, regime_state, as_of)
        sr_target_frac = sr_target_fraction(self._v3, near_res)
        htf_ema_frac, htf_swing_frac = _htf_alignment_fractions(
            long, htf_close, htf_ema, htf_highs, htf_lows)
        score = compose_score(
            {}, regime_fraction=regime_frac, sr_target_fraction=sr_target_frac,
            htf_ema_fraction=htf_ema_frac, htf_swing_fraction=htf_swing_frac,
            cfg=self._v3, playbook=playbook)

        # 8. would-be placement (PB-01 has no live twin → live_* MIRROR the would-be).
        signal_id = f"pb01:{conf.symbol}:{conf.trading_date}"
        return WouldBeRecord(
            signal_id=signal_id, symbol=conf.symbol, strategy=_STRATEGY, scanner=conf.source,
            side="BUY", intent="INTRADAY",
            signal_ts=_iso(as_of), enrichment_ts=_iso(self._safe_now()),
            last_candle_close_ts=last_close,
            regime=(regime_state.to_dict() if regime_state is not None else None),
            regime_asof_unavailable=regime_unavail,
            nearest_support=(near_sup.to_dict() if near_sup is not None else None),
            nearest_resistance=(near_res.to_dict() if near_res is not None else None),
            sr_confidence_class=CONF_ANCHOR_ONLY,   # unvalidated swing zones — promotion gate P2
            sr_sync_hit=False,                      # no warm cache today; PB-01's universe IS
                                                    # pre-knowable → pre-warm is the future path
            v3_sl=pb01_sl, v3_tgt=v3_tgt, v3_rr=v3_rr,
            live_entry=entry, live_sl=pb01_sl, live_tgt=v3_tgt, live_rr=v3_rr,
            gates=gates, score=score, live_score=0.0, live_tier="",
            v3_verdict=verdict, live_outcome_link=signal_id,
            notes=("PB-01 SHADOW — no live twin (live_* mirror the would-be placement); "
                   "the intraday step screener did not run for a watchlist candidate, so "
                   "sector/momentum/execution score factors are absent (Playbook-40 + "
                   "native Context only). level=%s breakout=%s touches=%d verdict-gates "
                   "recorded." % (round(level, 4), conf.breakout_date, touches)))

    # ── regime snapshot (.latest; guarded) ────────────────────────────────────
    def _regime_latest(self):
        if self._regime_runner is None:
            return None
        try:
            return self._regime_runner.latest
        except Exception:
            return None

    # ── persistence (best-effort; never raises — an error is NEVER an order) ──
    def _persist(self, rec: WouldBeRecord) -> None:
        path = getattr(self._cfg, "would_be_log_path", "data_store/v3/pb01_would_be.jsonl")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_json_dict()) + "\n")
            self._safe_log(
                "info", "pb01 would-be[%s]: verdict=%s entry=%s sl=%s tgt=%s rr=%s score=%.1f",
                rec.symbol, rec.v3_verdict, rec.live_entry, rec.v3_sl, rec.v3_tgt, rec.v3_rr,
                rec.score.get("total", rec.score.get("partial_total", 0.0)))
        except Exception as exc:
            self._safe_log("error", "pb01 would-be persist failed (ignored): %s", exc)

    def _safe_now(self):
        try:
            return self._now()
        except Exception:
            return None

    def _safe_log(self, level: str, msg: str, *args) -> None:
        if self._log is None:
            return
        try:
            getattr(self._log, level)(msg, *args)
        except Exception:
            pass


# ── pure helpers ──────────────────────────────────────────────────────────────

def _g(v) -> dict:
    return {"passed": bool(v.passed), "reason": v.reason, "evidence": v.evidence}


def _level_touches(daily_asof, level: float, atr30) -> int:
    """A touch proxy for level_significance: prior daily sessions whose HIGH came within
    a small band (max 0.5%×LEVEL, 0.5×ATR30) of LEVEL — how often the level was tested."""
    if not daily_asof or not level:
        return 0
    band = max(0.005 * float(level), (0.5 * float(atr30)) if atr30 else 0.0)
    if band <= 0:
        band = 0.005 * float(level)
    return sum(1 for c in daily_asof if abs(float(c.high) - float(level)) <= band)


def _iso(dt) -> str:
    try:
        return dt.isoformat()
    except Exception:
        return ""
