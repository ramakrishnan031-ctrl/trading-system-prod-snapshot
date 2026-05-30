"""
tests/unit/test_fix132_breakeven_manager.py

FIX-132 Item 8: Trailing SL activates at 60% of target.
  - At 60% target distance: SL moves to breakeven (entry price)
  - At 80% target distance: SL moves to 40% of target distance
  - Each milestone fires ONCE (idempotent)
  - adapter.modify_order called with correct trigger_price
  - SHORT direction: mirror logic (SL moves up)
"""
from __future__ import annotations

import sys
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orders.breakeven_manager import BreakevenManager
from strategies.schema import StrategyConfig


def _log():
    return logging.getLogger("test_breakeven")


@dataclass
class _MockCandle:
    symbol: str
    high: float
    low: float
    close: float = 0.0
    instrument_token: int = 0


@dataclass
class _ModifyResult:
    broker_order_id: str
    success: bool
    reason: str = ""


class _MockAdapter:
    def __init__(self, should_succeed=True):
        self.modify_calls = []
        self._succeed = should_succeed

    def modify_order(self, broker_order_id, trigger_price=None, **_):
        self.modify_calls.append({
            "broker_order_id": broker_order_id,
            "trigger_price": trigger_price,
        })
        return _ModifyResult(
            broker_order_id=broker_order_id,
            success=self._succeed,
            reason="" if self._succeed else "rejected",
        )


class _MockStore:
    def __init__(self, sl_broker_order_id="KITE_SL_001"):
        self._sl_id = sl_broker_order_id

    def fetch_one(self, sql, params):
        if "leg = 'SL'" in sql:
            class _Row:
                def __getitem__(self, key):
                    return "KITE_SL_001" if key == "broker_order_id" else None
            return _Row()
        return None


def _make_mgr(succeed=True):
    return BreakevenManager(
        adapter=_MockAdapter(should_succeed=succeed),
        state_store=_MockStore(),
        logger=_log(),
    )


class TestBreakevenManagerLong:

    def test_breakeven_triggered_at_60_pct(self) -> None:
        """LONG: price at 60% target distance → SL advances to breakeven."""
        mgr = _make_mgr()
        entry, target = 1000.0, 1100.0  # target_distance = 100
        mgr.register_trade("t1", "RELIANCE", "LONG", entry, target,
                           breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0)

        # 60% of 100 = 60 → price reaches 1060
        candle = _MockCandle("RELIANCE", high=1060.0, low=990.0)
        mgr.on_candle_close(candle)

        calls = mgr._adapter.modify_calls
        assert len(calls) == 1, f"Expected 1 modify call, got {len(calls)}"
        assert abs(calls[0]["trigger_price"] - entry) < 0.01, (
            f"SL should be at breakeven {entry}, got {calls[0]['trigger_price']}"
        )
        print(f"  OK LONG: 60% trigger → SL = breakeven {entry}")

    def test_partial_lock_triggered_at_80_pct(self) -> None:
        """LONG: price at 80% target distance → SL advances to 40% of target distance."""
        mgr = _make_mgr()
        entry, target = 1000.0, 1100.0  # target_distance = 100
        mgr.register_trade("t2", "RELIANCE", "LONG", entry, target,
                           breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
                           partial_lock_sl_pct=40.0)

        # 80% of 100 = 80 → price reaches 1080
        candle = _MockCandle("RELIANCE", high=1082.0, low=990.0)
        mgr.on_candle_close(candle)

        calls = mgr._adapter.modify_calls
        assert len(calls) == 1
        expected_sl = entry + 0.40 * (target - entry)  # 1040.0
        assert abs(calls[0]["trigger_price"] - expected_sl) < 0.01, (
            f"SL should be at 40% lock {expected_sl}, got {calls[0]['trigger_price']}"
        )
        print(f"  OK LONG: 80% trigger → SL = 40% lock {expected_sl}")

    def test_breakeven_fires_only_once(self) -> None:
        """Each milestone fires only once — second candle at same level does NOT re-modify."""
        mgr = _make_mgr()
        mgr.register_trade("t3", "RELIANCE", "LONG", 1000.0, 1100.0)

        candle = _MockCandle("RELIANCE", high=1065.0, low=990.0)
        mgr.on_candle_close(candle)  # fires breakeven
        mgr.on_candle_close(candle)  # should NOT fire again

        calls = mgr._adapter.modify_calls
        assert len(calls) == 1, f"Breakeven should fire ONCE; got {len(calls)} calls"
        print("  OK: breakeven fires exactly once (idempotent)")

    def test_below_threshold_no_modification(self) -> None:
        """Price below 60% threshold: no SL modification."""
        mgr = _make_mgr()
        mgr.register_trade("t4", "RELIANCE", "LONG", 1000.0, 1100.0)

        candle = _MockCandle("RELIANCE", high=1050.0, low=990.0)  # only 50% progress
        mgr.on_candle_close(candle)

        assert len(mgr._adapter.modify_calls) == 0
        print("  OK: below 60% threshold → no modification")

    def test_partial_lock_skips_breakeven_step(self) -> None:
        """When 80% is hit directly, only ONE modify call (partial lock, not two)."""
        mgr = _make_mgr()
        mgr.register_trade("t5", "RELIANCE", "LONG", 1000.0, 1100.0,
                           breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
                           partial_lock_sl_pct=40.0)

        # Price jumps directly to 85% progress
        candle = _MockCandle("RELIANCE", high=1085.0, low=990.0)
        mgr.on_candle_close(candle)

        calls = mgr._adapter.modify_calls
        assert len(calls) == 1, f"Should fire partial_lock (not breakeven+partial), got {len(calls)}"
        expected_sl = 1000.0 + 0.40 * 100.0  # 1040
        assert abs(calls[0]["trigger_price"] - expected_sl) < 0.01
        print("  OK: direct jump to 80%+ → one partial_lock call only")


