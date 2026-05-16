"""
test_fix064_flag_based_reconnect.py

FIX-064: Verify flag-based watchdog reconnect.

Tests verify that:
1. Watchdog sets _force_reconnect flag instead of calling ticker.close() directly
2. Consumer thread detects flag and calls ticker.close() from its own thread context
3. Reconnection occurs after flag-triggered close
"""

import logging
import threading
import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, call, patch

import pytest

from core.time_authority import now_ist
from data.live_feed import LiveFeedManager


class FakeMarketWindows:
    """Fake market_windows that always returns True for is_entry_allowed."""

    def is_entry_allowed(self, dt: datetime) -> bool:
        return True


@pytest.fixture
def fake_logger():
    """Return a mock logger."""
    return MagicMock(spec=logging.Logger)


@pytest.fixture
def fake_ticker():
    """Return a mock KiteTicker."""
    ticker = MagicMock()
    ticker.connect = MagicMock()
    ticker.close = MagicMock()
    ticker.subscribe = MagicMock()
    ticker.set_mode = MagicMock()
    return ticker


# ─────────────────────────────────────────────────────────────────────────────
# Test 1: Watchdog sets flag, does NOT call close() directly
# ─────────────────────────────────────────────────────────────────────────────


def test_watchdog_sets_flag_not_close(fake_logger, fake_ticker):
    """
    FIX-064: Watchdog triggers reconnect via flag, not direct close() call.

    The watchdog sets _force_reconnect flag, then consumer thread calls close().
    We verify that: (1) close() is eventually called, and (2) it's called from
    the consumer thread, not the watchdog thread.
    """
    close_thread_name = None

    def capture_close_thread(*args, **kwargs):
        nonlocal close_thread_name
        close_thread_name = threading.current_thread().name

    fake_ticker.close.side_effect = capture_close_thread

    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
        tick_stale_threshold_sec=1,  # Reduced to 1 second for faster test
        watchdog_check_interval_sec=0.5,  # Check every 0.5 seconds
        market_windows=FakeMarketWindows(),
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        # Manually set connected state (fake ticker doesn't call _on_connect)
        mgr._connected = True

        # Arm watchdog with a tick IMMEDIATELY (so watchdog starts checking)
        mgr._last_tick_at = now_ist()

        # Wait for watchdog to arm and start checking (needs to wait past first tick check)
        time.sleep(0.6)

        # Make tick stale (older than threshold)
        mgr._last_tick_at = now_ist() - timedelta(seconds=2)

        # Wait for full flow: watchdog detects → sets flag → consumer calls close
        time.sleep(1.5)

        # close() should have been called (by consumer thread, not watchdog)
        fake_ticker.close.assert_called_once()

        # Verify it was called from consumer thread, not watchdog thread
        assert close_thread_name is not None
        assert "consumer" in close_thread_name.lower(), (
            f"Expected close to be called from consumer thread, "
            f"got: {close_thread_name}"
        )

        mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 2: Consumer thread detects flag and calls close() from correct thread
# ─────────────────────────────────────────────────────────────────────────────


def test_consumer_detects_flag_and_closes(fake_logger, fake_ticker):
    """
    FIX-064: Consumer thread detects _force_reconnect flag and calls ticker.close().
    """
    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        time.sleep(0.2)  # Let consumer thread start

        # Manually set the reconnect flag
        mgr._force_reconnect.set()

        # Wait for consumer thread to detect flag and call close()
        time.sleep(0.3)

        # ticker.close() should have been called
        fake_ticker.close.assert_called_once()

        # Flag should be cleared
        assert not mgr._force_reconnect.is_set(), "Flag should be cleared after close"

        mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 3: Close is called from consumer thread, not watchdog thread
# ─────────────────────────────────────────────────────────────────────────────


def test_close_called_from_consumer_thread(fake_logger, fake_ticker):
    """
    FIX-064: Verify ticker.close() is called from consumer thread, not watchdog.
    """
    close_thread_name = None

    def capture_thread_name(*args, **kwargs):
        nonlocal close_thread_name
        close_thread_name = threading.current_thread().name

    fake_ticker.close.side_effect = capture_thread_name

    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        time.sleep(0.2)

        # Set flag manually (simulating watchdog)
        mgr._force_reconnect.set()

        # Wait for consumer to detect and close
        time.sleep(0.3)

        # Verify close was called from consumer thread
        assert close_thread_name is not None
        assert "consumer" in close_thread_name.lower(), (
            f"Expected close to be called from consumer thread, "
            f"got thread: {close_thread_name}"
        )

        mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 4: Multiple flag sets only trigger one close
# ─────────────────────────────────────────────────────────────────────────────


def test_multiple_flag_sets_single_close(fake_logger, fake_ticker):
    """
    FIX-064: Multiple flag sets should not cause multiple close() calls per cycle.
    """
    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        time.sleep(0.2)

        # Set flag multiple times
        mgr._force_reconnect.set()
        mgr._force_reconnect.set()
        mgr._force_reconnect.set()

        # Wait for consumer to process
        time.sleep(0.3)

        # Should only call close once (flag is cleared after first check)
        assert fake_ticker.close.call_count == 1

        mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 5: Reconnection occurs after flag-triggered close
