"""
tests/unit/test_hard_gate.py — V3 03.03/03.04 Hard-Gate + scorer re-scale (Step 4b).

Covers: HardGate rules + missing-data policy; the shared re-scale helpers; and the
screener in each v3_hardgate_mode (off = byte-identical, enforce = gate-first + 8-step
+ v3 thresholds, shadow = live follows OLD). Nothing here flips a live system.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from unittest.mock import MagicMock

import yaml

from core.config_loader import ScoringConfig
from core.time_authority import now_ist
from screening.hard_gate import (
    GATE_AT_CIRCUIT,
    GATE_CIRCUIT_PROXIMITY,
    GATE_STALE,
    HardGate,
    circuit_proximity_reason,
    rescale_min_score,
    rescaled_total,
    tier_for,
)
from screening.quality_scorer import QualityScorer
from screening.secondary_screener import SecondaryScreener
from screening.step_executor import StepExecutorResult


def _log():
    lg = logging.getLogger("test_hard_gate")
    lg.addHandler(logging.NullHandler())
    return lg


# ── HardGate rules ────────────────────────────────────────────────────────────

def _gate():
    return HardGate(now_fn=now_ist, logger=_log(), freshness_max_sec=60.0)


def test_gate_at_circuit_rejects():
    v = _gate().evaluate(trigger_price=100.0, triggered_at=now_ist(), direction="LONG",
                         market_data={"circuit_state": "upper_circuit"})
    assert not v.passed and v.reason == GATE_AT_CIRCUIT


def test_gate_circuit_proximity_rejects():
    md = {"upper_circuit": 190.82, "lower_circuit": 127.22}
    v = _gate().evaluate(trigger_price=189.9, triggered_at=now_ist(), direction="LONG", market_data=md)
    assert not v.passed and v.reason == GATE_CIRCUIT_PROXIMITY


def test_gate_stale_rejects_beyond_60s():
    old = now_ist() - timedelta(seconds=120)
    v = _gate().evaluate(trigger_price=100.0, triggered_at=old, direction="LONG",
                         market_data={"upper_circuit": 200.0, "lower_circuit": 50.0})
    assert not v.passed and v.reason == GATE_STALE
    assert v.evidence["age_sec"] > 60


def test_gate_fresh_and_feasible_passes():
    md = {"upper_circuit": 120.0, "lower_circuit": 80.0}
    v = _gate().evaluate(trigger_price=100.0, triggered_at=now_ist(), direction="LONG", market_data=md)
    assert v.passed and v.reason is None


def test_gate_missing_triggered_at_admits():
    md = {"upper_circuit": 120.0, "lower_circuit": 80.0}
    v = _gate().evaluate(trigger_price=100.0, triggered_at=None, direction="LONG", market_data=md)
    assert v.passed


def test_gate_liquidity_is_noop():
    # a bad spread does NOT gate (spread_check stays in the 8-step score, A8)
    md = {"upper_circuit": 120.0, "lower_circuit": 80.0, "bid": 90.0, "ask": 110.0}
    v = _gate().evaluate(trigger_price=100.0, triggered_at=now_ist(), direction="LONG", market_data=md)
    assert v.passed


def test_gate_proximity_matches_relocated_function():
    md = {"upper_circuit": 190.82, "lower_circuit": 127.22}
    # the gate and the standalone relocated function agree (single source)
    assert circuit_proximity_reason(189.9, "LONG", md) is not None


# ── shared re-scale helpers ───────────────────────────────────────────────────

_WEIGHTS = {"volume_surge": 15, "vwap_position": 10, "atr_filter": 10, "rsi_range": 10,
            "price_action": 15, "sector_strength": 10, "time_of_day": 5, "spread_check": 5,
            "circuit_check": 10, "signal_age": 10}
_GATE_STEPS = {"circuit_check", "signal_age"}


def test_rescaled_total_drops_gate_steps():
    # all 8 kept steps full → 80/80×100 = 100 (circuit/age excluded)
    sr = {k: 1.0 for k in _WEIGHTS}
    assert rescaled_total(sr, _WEIGHTS, _GATE_STEPS) == 100


def test_rescaled_total_proportional_on_kept():
    sr = {k: 1.0 for k in _WEIGHTS}
    sr["price_action"] = 0.0            # drop 15 of the 80 kept → 65/80×100 = 81.25 → 81
    assert rescaled_total(sr, _WEIGHTS, _GATE_STEPS) == 81


def test_rescaled_total_missing_excluded_from_denominator():
    # only volume_surge(15) + vwap(10) present, both full → 25/25×100 = 100
    sr = {"volume_surge": 1.0, "vwap_position": 1.0}
    assert rescaled_total(sr, _WEIGHTS, _GATE_STEPS) == 100


def test_tier_for():
    assert tier_for(80, 75, 56) == "HIGH"
    assert tier_for(60, 75, 56) == "MEDIUM"
    assert tier_for(40, 75, 56) == "LOW"


def test_rescale_min_score():
    assert rescale_min_score(0) == 0            # use-global stays global (all 15 strategies)
    assert rescale_min_score(60) == 50          # 1.25*60-25 = 50
    assert rescale_min_score(80) == 75


# ── screener modes ────────────────────────────────────────────────────────────

def _scoring_config(mode="off"):
    raw = yaml.safe_load(open("config/scoring_weights.yaml"))
    raw["v3_hardgate_mode"] = mode
    return ScoringConfig(**raw)


def _canned_executor(step_results):
    ex = MagicMock()
    ex.run_all.return_value = StepExecutorResult(
        step_results=dict(step_results),
        step_statuses={k: ("PASSED" if v > 0 else "REJECTED") for k, v in step_results.items()},
        rejected_at=None, error_steps=[], latencies_ms={k: 0.1 for k in step_results},
    )
    return ex


def _strategy():
    s = MagicMock()
    s.min_volume_surge = 1.5
    s.min_adr_pct = 0.5
    s.max_spread_pct = 0.1
    s.min_score = 0
    return s


def _screener(mode, step_results, *, hard_gate=None):
    cfg = _scoring_config(mode)
    return SecondaryScreener(
        step_executor=_canned_executor(step_results),
        quality_scorer=QualityScorer(cfg, _log()),
        state_store=MagicMock(), quote_fn=MagicMock(), logger=_log(),
        hard_gate=hard_gate or HardGate(now_fn=now_ist, logger=_log(), freshness_max_sec=60.0),
        scoring_config=cfg if mode != "off" else None,
    )


_FULL = {k: 1.0 for k in _WEIGHTS}       # all 10 steps full
_FEASIBLE_MD = {"upper_circuit": 120.0, "lower_circuit": 80.0, "ltp": 100.0,
                "circuit_state": ""}


def _screen(s, md=None, trigger=100.0, triggered_at=None, strategy=None):
    return s.screen(
        signal_id="sig", symbol="ACME", scanner_name="t", trigger_price=trigger,
        triggered_at=triggered_at if triggered_at is not None else now_ist(),
        direction="LONG", intent="INTRADAY", strategy=strategy or _strategy(),
        market_data=md if md is not None else dict(_FEASIBLE_MD),
    )


def test_off_mode_byte_identical_to_no_v3():
    # a screener with v3 params (mode off) == a screener built the OLD way
    old = SecondaryScreener(_canned_executor(_FULL), QualityScorer(_scoring_config("off"), _log()),
                            MagicMock(), MagicMock(), _log())
    new = _screener("off", _FULL)
    r_old = _screen(old)
    r_new = _screen(new)
    assert (r_old.passed, r_old.status, r_old.score, r_old.tier) == \
           (r_new.passed, r_new.status, r_new.score, r_new.tier)
    assert r_new.status == "PASSED" and r_new.score == 100    # OFF: 10-step full


def test_enforce_pass_uses_8step_and_v3_tier():
    r = _screen(_screener("enforce", _FULL))
    assert r.passed and r.status == "PASSED"
    assert r.score == 100 and r.tier == "HIGH"                # 8-step full = 100


def test_enforce_gate_rejects_at_circuit_before_scoring():
    md = dict(_FEASIBLE_MD); md["circuit_state"] = "upper_circuit"
    r = _screen(_screener("enforce", _FULL), md=md)
    assert not r.passed and r.status == f"REJECTED_{GATE_AT_CIRCUIT}"


def test_enforce_gate_rejects_stale():
    r = _screen(_screener("enforce", _FULL), triggered_at=now_ist() - timedelta(seconds=120))
    assert not r.passed and r.status == f"REJECTED_{GATE_STALE}"


def test_enforce_score_reject_below_v3_min_pass():
    # kept steps low: only spread_check(5) full → 5/80×100 = 6 < v3_min_pass 50
    low = {k: 0.0 for k in _WEIGHTS}
    low["spread_check"] = 1.0
    r = _screen(_screener("enforce", low))
    assert not r.passed and r.status.startswith("REJECTED_SCORE_")


def test_shadow_mode_live_follows_old():
    # shadow returns the OLD decision (10-step, old thresholds); NEW is only logged
    r = _screen(_screener("shadow", _FULL))
    assert r.passed and r.status == "PASSED"
    assert r.score == 100                                      # OLD 10-step score (live)
