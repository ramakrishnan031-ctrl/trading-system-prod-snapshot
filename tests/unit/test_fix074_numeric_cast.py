"""
tests/unit/test_fix074_numeric_cast.py

FIX-074: Type-cast numeric fields at ingestion

Prevents TypeError when Chartink sends numeric values as strings.
"""
from __future__ import annotations

import json
import queue
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

from core.state_store import StateStore
from core.time_authority import now_ist
from signals.webhook_receiver import WebhookReceiver


IST = ZoneInfo("Asia/Kolkata")


def _make_minimal_config():
    """Minimal config for webhook receiver tests."""
    cfg = SimpleNamespace()
    cfg.system = SimpleNamespace()
    cfg.system.signal_queue = SimpleNamespace(
        capacity=100,
        backpressure_pct=0.9,
        expiry_sec=300,
    )
    cfg.scan_webhook_map = SimpleNamespace(
        scanners={"test_scanner": {"name": "test_scanner"}}
    )
    cfg.webhook = SimpleNamespace(require_hmac=False)
    return cfg


def _recent_trigger_time() -> str:
    """Generate a triggered_at time string that's within expiry window (5min ago)."""
    now = now_ist()
    return now.strftime("%Y-%m-%d %H:%M:%S")


def _make_receiver(tmp_path: Path):
    """Build a WebhookReceiver for testing."""
    store = StateStore(tmp_path / "test.db")
    signal_queue = queue.Queue(maxsize=100)
    config = _make_minimal_config()

    market_windows = MagicMock()
    market_windows.is_entry_allowed.return_value = True

    kill_switch = MagicMock()
    kill_switch.is_active.return_value = False

    logger = MagicMock()

    receiver = WebhookReceiver(
        signal_queue=signal_queue,
        state_store=store,
        config=config,
        market_windows=market_windows,
        kill_switch=kill_switch,
        logger=logger,
    )

    return receiver, signal_queue, store


class TestFix074NumericCast:
    """FIX-074: Type-cast numeric fields at webhook ingestion."""

    def test_string_price_cast_to_float(self):
        """String "2500.50" -> float 2500.50 in queued signal."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    "price": "2500.50",  # Top-level string price
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1

                # Verify the queued signal has float price
                sig_id, scanner, symbol, price, triggered_at = signal_queue.get(timeout=1)
                assert isinstance(price, float)
                assert price == 2500.50
            finally:
                store.close()

    def test_float_price_unchanged(self):
        """Already-float price 2500.50 -> unchanged."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    "price": 2500.50,  # Already float
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1

                # Verify float unchanged
                sig_id, scanner, symbol, price, triggered_at = signal_queue.get(timeout=1)
                assert isinstance(price, float)
                assert price == 2500.50
            finally:
                store.close()

    def test_invalid_critical_price_rejected(self):
        """String "invalid" in critical field (price) -> 400 rejected."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "100.0",
                    "triggered_at": _recent_trigger_time(),
                    "price": "invalid",  # Critical field with invalid value
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                # Should reject with 400
                assert resp.status_code == 400
                data = resp.get_json()
                assert "Invalid payload" in data["error"]
                assert "price" in data["error"]

                # Queue should be empty (signal not queued)
                assert signal_queue.empty()
            finally:
                store.close()

    def test_invalid_entry_price_rejected(self):
        """String "abc" in critical field (entry_price) -> 400 rejected."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "100.0",
                    "triggered_at": _recent_trigger_time(),
                    "entry_price": "abc",  # Critical field with invalid value
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                assert resp.status_code == 400
                data = resp.get_json()
                assert "Invalid payload" in data["error"]
                assert "entry_price" in data["error"]
                assert signal_queue.empty()
            finally:
                store.close()

    def test_noncritical_sl_pct_string_cast(self):
        """String "0.02" in non-critical field (sl_pct) -> cast to 0.02 float."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    "sl_pct": "0.02",  # Non-critical string
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                # Should succeed
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1

                # Signal queued successfully
                assert not signal_queue.empty()
            finally:
                store.close()

    def test_noncritical_field_invalid_set_to_none(self):
        """Non-critical field with invalid value -> set to None, continue."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    "sl_pct": "invalid",  # Non-critical invalid value
                    "target_pct": "0.05",  # Valid non-critical
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                # Should succeed despite invalid sl_pct
                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1

                # Signal queued successfully
                assert not signal_queue.empty()
            finally:
                store.close()

    def test_missing_price_field_handled(self):
        """Missing price field entirely -> processed normally (uses trigger_prices)."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    # No "price" field - uses trigger_prices
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1
            finally:
                store.close()

    def test_multiple_fields_cast_correctly(self):
        """Multiple numeric fields all cast correctly."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    "price": "2500.50",
                    "entry_price": "2505.0",
                    "sl_pct": "0.02",
                    "target_pct": "0.05",
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1
            finally:
                store.close()

    def test_none_value_skipped(self):
        """Field with None value -> skipped, not cast."""
        with TemporaryDirectory() as tmp:
            receiver, signal_queue, store = _make_receiver(Path(tmp))

            try:
                payload = {
                    "stocks": "RELIANCE",
                    "trigger_prices": "2500.50",
                    "triggered_at": _recent_trigger_time(),
                    "sl_pct": None,  # None value should be skipped
                }

                with receiver.app.test_client() as client:
                    resp = client.post(
                        "/webhook/test_scanner",
                        data=json.dumps(payload),
                        content_type="application/json",
                    )

                assert resp.status_code == 200
                data = resp.get_json()
                assert data["accepted"] == 1
            finally:
                store.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
