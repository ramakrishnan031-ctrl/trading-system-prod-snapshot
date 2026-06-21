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
    database,
    recovery,
    security,
    services,
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
        *state.CHECKS,
        *security.CHECKS,
        *recovery.CHECKS,
    ]
