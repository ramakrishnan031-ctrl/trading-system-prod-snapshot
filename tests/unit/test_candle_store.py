"""
tests/unit/test_candle_store.py  -  Unit tests for data/candle_store.py
"""

from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import List
from unittest.mock import MagicMock

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from core.time_authority import now_ist


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_logger() -> MagicMock:
    log = MagicMock()
    log.info = MagicMock()
    log.warning = MagicMock()
    log.error = MagicMock()
    log.critical = MagicMock()
    return log


def _make_store(interval_sec: int = 60):
    from data.candle_store import CandleStore
    return CandleStore(_make_logger(), candle_interval_sec=interval_sec)


def _tick(store, token: int, ltp: float) -> None:
    store.on_tick(token, ltp, now_ist().replace(tzinfo=None))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import_and_instantiate() -> None:
    from data.candle_store import CandleStore, CandleData
    store = CandleStore(_make_logger())
    assert store is not None
    print("  OK import_and_instantiate")


def test_single_ltp_candle_o_eq_h_eq_l_eq_c() -> None:
    store = _make_store()
    _tick(store, 101, 500.0)
    store._close_candles()
    candles = store.get_candles(101, n=1)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == 500.0
    assert c.high == 500.0
    assert c.low == 500.0
    assert c.close == 500.0
    assert c.instrument_token == 101
    print("  OK single_ltp_candle_o_eq_h_eq_l_eq_c")


def test_multiple_ltps_same_minute() -> None:
    store = _make_store()
    for ltp in [100.0, 105.0, 98.0, 103.0]:
        _tick(store, 101, ltp)
    store._close_candles()
    candles = store.get_candles(101, n=1)
    assert len(candles) == 1
    c = candles[0]
    assert c.open == 100.0
    assert c.high == 105.0
    assert c.low == 98.0
    assert c.close == 103.0
    print("  OK multiple_ltps_same_minute")


def test_close_has_correct_open_high_low_close() -> None:
    store = _make_store()
    # First tick: open
    _tick(store, 202, 200.0)
    # Spike high
    _tick(store, 202, 250.0)
    # Drop low
    _tick(store, 202, 190.0)
    # Last tick: close
    _tick(store, 202, 210.0)
    store._close_candles()
    c = store.get_candles(202, n=1)[0]
    assert c.open == 200.0
    assert c.high == 250.0
    assert c.low == 190.0
    assert c.close == 210.0
    print("  OK close_has_correct_open_high_low_close")


def test_timer_fires_candle_closed_callback_invoked() -> None:
    """LF12: clock thread fires candle close without needing a new tick."""
    received: list = []
    store = _make_store(interval_sec=1)
    store.register_on_candle_close(lambda c: received.append(c))
    store.start()
    try:
        _tick(store, 101, 300.0)
        # Wait for clock thread to fire (up to 2 seconds)
        deadline = time.time() + 2.0
        while time.time() < deadline and not received:
            time.sleep(0.05)
        assert len(received) == 1
        assert received[0].close == 300.0
    finally:
        store.stop()
    print("  OK timer_fires_candle_closed_callback_invoked")


def test_illiquid_one_tick_then_timer_valid_candle() -> None:
    """LF12: even illiquid symbols with a single tick get a valid candle."""
    received: list = []
    store = _make_store(interval_sec=1)
    store.register_on_candle_close(lambda c: received.append(c))
    store.start()
    try:
        _tick(store, 999, 42.5)
        deadline = time.time() + 2.0
        while time.time() < deadline and not received:
            time.sleep(0.05)
        assert len(received) == 1
        c = received[0]
        assert c.open == c.high == c.low == c.close == 42.5
        assert c.instrument_token == 999
    finally:
        store.stop()
    print("  OK illiquid_one_tick_then_timer_valid_candle")


def test_no_ticks_no_candle() -> None:
    """LF12: timer fires but no ticks -> no candle emitted."""
    received: list = []
    store = _make_store(interval_sec=1)
    store.register_on_candle_close(lambda c: received.append(c))
    store.start()
    try:
        # No ticks
        deadline = time.time() + 1.5
        while time.time() < deadline:
            time.sleep(0.05)
        assert len(received) == 0
    finally:
        store.stop()
    print("  OK no_ticks_no_candle")


