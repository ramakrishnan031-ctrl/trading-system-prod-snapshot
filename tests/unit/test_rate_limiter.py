"""
tests/unit/test_rate_limiter.py

Validates broker/rate_limiter.py against RL1–RL10 locked decisions:
  - acquire(1) succeeds when bucket full (RL1, RL3)
  - acquire(N > capacity) raises BrokerRateLimitError immediately (RL3)
  - acquire blocks until refill provides enough tokens (RL1, RL3)
  - acquire raises BrokerRateLimitError when max_wait_sec exceeded (RL3, RL4)
  - try_acquire returns False when insufficient, doesn't block (RL5)
  - try_acquire returns True and consumes tokens when sufficient (RL5)
  - independent buckets: draining 'order' doesn't affect 'quote' (RL2)
  - refill rate: 1 token available after ~1/rate_per_sec seconds (RL1, RL8)
  - bucket caps at capacity (no over-refill) (RL1)
  - penalize freezes bucket for duration (RL6)
  - thread safety: 50 concurrent acquires complete, total consumed = 50 (RL7)
  - unknown category raises ValueError (RL2)

Run: python tests/unit/test_rate_limiter.py
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.exceptions import BrokerRateLimitError
from broker.rate_limiter import RateLimiter


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — build lightweight mock BrokerLimitsConfig
# ─────────────────────────────────────────────────────────────────────────────

def _make_bucket_cfg(burst: int, rate_per_sec: int) -> MagicMock:
    cfg = MagicMock()
    cfg.burst = burst
    cfg.rate_per_sec = rate_per_sec
    return cfg


def _make_limits(
    order_burst: int = 8,    order_rate: int = 8,
    quote_burst: int = 1,    quote_rate: int = 1,
    hist_burst: int  = 2,    hist_rate: int  = 2,
    margins_burst: int = 8,  margins_rate: int = 8,
) -> MagicMock:
    limits = MagicMock()
    limits.order      = _make_bucket_cfg(order_burst,   order_rate)
    limits.quote      = _make_bucket_cfg(quote_burst,   quote_rate)
    limits.historical = _make_bucket_cfg(hist_burst,    hist_rate)
    limits.margins    = _make_bucket_cfg(margins_burst, margins_rate)
    return limits


# ─────────────────────────────────────────────────────────────────────────────
# Tests — basic acquire (RL1, RL3)
# ─────────────────────────────────────────────────────────────────────────────

def test_acquire_succeeds_when_bucket_full() -> None:
    rl = RateLimiter(_make_limits())
    # Bucket starts full (burst=8); acquiring 1 should return immediately
    start = time.monotonic()
    rl.acquire("order", 1)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"acquire on full bucket took too long: {elapsed:.3f}s"
    print("  OK acquire(1) returns immediately when bucket full (RL1, RL3)")


def test_acquire_n_exceeds_capacity_raises_immediately() -> None:
    rl = RateLimiter(_make_limits(order_burst=4, order_rate=4))
    start = time.monotonic()
    raised = False
    try:
        rl.acquire("order", 5)  # 5 > capacity of 4
    except BrokerRateLimitError as exc:
        raised = True
        assert exc.context["category"] == "order"
        assert exc.context["requested_tokens"] == 5
        assert exc.context["waited_sec"] == 0.0
    elapsed = time.monotonic() - start
    assert raised, "Expected BrokerRateLimitError for n > capacity"
    assert elapsed < 0.1, f"Should raise immediately, took {elapsed:.3f}s"
    print("  OK acquire(N > capacity) raises BrokerRateLimitError immediately (RL3)")


def test_acquire_blocks_until_refill() -> None:
    # Bucket: capacity=2, rate=20/sec (50ms per token)
    rl = RateLimiter(_make_limits(order_burst=2, order_rate=20))
    # Drain the bucket
    rl.acquire("order", 2)
    # Now acquiring 1 more should block ~50ms until refill
    start = time.monotonic()
    rl.acquire("order", 1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.03, f"Should have waited for refill, elapsed={elapsed:.3f}s"
    assert elapsed < 0.5,   f"Waited too long: {elapsed:.3f}s"
    print(f"  OK acquire blocks until refill (~50ms); actual wait={elapsed*1000:.0f}ms (RL1, RL3)")


def test_acquire_raises_when_max_wait_exceeded() -> None:
    # capacity=1, rate=1/sec (1s refill) but max_wait_sec=0.05s
    rl = RateLimiter(_make_limits(order_burst=1, order_rate=1), max_wait_sec=0.05)
    rl.acquire("order", 1)    # drain the bucket
    start = time.monotonic()
    raised = False
    try:
        rl.acquire("order", 1)
    except BrokerRateLimitError as exc:
        raised = True
        assert exc.context["category"] == "order"
        assert exc.context["waited_sec"] >= 0.04
        assert exc.context["max_wait_sec"] == 0.05
    elapsed = time.monotonic() - start
    assert raised, "Expected BrokerRateLimitError on timeout"
    assert elapsed >= 0.04, f"Should have waited ~50ms, elapsed={elapsed:.3f}s"
    assert elapsed < 0.5,   f"Timeout took too long: {elapsed:.3f}s"
    print(f"  OK acquire raises BrokerRateLimitError after max_wait_sec={0.05}s (RL3, RL4)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — try_acquire (RL5)
# ─────────────────────────────────────────────────────────────────────────────

def test_try_acquire_returns_false_when_insufficient() -> None:
    rl = RateLimiter(_make_limits(order_burst=2, order_rate=1))
    rl.acquire("order", 2)    # drain
    start = time.monotonic()
    result = rl.try_acquire("order", 1)
    elapsed = time.monotonic() - start
    assert result is False, "try_acquire should return False on empty bucket"
    assert elapsed < 0.05, f"try_acquire must not block: elapsed={elapsed:.3f}s"
    print("  OK try_acquire returns False when bucket empty; does not block (RL5)")


def test_try_acquire_returns_true_and_consumes() -> None:
    rl = RateLimiter(_make_limits(order_burst=5, order_rate=5))
    # Bucket starts full (5 tokens)
    result = rl.try_acquire("order", 3)
    assert result is True, "try_acquire should return True when tokens available"
    # Next: only 2 tokens left; requesting 3 should fail
    result2 = rl.try_acquire("order", 3)
    assert result2 is False, "Only 2 tokens left after consuming 3; should return False"
    print("  OK try_acquire returns True, consumes tokens; subsequent call fails (RL5)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — independent buckets (RL2)
# ─────────────────────────────────────────────────────────────────────────────

def test_independent_buckets_drain_order_not_quote() -> None:
    rl = RateLimiter(_make_limits(
        order_burst=3, order_rate=1,
        quote_burst=3, quote_rate=1,
    ))
    # Drain order completely
    rl.acquire("order", 3)
    assert rl.try_acquire("order", 1) is False, "order bucket should be empty"
    # quote bucket must be untouched
    assert rl.try_acquire("quote", 1) is True, "quote bucket must be independent"
    print("  OK Draining 'order' bucket does not affect 'quote' bucket (RL2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — refill rate (RL1, RL8)
# ─────────────────────────────────────────────────────────────────────────────

def test_refill_rate_one_token_after_expected_duration() -> None:
    # capacity=1, rate=10/sec → 1 token refills in ~100ms
    rl = RateLimiter(_make_limits(order_burst=1, order_rate=10))
    rl.acquire("order", 1)    # drain
    assert rl.try_acquire("order", 1) is False, "should be empty immediately after drain"
    time.sleep(0.12)           # wait ~120ms (10% margin over 100ms refill)
    result = rl.try_acquire("order", 1)
    assert result is True, "1 token should be available after ~120ms with rate=10/sec"
    print("  OK 1 token available after ~120ms at rate=10/sec (RL1, RL8)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — bucket caps at capacity (RL1)
# ─────────────────────────────────────────────────────────────────────────────

def test_bucket_caps_at_capacity() -> None:
    # capacity=3, rate=100/sec
    rl = RateLimiter(_make_limits(order_burst=3, order_rate=100))
    # Drain 1 token so bucket is at 2
    rl.acquire("order", 1)
    # Sleep much longer than needed to refill from 2 → 3 (3/100 = 30ms; sleep 200ms)
    time.sleep(0.2)
    # Bucket should be at capacity=3 (not > 3); acquire(3) succeeds, acquire(1) then fails
    rl.acquire("order", 3)
    result = rl.try_acquire("order", 1)
    assert result is False, "Bucket should be empty after taking capacity tokens"
    print("  OK Bucket caps at capacity; no over-refill (RL1)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — penalize (RL6)
# ─────────────────────────────────────────────────────────────────────────────

def test_penalize_freezes_bucket_during_duration() -> None:
    rl = RateLimiter(_make_limits(order_burst=5, order_rate=5))
    # Penalize for 150ms
    rl.penalize("order", 0.15)
    # Immediately after penalize, try_acquire must return False (bucket is frozen)
    result_during = rl.try_acquire("order", 1)
    assert result_during is False, "try_acquire must return False during freeze"
    # After freeze ends, tokens are available again
    time.sleep(0.2)
    result_after = rl.try_acquire("order", 1)
    assert result_after is True, "try_acquire must succeed after freeze expires"
    print("  OK penalize freezes bucket during duration; try_acquire succeeds after (RL6)")


def test_penalize_acquire_blocks_for_freeze_duration() -> None:
    # capacity=1, rate=100/sec; freeze for 100ms; acquire should block until freeze clears
    rl = RateLimiter(_make_limits(order_burst=1, order_rate=100), max_wait_sec=1.0)
    rl.penalize("order", 0.1)
    start = time.monotonic()
    rl.acquire("order", 1)
    elapsed = time.monotonic() - start
    assert elapsed >= 0.08, f"acquire should have waited for freeze; elapsed={elapsed:.3f}s"
    assert elapsed < 0.5,   f"acquire waited too long: {elapsed:.3f}s"
    print(f"  OK acquire blocks for freeze duration (~100ms); actual={elapsed*1000:.0f}ms (RL6)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — thread safety (RL7)
# ─────────────────────────────────────────────────────────────────────────────

def test_thread_safety_concurrent_acquires() -> None:
    # Large capacity + high rate so 50 acquires complete without blocking long
    rl = RateLimiter(_make_limits(order_burst=50, order_rate=50), max_wait_sec=10.0)
    success_count = [0]
    lock = threading.Lock()
    errors: list[Exception] = []

    def worker(n_acquires: int) -> None:
        for _ in range(n_acquires):
            try:
                rl.acquire("order", 1)
                with lock:
                    success_count[0] += 1
            except Exception as exc:
                with lock:
                    errors.append(exc)

    threads = [threading.Thread(target=worker, args=(10,)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert not errors, f"Thread errors: {errors}"
    assert success_count[0] == 50, (
        f"Expected 50 successful acquires, got {success_count[0]}"
    )
    print("  OK 50 concurrent acquires from 5 threads: all complete, total consumed = 50 (RL7)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — unknown category (RL2)
# ─────────────────────────────────────────────────────────────────────────────

def test_unknown_category_acquire_raises_value_error() -> None:
    rl = RateLimiter(_make_limits())
    raised = False
    try:
        rl.acquire("unknown_endpoint")
    except ValueError as exc:
        raised = True
        assert "unknown_endpoint" in str(exc)
    assert raised, "Expected ValueError for unknown category"
    print("  OK acquire with unknown category raises ValueError (RL2)")


def test_unknown_category_try_acquire_raises_value_error() -> None:
    rl = RateLimiter(_make_limits())
    raised = False
    try:
        rl.try_acquire("bad_category")
    except ValueError:
        raised = True
    assert raised, "Expected ValueError from try_acquire with unknown category"
    print("  OK try_acquire with unknown category raises ValueError (RL2)")


def test_unknown_category_penalize_raises_value_error() -> None:
    rl = RateLimiter(_make_limits())
    raised = False
    try:
        rl.penalize("nonexistent", 1.0)
    except ValueError:
        raised = True
    assert raised, "Expected ValueError from penalize with unknown category"
    print("  OK penalize with unknown category raises ValueError (RL2)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — BrokerRateLimitError context fields (RL9)
# ─────────────────────────────────────────────────────────────────────────────

def test_broker_rate_limit_error_context_fields() -> None:
    rl = RateLimiter(_make_limits(quote_burst=1, quote_rate=1), max_wait_sec=0.05)
    rl.acquire("quote", 1)    # drain
    exc_caught = None
    try:
        rl.acquire("quote", 1)
    except BrokerRateLimitError as exc:
        exc_caught = exc

    assert exc_caught is not None
    ctx = exc_caught.context
    assert ctx["category"] == "quote"
    assert isinstance(ctx["requested_tokens"], int)
    assert isinstance(ctx["available_tokens"], float)
    assert isinstance(ctx["waited_sec"], float)
    assert ctx["max_wait_sec"] == 0.05
    assert exc_caught.SEVERITY == "WARN"
    print("  OK BrokerRateLimitError carries full context and SEVERITY=WARN (RL9)")


# ─────────────────────────────────────────────────────────────────────────────
# Tests — thaw logging (MED #4)
# ─────────────────────────────────────────────────────────────────────────────

def test_thaw_logged_once_after_freeze_expires() -> None:
    """MED #4: acquire() after freeze expiry logs INFO 'thawed' exactly once."""
    import logging
    from unittest.mock import patch, MagicMock as MM

    rl = RateLimiter(_make_limits(order_burst=5, order_rate=100), max_wait_sec=1.0)
    rl.penalize("order", 0.05)  # 50ms freeze

    logged_calls: list = []

    original_info = rl._buckets["order"].__class__  # unused — patch at module level

    with patch("broker.rate_limiter._log") as mock_log:
        mock_log.info = MM(side_effect=lambda *a, **kw: logged_calls.append(a))
        mock_log.warning = MM()
        # Wait for freeze to expire, then acquire
        time.sleep(0.1)
        rl.acquire("order", 1)
        rl.acquire("order", 1)  # second acquire must not log again

    thaw_calls = [c for c in logged_calls if "thawed" in str(c)]
    assert len(thaw_calls) == 1, (
        f"Expected exactly 1 thaw log; got {len(thaw_calls)}: {thaw_calls}"
    )
    print("  OK thaw logged exactly once after freeze expiry (MED #4)")


