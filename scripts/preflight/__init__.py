"""
scripts/preflight/ -- Trading System v2 Pre-flight check orchestrator (Diary #1+#2).

A daily, multi-phase readiness check that runs BEFORE the market opens:

    Phase A (08:30 IST)  -- deep infra checks (VM, DB, broker token, config, security)
    Phase B (09:14 IST)  -- fast final gate + engine readiness (app is up by now)
    Phase C (09:15-09:20) -- passive signal-warmup watch

Design (locked with Rama, 21-Jun-2026):
  * ALERT-ONLY on CRITICAL -- pre-flight is the eyes, Rama is the gate; trading
    is NEVER auto-blocked by a failed check.
  * AUTO-FIX only on a DB/OS-level safe whitelist; in-memory app state
    (entry throttle / reservations / stuck-signal nudge) is ALERT-ONLY because a
    separate process cannot mutate the running app's memory (the app self-heals
    those at startup / intraday).
  * Phase A runs what is testable while the app is DOWN; engine-readiness +
    signal checks live in Phase B/C (the app auto-starts on token ~08:15-09:00).
  * Broker calls go through broker/zerodha_adapter only; in paper mode the
    broker-reality checks return SKIPPED (no real kite), the check SET is identical.

The 5 checks of the legacy scripts/premarket_healthcheck.py are subsumed here
(parity-tested) and that script is retired after a 2-day live-proof safety net.
"""
from __future__ import annotations

from scripts.preflight.base import (
    Check,
    CheckContext,
    CheckResult,
    Criticality,
    FixResult,
    Status,
)

__all__ = [
    "Check",
    "CheckContext",
    "CheckResult",
    "Criticality",
    "FixResult",
    "Status",
]
