"""
scripts/preflight/checks/ -- one module per check group.

Each module exposes a module-level ``CHECKS`` list of instantiated Check objects.
The orchestrator composes the phase check-sets from these (Fork 1: infra in
Phase A; engine-readiness + signals in Phase B/C).
"""
from __future__ import annotations

from typing import List

from scripts.preflight.base import Check
from scripts.preflight.checks import (
    broker,
    config_integrity,
    config_sanity,
    database,
    engine,
    recovery,
    security,
    services,
    signals,
    state,
    vm_health,
)


def phase_a_checks() -> List[Check]:
    """Phase A (08:30) -- everything testable while the trading app is DOWN."""
    return [
        *vm_health.CHECKS,
        *services.CHECKS,
        *database.CHECKS,
        *broker.CHECKS,
        *config_integrity.CHECKS,
        *config_sanity.CHECKS,    # BUILD 2: Config Sanity Auditor (groups A-G)
        *state.CHECKS,
        *security.CHECKS,
        *recovery.CHECKS,
    ]


def phase_b_checks() -> List[Check]:
    """Phase B (09:14) -- the app is UP (token-watcher started it). Engine readiness
    via the app's HTTP surface + a fast re-gate of the time-sensitive Phase-A checks."""
    return [
        # app process up -- ALERT-ONLY (token-watcher owns lifecycle; never auto-start)
        services.ServiceActiveCheck("trading-system.service", auto_fixable=False),
        *engine.CHECKS,                 # app_health, app_metrics, fund_manager_balance, vm_ntp_strict
        # fast re-gate: token still fresh, public IP unchanged, no new resting orders
        broker.TokenFreshCheck(),
        broker.VmIpUnchangedCheck(),
        state.OpenOrdersCheck(),
    ]


def phase_c_checks() -> List[Check]:
    """Phase C (09:15-09:20) -- passive signal-warmup watch (the orchestrator
    re-samples these until ~09:20). Read-only; no synthetic signal injected."""
    return [*signals.CHECKS]