def test_no_thaw_log_without_prior_freeze() -> None:
    """MED #4: No thaw log when bucket was never frozen."""
    from unittest.mock import patch, MagicMock as MM

    rl = RateLimiter(_make_limits(order_burst=5, order_rate=100), max_wait_sec=1.0)

    logged_calls: list = []
    with patch("broker.rate_limiter._log") as mock_log:
        mock_log.info = MM(side_effect=lambda *a, **kw: logged_calls.append(a))
        mock_log.warning = MM()
        rl.acquire("order", 1)
        rl.acquire("order", 1)

    thaw_calls = [c for c in logged_calls if "thawed" in str(c)]
    assert len(thaw_calls) == 0, f"Unexpected thaw log without freeze: {thaw_calls}"
    print("  OK no thaw log when bucket was never frozen (MED #4)")


def test_fix060_shutdown_event_aborts_acquire() -> None:
    """FIX-060: shutdown_event fires -> acquire() raises RateLimitAbortedError."""
    import threading
    from core.exceptions import RateLimitAbortedError

    shutdown_event = threading.Event()
    # Create limiter with very slow refill so acquire() would normally block
    rl = RateLimiter(_make_limits(order_burst=1, order_rate=0.1), shutdown_event=shutdown_event)
    rl.acquire("order", 1)  # drain the single token

    # Set shutdown event in 0.05s, then try to acquire
    timer = threading.Timer(0.05, shutdown_event.set)
    timer.start()

    try:
        rl.acquire("order", 1)  # should raise RateLimitAbortedError
        assert False, "Expected RateLimitAbortedError but acquire succeeded"
    except RateLimitAbortedError as e:
        assert e.context["category"] == "order"
        assert e.context["waited_sec"] >= 0.0
    finally:
        timer.cancel()

    print("  OK FIX-060 shutdown_event aborts acquire()")