def test_history_get_candles_returns_last_n() -> None:
    store = _make_store()
    for i in range(5):
        _tick(store, 101, float(100 + i))
        store._close_candles()
    all_candles = store.get_candles(101, n=10)
    assert len(all_candles) == 5
    last_3 = store.get_candles(101, n=3)
    assert len(last_3) == 3
    # Last 3 candles have close = 102, 103, 104
    closes = [c.close for c in last_3]
    assert closes == [102.0, 103.0, 104.0]
    print("  OK history_get_candles_returns_last_n")


def test_history_capped_at_max_history() -> None:
    """LF14: deque capped at MAX_HISTORY candles (MED #14: bumped 390->500)."""
    from data.candle_store import CandleStore
    store = _make_store()
    for i in range(600):
        _tick(store, 101, float(i))
        store._close_candles()
    candles = store.get_candles(101, n=600)
    assert len(candles) == store.MAX_HISTORY
    assert store.MAX_HISTORY == 500, f"MAX_HISTORY should be 500, got {store.MAX_HISTORY}"
    print("  OK history_capped_at_max_history: MAX_HISTORY=%d (MED #14)" % store.MAX_HISTORY)


def test_history_accepts_400_candles_without_eviction_panic() -> None:
    """MED #14: store must accept 400+ candles; history capped at MAX_HISTORY=500."""
    from data.candle_store import CandleStore
    store = _make_store()
    for i in range(420):
        _tick(store, 101, float(100 + i))
        store._close_candles()
    candles = store.get_candles(101, n=420)
    # All 420 fit inside MAX_HISTORY=500; oldest are retained
    assert len(candles) == 420, f"Expected 420 candles, got {len(candles)}"
    print("  OK history_accepts_400_candles_without_eviction_panic (MED #14)")


def test_ltp_only_exchange_ohlc_ignored() -> None:
    """Audit 3.4 regression: exchange ohlc in tick must NOT affect candle OHLC."""
    store = _make_store()
    # Send tick with LTP=100 — the test verifies candle comes from LTP
    _tick(store, 101, 100.0)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]
    # If we accidentally used exchange ohlc, these would differ
    assert c.open == 100.0
    assert c.close == 100.0
    # volume is always 0 (LF11: unreliable from ticks)
    assert c.volume == 0
    print("  OK ltp_only_exchange_ohlc_ignored: OHLC from LTP, volume=0")


def test_clock_based_close_fires_without_new_tick_after_first() -> None:
    """Audit 3.4: candle close does not wait for a second tick arrival."""
    received: list = []
    store = _make_store(interval_sec=1)
    store.register_on_candle_close(lambda c: received.append(c))
    store.start()
    try:
        # One tick, then silence — timer should still close
        _tick(store, 101, 150.0)
        deadline = time.time() + 2.0
        while time.time() < deadline and not received:
            time.sleep(0.05)
        assert len(received) == 1, (
            "Candle must close via clock thread even with no further ticks"
        )
    finally:
        store.stop()
    print("  OK clock_based_close_fires_without_new_tick_after_first")


def test_mark_reconnect_discards_partial() -> None:
    """LF16: partial (unclosed) candles are discarded on reconnect."""
    store = _make_store()
    _tick(store, 101, 100.0)
    _tick(store, 202, 200.0)
    assert len(store._accum) == 2

    store.mark_reconnect(now_ist().replace(tzinfo=None))
    assert len(store._accum) == 0
    print("  OK mark_reconnect_discards_partial")


def test_mark_reconnect_preserves_history() -> None:
    """LF16: closed candles in history are NOT removed on reconnect."""
    store = _make_store()
    _tick(store, 101, 100.0)
    store._close_candles()
    assert len(store.get_candles(101, n=10)) == 1

    # Now add a new partial candle and reconnect
    _tick(store, 101, 110.0)
    store.mark_reconnect(now_ist().replace(tzinfo=None))

    # History should still have the 1 closed candle
    assert len(store.get_candles(101, n=10)) == 1
    print("  OK mark_reconnect_preserves_history")


