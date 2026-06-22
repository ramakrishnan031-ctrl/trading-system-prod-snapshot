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


# ── Circuit-band placeability gate (clamp_exit_into_band) — E.1 matrix ────────
# Polarity (the crux):
#   TGT (profit side):     LONG wrong = price <= fill ; SHORT wrong = price >= fill
#   SL  (protective side): LONG wrong = price >= fill ; SHORT wrong = price <= fill

def test_gate_tgt_long_clamped_below_fill_unplaceable():
    """NOCIL: LONG TGT 197.12 clamped to upper*0.98=187.00 < fill 189.78."""
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        197.12, leg="TGT", direction="LONG", entry_fill=189.78,
        upper_circuit=190.82, lower_circuit=127.22,
    )
    assert r.was_clamped is True
    assert r.price == 187.00
    assert r.placeable is False
    assert r.price < 189.78


def test_gate_tgt_long_clamped_above_fill_placeable():
    """Reduced-but-profitable target: clamp keeps it above the fill."""
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        130.0, leg="TGT", direction="LONG", entry_fill=100.0,
        upper_circuit=120.0, lower_circuit=80.0,
    )
    assert r.was_clamped is True
    assert r.price > 100.0
    assert r.placeable is True


def test_gate_tgt_short_clamped_above_fill_unplaceable():
    """SHORT TGT clamped UP off the lower circuit to >= fill -> wrong-side."""
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        90.0, leg="TGT", direction="SHORT", entry_fill=100.0,
        upper_circuit=130.0, lower_circuit=99.0,
    )
    assert r.was_clamped is True
    assert r.price >= 100.0
    assert r.placeable is False


def test_gate_tgt_short_clamped_below_fill_placeable():
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        85.0, leg="TGT", direction="SHORT", entry_fill=100.0,
        upper_circuit=130.0, lower_circuit=90.0,
    )
    assert r.was_clamped is True
    assert r.price < 100.0
    assert r.placeable is True


def test_gate_sl_long_clamped_below_fill_is_benign_placeable():
    """A LONG SL clamped UP off the lower circuit but still BELOW fill is a
    valid tighter stop -> MUST NOT be rejected (the polarity-overfire guard)."""
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        85.0, leg="SL", direction="LONG", entry_fill=100.0,
        upper_circuit=130.0, lower_circuit=90.0,
    )
    assert r.was_clamped is True
    assert r.price < 100.0
    assert r.placeable is True


def test_gate_sl_long_clamped_at_or_above_fill_unplaceable():
    """LONG SL clamped UP to >= fill = instant stop-out -> unplaceable."""
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        95.0, leg="SL", direction="LONG", entry_fill=100.0,
        upper_circuit=130.0, lower_circuit=99.0,
    )
    assert r.price >= 100.0
    assert r.placeable is False


def test_gate_sl_short_clamped_above_fill_is_benign_placeable():
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        115.0, leg="SL", direction="SHORT", entry_fill=100.0,
        upper_circuit=110.0, lower_circuit=70.0,
    )
    assert r.was_clamped is True
    assert r.price > 100.0
    assert r.placeable is True


def test_gate_sl_short_clamped_at_or_below_fill_unplaceable():
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        115.0, leg="SL", direction="SHORT", entry_fill=100.0,
        upper_circuit=101.0, lower_circuit=70.0,
    )
    assert r.price <= 100.0
    assert r.placeable is False


def test_gate_inside_band_unchanged_placeable():
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        480.0, leg="TGT", direction="LONG", entry_fill=460.0,
        upper_circuit=503.35, lower_circuit=440.0,
    )
    assert r.was_clamped is False
    assert r.price == 480.0
    assert r.placeable is True


def test_gate_no_band_data_passthrough_placeable():
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        505.0, leg="TGT", direction="LONG", entry_fill=480.0,
        upper_circuit=None, lower_circuit=None,
    )
    assert r.was_clamped is False and r.price == 505.0 and r.placeable is True


def test_gate_nonpositive_price_unplaceable():
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        0.0, leg="TGT", direction="LONG", entry_fill=100.0,
        upper_circuit=503.0, lower_circuit=440.0,
    )
    assert r.placeable is False


def test_gate_zero_entry_fill_fails_open_placeable():
    """No fill reference (entry_fill<=0) -> cannot judge side -> fail open."""
    from orders.price_math import clamp_exit_into_band
    r = clamp_exit_into_band(
        187.0, leg="TGT", direction="LONG", entry_fill=0.0,
        upper_circuit=190.82, lower_circuit=127.22,
    )
    assert r.placeable is True


def test_gate_invalid_leg_raises():
    import pytest
    from orders.price_math import clamp_exit_into_band
    with pytest.raises(ValueError):
        clamp_exit_into_band(
            100.0, leg="ENTRY", direction="LONG", entry_fill=100.0,
            upper_circuit=110.0, lower_circuit=90.0,
        )
