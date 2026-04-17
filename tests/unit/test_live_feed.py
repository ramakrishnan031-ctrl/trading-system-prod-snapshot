"""
tests/unit/test_live_feed.py  -  Unit tests for data/live_feed.py

Mock KiteTicker entirely. No real WebSocket connections.
"""

from __future__ import annotations

import queue
import threading
import time
import unittest.mock as mock
from datetime import datetime, timedelta
from typing import List
from unittest.mock import MagicMock, patch

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


# ---------------------------------------------------------------------------
# MockTicker: simulates KiteTicker without network
# ---------------------------------------------------------------------------

class MockTicker:
    """Minimal KiteTicker stand-in for unit tests."""

    MODE_LTP = "ltp"
    MODE_QUOTE = "quote"
    MODE_FULL = "full"

    def __init__(self, api_key, access_token, **kwargs):
        self.api_key = api_key
        self.access_token = access_token
        self._kwargs = kwargs

        # Callback slots (LiveFeedManager assigns these)
        self.on_ticks = None
        self.on_connect = None
        self.on_close = None
        self.on_error = None
        self.on_reconnect = None
        self.on_noreconnect = None

        # Track calls for assertions
        self.subscribe = MagicMock()
        self.unsubscribe = MagicMock()
        self.set_mode = MagicMock()
        self.close = MagicMock()
        self._connected = False

    def connect(self, threaded=False):
        """Simulate connect: immediately fire on_connect."""
        self._connected = True
        if self.on_connect:
            self.on_connect(self, {})

    # --- Simulation helpers (called by tests) ---

    def fire_ticks(self, ticks: list) -> None:
        if self.on_ticks:
            self.on_ticks(self, ticks)

    def fire_close(self, code=1000, reason="normal close") -> None:
        self._connected = False
        if self.on_close:
            self.on_close(self, code, reason)

    def fire_error(self, code=0, reason="error") -> None:
        if self.on_error:
            self.on_error(self, code, reason)

    def fire_reconnect(self, attempts_count: int) -> None:
        if self.on_reconnect:
            self.on_reconnect(self, attempts_count)

    def fire_noreconnect(self) -> None:
        if self.on_noreconnect:
            self.on_noreconnect(self)


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


def _make_feed(
    mock_ticker: MockTicker = None,
    on_critical=None,
    max_reconnect=10,
    reconnect_delay=5,
):
    """Create LiveFeedManager with mocked KiteTicker."""
    from data.live_feed import LiveFeedManager

    logger = _make_logger()
    feed = LiveFeedManager(
        api_key="test_key",
        access_token="test_token",
        logger=logger,
        on_critical_failure=on_critical,
        max_reconnect_attempts=max_reconnect,
        reconnect_delay_sec=reconnect_delay,
    )
    if mock_ticker is not None:
        feed._ticker = mock_ticker
    return feed, logger


def _make_and_connect(
    on_critical=None,
    max_reconnect=10,
    reconnect_delay=5,
):
    """Create feed, patch KiteTicker, and call connect()."""
    ticker = MockTicker("test_key", "test_token")
    with patch("data.live_feed.KiteTicker", return_value=ticker):
        from data.live_feed import LiveFeedManager
        logger = _make_logger()
        feed = LiveFeedManager(
            api_key="test_key",
            access_token="test_token",
            logger=logger,
            on_critical_failure=on_critical,
            max_reconnect_attempts=max_reconnect,
            reconnect_delay_sec=reconnect_delay,
        )
        feed.connect()
    return feed, ticker, logger


