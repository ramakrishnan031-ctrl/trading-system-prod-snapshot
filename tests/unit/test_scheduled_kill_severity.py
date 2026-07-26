"""tests/unit/test_scheduled_kill_severity.py -- SK-B (26-Jul-2026).

A SCHEDULED kill is a normal daily event and must not page like an emergency.

`soft_kill()` sent its HALT alert at CRITICAL unconditionally, so the 15:15 circuit
breaker and the EOD squareoff -- both of which fire on a timer every trading day --
arrived at the same severity as a genuine trading halt. That accounted for 5 of the
CRITICALs in the 82-alert review.

⭐ THE TAXONOMY ALREADY EXISTS AND IS ALREADY TRUSTED FOR MORE.
`SCHEDULED_KILL_REASONS` (kill_switch.py:114) is what `auto_clear_scheduled_kill()`
consults to decide whether a persisted kill SILENTLY CLEARS ITSELF at the next 08:15
boot -- i.e. whether the system resumes trading unattended. Reusing the same predicate
to choose a severity is a strictly SMALLER trust than the one already placed in it.
No new classification was invented.

⚠️ THE THING THAT MUST NOT BREAK: an EMERGENCY kill must stay CRITICAL, because
CRITICAL is the only severity with the email-fallback leg (FIX-191, 23-Jun-2026) --
a WARNING drops silently when Telegram is down. The branch is exact string equality
against a 2-element frozenset, and every emergency caller in the tree passes a reason
that cannot equal either literal:

    main.py:665            f"{source}: {reason}"                 (composite)
    kill_switch.py:775     "Auto-trip: N consecutive API failures ..."
    system_manager.py:913  f"System Manager EOD {day}: ..."      (prefixed)
    cnc_gtt_monitor.py:607 f"cnc_gtt_monitor: {reason}"          (prefixed)
    live_feed.py:303/474/648  LIVEFEED_QUEUE_FULL / _RECONNECT_EXHAUSTED / _CONSUMER_THREAD_DEAD

⭐ AND IT DOES NOT GO QUIET. The alert still SENDS, at WARNING -- it is not
suppressed. Independently, the 15:15 event already carries a dedicated and MORE
informative WARNING from a DIFFERENT module (`main.py:699`, "CIRCUIT BREAKER -- Force
Close ... EOD squareoff will close all positions at 15:17"), and the kill is still
logged CRITICAL to the log (`kill_switch.py:562`) and persisted to
`kill_switch_state`. So a downgrade here cannot manufacture a silent success.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capital.kill_switch import (
    SCHEDULED_KILL_REASONS,
    KillState,
    KillSwitch,
)
from core.events import EventBus
from core.state_store import StateStore


# The reason strings the two SCHEDULED callers actually pass, read from source:
#   main.py:694            -> "circuit_breaker_force_close_15:15"
#   eod_squareoff.py:378   -> "EOD_SQUAREOFF"
SCHEDULED = ["circuit_breaker_force_close_15:15", "EOD_SQUAREOFF"]

# The reason strings EMERGENCY callers actually pass, read from source (see docstring).
EMERGENCY = [
    "Auto-trip: 3 consecutive API failures (threshold=3)",
    "LIVEFEED_QUEUE_FULL",
    "LIVEFEED_RECONNECT_EXHAUSTED",
    "LIVEFEED_CONSUMER_THREAD_DEAD",
    "order_reconciler: BrokerAuthError repeated",
    "cnc_gtt_monitor: >1 ACTIVE GTT for trade trd_x -- ownership ambiguity",
    "System Manager EOD 2026-07-27: broker reconciliation FAILED",
    "token_monitor: access token expired",
]


def _ks(tmp_path: Path, mode: str = "live") -> tuple[KillSwitch, MagicMock, StateStore]:
    store = StateStore(tmp_path / "sk_test.db")
    ks = KillSwitch(
        state_store=store,
        bus=EventBus(),
        logger=logging.getLogger("test_scheduled_kill_severity"),
    )
    notifier = MagicMock()
    ks.set_notifier(notifier, mode=mode)
    return ks, notifier, store


def _severity_of(notifier: MagicMock) -> Any:
    assert notifier.send.call_count == 1, (
        f"expected exactly one halt alert, got {notifier.send.call_count}"
    )
    return notifier.send.call_args.kwargs["severity"]


# ── the downgrade itself ────────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", SCHEDULED)
def test_scheduled_kill_routes_at_warning_not_critical(tmp_path: Path, reason: str) -> None:
    """RED before the fix: both scheduled reasons sent CRITICAL."""
    ks, notifier, store = _ks(tmp_path)
    try:
        ks.soft_kill(reason=reason, triggered_by="test")
        assert _severity_of(notifier) == "WARNING", (
            f"a scheduled kill ({reason!r}) is a normal daily event; it must not page "
            f"at CRITICAL"
        )
    finally:
        store.close()


def test_parity_paper_mode_scheduled_kill_routes_the_same(tmp_path: Path) -> None:
    """PARITY (Rule #5): severity is a property of the reason, never of the mode."""
    ks, notifier, store = _ks(tmp_path, mode="paper")
    try:
        ks.soft_kill(reason="circuit_breaker_force_close_15:15", triggered_by="test")
        assert _severity_of(notifier) == "WARNING", \
            "paper and live must classify a scheduled kill identically"
    finally:
        store.close()


# ── ⚠️ the thing that must not break ────────────────────────────────────────────

@pytest.mark.parametrize("reason", EMERGENCY)
def test_emergency_kill_still_routes_at_critical(tmp_path: Path, reason: str) -> None:
    """⭐ B3: plant a real emergency reason from every non-scheduled caller in the
    tree and prove the branch cannot swallow it. CRITICAL is the only severity with
    the FIX-191 email-fallback leg, so a swallow here is a silenced halt."""
    ks, notifier, store = _ks(tmp_path)
    try:
        ks.soft_kill(reason=reason, triggered_by="test")
        assert _severity_of(notifier) == "CRITICAL", (
            f"emergency kill {reason!r} MUST stay CRITICAL -- WARNING has no email "
            f"fallback and would drop silently when Telegram is down"
        )
    finally:
        store.close()


@pytest.mark.parametrize("reason", EMERGENCY)
def test_no_real_emergency_reason_can_equal_a_scheduled_literal(reason: str) -> None:
    """The branch is exact equality against a 2-element frozenset. This pins the
    OTHER half of that argument: no reason any emergency caller constructs can
    collide with either literal. Pure predicate check -- no store, no notifier."""
    assert reason not in SCHEDULED_KILL_REASONS, (
        f"{reason!r} would be classified SCHEDULED and lose its CRITICAL routing"
    )


def test_the_scheduled_set_is_exactly_the_two_timer_driven_reasons() -> None:
    """If someone widens SCHEDULED_KILL_REASONS, they silently widen BOTH the
    severity downgrade AND `auto_clear_scheduled_kill`'s unattended-resume decision.
    This makes that a deliberate act with a failing test attached."""
    assert SCHEDULED_KILL_REASONS == frozenset(SCHEDULED), (
        "SCHEDULED_KILL_REASONS changed -- this set also governs whether a persisted "
        "kill auto-clears at the 08:15 boot. Widening it is never severity-only."
    )


# ── ⭐ it must not go QUIET ─────────────────────────────────────────────────────

@pytest.mark.parametrize("reason", SCHEDULED)
def test_scheduled_kill_is_still_alerted_not_suppressed(tmp_path: Path, reason: str) -> None:
    """The requirement was 'not CRITICAL', NOT 'silent'. A scheduled kill that stopped
    happening is a real problem, so the alert must still be emitted and still name the
    reason. Suppression here would be the silent-failure pattern."""
    ks, notifier, store = _ks(tmp_path)
    try:
        ks.soft_kill(reason=reason, triggered_by="test")
        assert notifier.send.call_count == 1, "the scheduled kill must still ALERT"
        kwargs = notifier.send.call_args.kwargs
        assert reason in kwargs["body"] or reason in kwargs["title"], \
            "the alert must still say WHICH kill fired"
        assert kwargs["source_module"] == "kill_switch"
    finally:
        store.close()


@pytest.mark.parametrize("reason", SCHEDULED + EMERGENCY[:2])
def test_kill_mechanics_are_untouched_by_the_severity_branch(tmp_path: Path, reason: str) -> None:
    """Severity/routing ONLY. The state transition and its persistence must be
    identical for scheduled and emergency reasons -- this is the kill path."""
    ks, _notifier, store = _ks(tmp_path)
    try:
        ks.soft_kill(reason=reason, triggered_by="test")
        assert ks.current_state() == KillState.SOFT_KILL, \
            "the severity branch must never alter the kill state"
        row = store.fetch_one("SELECT state, reason FROM kill_switch_state WHERE id=1", ())
        assert row["state"] == "SOFT_KILL"
        assert row["reason"] == reason, "the persisted reason must be untouched"
    finally:
        store.close()


def test_notifier_failure_still_never_crashes_the_kill(tmp_path: Path) -> None:
    """Pre-existing invariant, re-pinned because the branch sits in that try block."""
    ks, notifier, store = _ks(tmp_path)
    try:
        notifier.send.side_effect = RuntimeError("telegram down")
        ks.soft_kill(reason="circuit_breaker_force_close_15:15", triggered_by="test")
        assert ks.current_state() == KillState.SOFT_KILL, \
            "a notifier failure must never prevent or undo the kill"
    finally:
        store.close()