class TestBreakevenManagerShort:

    def test_short_breakeven_triggered(self) -> None:
        """SHORT: price falls 60% toward target → SL advances down to breakeven."""
        mgr = _make_mgr()
        entry, target = 1100.0, 1000.0  # target_distance = 100, direction DOWN
        mgr.register_trade("t6", "RELIANCE", "SHORT", entry, target,
                           breakeven_trigger_pct=60.0)

        # 60% of 100 = 60 → price falls to 1040
        candle = _MockCandle("RELIANCE", high=1110.0, low=1038.0)
        mgr.on_candle_close(candle)

        calls = mgr._adapter.modify_calls
        assert len(calls) == 1
        assert abs(calls[0]["trigger_price"] - entry) < 0.01, (
            f"SHORT breakeven SL should be at entry {entry}, got {calls[0]['trigger_price']}"
        )
        print(f"  OK SHORT: 60% trigger → SL = breakeven {entry}")

    def test_short_partial_lock_triggered(self) -> None:
        """SHORT: price at 80% target distance → SL at 40% of target distance (above entry)."""
        mgr = _make_mgr()
        entry, target = 1100.0, 1000.0  # target_distance = 100, direction DOWN
        mgr.register_trade("t7", "RELIANCE", "SHORT", entry, target,
                           breakeven_trigger_pct=60.0, partial_lock_trigger_pct=80.0,
                           partial_lock_sl_pct=40.0)

        # 80% of 100 = 80 → price falls to 1020
        candle = _MockCandle("RELIANCE", high=1110.0, low=1018.0)
        mgr.on_candle_close(candle)

        calls = mgr._adapter.modify_calls
        assert len(calls) == 1
        expected_sl = entry - 0.40 * (entry - target)  # 1060
        assert abs(calls[0]["trigger_price"] - expected_sl) < 0.01
        print(f"  OK SHORT: 80% trigger → SL = 40% lock {expected_sl}")


class TestBreakevenManagerBrokerFailure:

    def test_no_state_update_on_broker_failure(self) -> None:
        """If adapter.modify_order fails, milestone is NOT marked as applied."""
        mgr = BreakevenManager(
            adapter=_MockAdapter(should_succeed=False),
            state_store=_MockStore(),
            logger=_log(),
        )
        mgr.register_trade("t8", "RELIANCE", "LONG", 1000.0, 1100.0)

        candle = _MockCandle("RELIANCE", high=1065.0, low=990.0)
        mgr.on_candle_close(candle)  # modify fails
        mgr.on_candle_close(candle)  # should retry since milestone not applied

        calls = mgr._adapter.modify_calls
        assert len(calls) >= 2, "Should retry when broker rejects modification"
        print(f"  OK: broker failure → retry on next candle ({len(calls)} calls)")


class TestStrategyConfigTrailingSL:

    def test_trailing_sl_fields_have_defaults(self) -> None:
        """StrategyConfig has trailing_sl fields with correct defaults."""
        cfg = StrategyConfig(
            name="test",
            display_name="Test",
            description="test",
            direction="LONG",
            intent="INTRADAY",
            order_protocol="LIMIT_TRIPLE",
            entry_method="LIMIT",
            sl_method="FIXED_PCT",
            sl_pct=0.01,
            tgt_method="RISK_REWARD",
            tgt_risk_reward=2.0,
            smart_tgt_enabled=False,
            pullback_wait_enabled=False,
            min_score=0,
            lot_size=1,
        )
        assert cfg.trailing_sl_enabled is False
        assert cfg.trailing_sl_breakeven_trigger_pct == 60.0
        assert cfg.trailing_sl_partial_lock_trigger_pct == 80.0
        assert cfg.trailing_sl_partial_lock_sl_pct == 40.0
        print("  OK: StrategyConfig trailing_sl fields exist with defaults")

    def test_trailing_sl_enabled_configurable(self) -> None:
        """trailing_sl_enabled=True can be set in StrategyConfig."""
        cfg = StrategyConfig(
            name="test",
            display_name="Test",
            description="test",
            direction="LONG",
            intent="INTRADAY",
            order_protocol="LIMIT_TRIPLE",
            entry_method="LIMIT",
            sl_method="FIXED_PCT",
            sl_pct=0.01,
            tgt_method="RISK_REWARD",
            tgt_risk_reward=2.0,
            smart_tgt_enabled=False,
            pullback_wait_enabled=False,
            min_score=0,
            lot_size=1,
            trailing_sl_enabled=True,
            trailing_sl_breakeven_trigger_pct=55.0,
        )
        assert cfg.trailing_sl_enabled is True
        assert cfg.trailing_sl_breakeven_trigger_pct == 55.0
        print("  OK: trailing_sl fields configurable per strategy")


if __name__ == "__main__":
    tests = [
        TestBreakevenManagerLong().test_breakeven_triggered_at_60_pct,
        TestBreakevenManagerLong().test_partial_lock_triggered_at_80_pct,
        TestBreakevenManagerLong().test_breakeven_fires_only_once,
        TestBreakevenManagerLong().test_below_threshold_no_modification,
        TestBreakevenManagerLong().test_partial_lock_skips_breakeven_step,
        TestBreakevenManagerShort().test_short_breakeven_triggered,
        TestBreakevenManagerShort().test_short_partial_lock_triggered,
        TestBreakevenManagerBrokerFailure().test_no_state_update_on_broker_failure,
        TestStrategyConfigTrailingSL().test_trailing_sl_fields_have_defaults,
        TestStrategyConfigTrailingSL().test_trailing_sl_enabled_configurable,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
