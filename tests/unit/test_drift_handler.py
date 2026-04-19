"""
tests/unit/test_drift_handler.py

Validates capital/drift_handler.py against BL-2 (Phase B.4).

Run: python -m pytest tests/unit/test_drift_handler.py -v
Or:  python tests/unit/test_drift_handler.py  (standalone mode)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest

from capital.drift_handler import (
    CapitalDriftHandler,
    TIER_HARD,
    TIER_LOG_ONLY,
    TIER_NOISE,
    TIER_SOFT,
    TIER_SOFT_ESCALATED,
    _ESCALATING_SOURCES,
)
from core.config_loader import DriftHandlerConfig
from core.events import CapitalDriftDetected


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config(
    log_only: float = 250.0,
    soft: float = 1_000.0,
    hard: float = 2_500.0,
    cycles: int = 3,
) -> DriftHandlerConfig:
    return DriftHandlerConfig(
        log_only_threshold_rs=log_only,
        soft_kill_threshold_rs=soft,
        hard_kill_threshold_rs=hard,
        consecutive_cycles_before_escalate=cycles,
    )


class _FakeKillSwitch:
    """Records soft_kill / hard_kill invocations (mirrors BL-9 fake)."""

    def __init__(self, raise_on_soft: Optional[Exception] = None,
                 raise_on_hard: Optional[Exception] = None) -> None:
        self.hard_kill_calls: list[dict] = []
        self.soft_kill_calls: list[dict] = []
        self._raise_on_soft = raise_on_soft
        self._raise_on_hard = raise_on_hard

    def hard_kill(self, reason: str, triggered_by: str = "system"):
        self.hard_kill_calls.append({"reason": reason, "triggered_by": triggered_by})
        if self._raise_on_hard is not None:
            raise self._raise_on_hard
        return None

    def soft_kill(self, reason: str, triggered_by: str = "system"):
        self.soft_kill_calls.append({"reason": reason, "triggered_by": triggered_by})
        if self._raise_on_soft is not None:
            raise self._raise_on_soft


def _fm_event(delta: float, expected: float = 100_000.0) -> CapitalDriftDetected:
    """Build an event as fund_manager would publish (escalating source)."""
    return CapitalDriftDetected(
        source_module="fund_manager",
        expected=expected,
        actual=expected + delta,
        delta=delta,
    )


def _reconciler_event(
    delta: float, expected: float = 10.0, source: str = "order_reconciler",
) -> CapitalDriftDetected:
    """Build an event as the reconciler would publish (non-escalating)."""
    return CapitalDriftDetected(
        source_module=source,
        expected=expected,
        actual=expected + delta,
        delta=delta,
    )


def _make_handler(
    kill_switch=None,
    config: Optional[DriftHandlerConfig] = None,
    logger: Optional[logging.Logger] = None,
) -> CapitalDriftHandler:
    return CapitalDriftHandler(
        config=config or _default_config(),
        kill_switch=kill_switch,
        logger=logger or logging.getLogger("test_drift_handler"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tier dispatch tests
# ─────────────────────────────────────────────────────────────────────────────

def test_noise_tier_no_log_no_kill(caplog) -> None:
    """Drift below log_only threshold: no CRITICAL log, no kill invocation."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    with caplog.at_level(logging.DEBUG, logger="test_drift_handler"):
        h.on_drift(_fm_event(delta=100.0))  # below 250 log_only

    assert ks.hard_kill_calls == []
    assert ks.soft_kill_calls == []
    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert critical_records == [], "NOISE tier must not emit CRITICAL logs"
    print("  OK NOISE tier: no log, no kill (BL-2)")


def test_log_only_tier_critical_log_no_kill(caplog) -> None:
    """Drift in [log_only, soft_kill): CRITICAL log emitted, no kill."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    with caplog.at_level(logging.CRITICAL, logger="test_drift_handler"):
        h.on_drift(_fm_event(delta=500.0))  # >=250 <1000

    assert ks.hard_kill_calls == []
    assert ks.soft_kill_calls == []
    assert h._consecutive_log_only_cycles == 1
    critical_records = [r for r in caplog.records if r.levelno == logging.CRITICAL]
    assert any("capital drift detected" in r.message for r in critical_records)
    print("  OK LOG_ONLY tier: CRITICAL log, no kill, counter=1 (BL-2)")


def test_soft_kill_tier_invokes_soft_kill() -> None:
    """Drift in [soft_kill, hard_kill): kill_switch.soft_kill called."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    h.on_drift(_fm_event(delta=1_500.0))  # >=1000 <2500

    assert len(ks.soft_kill_calls) == 1
    assert ks.hard_kill_calls == []
    call = ks.soft_kill_calls[0]
    assert call["triggered_by"] == "drift_handler"
    assert TIER_SOFT in call["reason"]
    print("  OK SOFT tier: soft_kill called with tier in reason (BL-2)")