# ─────────────────────────────────────────────────────────────────────────────


def test_reconnection_after_flag_close(fake_logger, fake_ticker):
    """
    FIX-064: Verify that reconnection flow works after flag-triggered close.
    """
    on_reconnect_called = threading.Event()

    def fake_on_reconnect(ws, attempts):
        on_reconnect_called.set()

    fake_ticker.on_reconnect = None  # Will be set by _create_ticker

    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        time.sleep(0.2)

        # Capture the on_reconnect callback
        original_on_reconnect = mgr._on_reconnect

        # Set flag to trigger close
        mgr._force_reconnect.set()
        time.sleep(0.3)

        # ticker.close() should have been called
        fake_ticker.close.assert_called_once()

        # Simulate KiteTicker's reconnect callback
        original_on_reconnect(fake_ticker, 1)

        # Verify reconnect logic ran
        assert mgr._reconnect_notified

        mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 6: Flag is cleared even if close() raises exception
# ─────────────────────────────────────────────────────────────────────────────


def test_flag_cleared_even_on_exception(fake_logger, fake_ticker):
    """
    FIX-064: Flag should be cleared even if ticker.close() raises an exception.
    """
    fake_ticker.close.side_effect = RuntimeError("close failed")

    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        time.sleep(0.2)

        # Set flag
        mgr._force_reconnect.set()

        # Wait for consumer to process (close will raise)
        time.sleep(0.3)

        # Flag should still be cleared
        assert not mgr._force_reconnect.is_set()

        # close() was attempted
        fake_ticker.close.assert_called_once()

        mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 7: Paper mode does not start watchdog, no flag behavior
# ─────────────────────────────────────────────────────────────────────────────


def test_paper_mode_no_watchdog_no_flag(fake_logger):
    """
    FIX-064: Paper mode does not start watchdog, so flag is never set.
    """
    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=True,
    )
    mgr.connect()
    time.sleep(0.3)

    # No watchdog in paper mode
    assert mgr._watchdog_thread is None

    # Flag should never be set
    assert not mgr._force_reconnect.is_set()

    mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 8: Watchdog does not call close if ticker is None
# ─────────────────────────────────────────────────────────────────────────────


def test_consumer_no_close_if_ticker_none(fake_logger):
    """
    FIX-064: Consumer thread should not crash if ticker is None when flag is set.
    """
    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
    )
    # Don't actually connect, so ticker stays None
    mgr._stop_event.clear()
    mgr._consumer_thread = threading.Thread(
        target=mgr._consume_ticks,
        daemon=True,
        name="test-consumer",
    )
    mgr._consumer_thread.start()
    time.sleep(0.2)

    # Set flag even though ticker is None
    mgr._force_reconnect.set()

    # Wait for consumer to process
    time.sleep(0.3)

    # Should not crash; flag should be cleared
    assert not mgr._force_reconnect.is_set()

    mgr.disconnect()


# ─────────────────────────────────────────────────────────────────────────────
# Test 9: Integration test - full watchdog → flag → close → reconnect flow
# ─────────────────────────────────────────────────────────────────────────────


def test_full_watchdog_flag_close_flow(fake_logger, fake_ticker):
    """
    FIX-064: Full integration test - watchdog detects stale tick, sets flag,
    consumer closes, reconnect callback fires.
    """
    close_thread_name = None

    def capture_close_thread(*args, **kwargs):
        nonlocal close_thread_name
        close_thread_name = threading.current_thread().name

    fake_ticker.close.side_effect = capture_close_thread

    mgr = LiveFeedManager(
        api_key="fake",
        access_token="fake",
        logger=fake_logger,
        paper_mode=False,
        tick_stale_threshold_sec=1,  # Reduced for faster test
        watchdog_check_interval_sec=0.5,  # Check every 0.5 seconds
        market_windows=FakeMarketWindows(),
    )
    with patch.object(mgr, "_create_ticker", return_value=fake_ticker):
        mgr.connect()
        # Manually set connected state (fake ticker doesn't call _on_connect)
        mgr._connected = True

        # Arm watchdog with a tick
        mgr._last_tick_at = now_ist()
        time.sleep(0.6)  # Wait for watchdog to arm

        # Make tick stale
        mgr._last_tick_at = now_ist() - timedelta(seconds=2)

        # Wait for full flow: watchdog → flag → consumer → close
        time.sleep(1.5)

        # close() should have been called from consumer thread
        fake_ticker.close.assert_called_once()
        assert "consumer" in close_thread_name.lower()

        # Flag should be cleared by now
        assert not mgr._force_reconnect.is_set()

        # Simulate reconnect
        mgr._on_reconnect(fake_ticker, 1)
        assert mgr._reconnect_notified

        mgr.disconnect()
