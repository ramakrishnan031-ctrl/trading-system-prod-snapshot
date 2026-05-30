"""
tests/unit/test_fix133_entry_windows.py

FIX-133 Item 22: Per-strategy entry time windows.
  - gap_fade strategies have 09:15-09:45 window
  - gap_go strategies have 09:20-11:00 window
  - positional strategies have 09:25-14:00 window
  - default strategies have 09:25-15:00 window
  - StrategyConfig entry_start_time / entry_end_time fields exist
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from strategies.loader import StrategyLoader
from strategies.schema import StrategyConfig


class TestEntryWindows:

    def test_gap_fade_morning_only(self) -> None:
        """gap_fade_long/short should have 09:15-09:45 window."""
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        for name in ["gap_fade_long", "gap_fade_short"]:
            assert name in strategies, f"{name} not loaded"
            s = strategies[name]
            assert s.entry_start_time == "09:15", f"{name} start={s.entry_start_time}"
            assert s.entry_end_time == "09:45", f"{name} end={s.entry_end_time}"
        print("  OK: gap_fade strategies have 09:15-09:45 window")

    def test_gap_go_morning_window(self) -> None:
        """gap_go_long/short should have 09:20-11:00 window."""
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        for name in ["gap_go_long", "gap_go_short"]:
            assert name in strategies, f"{name} not loaded"
            s = strategies[name]
            assert s.entry_start_time == "09:20", f"{name} start={s.entry_start_time}"
            assert s.entry_end_time == "11:00", f"{name} end={s.entry_end_time}"
        print("  OK: gap_go strategies have 09:20-11:00 window")

    def test_positional_strategies_end_at_14(self) -> None:
        """Positional strategies should end entries at 14:00."""
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        for name in ["positional_swing_long", "positional_momentum_long",
                     "positional_sector_rotation"]:
            assert name in strategies, f"{name} not loaded"
            s = strategies[name]
            assert s.entry_end_time == "14:00", f"{name} end={s.entry_end_time}"
        print("  OK: positional strategies end at 14:00")

    def test_default_strategies_full_window(self) -> None:
        """Intraday strategies have 09:25-15:00 default window."""
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        for name in ["first_pullback_long", "range_breakout_long", "vwap_bounce_long"]:
            assert name in strategies, f"{name} not loaded"
            s = strategies[name]
            assert s.entry_start_time == "09:25", f"{name} start={s.entry_start_time}"
            assert s.entry_end_time == "15:00", f"{name} end={s.entry_end_time}"
        print("  OK: default intraday strategies have 09:25-15:00 window")

    def test_schema_has_entry_time_fields(self) -> None:
        """StrategyConfig schema has entry_start_time and entry_end_time."""
        cfg = StrategyConfig(
            name="test", display_name="Test", description="test",
            direction="LONG", intent="INTRADAY", order_protocol="CO_PLUS_TGT",
            entry_method="LIMIT", sl_method="FIXED_PCT", sl_pct=0.01,
            tgt_method="RISK_REWARD", tgt_risk_reward=2.0,
            smart_tgt_enabled=False, pullback_wait_enabled=False,
            min_score=0, lot_size=1,
            entry_start_time="09:30", entry_end_time="14:30",
        )
        assert cfg.entry_start_time == "09:30"
        assert cfg.entry_end_time == "14:30"
        print("  OK: StrategyConfig entry_start_time/entry_end_time configurable")


if __name__ == "__main__":
    tests = [
        TestEntryWindows().test_gap_fade_morning_only,
        TestEntryWindows().test_gap_go_morning_window,
        TestEntryWindows().test_positional_strategies_end_at_14,
        TestEntryWindows().test_default_strategies_full_window,
        TestEntryWindows().test_schema_has_entry_time_fields,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