def test_hard_kill_tier_invokes_hard_kill() -> None:
    """Drift >= hard_kill threshold: kill_switch.hard_kill called."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    h.on_drift(_fm_event(delta=3_000.0))  # >=2500

    assert len(ks.hard_kill_calls) == 1
    assert ks.soft_kill_calls == []
    call = ks.hard_kill_calls[0]
    assert call["triggered_by"] == "drift_handler"
    assert TIER_HARD in call["reason"]
    print("  OK HARD tier: hard_kill called (BL-2)")


# ─────────────────────────────────────────────────────────────────────────────
# Consecutive-cycle escalation tests
# ─────────────────────────────────────────────────────────────────────────────

def test_consecutive_log_only_escalates_to_soft() -> None:
    """N consecutive LOG_ONLY cycles (default N=3) -> SOFT_ESCALATED."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    h.on_drift(_fm_event(delta=500.0))  # cycle 1
    h.on_drift(_fm_event(delta=500.0))  # cycle 2
    assert ks.soft_kill_calls == []
    assert h._consecutive_log_only_cycles == 2

    h.on_drift(_fm_event(delta=500.0))  # cycle 3 -> SOFT_ESCALATED

    assert len(ks.soft_kill_calls) == 1, "3rd LOG_ONLY must escalate to soft_kill"
    assert TIER_SOFT_ESCALATED in ks.soft_kill_calls[0]["reason"]
    assert ks.hard_kill_calls == []
    print("  OK 3 consecutive LOG_ONLY cycles escalate to SOFT_ESCALATED (BL-2)")


def test_consecutive_counter_resets_on_noise() -> None:
    """NOISE tier must reset the counter to 0."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    h.on_drift(_fm_event(delta=500.0))   # LOG_ONLY, counter=1
    h.on_drift(_fm_event(delta=500.0))   # LOG_ONLY, counter=2
    assert h._consecutive_log_only_cycles == 2

    h.on_drift(_fm_event(delta=100.0))   # NOISE, resets to 0
    assert h._consecutive_log_only_cycles == 0

    h.on_drift(_fm_event(delta=500.0))   # LOG_ONLY again, counter=1
    assert h._consecutive_log_only_cycles == 1
    assert ks.soft_kill_calls == []
    print("  OK counter resets on NOISE tier (BL-2)")


def test_consecutive_counter_resets_on_soft_tier() -> None:
    """SOFT tier must reset the counter to 0."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    h.on_drift(_fm_event(delta=500.0))   # LOG_ONLY, counter=1
    h.on_drift(_fm_event(delta=500.0))   # LOG_ONLY, counter=2

    h.on_drift(_fm_event(delta=1_500.0))  # SOFT, counter reset + soft_kill
    assert h._consecutive_log_only_cycles == 0
    assert len(ks.soft_kill_calls) == 1

    h.on_drift(_fm_event(delta=500.0))   # LOG_ONLY, counter=1 (fresh start)
    assert h._consecutive_log_only_cycles == 1
    assert len(ks.soft_kill_calls) == 1, "no new soft_kill from single log_only"
    print("  OK counter resets on SOFT tier (BL-2)")


# ─────────────────────────────────────────────────────────────────────────────
# Source-module filtering tests (DH1)
# ─────────────────────────────────────────────────────────────────────────────