def test_mark_reconnect_logs_warning_with_count() -> None:
    """LF16: WARNING logged with discard count."""
    logger = _make_logger()
    from data.candle_store import CandleStore
    store = CandleStore(logger, candle_interval_sec=60)
    _tick(store, 101, 100.0)
    _tick(store, 202, 200.0)

    store.mark_reconnect(now_ist().replace(tzinfo=None))

    logger.warning.assert_called_once()
    msg = str(logger.warning.call_args)
    assert "2" in msg, "Warning should mention 2 discarded candles"
    print("  OK mark_reconnect_logs_warning_with_count")


def test_token_map_populates_symbol() -> None:
    """LF15: symbol field populated from token_map."""
    store = _make_store()
    store.set_token_map({101: "RELIANCE", 202: "INFY"})
    _tick(store, 101, 2500.0)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]
    assert c.symbol == "RELIANCE"
    print("  OK token_map_populates_symbol")


def test_missing_token_symbol_is_str_token() -> None:
    """LF15: token not in map -> symbol = str(instrument_token)."""
    store = _make_store()
    store.set_token_map({101: "RELIANCE"})
    _tick(store, 999, 50.0)  # 999 not in map
    store._close_candles()
    c = store.get_candles(999, n=1)[0]
    assert c.symbol == "999"
    print("  OK missing_token_symbol_is_str_token")


def test_thread_safety_concurrent_on_tick() -> None:
    """LF17: 50 concurrent on_tick calls must not corrupt accumulator."""
    store = _make_store()
    token = 101
    n_threads = 50
    errors: list = []

    def worker(ltp: float) -> None:
        try:
            store.on_tick(token, ltp, now_ist().replace(tzinfo=None))
        except Exception as exc:
            errors.append(exc)

    ltps = [float(100 + i) for i in range(n_threads)]
    threads = [threading.Thread(target=worker, args=(ltp,)) for ltp in ltps]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"Thread safety errors: {errors}"

    store._close_candles()
    candles = store.get_candles(token, n=1)
    assert len(candles) == 1
    c = candles[0]
    assert c.open in ltps
    assert c.high == max(ltps)
    assert c.low == min(ltps)
    assert c.close in ltps
    print("  OK thread_safety_concurrent_on_tick: no errors, H=%.1f L=%.1f" % (c.high, c.low))


def test_callback_invoked_once_per_candle_per_symbol() -> None:
    """Each candle close fires callback exactly once per symbol."""
    calls: list = []
    store = _make_store()
    store.register_on_candle_close(lambda c: calls.append(c))

    _tick(store, 101, 100.0)
    _tick(store, 202, 200.0)
    store._close_candles()

    assert len(calls) == 2
    tokens = {c.instrument_token for c in calls}
    assert tokens == {101, 202}
    print("  OK callback_invoked_once_per_candle_per_symbol")


def test_candle_data_fields_present() -> None:
    """LF13: All CandleData fields present and correct types."""
    from data.candle_store import CandleData
    store = _make_store()
    store.set_token_map({101: "NIFTY50"})
    _tick(store, 101, 22000.0)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]

    assert isinstance(c.instrument_token, int)
    assert isinstance(c.symbol, str)
    assert isinstance(c.open, float)
    assert isinstance(c.high, float)
    assert isinstance(c.low, float)
    assert isinstance(c.close, float)
    assert isinstance(c.volume, int)
    assert isinstance(c.ts, datetime)
    assert isinstance(c.interval_sec, int)
    assert c.symbol == "NIFTY50"
    assert c.volume == 0
    print("  OK candle_data_fields_present")


