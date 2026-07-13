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
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Optional

from core.logger import SafeJSONEncoder  # FIX-104: Reuse instead of duplicating
from core.time_authority import now_ist
# Single source of truth for the circuit-band margin: the SAME constant the
# post-fill placeability gate (orders.price_math.clamp_exit_into_band) uses, so
# the pre-fill ceiling and the clamp ceiling never drift (NOCIL fix).
from orders.price_math import DEFAULT_CIRCUIT_MARGIN_PCT
# V3 03.03/03.04: the pre-fill circuit-proximity rule is RELOCATED into
# screening.hard_gate (single source; the OFF path delegates to it, byte-identical)
# + the shared 8-step re-scale helpers used by both the live v3 path and the
# offline parity recompute tool.
from screening.hard_gate import (
    circuit_proximity_reason as _hg_circuit_proximity_reason,
    rescale_min_score,
    rescaled_total,
    tier_for,
)

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
        circuit_proximity_reject_enabled: bool = True,
        mis_blocklist=None,                       # core.mis_blocklist.MisLearnedBlocklist | None
        mis_filter_enabled: bool = False,         # master switch (default OFF -> dormant)
        mis_filter_shadow: bool = True,           # when enabled: True = log-only, False = actually reject
        resolve_product: Optional[Callable] = None,  # intent -> product code ("MIS"/...); broker-free closure
        hard_gate=None,                           # V3 03.03: screening.hard_gate.HardGate | None
        scoring_config=None,                      # V3 03.04: core.config_loader.ScoringConfig | None
    ) -> None:
        self._executor = step_executor
        self._scorer = quality_scorer
        self._state_store = state_store
        self._quote_fn = quote_fn
        self._logger = logger

        # V3 03.03/03.04 — Hard-Gate + scorer re-scale. DEFAULT-OFF: when the mode
        # is "off" (or no scoring_config is injected, as in most tests) NONE of the
        # v3 code runs and screen() is byte-identical to the pre-V3 path.
        self._hard_gate = hard_gate
        self._v3_mode = str(getattr(scoring_config, "v3_hardgate_mode", "off") or "off")
        self._v3_gate_steps = set(
            getattr(scoring_config, "v3_gate_steps", ["circuit_check", "signal_age"]) or []
        )
        self._v3_min_pass = int(getattr(scoring_config, "v3_min_pass_score", 50))
        self._v3_high = int(getattr(scoring_config, "v3_high_score_threshold", 75))
        self._v3_medium = int(getattr(scoring_config, "v3_medium_score_threshold", 56))
        self._step_weights = (
            dict(scoring_config.steps.model_dump())
            if scoring_config is not None and hasattr(scoring_config, "steps") else {}
        )
        # NOCIL fix: pre-fill circuit-proximity reject (framing-b, both legs).
        # YAML fast-disable lever (default ON, parity-safe). The post-fill
        # placeability gate is the core safety net and is NOT flag-gated.
        self._circuit_proximity_reject_enabled = bool(circuit_proximity_reject_enabled)
        # MIS learned blocklist (source-free): pre-drop a symbol the broker has
        # recently MIS-blocked. Default OFF -> dormant. Recording happens in the
        # order_placer 400-handler; the screener only READS the learned set here.
        self._mis_blocklist = mis_blocklist
        self._mis_filter_enabled = bool(mis_filter_enabled)
        self._mis_filter_shadow = bool(mis_filter_shadow)
        self._resolve_product = resolve_product

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
        # ── 0. MIS learned-blocklist pre-drop (source-free; observes the broker's 400) ──
        # Drop a signal for a symbol the broker has recently MIS-blocked, BEFORE we
        # spend the quote + 10-step funnel on a doomed-at-placement entry. Applied only
        # when the resolved product is MIS (future-proof for CNC/delivery). Gated by
        # mis_filter_enabled; in shadow mode we log the would-drop but let it continue.
        # Needs no market data — pure symbol/intent lookup, so it runs first.
        if self._mis_filter_enabled and self._mis_blocklist is not None:
            if self._is_mis_blocked_decision(symbol, intent, direction, signal_id):
                if not self._mis_filter_shadow:
                    result = ScreeningResult(
                        passed=False,
                        status="REJECTED_NOT_MIS_TRADABLE",
                        score=0,
                        tier="LOW",
                        rejected_step="mis_tradable",
                        step_results={},
                        step_statuses={},
                        error_steps=[],
                        latencies_ms={},
                        market_data_snapshot={},
                    )
                    self._persist(signal_id, result)
                    return result
                # shadow=True -> the would-drop is logged inside the helper; fall through.

        # ── 1. Fetch market data if not provided ──────────────────────────────
        if market_data is None:
            try:
                quotes = self._quote_fn([symbol])
                quote = quotes.get(symbol)
                if quote is None:
                    # No quote for this symbol right now (illiquid / not currently trading) is an
                    # EXPECTED, benign skip that fires ~hundreds of times a session. Log a single
                    # INFO line WITHOUT a traceback and WITHOUT raising — the old `raise KeyError`
                    # +ERROR+traceback produced ~249 error tracebacks/day that masked real ERRORs.
                    self._logger.info(
                        "secondary_screener [%s/%s]: no quote available -> "
                        "SKIPPED_QUOTE_UNAVAILABLE", signal_id, symbol,
                    )
                    result = self._make_skipped("SKIPPED_QUOTE_UNAVAILABLE", {}, signal_id)
                    self._persist(signal_id, result)
                    return result
                market_data = self._build_market_data(quote)
            except Exception:
                # quote_fn or _build_market_data genuinely FAILED (broker/network/parse) — this
                # IS unexpected, so keep the full ERROR + traceback.
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

        # ── V3 03.03/03.04 ENFORCE: gate-first + 8-step + re-scaled thresholds ──
        # Only taken when the flag is explicitly "enforce" (default off never here).
        if self._v3_mode == "enforce" and self._hard_gate is not None:
            return self._screen_v3_enforce(
                signal_id, symbol, scanner_name, trigger_price, triggered_at,
                direction, intent, strategy, market_data, market_data_snapshot,
            )

        # ── 1b. Pre-fill circuit-proximity rejection (NOCIL fix) ──────────────
        # Reject doomed-at-fill entries that sit at/beyond the exit-clamp ceiling
        # (no profitable TGT or valid SL could be placed inside the day's band).
        # Framing-b, BOTH legs; behind a fast-disable flag. Hard reject, not a
        # soft score — runs before the 10-step pipeline.
        cp_reason = self._circuit_proximity_reason(trigger_price, direction, market_data)
        if cp_reason is not None:
            self._logger.warning(
                "secondary_screener [%s/%s]: REJECTED_CIRCUIT_PROXIMITY — %s",
                signal_id, symbol, cp_reason,
            )
            result = ScreeningResult(
                passed=False,
                status="REJECTED_CIRCUIT_PROXIMITY",
                score=0,
                tier="LOW",
                rejected_step="circuit_proximity",
                step_results={},
                step_statuses={},
                error_steps=[],
                latencies_ms={},
                market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result)
            return result

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
            # FIX-169 F40: log ALL error steps, not just first
            bad = ", ".join(str(s) for s in exec_result.error_steps)
            self._logger.warning(
                "secondary_screener [%s/%s]: step errors in [%s]",
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

        # ── V3 03.03/03.04 SHADOW: compute the NEW (gate + 8-step + v3 tier) and
        # log it alongside the OLD. The LIVE decision below is UNCHANGED (follows
        # OLD); this is a pure side-effect for the old-vs-new soak compare. Never
        # runs when mode is off. Belt-and-suspenders — the binding parity proof is
        # the offline recompute (scripts/v3_hardgate_parity_recompute.py). ──
        if self._v3_mode == "shadow" and self._hard_gate is not None:
            self._log_v3_shadow_compare(
                signal_id, symbol, trigger_price, triggered_at, direction,
                market_data, exec_result.step_results, score_result,
            )

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
            self._persist(signal_id, result, eligible_score=effective_min)
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
            self._persist(signal_id, result, eligible_score=effective_min)
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
        self._persist(signal_id, result, eligible_score=effective_min)
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
            # Raw band values for the pre-fill circuit-proximity reject (NOCIL fix)
            # + forensic snapshot. None when the quote does not carry them.
            "upper_circuit": upper,
            "lower_circuit": lower,
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

    def _circuit_proximity_reason(
        self, trigger_price, direction, market_data: dict
    ) -> Optional[str]:
        """Pre-fill circuit-proximity reject (NOCIL fix). RELOCATED to
        screening.hard_gate.circuit_proximity_reason (single source); this
        delegates so the OFF path is byte-identical AND the same implementation
        backs the V3 Hard-Gate. Fail-open behaviour unchanged.
        """
        return _hg_circuit_proximity_reason(
            trigger_price, direction, market_data,
            enabled=self._circuit_proximity_reject_enabled,
        )

    # ── V3 03.03/03.04: enforce path (gate-first + 8-step + re-scaled thresholds) ──

    def _screen_v3_enforce(
        self, signal_id, symbol, scanner_name, trigger_price, triggered_at,
        direction, intent, strategy, market_data, market_data_snapshot,
    ) -> ScreeningResult:
        """ENFORCE: the Hard-Gate runs BEFORE scoring; survivors are scored on the
        8 kept steps and judged against the re-scaled v3 thresholds; there is NO
        signal_age defense-in-depth (the gate owns freshness). Same
        ScreeningResult shape/status vocabulary as the OFF path."""
        # 1. Hard-Gate first (at-circuit + circuit-proximity + freshness; liquidity no-op).
        gate = self._hard_gate.evaluate(
            trigger_price=trigger_price, triggered_at=triggered_at,
            direction=direction, market_data=market_data,
        )
        if not gate.passed:
            self._logger.warning(
                "secondary_screener [%s/%s]: HARD_GATE reject — %s %s",
                signal_id, symbol, gate.reason, gate.evidence,
            )
            result = ScreeningResult(
                passed=False, status=f"REJECTED_{gate.reason}", score=0, tier="LOW",
                rejected_step=str(gate.reason).lower(), step_results={}, step_statuses={},
                error_steps=[], latencies_ms={}, market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result)
            return result

        thresholds = {
            "min_volume_surge": strategy.min_volume_surge,
            "min_adr_pct": strategy.min_adr_pct,
            "max_spread_pct": strategy.max_spread_pct,
        }
        signal_dict = {
            "symbol": symbol, "scanner_name": scanner_name,
            "trigger_price": trigger_price, "triggered_at": triggered_at,
            "direction": direction, "intent": intent,
        }

        # 2. Run the 8 scored steps (exclude the gate-owned steps).
        try:
            exec_result = self._executor.run_all(
                signal_dict, market_data, thresholds, exclude_steps=self._v3_gate_steps
            )
        except Exception:
            self._logger.error(
                "secondary_screener [%s/%s]: step_executor raised:\n%s",
                signal_id, symbol, traceback.format_exc(),
            )
            result = self._make_skipped("SKIPPED_EXECUTOR_ERROR", market_data_snapshot, signal_id)
            self._persist(signal_id, result)
            return result
        if exec_result.error_steps:
            bad = ", ".join(str(s) for s in exec_result.error_steps)
            result = ScreeningResult(
                passed=False, status="REJECTED_STEP_ERROR", score=0, tier="LOW",
                rejected_step=bad, step_results=exec_result.step_results,
                step_statuses=exec_result.step_statuses, error_steps=exec_result.error_steps,
                latencies_ms=exec_result.latencies_ms, market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result)
            return result

        # 3. Re-scaled 8-step total + v3 tier (shared helpers; scorer untouched).
        total = rescaled_total(exec_result.step_results, self._step_weights, self._v3_gate_steps)
        new_tier = tier_for(total, self._v3_high, self._v3_medium)

        # 4. Per-strategy min_score override (re-scaled, A7) else the v3 min_pass.
        eff_min = (
            rescale_min_score(strategy.min_score)
            if getattr(strategy, "min_score", 0) and strategy.min_score > 0
            else self._v3_min_pass
        )
        if total < eff_min:
            result = ScreeningResult(
                passed=False, status=f"REJECTED_SCORE_{total}", score=total, tier=new_tier,
                rejected_step=None, step_results=exec_result.step_results,
                step_statuses=exec_result.step_statuses, error_steps=exec_result.error_steps,
                latencies_ms=exec_result.latencies_ms, market_data_snapshot=market_data_snapshot,
            )
            self._persist(signal_id, result, eligible_score=eff_min)
            return result

        # 5. Passed — NO signal_age defense-in-depth (freshness is now the gate).
        result = ScreeningResult(
            passed=True, status="PASSED", score=total, tier=new_tier, rejected_step=None,
            step_results=exec_result.step_results, step_statuses=exec_result.step_statuses,
            error_steps=exec_result.error_steps, latencies_ms=exec_result.latencies_ms,
            market_data_snapshot=market_data_snapshot,
        )
        self._persist(signal_id, result, eligible_score=eff_min)
        self._logger.info(
            "secondary_screener [%s/%s]: %s score=%d tier=%s (v3-enforce)",
            signal_id, symbol, result.status, result.score, result.tier,
        )
        return result

    def _log_v3_shadow_compare(
        self, signal_id, symbol, trigger_price, triggered_at, direction,
        market_data, step_results, score_result,
    ) -> None:
        """SHADOW: log OLD (10-step) vs NEW (gate + 8-step + v3 tier) for the
        old-vs-new soak compare. Pure side-effect; never changes the live result,
        never raises."""
        try:
            gate = self._hard_gate.evaluate(
                trigger_price=trigger_price, triggered_at=triggered_at,
                direction=direction, market_data=market_data,
            )
            new_total = rescaled_total(step_results, self._step_weights, self._v3_gate_steps)
            new_tier = tier_for(new_total, self._v3_high, self._v3_medium)
            self._logger.info(
                "v3_shadow [%s/%s]: OLD score=%d tier=%s | NEW score=%d tier=%s gate=%s",
                signal_id, symbol, score_result.total_score, score_result.tier,
                new_total, new_tier, (gate.reason or "PASS"),
            )
        except Exception:
            self._logger.error(
                "v3_shadow [%s/%s]: compare failed:\n%s", signal_id, symbol, traceback.format_exc(),
            )

    def _is_mis_blocked_decision(
        self, symbol: str, intent: str, direction: str, signal_id: str
    ) -> bool:
        """True iff this signal is a would-drop as not-MIS-tradable.

        Gating: only when the resolved product is MIS (future-proof for CNC/delivery);
        fail-open on resolve errors. LOGS every would-drop (req 4): symbol, direction,
        last_blocked_date, days-since, mode (shadow/active). Returns True for BOTH
        shadow and active — the caller decides whether to actually reject.
        """
        # Future-proof product gate: only MIS orders can be MIS-blocked.
        if self._resolve_product is not None:
            try:
                product = self._resolve_product(intent)
            except Exception:
                return False  # cannot confirm product -> do not drop (fail-open)
            if product != "MIS":
                return False
        if not self._mis_blocklist.is_blocked(symbol):
            return False
        last, days = self._mis_blocklist.info(symbol)
        mode = "shadow" if self._mis_filter_shadow else "active"
        self._logger.warning(
            "secondary_screener [%s/%s]: REJECTED_NOT_MIS_TRADABLE (%s) — symbol "
            "MIS-blocked on %s (%s days ago, ttl re-test); direction=%s",
            signal_id, symbol, mode, last, days, direction,
        )
        return True

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

    def _persist(
        self, signal_id: str, result: ScreeningResult,
        eligible_score: Optional[int] = None,
    ) -> None:
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
                market_data_snapshot_json=json.dumps(result.market_data_snapshot, cls=SafeJSONEncoder),
                ts=ts,
                eligible_score=eligible_score,
            )
        except Exception:
            self._logger.error(
                "secondary_screener [%s]: state_store write failed:\n%s",
                signal_id, traceback.format_exc(),
            )

    def shutdown(self) -> None:
        """
        FIX-100: Shutdown internal step_executor cleanly.

        Called by signal_processor.stop() during system shutdown.
        Propagates shutdown to the step_executor's internal thread pool.
        """
        if hasattr(self._executor, 'shutdown'):
            self._executor.shutdown()
