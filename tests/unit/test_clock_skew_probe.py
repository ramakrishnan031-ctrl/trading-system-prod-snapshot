"""
tests/unit/test_clock_skew_probe.py

Validates broker/clock_skew_probe.py (BL-21 / Phase D.2, Path X).

The probe is a thin driver: it periodically calls
adapter.get_server_time() and hands the result to
time_authority.record_broker_skew(). Tier classification,
thresholds, and callback dispatch are tested in
tests/unit/test_time_authority.py -- not here.

Run: python -m pytest tests/unit/test_clock_skew_probe.py -v
Or:  python tests/unit/test_clock_skew_probe.py  (standalone)
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from broker.clock_skew_probe import BrokerClockSkewProbe
from core.config_loader import ClockSkewProbeConfig
from core.exceptions import (
    BrokerAuthError,
    BrokerRateLimit429Error,
    BrokerTimeoutError,
)

_IST = timezone(timedelta(hours=5, minutes=30), name="IST")


def _log() -> logging.Logger:
    return logging.getLogger("test_clock_skew_probe")


def _make_mocks(
    broker_ts: datetime | None = None,
    adapter_side_effect=None,
    now_values: list[datetime] | None = None,
):
    """Return (adapter_mock, time_auth_mock)."""
    adapter = MagicMock()
    if adapter_side_effect is not None:
        adapter.get_server_time.side_effect = adapter_side_effect
    else:
        adapter.get_server_time.return_value = (
            broker_ts if broker_ts is not None else datetime.now(tz=_IST)
        )

    time_auth = MagicMock()
    if now_values is not None:
        time_auth.now_ist.side_effect = list(now_values)
    else:
        time_auth.now_ist.return_value = datetime.now(tz=_IST)
    return adapter, time_auth


def _cfg(enabled: bool = True, interval: int = 60) -> ClockSkewProbeConfig:
    return ClockSkewProbeConfig(enabled=enabled, probe_interval_sec=interval)


# ─────────────────────────────────────────────────────────────────────────────
# Happy-path probing
# ─────────────────────────────────────────────────────────────────────────────

def test_probe_calls_adapter_and_time_authority() -> None:
    broker_ts = datetime.now(tz=_IST)
    adapter, time_auth = _make_mocks(broker_ts=broker_ts)
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(),
        logger=_log(),
    )
    probe._probe_once()
    assert adapter.get_server_time.call_count == 1
    assert time_auth.record_broker_skew.call_count == 1
    kwargs = time_auth.record_broker_skew.call_args.kwargs
    assert kwargs["broker_timestamp"] == broker_ts
    assert kwargs["local_ref_ts"] is not None
    print("  OK BL-21: _probe_once calls adapter once + record_broker_skew once")


def test_probe_passes_midpoint_as_local_ref() -> None:
    """now_ist() returns t0 then t0+500ms. local_ref_ts must be the midpoint."""
    t0 = datetime(2026, 4, 19, 10, 0, 0, tzinfo=_IST)
    t1 = t0 + timedelta(milliseconds=500)
    broker_ts = t0 + timedelta(milliseconds=250)

    adapter, time_auth = _make_mocks(broker_ts=broker_ts, now_values=[t0, t1])
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(),
        logger=_log(),
    )
    probe._probe_once()

    kwargs = time_auth.record_broker_skew.call_args.kwargs
    local_ref = kwargs["local_ref_ts"]
    expected_mid = t0 + timedelta(milliseconds=250)
    assert local_ref == expected_mid, (
        f"Expected midpoint {expected_mid}, got {local_ref}"
    )
    print("  OK BL-21: local_ref_ts is RTT midpoint (not t_before, not t_after)")


# ─────────────────────────────────────────────────────────────────────────────
# Graceful degradation
# ─────────────────────────────────────────────────────────────────────────────

def test_probe_auth_error_logs_error_skips_record() -> None:
    adapter, time_auth = _make_mocks(
        adapter_side_effect=BrokerAuthError("token expired")
    )
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(),
        logger=_log(),
    )
    probe._probe_once()  # must NOT raise
    assert time_auth.record_broker_skew.call_count == 0, (
        "Auth error must not record skew"
    )
    print("  OK BL-21: BrokerAuthError -> no record, no raise")


def test_probe_429_logs_debug_skips_record() -> None:
    adapter, time_auth = _make_mocks(
        adapter_side_effect=BrokerRateLimit429Error(
            "429", operation="get_margins", category="margins",
            delay_sec=0.2, attempt=1,
        )
    )
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(),
        logger=_log(),
    )
    probe._probe_once()
    assert time_auth.record_broker_skew.call_count == 0
    print("  OK BL-21: BrokerRateLimit429Error -> no record, no raise")


def test_probe_timeout_logs_debug_skips_record() -> None:
    adapter, time_auth = _make_mocks(
        adapter_side_effect=BrokerTimeoutError("read timeout")
    )
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(),
        logger=_log(),
    )
    probe._probe_once()
    assert time_auth.record_broker_skew.call_count == 0
    print("  OK BL-21: BrokerTimeoutError -> no record, no raise")


def test_probe_unexpected_error_logs_error_continues() -> None:
    adapter, time_auth = _make_mocks(
        adapter_side_effect=RuntimeError("something weird")
    )
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(),
        logger=_log(),
    )
    probe._probe_once()  # must not raise -- generic Exception is caught
    assert time_auth.record_broker_skew.call_count == 0
    print("  OK BL-21: unexpected Exception -> logged via log_exception, no raise")


# ─────────────────────────────────────────────────────────────────────────────
# Lifecycle / daemon thread
# ─────────────────────────────────────────────────────────────────────────────

def test_probe_daemon_thread_lifecycle() -> None:
    adapter, time_auth = _make_mocks(broker_ts=datetime.now(tz=_IST))
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(interval=5),
        logger=_log(),
    )
    probe.start()
    assert probe._thread is not None
    assert probe._thread.daemon is True
    assert probe._thread.is_alive()
    probe.stop()
    # join timeout=2.0 inside stop(); thread should be dead after that.
    assert not probe._thread.is_alive()
    print("  OK BL-21: thread is daemon; stop() joins within 2s")


def test_probe_disabled_config_skips_start() -> None:
    adapter, time_auth = _make_mocks()
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(enabled=False),
        logger=_log(),
    )
    probe.start()
    assert probe._thread is None, "Disabled config must not create a thread"
    print("  OK BL-21: enabled=False -> no thread created")


def test_probe_start_idempotent() -> None:
    adapter, time_auth = _make_mocks(broker_ts=datetime.now(tz=_IST))
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(interval=5),
        logger=_log(),
    )
    probe.start()
    first = probe._thread
    probe.start()  # second start should be a no-op (thread already alive)
    assert probe._thread is first, "Second start must not replace the thread"
    probe.stop()
    print("  OK BL-21: start() is idempotent (no second thread)")


def test_probe_first_cycle_runs_immediately() -> None:
    """Lock the 'probe first, then sleep' loop ordering."""
    adapter, time_auth = _make_mocks(broker_ts=datetime.now(tz=_IST))
    probe = BrokerClockSkewProbe(
        adapter=adapter,
        time_authority=time_auth,
        config=_cfg(interval=5),  # 5s interval; first probe should beat this
        logger=_log(),
    )
    probe.start()
    # Within a reasonable grace, at least one probe should have run.
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if time_auth.record_broker_skew.call_count >= 1:
            break
        time.sleep(0.02)
    probe.stop()
    assert time_auth.record_broker_skew.call_count >= 1, (
        "First probe must run immediately, not after probe_interval_sec"
    )
    print(
        "  OK BL-21: first probe runs within <1s "
        f"(call_count={time_auth.record_broker_skew.call_count})"
    )


def test_probe_config_validation_interval_must_be_positive() -> None:
    from pydantic import ValidationError
    try:
        ClockSkewProbeConfig(enabled=True, probe_interval_sec=0)
    except ValidationError:
        print("  OK BL-21: probe_interval_sec=0 rejected by validator")
        return
    raise AssertionError("probe_interval_sec=0 must raise ValidationError")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_probe_calls_adapter_and_time_authority,
        test_probe_passes_midpoint_as_local_ref,
        test_probe_auth_error_logs_error_skips_record,
        test_probe_429_logs_debug_skips_record,
        test_probe_timeout_logs_debug_skips_record,
        test_probe_unexpected_error_logs_error_continues,
        test_probe_daemon_thread_lifecycle,
        test_probe_disabled_config_skips_start,
        test_probe_start_idempotent,
        test_probe_first_cycle_runs_immediately,
        test_probe_config_validation_interval_must_be_positive,
    ]
    print("=" * 70)
    print("clock_skew_probe.py -- BL-21 Path X Test Suite")
    print("=" * 70)
    failed = []
    for t in tests:
        print(f"\n-> {t.__name__}")
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL: {e}")
    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)}")
        for n, err in failed:
            print(f"  - {n}: {err}")
        return 1
    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
