"""
tests/unit/test_phase19_batch3.py — Trading System v2

Tests for Phase 19 audit fixes: BATCH 3 (FIX-091, FIX-092)
"""
from __future__ import annotations

import logging
import time
from unittest.mock import Mock, patch

import pytest

from screening.step_executor import StepExecutor


# ─────────────────────────────────────────────────────────────────────────────
# FIX-091: Per-step timeout with neutral score
# ─────────────────────────────────────────────────────────────────────────────

def test_fix091_step_timeout_neutral_score() -> None:
    """
    FIX-091: Step takes >timeout. Assert score=0.5 (neutral), status=TIMEOUT, WARNING logged.
    """
    logger = logging.getLogger("test_fix091")
    executor = StepExecutor(logger, step_timeout_sec=0.5)

    # Mock a slow step function
    def slow_step(signal, md, thr, direction):
        time.sleep(1.0)  # Sleep longer than timeout
        return 1.0

    # Patch one of the step methods to be slow
    with patch.object(executor, '_step_1_volume_surge', side_effect=slow_step):
        signal = {"direction": "LONG"}
        market_data = {
            "volume": 1000,
            "avg_volume_20d": 500,
            "ltp": 100.0,
            "vwap": 99.0,
        }
        thresholds = {}

        result = executor.run_all(signal, market_data, thresholds)

        # Check timeout handling
        assert result.step_results["volume_surge"] == 0.5, "Expected neutral score on timeout"
        assert result.step_statuses["volume_surge"] == "TIMEOUT", "Expected TIMEOUT status"
        assert result.latencies_ms["volume_surge"] >= 500, "Latency should reflect timeout wait"

    print("  OK fix091_step_timeout_neutral_score: timeout → 0.5 neutral + TIMEOUT status")


def test_fix091_configurable_timeout() -> None:
    """
    FIX-091: step_timeout_sec is configurable. Changing timeout changes behavior.
    """
    logger = logging.getLogger("test_fix091")

    # Timeout of 2 seconds should allow 1-second sleep to complete
    executor = StepExecutor(logger, step_timeout_sec=2.0)

    def slow_step(signal, md, thr, direction):
        time.sleep(1.0)
        return 1.0

    with patch.object(executor, '_step_1_volume_surge', side_effect=slow_step):
        signal = {"direction": "LONG"}
        market_data = {
            "volume": 1000,
            "avg_volume_20d": 500,
            "ltp": 100.0,
            "vwap": 99.0,
        }
        thresholds = {}

        result = executor.run_all(signal, market_data, thresholds)

        # With 2s timeout, 1s sleep should complete
        assert result.step_results["volume_surge"] == 1.0, "Expected step to complete"
        assert result.step_statuses["volume_surge"] == "PASSED", "Expected PASSED status"

    print("  OK fix091_configurable_timeout: 2s timeout allows 1s step to complete")


def test_fix091_timeout_does_not_affect_other_steps() -> None:
    """
    FIX-091: One step timeout doesn't prevent other steps from running.
    """
    logger = logging.getLogger("test_fix091")
    executor = StepExecutor(logger, step_timeout_sec=0.5)

    def slow_step(signal, md, thr, direction):
        time.sleep(1.0)
        return 1.0

    # Only step_1 is slow, others should complete
    with patch.object(executor, '_step_1_volume_surge', side_effect=slow_step):
        signal = {"direction": "LONG"}
        market_data = {
            "volume": 1000,
            "avg_volume_20d": 500,
            "ltp": 100.0,
            "vwap": 99.0,
            "atr": 5.0,
            "rsi": 60.0,
        }
        thresholds = {}

        result = executor.run_all(signal, market_data, thresholds)

        # Step 1 timed out
        assert result.step_statuses["volume_surge"] == "TIMEOUT"
        # Other steps should complete normally
        assert result.step_statuses["vwap_position"] == "PASSED"
        assert result.step_statuses["atr_filter"] == "PASSED"

    print("  OK fix091_timeout_does_not_affect_other_steps: timeout isolated to one step")


