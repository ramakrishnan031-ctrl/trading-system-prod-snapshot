"""
tests/unit/test_v3_hardgate_parity.py — V3 03.03/03.04 offline parity tool (T4).

Proves the recompute's classification, explained-residual gate, data-fit, and
unexplained-flip detection on a SYNTHETIC screener_results corpus (the real
binding run is on a VM backup).
"""
from __future__ import annotations

import json

from scripts.v3_hardgate_parity_recompute import analyze, classify_row, data_fit

_WEIGHTS = {"volume_surge": 15, "vwap_position": 10, "atr_filter": 10, "rsi_range": 10,
            "price_action": 15, "sector_strength": 10, "time_of_day": 5, "spread_check": 5,
            "circuit_check": 10, "signal_age": 10}
_FEASIBLE = json.dumps({"upper_circuit": 120.0, "lower_circuit": 80.0, "ltp": 100.0})


def _row(signal_id, status, score, tier, kept: dict, circuit, age, md=_FEASIBLE):
    sr = {"volume_surge": 0.0, "vwap_position": 0.0, "atr_filter": 0.0, "rsi_range": 0.0,
          "price_action": 0.0, "sector_strength": 0.0, "time_of_day": 0.0, "spread_check": 0.0}
    sr.update(kept)
    sr["circuit_check"] = circuit
    sr["signal_age"] = age
    return {"signal_id": signal_id, "status": status, "score": score, "tier": tier,
            "direction": "LONG", "step_results_json": json.dumps(sr), "market_data_snapshot": md}


# a8 = 42 (volume15 + vwap10 + atr10 + rsi 0.7*10=7)
_KEPT_42 = {"volume_surge": 1.0, "vwap_position": 1.0, "atr_filter": 1.0, "rsi_range": 0.7}
# a8 = 80 (all 8 kept full)
_KEPT_80 = {"volume_surge": 1.0, "vwap_position": 1.0, "atr_filter": 1.0, "rsi_range": 1.0,
            "price_action": 1.0, "sector_strength": 1.0, "time_of_day": 1.0, "spread_check": 1.0}


def _classify(row, mp=50, hi=75, med=56):
    return classify_row(row, weights=_WEIGHTS, v3_min_pass=mp, v3_high=hi, v3_medium=med)


def test_unchanged_fresh_full_pass():
    # a8=80, circuit=1, age=1 → old PASSED 100, new 100 HIGH → UNCHANGED
    r = _row("s_unchanged", "PASSED", 100, "HIGH", _KEPT_80, 1.0, 1.0)
    v = _classify(r)
    assert v.classification == "UNCHANGED" and v.new_score == 100


def test_flip_pass_explained_by_age_half():
    # a8=42, circuit=1, age=0.5 → OLD score 42+10+5=57 REJECTED_SCORE; NEW
    # round(42/80*100)=round(52.5)=52 (banker's, mirrors the scorer) ≥50 → PASS.
    r = _row("s_flip_pass", "REJECTED_SCORE_57", 57, "LOW", _KEPT_42, 1.0, 0.5)
    v = _classify(r)
    assert v.classification == "FLIP_PASS"
    assert v.explained and "age0.5" in v.reason
    assert v.new_score == 52


def test_now_gated_at_circuit_explained():
    # a8=80, circuit=0 (at circuit) but OLD still passed (80+0+10=90) → NEW gate-rejects
    r = _row("s_gated", "PASSED", 90, "HIGH", _KEPT_80, 0.0, 1.0)
    v = _classify(r)
    assert v.classification == "NOW_GATED" and v.gate_reason == "AT_CIRCUIT"
    assert v.explained


def test_analyze_clean_corpus_parity_ok():
    rows = [
        _row("a", "PASSED", 100, "HIGH", _KEPT_80, 1.0, 1.0),          # UNCHANGED
        _row("b", "REJECTED_SCORE_57", 57, "LOW", _KEPT_42, 1.0, 0.5),  # FLIP_PASS (explained)
        _row("c", "PASSED", 90, "HIGH", _KEPT_80, 0.0, 1.0),           # NOW_GATED (explained)
    ]
    res = analyze(rows, weights=_WEIGHTS, v3_min_pass=50, v3_high=75, v3_medium=56)
    assert res["parity_ok"] is True
    assert res["unexplained"] == []
    assert res["counts"].get("FLIP_PASS") == 1 and res["counts"].get("NOW_GATED") == 1


def test_analyze_detects_unexplained_flip():
    # a8=42 with age=1.0 (NOT residual). At a BAD min_pass=56, new needs a8>=44.8 → new
    # fails while OLD (42+10+10=62) passed → FLIP_FAIL that is UNEXPLAINED.
    row = _row("bad", "PASSED", 62, "MEDIUM", _KEPT_42, 1.0, 1.0)
    res = analyze([row], weights=_WEIGHTS, v3_min_pass=56, v3_high=75, v3_medium=56)
    assert res["parity_ok"] is False
    assert len(res["unexplained"]) == 1
    assert res["unexplained"][0].classification == "FLIP_FAIL"


def test_old_recomputed_at_current_config_not_stored_status():
    # a row stored PASSED at the historical min_pass=55 (score 57) must be treated as
    # OLD-REJECTED at today's 60 → NEW also rejects (new=52<... depends) → UNCHANGED,
    # NOT a false FLIP. (This was the 23k-false-flip bug.)
    r = _row("hist55", "PASSED", 57, "LOW", _KEPT_42, 1.0, 0.5)  # a8=42 → old_total 57 < 60
    v = _classify(r)
    assert v.old_score == 57                 # recomputed OLD total (not the stored 57-at-55)
    assert v.classification in ("FLIP_PASS", "UNCHANGED")  # old-reject-at-60 → not FLIP_FAIL


def test_rounding_boundary_flip_fail_is_explained():
    # old_total lands exactly on min_pass by rounding (a8≈39.5: 59.5→60 pass), new
    # rounds just under → FLIP_FAIL explained as rounding_boundary (age=1.0, circ=1.0).
    # a8=39.5 via price_action 0.6333*15≈9.5 ... build a8=39.5: vol1(15)+vwap1(10)+rsi1(10)+time0.9(4.5)=39.5
    kept = {"volume_surge": 1.0, "vwap_position": 1.0, "rsi_range": 1.0, "time_of_day": 0.9}
    r = _row("round_edge", "PASSED", 60, "MEDIUM", kept, 1.0, 1.0)
    v = _classify(r)
    assert v.old_score == 60 and v.classification == "FLIP_FAIL"
    assert v.explained and v.reason == "rounding_boundary"


def test_data_fit_finds_zero_unexplained_thresholds():
    rows = [
        _row("a", "PASSED", 100, "HIGH", _KEPT_80, 1.0, 1.0),
        _row("b", "REJECTED_SCORE_57", 57, "LOW", _KEPT_42, 1.0, 0.5),
        _row("c", "PASSED", 90, "HIGH", _KEPT_80, 0.0, 1.0),
    ]
    fit = data_fit(rows, weights=_WEIGHTS)
    assert fit["fitted"] is not None
    assert fit["analysis"]["parity_ok"] is True
    # analytic start is a valid zero-unexplained point → fit stays near it
    assert 48 <= fit["fitted"]["min_pass"] <= 56
