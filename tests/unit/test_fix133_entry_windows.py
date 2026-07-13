"""
tests/unit/test_fix133_entry_windows.py

FIX-133 Item 22 / FIX-134 revert: Per-strategy entry time windows.
  - ALL strategies use uniform 09:25-15:00 window until paper testing complete.
  - StrategyConfig entry_start_time / entry_end_time fields exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from strategies.loader import StrategyLoader
from strategies.schema import StrategyConfig


class TestEntryWindows:

    def test_all_strategies_uniform_window(self) -> None:
        """FIX-134: ALL live strategies should have 09:25-15:00 until paper testing complete.

        The PB-01 shadow playbook (V3 Step 10b, v3_playbook:true) is exempt — its
        authoritative entry window (09:20-11:00 per the SPEC) is enforced by the
        watchlist entry stage, not the intraday webhook path this uniform window governs.
        """
        loader = StrategyLoader()
        strategies = loader.load_all_strategies(Path("config/strategies"))

        live = 0
        for name, s in strategies.items():
            if s.v3_playbook:
                continue
            live += 1
            assert s.entry_start_time == "09:25", f"{name} start={s.entry_start_time}"
            assert s.entry_end_time == "15:00", f"{name} end={s.entry_end_time}"
        print(f"  OK: all {live} live strategies have 09:25-15:00 window")

    def test_schema_has_entry_time_fields(self) -> None:
        """StrategyConfig schema has entry_start_time and entry_end_time."""
        cfg = StrategyConfig(
            name="test", display_name="Test", description="test",
            direction="LONG", intent="INTRADAY", order_protocol="CO_PLUS_TGT",
            pipeline="INTRADAY", horizon="SAME_DAY",
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
        TestEntryWindows().test_all_strategies_uniform_window,
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
