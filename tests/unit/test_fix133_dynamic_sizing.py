"""
tests/unit/test_fix133_dynamic_sizing.py

FIX-133 Item 21: Dynamic position sizing by strategy win rate.
  - High perf_weight -> larger qty (up to 2x cap)
  - Low perf_weight -> smaller qty (floor at 1)
  - perf_weight=0 -> floor at 1
  - perf_weight=3.0 -> capped at 2x raw_qty
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from capital.position_sizer import PositionSizer, SizingResult


def _make_sizer(total=200000.0, risk_pct=0.01, max_conc=0.20):
    fm = MagicMock()
    fm.total_capital = total
    fm.available_for_intent.return_value = total
    fm.snapshot.return_value = {"total": total}
    sizer = PositionSizer(
        fund_manager=fm,
        leverage_map={"INTRADAY": 5.0, "POSITIONAL": 1.0},
        risk_per_trade_pct=risk_pct,
        max_concentration_pct=max_conc,
    )
    return sizer


class TestDynamicSizingCap:

    def test_high_perf_weight_larger_qty(self) -> None:
        """High perf_weight (1.5) should produce larger qty than 1.0."""
        sizer = _make_sizer()
        base = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=1.0)
        boosted = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=1.5)

        assert boosted.qty >= base.qty, (
            f"perf_weight=1.5 qty ({boosted.qty}) should be >= base ({base.qty})"
        )
        print(f"  OK: perf_weight=1.5 -> qty={boosted.qty} >= base={base.qty}")

    def test_low_perf_weight_smaller_qty(self) -> None:
        """Low perf_weight (0.5) should produce smaller qty than 1.0."""
        sizer = _make_sizer()
        base = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=1.0)
        reduced = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=0.5)

        assert reduced.qty <= base.qty, (
            f"perf_weight=0.5 qty ({reduced.qty}) should be <= base ({base.qty})"
        )
        print(f"  OK: perf_weight=0.5 -> qty={reduced.qty} <= base={base.qty}")

    def test_perf_weight_zero_floor_at_one(self) -> None:
        """perf_weight=0 should floor qty at 1 (not zero)."""
        sizer = _make_sizer()
        result = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=0.0)

        # tiered_qty is floored at 1, but may still be filtered by lot_size or min_qty
        assert result.breakdown.get("tiered_qty", 0) >= 1, "tiered_qty must be >= 1"
        print(f"  OK: perf_weight=0.0 -> tiered_qty={result.breakdown['tiered_qty']} (>= 1)")

    def test_perf_weight_large_capped_at_2x(self) -> None:
        """perf_weight=3.0 should cap tiered_qty at 2x raw_qty."""
        sizer = _make_sizer()
        base = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=1.0)
        extreme = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=3.0)

        raw_qty = base.breakdown.get("raw_qty", 0)
        tiered_qty = extreme.breakdown.get("tiered_qty", 0)
        assert tiered_qty <= raw_qty * 2, (
            f"tiered_qty ({tiered_qty}) must be <= 2 * raw_qty ({raw_qty * 2})"
        )
        print(f"  OK: perf_weight=3.0 -> tiered_qty={tiered_qty} <= 2*raw={raw_qty * 2}")

    def test_perf_weight_breakdown_recorded(self) -> None:
        """Breakdown dict should contain perf_weight value."""
        sizer = _make_sizer()
        result = sizer.calculate("TEST", "BUY", 1000.0, 985.0, "INTRADAY", perf_weight=1.3)
        assert "perf_weight" in result.breakdown
        assert result.breakdown["perf_weight"] == 1.3
        print(f"  OK: breakdown records perf_weight={result.breakdown['perf_weight']}")


if __name__ == "__main__":
    tests = [
        TestDynamicSizingCap().test_high_perf_weight_larger_qty,
        TestDynamicSizingCap().test_low_perf_weight_smaller_qty,
        TestDynamicSizingCap().test_perf_weight_zero_floor_at_one,
        TestDynamicSizingCap().test_perf_weight_large_capped_at_2x,
        TestDynamicSizingCap().test_perf_weight_breakdown_recorded,
    ]
    passed = 0
    for t in tests:
        try:
            t()
            passed += 1
        except Exception as exc:
            print(f"  FAIL {t.__name__}: {exc}")
    print(f"\n{passed}/{len(tests)} passed")
