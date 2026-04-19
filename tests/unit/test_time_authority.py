"""
tests/unit/test_time_authority.py

Validates core/time_authority.py:
  - now_ist() returns IST timezone-aware datetime
  - today_ist() returns correct date string
  - assert_clock_at_startup() passes when skew < 30s
  - assert_clock_at_startup() raises ClockSkewTooLarge when skew > 30s
  - assert_clock_at_startup() seeds the skew window
  - record_broker_skew() accumulates readings in deque
  - record_broker_skew() returns correct tier (NORMAL/WARN/ALERT/HALT)
  - HALT tier fires on_critical_skew callback
  - HALT tier requires min 3 samples (skipped with fewer)
  - ALERT and WARN fire their respective callbacks
  - Thread safety: concurrent skew recordings don't crash
  - reset() clears all state
  - configure() updates thresholds and callbacks
  - Naive broker timestamps are treated as IST

Run: python -m pytest tests/unit/test_time_authority.py -v
Or:  python tests/unit/test_time_authority.py  (standalone mode)
"""

from __future__ import annotations

import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.time_authority import (
    ClockSkewTooLarge,
    SkewResult,
    assert_clock_at_startup,
    configure,
    get_skew_status,
    ist_timezone,
    now_ist,
    now_ist_iso,
    record_broker_skew,
    reset,
    today_ist,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

IST = ist_timezone()


def broker_ts_offset(offset_sec: float) -> datetime:
    """Return a broker timestamp that is offset_sec ahead of now_ist()."""
    return now_ist() + timedelta(seconds=offset_sec)


def setup() -> None:
    """Reset time_authority state before each test."""
    reset()


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Basic time functions
# ─────────────────────────────────────────────────────────────────────────────

def test_now_ist_returns_ist_aware_datetime() -> None:
    setup()
    now = now_ist()
    assert now.tzinfo is not None, "now_ist() returned naive datetime"
    offset = now.utcoffset()
    assert offset == timedelta(hours=5, minutes=30), \
        f"Expected UTC+05:30, got {offset}"
    print(f"  OK now_ist() is IST-aware: {now.isoformat()}")


def test_today_ist_returns_date_string() -> None:
    setup()
    today = today_ist()
    assert len(today) == 10, f"Expected YYYY-MM-DD (10 chars), got {len(today)}"
    assert today[4] == "-" and today[7] == "-", f"Bad format: {today}"
    print(f"  OK today_ist() = {today}")


def test_now_ist_iso_returns_iso_string() -> None:
    setup()
    iso = now_ist_iso()
    assert "T" in iso, f"Not ISO format: {iso}"
    assert "+05:30" in iso, f"Missing IST offset: {iso}"
    print(f"  OK now_ist_iso() = {iso}")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Startup check
# ─────────────────────────────────────────────────────────────────────────────

def test_startup_check_passes_within_tolerance() -> None:
    setup()
    broker_ts = broker_ts_offset(2.0)  # 2s ahead — well within 30s
    skew = assert_clock_at_startup(broker_ts)
    assert abs(skew) < 5.0, f"Unexpected skew: {skew}"
    print(f"  OK Startup check passed with skew={skew:+.1f}s")


def test_startup_check_raises_on_large_skew() -> None:
    setup()
    broker_ts = broker_ts_offset(45.0)  # 45s ahead — exceeds 30s
    raised = False
    try:
        assert_clock_at_startup(broker_ts)
    except ClockSkewTooLarge as e:
        raised = True
        assert abs(e.context["skew_seconds"]) > 30, \
            f"Skew too small: {e.context['skew_seconds']}"
        print(f"  OK Startup check raised ClockSkewTooLarge: {e}")

    assert raised, "ClockSkewTooLarge was NOT raised for 45s skew"


def test_startup_check_raises_on_negative_skew() -> None:
    setup()
    broker_ts = broker_ts_offset(-40.0)  # 40s behind
    raised = False
    try:
        assert_clock_at_startup(broker_ts)
    except ClockSkewTooLarge as e:
        raised = True
        print(f"  OK Negative skew also caught: {e.context['skew_seconds']:+.1f}s")

    assert raised, "ClockSkewTooLarge was NOT raised for -40s skew"


def test_startup_check_seeds_skew_window() -> None:
    setup()
    broker_ts = broker_ts_offset(1.0)
    assert_clock_at_startup(broker_ts)
    status = get_skew_status()
    assert status["sample_count"] == 1, \
        f"Expected 1 sample in window, got {status['sample_count']}"
    print(f"  OK Startup check seeded window: {status['sample_count']} sample")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Runtime skew recording
# ─────────────────────────────────────────────────────────────────────────────

def test_record_skew_normal_tier() -> None:
    setup()
    result = record_broker_skew(broker_ts_offset(0.5))
    assert result.tier == "NORMAL", f"Expected NORMAL, got {result.tier}"
    assert result.callback_fired is False
    print(f"  OK 0.5s skew -> tier=NORMAL, no callback")


def test_record_skew_warn_tier() -> None:
    setup()
    fired = []
    configure(on_warn_skew=lambda s, r: fired.append(("warn", s, r)))

    # 3s ahead — in WARN range (2-5s)
    result = record_broker_skew(broker_ts_offset(3.0))
    assert result.tier == "WARN", f"Expected WARN, got {result.tier}"
    assert result.callback_fired is True
    assert len(fired) == 1
    print(f"  OK 3s skew -> tier=WARN, callback fired")


def test_record_skew_alert_tier() -> None:
    setup()
    fired = []
    configure(on_alert_skew=lambda s, r: fired.append(("alert", s, r)))

    # 10s ahead — in ALERT range (5-30s)
    result = record_broker_skew(broker_ts_offset(10.0))
    assert result.tier == "ALERT", f"Expected ALERT, got {result.tier}"
    assert result.callback_fired is True
    assert len(fired) == 1
    print(f"  OK 10s skew -> tier=ALERT, callback fired")


def test_halt_tier_requires_min_samples() -> None:
    setup()
    fired = []
    configure(on_critical_skew=lambda s, r: fired.append(("halt", s, r)))

    # Single reading of 35s — should NOT fire halt (needs 3 samples)
    result = record_broker_skew(broker_ts_offset(35.0))
    # With only 1 sample, halt check is skipped; but avg > alert threshold
    assert result.tier != "HALT" or len(fired) == 0, \
        f"HALT fired with only 1 sample"
    print(f"  OK 1 sample at 35s -> tier={result.tier} (HALT skipped, need 3)")


def test_halt_tier_fires_with_enough_samples() -> None:
    setup()
    fired = []
    configure(on_critical_skew=lambda s, r: fired.append(("halt", s, r)))

    # 5 readings all at 35s — avg will be 35s, count >= 3 -> HALT
    for _ in range(5):
        result = record_broker_skew(broker_ts_offset(35.0))

    assert result.tier == "HALT", f"Expected HALT after 5 readings, got {result.tier}"
    assert len(fired) > 0, "on_critical_skew was NOT fired"
    print(f"  OK 5 samples at 35s -> tier=HALT, callback fired {len(fired)} times")


def test_halt_not_fired_when_average_below_threshold() -> None:
    setup()
    fired = []
    configure(on_critical_skew=lambda s, r: fired.append(("halt", s, r)))

    # 9 readings at 1s, then 1 reading at 35s — avg ~4.4s, well below 30s
    for _ in range(9):
        record_broker_skew(broker_ts_offset(1.0))
    result = record_broker_skew(broker_ts_offset(35.0))

    assert result.tier != "HALT", \
        f"HALT fired but avg should be ~4s, got avg={result.avg_abs_skew_sec}"
    assert len(fired) == 0, "on_critical_skew fired on diluted average"
    print(
        f"  OK 9×1s + 1×35s -> avg={result.avg_abs_skew_sec:.1f}s, "
        f"tier={result.tier} (no HALT)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Naive timestamp handling
# ─────────────────────────────────────────────────────────────────────────────

def test_record_skew_local_ref_ts_kwarg_backward_compat() -> None:
    """
    BL-21: record_broker_skew gained an optional local_ref_ts kwarg for
    mid-point round-trip correction. Existing callers (no kwarg) must
    continue to behave exactly as before -- local reference is now_ist()
    at call time.
    """
    setup()
    broker_ts = broker_ts_offset(1.5)

    # Legacy call: positional arg only, no local_ref_ts. Should fall back
    # to now_ist() internally. Skew should be ~1.5s (broker ahead).
    result_legacy = record_broker_skew(broker_ts)
    assert 1.0 < result_legacy.skew_sec < 2.0, (
        f"Legacy path broken: skew={result_legacy.skew_sec}"
    )

    # Explicit local_ref_ts at +1.5s => skew ~0 (we align ref with broker).
    ref_aligned = broker_ts
    result_mid = record_broker_skew(broker_ts, local_ref_ts=ref_aligned)
    assert abs(result_mid.skew_sec) < 0.1, (
        f"local_ref_ts kwarg not honored: skew={result_mid.skew_sec}"
    )
    print(
        "  OK BL-21: local_ref_ts kwarg backward-compatible "
        f"(legacy skew={result_legacy.skew_sec:+.2f}s, "
        f"aligned skew={result_mid.skew_sec:+.3f}s)"
    )


def test_naive_broker_timestamp_treated_as_ist() -> None:
    setup()
    # Build a naive (no tzinfo) IST wall-clock timestamp ~1s ahead of now_ist().
    # The module's contract: naive timestamps are assumed to already be IST.
    from core.time_authority import now_ist
    naive_ts = (now_ist() + timedelta(seconds=1)).replace(tzinfo=None)
    result = record_broker_skew(naive_ts)
    assert abs(result.skew_sec) < 5.0, \
        f"Naive timestamp not handled as IST — skew={result.skew_sec}"
    print(f"  OK Naive broker timestamp treated as IST: skew={result.skew_sec:+.1f}s")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Thread safety
# ─────────────────────────────────────────────────────────────────────────────

def test_concurrent_skew_recordings() -> None:
    setup()
    errors = []

    def writer(thread_id: int) -> None:
        try:
            for i in range(20):
                offset = 0.5 + (thread_id * 0.1) + (i * 0.01)
                record_broker_skew(broker_ts_offset(offset))
        except Exception as e:
            errors.append((thread_id, str(e)))

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Thread errors: {errors}"
    status = get_skew_status()
    assert status["sample_count"] == 10, \
        f"Expected 10 samples (deque maxlen), got {status['sample_count']}"
    print(f"  OK 100 concurrent recordings from 5 threads — no errors, "
          f"window has {status['sample_count']} samples")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — Reset and configure
# ─────────────────────────────────────────────────────────────────────────────

def test_reset_clears_all_state() -> None:
    setup()
    # Add some state
    record_broker_skew(broker_ts_offset(5.0))
    record_broker_skew(broker_ts_offset(5.0))
    assert get_skew_status()["sample_count"] == 2

    reset()
    status = get_skew_status()
    assert status["sample_count"] == 0, "Reset did not clear skew window"
    assert status["current_tier"] == "NORMAL"
    print(f"  OK reset() cleared all state")


def test_configure_updates_thresholds() -> None:
    setup()
    configure(thresholds={"warn_sec": 10.0, "alert_sec": 20.0})
    status = get_skew_status()
    assert status["thresholds"]["warn_sec"] == 10.0
    assert status["thresholds"]["alert_sec"] == 20.0
    assert status["thresholds"]["halt_sec"] == 30.0  # unchanged default
    print(f"  OK configure() updated thresholds: warn=10, alert=20, halt=30")


def test_configure_with_custom_window_size() -> None:
    setup()
    configure(thresholds={"window_size": 5})

    # Fill 7 readings — only last 5 should remain
    for i in range(7):
        record_broker_skew(broker_ts_offset(1.0))

    status = get_skew_status()
    assert status["sample_count"] == 5, \
        f"Expected 5 samples (window_size=5), got {status['sample_count']}"
    print(f"  OK Custom window_size=5 enforced: {status['sample_count']} samples")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — get_skew_status introspection
# ─────────────────────────────────────────────────────────────────────────────

def test_get_skew_status_empty_window() -> None:
    setup()
    status = get_skew_status()
    assert status["sample_count"] == 0
    assert status["current_tier"] == "NORMAL"
    assert status["avg_abs_skew_sec"] == 0.0
    print(f"  OK Empty window -> NORMAL tier, 0 samples")


def test_get_skew_status_after_recordings() -> None:
    setup()
    record_broker_skew(broker_ts_offset(3.0))
    record_broker_skew(broker_ts_offset(3.5))
    status = get_skew_status()
    assert status["sample_count"] == 2
    assert status["avg_abs_skew_sec"] > 2.0
    assert status["current_tier"] == "WARN"
    print(f"  OK After 2 readings at ~3s: tier={status['current_tier']}, "
          f"avg={status['avg_abs_skew_sec']:.1f}s")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_now_ist_returns_ist_aware_datetime,
        test_today_ist_returns_date_string,
        test_now_ist_iso_returns_iso_string,
        test_startup_check_passes_within_tolerance,
        test_startup_check_raises_on_large_skew,
        test_startup_check_raises_on_negative_skew,
        test_startup_check_seeds_skew_window,
        test_record_skew_normal_tier,
        test_record_skew_warn_tier,
        test_record_skew_alert_tier,
        test_halt_tier_requires_min_samples,
        test_halt_tier_fires_with_enough_samples,
        test_halt_not_fired_when_average_below_threshold,
        test_record_skew_local_ref_ts_kwarg_backward_compat,
        test_naive_broker_timestamp_treated_as_ist,
        test_concurrent_skew_recordings,
        test_reset_clears_all_state,
        test_configure_updates_thresholds,
        test_configure_with_custom_window_size,
        test_get_skew_status_empty_window,
        test_get_skew_status_after_recordings,
    ]

    print("=" * 70)
    print("time_authority.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  FAIL ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
