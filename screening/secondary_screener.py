# screening/secondary_screener.py — Trading System v2
#
# Orchestrates the 10-step screening pipeline: fetches market data,
# runs step_executor, scores via quality_scorer, applies strategy
# min_score override, persists result per P18.
#
# Locked decisions: SS1-SS15, P9a, P18

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

from core.time_authority import now_ist

if TYPE_CHECKING:
    from screening.step_executor import StepExecutor
    from screening.quality_scorer import QualityScorer
    from strategies.schema import StrategyConfig


@dataclass(frozen=True)
class ScreeningResult:
    """Result of secondary_screener.screen() for one signal."""
    passed: bool
    status: str                    # "PASSED" | "REJECTED_<step>" | "SKIPPED_<reason>"
    score: int                     # 0-100
    tier: str                      # "HIGH" | "MEDIUM" | "LOW"
    rejected_step: object          # str | None
    step_results: dict             # step_name -> float
    step_statuses: dict            # step_name -> "PASSED"|"REJECTED"|"ERROR"
    error_steps: list              # steps that raised
    latencies_ms: dict             # step_name -> float ms
    market_data_snapshot: dict     # market_data dict at screening time


class SecondaryScreener:
    """
    SS1: Pure orchestration of step_executor + quality_scorer.
    SS2: Constructor takes step_executor, quality_scorer, state_store,
         quote_fn, logger.
    SS3: screen() -> ScreeningResult.
    SS12: Layer 5 (screening/). No broker import; quote_fn injected.
    """

    def __init__(
        self,
        step_executor: "StepExecutor",
        quality_scorer: "QualityScorer",
        state_store,
        quote_fn: Callable,
        logger,
    ) -> None:
        self._executor = step_executor
        self._scorer = quality_scorer
        self._state_store = state_store
        self._quote_fn = quote_fn
        self._logger = logger

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------

    def screen(
        self,
        signal_id: str,
        symbol: str,
        scanner_name: str,
        trigger_price: float,
        triggered_at: datetime,
        direction: str,
        intent: str,
        strategy: "StrategyConfig",
        market_data: Optional[dict] = None,
    ) -> ScreeningResult:
        """
        SS3: Run full 10-step pipeline and return ScreeningResult.
        SS4: Pipeline order: fetch data -> run steps -> check errors
             -> score -> apply min_score override -> final status.
        SS5: Persist result to state_store (P18). DB failures logged
             but do not prevent returning ScreeningResult.
        """
        # ── 1. Fetch market data if not provided ──────────────────────────────
        if market_data is None:
            try:
                quotes = self._quote_fn([symbol])
                quote = quotes.get(symbol)
                if quote is None:
                    raise KeyError(f"No quote returned for {symbol}")
                market_data = self._build_market_data(quote)
            except Exception:
                self._logger.error(
                    "secondary_screener [%s/%s]: quote_fn failed:\n%s",
                    signal_id, symbol, traceback.format_exc(),
                )
                result = self._make_skipped(
                    "SKIPPED_QUOTE_UNAVAILABLE", {}, signal_id
                )
                self._persist(signal_id, result)
                return result

        market_data_snapshot = dict(market_data)

        # ── 2. Build thresholds from strategy ─────────────────────────────────
        thresholds = {
            "min_volume_surge": strategy.min_volume_surge,
            "min_adr_pct": strategy.min_adr_pct,
            "max_spread_pct": strategy.max_spread_pct,
        }

        # ── 3. Build signal dict for step_executor ────────────────────────────
        signal_dict = {
            "symbol": symbol,
            "scanner_name": scanner_name,
            "trigger_price": trigger_price,
            "triggered_at": triggered_at,
            "direction": direction,
            "intent": intent,
        }

        # ── 4. Run step_executor ──────────────────────────────────────────────
        try:
            exec_result = self._executor.run_all(signal_dict, market_data, thresholds)
        except Exception:
            self._logger.error(
                "secondary_screener [%s/%s]: step_executor raised:\n%s",
                signal_id, symbol, traceback.format_exc(),
            )
            result = self._make_skipped(
                "SKIPPED_EXECUTOR_ERROR", market_data_snapshot, signal_id
            )
            self._persist(signal_id, result)
            return result

        # ── 4b. Check for step errors (P9a fix a) ─────────────────────────────
        if exec_result.error_steps:
            # Any errored step = conservative rejection
            bad = exec_result.error_steps[0]
            self._logger.warning(
                "secondary_screener [%s/%s]: step error in '%s'",
                signal_id, symbol, bad,
            )
            result = ScreeningResult(
                passed=False,
                status="REJECTED_STEP_ERROR",
                score=0,
                tier="LOW",
                rejected_step=bad,
                step_results=exec_result.step_results,
                step_statuses=exec_result.step_statuses,
                error_steps=exec_result.error_steps,
                latencies_ms=exec_result.latencies_ms,
                market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result)
            return result

        # ── 5. Score ──────────────────────────────────────────────────────────
        try:
            score_result = self._scorer.score(exec_result.step_results)
        except Exception:
            self._logger.error(
                "secondary_screener [%s/%s]: quality_scorer raised:\n%s",
                signal_id, symbol, traceback.format_exc(),
            )
            result = self._make_skipped(
                "SKIPPED_SCORER_ERROR", market_data_snapshot, signal_id
            )
            self._persist(signal_id, result)
            return result

        total_score = score_result.total_score
        tier = score_result.tier

        # ── 6. Apply per-strategy min_score override (SS4 step 6) ────────────
        effective_min = (
            strategy.min_score
            if strategy.min_score > 0
            else score_result.min_pass_score
        )
        if total_score < effective_min:
            status = f"REJECTED_SCORE_{total_score}"
            result = ScreeningResult(
                passed=False,
                status=status,
                score=total_score,
                tier=tier,
                rejected_step=None,
                step_results=exec_result.step_results,
                step_statuses=exec_result.step_statuses,
                error_steps=exec_result.error_steps,
                latencies_ms=exec_result.latencies_ms,
                market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result)
            return result

        # ── 7. Signal age defense-in-depth check (P9a add, SS4 step 7) ───────
        if exec_result.step_results.get("signal_age", 1.0) == 0.0:
            result = ScreeningResult(
                passed=False,
                status="REJECTED_SIGNAL_AGE",
                score=total_score,
                tier=tier,
                rejected_step="signal_age",
                step_results=exec_result.step_results,
                step_statuses=exec_result.step_statuses,
                error_steps=exec_result.error_steps,
                latencies_ms=exec_result.latencies_ms,
                market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result)
            return result

        # ── 8. All passed ─────────────────────────────────────────────────────
        result = ScreeningResult(
            passed=True,
            status="PASSED",
            score=total_score,
            tier=tier,
            rejected_step=None,
            step_results=exec_result.step_results,
            step_statuses=exec_result.step_statuses,
            error_steps=exec_result.error_steps,
            latencies_ms=exec_result.latencies_ms,
            market_data_snapshot=market_data_snapshot,
        )
        self._persist(signal_id, result)
        self._logger.info(
            "secondary_screener [%s/%s]: %s score=%d tier=%s",
            signal_id, symbol, result.status, result.score, result.tier,
        )
        return result

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _build_market_data(self, quote) -> dict:
        """
        SS7: Build market_data dict from Quote. Fields not in Quote are set
        to None; step_executor handles missing keys gracefully (SE8).
        SS8: Circuit state derived from quote fields if available.
        """
        ltp = quote.last_price

        # Attempt circuit state detection (SS8) using v2.1 Quote fields
        circuit_state = ""
        upper = getattr(quote, "upper_circuit", None)
        lower = getattr(quote, "lower_circuit", None)
        if upper is not None and ltp >= upper:
            circuit_state = "upper_circuit"
        elif lower is not None and ltp <= lower:
            circuit_state = "lower_circuit"

        return {
            "ltp": ltp,
            "bid": quote.bid,
            "ask": quote.ask,
            "volume": quote.volume,
            "circuit_state": circuit_state,
            # v2.1: populated from Quote fields (Kite API response)
            "vwap": getattr(quote, "vwap", None),
            "open": getattr(quote, "open_price", None),
            "day_high": getattr(quote, "day_high", None),
            "day_low": getattr(quote, "day_low", None),
            # Still not in Kite quote API; would need instruments cache
            "atr": None,
            "rsi": None,
            "sector": None,
            "prev_close": None,
            "avg_volume_20d": None,
        }

    def _make_skipped(self, status: str, market_data_snapshot: dict, signal_id: str) -> ScreeningResult:
        """Build a SKIPPED result with empty step data."""
        return ScreeningResult(
            passed=False,
            status=status,
            score=0,
            tier="LOW",
            rejected_step=None,
            step_results={},
            step_statuses={},
            error_steps=[],
            latencies_ms={},
            market_data_snapshot=market_data_snapshot,
        )

    def _persist(self, signal_id: str, result: ScreeningResult) -> None:
        """
        SS5: Write to state_store. DB failure is logged but never raised
        (don't crash signal pipeline for a DB write issue).
        """
        ts = now_ist().isoformat()
        try:
            # Update signal status row (P18)
            reason = result.rejected_step or result.status
            self._state_store.update_signal_status(
                signal_id,
                result.status,
                reason=reason,
                ts=ts,
            )
            # Write detailed screening analytics row (SS6)
            self._state_store.insert_screener_result(
                signal_id=signal_id,
                score=result.score,
                tier=result.tier,
                status=result.status,
                step_results_json=json.dumps(result.step_results),
                latencies_json=json.dumps(result.latencies_ms),
                market_data_snapshot_json=json.dumps(result.market_data_snapshot),
                ts=ts,
            )
        except Exception:
            self._logger.error(
                "secondary_screener [%s]: state_store write failed:\n%s",
                signal_id, traceback.format_exc(),
            )