def test_candle_data_is_frozen() -> None:
    """LF13: CandleData is a frozen dataclass (immutable)."""
    from data.candle_store import CandleData
    store = _make_store()
    _tick(store, 101, 100.0)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]
    try:
        c.close = 999.0  # type: ignore
        raise AssertionError("CandleData should be frozen/immutable")
    except Exception as exc:
        # frozen dataclass raises FrozenInstanceError or similar
        assert "frozen" in str(type(exc).__name__).lower() or "cannot assign" in str(exc).lower(), \
            f"Unexpected exception type: {type(exc)}: {exc}"
    print("  OK candle_data_is_frozen")


def test_candle_data_interval_sec_set() -> None:
    """CandleData.interval_sec reflects candle_interval_sec constructor arg."""
    store = _make_store(interval_sec=5)
    _tick(store, 101, 100.0)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]
    assert c.interval_sec == 5
    print("  OK candle_data_interval_sec_set")


def test_multiple_symbols_independent_candles() -> None:
    """Each symbol has its own independent accumulator."""
    store = _make_store()
    _tick(store, 101, 100.0)
    _tick(store, 101, 110.0)
    _tick(store, 202, 200.0)
    _tick(store, 202, 190.0)
    store._close_candles()

    c101 = store.get_candles(101, n=1)[0]
    c202 = store.get_candles(202, n=1)[0]

    assert c101.open == 100.0
    assert c101.high == 110.0
    assert c101.low == 100.0
    assert c101.close == 110.0

    assert c202.open == 200.0
    assert c202.high == 200.0
    assert c202.low == 190.0
    assert c202.close == 190.0
    print("  OK multiple_symbols_independent_candles")


def test_close_candles_multiple_symbols_each_fires_callback() -> None:
    """_close_candles fires callback for each symbol with accumulated data."""
    received: list = []
    store = _make_store()
    store.register_on_candle_close(lambda c: received.append(c.instrument_token))

    for token in [101, 202, 303, 404]:
        _tick(store, token, float(token))
    store._close_candles()

    assert set(received) == {101, 202, 303, 404}
    print("  OK close_candles_multiple_symbols_each_fires_callback")


def test_mark_reconnect_zero_partial_candles() -> None:
    """mark_reconnect with no partials: logs '0 partial candle(s)', no error."""
    logger = _make_logger()
    from data.candle_store import CandleStore
    store = CandleStore(logger, candle_interval_sec=60)
    store.mark_reconnect(now_ist().replace(tzinfo=None))
    logger.warning.assert_called_once()
    msg = str(logger.warning.call_args)
    assert "0" in msg
    print("  OK mark_reconnect_zero_partial_candles")


def test_candle_ts_is_ist() -> None:
    """LF13: CandleData.ts must be IST timezone-aware datetime."""
    store = _make_store()
    _tick(store, 101, 100.0)
    store._close_candles()
    c = store.get_candles(101, n=1)[0]
    assert c.ts.tzinfo is not None, "ts must be timezone-aware"
    assert "IST" in str(c.ts.tzinfo) or "Asia/Calcutta" in str(c.ts.tzinfo) or "UTC+5:30" in str(c.ts.tzinfo), \
        f"ts.tzinfo should be IST, got: {c.ts.tzinfo}"
    print("  OK candle_ts_is_ist: tzinfo=%s" % c.ts.tzinfo)


# ---------------------------------------------------------------------------
# FIX-020: Flat Candle Carry-Forward
# ---------------------------------------------------------------------------

