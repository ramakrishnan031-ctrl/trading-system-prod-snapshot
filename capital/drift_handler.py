"""
capital/drift_handler.py -- Trading System v2 (BL-2 / Phase B.4)

Purpose:
    Subscribe to CapitalDriftDetected events and escalate based on severity.
    Replaces the log-only _log_capital_drift_event stub that previously
    subscribed to this event.

Why this is a separate component:
    FundManager already carries enough wiring (state_store, kill_switch,
    on_critical_failure). Drift handling is a policy concern orthogonal to
    capital mutation; it deserves its own file with single responsibility.

Locked Design Decisions:
    DH1 -- Source-module filter (Option X from pre-work). Only events with
           source_module in _ESCALATING_SOURCES trigger kill_switch escalation.
           Today that's {"fund_manager"} -- the FM9 sync_from_broker publisher.
           Events from order_reconciler (G3 CAPITAL_DRIFT, CHECK2
           ORPHAN_ADOPTION, CHECK5 POSITION_GREW) are logged at INFO and
           ignored for escalation purposes: they already have their own
           escalation paths (Telegram alerts, UNRECOVERABLE reconciliation
           actions), and their delta semantics differ (CHECK5 emits integer
           share qty, not rupees -- a percentage/absolute-rupee escalation
           would misfire catastrophically).
    DH2 -- Absolute rupee thresholds. Event emits delta in rupees for the
           escalating publisher (FM9); no computation surface, no division
           by possibly-zero expected. Existing capital_drift_tolerance in
           OrderReconcilerConfig is also absolute; symmetry.
    DH3 -- Three tiers + NOISE floor:
             NOISE    -- |delta| < log_only_threshold          no action, DEBUG
             LOG_ONLY -- log_only <= |delta| < soft_kill       CRITICAL log only
             SOFT     -- soft_kill <= |delta| < hard_kill      soft_kill + log
             HARD     -- hard_kill <= |delta|                  hard_kill + log
    DH4 -- Consecutive-cycle escalation. Persistent LOG_ONLY drift on an
           escalating source auto-bumps to SOFT after N cycles. Counter is
           ESCALATING-SOURCE-SCOPED: non-escalating events (CHECK5 etc.)
           do NOT reset or increment it. Both escalating sources
           (fund_manager FM9 and fund_manager_self_check BL-3) share the
           same counter -- intentional, since concurrent drift on both
           surfaces is strictly worse than drift on one.
    DH5 -- Optional kill_switch (matches FM19 / BL-9 pattern). If None,
           escalation still computes the tier but logs CRITICAL that
           escalation is impossible. Unit tests can inject None; production
           wires the real KillSwitch.
    DH6 -- Subscriber exception swallow is LOAD-BEARING per EV4. The
           EventBus collects errors and raises EventDispatchError if any
           subscriber propagates an exception. If on_drift didn't swallow,
           a handler bug would propagate into fund_manager.sync_from_broker
           or the reconciler publish thread. Do NOT remove the try/except
           in on_drift without first confirming EV4 policy has changed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Final, Optional

from core.events import CapitalDriftDetected
from core.logger import log_exception

if TYPE_CHECKING:
    from capital.kill_switch import KillSwitch
    from core.config_loader import DriftHandlerConfig


_ESCALATING_SOURCES: Final[frozenset[str]] = frozenset({
    "fund_manager",            # FM9 sync_from_broker (broker vs local total)
    "fund_manager_self_check", # BL-3 OrderReconciler._check7 (fm vs fm_ledger)
})
# Tier constants -- string literals used in logs and tests; centralised here.
TIER_NOISE: Final[str] = "NOISE"
TIER_LOG_ONLY: Final[str] = "LOG_ONLY"
TIER_SOFT: Final[str] = "SOFT"
TIER_HARD: Final[str] = "HARD"
TIER_SOFT_ESCALATED: Final[str] = "SOFT_ESCALATED"


class CapitalDriftHandler:
    """BL-2 subscriber for CapitalDriftDetected -- tiered kill escalation."""

    def __init__(
        self,
        config: "DriftHandlerConfig",
        kill_switch: Optional["KillSwitch"] = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._config = config
        self._kill_switch = kill_switch
        self._log = logger or logging.getLogger(__name__)
        # DH4: escalating-source-scoped counter; non-escalating reconciler
        # events (CHECK5 POSITION_GREW etc.) never touch it. Both escalating
        # sources (fund_manager, fund_manager_self_check) share this counter.
        self._consecutive_log_only_cycles = 0

    # ── public handler ──────────────────────────────────────────────────────

    def get_consecutive_cycles(self) -> int:
        """
        Read-only accessor for the fund_manager-scoped consecutive
        log-only-tier counter (DH4). Used by integration tests to assert
        that escalation wiring is live end-to-end.
        """
        return self._consecutive_log_only_cycles

    def on_drift(self, event: CapitalDriftDetected) -> None:
        """
        Event-bus entry point. Swallows its own exceptions per DH6 / EV4
        so subscriber failures never propagate back into the publisher
        thread (fund_manager.sync_from_broker or reconciler publish).
        """
        try:
            self._handle(event)
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.error(
                "drift_handler.on_drift_failed",
                extra={
                    "source": getattr(event, "source_module", "?"),
                    "delta": getattr(event, "delta", None),
                },
            )
            # DH6 / EV4: do NOT re-raise.

    # ── internal ────────────────────────────────────────────────────────────

    def _compute_tier(self, drift_rs: float) -> str:
        if drift_rs >= self._config.hard_kill_threshold_rs:
            return TIER_HARD
        if drift_rs >= self._config.soft_kill_threshold_rs:
            return TIER_SOFT
        if drift_rs >= self._config.log_only_threshold_rs:
            return TIER_LOG_ONLY
        return TIER_NOISE

    def _handle(self, event: CapitalDriftDetected) -> None:
        drift_rs = abs(event.delta)
        tier = self._compute_tier(drift_rs)
        is_escalating = event.source_module in _ESCALATING_SOURCES

        if not is_escalating:
            # DH1: non-escalating sources log at INFO only. Do NOT touch
            # the counter -- it's strictly fund_manager-scoped.
            self._log.info(
                "drift event from non-escalating source",
                extra={
                    "source": event.source_module,
                    "tier": tier,
                    "delta": event.delta,
                    "expected": event.expected,
                    "actual": event.actual,
                },
            )
            return

        # Defensive: fund_manager's FM9 path never emits expected ~= 0 in
        # normal flow. A zero expected from an escalating source is suspicious.
        if abs(event.expected) < 1.0:
            self._log.warning(
                "escalating drift event with ~zero expected; possible publisher bug",
                extra={
                    "source": event.source_module,
                    "expected": event.expected,
                    "actual": event.actual,
                    "delta": event.delta,
                },
            )

        # DH4: counter mechanics for escalating source only.
        if tier == TIER_LOG_ONLY:
            self._consecutive_log_only_cycles += 1
            if (self._consecutive_log_only_cycles
                    >= self._config.consecutive_cycles_before_escalate):
                self._log.warning(
                    "fund_manager drift persisted for %d cycles; "
                    "escalating log-only tier to soft_kill",
                    self._consecutive_log_only_cycles,
                )
                tier = TIER_SOFT_ESCALATED
        elif tier in (TIER_SOFT, TIER_HARD):
            self._consecutive_log_only_cycles = 0
        elif tier == TIER_NOISE:
            self._consecutive_log_only_cycles = 0

        self._dispatch(tier, event, drift_rs)

    def _dispatch(
        self,
        tier: str,
        event: CapitalDriftDetected,
        drift_rs: float,
    ) -> None:
        if tier == TIER_NOISE:
            self._log.debug(
                "drift below log-only threshold: %.2f (threshold=%.2f)",
                drift_rs, self._config.log_only_threshold_rs,
            )
            return

        # Every non-noise tier logs CRITICAL with structured context.
        self._log.critical(
            "capital drift detected",
            extra={
                "tier": tier,
                "source": event.source_module,
                "delta": event.delta,
                "expected": event.expected,
                "actual": event.actual,
                "consecutive_cycles": self._consecutive_log_only_cycles,
            },
        )

        if tier == TIER_LOG_ONLY:
            return

        # SOFT / SOFT_ESCALATED / HARD -- require kill_switch.
        if self._kill_switch is None:
            self._log.critical(
                "drift tier=%s but kill_switch=None; escalation not possible, "
                "operator intervention required",
                tier,
            )
            return

        reason = (
            f"capital drift {tier}: delta=Rs{drift_rs:.2f} "
            f"(expected={event.expected:.2f}, actual={event.actual:.2f}, "
            f"source={event.source_module})"
        )
        try:
            if tier == TIER_HARD:
                self._kill_switch.hard_kill(
                    reason=reason,
                    triggered_by="drift_handler",
                )
            else:  # SOFT or SOFT_ESCALATED
                self._kill_switch.soft_kill(
                    reason=reason,
                    triggered_by="drift_handler",
                )
        except Exception as exc:
            log_exception(self._log, exc)
            self._log.critical(
                "drift_handler: kill_switch invocation failed",
                extra={"tier": tier, "error": str(exc)},
            )
