"""
tests/unit/test_v3_pb01_gates_score.py — V3 Step 10b · PB-01 playbook gates + score.

Covers the SPEC §3 must-have gates (G-CONFIRM / G-PULLBACK) and the §4 Playbook-40
score layer (retest_quality / confirmation_strength[confluence] / level_significance),
plus the proof that the 10a compose_score path (playbook=None) is BYTE-IDENTICAL.
"""
from __future__ import annotations

from core.config_loader import V3ChainConfig
from screening.hard_gate import (
    GATE_CONFIRM, GATE_PULLBACK, gate_confirm, gate_pullback,
)
from v3_chain.score import (
    compose_score, confirmation_measure_fraction, level_significance_fraction,
    retest_quality_fraction,
)

CFG = V3ChainConfig()   # defaults = the SPEC seeds


# ── config loads with the new 10b knobs ──────────────────────────────────────

def test_config_has_10b_seed_knobs():
    assert CFG.confirm_min_body_frac == 0.50
    assert CFG.confirm_volume_mult == 1.20
    assert CFG.baseline_candles_per_session == 75
    assert CFG.pullback_proximity_pct == 0.005
    assert CFG.hold_buffer_atr_mult == 0.20
    assert CFG.retest_quality_atr_span == 0.75
    assert CFG.level_touches_cap == 5


# ── G-CONFIRM (spec §3) ───────────────────────────────────────────────────────

def _confirm(**kw):
    base = dict(candle_open=100.0, candle_high=103.0, candle_low=99.5, candle_close=102.5,
                candle_volume=2000.0, level=101.0, baseline_5m_volume=1000.0,
                min_body_frac=0.50, volume_mult=1.20)
    base.update(kw)
    return gate_confirm(**base)


def test_gconfirm_passes_strong_close_above_level():
    v = _confirm()
    assert v.passed and v.reason is None
    assert v.evidence["body_frac"] >= 0.50


def test_gconfirm_fails_close_not_above_level():
    v = _confirm(candle_close=100.9)   # <= level 101
    assert not v.passed and v.reason == GATE_CONFIRM
    assert "not above LEVEL" in v.evidence["detail"]


def test_gconfirm_fails_doji_body():
    # open≈close → tiny body → body_frac < 0.50, even though it closes above LEVEL.
    v = _confirm(candle_open=102.4, candle_close=102.5, candle_high=104.0, candle_low=99.0)
    assert not v.passed and v.reason == GATE_CONFIRM
    assert "body_frac" in v.evidence["detail"]


def test_gconfirm_fails_low_volume():
    v = _confirm(candle_volume=1000.0)   # 1000 < 1.20 × 1000
    assert not v.passed and v.reason == GATE_CONFIRM
    assert "volume" in v.evidence["detail"]


def test_gconfirm_fails_missing_data():
    assert not _confirm(baseline_5m_volume=None).passed
    assert not _confirm(level=None).passed


def test_gconfirm_fails_degenerate_candle():
    v = _confirm(candle_high=100.0, candle_low=100.0)   # high == low
    assert not v.passed and "degenerate" in v.evidence["detail"]


# ── G-PULLBACK (spec §3) ──────────────────────────────────────────────────────

def _pullback(**kw):
    # LEVEL 100, ATR30 2.0 → proximity = max(0.5%×100=0.5, 0.5×2=1.0) = 1.0;
    # hold_floor = 100 - 0.20×2 = 99.6.
    base = dict(level=100.0, session_low=100.5, lowest_5m_close=100.2, atr30=2.0,
                proximity_pct=0.005, proximity_atr_mult=0.50, hold_buffer_atr_mult=0.20)
    base.update(kw)
    return gate_pullback(**base)


def test_gpullback_passes_touched_and_held():
    v = _pullback()
    assert v.passed and v.reason is None
    assert v.evidence["touched"] and v.evidence["held"]


def test_gpullback_fails_no_touch():
    # session_low far above LEVEL+proximity (101.0) → never came back.
    v = _pullback(session_low=103.0)
    assert not v.passed and v.reason == GATE_PULLBACK
    assert "no touch" in v.evidence["detail"]


def test_gpullback_fails_not_held():
    # a 5-min close dipped below hold_floor 99.6 → the flip failed.
    v = _pullback(lowest_5m_close=99.4)
    assert not v.passed and v.reason == GATE_PULLBACK
    assert "not held" in v.evidence["detail"]


def test_gpullback_fails_missing_atr():
    assert not _pullback(atr30=None).passed


# ── Playbook factor helpers (spec §4) ─────────────────────────────────────────

def test_retest_quality_peaks_at_precise_retest():
    # dist = 0 → frac = 1.0 (pullback_low exactly at LEVEL)
    assert retest_quality_fraction(100.0, 100.0, 2.0, 0.75) == 1.0
    # a deep plunge-through decays; |dist| = span → 0
    deep = retest_quality_fraction(100.0 - 0.75 * 2.0, 100.0, 2.0, 0.75)
    assert abs(deep) < 1e-9
    # missing → 0
    assert retest_quality_fraction(None, 100.0, 2.0, 0.75) == 0.0
    assert retest_quality_fraction(100.0, 100.0, 0.0, 0.75) == 0.0


