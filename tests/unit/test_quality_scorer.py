# tests/unit/test_quality_scorer.py — Trading System v2
#
# Tests for screening/quality_scorer.py (QS1-QS10)

import logging
import pytest
from unittest.mock import MagicMock

from screening.quality_scorer import QualityScorer, ScoreResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scoring_config(
    volume_surge=15, vwap_position=10, atr_filter=10, rsi_range=10,
    price_action=15, sector_strength=10, time_of_day=5, spread_check=5,
    circuit_check=10, signal_age=10,
    min_pass_score=60, high_score_threshold=80, medium_score_threshold=65,
):
    """Build a ScoringConfig-like mock."""
    steps = MagicMock()
    steps.volume_surge = volume_surge
    steps.vwap_position = vwap_position
    steps.atr_filter = atr_filter
    steps.rsi_range = rsi_range
    steps.price_action = price_action
    steps.sector_strength = sector_strength
    steps.time_of_day = time_of_day
    steps.spread_check = spread_check
    steps.circuit_check = circuit_check
    steps.signal_age = signal_age

    cfg = MagicMock()
    cfg.steps = steps
    cfg.min_pass_score = min_pass_score
    cfg.high_score_threshold = high_score_threshold
    cfg.medium_score_threshold = medium_score_threshold
    return cfg


def _make_scorer():
    logger = logging.getLogger("test_quality_scorer")
    cfg = _make_scoring_config()
    return QualityScorer(cfg, logger)