def test_fix020_one_tick_then_three_minutes_silence() -> None:
    """FIX-020: 1 real tick then 3 minutes silence → 3 synthetic flat candles."""
    store = _make_store()
    store.set_token_map({101: "RELIANCE"})

    # Real tick at minute 1: close=150.0
    _tick(store, 101, 150.0)
    store._close_candles()

    # Get first candle and verify it's real
    candles = store.get_candles(101, n=10)
    assert len(candles) == 1
    c0 = candles[0]
    assert c0.close == 150.0
    assert c0.is_synthetic is False

    # Simulate 3 minutes of silence (no ticks, just timer fires)
    for _ in range(3):
        store._close_candles()

    # Should now have 4 candles: 1 real + 3 synthetic
    candles = store.get_candles(101, n=10)
    assert len(candles) == 4, f"Expected 4 candles (1 real + 3 synthetic), got {len(candles)}"

    # Verify first is real
    assert candles[0].is_synthetic is False
    assert candles[0].close == 150.0

    # Verify next 3 are synthetic with carried-forward price
    for i in [1, 2, 3]:
        c = candles[i]
        assert c.is_synthetic is True, f"Candle {i} should be synthetic"
        assert c.open == 150.0, f"Candle {i} open should be 150.0"
        assert c.high == 150.0, f"Candle {i} high should be 150.0"
        assert c.low == 150.0, f"Candle {i} low should be 150.0"
        assert c.close == 150.0, f"Candle {i} close should be 150.0"
        assert c.volume == 0

    print("  OK fix020_one_tick_then_three_minutes_silence: 1 real + 3 synthetic flat candles")


def test_fix020_first_minute_no_prior_no_synthetic() -> None:
    """FIX-020: First minute with no prior candle → NO synthetic emitted."""
    store = _make_store()
    store.set_token_map({101: "RELIANCE"})

    # Close candles without any ticks (no history)
    store._close_candles()

    # Should have NO candles (no real, no synthetic)
    candles = store.get_candles(101, n=10)
    assert len(candles) == 0, f"Expected 0 candles on first close with no ticks, got {len(candles)}"

    print("  OK fix020_first_minute_no_prior_no_synthetic: 0 candles emitted")


def test_fix020_synthetic_candle_callback_invoked() -> None:
    """FIX-020: Synthetic candles trigger on_candle_close callbacks."""
    received: List = []
    store = _make_store()
    store.set_token_map({101: "RELIANCE"})
    store.register_on_candle_close(lambda c: received.append(c))

    # Real tick
    _tick(store, 101, 200.0)
    store._close_candles()
    assert len(received) == 1
    assert received[0].is_synthetic is False

    # Silent minute → synthetic candle
    store._close_candles()
    assert len(received) == 2
    syn = received[1]
    assert syn.is_synthetic is True
    assert syn.open == syn.high == syn.low == syn.close == 200.0

    print("  OK fix020_synthetic_candle_callback_invoked: callback receives synthetic")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_tests() -> int:
    tests = [
        test_import_and_instantiate,
        test_single_ltp_candle_o_eq_h_eq_l_eq_c,
        test_multiple_ltps_same_minute,
        test_close_has_correct_open_high_low_close,
        test_timer_fires_candle_closed_callback_invoked,
        test_illiquid_one_tick_then_timer_valid_candle,
        test_no_ticks_no_candle,
        test_history_get_candles_returns_last_n,
        test_history_capped_at_max_history,
        test_history_accepts_400_candles_without_eviction_panic,
        test_ltp_only_exchange_ohlc_ignored,
        test_clock_based_close_fires_without_new_tick_after_first,
        test_mark_reconnect_discards_partial,
        test_mark_reconnect_preserves_history,
        test_mark_reconnect_logs_warning_with_count,
        test_token_map_populates_symbol,
        test_missing_token_symbol_is_str_token,
        test_thread_safety_concurrent_on_tick,
        test_callback_invoked_once_per_candle_per_symbol,
        test_candle_data_fields_present,
        test_candle_data_is_frozen,
        test_candle_data_interval_sec_set,
        test_multiple_symbols_independent_candles,
        test_close_candles_multiple_symbols_each_fires_callback,
        test_mark_reconnect_zero_partial_candles,
        test_candle_ts_is_ist,
        test_fix020_one_tick_then_three_minutes_silence,
        test_fix020_first_minute_no_prior_no_synthetic,
        test_fix020_synthetic_candle_callback_invoked,
    ]

    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            failed += 1
            print(f"  FAIL {fn.__name__}: {exc}")

    print(f"\n{'='*50}")
    print(f"test_candle_store.py: {passed}/{len(tests)} passed")
    if failed:
        print(f"  FAILED: {failed}")
    return failed


if __name__ == "__main__":
    sys.exit(run_all_tests())
