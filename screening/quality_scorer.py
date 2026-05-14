# screening/quality_scorer.py — Trading System v2
#
# Compute quality score (0-100) for a signal based on weighted step scores.
# All weights and thresholds come from ScoringConfig (scoring_weights.yaml).
# NO inline constants (P9b).
#
# Locked decisions: QS1-QS10, P9b

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.config_loader import ScoringConfig


@dataclass(frozen=True)
class ScoreResult:
    """Result of quality scoring for a single signal."""
    total_score: int                   # 0-100
    tier: str                          # "HIGH" | "MEDIUM" | "LOW"
    passed: bool                       # total_score >= min_pass_score
    step_scores: dict                  # step_name -> weighted int score
    missing_steps: list                # steps absent from step_results input
    min_pass_score: int                # from config
    tier_thresholds: dict              # {high: int, medium: int} from config


class QualityScorer:
    """
    Compute a quality score for a signal from pre-computed step raw scores.

    QS3: score(step_results) -> ScoreResult
    QS4: weighted_score = raw_score * weight; total capped at 100
    QS5: tier assigned from high_score_threshold / medium_score_threshold
    QS7: scorer returns tier string only; sizer applies size multiplier
    QS9: pure, deterministic, thread-safe
    """

    def __init__(self, weights: "ScoringConfig", logger) -> None:
        self._weights = weights
        self._logger = logger
        self._step_names: list[str] = list(
            weights.steps.model_fields_set
            if hasattr(weights.steps, "model_fields_set")
            else vars(weights.steps).keys()
        )
        # Build a stable ordered list from the Pydantic model's fields
        self._step_names = [
            "volume_surge", "vwap_position", "atr_filter", "rsi_range",
            "price_action", "sector_strength", "time_of_day", "spread_check",
            "circuit_check", "signal_age",
        ]

    def score(self, step_results: dict) -> ScoreResult:
        """
        QS3: Compute total score from step raw scores (0.0-1.0).

        FIX-042: Proportional scoring — missing steps excluded from denominator.
        If circuit_check times out, recalculate score as (achieved / weights_present) * 100
        instead of penalizing with 0.0 for infrastructure jitter.
        """
        steps_cfg = self._weights.steps
        step_scores: dict[str, int] = {}
        missing_steps: list[str] = []
        total_achieved: float = 0.0
        total_weights_present: int = 0
        total_weights_possible: int = 0

        for name in self._step_names:
            weight: int = getattr(steps_cfg, name)
            total_weights_possible += weight

            if name not in step_results:
                # FIX-042: Missing step excluded from denominator
                self._logger.warning(
                    "quality_scorer.missing_step: step '%s' missing - excluded from scoring denominator",
                    name,
                )
                missing_steps.append(name)
                step_scores[name] = 0  # still report 0 in step_scores for transparency
            else:
                raw = float(step_results[name])
                weighted = raw * weight
                step_scores[name] = int(round(weighted))
                total_achieved += weighted
                total_weights_present += weight

        # FIX-042: Proportional scoring calculation
        if total_weights_present > 0:
            final_score = (total_achieved / total_weights_present) * 100.0
            total_score: int = min(100, int(round(final_score)))
            self._logger.info(
                "quality_scorer.proportional_scoring: effective_weights=%d / total_weights=%d (%.1f%%)",
                total_weights_present,
                total_weights_possible,
                (total_weights_present / total_weights_possible) * 100.0,
            )
        else:
            # FIX-042: All steps missing -> score 0
            total_score = 0
            self._logger.warning(
                "quality_scorer.all_steps_missing: returning score 0 (no valid steps)",
            )

        high_thr = self._weights.high_score_threshold
        med_thr = self._weights.medium_score_threshold
        if total_score >= high_thr:
            tier = "HIGH"
        elif total_score >= med_thr:
            tier = "MEDIUM"
        else:
            tier = "LOW"

        passed = total_score >= self._weights.min_pass_score

        return ScoreResult(
            total_score=total_score,
            tier=tier,
            passed=passed,
            step_scores=step_scores,
            missing_steps=missing_steps,
            min_pass_score=self._weights.min_pass_score,
            tier_thresholds={"high": high_thr, "medium": med_thr},
        )
