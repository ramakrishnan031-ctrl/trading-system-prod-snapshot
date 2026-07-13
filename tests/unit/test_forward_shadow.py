# tests/unit/test_forward_shadow.py — forward-shadow research recorder core (pure).
# RECORDS ONLY; nothing wired to the live path. Mirrors step_executor's 4 M-S4 steps +
# the §3/Q2.8 walker so the forward out-of-sample record is identical to the backfill.
from __future__ import annotations

from v3_chain.forward_shadow import (
    ms4_score,
    ms4_step_values,
    score_band,
    simulate_true_path,
)

_W = {'volume_surge':15,'vwap_position':10,'atr_filter':10,'rsi_range':10,'price_action':15,
      'sector_strength':10,'time_of_day':5,'spread_check':5,'circuit_check':10,'signal_age':10}


def test_ms4_step_values_volume_surge():
    assert ms4_step_values(volume=200, ltp=100, direction="LONG",
                           avg_volume_20d=100, atr14=None, rsi14=None, sector=None)["volume_surge"] == 1.0
    assert ms4_step_values(volume=120, ltp=100, direction="LONG",
                           avg_volume_20d=100, atr14=None, rsi14=None, sector=None)["volume_surge"] == 0.0
    # fail-safe: no avg_volume -> 0.0 (today's behaviour), never fabricated
    assert ms4_step_values(volume=999, ltp=100, direction="LONG",
                           avg_volume_20d=None, atr14=None, rsi14=None, sector=None)["volume_surge"] == 0.0


def test_ms4_step_values_atr_and_rsi_and_sector():
    v = ms4_step_values(volume=0, ltp=100, direction="LONG",
                        avg_volume_20d=None, atr14=1.0, rsi14=50.0, sector="IT")
    assert v["atr_filter"] == 1.0          # 1/100*100 = 1.0% >= 0.5
    assert v["rsi_range"] == 1.0           # LONG 40<=50<=80
    assert v["sector_strength"] == 1.0     # sector present
    v2 = ms4_step_values(volume=0, ltp=100, direction="LONG",
                         avg_volume_20d=None, atr14=0.4, rsi14=90.0, sector=None)
    assert v2["atr_filter"] == 0.0         # 0.4% < 0.5
    assert v2["rsi_range"] == 0.0          # LONG rsi 90 out of band
    assert v2["sector_strength"] == 0.5    # no sector -> half
    # rsi missing -> neutral 0.5; SHORT band 20-60
    assert ms4_step_values(volume=0, ltp=100, direction="LONG",
                           avg_volume_20d=None, atr14=None, rsi14=None, sector=None)["rsi_range"] == 0.5
    assert ms4_step_values(volume=0, ltp=100, direction="SHORT",
                           avg_volume_20d=None, atr14=None, rsi14=30.0, sector=None)["rsi_range"] == 1.0


def test_ms4_score_replaces_only_the_four_dead_steps():
    # a realistic OLD row: dead steps at 0/0.5, alive steps as scored -> old score
    old = {'volume_surge':0.0,'vwap_position':1.0,'atr_filter':0.0,'rsi_range':0.5,'price_action':1.0,
           'sector_strength':0.5,'time_of_day':0.5,'spread_check':1.0,'circuit_check':1.0,'signal_age':1.0}
    old_score = round(sum(_W[k]*v for k, v in old.items()))  # = 62 (SATIN-like)
    assert old_score == 62
    ms4 = ms4_step_values(volume=1000, ltp=100, direction="LONG",
                          avg_volume_20d=100, atr14=2.0, rsi14=55.0, sector="IT")
    # all four now fire (vs=1,af=1,rr=1,ss=1): raw Σw·v goes 62.5 -> 97.5. Banker's rounding
    # (matching the live scorer: SATIN 62.5 -> 62) takes 62.5->62 and 97.5->98.
    assert ms4_score(old, ms4, _W) == 98


def test_score_band():
    assert score_band(20) == "0-34"
    assert score_band(34) == "0-34"
    assert score_band(35) == "35-39"
    assert score_band(44) == "40-44"
    assert score_band(50) == "50-54"
    assert score_band(59) == "55-59"
    assert score_band(60) == "60-65"
    assert score_band(65) == "60-65"


def test_simulate_true_path():
    # LONG hits TGT (+1.5R): high reaches entry*(1+1.5%)
    assert simulate_true_path(100.0, "LONG", [(102.0, 99.5, 101.5)]) == 1.5
    # LONG hits SL (-1R): low <= entry*(1-1%); stop-first (adverse before favourable)
    assert simulate_true_path(100.0, "LONG", [(102.0, 98.9, 99.0)]) == -1.0
    # neither -> squareoff at last close (R in stop units)
    assert simulate_true_path(100.0, "LONG", [(100.4, 99.5, 100.5)]) == 0.5  # (100.5-100)/1.0
    # SHORT symmetric
    assert simulate_true_path(100.0, "SHORT", [(100.4, 98.0, 98.5)]) == 1.5
    # guards
    assert simulate_true_path(0.0, "LONG", [(1, 1, 1)]) is None
    assert simulate_true_path(100.0, "LONG", []) is None