def _drain(feed, timeout=0.3) -> None:
    """Wait for consumer thread to drain the tick queue."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if feed._tick_queue.empty():
            time.sleep(0.02)
            break
        time.sleep(0.01)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import_and_instantiate() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    assert feed is not None
    print("  OK import_and_instantiate")


def test_constructor_stores_api_key_and_token() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("my_key", "my_token", _make_logger())
    assert feed._api_key == "my_key"
    assert feed._access_token == "my_token"
    print("  OK constructor_stores_api_key_and_token")


def test_subscribe_adds_to_subscribed_set() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    feed.subscribe([101, 202, 303])
    assert 101 in feed._subscribed
    assert 202 in feed._subscribed
    assert 303 in feed._subscribed
    print("  OK subscribe_adds_to_subscribed_set")


def test_unsubscribe_removes_from_subscribed_set() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    feed.subscribe([101, 202, 303])
    feed.unsubscribe([202])
    assert 101 in feed._subscribed
    assert 202 not in feed._subscribed
    assert 303 in feed._subscribed
    print("  OK unsubscribe_removes_from_subscribed_set")


def test_subscribe_idempotent() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    feed.subscribe([101, 101, 202])
    assert feed._subscribed == {101, 202}
    feed.subscribe([101])
    assert feed._subscribed == {101, 202}
    print("  OK subscribe_idempotent")


def test_register_callback() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    cb = MagicMock()
    feed.register_callback(cb)
    assert cb in feed._callbacks
    print("  OK register_callback")


def test_unregister_callback() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    cb = MagicMock()
    feed.register_callback(cb)
    feed.unregister_callback(cb)
    assert cb not in feed._callbacks
    print("  OK unregister_callback")


def test_register_callback_idempotent() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    cb = MagicMock()
    feed.register_callback(cb)
    feed.register_callback(cb)
    assert feed._callbacks.count(cb) == 1
    print("  OK register_callback_idempotent")


def test_is_connected_false_before_connect() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    assert feed.is_connected() is False
    print("  OK is_connected_false_before_connect")


def test_is_connected_true_after_connect() -> None:
    feed, ticker, _ = _make_and_connect()
    try:
        assert feed.is_connected() is True
    finally:
        feed.disconnect()
    print("  OK is_connected_true_after_connect")


def test_is_connected_false_after_disconnect() -> None:
    feed, ticker, _ = _make_and_connect()
    feed.disconnect()
    assert feed.is_connected() is False
    print("  OK is_connected_false_after_disconnect")


def test_tick_invokes_callback() -> None:
    received: list = []

    def cb(batch):
        received.extend(batch)

    feed, ticker, _ = _make_and_connect()
    try:
        feed.register_callback(cb)
        ticker.fire_ticks([
            {"instrument_token": 101, "last_price": 500.0}
        ])
        _drain(feed)
        assert len(received) == 1
        assert received[0]["instrument_token"] == 101
        assert received[0]["last_price"] == 500.0
    finally:
        feed.disconnect()
    print("  OK tick_invokes_callback")


def test_tick_batch_invokes_callback_with_all_ticks() -> None:
    received: list = []

    def cb(batch):
        received.extend(batch)

    feed, ticker, _ = _make_and_connect()
    try:
        feed.register_callback(cb)
        ticker.fire_ticks([
            {"instrument_token": 101, "last_price": 100.0},
            {"instrument_token": 202, "last_price": 200.0},
            {"instrument_token": 303, "last_price": 300.0},
        ])
        _drain(feed)
        tokens = {t["instrument_token"] for t in received}
        assert tokens == {101, 202, 303}
    finally:
        feed.disconnect()
    print("  OK tick_batch_invokes_callback_with_all_ticks")


def test_queue_full_drops_oldest_tick_and_logs_warning() -> None:
    feed, ticker, logger = _make_and_connect()
    try:
        # Replace queue with capacity=3 to easily fill it
        feed._tick_queue = queue.Queue(maxsize=3)

        # Fill queue: stop consumer from draining by putting stops
        feed._stop_event.set()  # pause consumer
        time.sleep(0.05)

        # Add 3 ticks to fill queue
        feed._on_ticks(None, [
            {"instrument_token": 1, "last_price": 1.0},
            {"instrument_token": 2, "last_price": 2.0},
            {"instrument_token": 3, "last_price": 3.0},
        ])
        assert feed._tick_queue.full()

        # 4th tick should drop oldest (token=1) and add new (token=4)
        feed._on_ticks(None, [
            {"instrument_token": 4, "last_price": 4.0},
        ])

        logger.warning.assert_called()
        warning_msg = str(logger.warning.call_args)
        assert "queue full" in warning_msg

        # Queue still has 3 items; oldest (token=1) was dropped
        items = []
        while not feed._tick_queue.empty():
            items.append(feed._tick_queue.get_nowait())
        tokens = [i["instrument_token"] for i in items]
        assert 4 in tokens, "new tick must be in queue"
        assert 1 not in tokens, "oldest tick must have been dropped"
    finally:
        feed._stop_event.clear()
        feed.disconnect()
    print("  OK queue_full_drops_oldest_tick_and_logs_warning")


def test_reconnect_notifies_candle_store() -> None:
    notified: list = []
    feed, ticker, _ = _make_and_connect()
    try:
        feed.set_on_reconnect_callback(lambda ts: notified.append(ts))
        # Simulate disconnect then reconnect attempt
        ticker.fire_close()
        ticker.fire_reconnect(1)
        assert len(notified) == 1
        assert isinstance(notified[0], datetime)
    finally:
        feed.disconnect()
    print("  OK reconnect_notifies_candle_store")


def test_reconnect_notifies_candle_store_only_once_per_gap() -> None:
    notified: list = []
    feed, ticker, _ = _make_and_connect()
    try:
        feed.set_on_reconnect_callback(lambda ts: notified.append(ts))
        ticker.fire_close()
        # Multiple reconnect attempts in same disconnect event
        ticker.fire_reconnect(1)
        ticker.fire_reconnect(2)
        ticker.fire_reconnect(3)
        assert len(notified) == 1, "should notify candle_store only once per gap"
    finally:
        feed.disconnect()
    print("  OK reconnect_notifies_candle_store_only_once_per_gap")


def test_reconnect_resubscribes_all_tokens() -> None:
    feed, ticker, _ = _make_and_connect()
    try:
        feed.subscribe([101, 202])
        # Clear call history from initial subscribe
        ticker.subscribe.reset_mock()
        ticker.set_mode.reset_mock()

        # Simulate disconnect + reconnect (on_connect fires again)
        ticker.fire_close()
        # Reconnect: on_connect fires again (simulates successful reconnect)
        ticker.on_connect(ticker, {})

        # Verify resubscribe was called with our tokens
        ticker.subscribe.assert_called()
        subscribed_tokens = set(ticker.subscribe.call_args[0][0])
        assert 101 in subscribed_tokens
        assert 202 in subscribed_tokens
    finally:
        feed.disconnect()
    print("  OK reconnect_resubscribes_all_tokens")


def test_reconnect_gap_over_10min_fires_critical_failure() -> None:
    critical_calls: list = []
    feed, ticker, _ = _make_and_connect(on_critical=lambda msg: critical_calls.append(msg))
    try:
        # Fake a disconnect time 11 minutes ago
        from core.time_authority import now_ist
        feed._disconnect_time = now_ist() - timedelta(minutes=11)
        ticker.fire_reconnect(1)
        assert len(critical_calls) == 1
        assert "10 min" in critical_calls[0]
    finally:
        feed.disconnect()
    print("  OK reconnect_gap_over_10min_fires_critical_failure")


def test_reconnect_gap_under_10min_no_critical_failure() -> None:
    critical_calls: list = []
    feed, ticker, _ = _make_and_connect(on_critical=lambda msg: critical_calls.append(msg))
    try:
        from core.time_authority import now_ist
        feed._disconnect_time = now_ist() - timedelta(minutes=5)
        ticker.fire_reconnect(1)
        assert len(critical_calls) == 0
    finally:
        feed.disconnect()
    print("  OK reconnect_gap_under_10min_no_critical_failure")


def test_noreconnect_fires_critical_failure() -> None:
    critical_calls: list = []
    feed, ticker, _ = _make_and_connect(
        on_critical=lambda msg: critical_calls.append(msg),
        max_reconnect=5,
    )
    try:
        ticker.fire_noreconnect()
        assert len(critical_calls) == 1
        assert "5" in critical_calls[0]  # max_reconnect_attempts=5
    finally:
        feed.disconnect()
    print("  OK noreconnect_fires_critical_failure")


def test_noreconnect_logs_critical() -> None:
    feed, ticker, logger = _make_and_connect(max_reconnect=3)
    try:
        ticker.fire_noreconnect()
        logger.critical.assert_called()
        msg = str(logger.critical.call_args)
        assert "3" in msg
    finally:
        feed.disconnect()
    print("  OK noreconnect_logs_critical")


def test_on_close_sets_connected_false() -> None:
    feed, ticker, _ = _make_and_connect()
    assert feed.is_connected() is True
    ticker.fire_close(code=1006, reason="network error")
    assert feed.is_connected() is False
    feed.disconnect()
    print("  OK on_close_sets_connected_false")


def test_set_on_reconnect_callback() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    cb = MagicMock()
    feed.set_on_reconnect_callback(cb)
    assert feed._on_reconnect_cb is cb
    print("  OK set_on_reconnect_callback")


def test_subscribe_calls_ticker_when_connected() -> None:
    feed, ticker, _ = _make_and_connect()
    try:
        ticker.subscribe.reset_mock()
        ticker.set_mode.reset_mock()
        feed.subscribe([555, 666])
        ticker.subscribe.assert_called_once_with([555, 666])
        ticker.set_mode.assert_called()
    finally:
        feed.disconnect()
    print("  OK subscribe_calls_ticker_when_connected")


def test_subscribe_does_not_call_ticker_when_disconnected() -> None:
    from data.live_feed import LiveFeedManager
    ticker = MockTicker("k", "t")
    feed, _ = _make_feed(mock_ticker=ticker)
    # _connected is False, _ticker set but not connected
    feed._ticker = ticker
    feed.subscribe([999])
    ticker.subscribe.assert_not_called()
    print("  OK subscribe_does_not_call_ticker_when_disconnected")


def test_thread_safety_concurrent_subscribe_unsubscribe() -> None:
    from data.live_feed import LiveFeedManager
    feed = LiveFeedManager("k", "t", _make_logger())
    tokens_per_thread = 20
    n_threads = 5
    errors: list = []

    def worker(start: int) -> None:
        chunk = list(range(start, start + tokens_per_thread))
        try:
            for _ in range(10):
                feed.subscribe(chunk)
                feed.unsubscribe(chunk[:tokens_per_thread // 2])
                feed.subscribe(chunk[:tokens_per_thread // 2])
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(i * tokens_per_thread,))
        for i in range(n_threads)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert not errors, f"Thread safety errors: {errors}"
    assert len(feed._subscribed) > 0
    print("  OK thread_safety_concurrent_subscribe_unsubscribe: no errors, %d tokens" % len(feed._subscribed))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_all_tests() -> int:
    tests = [
        test_import_and_instantiate,
        test_constructor_stores_api_key_and_token,
        test_subscribe_adds_to_subscribed_set,
        test_unsubscribe_removes_from_subscribed_set,
        test_subscribe_idempotent,
        test_register_callback,
        test_unregister_callback,
        test_register_callback_idempotent,
        test_is_connected_false_before_connect,
        test_is_connected_true_after_connect,
        test_is_connected_false_after_disconnect,
        test_tick_invokes_callback,
        test_tick_batch_invokes_callback_with_all_ticks,
        test_queue_full_drops_oldest_tick_and_logs_warning,
        test_reconnect_notifies_candle_store,
        test_reconnect_notifies_candle_store_only_once_per_gap,
        test_reconnect_resubscribes_all_tokens,
        test_reconnect_gap_over_10min_fires_critical_failure,
        test_reconnect_gap_under_10min_no_critical_failure,
        test_noreconnect_fires_critical_failure,
        test_noreconnect_logs_critical,
        test_on_close_sets_connected_false,
        test_set_on_reconnect_callback,
        test_subscribe_calls_ticker_when_connected,
        test_subscribe_does_not_call_ticker_when_disconnected,
        test_thread_safety_concurrent_subscribe_unsubscribe,
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
    print(f"test_live_feed.py: {passed}/{len(tests)} passed")
    if failed:
        print(f"  FAILED: {failed}")
    return failed


if __name__ == "__main__":
    sys.exit(run_all_tests())
