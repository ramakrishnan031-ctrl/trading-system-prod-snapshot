"""
tests/unit/test_sr_detector_units.py — SNR-DETECTOR-V1 pure units.

Covers (spec K): swing pivots, zone clustering (band + touch-count), confluence
bucketing (>=2 methods on >=2 TFs -> HIGH; lone weak pivot -> LOW; evidence
recorded), flag logic (entry inside a HIGH resistance band -> BUYING_INTO_
RESISTANCE; clean breakout with room -> no flag), and the default-off config.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from sr_detector.confluence import ConfluenceContext, ScoringParams, score_zones
from sr_detector.flags import BreakoutContext, FlagParams, compute_flags_and_retest
from sr_detector.models import Candidate, Candle, ScoredZone, Zone
from sr_detector.pivots import find_swing_pivots
from sr_detector.zones import cluster_zones, volume_profile_nodes

_T0 = datetime(2026, 6, 1, 9, 15)


def _c(i, high, low, close=None, vol=1000):
    return Candle(ts=_T0 + timedelta(minutes=i), open=close or close or high,
                  high=high, low=low, close=close if close is not None else (high + low) / 2,
                  volume=vol)


# ── pivots ──────────────────────────────────────────────────────────────────

def test_swing_high_and_low_pivots():
    highs = [10, 11, 13, 11, 10, 11, 15, 11, 10]
    lows = [5, 4, 6, 4, 3, 4, 6, 4, 5]
    candles = [_c(i, highs[i], lows[i]) for i in range(len(highs))]
    pivots = find_swing_pivots(candles, left=2, right=2)
    hi = {round(p.price) for p in pivots if p.kind == "HIGH"}
    lo = {round(p.price) for p in pivots if p.kind == "LOW"}
    assert 13 in hi and 15 in hi          # the two local-max highs
    assert 3 in lo                         # the local-min low at index 4


def test_flat_plateau_does_not_mark_every_bar():
    candles = [_c(i, 100, 99) for i in range(9)]   # perfectly flat
    assert find_swing_pivots(candles, left=2, right=2) == []


def test_too_few_bars_no_pivots():
    candles = [_c(i, 100 + i, 90) for i in range(3)]
    assert find_swing_pivots(candles, left=2, right=2) == []


# ── zones ───────────────────────────────────────────────────────────────────

def test_nearby_pivots_cluster_into_one_band_with_touch_count():
    from sr_detector.models import Pivot
    pivots = [
        Pivot(100.0, _T0, "HIGH", 0),
        Pivot(100.2, _T0, "HIGH", 1),
        Pivot(100.4, _T0, "HIGH", 2),
        Pivot(105.0, _T0, "HIGH", 3),    # far -> separate zone
    ]
    zones = cluster_zones(pivots, cluster_pct=0.5, timeframe="day")
    res = sorted([z for z in zones if z.kind == "RESISTANCE"], key=lambda z: z.band_low)
    assert len(res) == 2
    assert res[0].band_low == 100.0 and res[0].band_high == 100.4 and res[0].touches == 3
    assert res[1].touches == 1


def test_volume_profile_finds_high_volume_node():
    candles = [_c(i, 100 + i % 3, 99 + i % 3, vol=(9000 if i % 3 == 1 else 100))
               for i in range(30)]
    nodes = volume_profile_nodes(candles, bins=6, node_frac=0.7)
    assert nodes, "expected at least one volume node"


# ── confluence ──────────────────────────────────────────────────────────────

def _params():
    return ScoringParams(t_high=5.0, t_med=3.0, touch_cap=4, merge_pct=0.4)


def test_two_methods_two_timeframes_is_high_with_evidence():
    zbt = {
        "day": [Zone(520.0, 520.0, "RESISTANCE", touches=5, timeframe="day")],
        "60minute": [Zone(519.8, 520.2, "RESISTANCE", touches=3, timeframe="60minute")],
    }
    scored = score_zones(zbt, ConfluenceContext(), _params())
    res = [z for z in scored if z.kind == "RESISTANCE"]
    assert len(res) == 1
    z = res[0]
    assert z.confidence == "HIGH"
    methods = {m for m, _ in z.evidence}
    assert "swing_pivots" in methods and "multi_tf" in methods   # evidence recorded
    assert set(z.timeframes) == {"day", "60minute"}


def test_lone_weak_pivot_is_low():
    zbt = {"day": [Zone(100.0, 100.0, "RESISTANCE", touches=1, timeframe="day")]}
    scored = score_zones(zbt, ConfluenceContext(), _params())
    assert scored[0].confidence == "LOW"


def test_single_tf_multi_touch_is_medium_not_high():
    zbt = {"day": [Zone(100.0, 100.2, "RESISTANCE", touches=4, timeframe="day")]}
    scored = score_zones(zbt, ConfluenceContext(), _params())
    # swing 4 + multi 1 = 5 >= t_med, but only 1 TF -> cannot be HIGH
    assert scored[0].confidence == "MEDIUM"


def test_prior_day_level_adds_a_vote():
    zbt = {"day": [Zone(100.0, 100.5, "RESISTANCE", touches=2, timeframe="day")]}
    ctx = ConfluenceContext(prior_day_levels=(100.2,))
    scored = score_zones(zbt, ctx, _params())
    assert any(m == "prior_day_level" for m, _ in scored[0].evidence)


# ── flags ───────────────────────────────────────────────────────────────────

def _cand(entry, direction="LONG"):
    return Candidate(signal_id="S", symbol="X", strategy="st", direction=direction,
                     intended_entry=entry, sl_price=entry * 0.98, tgt_price=entry * 1.03,
                     qty=1, intent="INTRADAY", mode="paper", ts=_T0, score=60)


def _high_res(low, high):
    return ScoredZone(low, high, "RESISTANCE", score=6.0, confidence="HIGH",
                      touches=5, timeframes=("day", "60minute"))


def test_long_entry_into_high_resistance_flags_and_proposes_retest():
    z = _high_res(520.0, 521.0)
    fr = compute_flags_and_retest(_cand(519.5), [z], None, FlagParams())
    assert "BUYING_INTO_RESISTANCE" in fr.flags
    assert fr.retest.would_wait and fr.retest.entry == 521.0 and fr.retest.sl < 520.0


def test_clean_breakout_with_room_no_flag():
    # entry well above a broken resistance, far from the next zone, with a strong
    # high-volume breakout candle -> no structural flag.
    z = _high_res(500.0, 501.0)
    far = ScoredZone(600.0, 601.0, "RESISTANCE", 6.0, "HIGH", 5, ("day", "60minute"))
    bo = BreakoutContext(last_close=550.0, last_high=552.0, last_low=548.0,
                         last_volume=5000.0, avg_volume=1000.0)
    fr = compute_flags_and_retest(_cand(550.0), [z, far], bo, FlagParams())
    assert fr.flags == ()


def test_low_confidence_structure_flag():
    z = ScoredZone(520.0, 521.0, "RESISTANCE", 2.0, "LOW", 1, ("day",))
    fr = compute_flags_and_retest(_cand(519.5), [z], None, FlagParams())
    assert "LOW_CONFIDENCE_STRUCTURE" in fr.flags
    assert "BUYING_INTO_RESISTANCE" not in fr.flags


def test_no_clear_structure_when_no_zones():
    fr = compute_flags_and_retest(_cand(100.0), [], None, FlagParams())
    assert fr.flags == ("NO_CLEAR_STRUCTURE",)


def test_no_volume_confirmation_on_low_volume_breakout():
    z = _high_res(500.0, 501.0)
    bo = BreakoutContext(last_close=505.0, last_high=506.0, last_low=499.0,
                         last_volume=800.0, avg_volume=1000.0)   # weak volume
    fr = compute_flags_and_retest(_cand(505.0), [z], bo, FlagParams())
    assert "NO_VOLUME_CONFIRMATION" in fr.flags


def test_weak_breakout_targets_broken_zone_even_with_higher_zone():
    # entry just cleared a lower zone (no follow-through, weak volume) while a
    # higher zone exists above — the breakout flags must target the BROKEN zone.
    broken = ScoredZone(500.0, 502.0, "RESISTANCE", 6.0, "HIGH", 5, ("day", "60minute"))
    higher = ScoredZone(600.0, 601.0, "RESISTANCE", 6.0, "HIGH", 5, ("day", "60minute"))
    bo = BreakoutContext(last_close=502.3, last_high=503.0, last_low=499.0,
                         last_volume=700.0, avg_volume=1000.0)
    fr = compute_flags_and_retest(_cand(502.3), [broken, higher], bo, FlagParams())
    assert "WEAK_BREAKOUT" in fr.flags
    assert "NO_VOLUME_CONFIRMATION" in fr.flags
    assert "BUYING_INTO_RESISTANCE" not in fr.flags


def test_short_selling_into_support_mirror():
    z = ScoredZone(480.0, 481.0, "SUPPORT", 6.0, "HIGH", 5, ("day", "60minute"))
    fr = compute_flags_and_retest(_cand(481.5, direction="SHORT"), [z], None, FlagParams())
    assert "SELLING_INTO_SUPPORT" in fr.flags
    assert fr.retest.would_wait


# ── config (default-off) ──────────────────────────────────────────────────────

def test_config_default_off_and_validates_timeframes():
    from core.config_loader import SRDetectorConfig
    cfg = SRDetectorConfig()
    assert cfg.enabled is False
    assert cfg.timeframes == ["day", "60minute", "30minute"]
    with pytest.raises(Exception):
        SRDetectorConfig(timeframes=["weekly"])      # unsupported interval rejected
    with pytest.raises(Exception):
        SRDetectorConfig(timeframes=[])               # empty rejected