def _all_ones() -> dict:
    """All 10 steps at raw score 1.0."""
    return {
        "volume_surge": 1.0, "vwap_position": 1.0, "atr_filter": 1.0,
        "rsi_range": 1.0, "price_action": 1.0, "sector_strength": 1.0,
        "time_of_day": 1.0, "spread_check": 1.0, "circuit_check": 1.0,
        "signal_age": 1.0,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import_and_instantiate():
    scorer = _make_scorer()
    assert scorer is not None


def test_score_result_is_score_result_instance():
    scorer = _make_scorer()
    result = scorer.score(_all_ones())
    assert isinstance(result, ScoreResult)


def test_all_steps_at_one_total_matches_weight_sum():
    """Total = sum of all weights = 15+10+10+10+15+10+5+5+10+10 = 100."""
    scorer = _make_scorer()
    result = scorer.score(_all_ones())
    # weights sum to 100
    assert result.total_score == 100


def test_score_capped_at_100():
    """Even if raw scores somehow push beyond 100 (e.g. weights tuned up), cap at 100."""
    cfg = _make_scoring_config(volume_surge=50, price_action=60)  # sum > 100
    scorer = QualityScorer(cfg, logging.getLogger("t"))
    result = scorer.score(_all_ones())
    assert result.total_score == 100


def test_high_tier_when_score_at_threshold():
    """Score == 80 -> HIGH tier."""
    # All steps 1.0 gives 100; set weights so total = 80
    # Use default weights (100 total), set half steps to 0.0 to get 80
    scorer = _make_scorer()
    step_results = _all_ones()
    # Remove 20 points: time_of_day(5) + spread_check(5) + rsi_range(10) = 20
    step_results["time_of_day"] = 0.0
    step_results["spread_check"] = 0.0
    step_results["rsi_range"] = 0.0
    result = scorer.score(step_results)
    assert result.total_score == 80
    assert result.tier == "HIGH"


def test_medium_tier_at_threshold():
    """Score == 65 -> MEDIUM tier."""
    scorer = _make_scorer()
    step_results = _all_ones()
    # Remove 35 points: signal_age(10)+circuit_check(10)+spread_check(5)+time_of_day(5)+rsi_range(5 partial)
    # Easier: set volume_surge=0 (15), vwap_position=0 (10), spread_check=0 (5), time_of_day=0 (5) = 35
    step_results["volume_surge"] = 0.0
    step_results["vwap_position"] = 0.0
    step_results["spread_check"] = 0.0
    step_results["time_of_day"] = 0.0
    result = scorer.score(step_results)
    assert result.total_score == 65
    assert result.tier == "MEDIUM"


def test_medium_tier_between_thresholds():
    """Score between 65 and 79 -> MEDIUM."""
    scorer = _make_scorer()
    step_results = _all_ones()
    # Remove 24 points: signal_age(10)+circuit_check(10)+spread_check(4 partial) nope, use round numbers
    # Remove volume_surge(15) + rsi_range(10) = 25 -> total 75
    step_results["volume_surge"] = 0.0
    step_results["rsi_range"] = 0.0
    result = scorer.score(step_results)
    assert result.total_score == 75
    assert result.tier == "MEDIUM"


def test_low_tier_below_medium_threshold():
    """Score < 65 -> LOW."""
    scorer = _make_scorer()
    step_results = {k: 0.0 for k in _all_ones()}
    step_results["price_action"] = 1.0   # only 15 points
    result = scorer.score(step_results)
    assert result.total_score == 15
    assert result.tier == "LOW"


def test_passed_true_when_score_at_min_pass():
    """Score == 60 -> passed=True."""
    scorer = _make_scorer()
    step_results = _all_ones()
    # Remove 40 points: volume_surge(15)+vwap_position(10)+spread_check(5)+time_of_day(5)+rsi_range(5partial)
    # Remove volume_surge(15)+vwap_position(10)+atr_filter(10)+spread_check(5) = 40
    step_results["volume_surge"] = 0.0
    step_results["vwap_position"] = 0.0
    step_results["atr_filter"] = 0.0
    step_results["spread_check"] = 0.0
    result = scorer.score(step_results)
    assert result.total_score == 60
    assert result.passed is True


def test_passed_false_when_score_below_min_pass():
    """Score < 60 -> passed=False."""
    scorer = _make_scorer()
    step_results = {k: 0.0 for k in _all_ones()}
    step_results["signal_age"] = 1.0   # only 10 points
    result = scorer.score(step_results)
    assert result.passed is False


def test_missing_step_contributes_zero_and_appears_in_missing_list():
    """FIX-042: Missing step -> excluded from denominator, recalculated proportionally."""
    scorer = _make_scorer()
    step_results = _all_ones()
    del step_results["volume_surge"]  # weight=15
    result = scorer.score(step_results)
    # FIX-042: 9 steps achieve 85 out of 85 present weights -> (85/85)*100 = 100
    assert result.total_score == 100
    assert "volume_surge" in result.missing_steps
    assert result.step_scores["volume_surge"] == 0  # still reported as 0 for transparency


def test_all_steps_missing_score_zero_low_not_passed():
    """All steps missing -> score=0, tier=LOW, passed=False."""
    scorer = _make_scorer()
    result = scorer.score({})
    assert result.total_score == 0
    assert result.tier == "LOW"
    assert result.passed is False
    assert len(result.missing_steps) == 10


def test_step_scores_populated_for_all_10_steps():
    """step_scores must have all 10 keys."""
    scorer = _make_scorer()
    result = scorer.score(_all_ones())
    expected_keys = {
        "volume_surge", "vwap_position", "atr_filter", "rsi_range",
        "price_action", "sector_strength", "time_of_day", "spread_check",
        "circuit_check", "signal_age",
    }
    assert set(result.step_scores.keys()) == expected_keys


def test_determinism_same_inputs_same_result():
    """Same inputs twice -> identical ScoreResult."""
    scorer = _make_scorer()
    step_results = _all_ones()
    step_results["volume_surge"] = 0.5
    r1 = scorer.score(step_results)
    r2 = scorer.score(step_results)
    assert r1 == r2


def test_score_result_fields_all_populated():
    """All ScoreResult fields have expected types."""
    scorer = _make_scorer()
    result = scorer.score(_all_ones())
    assert isinstance(result.total_score, int)
    assert result.tier in ("HIGH", "MEDIUM", "LOW")
    assert isinstance(result.passed, bool)
    assert isinstance(result.step_scores, dict)
    assert isinstance(result.missing_steps, list)
    assert isinstance(result.min_pass_score, int)
    assert isinstance(result.tier_thresholds, dict)
    assert "high" in result.tier_thresholds
    assert "medium" in result.tier_thresholds


def test_partial_raw_scores_weighted_correctly():
    """0.5 raw on a 10-weight step -> 5 in step_scores."""
    scorer = _make_scorer()
    step_results = {k: 0.0 for k in _all_ones()}
    step_results["rsi_range"] = 0.5   # weight=10 -> 5 points
    result = scorer.score(step_results)
    assert result.step_scores["rsi_range"] == 5
    assert result.total_score == 5


def test_min_pass_score_and_tier_thresholds_from_config():
    """ScoreResult carries config values."""
    scorer = _make_scorer()
    result = scorer.score(_all_ones())
    assert result.min_pass_score == 60
    assert result.tier_thresholds["high"] == 80
    assert result.tier_thresholds["medium"] == 65


# ---------------------------------------------------------------------------
# FIX-042: Proportional Scoring Tests
# ---------------------------------------------------------------------------

def test_fix042_proportional_scoring_prevents_false_rejection():
    """
    FIX-042: Signal scoring 65 doesn't drop below min_pass_score=60 when one step times out.

    Scenario: 9 steps achieve 55 out of 90 weights (61.1% -> rounds to 61).
    Old behavior: 55/100 = 55 -> rejected (< 60)
    New behavior: (55/90)*100 = 61 -> passed (>= 60)
    """
    scorer = _make_scorer()
    step_results = {
        "volume_surge": 0.6,      # 15 * 0.6 = 9
        "vwap_position": 0.5,     # 10 * 0.5 = 5
        "atr_filter": 0.5,        # 10 * 0.5 = 5
        "rsi_range": 0.6,         # 10 * 0.6 = 6
        "price_action": 0.6,      # 15 * 0.6 = 9
        "sector_strength": 0.5,   # 10 * 0.5 = 5
        "time_of_day": 1.0,       # 5 * 1.0 = 5
        "spread_check": 1.0,      # 5 * 1.0 = 5
        "signal_age": 0.6,        # 10 * 0.6 = 6
        # circuit_check (weight=10) missing -> times out due to broker API delay
    }
    result = scorer.score(step_results)
    # Sum achieved: 9+5+5+6+9+5+5+5+6 = 55
    # Weights present: 90 (circuit_check's 10 excluded)
    # Score: (55/90)*100 = 61.11 -> rounds to 61
    assert result.total_score == 61
    assert result.passed is True
    assert "circuit_check" in result.missing_steps


def test_fix042_multiple_missing_steps_recalculate_denominator():
    """FIX-042: Multiple missing steps -> denominator adjusted proportionally."""
    scorer = _make_scorer()
    step_results = {
        "volume_surge": 1.0,      # 15
        "price_action": 1.0,      # 15
        "vwap_position": 1.0,     # 10
        # 7 steps missing (total weight 60) -> present weight = 40
    }
    result = scorer.score(step_results)
    # Achieved: 15+15+10 = 40 out of 40 present weights
    # Score: (40/40)*100 = 100
    assert result.total_score == 100
    assert len(result.missing_steps) == 7
    assert result.tier == "HIGH"


def test_fix042_partial_scores_with_missing_steps():
    """FIX-042: Partial scores (< 1.0) with missing steps calculate correctly."""
    scorer = _make_scorer()
    step_results = {
        "volume_surge": 0.8,      # 15 * 0.8 = 12
        "vwap_position": 0.5,     # 10 * 0.5 = 5
        "atr_filter": 0.6,        # 10 * 0.6 = 6
        "price_action": 0.7,      # 15 * 0.7 = 10.5
        "sector_strength": 0.5,   # 10 * 0.5 = 5
        # 5 steps missing (weight 40) -> present weight = 60
    }
    result = scorer.score(step_results)
    # Achieved: 12+5+6+10.5+5 = 38.5
    # Present weights: 60
    # Score: (38.5/60)*100 = 64.17 -> rounds to 64
    assert result.total_score == 64
    assert result.tier == "LOW"  # 64 < 65 (medium threshold)
    assert len(result.missing_steps) == 5


def test_fix042_all_steps_missing_returns_zero():
    """FIX-042: All steps missing -> total_weights_present=0 -> score 0 (no divide-by-zero)."""
    scorer = _make_scorer()
    result = scorer.score({})
    assert result.total_score == 0
    assert result.tier == "LOW"
    assert result.passed is False
    assert len(result.missing_steps) == 10


if __name__ == "__main__":
    import subprocess, sys
    r = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-v"],
        capture_output=False,
    )
    sys.exit(r.returncode)