def test_reconciler_sourced_event_logs_info_only(caplog) -> None:
    """Event from order_reconciler: INFO log, no kill, counter untouched."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    with caplog.at_level(logging.INFO, logger="test_drift_handler"):
        # CHECK5 POSITION_GREW shape: integer qty, not rupees at all.
        h.on_drift(_reconciler_event(delta=3.0, expected=10.0))

    assert ks.hard_kill_calls == []
    assert ks.soft_kill_calls == []
    assert h._consecutive_log_only_cycles == 0, (
        "counter must NOT be touched by non-escalating source"
    )
    info_records = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("non-escalating source" in r.message for r in info_records)
    print("  OK reconciler-sourced event: INFO-only, counter untouched (BL-2, DH1)")


def test_non_escalating_source_does_not_escalate_or_reset_counter() -> None:
    """A reconciler event arriving mid-sequence must NOT reset the counter."""
    ks = _FakeKillSwitch()
    h = _make_handler(kill_switch=ks)

    # Build up counter via fund_manager events
    h.on_drift(_fm_event(delta=500.0))  # counter=1
    h.on_drift(_fm_event(delta=500.0))  # counter=2
    assert h._consecutive_log_only_cycles == 2

    # Reconciler event arrives; counter must NOT change
    h.on_drift(_reconciler_event(delta=5_000.0))  # would be HARD if escalating
    assert h._consecutive_log_only_cycles == 2, (
        "non-escalating event must not reset fund_manager counter"
    )
    assert ks.hard_kill_calls == [], "reconciler event must not trigger hard_kill"

    # Third fund_manager LOG_ONLY triggers escalation as if the reconciler
    # event hadn't happened at all.
    h.on_drift(_fm_event(delta=500.0))  # counter=3 -> SOFT_ESCALATED
    assert len(ks.soft_kill_calls) == 1
    assert TIER_SOFT_ESCALATED in ks.soft_kill_calls[0]["reason"]
    print("  OK reconciler event mid-sequence preserves fund_manager counter (BL-2, DH4)")


# ─────────────────────────────────────────────────────────────────────────────
# Robustness tests
# ─────────────────────────────────────────────────────────────────────────────

def test_subscriber_exception_does_not_propagate(caplog) -> None:
    """
    BL-2 + EV4: EventBus raises EventDispatchError if a subscriber
    exception propagates. drift_handler.on_drift MUST swallow its
    own failures to avoid crashing fund_manager.sync_from_broker
    or the reconciler publish thread.

    DO NOT remove the try/except in on_drift without first confirming
    EV4 policy has changed.
    """
    ks = _FakeKillSwitch(raise_on_soft=RuntimeError("kill engine down"))
    h = _make_handler(kill_switch=ks)

    with caplog.at_level(logging.CRITICAL, logger="test_drift_handler"):
        # Must not raise -- the handler catches internally.
        h.on_drift(_fm_event(delta=1_500.0))  # SOFT tier -> soft_kill raises

    # soft_kill was called (kill path entered) but the exception was swallowed
    # inside _dispatch. The CRITICAL log about kill_switch invocation failure
    # must have been emitted.
    assert len(ks.soft_kill_calls) == 1
    assert any(
        "kill_switch invocation failed" in r.message
        for r in caplog.records
    ), "failure of kill_switch must be logged CRITICAL"
    print("  OK kill_switch exception swallowed, CRITICAL logged, no propagation (BL-2, EV4)")


def test_kill_switch_none_logs_escalation_impossible(caplog) -> None:
    """kill_switch=None: HARD tier logs 'escalation not possible', no crash."""
    h = _make_handler(kill_switch=None)

    with caplog.at_level(logging.CRITICAL, logger="test_drift_handler"):
        h.on_drift(_fm_event(delta=5_000.0))  # HARD tier, no kill_switch

    # No AttributeError; CRITICAL about escalation-not-possible logged.
    assert any(
        "escalation not possible" in r.message
        for r in caplog.records
    )
    print("  OK kill_switch=None: CRITICAL log, graceful degradation (BL-2, DH5)")


def test_drift_handler_config_validation() -> None:
    """Config validator: thresholds positive AND log_only < soft < hard."""
    # negative threshold
    with pytest.raises(ValueError, match="must be > 0"):
        DriftHandlerConfig(
            log_only_threshold_rs=-1.0,
            soft_kill_threshold_rs=1_000.0,
            hard_kill_threshold_rs=2_500.0,
            consecutive_cycles_before_escalate=3,
        )

    # misordered thresholds (soft < log_only)
    with pytest.raises(ValueError, match="log_only < soft_kill < hard_kill"):
        DriftHandlerConfig(
            log_only_threshold_rs=1_000.0,
            soft_kill_threshold_rs=500.0,
            hard_kill_threshold_rs=2_500.0,
            consecutive_cycles_before_escalate=3,
        )

    # cycles < 1
    with pytest.raises(ValueError, match="consecutive_cycles_before_escalate"):
        DriftHandlerConfig(
            log_only_threshold_rs=250.0,
            soft_kill_threshold_rs=1_000.0,
            hard_kill_threshold_rs=2_500.0,
            consecutive_cycles_before_escalate=0,
        )

    # valid config succeeds
    cfg = _default_config()
    assert cfg.log_only_threshold_rs == 250.0
    print("  OK config validation: positive, ordered, cycles>=1 (BL-2)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    """Standalone runner (no pytest fixtures). Caplog-based tests skip."""
    caplog_dependent = {
        "test_noise_tier_no_log_no_kill",
        "test_log_only_tier_critical_log_no_kill",
        "test_reconciler_sourced_event_logs_info_only",
        "test_subscriber_exception_does_not_propagate",
        "test_kill_switch_none_logs_escalation_impossible",
    }
    tests = [
        test_noise_tier_no_log_no_kill,
        test_log_only_tier_critical_log_no_kill,
        test_soft_kill_tier_invokes_soft_kill,
        test_hard_kill_tier_invokes_hard_kill,
        test_consecutive_log_only_escalates_to_soft,
        test_consecutive_counter_resets_on_noise,
        test_consecutive_counter_resets_on_soft_tier,
        test_reconciler_sourced_event_logs_info_only,
        test_non_escalating_source_does_not_escalate_or_reset_counter,
        test_subscriber_exception_does_not_propagate,
        test_kill_switch_none_logs_escalation_impossible,
        test_drift_handler_config_validation,
    ]

    print("=" * 70)
    print("drift_handler.py -- Test Suite")
    print("=" * 70)

    failed: list[tuple[str, str]] = []
    skipped = 0
    for test in tests:
        print(f"\n-> {test.__name__}")
        if test.__name__ in caplog_dependent:
            print("  SKIP (requires pytest caplog fixture)")
            skipped += 1
            continue
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests ({skipped} skipped)")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: {len(tests) - skipped} of {len(tests)} tests ({skipped} skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
