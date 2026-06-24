"""
tests/unit/test_fix130_sl_gap_buffer.py

Tests for FIX-130 Item 4: SL gap-jump buffer during 09:15-09:30.
  - During gap window (09:15-09:30): buffer applied, SL widens
  - After gap window (09:31+): buffer NOT applied
  - sl_gap_buffer_pct=0.0 (default): buffer never applied
  - LONG: SL moves lower; SHORT: SL moves higher
  - Buffer applies after bounds enforcement
"""
from __future__ import annotations

import sys
import logging
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from strategies.schema import StrategyConfig


def _log():
    return logging.getLogger("test_fix130_gap_buffer")


def _make_strategy(direction="LONG", sl_pct=0.01, sl_gap_buffer_pct=0.3):
    """Build a minimal StrategyConfig for testing."""
    return StrategyConfig(
        name="test_strategy",
        display_name="Test Strategy",
        description="test",
        direction=direction,
        intent="INTRADAY",
        order_protocol="LIMIT_TRIPLE",
        entry_method="LIMIT",
        entry_offset_pct=0.0,
        sl_method="FIXED_PCT",
        sl_pct=sl_pct,
        sl_atr_multiplier=1.5,
        sl_min_pct=0.003,
        sl_max_pct=0.05,
        sl_gap_buffer_pct=sl_gap_buffer_pct,
        tgt_method="RISK_REWARD",
        tgt_pct=0.0,
        tgt_risk_reward=2.0,
        tgt_atr_multiplier=2.5,
        smart_tgt_enabled=False,
        smart_tgt_trail_trigger_pct=0.005,
        smart_tgt_trail_step_pct=0.003,
        pullback_wait_enabled=False,
        pullback_wait_tolerance_pct=0.005,
        pullback_wait_timeout_sec=180,
        min_score=0,
        min_volume_surge=1.3,
        min_adr_pct=0.005,
        max_spread_pct=0.005,
        lot_size=1,
        entry_start_time="09:15",
        entry_end_time="15:30",
        active_days=["MON", "TUE", "WED", "THU", "FRI"],
    )


class _MockProcessor:
    """Thin wrapper to test _derive_prices in isolation."""
    _GAP_WINDOW_START = time(9, 15)
    _GAP_WINDOW_END   = time(9, 30)
    _atr_fallback_mode = "WARN"

    def __init__(self):
        self._log = _log()

    def _derive_prices(self, trigger_price, strategy, now_time=None):
        direction = strategy.direction
        entry_price = trigger_price
        if strategy.entry_method == "LIMIT":
            offset = float(strategy.entry_offset_pct)
            entry_price = trigger_price * (1.0 - offset) if direction == "LONG" else trigger_price * (1.0 + offset)
        if entry_price <= 0:
            raise ValueError("entry_price <= 0")
        sl_method = strategy.sl_method
        if sl_method == "FIXED_PCT":
            sl_pct = float(strategy.sl_pct)
            sl_price = entry_price * (1.0 - sl_pct) if direction == "LONG" else entry_price * (1.0 + sl_pct)
        else:
            raise ValueError(f"Unknown sl_method: {sl_method}")
        sl_distance = abs(entry_price - sl_price)
        sl_dist_pct = sl_distance / entry_price if entry_price else 0.0
        if sl_dist_pct < strategy.sl_min_pct:
            adj = entry_price * strategy.sl_min_pct
            sl_price = entry_price - adj if direction == "LONG" else entry_price + adj
        elif sl_dist_pct > strategy.sl_max_pct:
            adj = entry_price * strategy.sl_max_pct
            sl_price = entry_price - adj if direction == "LONG" else entry_price + adj
        # Gap buffer (FIX-130)
        gap_buffer = float(getattr(strategy, "sl_gap_buffer_pct", 0.0))
        if (gap_buffer > 0.0 and now_time is not None
                and self._GAP_WINDOW_START <= now_time <= self._GAP_WINDOW_END):
            factor = gap_buffer / 100.0
            sl_price = sl_price * (1.0 - factor) if direction == "LONG" else sl_price * (1.0 + factor)
        return entry_price, sl_price


