"""
tests/unit/test_price_math.py

Validates orders/price_math.py (FIX-004).

Run: python -m pytest tests/unit/test_price_math.py -v
Or:  python tests/unit/test_price_math.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from orders.price_math import calc_sl_price, calc_tgt_price


# ─────────────────────────────────────────────────────────────────────────────
# calc_sl_price
# ─────────────────────────────────────────────────────────────────────────────

def test_calc_sl_price_long() -> None:
    sl = calc_sl_price("LONG", entry_price=100.0, sl_pct=0.02)
    assert abs(sl - 98.0) < 1e-9
    print("  OK calc_sl_price LONG: entry=100, pct=2% -> sl=98 (FIX-004)")


def test_calc_sl_price_short() -> None:
    sl = calc_sl_price("SHORT", entry_price=100.0, sl_pct=0.02)
    assert abs(sl - 102.0) < 1e-9
    print("  OK calc_sl_price SHORT: entry=100, pct=2% -> sl=102 (FIX-004)")


def test_calc_sl_price_accepts_buy_sell_side() -> None:
    sl_buy = calc_sl_price("BUY", entry_price=200.0, sl_pct=0.01)
    sl_long = calc_sl_price("LONG", entry_price=200.0, sl_pct=0.01)
    assert abs(sl_buy - sl_long) < 1e-9, "BUY must equal LONG"

    sl_sell = calc_sl_price("SELL", entry_price=200.0, sl_pct=0.01)
    sl_short = calc_sl_price("SHORT", entry_price=200.0, sl_pct=0.01)
    assert abs(sl_sell - sl_short) < 1e-9, "SELL must equal SHORT"
    print("  OK calc_sl_price: BUY==LONG, SELL==SHORT aliases (FIX-004)")


# ─────────────────────────────────────────────────────────────────────────────
# calc_tgt_price
# ─────────────────────────────────────────────────────────────────────────────

def test_calc_tgt_price_long_rr2() -> None:
    # entry=100, sl=98 → risk=2 → tgt=100+2*2=104
    tgt = calc_tgt_price("LONG", entry_price=100.0, sl_price=98.0, rr_ratio=2.0)
    assert abs(tgt - 104.0) < 1e-9
    print("  OK calc_tgt_price LONG 1:2 R:R (FIX-004)")


def test_calc_tgt_price_short_rr2() -> None:
    # entry=100, sl=102 → risk=2 → tgt=100-2*2=96
    tgt = calc_tgt_price("SHORT", entry_price=100.0, sl_price=102.0, rr_ratio=2.0)
    assert abs(tgt - 96.0) < 1e-9
    print("  OK calc_tgt_price SHORT 1:2 R:R (FIX-004)")


def test_calc_tgt_price_matches_order_placer_formula() -> None:
    """Ensure price_math matches the old inline order_placer formula exactly."""
    entry, sl_price, rr = 2500.0, 2450.0, 2.5
    risk = abs(entry - sl_price)
    expected_long = entry + risk * rr
    expected_short = entry - risk * rr

    assert abs(calc_tgt_price("BUY", entry, sl_price, rr) - expected_long) < 1e-9
    assert abs(calc_tgt_price("SELL", entry, sl_price, rr) - expected_short) < 1e-9
    print("  OK calc_tgt_price matches old order_placer formula (FIX-004)")


def test_calc_tgt_price_matches_shadow_tracker_formula() -> None:
    """Ensure price_math matches the old inline shadow_tracker RISK_REWARD formula."""
    entry, sl, ratio = 1500.0, 1470.0, 1.5
    sl_dist = abs(entry - sl)
    expected_long = entry + sl_dist * ratio
    expected_short = entry - sl_dist * ratio

    assert abs(calc_tgt_price("LONG", entry, sl, ratio) - expected_long) < 1e-9
    assert abs(calc_tgt_price("SHORT", entry, sl, ratio) - expected_short) < 1e-9
    print("  OK calc_tgt_price matches old shadow_tracker formula (FIX-004)")


def test_calc_tgt_price_sl_equals_entry_gives_entry() -> None:
    """Degenerate case: sl == entry → risk = 0 → tgt = entry."""
    tgt = calc_tgt_price("LONG", entry_price=100.0, sl_price=100.0, rr_ratio=2.0)
    assert abs(tgt - 100.0) < 1e-9
    print("  OK calc_tgt_price: sl==entry -> tgt==entry (FIX-004)")


# ─────────────────────────────────────────────────────────────────────────────
# Standalone runner
# ─────────────────────────────────────────────────────────────────────────────

def run_all_tests() -> int:
    tests = [
        test_calc_sl_price_long,
        test_calc_sl_price_short,
        test_calc_sl_price_accepts_buy_sell_side,
        test_calc_tgt_price_long_rr2,
        test_calc_tgt_price_short_rr2,
        test_calc_tgt_price_matches_order_placer_formula,
        test_calc_tgt_price_matches_shadow_tracker_formula,
        test_calc_tgt_price_sl_equals_entry_gives_entry,
    ]

    print("=" * 60)
    print("price_math.py -- Test Suite (FIX-004)")
    print("=" * 60)

    failed = []
    for test in tests:
        print(f"\n-> {test.__name__}")
        try:
            test()
        except AssertionError as e:
            failed.append((test.__name__, f"AssertionError: {e}"))
            print(f"  FAIL: {e}")
        except Exception as e:
            failed.append((test.__name__, f"{type(e).__name__}: {e}"))
            print(f"  ERROR: {type(e).__name__}: {e}")

    print("\n" + "=" * 60)
    if failed:
        print(f"FAILED: {len(failed)} of {len(tests)} tests")
        for name, err in failed:
            print(f"  FAIL {name}: {err}")
        return 1

    print(f"PASSED: all {len(tests)} tests")
    return 0


if __name__ == "__main__":
    sys.exit(run_all_tests())