def test_fix091_default_timeout() -> None:
    """
    FIX-091: Default timeout is 5.0 seconds.
    """
    logger = logging.getLogger("test_fix091")
    executor = StepExecutor(logger)  # No timeout specified

    assert executor._step_timeout_sec == 5.0, "Expected default timeout of 5.0 seconds"
    print("  OK fix091_default_timeout: default is 5.0 seconds")


# ─────────────────────────────────────────────────────────────────────────────
# FIX-092: GC sweep on InstrumentsRefreshed event
# ─────────────────────────────────────────────────────────────────────────────

def test_fix092_candle_store_gc_sweep() -> None:
    """
    FIX-092: CandleStore.gc_sweep() removes dead tokens.
    """
    from data.candle_store import CandleStore

    logger = logging.getLogger("test_fix092")
    store = CandleStore(logger)

    # Set up initial state with tokens 1, 2, 3
    store.set_token_map({1: "SYM1", 2: "SYM2", 3: "SYM3"})
    store._accum[1] = Mock()  # Simulate active accumulators
    store._accum[2] = Mock()
    store._history[1] = Mock()  # Simulate history
    store._history[3] = Mock()

    # Now only tokens 1 and 3 are valid (token 2 is dead)
    valid_tokens = {1, 3}
    removed = store.gc_sweep(valid_tokens)

    # Token 2 should be removed from _accum, no other tokens removed
    assert removed == 1, f"Expected 1 token removed, got {removed}"
    assert 1 in store._accum, "Token 1 should still be in _accum"
    assert 2 not in store._accum, "Token 2 should be removed from _accum"
    assert 3 not in store._accum, "Token 3 was not in _accum"

    assert 1 in store._history, "Token 1 should still be in _history"
    assert 2 not in store._history, "Token 2 should be removed from _history"
    assert 3 in store._history, "Token 3 should still be in _history"

    print("  OK fix092_candle_store_gc_sweep: dead tokens removed")


def test_fix092_gc_sweep_empty_state() -> None:
    """
    FIX-092: gc_sweep on empty state is a no-op.
    """
    from data.candle_store import CandleStore

    logger = logging.getLogger("test_fix092")
    store = CandleStore(logger)

    removed = store.gc_sweep({1, 2, 3})
    assert removed == 0, "Expected 0 tokens removed from empty state"

    print("  OK fix092_gc_sweep_empty_state: no-op on empty state")


def test_fix092_instrument_cache_reload() -> None:
    """
    FIX-092: InstrumentCache.reload() publishes InstrumentsRefreshed event.
    """
    import tempfile
    from pathlib import Path
    from core.instrument_cache import InstrumentCache
    from core.events import EventBus, InstrumentsRefreshed

    # Create a temp CSV with 2 instruments
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False, newline='') as f:
        f.write("symbol,instrument_token,exchange,lot_size,tick_size,is_fno,sector\n")
        f.write("SYM1,1,NSE,1,0.05,false,IT\n")
        f.write("SYM2,2,NSE,1,0.05,false,BANK\n")
        temp_path = Path(f.name)

    try:
        # Load initial cache
        cache = InstrumentCache.load(temp_path)

        # Set up event bus
        bus = EventBus()
        events_received = []

        def on_refresh(event: InstrumentsRefreshed) -> None:
            events_received.append(event)

        bus.subscribe(InstrumentsRefreshed, on_refresh)

        # Reload cache
        new_cache = cache.reload(temp_path, bus)

        # Check event published
        assert len(events_received) == 1, "Expected 1 InstrumentsRefreshed event"
        assert events_received[0].token_count == 2, "Expected token_count=2"
        assert events_received[0].source_module == "instrument_cache"

        # Check new cache has correct data
        assert new_cache.count() == 2
        assert new_cache.has("SYM1")
        assert new_cache.has("SYM2")

        print("  OK fix092_instrument_cache_reload: reload publishes event")
    finally:
        temp_path.unlink()


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n=== Phase 19 Batch 3 Tests ===\n")

    test_fix091_step_timeout_neutral_score()
    test_fix091_configurable_timeout()
    test_fix091_timeout_does_not_affect_other_steps()
    test_fix091_default_timeout()

    test_fix092_candle_store_gc_sweep()
    test_fix092_gc_sweep_empty_state()
    test_fix092_instrument_cache_reload()

    print("\n=== All Phase 19 Batch 3 tests passed ===\n")