def test_confirmation_measure_rewards_excess_over_gate():
    # A candle EXACTLY at the gate floor (body_frac == min, close at high) → body_norm 0,
    # close_position 1 → 0.5×0 + 0.5×1 = 0.5.
    # open=100, high=102, low=100, close=101 → body_frac=0.5, close_position=(101-100)/2=0.5
    m = confirmation_measure_fraction(100.0, 102.0, 100.0, 101.0, 0.50)
    assert abs(m - (0.5 * 0.0 + 0.5 * 0.5)) < 1e-9
    # a strong candle (full body, close at high) scores near 1
    strong = confirmation_measure_fraction(100.0, 102.0, 100.0, 102.0, 0.50)
    assert strong > 0.9
    assert confirmation_measure_fraction(1, 1, 1, 1, 0.5) == 0.0  # degenerate → 0


def test_level_significance_caps():
    assert level_significance_fraction(2, 5) == 0.4
    assert level_significance_fraction(10, 5) == 1.0   # capped
    assert level_significance_fraction(None, 5) == 0.0


# ── compose_score: 10a byte-identity + 10b Playbook layer ─────────────────────

STEPS = {"price_action": 0.8, "volume_surge": 0.6, "sector_strength": 0.5,
         "rsi_range": 0.7, "vwap_position": 0.4, "atr_filter": 0.3,
         "time_of_day": 1.0, "spread_check": 0.9}


def _compose(playbook=None):
    return compose_score(
        STEPS, regime_fraction=0.5, sr_target_fraction=0.6,
        htf_ema_fraction=1.0, htf_swing_fraction=1.0, cfg=CFG, playbook=playbook)


def test_10a_path_byte_identical_when_no_playbook():
    s = _compose(playbook=None)
    assert s["playbook"] is None
    assert "partial_total" in s
    assert "total" not in s               # 10a never emits a full total
    assert s["partial_total"] == round(s["context"]["total"] + s["execution"]["total"], 4)


def test_playbook_layer_present_and_total_is_full():
    pb = {"retest_quality_frac": 1.0, "confirmation_measure_frac": 1.0,
          "level_significance_frac": 1.0}
    s = _compose(playbook=pb)
    assert s["playbook"] is not None
    # retest 1.0×15=15 ; level 1.0×10=10 ; confirmation = combine([1.0, price_action 0.8]) ×15
    assert abs(s["playbook"]["retest_quality"] - 15.0) < 1e-9
    assert abs(s["playbook"]["level_significance"] - 10.0) < 1e-9
    assert s["total"] == round(s["playbook"]["total"] + s["context"]["total"]
                               + s["execution"]["total"], 4)
    # a valid full score never exceeds 100
    assert s["total"] <= 100.0 + 1e-9


def test_confirmation_strength_is_confluence_not_double_count():
    # confirmation_strength must be combine(measure, price_action)×15 — NOT measure×15
    # PLUS a separate price_action term. Mean of {1.0, 0.8} = 0.9 → 0.9×15 = 13.5.
    pb = {"retest_quality_frac": 0.0, "confirmation_measure_frac": 1.0,
          "level_significance_frac": 0.0}
    s = _compose(playbook=pb)
    assert abs(s["playbook"]["confirmation_strength"] - 0.9 * 15.0) < 1e-9


def test_confirmation_strength_absent_price_action_not_diluted():
    # A PB-01 watchlist candidate never ran the intraday step screener → price_action is
    # ABSENT (missing key). It must NOT dilute the confluence to measure/2; the group
    # yields the playbook measure alone. measure 1.0 → 1.0×15 = 15.0 (not 7.5).
    pb = {"retest_quality_frac": 0.0, "confirmation_measure_frac": 1.0,
          "level_significance_frac": 0.0}
    s = compose_score({}, regime_fraction=0.0, sr_target_fraction=0.0,
                      htf_ema_fraction=None, htf_swing_fraction=None, cfg=CFG, playbook=pb)
    assert abs(s["playbook"]["confirmation_strength"] - 15.0) < 1e-9
    # a present-but-zero price_action IS a real member (0.0) → combine([1.0, 0.0]) = 0.5.
    s2 = compose_score({"price_action": 0.0}, regime_fraction=0.0, sr_target_fraction=0.0,
                       htf_ema_fraction=None, htf_swing_fraction=None, cfg=CFG, playbook=pb)
    assert abs(s2["playbook"]["confirmation_strength"] - 0.5 * 15.0) < 1e-9


if __name__ == "__main__":
    import sys
    mod = sys.modules[__name__]
    for name in sorted(dir(mod)):
        if name.startswith("test_"):
            getattr(mod, name)()
            print(f"  OK {name}")
    print("\nPB-01 gates + score: all checks passed.")