def test_fix060_shutdown_event_none_uses_normal_sleep() -> None:
    """FIX-060: shutdown_event=None falls back to normal time.sleep behavior."""
    # No shutdown_event passed
    rl = RateLimiter(_make_limits(order_burst=2, order_rate=100))
    rl.acquire("order", 1)
    rl.acquire("order", 1)  # should succeed without any shutdown checks
    print("  OK FIX-060 shutdown_event=None uses normal sleep")


def test_fix060_acquire_aborted_error_has_correct_context() -> None:
    """FIX-060: RateLimitAbortedError includes category and waited_sec."""
    import threading
    import time
    from core.exceptions import RateLimitAbortedError

    shutdown_event = threading.Event()
    rl = RateLimiter(_make_limits(quote_burst=1, quote_rate=0.1), shutdown_event=shutdown_event)
    rl.acquire("quote", 1)  # drain

    # Fire shutdown after 0.05s
    timer = threading.Timer(0.05, shutdown_event.set)
    timer.start()
    start = time.monotonic()

    try:
        rl.acquire("quote", 1)
        assert False, "Expected RateLimitAbortedError"
    except RateLimitAbortedError as e:
        elapsed = time.monotonic() - start
        assert e.context["category"] == "quote"
        assert 0.03 <= e.context["waited_sec"] <= 0.2, (
            f"waited_sec should be ~0.05s, got {e.context['waited_sec']}"
        )
        assert 0.03 <= elapsed <= 0.2, f"elapsed should be ~0.05s, got {elapsed}"
    finally:
        timer.cancel()

    print("  OK FIX-060 RateLimitAbortedError has correct context")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_acquire_succeeds_when_bucket_full,
        test_acquire_n_exceeds_capacity_raises_immediately,
        test_acquire_blocks_until_refill,
        test_acquire_raises_when_max_wait_exceeded,
        test_try_acquire_returns_false_when_insufficient,
        test_try_acquire_returns_true_and_consumes,
        test_independent_buckets_drain_order_not_quote,
        test_refill_rate_one_token_after_expected_duration,
        test_bucket_caps_at_capacity,
        test_penalize_freezes_bucket_during_duration,
        test_penalize_acquire_blocks_for_freeze_duration,
        test_thread_safety_concurrent_acquires,
        test_unknown_category_acquire_raises_value_error,
        test_unknown_category_try_acquire_raises_value_error,
        test_unknown_category_penalize_raises_value_error,
        test_broker_rate_limit_error_context_fields,
        test_thaw_logged_once_after_freeze_expires,
        test_no_thaw_log_without_prior_freeze,
        test_fix060_shutdown_event_aborts_acquire,
        test_fix060_shutdown_event_none_uses_normal_sleep,
        test_fix060_acquire_aborted_error_has_correct_context,
    ]

    print("=" * 70)
    print("rate_limiter.py — Test Suite")
    print("=" * 70)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
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
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
