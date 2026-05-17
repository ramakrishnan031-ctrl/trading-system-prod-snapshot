"""
tests/unit/test_phase21_batch1.py -- Phase 21 audit fixes (batch 1)

FIX-098: Safe dictionary iteration (MEM-002)
Tests for order_placer._pending_exit_retry safe iteration under modification.
"""
from __future__ import annotations

import logging
import unittest
from unittest.mock import MagicMock

from core.events import EventBus
from orders.order_placer import OrderPlacer, _ExitRetryParams


class TestFix098SafeDictIteration(unittest.TestCase):
    """FIX-098: Safe dictionary iteration prevents RuntimeError (MEM-002)."""

    def _minimal_placer_kwargs(self) -> dict:
        """Minimal kwargs for OrderPlacer construction."""
        return {
            "entry_engine": MagicMock(),
            "order_manager": MagicMock(),
            "order_monitor": MagicMock(),
            "fund_manager": MagicMock(),
            "bus": EventBus(),
            "logger": logging.getLogger("test_fix098"),
            "cost_calculator": MagicMock(),
            "product_resolver": MagicMock(),
        }

    def _make_mock_fill_entry(self, trade_id: str, symbol: str) -> MagicMock:
        """Create a mock _FillEntry with just the fields we need for the test."""
        mock_entry = MagicMock()
        mock_entry.symbol = symbol
        return mock_entry

    def test_fix098_pending_exit_retry_safe_under_modification(self) -> None:
        """FIX-098: Multiple ticks trigger dict iteration after deletion - no RuntimeError."""
        placer = OrderPlacer(**self._minimal_placer_kwargs())

        # Mock instrument_cache to return a valid row with instrument_token
        mock_cache = MagicMock()
        mock_row = MagicMock()
        mock_row.instrument_token = 123
        mock_cache.get_by_symbol.return_value = mock_row
        placer._instrument_cache = mock_cache

        # Populate _pending_exit_retry with 3 entries for symbol "RELIANCE"
        for i in range(1, 4):
            params = _ExitRetryParams(
                trade_id=f"T{i}",
                fill_entry=self._make_mock_fill_entry(f"T{i}", "RELIANCE"),
                qty_filled=1,
                avg_fill_price=2500.0 + i,
                reason="test",
                retry_count=0,
            )
            placer._pending_exit_retry[f"T{i}"] = params

        # Mock _retry_limit_triple_exits to do nothing (avoid actual broker calls)
        placer._retry_limit_triple_exits = MagicMock()

        # Send 3 ticks for the same symbol with valid LTP
        # Each tick should trigger iteration over _pending_exit_retry.items()
        # After first tick processes T1, dict is modified (T1 deleted)
        # Second tick iteration should NOT raise RuntimeError due to list() wrapper
        ticks = [
            {"instrument_token": 123, "last_price": 2500.0},
            {"instrument_token": 123, "last_price": 2501.0},
            {"instrument_token": 123, "last_price": 2502.0},
        ]

        # This should NOT raise RuntimeError
        placer._on_ltp_tick_for_retry(ticks)

        # Verify all 3 entries were retried and removed
        self.assertEqual(len(placer._pending_exit_retry), 0)
        self.assertEqual(placer._retry_limit_triple_exits.call_count, 3)
        print("  OK FIX-098: safe dict iteration under modification")

    def test_fix098_no_runtime_error_on_empty_after_first_tick(self) -> None:
        """FIX-098: Edge case - dict becomes empty after first tick, second tick iterates safely."""
        placer = OrderPlacer(**self._minimal_placer_kwargs())

        mock_cache = MagicMock()
        mock_row = MagicMock()
        mock_row.instrument_token = 123
        mock_cache.get_by_symbol.return_value = mock_row
        placer._instrument_cache = mock_cache

        # Single entry
        params = _ExitRetryParams(
            trade_id="T1",
            fill_entry=self._make_mock_fill_entry("T1", "RELIANCE"),
            qty_filled=1,
            avg_fill_price=2500.0,
            reason="test",
            retry_count=0,
        )
        placer._pending_exit_retry["T1"] = params

        placer._retry_limit_triple_exits = MagicMock()

        # Two ticks - first processes T1 (dict becomes empty), second iterates over empty dict
        ticks = [
            {"instrument_token": 123, "last_price": 2500.0},
            {"instrument_token": 123, "last_price": 2501.0},
        ]

        # Should NOT raise RuntimeError
        placer._on_ltp_tick_for_retry(ticks)

        self.assertEqual(len(placer._pending_exit_retry), 0)
        self.assertEqual(placer._retry_limit_triple_exits.call_count, 1)
        print("  OK FIX-098: safe iteration when dict becomes empty")

    def test_fix098_concurrent_modification_simulation(self) -> None:
        """FIX-098: Simulate worst case - all 3 entries for same symbol, all deleted in one tick batch."""
        placer = OrderPlacer(**self._minimal_placer_kwargs())

        mock_cache = MagicMock()
        mock_row = MagicMock()
        mock_row.instrument_token = 123
        mock_cache.get_by_symbol.return_value = mock_row
        placer._instrument_cache = mock_cache

        # 3 entries for RELIANCE
        for i in range(1, 4):
            params = _ExitRetryParams(
                trade_id=f"T{i}",
                fill_entry=self._make_mock_fill_entry(f"T{i}", "RELIANCE"),
                qty_filled=1,
                avg_fill_price=2500.0 + i,
                reason="test",
                retry_count=0,
            )
            placer._pending_exit_retry[f"T{i}"] = params

        placer._retry_limit_triple_exits = MagicMock()

        # Single tick that matches all 3 - all will be deleted in one go
        # The list comprehension at line 2425 creates snapshot, so safe
        ticks = [{"instrument_token": 123, "last_price": 2500.0}]

        placer._on_ltp_tick_for_retry(ticks)

        self.assertEqual(len(placer._pending_exit_retry), 0)
        self.assertEqual(placer._retry_limit_triple_exits.call_count, 3)
        print("  OK FIX-098: safe when all entries deleted in one tick")


if __name__ == "__main__":
    unittest.main()