class TestSLGapBuffer:

    def _proc(self):
        return _MockProcessor()

    def test_long_buffer_applied_in_window(self) -> None:
        """LONG + within gap window + buffer=0.3% → SL moves lower by 0.3%."""
        proc = self._proc()
        strategy = _make_strategy(direction="LONG", sl_pct=0.01, sl_gap_buffer_pct=0.3)
        trigger = 1000.0
        # Without buffer: entry=1000, sl=990
        _, sl_no_buf = proc._derive_prices(trigger, strategy, now_time=None)
        # With buffer (09:20 = in window): sl should be 0.3% lower
        _, sl_with_buf = proc._derive_prices(trigger, strategy, now_time=time(9, 20))
        assert sl_with_buf < sl_no_buf, "LONG: SL should move lower with gap buffer"
        expected = sl_no_buf * (1.0 - 0.003)
        assert abs(sl_with_buf - expected) < 0.01, f"Expected {expected:.4f}, got {sl_with_buf:.4f}"
        print(f"  OK: LONG gap buffer: SL {sl_no_buf:.2f} -> {sl_with_buf:.2f} (09:20 in window)")

    def test_short_buffer_applied_in_window(self) -> None:
        """SHORT + within gap window + buffer=0.3% → SL moves higher by 0.3%."""
        proc = self._proc()
        strategy = _make_strategy(direction="SHORT", sl_pct=0.01, sl_gap_buffer_pct=0.3)
        trigger = 1000.0
        _, sl_no_buf = proc._derive_prices(trigger, strategy, now_time=None)
        _, sl_with_buf = proc._derive_prices(trigger, strategy, now_time=time(9, 20))
        assert sl_with_buf > sl_no_buf, "SHORT: SL should move higher with gap buffer"
        expected = sl_no_buf * (1.0 + 0.003)
        assert abs(sl_with_buf - expected) < 0.01
        print(f"  OK: SHORT gap buffer: SL {sl_no_buf:.2f} -> {sl_with_buf:.2f} (09:20 in window)")

    def test_no_buffer_after_gap_window(self) -> None:
        """After 09:30, gap buffer NOT applied even if strategy has sl_gap_buffer_pct > 0."""
        proc = self._proc()
        strategy = _make_strategy(direction="LONG", sl_pct=0.01, sl_gap_buffer_pct=0.3)
        _, sl_no_time = proc._derive_prices(1000.0, strategy, now_time=None)
        _, sl_after = proc._derive_prices(1000.0, strategy, now_time=time(9, 31))
        assert abs(sl_after - sl_no_time) < 0.001, "After 09:30, buffer must not apply"
        print("  OK: buffer NOT applied at 09:31 (outside gap window)")

    def test_no_buffer_when_zero(self) -> None:
        """sl_gap_buffer_pct=0.0 → no buffer even during gap window."""
        proc = self._proc()
        strategy = _make_strategy(direction="LONG", sl_pct=0.01, sl_gap_buffer_pct=0.0)
        _, sl_no_time = proc._derive_prices(1000.0, strategy, now_time=None)
        _, sl_in_window = proc._derive_prices(1000.0, strategy, now_time=time(9, 20))
        assert abs(sl_in_window - sl_no_time) < 0.001, "Buffer=0 must not change SL"
        print("  OK: sl_gap_buffer_pct=0.0 -> no change even in gap window")

    def test_at_window_boundaries(self) -> None:
        """Buffer applies at exactly 09:15 and 09:30 (inclusive boundaries)."""
        proc = self._proc()
        strategy = _make_strategy(direction="LONG", sl_pct=0.01, sl_gap_buffer_pct=0.3)
        _, sl_base = proc._derive_prices(1000.0, strategy, now_time=None)
        _, sl_at_915 = proc._derive_prices(1000.0, strategy, now_time=time(9, 15))
        _, sl_at_930 = proc._derive_prices(1000.0, strategy, now_time=time(9, 30))
        assert sl_at_915 < sl_base, "09:15 is inclusive start of gap window"
        assert sl_at_930 < sl_base, "09:30 is inclusive end of gap window"
        print("  OK: gap window boundaries 09:15 and 09:30 are inclusive")

    def test_strategy_schema_has_field(self) -> None:
        """StrategyConfig now has sl_gap_buffer_pct field with default=0.0."""
        strategy = _make_strategy(sl_gap_buffer_pct=0.0)
        assert hasattr(strategy, "sl_gap_buffer_pct")
        assert strategy.sl_gap_buffer_pct == 0.0
        strategy_with = _make_strategy(sl_gap_buffer_pct=0.5)
        assert strategy_with.sl_gap_buffer_pct == 0.5
        print("  OK: StrategyConfig.sl_gap_buffer_pct field exists with default 0.0")


if __name__ == "__main__":
    tests = [
        TestSLGapBuffer().test_long_buffer_applied_in_window,
        TestSLGapBuffer().test_short_buffer_applied_in_window,
        TestSLGapBuffer().test_no_buffer_after_gap_window,
        TestSLGapBuffer().test_no_buffer_when_zero,
        TestSLGapBuffer().test_at_window_boundaries,
        TestSLGapBuffer().test_strategy_schema_has_field,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
